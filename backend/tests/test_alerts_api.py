"""Alerts through the API, and the deduplication that keeps them readable."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.alerts.service import raise_alert, unread_count
from app.domain.models import Alert, AlertKind, AlertSeverity


@pytest.fixture(autouse=True)
def organization(client: TestClient) -> None:
    """The organization row is created by the first authenticated request, like in production."""
    assert client.get("/api/v1/organization").status_code == 200


def make_alert(
    db: Session,
    org_id: uuid.UUID,
    *,
    dedup_key: str = "dnd:1:HIGH",
    title: str = "MSCU4821990: demurrage risk HIGH",
) -> Alert | None:
    return raise_alert(
        db,
        org_id,
        kind=AlertKind.DND_RISK,
        severity=AlertSeverity.CRITICAL,
        title=title,
        body="Last free day 2026-09-10, 1 day left.",
        dedup_key=dedup_key,
    )


def test_the_same_fact_is_only_told_once(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    """A daily recompute sees the same container every morning; the dedup key is what stops it
    turning into ten identical warnings."""
    assert make_alert(db, org_id) is not None
    assert make_alert(db, org_id) is None
    db.commit()
    assert len(client.get("/api/v1/alerts").json()) == 1

    # a different threshold is a different fact
    assert make_alert(db, org_id, dedup_key="dnd:1:INCURRING", title="Demurrage running") is not None
    db.commit()
    assert len(client.get("/api/v1/alerts").json()) == 2


def test_unread_filtering_and_marking(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    make_alert(db, org_id, dedup_key="a")
    make_alert(db, org_id, dedup_key="b")
    db.commit()

    unread = client.get("/api/v1/alerts", params={"unread": True}).json()
    assert len(unread) == 2
    assert client.get("/api/v1/organization").json()["unread_alerts"] == 2

    read = client.post(f"/api/v1/alerts/{unread[0]['id']}/read")
    assert read.status_code == 200
    assert read.json()["read_at"] is not None
    assert client.get("/api/v1/alerts/count").json()["marked"] == 1

    assert client.post("/api/v1/alerts/read-all").json()["marked"] == 1
    assert client.get("/api/v1/alerts", params={"unread": True}).json() == []
    assert client.get("/api/v1/organization").json()["unread_alerts"] == 0


def test_reading_an_unknown_alert_is_a_404(client: TestClient) -> None:
    assert client.post(f"/api/v1/alerts/{uuid.uuid4()}/read").status_code == 404


def test_the_newest_alert_comes_first(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    old = make_alert(db, org_id, dedup_key="old", title="Older")
    assert old is not None
    old.created_at = datetime.now(UTC) - timedelta(days=2)
    make_alert(db, org_id, dedup_key="new", title="Newer")
    db.commit()
    assert [a["title"] for a in client.get("/api/v1/alerts").json()] == ["Newer", "Older"]


def test_alerts_are_invisible_to_another_organization(
    client: TestClient, db: Session, org_id: uuid.UUID
) -> None:
    make_alert(db, org_id, dedup_key="private")
    db.commit()
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert len(list(db.scalars(select(Alert)))) == 1
        assert unread_count(db, org_id) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(Alert))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)


def test_an_alert_carries_its_container_number(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    """A screen writes the alert in its own language from kind, payload and the box number, so the
    number rides along instead of costing a second call per alert."""
    created = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    assert created.status_code == 201, created.text
    container_id = uuid.UUID(created.json()["id"])
    set_current_org(db, org_id)
    alert = raise_alert(
        db,
        org_id,
        kind=AlertKind.DND_RISK,
        severity=AlertSeverity.WARNING,
        title="MSCU4821990: demurrage risk HIGH",
        dedup_key="dnd:box:HIGH",
        container_id=container_id,
        payload={"risk": "HIGH", "last_free_day": "2026-09-20"},
    )
    assert alert is not None
    db.commit()
    make_alert(db, org_id, dedup_key="no-box", title="provider down")
    db.commit()

    by_title = {a["title"]: a for a in client.get("/api/v1/alerts").json()}
    assert by_title["MSCU4821990: demurrage risk HIGH"]["container_number"] == "MSCU4821990"
    assert by_title["provider down"]["container_number"] is None
    read = client.post(f"/api/v1/alerts/{alert.id}/read")
    assert read.status_code == 200 and read.json()["container_number"] == "MSCU4821990"
