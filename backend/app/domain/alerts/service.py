"""Raising alerts, without raising the same one twice.

Everything an alert says has to survive being recomputed: the daily demurrage pass looks at the same
container every morning, and the ETA job re-reads the same history rows. `dedup_key` carries that —
one row per organization per key — so a job can be written as "state the fact" rather than "work out
whether I already said this".
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.models import (
    Alert,
    AlertKind,
    AlertSeverity,
    Container,
    CostType,
    DndRisk,
    Organization,
    RateBasis,
    RateCard,
)
from app.domain.tracking.state import ClockKind, DndClock, running_clock

#: A running demurrage or detention clock is said again every day for the first fortnight, then once
#: a week, then not at all (see `raise_dnd_risk_alert`).
REMIND_DAILY_FOR_DAYS = 14
REMIND_UNTIL_DAYS = 60

_DND_SEVERITY = {
    DndRisk.LOW: AlertSeverity.INFO,
    DndRisk.MEDIUM: AlertSeverity.WARNING,
    DndRisk.HIGH: AlertSeverity.CRITICAL,
    DndRisk.INCURRING: AlertSeverity.CRITICAL,
}

#: The rate card each clock reads as a daily rate. The same cards the demurrage report already uses
#: for its "avoided" column, so one number cannot say two things in two places.
_RATE_COST_TYPE: dict[ClockKind, CostType] = {
    "demurrage": CostType.DEMURRAGE,
    "detention": CostType.DETENTION,
}

logger = logging.getLogger(__name__)


def raise_alert(
    db: Session,
    org_id: UUID,
    *,
    kind: AlertKind,
    severity: AlertSeverity,
    title: str,
    body: str = "",
    dedup_key: str,
    container_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Alert | None:
    """Create the alert, or return None because this organization has already been told."""
    stmt = (
        insert(Alert)
        .values(
            org_id=org_id,
            container_id=container_id,
            kind=kind,
            severity=severity,
            title=title,
            body=body,
            dedup_key=dedup_key,
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["org_id", "dedup_key"])
        .returning(Alert)
    )
    alert = db.scalars(stmt).one_or_none()
    db.flush()
    if alert is not None:
        logger.info("alert %s raised for org %s: %s", kind.value, org_id, title)
    return alert


def daily_rate(db: Session, org_id: UUID, clock: ClockKind) -> RateCard | None:
    """The organization's own daily rate for this clock, or nothing.

    A flat rate card of the matching cost type is read as a price per day — the same reading the
    demurrage report makes of it. There is no invented default: a made-up figure on an alert that
    says "this is costing you money" is the fastest way to lose the right to say it.
    """
    return db.scalar(
        select(RateCard).where(
            RateCard.org_id == org_id,
            RateCard.cost_type == _RATE_COST_TYPE[clock],
            RateCard.basis == RateBasis.FLAT,
        )
    )


def _dnd_payload(
    clock: DndClock, container: Container, card: RateCard | None, *, confirmed: bool
) -> dict[str, Any]:
    """What the sentence needs, in machine form: the front and the digest both write from this."""
    payload: dict[str, Any] = {
        "risk": clock.risk.value,
        "clock": clock.kind,
        "deadline": clock.deadline.isoformat(),
        # Kept under its old name as well: clients and the reporting screen read `last_free_day`,
        # and for the demurrage clock the deadline is exactly that.
        "last_free_day": container.last_free_day.isoformat() if container.last_free_day else None,
        "days_left": clock.days_left,
        "days_elapsed": clock.days_elapsed,
        "port_of_discharge_confirmed": confirmed,
    }
    if card is not None:
        payload["daily_rate"] = str(card.amount)
        payload["currency"] = card.currency
        payload["amount_to_date"] = str((Decimal(clock.days_elapsed) * card.amount).quantize(Decimal("0.01")))
    return payload


def raise_dnd_risk_alert(
    db: Session,
    org: Organization,
    container: Container,
    moment: datetime | None = None,
    *,
    discharge_confirmed: bool = True,
) -> Alert | None:
    """State the container's current demurrage or detention risk — idempotent via dedup_key.

    Two things this is asked to do that pull in opposite directions. It must be safe to call from
    anywhere — the daily pass, a webhook, a manual event, the onboarding of a box already past its
    last free day — which is what the dedup key is for. And it must not fall silent on the one alert
    that costs money every day: while a clock is *running*, the key carries the date, so the customer
    is reminded once a day rather than once ever.

    The detention keys are separate from the demurrage ones (`det:`) because they are separate facts
    about the same box: one is the terminal's clock, the other the carrier's.
    """
    moment = moment or datetime.now(UTC)
    clock = running_clock(container, org, moment)
    if clock is None or clock.risk is DndRisk.NONE:
        return None
    card = daily_rate(db, org.id, clock.kind)
    scope = "" if clock.kind == "demurrage" else "det:"
    noun = "Demurrage" if clock.kind == "demurrage" else "Detention"
    if clock.risk is DndRisk.INCURRING:
        # One reminder per day while it runs. `dnd:{id}:{risk}` alone said it once and never again,
        # so a box left at the terminal over a holiday cost four figures in silence.
        # But a clock only stops on the empty return, which in manual tracking is an event somebody
        # has to type in — and mostly nobody does. Daily for ever would be a critical alert every
        # morning for every box ever delivered, so the rhythm slows to weekly after a fortnight and
        # stops after two months: by then it is a data-entry gap, which the manual-tracking note says.
        if clock.days_elapsed > REMIND_UNTIL_DAYS:
            return None
        if clock.days_elapsed <= REMIND_DAILY_FOR_DAYS:
            period = moment.date().isoformat()
        else:
            iso = moment.isocalendar()
            period = f"{iso.year}-W{iso.week:02d}"
        key = f"dnd:{container.id}:{scope}INCURRING:{period}"
        body = f"The deadline was {clock.deadline}. {noun} has been running for {clock.days_elapsed} day(s)."
        if card is not None:
            total = (Decimal(clock.days_elapsed) * card.amount).quantize(Decimal("0.01"))
            body += f" {total} {card.currency} so far, at {card.amount} {card.currency} per day."
        else:
            body += " Add a daily rate card to see the amount."
        title = f"{container.container_number}: {noun.lower()} running"
    else:
        key = f"dnd:{container.id}:{scope}{clock.risk.value}"
        body = f"Deadline {clock.deadline}, {clock.days_left} day(s) left."
        title = f"{container.container_number}: {noun.lower()} risk {clock.risk.value}"
    return raise_alert(
        db,
        org.id,
        kind=AlertKind.DND_RISK,
        severity=_DND_SEVERITY.get(clock.risk, AlertSeverity.INFO),
        title=title,
        body=body,
        dedup_key=key,
        container_id=container.id,
        payload=_dnd_payload(clock, container, card, confirmed=discharge_confirmed),
    )


def unread_count(db: Session, org_id: UUID) -> int:
    return db.scalar(select(func.count(Alert.id)).where(Alert.org_id == org_id, Alert.read_at.is_(None))) or 0


def mark_read(db: Session, org_id: UUID, alert_id: UUID) -> Alert | None:
    alert = db.scalar(select(Alert).where(Alert.org_id == org_id, Alert.id == alert_id))
    if alert is None:
        return None
    if alert.read_at is None:
        alert.read_at = datetime.now(UTC)
        db.flush()
    return alert


def mark_all_read(db: Session, org_id: UUID) -> int:
    now = datetime.now(UTC)
    alerts = list(db.scalars(select(Alert).where(Alert.org_id == org_id, Alert.read_at.is_(None))))
    for alert in alerts:
        alert.read_at = now
    db.flush()
    return len(alerts)
