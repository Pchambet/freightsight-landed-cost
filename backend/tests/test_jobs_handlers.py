"""The background jobs, called directly: no worker, no queue, a clock passed in as an argument."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import fakes
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Alert,
    AlertKind,
    Container,
    ContainerMilestone,
    DndRisk,
    EtaHistory,
    MilestoneCode,
    Organization,
    TrackingEvent,
    TrackingState,
    TrackingSubscription,
    WebhookDelivery,
)
from app.jobs import handlers

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def make_container(client: TestClient, number: str = "MSCU4821990") -> str:
    res = client.post("/api/v1/containers", json={"container_number": number, "carrier_scac": "MSCU"})
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def alerts_of(db: Session, kind: AlertKind) -> list[Alert]:
    return list(db.scalars(select(Alert).where(Alert.kind == kind).order_by(Alert.created_at)))


# ---------------------------------------------------------------------------- demurrage


def test_the_morning_pass_alerts_once_per_threshold(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.discharged_at = NOW - timedelta(days=2)  # five free days: two left, MEDIUM
    container.dnd_risk = DndRisk.NONE
    db.commit()

    assert handlers.recompute_dnd_risk(db, now=NOW) == 1
    db.expire_all()
    recomputed = db.get(Container, uuid.UUID(container_id))
    assert recomputed is not None and recomputed.dnd_risk is DndRisk.MEDIUM
    assert len(alerts_of(db, AlertKind.DND_RISK)) == 1

    # run it again the same day: nothing changed, nothing said
    assert handlers.recompute_dnd_risk(db, now=NOW) == 0
    assert len(alerts_of(db, AlertKind.DND_RISK)) == 1

    # a day later the threshold moves and that is worth saying
    assert handlers.recompute_dnd_risk(db, now=NOW + timedelta(days=2)) == 1
    risks = [a.payload["risk"] for a in alerts_of(db, AlertKind.DND_RISK)]
    assert risks == ["MEDIUM", "HIGH"]


def test_a_picked_up_container_is_watched_for_detention(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The terminal clock stops at the gate; the carrier's clock starts there. The sweep used to
    filter on `gate_out_at IS NULL`, so the badge froze at whatever it said the day the box left —
    green, usually — while detention ran for a fortnight at the carrier's daily rate."""
    container_id = make_container(client, "TGHU7245081")
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.discharged_at = NOW - timedelta(days=9)
    container.gate_out_at = NOW - timedelta(days=8)  # seven free days: one day over
    db.commit()

    assert handlers.recompute_dnd_risk(db, now=NOW) == 1
    (alert,) = alerts_of(db, AlertKind.DND_RISK)
    assert alert.payload["clock"] == "detention"
    assert alert.payload["risk"] == "INCURRING"
    assert alert.dedup_key.startswith(f"dnd:{container_id}:det:INCURRING:")


def test_a_returned_empty_stops_both_clocks(client: TestClient, db: Session, org: Organization) -> None:
    container_id = make_container(client, "TGHU7245082")
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.discharged_at = NOW - timedelta(days=20)
    container.gate_out_at = NOW - timedelta(days=15)
    container.empty_returned_at = NOW - timedelta(days=2)
    db.commit()

    assert handlers.recompute_dnd_risk(db, now=NOW) == 0
    assert alerts_of(db, AlertKind.DND_RISK) == []


def test_the_daily_pass_catches_up_instead_of_matching_the_hour(db: Session) -> None:
    """A worker restarted between 06:00 and 07:00 Paris used to skip the whole day — no J-3 warnings
    that morning, and nothing anywhere to say the day had been missed."""
    before_six = datetime(2026, 9, 9, 3, 0, tzinfo=UTC)  # 05:00 Paris, summer
    assert not handlers.daily_risk_due(db, before_six)
    assert handlers.daily_risk_due(db, datetime(2026, 9, 9, 4, 0, tzinfo=UTC))  # 06:00 Paris
    assert handlers.daily_risk_due(db, datetime(2026, 9, 9, 6, 0, tzinfo=UTC))  # 08:00: catch up
    assert handlers.daily_risk_due(db, datetime(2026, 12, 9, 5, 0, tzinfo=UTC))  # 06:00, winter

    handlers.note_job_run(db, handlers.DAILY_RISK_JOB, datetime(2026, 9, 9, 4, 0, tzinfo=UTC))
    assert not handlers.daily_risk_due(db, datetime(2026, 9, 9, 6, 0, tzinfo=UTC))  # already run
    assert handlers.daily_risk_due(db, datetime(2026, 9, 10, 4, 0, tzinfo=UTC))  # the next morning


# ---------------------------------------------------------------------------- ETA


def test_eta_history_becomes_one_alert_per_row(client: TestClient, db: Session, org: Organization) -> None:
    container_id = make_container(client)
    db.add(
        EtaHistory(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            eta=NOW + timedelta(days=8),
            previous_eta=NOW + timedelta(days=5),
            delta_hours=72,
            severity="warning",
            source="fake",
            recorded_at=NOW - timedelta(hours=1),
        )
    )
    db.commit()

    assert handlers.alert_on_eta_changes(db, now=NOW) == 1
    (alert,) = alerts_of(db, AlertKind.ETA_CHANGED)
    assert alert.severity.value == "warning"
    assert "72" in alert.title and "later" in alert.title

    # running again says nothing new: the alert is keyed on the history row
    assert handlers.alert_on_eta_changes(db, now=NOW) == 0
    assert len(alerts_of(db, AlertKind.ETA_CHANGED)) == 1


# ---------------------------------------------------------------------------- silent feeds


def test_a_box_whose_arrival_is_overdue_is_reported(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.eta = NOW - timedelta(days=3)  # it should have landed; nobody has said it did
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="terminal49",
            provider_ref=None,  # webhook-only: nothing to poll
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )
    db.commit()

    handlers.poll_stale_subscriptions(db, now=NOW)
    (alert,) = alerts_of(db, AlertKind.TRACKING_DATA_MISSING)
    assert "MSCU4821990" in alert.title
    assert alert.severity.value == "warning"
    assert alert.payload["reason"] == "ETA_PASSED"
    # webhook-only is a deployment choice, not a broken feed: the screen does not go red for it
    db.expire_all()
    silent = db.get(Container, uuid.UUID(container_id))
    assert silent is not None and silent.tracking_state is not TrackingState.FAILED

    # same day, same silence: one alert
    handlers.poll_stale_subscriptions(db, now=NOW)
    assert len(alerts_of(db, AlertKind.TRACKING_DATA_MISSING)) == 1


def test_a_container_at_sea_is_not_reported_as_broken(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The alert this whole sweep nearly died of: Ningbo to Le Havre is silent by construction —
    `vessel_departed`, then nothing for three weeks. Judging that at 48 h produced one warning per
    container per day for the whole crossing, every one of them false, and marked the box FAILED."""
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.milestone = ContainerMilestone.VESSEL_DEPARTED
    container.tracking_state = TrackingState.ACTIVE
    container.eta = NOW + timedelta(days=18)  # still at sea, and on time
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="terminal49",
            provider_ref=None,
            status="active",
            subscribed_at=NOW - timedelta(days=12),
        )
    )
    db.commit()

    assert handlers.poll_stale_subscriptions(db, now=NOW) == 0  # nothing to ask, nothing to say
    assert alerts_of(db, AlertKind.TRACKING_DATA_MISSING) == []
    db.expire_all()
    sailing = db.get(Container, uuid.UUID(container_id))
    assert sailing is not None and sailing.tracking_state is TrackingState.ACTIVE


def test_a_box_at_the_terminal_is_reported_after_two_days(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The other half: once the ship has berthed, the next milestone is hours away, not weeks."""
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.milestone = ContainerMilestone.VESSEL_ARRIVED
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="terminal49",
            provider_ref=None,
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )
    db.commit()

    handlers.poll_stale_subscriptions(db, now=NOW)
    (alert,) = alerts_of(db, AlertKind.TRACKING_DATA_MISSING)
    assert alert.payload["reason"] == "EVENT_OVERDUE"


def test_a_recently_fed_subscription_is_not_reported(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id = make_container(client)
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="terminal49",
            provider_ref="t49-1",
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )
    db.add(
        TrackingEvent(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="terminal49",
            provider_event_key="k1",
            code="DISCHARGED",
            occurred_at=NOW - timedelta(hours=6),
            inserted_at=NOW - timedelta(hours=6),
        )
    )
    db.commit()
    assert handlers.poll_stale_subscriptions(db, now=NOW) == 0
    assert alerts_of(db, AlertKind.TRACKING_DATA_MISSING) == []


# ---------------------------------------------------------------------------- failed deliveries


def test_a_failed_delivery_is_replayed_and_then_left_alone(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A delivery that failed on our side is recoverable: the raw body is still in the table."""
    from app.api.v1.deps import get_provider_factory
    from app.main import app as fastapi_app

    provider = fakes.FakeProvider()
    fastapi_app.dependency_overrides[get_provider_factory] = lambda: lambda name: provider

    container_id = make_container(client)
    body = fakes.payload(
        delivery_id="stored-1",
        provider_ref="fake-ref-9",
        events=[fakes.event(MilestoneCode.DISCHARGED, NOW - timedelta(days=1))],
    )
    res = client.post(
        "/api/v1/webhooks/tracking/fake",
        content=body,
        headers={fakes.SIGNATURE_HEADER: fakes.sign(body)},
    )
    assert res.json()["status"] == "unroutable"  # no subscription yet: that is the failure to recover

    # the subscription arrives late, as it does when someone wires tracking up after the fact
    client.post(
        f"/api/v1/containers/{container_id}/tracking",
        json={"provider": "fake", "provider_ref": "fake-ref-9"},
    )
    delivery = db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == "stored-1"))
    assert delivery is not None
    delivery.status = "failed"
    db.commit()
    # The delivery was stamped by the real clock when the webhook came in, and the retry is only
    # due after its backoff. Take the moment from the row rather than from a constant, or this
    # test passes on the day it is written and fails the next morning.
    assert delivery.processed_at is not None
    retry_at = delivery.processed_at + timedelta(minutes=5)

    try:
        assert handlers.retry_failed_deliveries(db, now=retry_at, provider_factory=lambda _: provider) == 1
        db.expire_all()
        replayed = db.get(Container, uuid.UUID(container_id))
        assert replayed is not None
        assert replayed.milestone.value == "DISCHARGED"

        # already processed: not picked up again
        assert handlers.retry_failed_deliveries(db, now=retry_at, provider_factory=lambda _: provider) == 0
    finally:
        fastapi_app.dependency_overrides.clear()


def test_retries_stop_after_five_attempts(client: TestClient, db: Session, org: Organization) -> None:
    delivery = WebhookDelivery(
        provider="fake",
        delivery_id="exhausted",
        headers={},
        body=b"{}",
        signature_ok=True,
        status="failed",
        attempts=handlers.MAX_DELIVERY_ATTEMPTS,
        processed_at=NOW - timedelta(days=1),
    )
    db.add(delivery)
    db.commit()
    assert handlers.retry_failed_deliveries(db, now=NOW) == 0


def subscribed_container(
    client: TestClient, db: Session, org: Organization, provider_ref: str, number: str = "MSCU4821990"
) -> str:
    """A container the fake provider is already subscribed to. The row is written directly: the
    provider is not in the registry, and this test is about what happens to a delivery afterwards."""
    container_id = make_container(client, number)
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=uuid.UUID(container_id),
            provider="fake",
            provider_ref=provider_ref,
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )
    db.commit()
    return container_id


def stuck_delivery(db: Session, body: bytes, *, attempts: int, delivery_id: str = "stuck") -> WebhookDelivery:
    """A delivery that has already failed `attempts` times and is due for another go."""
    delivery = WebhookDelivery(
        provider="fake",
        delivery_id=delivery_id,
        headers={},
        body=body,
        signature_ok=True,
        status="failed",
        attempts=attempts,
        received_at=NOW - timedelta(hours=2),
        processed_at=NOW - timedelta(days=1),
    )
    db.add(delivery)
    db.commit()
    return delivery


def test_a_tracking_update_we_never_managed_to_apply_is_said_out_loud(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It used to end in silence: the body stayed in the table, the container's history quietly
    missed an event, and the only trace was a log line nobody reads."""
    container_id = subscribed_container(client, db, org, "fake-ref-1")
    body = fakes.payload(
        delivery_id="stuck",
        provider_ref="fake-ref-1",
        events=[fakes.event(MilestoneCode.DISCHARGED, NOW - timedelta(days=1))],
    )

    # A bug on our side, which is what these retries exist for: the delivery routes to the right
    # container and then something in our own processing raises.
    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("a bug on our side")

    monkeypatch.setattr("app.domain.tracking.delivery.ingest", boom)
    provider = fakes.FakeProvider()
    delivery = stuck_delivery(db, body, attempts=handlers.MAX_DELIVERY_ATTEMPTS - 1)

    assert handlers.retry_failed_deliveries(db, now=NOW, provider_factory=lambda _: provider) == 1

    (alert,) = alerts_of(db, AlertKind.TRACKING_DELIVERY_FAILED)
    assert alert.org_id == org.id
    assert alert.container_id == uuid.UUID(container_id)
    assert alert.payload == {
        "provider": "fake",
        "attempts": handlers.MAX_DELIVERY_ATTEMPTS,
        "delivery_id": "stuck",
        "received_at": delivery.received_at.isoformat(),
    }
    # and the next round does not say it again: the delivery is spent, and the key is its own id
    assert handlers.retry_failed_deliveries(db, now=NOW, provider_factory=lambda _: provider) == 0
    assert len(alerts_of(db, AlertKind.TRACKING_DELIVERY_FAILED)) == 1


def test_a_delivery_that_still_has_attempts_left_says_nothing_yet(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Four failures are a retry, not a loss. Only the last one is news."""
    subscribed_container(client, db, org, "fake-ref-2", number="TGHU7654321")
    body = fakes.payload(
        delivery_id="early",
        provider_ref="fake-ref-2",
        events=[fakes.event(MilestoneCode.DISCHARGED, NOW - timedelta(days=1))],
    )

    def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("a bug on our side")

    monkeypatch.setattr("app.domain.tracking.delivery.ingest", boom)
    stuck_delivery(db, body, attempts=1, delivery_id="early")

    assert handlers.retry_failed_deliveries(db, now=NOW, provider_factory=lambda _: fakes.FakeProvider()) == 1
    assert alerts_of(db, AlertKind.TRACKING_DELIVERY_FAILED) == []


def test_a_delivery_nobody_owns_wakes_nobody(client: TestClient, db: Session, org: Organization) -> None:
    """No subscription matches it, so there is no organization to tell and nothing to say about
    anyone's freight. The body stays in the table for whoever investigates."""
    body = fakes.payload(
        delivery_id="orphan",
        provider_ref="nobody-has-this-ref",
        events=[fakes.event(MilestoneCode.DISCHARGED, NOW - timedelta(days=1))],
    )
    stuck_delivery(db, body, attempts=handlers.MAX_DELIVERY_ATTEMPTS - 1, delivery_id="orphan")

    handlers.retry_failed_deliveries(db, now=NOW, provider_factory=lambda _: fakes.FakeProvider())

    assert alerts_of(db, AlertKind.TRACKING_DELIVERY_FAILED) == []
    assert db.scalar(select(WebhookDelivery).where(WebhookDelivery.delivery_id == "orphan")) is not None


def test_backoff_grows_exponentially() -> None:
    waits = [handlers._backoff(n).total_seconds() / 60 for n in range(5)]
    assert waits == [1, 2, 4, 8, 16]


# ---------------------------------------------------------------------------- delivered containers


def test_a_delivered_container_stops_being_tracked_after_a_week(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Providers charge per tracked box, and a returned empty is not going to move again."""
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.milestone = ContainerMilestone.GATE_IN_EMPTY_RETURN
    container.empty_returned_at = NOW - timedelta(days=8)
    container.tracking_state = TrackingState.ACTIVE
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=container.id,
            provider="terminal49",
            provider_ref="t49-done",
            status="active",
            subscribed_at=NOW - timedelta(days=40),
        )
    )
    db.commit()

    unsubscribed: list[str] = []

    class Provider:
        name = "terminal49"

        def unsubscribe(self, provider_ref: str) -> None:
            unsubscribed.append(provider_ref)

    ended = handlers.end_delivered_subscriptions(
        db,
        now=NOW,
        provider_factory=lambda _: Provider(),  # type: ignore[arg-type,return-value]
    )
    assert ended == 1
    assert unsubscribed == ["t49-done"]
    db.expire_all()
    stopped = db.get(Container, uuid.UUID(container_id))
    assert stopped is not None and stopped.tracking_state is TrackingState.ENDED


def test_a_container_delivered_yesterday_is_still_watched(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The week of grace is for the late gate-in that arrives after the delivery event."""
    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.milestone = ContainerMilestone.DELIVERED
    container.gate_out_at = NOW - timedelta(days=1)
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=container.id,
            provider="terminal49",
            provider_ref="t49-recent",
            status="active",
            subscribed_at=NOW - timedelta(days=40),
        )
    )
    db.commit()

    class Provider:
        name = "terminal49"

        def unsubscribe(self, provider_ref: str) -> None:  # pragma: no cover - must not be called
            raise AssertionError("still moving")

    assert (
        handlers.end_delivered_subscriptions(
            db,
            now=NOW,
            provider_factory=lambda _: Provider(),  # type: ignore[arg-type,return-value]
        )
        == 0
    )


def test_a_provider_that_refuses_keeps_the_subscription_open(
    client: TestClient, db: Session, org: Organization
) -> None:
    """It will keep charging until someone sorts it out; closing our row would hide that."""
    from app.domain.tracking.ports import ProviderUnavailable

    container_id = make_container(client)
    container = db.get(Container, uuid.UUID(container_id))
    assert container is not None
    container.milestone = ContainerMilestone.GATE_IN_EMPTY_RETURN
    container.empty_returned_at = NOW - timedelta(days=30)
    subscription = TrackingSubscription(
        org_id=org.id,
        container_id=container.id,
        provider="terminal49",
        provider_ref="t49-stuck",
        status="active",
        subscribed_at=NOW - timedelta(days=60),
    )
    db.add(subscription)
    db.commit()

    class Provider:
        name = "terminal49"

        def unsubscribe(self, provider_ref: str) -> None:
            raise ProviderUnavailable("Terminal49 is down")

    assert (
        handlers.end_delivered_subscriptions(
            db,
            now=NOW,
            provider_factory=lambda _: Provider(),  # type: ignore[arg-type,return-value]
        )
        == 0
    )
    db.expire_all()
    assert subscription.ended_at is None
    assert subscription.last_error == "Terminal49 is down"
