"""Sending alert digests: grouping, recipients, and the Resend adapter against a mocked transport."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.notifications.log_notifier import LogNotifier
from app.adapters.notifications.resend import ResendNotifier
from app.domain.alerts.notifier import Notification, make_notifier
from app.domain.alerts.service import raise_alert
from app.domain.models import Alert, AlertKind, AlertSeverity, Membership, Organization, User
from app.domain.tracking.ports import ProviderUnavailable
from app.jobs import handlers


class RecordingNotifier:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)


class BrokenNotifier:
    name = "broken"

    def send(self, notification: Notification) -> None:
        raise ProviderUnavailable("the mail provider is down")


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    user = User(external_id="user_1", email="pierre@example.test", name="Pierre")
    db.add(user)
    db.flush()
    db.add(Membership(org_id=organization.id, user_id=user.id))
    db.commit()
    return organization


def make_alerts(db: Session, org: Organization, count: int) -> None:
    for i in range(count):
        raise_alert(
            db,
            org.id,
            kind=AlertKind.DND_RISK,
            severity=AlertSeverity.CRITICAL if i == 0 else AlertSeverity.WARNING,
            title=f"MSCU482199{i}: demurrage risk HIGH",
            body="Last free day 2026-09-10, 1 day left.",
            dedup_key=f"dnd:{i}:HIGH",
        )
    db.commit()


def test_one_digest_per_organization_not_one_per_alert(
    client: TestClient, db: Session, org: Organization
) -> None:
    make_alerts(db, org, 3)
    notifier = RecordingNotifier()

    assert handlers.dispatch_alerts(db, notifier=notifier) == 1
    (notification,) = notifier.sent
    assert len(notification.alerts) == 3
    assert notification.recipients == ["pierre@example.test"]
    assert notification.subject == "FreightSight : 3 alertes"  # French by default
    assert "demurrage risk HIGH" in notification.text()  # no payload: the stored words stand

    # nothing left to say on the next round
    assert handlers.dispatch_alerts(db, notifier=notifier) == 0
    assert len(notifier.sent) == 1

    # and an alert raised afterwards goes out on its own, next round
    make_alerts(db, org, 4)
    assert handlers.dispatch_alerts(db, notifier=notifier) == 1
    assert len(notifier.sent[-1].alerts) == 1


def test_a_single_alert_keeps_its_own_title(client: TestClient, db: Session, org: Organization) -> None:
    make_alerts(db, org, 1)
    notifier = RecordingNotifier()
    handlers.dispatch_alerts(db, notifier=notifier)
    assert notifier.sent[0].subject == "FreightSight : MSCU4821990: demurrage risk HIGH"


# ---------------------------------------------------------------------------- the reader's language


def container_with_alert(client: TestClient, db: Session, org: Organization) -> None:
    """One alert carrying everything its sentence needs: the kind, the moving parts, the box."""
    res = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    assert res.status_code == 201, res.text
    raise_alert(
        db,
        org.id,
        kind=AlertKind.DND_RISK,
        severity=AlertSeverity.CRITICAL,
        title="MSCU4821990: demurrage risk HIGH",
        body="Last free day 2026-09-20, 3 day(s) left.",
        dedup_key="dnd:one",
        container_id=uuid.UUID(res.json()["id"]),
        payload={"risk": "HIGH", "last_free_day": "2026-09-20"},
    )
    db.commit()


def test_the_digest_is_written_in_the_language_of_the_organization(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The screen already writes its own sentence from the kind and the payload. A digest that kept
    the stored English would have the same alert reading two different ways."""
    container_with_alert(client, db, org)
    notifier = RecordingNotifier()

    handlers.dispatch_alerts(db, notifier=notifier)

    body = notifier.sent[0].text()
    assert notifier.sent[0].subject == "FreightSight : MSCU4821990 : risque de surestaries élevé"
    assert "[Critique] MSCU4821990 : risque de surestaries élevé" in body
    assert "Dernier jour franc le 20/09/2026" in body
    assert "Ouvrez FreightSight" in body
    assert "demurrage" not in body


def test_an_english_organization_gets_the_english_sentences(
    client: TestClient, db: Session, org: Organization
) -> None:
    org.locale = "en"
    db.commit()
    container_with_alert(client, db, org)
    notifier = RecordingNotifier()

    handlers.dispatch_alerts(db, notifier=notifier)

    body = notifier.sent[0].text()
    assert notifier.sent[0].subject == "FreightSight: MSCU4821990: demurrage risk high"
    assert "Last free day 20 Sep 2026" in body
    assert "Open FreightSight to acknowledge these." in body
    assert "surestaries" not in body


def test_an_alert_without_the_parts_of_its_sentence_is_sent_as_it_was_stored(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Alerts raised before the payload carried what a sentence needs must still go out, in the
    words they were written with, rather than as a phrase with a hole in it."""
    make_alerts(db, org, 1)  # a DND_RISK with no payload and no container
    notifier = RecordingNotifier()

    handlers.dispatch_alerts(db, notifier=notifier)

    assert "MSCU4821990: demurrage risk HIGH" in notifier.sent[0].text()
    assert "Last free day 2026-09-10, 1 day left." in notifier.sent[0].text()


def test_a_failed_send_leaves_the_alerts_for_the_next_round(
    client: TestClient, db: Session, org: Organization
) -> None:
    make_alerts(db, org, 2)
    assert handlers.dispatch_alerts(db, notifier=BrokenNotifier()) == 0
    db.expire_all()
    unnotified = list(db.scalars(select(Alert).where(Alert.notified_at.is_(None))))
    assert len(unnotified) == 2

    notifier = RecordingNotifier()
    assert handlers.dispatch_alerts(db, notifier=notifier) == 1
    assert len(notifier.sent[0].alerts) == 2


def test_without_a_key_the_digest_is_logged_and_still_marked_sent(
    client: TestClient, db: Session, org: Organization, caplog: pytest.LogCaptureFixture
) -> None:
    """Otherwise the day a real key is added, the first round would mail out the whole backlog."""
    make_alerts(db, org, 2)
    with caplog.at_level("INFO", logger="freightsight.alerts"):
        assert handlers.dispatch_alerts(db, notifier=LogNotifier()) == 1
    assert "alert digest" in caplog.text
    db.expire_all()
    assert list(db.scalars(select(Alert).where(Alert.notified_at.is_(None)))) == []


def test_the_notifier_is_the_log_one_until_both_settings_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import get_settings

    get_settings.cache_clear()
    assert make_notifier().name == "log"

    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    get_settings.cache_clear()
    assert make_notifier().name == "log"  # a key without a sender address is not enough

    monkeypatch.setenv("ALERTS_FROM_EMAIL", "alerts@freightsight.test")
    get_settings.cache_clear()
    assert make_notifier().name == "resend"
    get_settings.cache_clear()


# ---------------------------------------------------------------------------- the Resend adapter


def notification(recipients: list[str]) -> Notification:
    alert = Alert(
        org_id=uuid.uuid4(),
        kind=AlertKind.DND_RISK,
        severity=AlertSeverity.CRITICAL,
        title="MSCU4821990: demurrage risk HIGH",
        body="Last free day 2026-09-10.",
        dedup_key="k",
        created_at=datetime.now(UTC),
    )
    return Notification(org_id=uuid.uuid4(), org_name="Acme", recipients=recipients, alerts=[alert])


def test_resend_posts_one_plain_message() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "email-1"})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.resend.test")
    ResendNotifier("re_key", "alerts@freightsight.test", client=client).send(
        notification(["pierre@example.test", "ops@example.test"])
    )

    assert seen["url"] == "https://api.resend.test/emails"
    assert seen["auth"] == "Bearer re_key"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["from"] == "alerts@freightsight.test"
    assert body["to"] == ["pierre@example.test", "ops@example.test"]
    assert "demurrage risk HIGH" in body["text"]


def test_resend_failures_are_raised_so_the_alerts_are_retried() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(422, text="invalid from address")),
        base_url="https://api.resend.test",
    )
    with pytest.raises(ProviderUnavailable):
        ResendNotifier("re_key", "nope", client=client).send(notification(["a@example.test"]))


def test_resend_does_not_call_the_api_without_a_recipient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not be called
        raise AssertionError("no recipient, no request")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.resend.test")
    ResendNotifier("re_key", "alerts@freightsight.test", client=client).send(notification([]))


# ---------------------------------------------------------------------------- when nothing leaves


def test_an_organization_with_no_e_mail_address_does_not_lose_its_alerts(
    client: TestClient, db: Session, org: Organization, caplog: pytest.LogCaptureFixture
) -> None:
    """A member created through Clerk without an e-mail used to cost the whole organization every
    demurrage alert it ever had: `send` returned, the alerts were marked notified, and they were
    gone. Nothing said so anywhere."""
    db.execute(text("UPDATE users SET email = ''"))
    make_alerts(db, org, 2)

    with caplog.at_level("ERROR"):
        assert handlers.dispatch_alerts(db, notifier=RecordingNotifier()) == 0
    assert "could not send the alert digest" in caplog.text
    db.expire_all()
    assert len(list(db.scalars(select(Alert).where(Alert.notified_at.is_(None))))) == 2


def test_a_permanent_refusal_stops_being_retried_every_five_minutes(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Resend answering 403 "domain not verified" left the same batch being retried every five
    minutes for ever, with no counter, no give-up and nobody told. After five tries the retry slows
    to hourly and the customer is told inside the product — the one channel that still works."""
    make_alerts(db, org, 1)
    broken = BrokenNotifier()
    moment = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)

    for minute in range(0, 30, 5):
        handlers.dispatch_alerts(db, notifier=broken, now=moment + timedelta(minutes=minute))

    told = list(db.scalars(select(Alert).where(Alert.kind == AlertKind.TRACKING_DELIVERY_FAILED)))
    assert len(told) == 1
    assert told[0].payload["channel"] == "email"
    assert told[0].payload["attempts"] == handlers.MAX_DIGEST_ATTEMPTS
    assert told[0].container_id is None

    counted = 0

    class Counting:
        name = "counting"

        def send(self, notification: Notification) -> None:
            nonlocal counted
            counted += 1
            raise ProviderUnavailable("still down")

    # within the hour, it is not tried again
    handlers.dispatch_alerts(db, notifier=Counting(), now=moment + timedelta(minutes=35))
    assert counted == 0
    handlers.dispatch_alerts(db, notifier=Counting(), now=moment + timedelta(hours=2))
    assert counted == 1


def test_the_channel_coming_back_clears_the_count_and_sends_everything(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Nothing is thrown away while the channel is down, which is the whole point of not marking
    unsent alerts as sent."""
    make_alerts(db, org, 3)
    moment = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
    handlers.dispatch_alerts(db, notifier=BrokenNotifier(), now=moment)

    notifier = RecordingNotifier()
    assert handlers.dispatch_alerts(db, notifier=notifier, now=moment + timedelta(minutes=5)) == 1
    assert len(notifier.sent[0].alerts) == 3
    failures, _ = handlers._digest_failures(db, org)
    assert failures == 0
