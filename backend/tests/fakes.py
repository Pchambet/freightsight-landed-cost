"""A tracking provider that exists only in tests: it signs and parses a payload of our own shape.

It stands in for a real provider wherever the test is about *our* pipeline — routing, idempotence,
derivation — rather than about a vendor's wire format. The Terminal49 tests use real fixtures instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.models import MilestoneCode
from app.domain.tracking.ports import (
    InvalidSignature,
    ProviderEvent,
    ProviderNotConfigured,
    SubscribeRequest,
    Subscription,
    WebhookEnvelope,
)

SECRET = "fake-webhook-secret"
SIGNATURE_HEADER = "x-fake-signature"
MAX_AGE = timedelta(minutes=5)


def sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def payload(
    *,
    delivery_id: str,
    provider_ref: str | None = None,
    container_number: str | None = None,
    events: list[dict[str, Any]],
    sent_at: datetime | None = None,
) -> bytes:
    body = {
        "delivery_id": delivery_id,
        "provider_ref": provider_ref,
        "container_number": container_number,
        "sent_at": (sent_at or datetime.now(UTC)).isoformat(),
        "events": events,
    }
    return json.dumps(body).encode()


def event(
    code: MilestoneCode, occurred_at: datetime, *, is_estimate: bool = False, key: str | None = None
) -> dict[str, Any]:
    return {
        "code": code.value,
        "occurred_at": occurred_at.isoformat(),
        "is_estimate": is_estimate,
        "key": key or f"{code.value}:{occurred_at.isoformat()}:{int(is_estimate)}",
    }


class FakeProvider:
    name = "fake"

    def __init__(self, secret: str = SECRET, *, can_subscribe: bool = True) -> None:
        self.secret = secret
        self.can_subscribe = can_subscribe

    def subscribe(self, req: SubscribeRequest) -> Subscription:
        if not self.can_subscribe:  # the webhook-only deployment: no API key
            raise ProviderNotConfigured("FAKE_API_KEY is not set")
        return Subscription(self.name, provider_ref=f"fake-{req.identifier}", status="active")

    def unsubscribe(self, provider_ref: str) -> None:
        return None

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        raise ProviderNotConfigured("no polling in tests")

    def parse(self, body: bytes) -> WebhookEnvelope:
        return self._envelope(json.loads(body))

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        lowered = {k.lower(): v for k, v in headers.items()}
        given = lowered.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(given, sign(body, self.secret)):
            raise InvalidSignature("Bad signature")
        data = json.loads(body)
        sent_at = datetime.fromisoformat(data["sent_at"])
        if datetime.now(UTC) - sent_at > MAX_AGE:
            raise InvalidSignature("Delivery too old")
        return self._envelope(data)

    def _envelope(self, data: dict[str, Any]) -> WebhookEnvelope:
        return WebhookEnvelope(
            provider=self.name,
            delivery_id=data["delivery_id"],
            provider_ref=data.get("provider_ref"),
            container_number=data.get("container_number"),
            events=[
                ProviderEvent(
                    code=MilestoneCode(e["code"]),
                    occurred_at=datetime.fromisoformat(e["occurred_at"]),
                    is_estimate=e["is_estimate"],
                    provider_event_key=e["key"],
                    source="carrier",
                )
                for e in data["events"]
            ],
        )
