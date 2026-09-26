"""Terminal49, the first real provider.

Built from the public documentation (terminal49.com/docs/api-docs): JSON:API over
`https://api.terminal49.com/v2`, `Authorization: Token <key>`, webhooks signed with an HMAC-SHA256
hex digest of the raw body in `X-T49-Webhook-Signature`.

Two modes, on purpose:
  * **webhook-only** — a signing secret and nothing else. Deliveries are verified, parsed and routed
    by container number. This is what a deployment without an API key gets, and it works.
  * **subscribed** — with an API key, `subscribe` creates a tracking request and `fetch` re-reads a
    container's transport events.

Open questions the public documentation does not settle are flagged in the code where they matter,
rather than guessed at here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.http import timeout as http_timeout
from app.domain.models import MilestoneCode
from app.domain.tracking.ports import (
    DataSource,
    InvalidSignature,
    ProviderEvent,
    ProviderNotConfigured,
    ProviderUnavailable,
    SubscribeRequest,
    Subscription,
    WebhookEnvelope,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


NAME = "terminal49"
SIGNATURE_HEADER = "x-t49-webhook-signature"
BASE_URL = "https://api.terminal49.com/v2"

#: Terminal49 event name -> our milestone. Anything absent is stored as UNKNOWN: it stays in the
#: timeline, it never moves the container, and it tells us what to add to this table.
EVENT_MILESTONES: dict[str, MilestoneCode] = {
    "container.transport.empty_out": MilestoneCode.GATE_OUT_EMPTY_ORIGIN,
    "container.transport.full_in": MilestoneCode.GATE_IN_FULL_ORIGIN,
    "container.transport.vessel_loaded": MilestoneCode.LOADED,
    "container.transport.vessel_departed": MilestoneCode.VESSEL_DEPARTED,
    "container.transport.transshipment_arrived": MilestoneCode.TRANSSHIPMENT_ARRIVED,
    "container.transport.transshipment_discharged": MilestoneCode.TRANSSHIPMENT_DISCHARGED,
    "container.transport.transshipment_loaded": MilestoneCode.TRANSSHIPMENT_LOADED,
    "container.transport.transshipment_departed": MilestoneCode.TRANSSHIPMENT_DEPARTED,
    "container.transport.feeder_arrived": MilestoneCode.TRANSSHIPMENT_ARRIVED,
    "container.transport.feeder_discharged": MilestoneCode.TRANSSHIPMENT_DISCHARGED,
    "container.transport.feeder_loaded": MilestoneCode.TRANSSHIPMENT_LOADED,
    "container.transport.feeder_departed": MilestoneCode.TRANSSHIPMENT_DEPARTED,
    "container.transport.vessel_arrived": MilestoneCode.VESSEL_ARRIVED,
    "container.transport.vessel_berthed": MilestoneCode.VESSEL_ARRIVED,
    "container.transport.vessel_discharged": MilestoneCode.DISCHARGED,
    "container.transport.available": MilestoneCode.AVAILABLE_FOR_PICKUP,
    "container.transport.full_out": MilestoneCode.GATE_OUT_FULL,
    "container.transport.delivered": MilestoneCode.DELIVERED,
    "container.transport.empty_in": MilestoneCode.GATE_IN_EMPTY_RETURN,
    "container.transport.rail_loaded": MilestoneCode.RAIL_LOADED,
    "container.transport.rail_departed": MilestoneCode.RAIL_DEPARTED,
    "container.transport.rail_arrived": MilestoneCode.RAIL_ARRIVED,
    "container.transport.rail_unloaded": MilestoneCode.RAIL_UNLOADED,
    "container.transport.arrived_at_inland_destination": MilestoneCode.DELIVERED,
    "container.transport.estimated.vessel_departed": MilestoneCode.VESSEL_DEPARTED,
    "container.transport.estimated.vessel_arrived": MilestoneCode.VESSEL_ARRIVED,
    "container.transport.estimated.arrived_at_inland_destination": MilestoneCode.DELIVERED,
    "shipment.estimated.arrival": MilestoneCode.VESSEL_ARRIVED,
}

#: `data_source` on a transport event.
SOURCES: dict[str, DataSource] = {
    "shipping_line": "carrier",
    "terminal": "terminal",
    "ais": "ais",
}

IDENTIFIER_TYPES = {
    "container": "container",
    "bill_of_lading": "bill_of_lading",
    "booking": "booking_number",
}


def is_estimate(event_name: str) -> bool:
    return ".estimated." in event_name or event_name.endswith(".estimated.arrival")


#: Terminal49 answers a subscription or a container read quickly, or not at all.
TIMEOUT_SECONDS = 20.0


class Terminal49Provider:
    """`verify_and_parse` needs only the signing secret; everything that talks to the API needs a key."""

    name = NAME

    def __init__(
        self,
        secret: str | None,
        api_key: str | None = None,
        *,
        base_url: str = BASE_URL,
        max_age: timedelta = timedelta(minutes=15),
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.secret = secret
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_age = max_age
        self._client = client
        self._clock = clock

    # -------------------------------------------------------------------- webhooks

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        if not self.secret:
            raise ProviderNotConfigured(
                "TRACKING_WEBHOOK_SECRET_TERMINAL49 is not set: deliveries cannot be verified"
            )
        lowered = {k.lower(): v for k, v in headers.items()}
        given = lowered.get(SIGNATURE_HEADER, "")
        expected = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(given, expected):
            raise InvalidSignature("Signature does not match the request body")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise InvalidSignature(f"Delivery is not JSON: {exc}") from exc
        self._check_age(payload)
        return self._parse(payload)

    def parse(self, body: bytes) -> WebhookEnvelope:
        """Translate a delivery we already verified once. Replaying our own stored body does not need
        the signature again — and must not be refused for being old, which is the point of a replay."""
        return self._parse(json.loads(body))

    def _check_age(self, payload: dict[str, Any]) -> None:
        """Terminal49 publishes no timestamp header, so freshness is judged on the notification's own
        `created_at`. A replay of an old, correctly signed body is refused."""
        created = _get(payload, "data", "attributes", "created_at")
        sent_at = _time(created)
        if sent_at is None:
            return  # no timestamp to judge: the signature is all we have
        if self._clock() - sent_at > self.max_age:
            raise InvalidSignature("Delivery is older than the accepted window")

    def _parse(self, payload: dict[str, Any]) -> WebhookEnvelope:
        data = payload.get("data") or {}
        attributes = data.get("attributes") or {}
        event_name = str(attributes.get("event", ""))
        included = payload.get("included") or []
        by_type: dict[str, list[dict[str, Any]]] = {}
        for resource in included:
            by_type.setdefault(str(resource.get("type")), []).append(resource)

        containers = by_type.get("container") or [{}]
        vessels: dict[str, str] = {
            str(v.get("id")): str((v.get("attributes") or {}).get("name") or "")
            for v in by_type.get("vessel", [])
        }

        # A shipment-level notification — `shipment.estimated.arrival` is the common one — comes with
        # every container on the bill of lading. Each transport event names the container it belongs
        # to; one that names none belongs to the notification's subject, which is the first.
        transports: dict[str, list[dict[str, Any]]] = {}
        primary_id = str(containers[0].get("id") or "")
        for transport in by_type.get("transport_event", []):
            owner = _get(transport, "relationships", "container", "data", "id")
            transports.setdefault(str(owner) if owner else primary_id, []).append(transport)

        delivery_id = str(data.get("id") or "")
        envelopes = [
            self._for_container(payload, container, transports, vessels, event_name, delivery_id)
            for container in containers
        ]
        first, others = envelopes[0], envelopes[1:]
        return replace(first, others=others) if others else first

    def _for_container(
        self,
        payload: dict[str, Any],
        container: dict[str, Any],
        transports: Mapping[str, list[dict[str, Any]]],
        vessels: Mapping[str, str],
        event_name: str,
        delivery_id: str,
    ) -> WebhookEnvelope:
        container_attrs = container.get("attributes") or {}
        container_id = str(container.get("id") or "")
        events: list[ProviderEvent] = []
        for transport in transports.get(container_id, []):
            event = self._event(transport, event_name, vessels)
            if event is not None:
                events.append(event)
        eta = _time(container_attrs.get("pod_eta_at"))
        # An `estimated` notification with no transport event of its own still carries the new ETA.
        if not events and eta is not None and is_estimate(event_name):
            events.append(
                ProviderEvent(
                    code=MilestoneCode.VESSEL_ARRIVED,
                    occurred_at=eta,
                    is_estimate=True,
                    provider_event_key=_key(NAME, event_name, eta.isoformat(), "estimate"),
                    raw_description=event_name,
                    source="carrier",
                )
            )

        return WebhookEnvelope(
            provider=NAME,
            delivery_id=delivery_id,
            provider_ref=container_id or None,
            container_number=container_attrs.get("number"),
            events=events,
            eta=eta,
            destination_unlocode=container_attrs.get("pod_locode"),
            raw=payload,
        )

    def _event(
        self, transport: dict[str, Any], notification_event: str, vessels: Mapping[str, str]
    ) -> ProviderEvent | None:
        attrs = transport.get("attributes") or {}
        name = str(attrs.get("event") or notification_event)
        occurred_at = _time(attrs.get("timestamp")) or _time(attrs.get("created_at"))
        if occurred_at is None:
            return None
        estimate = is_estimate(name)
        vessel_id = _get(transport, "relationships", "vessel", "data", "id")
        return ProviderEvent(
            code=EVENT_MILESTONES.get(name, MilestoneCode.UNKNOWN),
            occurred_at=occurred_at,
            is_estimate=estimate,
            # The provider's own event id would be enough, except that Terminal49 reissues one when a
            # milestone is corrected; the timestamp and the estimate flag are what make two rows two
            # facts rather than one fact twice.
            provider_event_key=_key(
                str(transport.get("id") or ""), name, occurred_at.isoformat(), str(int(estimate))
            ),
            location_unlocode=attrs.get("location_locode"),
            location_name=attrs.get("location_name"),
            vessel_name=vessels.get(str(vessel_id)) or None if vessel_id else None,
            voyage=attrs.get("voyage_number"),
            raw_description=name,
            source=SOURCES.get(str(attrs.get("data_source")), "unknown"),
        )

    # -------------------------------------------------------------------- API

    def _http(self) -> httpx.Client:
        if not self.api_key:
            raise ProviderNotConfigured(
                "TERMINAL49_API_KEY is not set: this deployment can receive Terminal49 webhooks "
                "but cannot ask Terminal49 for anything"
            )
        return self._client or httpx.Client(
            base_url=self.base_url,
            timeout=http_timeout(TIMEOUT_SECONDS),
            headers={
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/vnd.api+json",
            },
        )

    def subscribe(self, req: SubscribeRequest) -> Subscription:
        body = {
            "data": {
                "type": "tracking_request",
                "attributes": {
                    "request_type": IDENTIFIER_TYPES[req.identifier_type],
                    "request_number": req.identifier,
                    "ref_numbers": [req.external_ref],
                    **({"scac": req.scac} if req.scac else {"auto_detect_vocc_scac": True}),
                },
            }
        }
        client = self._http()
        try:
            response = client.post("/tracking_requests", json=body)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Terminal49 is unreachable: {exc}") from exc
        if response.status_code >= 400:
            return Subscription(NAME, provider_ref="", status="failed", message=_error(response))
        data = response.json().get("data") or {}
        attributes = data.get("attributes") or {}
        status = str(attributes.get("status") or "pending")
        return Subscription(
            NAME,
            provider_ref=str(data.get("id") or ""),
            status="failed" if status == "failed" else ("active" if status == "created" else "pending"),
            message=attributes.get("failed_reason"),
        )

    def unsubscribe(self, provider_ref: str) -> None:
        client = self._http()
        try:
            client.delete(f"/tracking_requests/{provider_ref}")
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Terminal49 is unreachable: {exc}") from exc

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        """Re-read a container's transport events, for when a delivery was lost."""
        client = self._http()
        try:
            response = client.get(f"/containers/{provider_ref}/transport_events")
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Terminal49 is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderUnavailable(_error(response))
        payload = response.json()
        transports = payload.get("data") or []
        vessels: dict[str, str] = {
            str(v.get("id")): str((v.get("attributes") or {}).get("name") or "")
            for v in payload.get("included", [])
            if v.get("type") == "vessel"
        }
        events = [e for t in transports if (e := self._event(t, "", vessels)) is not None]
        return WebhookEnvelope(
            provider=NAME,
            delivery_id=f"poll:{provider_ref}:{datetime.now(UTC).isoformat()}",
            provider_ref=provider_ref,
            events=events,
            raw=payload,
        )


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _get(payload: Mapping[str, Any], *path: str) -> Any:
    current: Any = payload
    for step in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(step)
    return current


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _error(response: httpx.Response) -> str:
    try:
        errors = response.json().get("errors") or []
        detail = "; ".join(str(e.get("detail") or e.get("title")) for e in errors)
    except ValueError:
        detail = response.text[:200]
    return f"Terminal49 answered {response.status_code}: {detail or 'no detail'}"
