"""Tracking through the API: manual events, subscriptions, and webhook deliveries end to end."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import fakes
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Container, MilestoneCode, TrackingEvent


@pytest.fixture
def fake_provider(client: TestClient) -> fakes.FakeProvider:
    """Hand the webhook route a provider whose wire format we control."""
    from app.api.v1.deps import get_provider_factory
    from app.domain.tracking.registry import get_provider
    from app.main import app

    provider = fakes.FakeProvider()

    def factory():  # type: ignore[no-untyped-def]
        return lambda name: provider if name == provider.name else get_provider(name)

    app.dependency_overrides[get_provider_factory] = factory
    return provider


def make_container(client: TestClient, number: str = "MSCU4821990") -> str:
    res = client.post("/api/v1/containers", json={"container_number": number, "carrier_scac": "MSCU"})
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def deliver(client: TestClient, body: bytes, *, secret: str = fakes.SECRET, signature: str | None = None):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/webhooks/tracking/fake",
        content=body,
        headers={fakes.SIGNATURE_HEADER: signature or fakes.sign(body, secret)},
    )


# ---------------------------------------------------------------------------- manual tracking


def test_manual_event_moves_the_container_and_computes_the_last_free_day(client: TestClient) -> None:
    container_id = make_container(client)
    discharged = datetime.now(UTC) - timedelta(days=2)
    res = client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "DISCHARGED", "occurred_at": discharged.isoformat(), "location_unlocode": "FRLEH"},
    )
    assert res.status_code == 201, res.text
    assert [e["code"] for e in res.json()] == ["DISCHARGED"]

    container = client.get(f"/api/v1/containers/{container_id}").json()
    assert container["milestone"] == "DISCHARGED"
    assert container["tracking_state"] == "MANUAL"
    assert container["last_free_day"] == (discharged.date() + timedelta(days=4)).isoformat()
    assert container["dnd_risk"] == "MEDIUM"  # two days left of five free


def test_the_same_manual_event_twice_writes_one_row(client: TestClient) -> None:
    container_id = make_container(client)
    moment = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    body = {"code": "VESSEL_ARRIVED", "occurred_at": moment}
    client.post(f"/api/v1/containers/{container_id}/events", json=body)
    res = client.post(f"/api/v1/containers/{container_id}/events", json=body)
    assert res.status_code == 201
    assert len(res.json()) == 1


def test_timeline_keeps_the_estimate_that_became_an_arrival(client: TestClient) -> None:
    container_id = make_container(client)
    eta = datetime.now(UTC) + timedelta(days=3)
    client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "VESSEL_ARRIVED", "occurred_at": eta.isoformat(), "is_estimate": True},
    )
    actual = datetime.now(UTC) - timedelta(hours=6)
    res = client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "VESSEL_ARRIVED", "occurred_at": actual.isoformat()},
    )
    assert [(e["code"], e["is_estimate"]) for e in res.json()] == [
        ("VESSEL_ARRIVED", False),
        ("VESSEL_ARRIVED", True),
    ]
    container = client.get(f"/api/v1/containers/{container_id}").json()
    assert container["milestone"] == "VESSEL_ARRIVED"
    assert container["eta"] is not None  # the promise is still on file


# ---------------------------------------------------------------------------- subscriptions


def test_subscription_lifecycle(client: TestClient) -> None:
    container_id = make_container(client)
    res = client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "manual", "provider_ref": "T49-CONTAINER-1"},
    )
    assert res.status_code == 201, res.text
    assert res.json()["status"] == "active"

    again = client.post(
        f"/api/v1/containers/{container_id}/tracking", json={"provider": "manual", "provider_ref": "x"}
    )
    assert again.status_code == 409

    assert client.delete(f"/api/v1/containers/{container_id}/tracking").status_code == 204
    assert client.get(f"/api/v1/containers/{container_id}/tracking").status_code == 404
    assert client.get(f"/api/v1/containers/{container_id}").json()["tracking_state"] == "ENDED"


def test_unknown_provider_is_a_404(client: TestClient) -> None:
    container_id = make_container(client)
    res = client.post(f"/api/v1/containers/{container_id}/tracking", json={"provider": "vizion"})
    assert res.status_code == 404


# ---------------------------------------------------------------------------- webhooks


def test_tracking_webhook_idempotent_and_derives_status(
    client: TestClient, db: Session, fake_provider: fakes.FakeProvider
) -> None:
    """Plan test 15: the same delivery twice leaves one set of events, and a real DISCHARGED gives a
    last free day and a demurrage risk."""
    container_id = make_container(client)
    client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "fake", "provider_ref": "fake-ref-1"},
    )
    discharged = datetime.now(UTC) - timedelta(days=4)
    body = fakes.payload(
        delivery_id="d-1",
        provider_ref="fake-ref-1",
        events=[
            fakes.event(MilestoneCode.VESSEL_ARRIVED, discharged - timedelta(days=1)),
            fakes.event(MilestoneCode.DISCHARGED, discharged),
        ],
    )

    first = deliver(client, body)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "processed"
    assert first.json()["events_ingested"] == 2

    second = deliver(client, body)
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"

    events = client.get(f"/api/v1/containers/{container_id}/events").json()
    assert [e["code"] for e in events] == ["VESSEL_ARRIVED", "DISCHARGED"]

    container = client.get(f"/api/v1/containers/{container_id}").json()
    assert container["milestone"] == "DISCHARGED"
    assert container["tracking_state"] == "ACTIVE"
    assert container["last_free_day"] == (discharged.date() + timedelta(days=4)).isoformat()
    assert container["dnd_risk"] == "HIGH"  # one day left

    assert db.scalar(select(text("count(*)")).select_from(TrackingEvent)) == 2


def test_a_bad_signature_writes_nothing(client: TestClient, fake_provider: fakes.FakeProvider) -> None:
    container_id = make_container(client)
    client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "fake", "provider_ref": "fake-ref-2"},
    )
    body = fakes.payload(
        delivery_id="d-2",
        provider_ref="fake-ref-2",
        events=[fakes.event(MilestoneCode.DISCHARGED, datetime.now(UTC))],
    )
    res = deliver(client, body, signature="deadbeef")
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_SIGNATURE"
    assert client.get(f"/api/v1/containers/{container_id}/events").json() == []


def test_a_stale_delivery_is_refused(client: TestClient, fake_provider: fakes.FakeProvider) -> None:
    container_id = make_container(client)
    client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "fake", "provider_ref": "fake-ref-3"},
    )
    body = fakes.payload(
        delivery_id="d-3",
        provider_ref="fake-ref-3",
        sent_at=datetime.now(UTC) - timedelta(hours=2),
        events=[fakes.event(MilestoneCode.DISCHARGED, datetime.now(UTC))],
    )
    assert deliver(client, body).status_code == 401


def test_an_unroutable_delivery_is_kept_and_acknowledged(
    client: TestClient, fake_provider: fakes.FakeProvider
) -> None:
    body = fakes.payload(
        delivery_id="d-4",
        provider_ref="nobody-subscribed-to-this",
        events=[fakes.event(MilestoneCode.DISCHARGED, datetime.now(UTC))],
    )
    res = deliver(client, body)
    assert res.status_code == 200
    assert res.json()["status"] == "unroutable"


def test_webhook_only_routing_by_container_number(client: TestClient) -> None:
    """No API key, so no provider reference: the box number is what routes the delivery."""
    from app.api.v1.deps import get_provider_factory
    from app.main import app

    provider = fakes.FakeProvider(can_subscribe=False)
    app.dependency_overrides[get_provider_factory] = lambda: lambda name: provider

    container_id = make_container(client, "TGHU7245081")
    sub = client.post(f"/api/v1/containers/{container_id}/tracking", json={"provider": "fake"})
    assert sub.status_code == 201, sub.text
    assert sub.json()["provider_ref"] is None
    assert "webhook-only" in sub.json()["last_error"]
    body = fakes.payload(
        delivery_id="d-5",
        container_number="TGHU7245081",
        events=[fakes.event(MilestoneCode.GATE_OUT_FULL, datetime.now(UTC) - timedelta(hours=3))],
    )
    res = deliver(client, body)
    assert res.json()["status"] == "processed", res.text
    assert client.get(f"/api/v1/containers/{container_id}").json()["milestone"] == "GATE_OUT_FULL"


def test_eta_moves_are_recorded_when_they_matter(
    client: TestClient, fake_provider: fakes.FakeProvider
) -> None:
    container_id = make_container(client)
    client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "fake", "provider_ref": "fake-ref-6"},
    )
    first_eta = datetime.now(UTC) + timedelta(days=6)
    deliver(
        client,
        fakes.payload(
            delivery_id="e-1",
            provider_ref="fake-ref-6",
            events=[fakes.event(MilestoneCode.VESSEL_ARRIVED, first_eta, is_estimate=True)],
        ),
    )
    # six hours later: noise, no history line
    deliver(
        client,
        fakes.payload(
            delivery_id="e-2",
            provider_ref="fake-ref-6",
            events=[
                fakes.event(MilestoneCode.VESSEL_ARRIVED, first_eta + timedelta(hours=6), is_estimate=True)
            ],
        ),
    )
    assert client.get(f"/api/v1/containers/{container_id}/eta-history").json() == []

    # three days later: that is a warning
    deliver(
        client,
        fakes.payload(
            delivery_id="e-3",
            provider_ref="fake-ref-6",
            events=[
                fakes.event(MilestoneCode.VESSEL_ARRIVED, first_eta + timedelta(days=3), is_estimate=True)
            ],
        ),
    )
    history = client.get(f"/api/v1/containers/{container_id}/eta-history").json()
    assert len(history) == 1
    assert history[0]["severity"] == "warning"
    assert history[0]["delta_hours"] == "66.0"


def test_tracking_events_are_invisible_to_another_organization(
    client: TestClient, db: Session, org_id: uuid.UUID, fake_provider: fakes.FakeProvider
) -> None:
    container_id = make_container(client)
    client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "DISCHARGED", "occurred_at": datetime.now(UTC).isoformat()},
    )
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert len(list(db.scalars(select(TrackingEvent)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(TrackingEvent))) == []
        assert list(db.scalars(select(Container))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)
