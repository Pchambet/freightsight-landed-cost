"""Shipsgo, the cheaper tracker, on their v2 API.

Written against their published OpenAPI specification (api.shipsgo.com/docs/v2/specs/openapi.json)
rather than the v1.2 PDF: v2 is machine-readable, is what their dashboard issues tokens for, and —
the deciding point — **signs its webhooks**. The v1.2 API is credit-based polling with no documented
signature at all, which would have meant either an unauthenticated endpoint or a secret in a URL.

Their documentation even publishes a test vector for the signature, so the HMAC here is checked
against the vendor's own expected value in the tests rather than against my reading of their prose.

Ocean shipments only. Their air API exists and is not our problem yet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
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

logger = logging.getLogger(__name__)

#: A shipment read can walk a whole voyage's movements, so the answer is allowed to be slow.
TIMEOUT_SECONDS = 30.0

NAME = "shipsgo"
BASE_URL = "https://api.shipsgo.com/v2"
TOKEN_HEADER = "X-Shipsgo-User-Token"
SIGNATURE_HEADER = "x-shipsgo-webhook-signature"

#: Their movement codes. Eight of them, and every one has an equivalent of ours.
EVENT_MILESTONES: dict[str, MilestoneCode] = {
    "EMSH": MilestoneCode.GATE_OUT_EMPTY_ORIGIN,  # empty released to the shipper
    "GTIN": MilestoneCode.GATE_IN_FULL_ORIGIN,
    "LOAD": MilestoneCode.LOADED,
    "DEPA": MilestoneCode.VESSEL_DEPARTED,
    "ARRV": MilestoneCode.VESSEL_ARRIVED,
    "DISC": MilestoneCode.DISCHARGED,
    "GTOT": MilestoneCode.GATE_OUT_FULL,
    "EMRT": MilestoneCode.GATE_IN_EMPTY_RETURN,
}

#: The shipment-level status, used only when a shipment carries no movements at all.
SHIPMENT_STATUS_MILESTONES: dict[str, MilestoneCode] = {
    "BOOKED": MilestoneCode.BOOKED,
    "LOADED": MilestoneCode.LOADED,
    "SAILING": MilestoneCode.VESSEL_DEPARTED,
    "ARRIVED": MilestoneCode.VESSEL_ARRIVED,
    "DISCHARGED": MilestoneCode.DISCHARGED,
}

#: The same movement, when it happens somewhere that is not the port of discharge. Shipsgo publishes
#: one `DISC` for the transhipment and one for the real thing, with nothing but the port to tell them
#: apart — and a transhipment discharge that starts the demurrage clock tells a customer their box is
#: incurring charges at Le Havre while it is still on a ship off Tanger.
#:
#: Only the two codes that can be told apart by the port alone: a box is never discharged or arrived
#: at the port it loaded in, so anywhere that is not the port of discharge is a call on the way.
#: `LOAD` and `DEPA` happen at the origin *and* at every transhipment and would need the port of
#: loading as well to be separated — and neither of them starts a clock, so they are left alone.
TRANSSHIPMENT: dict[str, MilestoneCode] = {
    "ARRV": MilestoneCode.TRANSSHIPMENT_ARRIVED,
    "DISC": MilestoneCode.TRANSSHIPMENT_DISCHARGED,
}

#: Their `status` on a movement: an estimate or something that happened.
ESTIMATED = "EST"


def _code(event: str, at: str | None, pod: str | None) -> MilestoneCode:
    """Their movement code, read against the port it happened at.

    Only when we know the port of discharge *and* the movement's own port and they differ: an unknown
    port is not evidence of a transhipment, and downgrading a real discharge would stop the clock
    that has to run.
    """
    if at and pod and at.strip().upper() != pod.strip().upper() and event in TRANSSHIPMENT:
        return TRANSSHIPMENT[event]
    return EVENT_MILESTONES.get(event, MilestoneCode.UNKNOWN)


class ShipsgoConnector:
    """Both halves of the port. Polling needs the API token; webhooks need only the secret."""

    name = NAME

    def __init__(
        self,
        api_key: str | None,
        secret: str | None = None,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.secret = secret
        self.base_url = base_url.rstrip("/")
        self._client = client

    # ------------------------------------------------------------------ plumbing

    def _http(self) -> httpx.Client:
        if not self.api_key:
            raise ProviderNotConfigured(
                "SHIPSGO_API_KEY is not set: this deployment can receive Shipsgo webhooks but "
                "cannot ask Shipsgo for anything"
            )
        return self._client or httpx.Client(base_url=self.base_url, timeout=http_timeout(TIMEOUT_SECONDS))

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = self._http()
        try:
            return client.request(method, path, headers={TOKEN_HEADER: self.api_key or ""}, **kwargs)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Shipsgo is unreachable: {exc}") from exc

    # ------------------------------------------------------------------ the port

    def subscribe(self, req: SubscribeRequest) -> Subscription:
        """Create an ocean shipment. One credit, whether it is a box or a bill of lading."""
        body: dict[str, Any] = {"reference": req.external_ref}
        if req.identifier_type == "container":
            body["container_number"] = req.identifier
        else:
            body["booking_number"] = req.identifier
        if req.scac:
            body["carrier"] = req.scac
        response = self._request("POST", "/ocean/shipments", json=body)
        if response.status_code >= 400:
            return Subscription(NAME, provider_ref="", status="failed", message=_error(response))
        shipment = (response.json() or {}).get("shipment") or {}
        status = str(shipment.get("status") or "NEW")
        return Subscription(
            NAME,
            provider_ref=str(shipment.get("id") or ""),
            # NEW and INPROGRESS mean Shipsgo has not matched the box with the carrier yet.
            status="pending" if status in ("NEW", "INPROGRESS") else "active",
            message=None,
        )

    def unsubscribe(self, provider_ref: str) -> None:
        response = self._request("DELETE", f"/ocean/shipments/{provider_ref}")
        if response.status_code >= 400 and response.status_code != 404:
            raise ProviderUnavailable(_error(response))

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        """Read a shipment. This is the mode that works today: they charge for the credit, not the
        read, so polling a subscription we already paid for costs nothing further."""
        response = self._request("GET", f"/ocean/shipments/{provider_ref}")
        if response.status_code == 404:
            raise ProviderUnavailable(f"Shipsgo has no shipment {provider_ref}")
        if response.status_code >= 400:
            raise ProviderUnavailable(_error(response))
        return self._envelope(response.json() or {}, delivery_id=f"poll:{provider_ref}")

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        if not self.secret:
            raise ProviderNotConfigured(
                "TRACKING_WEBHOOK_SECRET_SHIPSGO is not set: deliveries cannot be verified"
            )
        lowered = {k.lower(): v for k, v in headers.items()}
        given = lowered.get(SIGNATURE_HEADER, "")
        expected = hmac.new(self.secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(given, expected):
            raise InvalidSignature("Signature does not match the request body")
        return self.parse(body)

    def parse(self, body: bytes) -> WebhookEnvelope:
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise InvalidSignature(f"Delivery is not JSON: {exc}") from exc
        return self._envelope(payload, delivery_id=None)

    # ------------------------------------------------------------------ translation

    def _envelope(self, payload: dict[str, Any], *, delivery_id: str | None) -> WebhookEnvelope:
        # Their webhook payload is not in the specification, only the event envelope is. Accept the
        # shipment wherever it turns out to be rather than guessing one shape and breaking on another.
        shipment = payload.get("shipment") or payload.get("data") or payload
        if not isinstance(shipment, dict):
            shipment = {}
        containers = shipment.get("containers") or []
        container = containers[0] if containers else {}

        route = shipment.get("route") or {}
        destination = (route.get("port_of_discharge") or {}).get("location") or {}
        pod = destination.get("code")

        events: list[ProviderEvent] = []
        for movement in container.get("movements") or []:
            event = self._movement(shipment, container, movement, pod)
            if event is not None:
                events.append(event)
        if not events:
            fallback = self._from_status(shipment)
            if fallback is not None:
                events.append(fallback)

        identifier = str(payload.get("id") or shipment.get("id") or container.get("number") or "shipsgo")
        return WebhookEnvelope(
            provider=NAME,
            delivery_id=delivery_id or f"event:{identifier}:{_now_key(payload)}",
            provider_ref=str(shipment["id"]) if shipment.get("id") is not None else None,
            container_number=_container_number(container, shipment),
            events=events,
            eta=_arrival_estimate(container, pod),
            destination_unlocode=destination.get("code"),
            raw=payload,
        )

    def _movement(
        self,
        shipment: dict[str, Any],
        container: dict[str, Any],
        movement: dict[str, Any],
        pod: str | None = None,
    ) -> ProviderEvent | None:
        occurred_at = _time(movement.get("timestamp"))
        if occurred_at is None:
            return None
        code_name = str(movement.get("event") or "")
        estimate = str(movement.get("status") or "").upper() == ESTIMATED
        location = movement.get("location") or {}
        vessel = movement.get("vessel") or {}
        return ProviderEvent(
            code=_code(code_name, location.get("code"), pod),
            occurred_at=occurred_at,
            is_estimate=estimate,
            provider_event_key=_key(
                str(shipment.get("id") or ""),
                str(container.get("number") or ""),
                code_name,
                occurred_at.isoformat(),
                str(int(estimate)),
            ),
            location_unlocode=location.get("code"),
            location_name=location.get("name"),
            vessel_name=vessel.get("name"),
            voyage=movement.get("voyage"),
            raw_description=code_name,
            source=_source(movement),
        )

    def _from_status(self, shipment: dict[str, Any]) -> ProviderEvent | None:
        """A shipment with no movements still says where it is. Dated `checked_at`, because that is
        genuinely all we know: a status with an invented timestamp would be worse than none."""
        code = SHIPMENT_STATUS_MILESTONES.get(str(shipment.get("status") or ""))
        checked = _time(shipment.get("checked_at") or shipment.get("updated_at"))
        if code is None or checked is None:
            return None
        return ProviderEvent(
            code=code,
            occurred_at=checked,
            is_estimate=True,  # a status is not a dated movement; it must not set an actual date
            provider_event_key=_key(
                str(shipment.get("id") or ""), "status", str(shipment.get("status")), checked.isoformat()
            ),
            raw_description=f"shipment status {shipment.get('status')}",
            source="carrier",
        )


def _container_number(container: dict[str, Any], shipment: dict[str, Any]) -> str | None:
    number = container.get("number") or shipment.get("container_number")
    # Their own documentation says a container can be NOT_ASSIGNED before the carrier confirms one.
    return None if not number or number == "NOT_ASSIGNED" else str(number)


def _arrival_estimate(container: dict[str, Any], pod: str | None = None) -> datetime | None:
    """The estimated arrival at the port of discharge — never the one at a transhipment call, which
    would put an ETA three weeks early on the screen and on every alert that reads it."""
    for movement in container.get("movements") or []:
        if movement.get("event") != "ARRV" or str(movement.get("status")).upper() != ESTIMATED:
            continue
        at = (movement.get("location") or {}).get("code")
        if pod and at and at.strip().upper() != pod.strip().upper():
            continue
        return _time(movement.get("timestamp"))
    return None


def _source(movement: dict[str, Any]) -> DataSource:
    return "carrier" if movement.get("vessel") else "terminal"


def _key(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _now_key(payload: dict[str, Any]) -> str:
    """Their webhook envelope carries an event id; without one, the delivery is keyed on its content
    so that the same body twice is still one delivery."""
    event_id = payload.get("id")
    if event_id:
        return str(event_id)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


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
        body = response.json()
        detail = body.get("message") or body.get("error") or json.dumps(body)[:200]
    except ValueError:
        detail = response.text[:200]
    return f"Shipsgo answered {response.status_code}: {detail}"
