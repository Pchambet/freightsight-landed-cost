"""The audit log: what gets recorded, who can read it, and the fact that nobody can rewrite it."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.audit.service import record
from app.domain.models import AuditLog, Organization, User


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def actions(client: TestClient, **params: object) -> list[str]:
    body = client.get("/api/v1/audit-log", params=params).json()
    return [entry["action"] for entry in body["entries"]]


def seed_cost(client: TestClient) -> tuple[str, str]:
    container = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    assert container.status_code == 201
    container_id = container.json()["id"]
    cost = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": "THC",
            "amount": "275.00",
            "currency": "EUR",
            "cost_date": "2026-03-12",
        },
    )
    assert cost.status_code == 201, cost.text
    return container_id, cost.json()["id"]


# ---------------------------------------------------------------------------- what is recorded


def test_the_life_of_a_cost_is_recorded(client: TestClient, org: Organization) -> None:
    _, cost_id = seed_cost(client)
    client.patch(f"/api/v1/costs/{cost_id}", json={"amount": "300.00"})
    client.delete(f"/api/v1/costs/{cost_id}")

    entries = client.get("/api/v1/audit-log", params={"entity_id": cost_id}).json()["entries"]
    assert [e["action"] for e in entries] == ["cost.deleted", "cost.updated", "cost.created"]

    updated = entries[1]
    assert updated["before"]["amount"] == "275.00"
    assert updated["after"]["amount"] == "300.00"
    # the deletion keeps what was destroyed: this is the entry someone looks for later
    assert entries[0]["before"]["cost_type"] == "THC"
    assert entries[0]["after"] is None


def test_the_log_names_who_did_it_and_what_the_ids_stand_for(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A reader gets a name and a container number, not two uuid fragments."""
    container_id, cost_id = seed_cost(client)
    member = User(external_id="user_audit_reader", email="daf@roulezbio.example", name=None)
    db.add(member)
    db.flush()
    record(
        db,
        org.id,
        actor_user_id=member.id,
        action="cost.closed",
        entity_type="cost",
        entity_id=uuid.UUID(cost_id),
        after={"container_id": container_id},
    )
    db.commit()

    page = client.get("/api/v1/audit-log", params={"entity_id": cost_id}).json()
    closed, created = page["entries"]
    assert closed["actor_name"] == "daf@roulezbio.example"  # no name at Clerk: the e-mail
    assert created["actor_name"] is None  # the development principal is nobody
    assert page["labels"][container_id] == "MSCU4821990"
    assert cost_id not in page["labels"]  # a cost has no name of its own: the screen builds one


def test_money_is_not_rounded_on_its_way_into_the_log(client: TestClient, org: Organization) -> None:
    _, cost_id = seed_cost(client)
    entry = client.get("/api/v1/audit-log", params={"entity_id": cost_id}).json()["entries"][0]
    assert entry["after"]["amount"] == "275.00"  # a string, not a float
    assert entry["after"]["fx_rate"] == "1"  # a string, whatever its scale


def test_settings_changes_are_recorded(client: TestClient, org: Organization) -> None:
    res = client.patch("/api/v1/organization", json={"free_days_demurrage": 9})
    assert res.status_code == 200
    (entry,) = client.get("/api/v1/audit-log", params={"entity_type": "organization"}).json()["entries"]
    assert entry["action"] == "organization.updated"
    assert entry["before"] == {"free_days_demurrage": 5}
    assert entry["after"] == {"free_days_demurrage": 9}


def test_a_manual_milestone_is_recorded(client: TestClient, org: Organization) -> None:
    container_id, _ = seed_cost(client)
    moment = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    res = client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "DISCHARGED", "occurred_at": moment},
    )
    assert res.status_code == 201
    recorded = actions(client, entity_type="container")
    assert "container.tracking_event_added" in recorded


def test_changing_what_is_in_the_box_is_recorded(client: TestClient, db: Session, org: Organization) -> None:
    """The loads decide how every cost is split, so a change to them has to be explainable."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "po_number": "PO-A",
            "currency": "EUR",
            "lines": [{"line_no": 1, "quantity": "100", "unit_price": "10.00"}],
        },
    )
    assert po.status_code == 201, po.text
    line_id = po.json()["lines"][0]["id"]
    container_id, _ = seed_cost(client)

    res = client.put(
        f"/api/v1/containers/{container_id}/loads", json=[{"po_line_id": line_id, "quantity": "60"}]
    )
    assert res.status_code == 200, res.text
    entry = client.get("/api/v1/audit-log", params={"action": "container.loads_replaced"}).json()["entries"][
        0
    ]
    assert entry["before"] == {}
    assert entry["after"] == {line_id: "60"}


def test_a_rolled_back_action_leaves_no_entry(client: TestClient, org: Organization) -> None:
    """The log records what happened, not what was attempted."""
    seed_cost(client)
    refused = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": str(uuid.uuid4()),
            "cost_type": "THC",
            "amount": "1.00",
            "currency": "EUR",
            "cost_date": "2026-03-12",
        },
    )
    assert refused.status_code == 404
    assert actions(client, action="cost.created") == ["cost.created"]  # only the one that worked


# ---------------------------------------------------------------------------- pagination


def test_pages_walk_backwards_through_history(client: TestClient, db: Session, org: Organization) -> None:
    for index in range(5):
        record(
            db,
            org.id,
            actor_user_id=None,
            action="test.event",
            entity_type="test",
            entity_id=None,
            after={"index": index},
        )
    db.commit()

    first = client.get("/api/v1/audit-log", params={"action": "test.event", "limit": 2}).json()
    assert [e["after"]["index"] for e in first["entries"]] == [4, 3]
    assert first["next_cursor"]

    second = client.get(
        "/api/v1/audit-log",
        params={"action": "test.event", "limit": 2, "cursor": first["next_cursor"]},
    ).json()
    assert [e["after"]["index"] for e in second["entries"]] == [2, 1]

    last = client.get(
        "/api/v1/audit-log",
        params={"action": "test.event", "limit": 2, "cursor": second["next_cursor"]},
    ).json()
    assert [e["after"]["index"] for e in last["entries"]] == [0]
    assert last["next_cursor"] is None


def test_a_forged_cursor_is_refused(client: TestClient, org: Organization) -> None:
    res = client.get("/api/v1/audit-log", params={"cursor": "not-a-cursor"})
    assert res.status_code == 422
    assert res.json()["code"] == "INVALID_CURSOR"


# ---------------------------------------------------------------------------- append-only, for real


def test_the_log_cannot_be_rewritten_by_the_application_role(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A log the investigated party can edit is not evidence. Two locks: the role holds no UPDATE or
    DELETE, and a trigger refuses them to everyone, owner included."""
    seed_cost(client)
    entry_id = db.scalar(select(AuditLog.id))
    assert entry_id is not None

    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        for statement in (
            "UPDATE audit_log SET action = 'nothing to see here'",
            "DELETE FROM audit_log",
        ):
            savepoint = db.begin_nested()
            with pytest.raises(Exception) as raised:
                db.execute(text(statement))
            savepoint.rollback()
            assert "permission" in str(raised.value).lower() or "append-only" in str(raised.value)
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)

    # and the owner cannot either: the trigger does not care who is asking
    savepoint = db.begin_nested()
    with pytest.raises(Exception) as raised:
        db.execute(text("DELETE FROM audit_log"))
    savepoint.rollback()
    assert "append-only" in str(raised.value)


def test_the_log_is_invisible_to_another_organization(
    client: TestClient, db: Session, org: Organization
) -> None:
    seed_cost(client)
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert list(db.scalars(select(AuditLog)))
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(AuditLog))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)
