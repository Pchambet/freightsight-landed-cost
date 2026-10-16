"""The demo dataset's boxes are made up. A prospect who loaded it must not be woken by CRITICAL
demurrage e-mails about them — once a day for a fortnight, then weekly for two months."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.alerts.notifier import Notification
from app.domain.models import Alert, Container, DndRisk, Membership, Organization, User
from app.jobs import handlers


class RecordingNotifier:
    name = "recording"

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)


@pytest.fixture
def demo(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    """An organization with one member who has an e-mail address, and the demo loaded."""
    assert client.get("/api/v1/organization").status_code == 200
    org = db.get(Organization, org_id)
    assert org is not None
    user = User(external_id="user_demo", email="prospect@example.test", name="Prospect")
    db.add(user)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id))
    db.commit()
    assert client.post("/api/v1/organization/sample-data").status_code == 201
    return org


def alert_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Alert)) or 0


def test_the_demo_shows_its_alerts_in_the_product_and_mails_none(db: Session, demo: Organization) -> None:
    assert alert_count(db) > 0  # the bell shows the story
    notifier = RecordingNotifier()

    assert handlers.dispatch_alerts(db, notifier=notifier) == 0
    assert notifier.sent == []


def test_a_demo_box_keeps_its_clock_but_raises_no_reminder(db: Session, demo: Organization) -> None:
    """The morning pass still counts the demo box's free days, so its badge stays true; it just does
    not raise "demurrage running" about a box that does not exist."""
    before = alert_count(db)
    later = datetime.now(UTC) + timedelta(days=6)

    handlers.recompute_dnd_risk(db, now=later)

    box = db.scalar(select(Container).where(Container.container_number == "MSCU4821990"))
    assert box is not None and box.dnd_risk is DndRisk.INCURRING
    assert alert_count(db) == before
    assert handlers.dispatch_alerts(db, notifier=RecordingNotifier(), now=later) == 0


def test_a_real_box_next_to_the_demo_still_alerts_and_is_mailed(
    client: TestClient, db: Session, demo: Organization
) -> None:
    res = client.post("/api/v1/containers", json={"container_number": "TCLU7654321"})
    assert res.status_code == 201, res.text
    landed = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    assert (
        client.patch(f"/api/v1/containers/{res.json()['id']}", json={"discharged_at": landed}).status_code
        == 200
    )

    handlers.recompute_dnd_risk(db)
    notifier = RecordingNotifier()

    assert handlers.dispatch_alerts(db, notifier=notifier) == 1
    (digest,) = notifier.sent
    assert {alert.container_id for alert in digest.alerts} == {uuid.UUID(res.json()["id"])}
