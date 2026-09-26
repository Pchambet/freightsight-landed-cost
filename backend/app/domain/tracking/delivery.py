"""Receiving a webhook, from the raw bytes to the container's new state.

The rules, in order:
  1. the body has to be provably the provider's, or nothing is written at all (401);
  2. the delivery is stored whole, and that alone is what the 200 acknowledges;
  3. processing happens after, in its own transaction, and can never turn a stored delivery into a
     retry storm: a failure is recorded on the delivery and still answered 200.

A delivery arrives with no tenant context. `tracking_resolve_subscription` — a SECURITY DEFINER
function created in migration 0004 — is the only lookup allowed to cross organizations, and it
returns two ids and nothing else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Container, Organization, TrackingState, WebhookDelivery
from app.domain.tracking.ports import TrackingProvider, WebhookEnvelope
from app.domain.tracking.service import ingest, refresh

logger = logging.getLogger(__name__)

#: Headers worth keeping for a post-mortem. Anything else (cookies, proxy noise) is dropped.
KEEP_HEADERS = ("content-type", "user-agent", "x-t49-webhook-signature", "x-vizion-signature")


@dataclass(frozen=True)
class Received:
    delivery_id: UUID
    status: str  # processed | duplicate | failed | unroutable
    events_ingested: int = 0
    detail: str | None = None
    #: Who the delivery turned out to belong to, when routing got that far. A caller that has to
    #: say something to the customer about a failure needs it, and re-routing to find out means
    #: parsing the body a second time.
    org_id: UUID | None = None
    container_id: UUID | None = None


def _kept_headers(headers: dict[str, str]) -> dict[str, Any]:
    lowered = {k.lower(): v for k, v in headers.items()}
    return {k: lowered[k] for k in KEEP_HEADERS if k in lowered}


def _store(
    db: Session, provider: str, envelope: WebhookEnvelope, headers: dict[str, str], body: bytes
) -> tuple[UUID, bool]:
    """Store the delivery. Returns its id and whether this call is the one that created it."""
    stmt = (
        insert(WebhookDelivery)
        .values(
            provider=provider,
            delivery_id=envelope.delivery_id,
            headers=_kept_headers(headers),
            body=body,
            signature_ok=True,
            status="received",
        )
        .on_conflict_do_nothing(index_elements=["provider", "delivery_id"])
        .returning(WebhookDelivery.id)
    )
    created = db.scalar(stmt)
    db.commit()
    if created is not None:
        return created, True
    existing = db.scalar(
        select(WebhookDelivery.id).where(
            WebhookDelivery.provider == provider, WebhookDelivery.delivery_id == envelope.delivery_id
        )
    )
    assert existing is not None  # the conflict we just lost means the row is there
    return existing, False


def _route(db: Session, provider: str, envelope: WebhookEnvelope) -> tuple[UUID, UUID] | None:
    rows = db.execute(
        text(
            "SELECT org_id, container_id FROM tracking_resolve_subscription("
            ":provider, :provider_ref, :container_number)"
        ),
        {
            "provider": provider,
            "provider_ref": envelope.provider_ref,
            "container_number": envelope.container_number,
        },
    ).all()
    if len(rows) != 1:
        return None
    return UUID(str(rows[0][0])), UUID(str(rows[0][1]))


def _finish(
    db: Session, delivery_id: UUID, status: str, error: str | None = None, *, attempt: int = 1
) -> None:
    row = db.get(WebhookDelivery, delivery_id)
    if row is not None:
        row.status = status
        row.processed_at = datetime.now(UTC)
        row.error = error
        row.attempts = attempt
    db.commit()


def receive(
    db: Session,
    provider: TrackingProvider,
    headers: dict[str, str],
    body: bytes,
    *,
    now: datetime | None = None,
) -> Received:
    """Verify, store, then process. Raises `InvalidSignature` before anything is written."""
    envelope = provider.verify_and_parse(headers, body)
    delivery_id, created = _store(db, provider.name, envelope, headers, body)
    if not created:
        return Received(delivery_id, "duplicate")
    return _run(db, provider, envelope, delivery_id, attempt=1, now=now)


def process_stored_delivery(
    db: Session,
    provider: TrackingProvider,
    delivery: WebhookDelivery,
    *,
    attempt: int,
    now: datetime | None = None,
) -> Received:
    """Replay a delivery we already verified once, after a failure on our side.

    The signature is not checked again: it was checked at receipt, `signature_ok` records that, and
    the body has not left our own table since. Nor is freshness — a replay is old by definition.
    """
    try:
        envelope = provider.parse(delivery.body)
    except Exception as exc:
        _finish(db, delivery.id, "failed", f"unparseable on replay: {exc}"[:500], attempt=attempt)
        return Received(delivery.id, "failed", detail=str(exc)[:200])
    return _run(db, provider, envelope, delivery.id, attempt=attempt, now=now)


def _run(
    db: Session,
    provider: TrackingProvider,
    envelope: WebhookEnvelope,
    delivery_id: UUID,
    *,
    attempt: int,
    now: datetime | None = None,
) -> Received:
    """Route the delivery to a container and apply it. Never raises: a delivery that cannot be
    processed is marked and answered, because the alternative is a provider retrying forever.

    One delivery can be about several containers — a bill of lading whose arrival moved carries every
    box on it. Each part is routed on its own, because two boxes of the same bill can perfectly well
    belong to two of our customers, and a part we do not track is simply not ours to apply.
    """
    org_id: UUID | None = None
    container_id: UUID | None = None
    count = 0
    try:
        parts = [(part, _route(db, provider.name, part)) for part in (envelope, *envelope.others)]
        applied = [(part, routed) for part, routed in parts if routed is not None]
        if not applied:
            _finish(
                db,
                delivery_id,
                "unroutable",
                "No single tracking subscription matches this delivery",
                attempt=attempt,
            )
            return Received(delivery_id, "unroutable", detail="No matching tracking subscription")
        for part, routed in applied:
            part_org_id, part_container_id = routed
            org_id, container_id = org_id or part_org_id, container_id or part_container_id
            set_current_org(db, part_org_id)
            org = db.get(Organization, part_org_id)
            container = db.get(Container, part_container_id)
            if org is None or container is None:  # pragma: no cover - the resolver proved they exist
                _finish(db, delivery_id, "failed", "Organization or container vanished", attempt=attempt)
                return Received(delivery_id, "failed", org_id=org_id, container_id=container_id)
            count += ingest(
                db,
                part_org_id,
                part_container_id,
                part.events,
                provider=provider.name,
                delivery_id=delivery_id,
            )
            if part.destination_unlocode and container.shipment is not None:
                container.shipment.destination_unlocode = part.destination_unlocode
            container.tracking_state = TrackingState.ACTIVE
            refresh(db, org, container, source=provider.name, now=now)
            db.commit()
    except Exception as exc:  # a bad delivery must not turn into a provider retry storm
        db.rollback()
        logger.exception("tracking delivery %s failed", delivery_id)
        _finish(db, delivery_id, "failed", str(exc)[:500], attempt=attempt)
        return Received(
            delivery_id, "failed", detail=str(exc)[:200], org_id=org_id, container_id=container_id
        )
    finally:
        set_current_org(db, None)

    _finish(db, delivery_id, "processed", attempt=attempt)
    return Received(delivery_id, "processed", events_ingested=count, org_id=org_id, container_id=container_id)
