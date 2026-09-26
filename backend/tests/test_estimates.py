"""Estimated costs, and what happens when the invoice finally arrives."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.costing.service import compute, live_and_superseded
from app.domain.models import Cost, CostStatus, Organization


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def seed(client: TestClient) -> tuple[str, str]:
    """One container holding one PO line, so a cost has somewhere to land."""
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
    container = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    container_id = container.json()["id"]
    loads = client.put(
        f"/api/v1/containers/{container_id}/loads", json=[{"po_line_id": line_id, "quantity": "100"}]
    )
    assert loads.status_code == 200, loads.text
    return container_id, line_id


def add_cost(
    client: TestClient,
    container_id: str,
    *,
    amount: str,
    status: str = "ACTUAL",
    cost_type: str = "OCEAN_FREIGHT",
) -> dict[str, Any]:
    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": cost_type,
            "amount": amount,
            "currency": "EUR",
            "cost_date": "2026-03-12",
            "status": status,
        },
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def landed(client: TestClient, container_id: str) -> dict[str, Any]:
    report: dict[str, Any] = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()
    return report


# ---------------------------------------------------------------------------- an estimate allocates


def test_an_estimate_is_allocated_like_any_cost(client: TestClient, org: Organization) -> None:
    """The point of the feature: a landed cost that is right on the day the box lands."""
    container_id, _ = seed(client)
    add_cost(client, container_id, amount="3000.00", status="ESTIMATE")

    report = landed(client, container_id)
    assert report["totals"]["allocated"] == "3000.00"
    assert report["totals"]["landed"] == "4000.00"  # 1000 of goods + 3000 of freight


def test_a_replaced_estimate_leaves_the_allocation_and_stays_on_file(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    actual = add_cost(client, container_id, amount="3120.50")

    res = client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": estimate["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["supersedes_cost_id"] == estimate["id"]

    report = landed(client, container_id)
    assert report["totals"]["allocated"] == "3120.50"  # the invoice, not the quote
    assert [c["id"] for c in report["costs"]] == [actual["id"]]

    # the estimate is still there: it is what the variance is measured against
    still_there = client.get(f"/api/v1/costs/{estimate['id']}").json()
    assert still_there["amount"] == "3000.00"
    assert still_there["status"] == "ESTIMATE"


def test_a_closed_estimate_stops_being_allocated(client: TestClient, org: Organization) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="450.00", status="ESTIMATE", cost_type="INSPECTION")
    assert landed(client, container_id)["totals"]["allocated"] == "450.00"

    res = client.post(
        f"/api/v1/costs/{estimate['id']}/close", json={"reason": "the forwarder never billed it"}
    )
    assert res.status_code == 200, res.text
    assert res.json()["closed_at"] is not None

    report = landed(client, container_id)
    assert report["totals"]["allocated"] == "0.00"
    assert report["costs"] == []
    # closed once, and only an estimate can be closed
    assert client.post(f"/api/v1/costs/{estimate['id']}/close", json={"reason": "again"}).status_code == 409


def test_an_actual_cannot_be_closed(client: TestClient, org: Organization) -> None:
    container_id, _ = seed(client)
    actual = add_cost(client, container_id, amount="3000.00")
    res = client.post(f"/api/v1/costs/{actual['id']}/close", json={"reason": "no"})
    assert res.status_code == 422
    assert res.json()["code"] == "NOT_AN_ESTIMATE"


# ---------------------------------------------------------------------------- the pairing rules


def test_the_pairings_that_make_no_sense_are_refused(client: TestClient, org: Organization) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    other_estimate = add_cost(client, container_id, amount="275.00", status="ESTIMATE", cost_type="THC")
    actual = add_cost(client, container_id, amount="3120.50")
    second_actual = add_cost(client, container_id, amount="3100.00", cost_type="AIR_FREIGHT")

    # an estimate cannot replace anything
    res = client.patch(f"/api/v1/costs/{other_estimate['id']}", json={"supersedes_cost_id": estimate["id"]})
    assert res.status_code == 422 and res.json()["code"] == "NOT_AN_ACTUAL"

    # nor can a cost replace itself
    res = client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": actual["id"]})
    assert res.status_code == 422 and res.json()["code"] == "SELF_SUPERSEDE"

    # nor can an actual replace another actual
    res = client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": second_actual["id"]})
    assert res.status_code == 422 and res.json()["code"] == "NOT_AN_ESTIMATE"

    # one estimate, one replacement
    assert (
        client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": estimate["id"]}).status_code
        == 200
    )
    res = client.patch(f"/api/v1/costs/{second_actual['id']}", json={"supersedes_cost_id": estimate["id"]})
    assert res.status_code == 422 and res.json()["code"] == "ALREADY_SUPERSEDED"


def test_a_closed_estimate_cannot_be_replaced(client: TestClient, org: Organization) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    actual = add_cost(client, container_id, amount="3120.50")
    client.post(f"/api/v1/costs/{estimate['id']}/close", json={"reason": "never billed"})

    res = client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": estimate["id"]})
    assert res.status_code == 422 and res.json()["code"] == "ESTIMATE_CLOSED"


# ---------------------------------------------------------------------------- the split, in the engine


def test_the_split_of_live_and_replaced_costs(client: TestClient, db: Session, org: Organization) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    actual = add_cost(client, container_id, amount="3120.50")
    client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": estimate["id"]})

    rows = list(db.scalars(select(Cost).where(Cost.org_id == org.id)))
    live, pairs = live_and_superseded(rows)
    assert {row.id for row in live} == {uuid.UUID(actual["id"])}
    assert len(pairs) == 1
    replacement, replaced = pairs[0]
    assert replacement.amount == Decimal("3120.50")
    assert replaced.amount == Decimal("3000.00")
    assert replaced.status is CostStatus.ESTIMATE

    comp = compute(db, org)
    assert set(comp.cost_rows) == {uuid.UUID(actual["id"])}
    assert len(comp.superseded) == 1


# ---------------------------------------------------------------------------- history and isolation


def test_replacing_and_closing_are_in_the_audit_log(client: TestClient, org: Organization) -> None:
    container_id, _ = seed(client)
    estimate = add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    other = add_cost(client, container_id, amount="275.00", status="ESTIMATE", cost_type="THC")
    actual = add_cost(client, container_id, amount="3120.50")
    client.patch(f"/api/v1/costs/{actual['id']}", json={"supersedes_cost_id": estimate["id"]})
    client.post(f"/api/v1/costs/{other['id']}/close", json={"reason": "never billed"})

    actions = [e["action"] for e in client.get("/api/v1/audit-log").json()["entries"]]
    assert "cost.superseded" in actions
    assert "cost.closed" in actions

    entry = client.get("/api/v1/audit-log", params={"action": "cost.superseded"}).json()["entries"][0]
    assert entry["before"]["supersedes_cost_id"] is None
    assert entry["after"]["supersedes_cost_id"] == estimate["id"]


def test_estimates_are_invisible_to_another_organization(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id, _ = seed(client)
    add_cost(client, container_id, amount="3000.00", status="ESTIMATE")
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert [c.status for c in db.scalars(select(Cost))] == [CostStatus.ESTIMATE]
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(Cost))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)


def test_existing_costs_are_actual_after_the_migration(client: TestClient, org: Organization) -> None:
    """The migration changes no data: everything already in the database was an invoice."""
    container_id, _ = seed(client)
    plain = client.post(
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
    assert plain.status_code == 201
    assert plain.json()["status"] == "ACTUAL"  # the default, for callers that know nothing of this
