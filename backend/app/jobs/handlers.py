"""What the background jobs actually do. Plain functions, so they can be called in a test without a
worker, a queue, or a clock.

Every handler that touches tenant data walks the organizations one at a time and sets the tenant
variable before querying: the worker connects as `freightsight_app` like everything else, so Row
Level Security applies to it too. Nothing here reads across organizations.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.adapters.fx_ecb import make_fetcher
from app.core.db import get_sessionmaker
from app.core.settings import get_settings
from app.core.tenancy import TenantSession, set_current_org
from app.domain.alerts import codes
from app.domain.alerts.notifier import Notification, Notifier, make_notifier
from app.domain.alerts.service import REMIND_UNTIL_DAYS, raise_alert, raise_dnd_risk_alert
from app.domain.documents.registry import get_store
from app.domain.fx.service import FxService
from app.domain.invoices import service as invoices_service
from app.domain.invoices.registry import get_extractors
from app.domain.models import (
    Alert,
    AlertKind,
    AlertSeverity,
    Container,
    ContainerMilestone,
    DndRisk,
    ErpSyncStatus,
    EtaHistory,
    Invoice,
    InvoiceStatus,
    Membership,
    Organization,
    TrackingEvent,
    TrackingState,
    TrackingSubscription,
    User,
    WebhookDelivery,
)
from app.domain.tracking.delivery import Received, process_stored_delivery
from app.domain.tracking.ports import ProviderNotConfigured, ProviderUnavailable
from app.domain.tracking.ports import TrackingProvider as _TrackingProvider
from app.domain.tracking.registry import get_provider
from app.domain.tracking.service import ingest, refresh
from app.domain.tracking.state import ETA_SHIFT_HOURS, ETA_WARNING_HOURS, derive_dnd, risk_rose

logger = logging.getLogger(__name__)

#: How a handler gets a provider. A parameter rather than a global, so a test can hand in its own.
ProviderFactory = Callable[[str], _TrackingProvider]

#: How long a subscription may stay silent **once an event is actually due** before we say so.
STALE_AFTER = timedelta(hours=48)
#: How late an ETA has to be before the arrival is itself the missing event.
ETA_OVERDUE_AFTER = timedelta(hours=24)
#: Silence on a box we have no ETA for at all. Ningbo to Le Havre is three weeks of nothing by
#: construction — one `vessel_departed`, then nothing until the transhipment — so anything shorter
#: reports every container on the water as broken, every day, for the whole crossing.
SEA_SILENCE_AFTER = timedelta(days=21)
#: When a subscription is worth re-reading from the provider, in case a webhook was lost.
POLL_AFTER = timedelta(hours=48)
#: How many containers in a row a provider may fail to answer for before we stop asking it in this
#: sweep. Three, because one is a bad container and two is a coincidence.
CIRCUIT_TRIPS_AFTER = 3
#: How many times a failed delivery is retried before it is left alone.
MAX_DELIVERY_ATTEMPTS = 5
#: How many digests in a row may fail before we stop retrying every five minutes and say so.
MAX_DIGEST_ATTEMPTS = 5
#: How long to wait before trying a broken e-mail channel again, once it has been given up on.
DIGEST_BACKOFF = timedelta(hours=1)
#: The hour, in Europe/Paris, from which the demurrage pass may run.
DAILY_RISK_HOUR = 6
#: A daily pass that has not run for this long runs now, whatever the hour. A worker restarted
#: between 06:00 and 07:00 Paris used to skip the whole day — including its J-3 warnings.
CATCH_UP_AFTER = timedelta(hours=20)
PARIS = "Europe/Paris"

#: Rows of `job_runs`. Not organizations' data: when a background job last did its work.
DAILY_RISK_JOB = "dnd.recompute_risk"
MANUAL_REMINDER_JOB = "tracking.manual_silence"
WORKER_HEARTBEAT = "worker.heartbeat"
BACKUP_FRESHNESS_JOB = "ops.backup_freshness"

#: A backup older than this, or suddenly much smaller, is worth waking someone for.
BACKUP_MAX_AGE = timedelta(hours=48)
BACKUP_SHRINK_RATIO = 0.7
HOUR = timedelta(hours=1)

#: Once a box is here, an event is expected within `STALE_AFTER`: the terminal moves in hours.
AT_THE_TERMINAL = (
    ContainerMilestone.VESSEL_ARRIVED,
    ContainerMilestone.DISCHARGED,
    ContainerMilestone.AVAILABLE_FOR_PICKUP,
)
#: Once it is here, nothing more is due from the provider and silence means nothing.
SETTLED = (
    ContainerMilestone.GATE_OUT_FULL,
    ContainerMilestone.DELIVERED,
    ContainerMilestone.GATE_IN_EMPTY_RETURN,
)


class ProviderCircuit:
    """Stops asking a provider that has stopped answering, for the rest of a sweep.

    Without this, an outage at a provider costs one timeout per tracked container: bounded per call
    since the timeout audit, but multiplied by every container, on a job that holds a queueing lock.
    A hundred boxes and a silent provider is an hour of a worker spent learning the same thing a
    hundred times.

    Per sweep rather than persisted: the next run starts by trying again, which is what you want
    from an outage that is usually minutes long.
    """

    def __init__(self, trips_after: int = CIRCUIT_TRIPS_AFTER) -> None:
        self.trips_after = trips_after
        self.failures: dict[str, int] = {}
        self.tripped: set[str] = set()

    def is_open(self, provider: str) -> bool:
        """True when we have stopped asking this provider."""
        return provider in self.tripped

    def failed(self, provider: str) -> bool:
        """Record a provider that could not be reached. True when this is the one that trips it."""
        self.failures[provider] = self.failures.get(provider, 0) + 1
        if self.failures[provider] >= self.trips_after and provider not in self.tripped:
            self.tripped.add(provider)
            return True
        return False

    def answered(self, provider: str) -> None:
        """A provider that answers is not down, whatever the answer was."""
        self.failures.pop(provider, None)


@contextmanager
def session() -> Iterator[Session]:
    with get_sessionmaker()() as db:
        yield db


def organizations(db: Session) -> list[Organization]:
    return list(db.scalars(select(Organization).order_by(Organization.created_at)))


@contextmanager
def tenant(db: Session, org: Organization) -> Iterator[Organization]:
    """Work as one organization, then put the connection back the way it was found."""
    set_current_org(db, org.id)
    try:
        yield org
    finally:
        set_current_org(db, None)


# ---------------------------------------------------------------------------- deliveries


def retry_failed_deliveries(
    db: Session,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory = get_provider,
) -> int:
    """Re-run the deliveries that failed to process. Returns how many were retried.

    A delivery is stored the moment it is verified, so a bug on our side is recoverable: the raw body
    is still there and this replays it. Deliveries that have already been retried too often are left
    alone rather than hammered.
    """
    moment = now or datetime.now(UTC)
    failed = list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.status == "failed", WebhookDelivery.signature_ok.is_(True))
            .order_by(WebhookDelivery.received_at)
            .limit(50)
        )
    )
    retried = 0
    for delivery in failed:
        attempts = delivery.attempts
        if attempts >= MAX_DELIVERY_ATTEMPTS:
            continue
        if delivery.processed_at and moment < delivery.processed_at + _backoff(attempts):
            continue  # exponential backoff: 1, 2, 4, 8, 16 minutes
        try:
            provider = provider_factory(delivery.provider)
        except Exception:
            # A provider whose key has been removed, or a name no longer in the registry. The body
            # stays in the table and this returns to it; what it must not do is stop the *other*
            # providers' deliveries being replayed behind it.
            logger.exception("no provider for stored delivery %s", delivery.id)
            continue
        # process_stored_delivery never raises: a delivery that cannot be understood is marked,
        # not thrown. Anything that escapes anyway is a bug, and one bad body is not a reason to
        # abandon the rest of the queue.
        try:
            result = process_stored_delivery(db, provider, delivery, attempt=attempts + 1, now=moment)
        except Exception:  # pragma: no cover - the callee is written not to raise
            logger.exception("replaying delivery %s failed", delivery.id)
            db.rollback()
            continue
        if result.status == "failed" and attempts + 1 >= MAX_DELIVERY_ATTEMPTS:
            _delivery_gave_up(db, delivery, result)
        retried += 1
    return retried


def _backoff(attempts: int) -> timedelta:
    return timedelta(minutes=2**attempts)


def _delivery_gave_up(db: Session, delivery: WebhookDelivery, result: Received) -> None:
    """Say that a tracking update was accepted and never applied, once the retries are spent.

    Until now this ended in silence: the body stayed in the table, the container's history quietly
    missed an event, and the only trace was a log line nobody was reading. Said once per delivery,
    and only to the organization it belonged to — a delivery that could not even be routed has no
    owner to tell, and is not this alert's business.
    """
    if result.org_id is None or result.container_id is None:
        return  # never routed: nobody to tell, and nothing about their freight to say
    org = db.get(Organization, result.org_id)
    if org is None:  # pragma: no cover - the resolver just proved it exists
        return
    with tenant(db, org):
        container = db.get(Container, result.container_id)
        number = container.container_number if container is not None else ""
        raise_alert(
            db,
            org.id,
            kind=AlertKind.TRACKING_DELIVERY_FAILED,
            severity=AlertSeverity.WARNING,
            title=f"{number}: a tracking update could not be processed",
            body=(
                f"{delivery.provider} sent an update on {delivery.received_at:%Y-%m-%d %H:%M} UTC "
                f"that we could not process after {MAX_DELIVERY_ATTEMPTS} attempts. This container's "
                "tracking may be behind. The raw delivery is kept: nothing is lost, and it can be "
                "replayed."
            ),
            dedup_key=f"delivery-failed:{delivery.id}",
            container_id=result.container_id,
            payload={
                "provider": delivery.provider,
                "attempts": MAX_DELIVERY_ATTEMPTS,
                "delivery_id": delivery.delivery_id,
                "received_at": delivery.received_at.isoformat(),
            },
        )
        db.commit()


# ---------------------------------------------------------------------------- silent subscriptions


def poll_stale_subscriptions(
    db: Session,
    *,
    now: datetime | None = None,
    provider_factory: ProviderFactory = get_provider,
) -> int:
    """Ask the provider directly about containers whose feed has gone quiet, and say so if it stays
    quiet. Returns the number of subscriptions polled.

    Two things a provider outage must not be allowed to do, and both used to happen: mark every
    customer's containers as failed while telling them to go and check the carrier's site, and spend
    one timeout per container finding that out.

    A third, subtler one: telling only the organization the circuit happened to trip on. Whose turn
    it was is an accident of iteration order — every organization whose polling stopped this round
    is told, once, and only once the provider is really down rather than on a single timeout.

    And a fourth, which is why `overdue_event` exists: silence is not evidence of anything on its
    own. A box crossing from Asia is silent for three weeks by design, and treating that as a broken
    feed produced one alert per container per day for the whole voyage.
    """
    moment = now or datetime.now(UTC)
    circuit = ProviderCircuit()
    polled = 0
    stopped: dict[str, dict[UUID, Organization]] = {}
    for org in organizations(db):
        with tenant(db, org):
            subscriptions = list(
                db.scalars(
                    select(TrackingSubscription).where(
                        TrackingSubscription.org_id == org.id,
                        TrackingSubscription.ended_at.is_(None),
                        TrackingSubscription.provider != "manual",
                    )
                )
            )
            for subscription in subscriptions:
                container = db.get(Container, subscription.container_id)
                if container is None:  # pragma: no cover - foreign key says otherwise
                    continue
                last = db.scalar(
                    select(TrackingEvent.inserted_at)
                    .where(TrackingEvent.container_id == subscription.container_id)
                    .order_by(TrackingEvent.inserted_at.desc())
                    .limit(1)
                )
                since = last or subscription.subscribed_at or subscription.created_at
                silence = moment - since if since is not None else timedelta(0)
                due = overdue_event(container, silence, moment)
                if subscription.provider_ref is None:
                    # Webhook-only: the provider was subscribed somewhere else and there is no handle
                    # to ask with. Polling it into failure was inventing an outage out of a
                    # deployment choice; a milestone that never came is still worth saying.
                    if due:
                        _report_missing(db, org, subscription, container, moment, silence, due)
                    continue
                if silence < POLL_AFTER and not due:
                    continue
                if circuit.is_open(subscription.provider):
                    # Asking again costs a timeout. This organization's tracking has stopped all
                    # the same, so it is on the list of people to tell.
                    stopped.setdefault(subscription.provider, {})[org.id] = org
                    continue
                polled += 1
                try:
                    outcome = _poll_one(
                        db, org, subscription, container, moment, provider_factory, silence, due
                    )
                except Exception:
                    # One container's surprise — a payload shape nobody expected, a bug in a parser
                    # — must not cost every other organization its round.
                    logger.exception("polling failed for container %s", subscription.container_id)
                    db.rollback()
                    continue
                if outcome != "unreachable":
                    circuit.answered(subscription.provider)
                else:
                    stopped.setdefault(subscription.provider, {})[org.id] = org
                    circuit.failed(subscription.provider)
            db.commit()
    _announce_outages(db, stopped, circuit, moment)
    return polled


def overdue_event(container: Container, silence: timedelta, moment: datetime) -> str | None:
    """Why an event is late, or None because none is due yet.

    Whether silence means anything depends entirely on where the box is. At the terminal a milestone
    is due within a working day or two. At sea nothing at all is due until the ship arrives, which is
    why the only thing that can be late out there is the ETA itself. A box with no ETA and no news
    for three weeks is the remaining case, and is genuinely worth a question.
    """
    if container.milestone in SETTLED:
        return None
    if container.milestone in AT_THE_TERMINAL:
        return codes.EVENT_OVERDUE if silence >= STALE_AFTER else None
    if container.eta is not None:
        return codes.ETA_PASSED if moment - container.eta > ETA_OVERDUE_AFTER else None
    return codes.NO_NEWS if silence >= SEA_SILENCE_AFTER else None


def _report_missing(
    db: Session,
    org: Organization,
    subscription: TrackingSubscription,
    container: Container,
    moment: datetime,
    silence: timedelta,
    reason: str,
) -> None:
    """Say that a milestone we were waiting for has not come. Once a day, and never about the box
    being at sea — the `tracking_state` is deliberately left alone, because nothing here proves the
    subscription is broken."""
    hours = int(silence.total_seconds() // 3600)
    days = (moment - (subscription.subscribed_at or subscription.created_at)).days
    raise_alert(
        db,
        org.id,
        kind=AlertKind.TRACKING_DATA_MISSING,
        severity=AlertSeverity.WARNING,
        title=f"No tracking data for {container.container_number}",
        body=(
            f"{subscription.provider} has sent nothing for at least {hours} hours, and an event was "
            "due. Check the container on the carrier's own site."
        ),
        dedup_key=f"tracking-stale:{container.id}:{moment.date().isoformat()}",
        container_id=container.id,
        payload={
            "provider": subscription.provider,
            "days_since_subscribed": days,
            "hours": hours,
            "reason": reason,
        },
    )
    subscription.last_error = reason
    db.flush()


def _announce_outages(
    db: Session,
    stopped: dict[str, dict[UUID, Organization]],
    circuit: ProviderCircuit,
    moment: datetime,
) -> None:
    """Tell every organization whose tracking stopped, once the provider is confirmed down.

    At the end rather than as it happens: an organization polled before the third failure would
    otherwise hear nothing, and an organization polled after it would hear about a provider that
    answered again two containers later.
    """
    for provider, affected in stopped.items():
        if not circuit.is_open(provider):
            continue  # a timeout or two, and then it answered: not an outage anybody needs told
        for org in affected.values():
            with tenant(db, org):
                _provider_is_down(db, org, provider, moment)
                db.commit()


def _provider_is_down(db: Session, org: Organization, provider: str, moment: datetime) -> None:
    """One alert about the provider, instead of one alert per container about the containers."""
    logger.warning(
        "tracking provider not answering: polling stopped for this round",
        extra={"provider": provider, "org_id": str(org.id)},
    )
    raise_alert(
        db,
        org.id,
        kind=AlertKind.TRACKING_PROVIDER_DOWN,
        severity=AlertSeverity.WARNING,
        title=f"{provider} is not answering",
        body=(
            f"We could not reach {provider} for several containers in a row, so tracking updates "
            "are paused until it answers again. Nothing is wrong with the containers themselves, "
            "and nothing needs doing here: the next round picks up where this one stopped."
        ),
        dedup_key=f"tracking-provider-down:{provider}:{moment.date().isoformat()}",
        payload={"provider": provider},
    )


def _poll_one(
    db: Session,
    org: Organization,
    subscription: TrackingSubscription,
    container: Container,
    moment: datetime,
    provider_factory: ProviderFactory,
    silence: timedelta,
    due: str | None,
) -> str:
    """Ask about one container. Returns `ingested`, `quiet`, or `unreachable`.

    The three are not the same and used to be treated as two. "We could not reach the provider" is
    about us and our supplier: saying "check the container on the carrier's own site" then accuses
    the customer's carrier of something our own outage caused.

    "The provider answered and had nothing new" is the ordinary state of a container at sea, and is
    the third outcome. It says nothing and marks nothing — it is, in fact, the proof that the
    subscription works, so a container the sweep had previously marked FAILED goes back to ACTIVE
    here rather than staying red until a webhook happens to arrive.
    """
    try:
        envelope = provider_factory(subscription.provider).fetch(subscription.provider_ref or "")
        ingested = ingest(db, org.id, container.id, envelope.events, provider=subscription.provider)
        if ingested:
            refresh(db, org, container, source=subscription.provider, now=moment)
    except (ProviderNotConfigured, ProviderUnavailable) as exc:
        subscription.last_error = exc.message
        db.flush()
        return "unreachable"
    if container.tracking_state is TrackingState.FAILED and subscription.status == "active":
        container.tracking_state = TrackingState.ACTIVE
    if ingested:
        subscription.last_error = None
        db.flush()
        return "ingested"
    if due:
        _report_missing(db, org, subscription, container, moment, silence, due)
    else:
        subscription.last_error = None
    db.flush()
    return "quiet"


#: How long a delivered container is still worth paying a provider to watch.
KEEP_TRACKING_AFTER_DELIVERY = timedelta(days=7)

DELIVERED_MILESTONES = (ContainerMilestone.DELIVERED, ContainerMilestone.GATE_IN_EMPTY_RETURN)


def end_delivered_subscriptions(
    db: Session, *, now: datetime | None = None, provider_factory: ProviderFactory = get_provider
) -> int:
    """Stop tracking containers that arrived a week ago. Returns how many were unsubscribed.

    Providers charge per tracked container, and a box that has been returned empty is not going to
    move again. The week of grace is for the late gate-in that arrives after the delivery event.
    """
    moment = now or datetime.now(UTC)
    circuit = ProviderCircuit()
    ended = 0
    for org in organizations(db):
        with tenant(db, org):
            subscriptions = list(
                db.scalars(
                    select(TrackingSubscription).where(
                        TrackingSubscription.org_id == org.id,
                        TrackingSubscription.ended_at.is_(None),
                        TrackingSubscription.provider != "manual",
                    )
                )
            )
            for subscription in subscriptions:
                container = db.get(Container, subscription.container_id)
                if container is None or container.milestone not in DELIVERED_MILESTONES:
                    continue
                settled = container.empty_returned_at or container.gate_out_at
                if settled is None or moment - settled < KEEP_TRACKING_AFTER_DELIVERY:
                    continue
                if subscription.provider_ref:
                    if circuit.is_open(subscription.provider):
                        continue  # not answering; the next run will unsubscribe these
                    try:
                        provider_factory(subscription.provider).unsubscribe(subscription.provider_ref)
                    except (ProviderNotConfigured, ProviderUnavailable) as exc:
                        # The provider will keep charging until someone sorts it out; that is worth
                        # saying, and worth retrying, so the subscription stays open.
                        logger.warning(
                            "could not unsubscribe container %s: %s", container.container_number, exc
                        )
                        subscription.last_error = exc.message
                        circuit.failed(subscription.provider)
                        continue
                    circuit.answered(subscription.provider)
                subscription.ended_at = moment
                subscription.status = "ended"
                container.tracking_state = TrackingState.ENDED
                ended += 1
            db.commit()
    return ended


# ---------------------------------------------------------------------------- demurrage


def recompute_dnd_risk(db: Session, *, now: datetime | None = None) -> int:
    """Recompute every live container's demurrage **and detention** risk, and speak.

    Returns the number of containers whose band changed.

    Two clocks, and the second one used to be invisible: the sweep filtered on
    `gate_out_at IS NULL`, so a container that had left the terminal was never looked at again and
    its badge froze at whatever it said the day it was picked up — green, usually, while detention
    ran for a fortnight. The selection is now "landed and not yet given back".

    It speaks in two cases. A band that went up is news. A clock that is *running* is news again
    every morning: the dedup key carries the date, so this is one reminder a day rather than one
    reminder ever, which is what the most expensive alert in the product was doing.
    """
    moment = now or datetime.now(UTC)
    changed = 0
    for org in organizations(db):
        with tenant(db, org):
            containers = list(
                db.scalars(
                    select(Container).where(
                        Container.org_id == org.id,
                        Container.archived_at.is_(None),
                        Container.empty_returned_at.is_(None),
                        or_(
                            Container.discharged_at.is_not(None),
                            Container.gate_out_at.is_not(None),
                        ),
                    )
                )
            )
            for container in containers:
                before = container.dnd_risk
                derive_dnd(container, org, moment)
                if container.dnd_risk != before:
                    changed += 1
                if risk_rose(before, container.dnd_risk) or container.dnd_risk is DndRisk.INCURRING:
                    raise_dnd_risk_alert(db, org, container, moment)
            db.commit()
    return changed


# ---------------------------------------------------------------------------- manual tracking


def remind_manual_tracking(db: Session, *, now: datetime | None = None) -> int:
    """Tell an organization that nobody has entered anything. Returns how many notes were raised.

    An importer with no provider account — the likely shape of the first weeks, since the default
    provider is `manual` — has no guard rail at all: forget to enter the discharge and
    `discharged_at` stays null, the risk stays NONE, the daily pass skips the box and the screen is
    green and silent right up to the forwarder's invoice.

    Gentle and weekly on purpose: it is a request to type something in, not an alarm, and the person
    it is aimed at already knows their own freight better than we do.
    """
    moment = now or datetime.now(UTC)
    week = f"{moment.isocalendar().year}-W{moment.isocalendar().week:02d}"
    raised = 0
    for org in organizations(db):
        with tenant(db, org):
            containers = list(
                db.scalars(
                    select(Container).where(
                        Container.org_id == org.id,
                        Container.archived_at.is_(None),
                        Container.discharged_at.is_(None),
                        Container.eta.is_not(None),
                        Container.eta < moment - ETA_OVERDUE_AFTER * 2,
                        # Like the demurrage reminders, silent after two months: a box whose ETA is a
                        # quarter old is history someone imported, not a delivery to chase.
                        Container.eta >= moment - timedelta(days=REMIND_UNTIL_DAYS),
                        Container.tracking_state.in_([TrackingState.MANUAL, TrackingState.UNTRACKED]),
                    )
                )
            )
            for container in containers:
                alert = raise_alert(
                    db,
                    org.id,
                    kind=AlertKind.TRACKING_DATA_MISSING,
                    severity=AlertSeverity.INFO,
                    title=f"No tracking data for {container.container_number}",
                    body=(
                        f"{container.container_number} was due on "
                        f"{container.eta:%Y-%m-%d} and no milestone has been entered since. Enter "
                        "the discharge so we can count your free days."
                    ),
                    dedup_key=f"manual-silence:{container.id}:{week}",
                    container_id=container.id,
                    payload={
                        "reason": codes.MANUAL_SILENCE,
                        "eta": container.eta.isoformat() if container.eta else None,
                    },
                )
                raised += 1 if alert is not None else 0
            db.commit()
    return raised


# ---------------------------------------------------------------------------- ETA moves


def alert_on_eta_changes(db: Session, *, now: datetime | None = None, lookback_hours: int = 24) -> int:
    """Turn recent ETA history into alerts. Returns how many alerts were raised.

    The history row is the fact; the alert is the telling of it. Keying the alert on the history row
    means this can run as often as it likes.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(hours=lookback_hours)
    raised = 0
    for org in organizations(db):
        with tenant(db, org):
            rows = list(
                db.scalars(
                    select(EtaHistory)
                    .where(EtaHistory.org_id == org.id, EtaHistory.recorded_at >= since)
                    .order_by(EtaHistory.recorded_at)
                )
            )
            for row in rows:
                container = db.get(Container, row.container_id)
                if container is None:  # pragma: no cover
                    continue
                hours = row.delta_hours
                if abs(hours) < Decimal(ETA_SHIFT_HOURS):  # pragma: no cover - not written below 12 h
                    continue
                direction = "later" if hours > 0 else "earlier"
                alert = raise_alert(
                    db,
                    org.id,
                    kind=AlertKind.ETA_CHANGED,
                    severity=(
                        AlertSeverity.WARNING
                        if abs(hours) > Decimal(ETA_WARNING_HOURS)
                        else AlertSeverity.INFO
                    ),
                    title=f"{container.container_number}: arrival moved {abs(hours)} h {direction}",
                    body=f"New ETA {row.eta:%Y-%m-%d %H:%M} UTC, was {row.previous_eta:%Y-%m-%d %H:%M} UTC."
                    if row.previous_eta
                    else f"New ETA {row.eta:%Y-%m-%d %H:%M} UTC.",
                    dedup_key=f"eta:{row.id}",
                    container_id=container.id,
                    payload={
                        "delta_hours": str(hours),
                        "eta": row.eta.isoformat(),
                        "previous_eta": row.previous_eta.isoformat() if row.previous_eta else None,
                    },
                )
                raised += 1 if alert is not None else 0
            db.commit()
    return raised


# ---------------------------------------------------------------------------- invoice extraction


def extract_invoice(db: Session, org_id: UUID, invoice_id: UUID) -> str:
    """Read one uploaded invoice. Returns the status it ended in.

    Reading is the slow, failure-prone part — a PDF library, sometimes a model over the network — so
    it happens here rather than in the request. Nothing it produces is a cost: the invoice comes out
    in NEEDS_REVIEW, waiting for a person, or in FAILED with the reason.
    """
    org = db.get(Organization, org_id)
    if org is None:  # pragma: no cover - the caller just wrote it
        return "unknown-organization"
    with tenant(db, org):
        invoice = db.scalar(select(Invoice).where(Invoice.id == invoice_id, Invoice.org_id == org_id))
        if invoice is None:
            logger.warning("invoice %s no longer exists", invoice_id)
            return "unknown-invoice"
        store = get_store(db)
        fx = FxService(make_fetcher(get_settings().fx_api_url))
        try:
            invoices_service.extract(db, store, fx, org, invoice, get_extractors())
        except Exception as exc:
            db.rollback()
            logger.exception("extraction of invoice %s failed", invoice_id)
            failed = db.scalar(select(Invoice).where(Invoice.id == invoice_id))
            if failed is not None:
                failed.status = InvoiceStatus.FAILED
                failed.error = str(exc)[:500]
            db.commit()
            return InvoiceStatus.FAILED.value
        db.commit()
        return invoice.status.value


# ---------------------------------------------------------------------------- ERP


def sync_erp(db: Session, org_id: UUID | None = None, *, now: datetime | None = None) -> int:
    """Read every active ERP connection that is due, or one organization's. Returns syncs that worked.

    One failing customer's Odoo must not stop the others: each connection is its own transaction and
    its own run row, and a failure is recorded rather than raised. A connection that keeps failing is
    left alone for a while — see `erp_service.backoff_for` — so a customer whose server has been down
    for a week is not dialled every morning to write the same row again. Asking for one organization
    by name (`org_id`) ignores the backoff: that is a person asking, not the schedule.
    """
    from app.core.auth import Principal
    from app.domain.erp import service as erp_service
    from app.domain.models import ErpConnection, MemberRole

    moment = now or datetime.now(UTC)
    succeeded = 0
    for org in organizations(db):
        if org_id is not None and org.id != org_id:
            continue
        with tenant(db, org):
            connection = db.scalar(
                select(ErpConnection).where(ErpConnection.org_id == org.id, ErpConnection.active.is_(True))
            )
            if connection is None:
                continue
            if org_id is None and not erp_service.due_for_sync(connection, moment):
                logger.info(
                    "erp sync skipped: still backing off",
                    extra={
                        "org_id": str(org.id),
                        "retry_after": connection.retry_after.isoformat() if connection.retry_after else None,
                        "consecutive_failures": connection.consecutive_failures,
                    },
                )
                continue
            # The import pipeline works for a person; here the person is the schedule. The principal
            # is synthetic and carries no user, which is exactly what the audit trail should show.
            principal = Principal(org_id=org.id, user_id=None, role=MemberRole.OWNER, via="dev")
            t = TenantSession(db, principal, org)
            try:
                erp_service.sync(t, connection, fx=FxService(make_fetcher(get_settings().fx_api_url)))
                db.commit()
                succeeded += 1
            except Exception as exc:
                # The reason has to be read off the exception now: the rollback below throws away
                # the run row and the connection fields the service had already filled in.
                reason = f"{type(exc).__name__}: {exc}" if not str(exc) else str(exc)
                db.rollback()
                logger.exception("ERP sync failed for org %s", org.id)
                _record_failure(db, org, connection.id, reason, moment)
    return succeeded


def _record_failure(db: Session, org: Organization, connection_id: UUID, reason: str, at: datetime) -> None:
    """Write the failure again after the rollback that discarded it, with what actually went wrong.

    "See the application logs" is not something a customer can act on, and they cannot read our logs
    anyway. What goes on the screen is the ERP's own words — "Odoo is unreachable at …: timed out".
    """
    from app.domain.erp import service as erp_service
    from app.domain.models import ErpConnection, ErpSyncRun

    db.add(
        ErpSyncRun(
            org_id=org.id,
            connection_id=connection_id,
            status=ErpSyncStatus.FAILED,
            finished_at=at,
            error=reason[:1000],
        )
    )
    fresh = db.get(ErpConnection, connection_id)
    if fresh is not None:
        erp_service.note_failure(fresh, reason, at=at)
    db.commit()


# ---------------------------------------------------------------------------- sending the digest


def dispatch_alerts(db: Session, *, notifier: Notifier | None = None, now: datetime | None = None) -> int:
    """Send one digest per organization for everything not yet notified. Returns digests sent.

    Grouping is the point: an alert raised just after a round waits for the next one, five minutes
    later at worst, and nobody receives four e-mails in a minute.

    Failure used to have two shapes and both were silent. A digest with no recipient — an
    organization whose members came from Clerk without an e-mail — was marked sent and disappeared
    for good. A permanent refusal from the mail provider left its alerts unnotified and retried the
    same batch every five minutes, for ever, with no counter and nobody told. So: nothing is marked
    sent unless it really was, failures are counted per organization, and after
    `MAX_DIGEST_ATTEMPTS` the retry slows to hourly and the customer is told *inside the product* —
    which is the one channel that still works when e-mail does not.
    """
    sender = notifier or make_notifier()
    moment = now or datetime.now(UTC)
    sent = 0
    for org in organizations(db):
        with tenant(db, org):
            alerts = unnotified_alerts(db, org)
            if not alerts:
                continue
            attempts, last = _digest_failures(db, org)
            if attempts >= MAX_DIGEST_ATTEMPTS and last is not None and moment - last < DIGEST_BACKOFF:
                continue
            recipients = _recipients(db, org)
            reason = ""
            if not recipients:
                reason = "no recipient has an e-mail address"
            else:
                try:
                    sender.send(
                        Notification(
                            org_id=org.id,
                            org_name=org.name,
                            recipients=recipients,
                            alerts=list(alerts),
                            locale=org.locale,
                            container_numbers=_container_numbers(db, alerts),
                        )
                    )
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"[:200]
                    db.rollback()
            if reason:
                _digest_failed(db, org, attempts + 1, reason, moment)
                continue
            for alert in alerts:
                alert.notified_at = moment
            note_job_run(db, f"alerts.dispatch:{org.id}", moment, failures=0)
            db.commit()
            sent += 1
    return sent


def _digest_failures(db: Session, org: Organization) -> tuple[int, datetime | None]:
    row = db.execute(
        text("SELECT failures, last_run_at FROM job_runs WHERE name = :name"),
        {"name": f"alerts.dispatch:{org.id}"},
    ).first()
    return (int(row[0]), row[1]) if row is not None else (0, None)


def _digest_failed(db: Session, org: Organization, attempts: int, reason: str, moment: datetime) -> None:
    """Count the failure, log it at ERROR so Sentry sees it, and once we have given up, say so in
    the product. The alerts themselves stay unnotified: nothing is thrown away."""
    logger.error(
        "could not send the alert digest",
        extra={"org_id": str(org.id), "attempts": attempts, "reason": reason},
    )
    note_job_run(db, f"alerts.dispatch:{org.id}", moment, failures=attempts, detail=reason)
    if attempts < MAX_DIGEST_ATTEMPTS:
        return
    raise_alert(
        db,
        org.id,
        kind=AlertKind.TRACKING_DELIVERY_FAILED,
        severity=AlertSeverity.WARNING,
        title="Your alerts are no longer being e-mailed",
        body=(
            f"We could not send your digest after {attempts} attempts ({reason}). Your alerts stay "
            "visible here and will be sent again as soon as e-mail works."
        ),
        # One a day: the e-mail channel being down is one fact, not one fact per five minutes.
        dedup_key=f"digest-failed:{moment.date().isoformat()}",
        payload={"channel": codes.EMAIL_CHANNEL, "attempts": attempts, "reason": reason},
    )
    db.commit()


def _container_numbers(db: Session, alerts: list[Alert]) -> dict[UUID, str]:
    """The box numbers the digest's sentences name, in one query rather than one per alert."""
    ids = {alert.container_id for alert in alerts if alert.container_id is not None}
    if not ids:
        return {}
    rows = db.execute(select(Container.id, Container.container_number).where(Container.id.in_(ids))).all()
    return {row.id: row.container_number for row in rows}


def _recipients(db: Session, org: Organization) -> list[str]:
    """Everyone in the organization. There is no per-user notification preference yet, and inventing
    one from nothing would be worse than sending to all of a two-person team."""
    rows = db.execute(
        select(User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.org_id == org.id)
        .order_by(User.email)
    ).scalars()
    return [email for email in rows if email]


# ---------------------------------------------------------------------------- helpers for the tasks


def last_job_run(db: Session, name: str) -> datetime | None:
    """When a background job last got through its work. Raw SQL: `job_runs` is one row per job name,
    carries no tenant data and is therefore not an ORM model of anyone's."""
    at = db.scalar(text("SELECT last_run_at FROM job_runs WHERE name = :name"), {"name": name})
    return at if isinstance(at, datetime) else None


def note_job_run(
    db: Session, name: str, moment: datetime, *, failures: int = 0, detail: str | None = None
) -> None:
    db.execute(
        text(
            "INSERT INTO job_runs (name, last_run_at, failures, detail) "
            "VALUES (:name, :at, :failures, :detail) "
            "ON CONFLICT (name) DO UPDATE SET last_run_at = EXCLUDED.last_run_at, "
            "failures = EXCLUDED.failures, detail = EXCLUDED.detail"
        ),
        {"name": name, "at": moment, "failures": failures, "detail": detail},
    )
    db.commit()


def heartbeat(db: Session, *, now: datetime | None = None) -> datetime:
    """The worker saying it is alive. Nothing else in the product notices when it is not: there is no
    exception to report, there is simply no process, and every screen keeps showing the numbers it
    last computed. Read by `GET /healthz/worker`."""
    moment = now or datetime.now(UTC)
    note_job_run(db, WORKER_HEARTBEAT, moment)
    return moment


def daily_risk_due(db: Session, moment: datetime) -> bool:
    """Whether the morning demurrage pass should run now.

    Procrastinate's crons are UTC only and the users are in Paris, so this ticks hourly and reads the
    local clock. It used to be an equality on the hour, which meant a worker restarted between 06:00
    and 07:00 Paris skipped the whole day — no J-3 warnings that morning, and nothing to say so. It
    is a catch-up instead: past the hour, and only if the last run is old.
    """
    from zoneinfo import ZoneInfo

    if moment.astimezone(ZoneInfo(PARIS)).hour < DAILY_RISK_HOUR:
        return False
    last = last_job_run(db, DAILY_RISK_JOB)
    return last is None or moment - last >= CATCH_UP_AFTER


def weekly_reminder_due(db: Session, moment: datetime) -> bool:
    """The manual-tracking nudge goes out on Monday morning, Paris time — and catches up.

    Due when nothing has run since the most recent Monday at the morning hour. A plain "seven days
    since the last run" drifts a few hours earlier every week and ends up on a Thursday; an equality
    on Monday would skip the week whenever the worker is down that morning.
    """
    from zoneinfo import ZoneInfo

    local = moment.astimezone(ZoneInfo(PARIS))
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=DAILY_RISK_HOUR, minute=0, second=0, microsecond=0
    )
    if monday > local:  # Monday, before the hour: the slot that counts is last week's
        monday -= timedelta(days=7)
    last = last_job_run(db, MANUAL_REMINDER_JOB)
    return last is None or last < monday


# ---------------------------------------------------------------------------- backups


def check_backup_freshness(
    *,
    now: datetime | None = None,
    env: dict[str, str] | None = None,
    client: Any | None = None,
) -> str:
    """Look at the bucket rather than at the job that is supposed to fill it.

    Returns `ok`, `skipped`, `missing`, `stale` or `shrunk`. The nightly backup already fails loudly
    when it runs and cannot finish — but the failure mode that matters is the one where it stops
    running at all, or where a credential is removed: then nothing fails, because nothing happens,
    and the only trace is an absence in a bucket nobody opens. A backup that suddenly holds a third
    less than the one before is the other silent one: a dump taken against the wrong database, or a
    `pg_dump` that stopped early and still exited zero.

    Skipped in silence when there is no S3 configuration, because that is a development machine.
    """
    from app.adapters.s3 import S3Client
    from app.ops.backup import KEY_PREFIX, KEY_RE

    source = env if env is not None else dict(os.environ)
    if not all(source.get(name) for name in ("SCW_ACCESS_KEY", "SCW_SECRET_KEY", "SCW_BUCKET")):
        logger.info("backup freshness not checked: no bucket configured on this deployment")
        return "skipped"
    moment = now or datetime.now(UTC)
    prefix = f"{KEY_PREFIX}/{source.get('APP_ENV', 'dev')}/"
    s3 = client or S3Client(
        source["SCW_BUCKET"],
        source.get("SCW_REGION", "fr-par"),
        source["SCW_ACCESS_KEY"],
        source["SCW_SECRET_KEY"],
        source.get("SCW_ENDPOINT", "https://s3.fr-par.scw.cloud"),
    )
    # Dates come from the key, exactly as the retention pass reads them: an object re-uploaded today
    # is not a backup taken today.
    dated = sorted((obj for obj in s3.list(prefix) if KEY_RE.search(obj.key)), key=lambda o: o.key)
    if not dated:
        logger.error("no backup in the bucket at all", extra={"prefix": prefix})
        return "missing"
    newest = dated[-1]
    match = KEY_RE.search(newest.key)
    assert match is not None  # the filter above just proved it
    year, month, day, hhmm = match.groups()
    taken = datetime(int(year), int(month), int(day), int(hhmm[:2]), int(hhmm[2:]), tzinfo=UTC)
    if moment - taken > BACKUP_MAX_AGE:
        logger.error(
            "the newest backup is older than the window",
            extra={"key": newest.key, "taken_at": taken.isoformat(), "hours": BACKUP_MAX_AGE / HOUR},
        )
        return "stale"
    if len(dated) > 1 and dated[-2].size and newest.size < dated[-2].size * BACKUP_SHRINK_RATIO:
        logger.error(
            "the newest backup is much smaller than the one before it",
            extra={"key": newest.key, "size_bytes": newest.size, "previous_bytes": dated[-2].size},
        )
        return "shrunk"
    logger.info("backup is fresh", extra={"key": newest.key, "size_bytes": newest.size})
    return "ok"


# ---------------------------------------------------------------------------- helpers for the tasks


def unnotified_alerts(db: Session, org: Organization) -> list[Alert]:
    return list(
        db.scalars(
            select(Alert)
            .where(Alert.org_id == org.id, Alert.notified_at.is_(None))
            .order_by(Alert.created_at)
        )
    )
