"""The Terminal49 adapter, against captured payloads.

The fixtures follow the shapes published in Terminal49's public documentation (webhook envelope,
transport_event resource, container resource). No network and no API key: this exercises exactly what
a webhook-only deployment does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.adapters.tracking.terminal49 import SIGNATURE_HEADER, Terminal49Provider
from app.domain.models import MilestoneCode
from app.domain.tracking.ports import (
    InvalidSignature,
    ProviderNotConfigured,
    SubscribeRequest,
    WebhookEnvelope,
)

FIXTURES = Path(__file__).parent / "fixtures" / "terminal49"
SECRET = "t49-signing-secret"


def body_of(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def headers_for(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {SIGNATURE_HEADER: hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def at(moment: str) -> Callable[[], datetime]:
    """A fixed clock, so "is this delivery stale?" is a decision the test controls."""
    fixed = datetime.fromisoformat(moment)
    return lambda: fixed


def provider(moment: str = "2026-09-06T09:20:00+00:00", **kwargs: Any) -> Terminal49Provider:
    return Terminal49Provider(SECRET, clock=at(moment), **kwargs)


def parse(name: str, moment: str) -> WebhookEnvelope:
    body = body_of(name)
    return provider(moment).verify_and_parse(headers_for(body), body)


def test_a_discharge_becomes_a_real_milestone() -> None:
    envelope = parse("vessel_discharged.json", "2026-09-06T09:20:00+00:00")
    assert envelope.delivery_id == "87d4f5e3-df7b-4725-85a3-b80acc572e5d"
    assert envelope.container_number == "MSCU4821990"
    assert envelope.provider_ref == "eeafd337-72b5-4e5c-87cb-9ef83fa99cf4"
    assert envelope.destination_unlocode == "FRLEH"
    (event,) = envelope.events
    assert event.code is MilestoneCode.DISCHARGED
    assert event.is_estimate is False
    assert event.occurred_at == datetime(2026, 9, 6, 9, 5, tzinfo=UTC)
    assert event.location_unlocode == "FRLEH"
    assert event.vessel_name == "MSC ISABELLA"
    assert event.voyage == "443W"
    assert event.source == "terminal"


def test_an_estimated_arrival_is_flagged_as_an_estimate() -> None:
    envelope = parse("estimated_vessel_arrived.json", "2026-09-01T18:10:00+00:00")
    (event,) = envelope.events
    assert event.code is MilestoneCode.VESSEL_ARRIVED
    assert event.is_estimate is True
    assert envelope.eta == datetime(2026, 9, 4, 6, 15, tzinfo=UTC)
    assert event.source == "carrier"


def test_a_gate_out_without_a_vessel_parses() -> None:
    envelope = parse("full_out.json", "2026-09-09T14:45:00+00:00")
    (event,) = envelope.events
    assert event.code is MilestoneCode.GATE_OUT_FULL
    assert event.vessel_name is None
    assert event.voyage is None


def test_an_event_we_have_no_mapping_for_is_kept_as_unknown() -> None:
    """An unmapped description is stored, never dropped: it is how the mapping table grows."""
    envelope = parse("unmapped_event.json", "2026-09-07T11:05:00+00:00")
    (event,) = envelope.events
    assert event.code is MilestoneCode.UNKNOWN
    assert event.raw_description == "container.transport.rail_ramp_departed_unknown"
    assert event.source == "ais"


def test_the_key_separates_an_estimate_from_the_actual_it_became() -> None:
    estimate = parse("estimated_vessel_arrived.json", "2026-09-01T18:10:00+00:00").events[0]
    actual = parse("vessel_discharged.json", "2026-09-06T09:20:00+00:00").events[0]
    assert estimate.provider_event_key != actual.provider_event_key


def test_a_wrong_signature_is_refused() -> None:
    body = body_of("vessel_discharged.json")
    with pytest.raises(InvalidSignature):
        provider().verify_and_parse({SIGNATURE_HEADER: "0" * 64}, body)
    with pytest.raises(InvalidSignature):
        provider().verify_and_parse(headers_for(body, "another-secret"), body)
    with pytest.raises(InvalidSignature):
        provider().verify_and_parse({}, body)  # no signature at all


def test_a_correctly_signed_but_stale_delivery_is_refused() -> None:
    """Terminal49 publishes no timestamp header, so freshness is judged on the notification's own
    created_at: a captured body replayed a day later does not pass."""
    body = body_of("vessel_discharged.json")
    with pytest.raises(InvalidSignature):
        provider("2026-09-07T09:20:00+00:00").verify_and_parse(headers_for(body), body)


def test_a_wider_window_accepts_the_same_delivery() -> None:
    body = body_of("vessel_discharged.json")
    envelope = provider("2026-09-07T09:20:00+00:00", max_age=timedelta(days=2)).verify_and_parse(
        headers_for(body), body
    )
    assert envelope.events


def test_a_body_that_is_not_json_is_refused_before_parsing() -> None:
    body = b"not json at all"
    with pytest.raises(InvalidSignature):
        provider().verify_and_parse(headers_for(body), body)


def test_without_a_secret_nothing_is_verified() -> None:
    body = body_of("vessel_discharged.json")
    with pytest.raises(ProviderNotConfigured):
        Terminal49Provider(None).verify_and_parse(headers_for(body), body)


def test_subscribing_without_an_api_key_says_so_plainly() -> None:
    with pytest.raises(ProviderNotConfigured) as raised:
        provider().subscribe(
            SubscribeRequest(
                identifier="MSCU4821990",
                identifier_type="container",
                scac="MSCU",
                callback_url="https://example.test/api/v1/webhooks/tracking/terminal49",
                external_ref="local-id",
            )
        )
    assert "TERMINAL49_API_KEY" in raised.value.message


def test_a_delivery_goes_all_the_way_through_the_api(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Webhook-only end to end: subscribe with no API key, then post a real payload."""
    from app.core.settings import get_settings

    monkeypatch.setenv("TRACKING_WEBHOOK_SECRET_TERMINAL49", SECRET)
    # The fixture was captured in September 2026; the freshness window is widened rather than faked.
    monkeypatch.setenv("TRACKING_WEBHOOK_MAX_AGE_SECONDS", str(100 * 365 * 24 * 3600))
    get_settings.cache_clear()

    created = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    container_id = created.json()["id"]
    subscription = client.post(f"/api/v1/containers/{container_id}/tracking", json={"provider": "terminal49"})
    assert subscription.status_code == 201, subscription.text
    assert subscription.json()["provider_ref"] is None  # webhook-only: routed by container number

    body = body_of("vessel_discharged.json")
    res = client.post("/api/v1/webhooks/tracking/terminal49", content=body, headers=headers_for(body))
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "processed"
    assert res.json()["events_ingested"] == 1

    container = client.get(f"/api/v1/containers/{container_id}").json()
    assert container["milestone"] == "DISCHARGED"
    assert container["tracking_state"] == "ACTIVE"
    assert container["last_free_day"] == "2026-09-10"  # discharged 6 Sept + 5 free days - 1
    get_settings.cache_clear()
    payload = json.loads(body)
    assert payload["data"]["attributes"]["event"] == "container.transport.vessel_discharged"


# ---------------------------------------------------------------------------- one bill, three boxes


def test_a_shipment_notification_carries_every_container_on_the_bill() -> None:
    """`shipment.estimated.arrival` accompanies all the boxes on the bill of lading. Reading only
    the first left the other two showing a date the carrier had already moved — side by side on the
    same screen, which a supply-chain manager spots immediately."""
    envelope = parse("shipment_estimated_arrival.json", "2026-09-08T11:10:00+00:00")
    assert envelope.container_number == "MSCU1111111"
    assert [other.container_number for other in envelope.others] == ["MSCU2222222", "MSCU3333333"]
    for part in (envelope, *envelope.others):
        assert part.eta == datetime(2026, 9, 22, 6, 0, tzinfo=UTC)
        assert part.destination_unlocode == "FRLEH"
        (event,) = part.events
        assert event.is_estimate is True
        assert part.delivery_id == envelope.delivery_id  # one delivery, whatever it is about


def test_a_container_notification_still_carries_one_container() -> None:
    envelope = parse("vessel_discharged.json", "2026-09-06T09:20:00+00:00")
    assert envelope.others == []


def test_every_container_of_a_shipment_notification_is_updated(client: TestClient, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """End to end: three boxes tracked, one delivery, three new ETAs."""
    from app.core.settings import get_settings

    monkeypatch.setenv("TRACKING_WEBHOOK_SECRET_TERMINAL49", SECRET)
    monkeypatch.setenv("TRACKING_WEBHOOK_MAX_AGE_SECONDS", str(100 * 365 * 24 * 3600))
    get_settings.cache_clear()

    ids = {}
    for number in ("MSCU1111111", "MSCU2222222", "MSCU3333333"):
        created = client.post("/api/v1/containers", json={"container_number": number})
        assert created.status_code == 201, created.text
        ids[number] = created.json()["id"]
        subscribed = client.post(
            f"/api/v1/containers/{ids[number]}/tracking", json={"provider": "terminal49"}
        )
        assert subscribed.status_code == 201, subscribed.text

    body = body_of("shipment_estimated_arrival.json")
    res = client.post("/api/v1/webhooks/tracking/terminal49", content=body, headers=headers_for(body))
    assert res.status_code == 200, res.text
    assert res.json()["events_ingested"] == 3

    for number, container_id in ids.items():
        shown = client.get(f"/api/v1/containers/{container_id}").json()
        assert shown["eta"].startswith("2026-09-22"), number
    get_settings.cache_clear()
