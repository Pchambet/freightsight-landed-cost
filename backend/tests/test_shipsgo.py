"""The Shipsgo adapter, against the response examples in their own OpenAPI specification."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.tracking.shipsgo import (
    SIGNATURE_HEADER,
    TOKEN_HEADER,
    ShipsgoConnector,
)
from app.domain.models import ContainerMilestone, MilestoneCode, Organization
from app.domain.tracking.ports import (
    InvalidSignature,
    ProviderNotConfigured,
    ProviderUnavailable,
    SubscribeRequest,
)
from app.domain.tracking.state import derive

FIXTURES = Path(__file__).parent / "fixtures" / "shipsgo"
SECRET = "shipsgo-webhook-secret"
TOKEN = "shipsgo-api-token"


def fixture(name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text())
    return body


def sign(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {SIGNATURE_HEADER: hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def connector(handler: Any = None, *, api_key: str | None = TOKEN, secret: str | None = SECRET):  # type: ignore[no-untyped-def]
    client = (
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.shipsgo.test/v2")
        if handler
        else None
    )
    return ShipsgoConnector(api_key, secret, base_url="https://api.shipsgo.test/v2", client=client)


# ---------------------------------------------------------------------------- the signature


def test_the_signature_matches_the_vendors_own_published_vector() -> None:
    """Shipsgo publishes a secret, a payload and the signature they expect for it. This is that
    check, run against our implementation rather than against my reading of their prose."""
    body = b'{"message":"You shall not pass!"}'
    headers = sign(body, "SUPER_LONG_AND_SECURE_SECRET_KEY")
    assert headers[SIGNATURE_HEADER] == ("9527e0c9463e6f5b01a0af50aecb4658ff50c6b25d3efa8e5c8dea7e4b763772")


def test_a_signed_delivery_is_accepted_and_a_forged_one_is_not() -> None:
    body = json.dumps(fixture("discharged")).encode()
    envelope = connector().verify_and_parse(sign(body), body)
    assert envelope.provider == "shipsgo"

    with pytest.raises(InvalidSignature):
        connector().verify_and_parse({SIGNATURE_HEADER: "0" * 64}, body)
    with pytest.raises(InvalidSignature):
        connector().verify_and_parse(sign(body, "another-secret"), body)
    with pytest.raises(InvalidSignature):
        connector().verify_and_parse({}, body)


def test_without_a_secret_nothing_is_verified() -> None:
    body = json.dumps(fixture("discharged")).encode()
    with pytest.raises(ProviderNotConfigured) as raised:
        connector(secret=None).verify_and_parse(sign(body), body)
    assert "TRACKING_WEBHOOK_SECRET_SHIPSGO" in raised.value.message


def test_a_body_that_is_not_json_is_refused() -> None:
    body = b"not json"
    with pytest.raises(InvalidSignature):
        connector().verify_and_parse(sign(body), body)


# ---------------------------------------------------------------------------- reading a shipment


def parse(name: str) -> Any:
    body = json.dumps(fixture(name)).encode()
    return connector().verify_and_parse(sign(body), body)


def test_a_finished_voyage_becomes_our_whole_timeline() -> None:
    envelope = parse("discharged")
    assert envelope.container_number == "OOCU4935001"
    assert envelope.provider_ref == "1003"

    codes = [(event.code, event.is_estimate) for event in envelope.events]
    assert (MilestoneCode.GATE_OUT_EMPTY_ORIGIN, False) in codes
    assert (MilestoneCode.GATE_IN_FULL_ORIGIN, False) in codes
    assert (MilestoneCode.LOADED, False) in codes
    assert (MilestoneCode.VESSEL_DEPARTED, False) in codes
    assert (MilestoneCode.DISCHARGED, False) in codes
    assert (MilestoneCode.GATE_IN_EMPTY_RETURN, False) in codes
    assert all(event.code is not MilestoneCode.UNKNOWN for event in envelope.events)


def test_the_movement_carries_its_ship_and_its_port() -> None:
    envelope = parse("discharged")
    loaded = next(e for e in envelope.events if e.code is MilestoneCode.LOADED)
    assert loaded.vessel_name == "BF CARODA"
    assert loaded.voyage == "EZJ5616"
    assert loaded.location_unlocode == "BRSSZ"
    assert loaded.location_name == "SANTOS"
    assert loaded.occurred_at.tzinfo is not None  # they answer with an offset, we keep it


def test_an_estimated_movement_is_not_an_actual_one() -> None:
    """Their `status` field is EST or ACT, and reading it wrong would date an arrival that has not
    happened — which is exactly what the demurrage clock keys off."""
    envelope = parse("booked")
    estimates = [e for e in envelope.events if e.is_estimate]
    assert estimates, "the booked fixture has estimated movements"
    for event in estimates:
        assert event.occurred_at is not None


def test_a_shipment_still_being_matched_says_only_what_it_knows() -> None:
    envelope = parse("inprogress")
    # No movements yet: a status, dated when Shipsgo last looked, and flagged as an estimate so it
    # cannot set an arrival date the carrier has not confirmed.
    assert all(event.is_estimate for event in envelope.events)


def test_two_deliveries_of_the_same_body_are_one_delivery() -> None:
    body = json.dumps(fixture("discharged")).encode()
    first = connector().verify_and_parse(sign(body), body)
    second = connector().verify_and_parse(sign(body), body)
    assert first.delivery_id == second.delivery_id


def test_the_same_movement_read_twice_keeps_one_key() -> None:
    first = parse("discharged").events
    second = parse("discharged").events
    assert [e.provider_event_key for e in first] == [e.provider_event_key for e in second]
    assert len({e.provider_event_key for e in first}) == len(first)  # and they are distinct


# ---------------------------------------------------------------------------- the API side


def test_subscribing_sends_the_box_and_the_carrier() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["token"] = request.headers[TOKEN_HEADER]
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json={"shipment": {"id": 1001, "status": "INPROGRESS"}})

    result = connector(handler).subscribe(
        SubscribeRequest(
            identifier="MSDU7777777",
            identifier_type="container",
            scac="MSCU",
            callback_url="",
            external_ref="our-container-id",
        )
    )
    assert seen["url"].endswith("/ocean/shipments")
    assert seen["token"] == TOKEN
    assert seen["body"] == {
        "reference": "our-container-id",
        "container_number": "MSDU7777777",
        "carrier": "MSCU",
    }
    assert result.provider_ref == "1001"
    assert result.status == "pending"  # they have not matched it with the carrier yet


def test_a_refused_subscription_carries_the_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "Invalid container number"})

    result = connector(handler).subscribe(
        SubscribeRequest(
            identifier="NOPE",
            identifier_type="container",
            scac=None,
            callback_url="",
            external_ref="x",
        )
    )
    assert result.status == "failed"
    assert "Invalid container number" in (result.message or "")


def test_polling_reads_the_shipment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/ocean/shipments/1003")
        return httpx.Response(200, json=fixture("discharged"))

    envelope = connector(handler).fetch("1003")
    assert envelope.delivery_id == "poll:1003"
    assert envelope.container_number == "OOCU4935001"
    assert envelope.events


def test_without_an_api_key_polling_says_which_one_is_missing() -> None:
    with pytest.raises(ProviderNotConfigured) as raised:
        connector(api_key=None).fetch("1003")
    assert "SHIPSGO_API_KEY" in raised.value.message


def test_an_unreachable_shipsgo_is_not_a_bad_delivery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(ProviderUnavailable):
        connector(handler).fetch("1003")


# ---------------------------------------------------------------------------- choosing a provider


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def test_the_organization_can_choose_its_tracker(client: TestClient, org: Organization) -> None:
    res = client.patch("/api/v1/organization", json={"settings": {"tracking_provider_default": "shipsgo"}})
    assert res.status_code == 200, res.text
    assert res.json()["settings"]["tracking_provider_default"] == "shipsgo"


def test_a_tracker_we_do_not_have_is_refused_when_it_is_set(client: TestClient, org: Organization) -> None:
    """Otherwise the setting fails at the worst moment: when someone finally clicks "track this"."""
    res = client.patch("/api/v1/organization", json={"settings": {"tracking_provider_default": "vizion"}})
    assert res.status_code == 422
    assert res.json()["code"] == "UNKNOWN_PROVIDER"
    assert "shipsgo" in res.json()["detail"]


def test_the_default_is_used_when_the_caller_names_no_provider(
    client: TestClient, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.tracking import shipsgo as shipsgo_module

    client.patch("/api/v1/organization", json={"settings": {"tracking_provider_default": "shipsgo"}})
    created = client.post("/api/v1/containers", json={"container_number": "MSDU7777777"})
    container_id = created.json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"shipment": {"id": 2002, "status": "BOOKED"}})

    monkeypatch.setattr(
        shipsgo_module.ShipsgoConnector,
        "_http",
        lambda self: httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://api.shipsgo.test/v2"
        ),
    )
    monkeypatch.setenv("SHIPSGO_API_KEY", TOKEN)
    from app.core.settings import get_settings

    get_settings.cache_clear()
    try:
        res = client.post(f"/api/v1/containers/{container_id}/tracking", json={})
        assert res.status_code == 201, res.text
        assert res.json()["provider"] == "shipsgo"
        assert res.json()["provider_ref"] == "2002"
    finally:
        get_settings.cache_clear()


def test_without_a_default_a_subscription_is_manual(client: TestClient, org: Organization) -> None:
    created = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"})
    res = client.post(f"/api/v1/containers/{created.json()['id']}/tracking", json={})
    assert res.status_code == 201, res.text
    assert res.json()["provider"] == "manual"


# ---------------------------------------------------------------------------- calls on the way


def test_a_discharge_at_the_transhipment_port_is_not_the_discharge() -> None:
    """Ningbo → Tanger Med → Le Havre. Shipsgo publishes `DISC` at Tanger on the 12th with nothing
    but the port to distinguish it, and mapping that to DISCHARGED started the demurrage clock on a
    box still two weeks from land: a CRITICAL "demurrage running" e-mail, and a dashboard showing it
    discharged at the wrong port."""
    envelope = parse("transshipment")
    at_tanger = [e for e in envelope.events if e.location_unlocode == "MAPTM"]
    assert {e.code for e in at_tanger} == {
        MilestoneCode.TRANSSHIPMENT_ARRIVED,
        MilestoneCode.TRANSSHIPMENT_DISCHARGED,
    }
    assert not [e for e in envelope.events if e.code is MilestoneCode.DISCHARGED]

    snapshot = derive(envelope.events, datetime(2026, 9, 17, 12, 0, tzinfo=UTC), destination_unlocode="FRLEH")
    assert snapshot.discharged_at is None  # no clock starts
    assert snapshot.milestone is ContainerMilestone.VESSEL_DEPARTED


def test_the_eta_is_the_one_at_the_port_of_discharge() -> None:
    """The estimated arrival at Tanger would have put an ETA three weeks early on every screen and
    in every alert that reads it."""
    assert parse("transshipment").eta == datetime(2026, 9, 30, 6, 0, tzinfo=UTC)


def test_loading_at_the_origin_is_not_a_transhipment() -> None:
    """`LOAD` and `DEPA` happen at the origin and at every call on the way, so the port alone cannot
    separate them — and neither of them starts a clock, so they are left as they are."""
    envelope = parse("transshipment")
    loaded = next(
        e for e in envelope.events if e.location_unlocode == "CNNGB" and e.raw_description == "LOAD"
    )
    assert loaded.code is MilestoneCode.LOADED
