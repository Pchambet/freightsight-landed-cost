"""Writing tracking events and keeping the container in step with them.

Everything here runs inside the caller's transaction: events, the container's derived state and the
ETA history land together or not at all.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.domain.alerts.service import raise_dnd_risk_alert
from app.domain.models import Container, EtaHistory, Organization
from app.domain.models import TrackingEvent as TrackingEventRow
from app.domain.tracking.ports import ProviderEvent
from app.domain.tracking.state import apply_snapshot, derive, derive_dnd, eta_shift, risk_rose

logger = logging.getLogger(__name__)


def ingest(
    db: Session,
    org_id: UUID,
    container_id: UUID,
    events: Sequence[ProviderEvent],
    *,
    provider: str,
    delivery_id: UUID | None = None,
) -> int:
    """Append events, ignoring ones we already have. Returns how many rows were actually inserted.

    Idempotence is the unique key `(org_id, container_id, provider, provider_event_key)`: a provider
    that resends the same snapshot writes nothing new, and an estimate that turns into an actual has a
    different key, so both rows stay.
    """
    if not events:
        return 0
    rows = [
        {
            "org_id": org_id,
            "container_id": container_id,
            "provider": provider,
            "provider_event_key": e.provider_event_key,
            "code": e.code,
            "occurred_at": e.occurred_at.astimezone(UTC),
            "is_estimate": e.is_estimate,
            "location_unlocode": e.location_unlocode,
            "location_name": e.location_name,
            "vessel_name": e.vessel_name,
            "voyage": e.voyage,
            "raw_description": e.raw_description or None,
            "source": e.source,
            "delivery_id": delivery_id,
        }
        for e in events
    ]
    stmt = (
        insert(TrackingEventRow)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["org_id", "container_id", "provider", "provider_event_key"])
        .returning(TrackingEventRow.id)
    )
    inserted = len(list(db.scalars(stmt)))
    db.flush()
    return inserted


def history(db: Session, org_id: UUID, container_id: UUID) -> list[TrackingEventRow]:
    return list(
        db.scalars(
            select(TrackingEventRow)
            .where(TrackingEventRow.org_id == org_id, TrackingEventRow.container_id == container_id)
            .order_by(TrackingEventRow.occurred_at, TrackingEventRow.inserted_at)
        )
    )


def as_provider_events(rows: Sequence[TrackingEventRow]) -> list[ProviderEvent]:
    return [
        ProviderEvent(
            code=r.code,
            occurred_at=r.occurred_at,
            is_estimate=r.is_estimate,
            provider_event_key=r.provider_event_key,
            location_unlocode=r.location_unlocode,
            location_name=r.location_name,
            vessel_name=r.vessel_name,
            voyage=r.voyage,
            raw_description=r.raw_description or "",
            source=r.source,  # type: ignore[arg-type]
        )
        for r in rows
    ]


def record_eta(
    db: Session,
    org_id: UUID,
    container: Container,
    new_eta: datetime | None,
    *,
    source: str,
) -> EtaHistory | None:
    """Move the container's ETA, writing a history line when the move is large enough to matter."""
    if new_eta is None:
        return None
    new_eta = new_eta.astimezone(UTC)
    previous = container.eta
    shift = eta_shift(previous, new_eta)
    container.eta = new_eta
    if shift is None:
        return None
    delta_hours, severity = shift
    row = EtaHistory(
        org_id=org_id,
        container_id=container.id,
        eta=new_eta,
        previous_eta=previous,
        delta_hours=delta_hours,
        severity=severity,
        source=source,
    )
    db.add(row)
    db.flush()
    return row


def refresh(
    db: Session,
    org: Organization,
    container: Container,
    *,
    source: str = "provider",
    now: datetime | None = None,
    alert: bool = True,
) -> None:
    """Recompute everything the container derives from its events: milestone, timestamps, ETA, LFD.

    And say so when the risk band goes up. This used to be the daily 06:00 pass's job alone, which
    meant a band that reached its final value *here* — a webhook delivering a discharge that is
    already three days old, an importer entering a container that is past its last free day on the
    day they sign up — was compared with itself the next morning and never spoken of again. The
    dedup key makes saying it twice free, so the honest thing is to say it as soon as it is true.
    """
    moment = now or datetime.now(UTC)
    events = as_provider_events(history(db, org.id, container.id))
    destination = container.shipment.destination_unlocode if container.shipment is not None else None
    snapshot = derive(events, moment, destination_unlocode=destination)
    apply_snapshot(container, snapshot)
    record_eta(db, org.id, container, snapshot.eta, source=source)
    before = container.dnd_risk
    derive_dnd(container, org, moment)
    if alert and risk_rose(before, container.dnd_risk):
        raise_dnd_risk_alert(
            db, org, container, moment, discharge_confirmed=snapshot.discharge_port_confirmed
        )
    db.flush()
