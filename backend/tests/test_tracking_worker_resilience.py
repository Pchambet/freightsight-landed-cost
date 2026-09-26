"""What the tracking sweeps do when the provider, not the container, is the thing that is broken.

The distinction is the whole file. "This box has gone quiet" is news about a customer's freight and
belongs on their screen. "We cannot reach Shipsgo" is news about us and our supplier, and dressing
it up as the first one tells every customer at once that their containers are in trouble and sends
them to check a carrier site that has nothing to do with it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import (
    Alert,
    AlertKind,
    Container,
    ContainerMilestone,
    Organization,
    TrackingState,
    TrackingSubscription,
)
from app.domain.tracking.ports import ProviderUnavailable, SubscribeRequest, Subscription, WebhookEnvelope
from app.jobs import handlers

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


class DeadProvider:
    """A provider that is up as far as the network is concerned and answers nothing useful."""

    name = "fake"

    def __init__(self, *, error: str = "Shipsgo is unreachable: timed out") -> None:
        self.error = error
        self.asked = 0
        self.unsubscribed = 0

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        self.asked += 1
        raise ProviderUnavailable(self.error)

    def unsubscribe(self, provider_ref: str) -> None:
        self.unsubscribed += 1
        raise ProviderUnavailable(self.error)

    def subscribe(self, req: SubscribeRequest) -> Subscription:  # pragma: no cover - unused here
        raise ProviderUnavailable(self.error)

    def parse(self, body: bytes) -> WebhookEnvelope:  # pragma: no cover - unused here
        raise NotImplementedError

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        raise NotImplementedError  # pragma: no cover - unused here


class ExplodingProvider(DeadProvider):
    """Not an outage: a bug. A payload shape nobody anticipated, a parser that trips over it."""

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        self.asked += 1
        raise KeyError("attributes")


def make_container(client: TestClient, number: str) -> uuid.UUID:
    res = client.post("/api/v1/containers", json={"container_number": number})
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["id"])


def subscribe(db: Session, org: Organization, container_id: uuid.UUID, *, provider: str = "fake") -> None:
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=container_id,
            provider=provider,
            provider_ref=f"ref-{container_id}",
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )


def containers(client: TestClient, db: Session, org: Organization, count: int) -> list[uuid.UUID]:
    ids = []
    for index in range(count):
        container_id = make_container(client, f"MSCU48219{index:02d}")
        subscribe(db, org, container_id)
        ids.append(container_id)
    db.commit()
    return ids


def alerts_of(db: Session, kind: AlertKind) -> list[Alert]:
    return list(db.scalars(select(Alert).where(Alert.kind == kind)))


# ---------------------------------------------------------------------------- the wrong accusation


def test_a_provider_outage_is_not_blamed_on_the_containers(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The bug this file exists for: an hour of Shipsgo being down used to mark every customer's
    containers FAILED and tell each of them to go and check the carrier's website."""
    ids = containers(client, db, org, 2)
    provider = DeadProvider()

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    assert alerts_of(db, AlertKind.TRACKING_DATA_MISSING) == []
    db.expire_all()
    for container_id in ids:
        container = db.get(Container, container_id)
        assert container is not None
        assert container.tracking_state is not TrackingState.FAILED

    # the reason is still recorded where someone debugging would look for it
    subscription = db.scalars(select(TrackingSubscription)).first()
    assert subscription is not None
    assert subscription.last_error and "unreachable" in subscription.last_error


def test_the_provider_being_down_is_said_once_about_the_provider(
    client: TestClient, db: Session, org: Organization
) -> None:
    containers(client, db, org, 5)
    provider = DeadProvider()

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    (alert,) = alerts_of(db, AlertKind.TRACKING_PROVIDER_DOWN)
    assert "fake" in alert.title
    assert "Nothing is wrong with the containers themselves" in alert.body
    assert alert.container_id is None  # it is not about any one box

    # and running again the same day does not say it twice
    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)
    assert len(alerts_of(db, AlertKind.TRACKING_PROVIDER_DOWN)) == 1


def test_every_organization_whose_tracking_stopped_is_told_not_just_one(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Whose turn it was when the third failure landed is an accident of iteration order. Both of
    these companies stopped being tracked this round, so both hear about it — once each."""
    containers(client, db, org, 4)
    other = Organization(name="Other importer", base_currency="EUR")
    db.add(other)
    db.flush()
    for index in range(3):
        container = Container(
            org_id=other.id,
            container_number=f"TGHU76543{index:02d}",
            milestone=ContainerMilestone.VESSEL_DEPARTED,
            tracking_state=TrackingState.ACTIVE,
        )
        db.add(container)
        db.flush()
        subscribe(db, other, container.id)
    db.commit()
    provider = DeadProvider()

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    told = {alert.org_id for alert in alerts_of(db, AlertKind.TRACKING_PROVIDER_DOWN)}
    assert told == {org.id, other.id}
    # still one ask per failure up to the trip, for both companies together
    assert provider.asked == handlers.CIRCUIT_TRIPS_AFTER


def test_a_milestone_that_never_came_is_still_reported(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The other half: the change must not have silenced the alert that was the point of the job.

    Webhook-only, so there is nothing to ask anybody — but the ship was due three days ago and
    nobody has said it arrived, which is a fact about this box and worth saying."""
    container_id = make_container(client, "MSCU4821990")
    container = db.get(Container, container_id)
    assert container is not None
    container.eta = NOW - timedelta(days=3)
    db.add(
        TrackingSubscription(
            org_id=org.id,
            container_id=container_id,
            provider="fake",
            provider_ref=None,  # webhook-only: nothing to ask, and nothing has come
            status="active",
            subscribed_at=NOW - timedelta(days=5),
        )
    )
    db.commit()

    handlers.poll_stale_subscriptions(db, now=NOW)

    (alert,) = alerts_of(db, AlertKind.TRACKING_DATA_MISSING)
    assert "MSCU4821990" in alert.title
    assert alerts_of(db, AlertKind.TRACKING_PROVIDER_DOWN) == []


def test_a_provider_that_answers_with_nothing_new_says_nothing_and_clears_the_red(
    client: TestClient, db: Session, org: Organization
) -> None:
    """ "The provider answered" and "there was nothing new" used to be the same outcome, so a feed
    that was working perfectly produced a daily alert and a red screen for every box at sea."""
    container_id = make_container(client, "MSCU4821991")
    container = db.get(Container, container_id)
    assert container is not None
    container.tracking_state = TrackingState.FAILED  # left over from the old behaviour
    container.milestone = ContainerMilestone.VESSEL_DEPARTED
    container.eta = NOW + timedelta(days=15)
    subscribe(db, org, container_id)
    db.commit()

    class Quiet(DeadProvider):
        def fetch(self, provider_ref: str) -> WebhookEnvelope:
            self.asked += 1
            return WebhookEnvelope(provider="fake", delivery_id=provider_ref, events=[])

    provider = Quiet()
    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    assert provider.asked == 1  # it is still re-read, in case a webhook was lost
    assert alerts_of(db, AlertKind.TRACKING_DATA_MISSING) == []
    db.expire_all()
    healed = db.get(Container, container_id)
    assert healed is not None and healed.tracking_state is TrackingState.ACTIVE


# ---------------------------------------------------------------------------- the cost of asking


def test_a_silent_provider_is_asked_three_times_not_once_per_container(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Bounded per call since the timeout audit — but a bound multiplied by every tracked container
    is still an hour of a worker holding a queueing lock to learn one thing."""
    containers(client, db, org, 12)
    provider = DeadProvider()

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    assert provider.asked == handlers.CIRCUIT_TRIPS_AFTER


def test_the_next_round_tries_again(client: TestClient, db: Session, org: Organization) -> None:
    """Per sweep, not persisted: most outages are minutes long, and the next run should find out."""
    containers(client, db, org, 4)
    provider = DeadProvider()

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)
    asked_first = provider.asked
    handlers.poll_stale_subscriptions(db, now=NOW + timedelta(hours=4), provider_factory=lambda _: provider)

    assert provider.asked == asked_first * 2


def test_one_container_answering_keeps_the_provider_open(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Three *in a row*. A provider that answers about one box is not down, whatever it said."""
    containers(client, db, org, 8)
    calls = {"n": 0}

    class Flaky(DeadProvider):
        def fetch(self, provider_ref: str) -> WebhookEnvelope:
            calls["n"] += 1
            if calls["n"] % 3 == 0:
                return WebhookEnvelope(provider="fake", delivery_id=provider_ref, events=[])
            raise ProviderUnavailable("transient")

    handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: Flaky())

    assert calls["n"] == 8  # every container was asked about; the circuit never tripped
    assert alerts_of(db, AlertKind.TRACKING_PROVIDER_DOWN) == []


# ---------------------------------------------------------------------------- one bad row


def test_a_bug_on_one_container_does_not_cost_the_others_their_round(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A KeyError from a parser is not a ProviderUnavailable, and used to end the whole sweep —
    every organization after it in the loop simply did not run."""
    containers(client, db, org, 3)
    provider = ExplodingProvider()

    polled = handlers.poll_stale_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    assert polled == 3  # it kept going
    assert provider.asked == 3


def test_a_provider_that_cannot_be_built_does_not_stop_the_replay_queue(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A key removed from the environment, or a provider name no longer in the registry."""
    from app.domain.models import WebhookDelivery

    db.add(
        WebhookDelivery(
            provider="gone",
            delivery_id="d-1",
            body=b"{}",
            signature_ok=True,
            status="failed",
            received_at=NOW - timedelta(hours=1),
        )
    )
    db.commit()

    def factory(name: str) -> Any:
        raise RuntimeError(f"no provider {name}")

    assert handlers.retry_failed_deliveries(db, now=NOW, provider_factory=factory) == 0
    # the body is still there: this returns to it when the provider is configured again
    assert db.scalars(select(WebhookDelivery)).first() is not None


# ---------------------------------------------------------------------------- ending subscriptions


def test_unsubscribing_stops_asking_a_provider_that_is_not_answering(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Same bound, the other sweep: a provider that will not answer is not asked once per box."""
    ids = containers(client, db, org, 10)
    for container_id in ids:
        container = db.get(Container, container_id)
        assert container is not None
        container.milestone = ContainerMilestone.DELIVERED
        container.empty_returned_at = NOW - timedelta(days=30)
    db.commit()

    provider = DeadProvider()
    ended = handlers.end_delivered_subscriptions(db, now=NOW, provider_factory=lambda _: provider)

    assert ended == 0  # nothing was ended, because nothing could be unsubscribed
    assert provider.unsubscribed == handlers.CIRCUIT_TRIPS_AFTER
    # and every subscription is still open, so the next run tries again rather than leaking a
    # container the provider keeps charging for
    assert all(s.ended_at is None for s in db.scalars(select(TrackingSubscription)))
