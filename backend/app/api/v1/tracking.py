"""Tracking endpoints: the timeline of a container, manual events, and its provider subscription."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.v1 import schemas
from app.api.v1.deps import ProviderFactoryDep, WriterDep
from app.core.errors import Conflict, NotFound
from app.core.tenancy import TenantDep
from app.domain.audit.service import TRACKING_EVENT_ADDED, record
from app.domain.models import Container, EtaHistory, Organization, TrackingState, TrackingSubscription
from app.domain.tracking.manual import NAME as MANUAL
from app.domain.tracking.manual import manual_event
from app.domain.tracking.ports import ProviderNotConfigured, SubscribeRequest
from app.domain.tracking.service import history, ingest, refresh

router = APIRouter(prefix="/containers", tags=["tracking"])


def default_provider(org: Organization) -> str:
    """Which tracker this organization uses when nobody says. A setting, not a guess."""
    settings = org.settings if isinstance(org.settings, dict) else {}
    chosen = settings.get("tracking_provider_default")
    return str(chosen) if isinstance(chosen, str) and chosen else MANUAL


def _container(t: TenantDep, container_id: UUID) -> Container:
    c = t.db.scalar(t.q(Container).where(Container.id == container_id, Container.archived_at.is_(None)))
    if c is None:
        raise NotFound("Container")
    return c


@router.get("/{container_id}/events", response_model=list[schemas.TrackingEventResponse])
def list_events(container_id: UUID, t: TenantDep) -> list[schemas.TrackingEventResponse]:
    """The container's timeline, oldest first, estimates included."""
    c = _container(t, container_id)
    return [schemas.TrackingEventResponse.model_validate(e) for e in history(t.db, t.org_id, c.id)]


@router.post(
    "/{container_id}/events",
    response_model=list[schemas.TrackingEventResponse],
    status_code=status.HTTP_201_CREATED,
)
def add_event(
    container_id: UUID, payload: schemas.TrackingEventCreate, t: TenantDep, p: WriterDep
) -> list[schemas.TrackingEventResponse]:
    """Record a milestone by hand and re-derive the container from its whole history.

    Entering the same milestone at the same instant twice is not an error and writes nothing new.
    """
    c = _container(t, container_id)
    event = manual_event(
        payload.code,
        payload.occurred_at,
        is_estimate=payload.is_estimate,
        location_unlocode=payload.location_unlocode,
        location_name=payload.location_name,
        vessel_name=payload.vessel_name,
        voyage=payload.voyage,
        note=payload.note or "",
    )
    ingest(t.db, t.org_id, c.id, [event], provider=MANUAL)
    if c.tracking_state is TrackingState.UNTRACKED:
        c.tracking_state = TrackingState.MANUAL
    refresh(t.db, t.org, c, source=MANUAL)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=TRACKING_EVENT_ADDED,
        entity_type="container",
        entity_id=c.id,
        after={
            "code": payload.code,
            "occurred_at": payload.occurred_at,
            "is_estimate": payload.is_estimate,
            "milestone": c.milestone,
            "last_free_day": c.last_free_day,
        },
    )
    t.db.commit()
    return list_events(container_id, t)


@router.get("/{container_id}/eta-history", response_model=list[schemas.EtaHistoryResponse])
def list_eta_history(container_id: UUID, t: TenantDep) -> list[schemas.EtaHistoryResponse]:
    c = _container(t, container_id)
    rows = t.db.scalars(
        select(EtaHistory)
        .where(EtaHistory.org_id == t.org_id, EtaHistory.container_id == c.id)
        .order_by(EtaHistory.recorded_at.desc())
    )
    return [schemas.EtaHistoryResponse.model_validate(r) for r in rows]


@router.get("/{container_id}/tracking", response_model=schemas.TrackingSubscriptionResponse)
def get_subscription(container_id: UUID, t: TenantDep) -> TrackingSubscription:
    c = _container(t, container_id)
    sub = t.db.scalar(
        t.q(TrackingSubscription).where(
            TrackingSubscription.container_id == c.id, TrackingSubscription.ended_at.is_(None)
        )
    )
    if sub is None:
        raise NotFound("Tracking subscription")
    return sub


@router.post(
    "/{container_id}/tracking",
    response_model=schemas.TrackingSubscriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
def subscribe(
    container_id: UUID,
    payload: schemas.TrackingSubscriptionCreate,
    t: TenantDep,
    make_provider: ProviderFactoryDep,
    _: WriterDep,
) -> TrackingSubscription:
    """Start tracking this container.

    With a `provider_ref` the deployment is in webhook-only mode: the provider was subscribed
    elsewhere (its dashboard, another account) and we only need to know how to route its deliveries.
    Without one, the provider's API is called, which needs a key.
    """
    c = _container(t, container_id)
    existing = t.db.scalar(t.q(TrackingSubscription).where(TrackingSubscription.container_id == c.id))
    if existing is not None and existing.ended_at is None:
        raise Conflict(f"Container {c.container_number} is already tracked by {existing.provider}")

    provider = make_provider(payload.provider or default_provider(t.org))
    now = datetime.now(UTC)
    provider_ref: str | None
    if payload.provider_ref:
        provider_ref, state, message = payload.provider_ref, "active", None
    else:
        try:
            result = provider.subscribe(
                SubscribeRequest(
                    identifier=payload.identifier or c.container_number,
                    identifier_type=payload.identifier_type,
                    scac=c.carrier_scac,
                    callback_url="",  # the provider posts to the deployment's webhook route
                    external_ref=str(c.id),
                )
            )
            provider_ref, state, message = result.provider_ref, result.status, result.message
        except ProviderNotConfigured as exc:
            # Webhook-only: no API key to subscribe with, so deliveries will be routed by container
            # number instead. Nothing is broken; the deployment just cannot ask for the feed itself.
            provider_ref, state, message = None, "active", f"webhook-only: {exc.message}"

    if existing is not None:
        sub = existing
        sub.provider, sub.provider_ref, sub.status = provider.name, provider_ref, state
        sub.last_error, sub.subscribed_at, sub.ended_at = message, now, None
    else:
        sub = t.add(
            TrackingSubscription(
                container_id=c.id,
                provider=provider.name,
                provider_ref=provider_ref,
                status=state,
                last_error=message,
                subscribed_at=now,
            )
        )
    c.tracking_state = {
        "active": TrackingState.ACTIVE,
        "pending": TrackingState.PENDING,
        "failed": TrackingState.FAILED,
    }[state]
    if provider.name == MANUAL:
        c.tracking_state = TrackingState.MANUAL
    t.db.commit()
    t.db.refresh(sub)
    return sub


@router.delete("/{container_id}/tracking", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(container_id: UUID, t: TenantDep, make_provider: ProviderFactoryDep, _: WriterDep) -> None:
    """Stop tracking. The events already collected stay: they are the container's history."""
    c = _container(t, container_id)
    sub = t.db.scalar(t.q(TrackingSubscription).where(TrackingSubscription.container_id == c.id))
    if sub is None or sub.ended_at is not None:
        raise NotFound("Tracking subscription")
    if sub.provider_ref:
        make_provider(sub.provider).unsubscribe(sub.provider_ref)
    sub.ended_at = datetime.now(UTC)
    sub.status = "ended"
    c.tracking_state = TrackingState.ENDED
    t.db.commit()
