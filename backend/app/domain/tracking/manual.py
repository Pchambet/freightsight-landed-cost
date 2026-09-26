"""The provider that is a person: someone types the milestone in.

It is what Phase 1 did with `PATCH /containers/{id}`, rewired onto the port so that manual and
automatic tracking write the same events and go through the same derivation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

from app.core.errors import DomainError
from app.domain.models import MilestoneCode
from app.domain.tracking.ports import (
    ProviderEvent,
    ProviderNotConfigured,
    SubscribeRequest,
    Subscription,
    WebhookEnvelope,
)

NAME = "manual"


def event_key(code: MilestoneCode, occurred_at: datetime, is_estimate: bool) -> str:
    """The same milestone at the same instant is the same event, typed twice."""
    raw = f"{NAME}|{code.value}|{occurred_at.astimezone(UTC).isoformat()}|{int(is_estimate)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def manual_event(
    code: MilestoneCode,
    occurred_at: datetime,
    *,
    is_estimate: bool = False,
    location_unlocode: str | None = None,
    location_name: str | None = None,
    vessel_name: str | None = None,
    voyage: str | None = None,
    note: str = "",
) -> ProviderEvent:
    return ProviderEvent(
        code=code,
        occurred_at=occurred_at,
        is_estimate=is_estimate,
        provider_event_key=event_key(code, occurred_at, is_estimate),
        location_unlocode=location_unlocode,
        location_name=location_name,
        vessel_name=vessel_name,
        voyage=voyage,
        raw_description=note,
        source="manual",
    )


class ManualProvider:
    """Satisfies `TrackingProvider` so the rest of the code has one shape to deal with. It cannot be
    subscribed to and receives no webhooks: the events come from the API."""

    name = NAME

    def subscribe(self, req: SubscribeRequest) -> Subscription:
        return Subscription(provider=NAME, provider_ref=req.external_ref, status="active")

    def unsubscribe(self, provider_ref: str) -> None:
        return None

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        raise ProviderNotConfigured("Manual tracking has nothing to poll")

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        raise DomainError("Manual tracking does not receive webhooks", code="NO_WEBHOOK")

    def parse(self, body: bytes) -> WebhookEnvelope:
        raise DomainError("Manual tracking does not receive webhooks", code="NO_WEBHOOK")
