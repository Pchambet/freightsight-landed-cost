"""Rate cards, and the estimates they produce."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Organization, RateCard


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def seed_container(client: TestClient, *, weight: str = "2.0000", hs_code: str | None = None) -> str:
    """One container, one PO line of 1000 units at 10.00 with a known weight."""
    line: dict[str, Any] = {
        "line_no": 1,
        "quantity": "1000",
        "unit_price": "10.00",
        "unit_weight_kg": weight,
        "unit_volume_cbm": "0.010000",
    }
    if hs_code:
        line["hs_code"] = hs_code
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": "PO-A", "currency": "EUR", "lines": [line]},
    )
    assert po.status_code == 201, po.text
    line_id = po.json()["lines"][0]["id"]
    container = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    container_id = str(container.json()["id"])
    loads = client.put(
        f"/api/v1/containers/{container_id}/loads", json=[{"po_line_id": line_id, "quantity": "1000"}]
    )
    assert loads.status_code == 200, loads.text
    return container_id


def add_card(client: TestClient, **card: Any) -> dict[str, Any]:
    res = client.post("/api/v1/organization/rate-cards", json={"currency": "EUR", **card})
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def estimates(client: TestClient, container_id: str) -> dict[str, Any]:
    res = client.post(f"/api/v1/containers/{container_id}/estimates")
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


# ---------------------------------------------------------------------------- the cards themselves


def test_a_card_is_unique_per_cost_type_and_scope(client: TestClient, org: Organization) -> None:
    add_card(client, cost_type="THC", amount="275.00")
    res = client.post(
        "/api/v1/organization/rate-cards",
        json={"cost_type": "THC", "amount": "300.00", "currency": "EUR"},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "RATE_CARD_EXISTS"
    # a different scope is a different card
    add_card(client, cost_type="THC", scope="SHIPMENT", amount="250.00")


def test_cards_can_be_edited_and_removed(client: TestClient, org: Organization) -> None:
    card = add_card(client, cost_type="DRAYAGE", amount="425.00")
    res = client.patch(f"/api/v1/organization/rate-cards/{card['id']}", json={"amount": "450.0000"})
    assert res.status_code == 200 and res.json()["amount"] == "450.0000"
    assert client.delete(f"/api/v1/organization/rate-cards/{card['id']}").status_code == 204
    assert client.get("/api/v1/organization/rate-cards").json() == []


# ---------------------------------------------------------------------------- applying them


def test_each_basis_computes_what_the_price_list_says(client: TestClient, org: Organization) -> None:
    container_id = seed_container(client)  # 1000 units, 10.00 each, 2 kg each, 0.01 cbm each
    add_card(client, cost_type="THC", amount="275.00")  # flat
    add_card(client, cost_type="DRAYAGE", basis="PER_100KG", amount="1.5000")  # 2000 kg
    add_card(client, cost_type="WAREHOUSING", basis="PER_CBM", amount="12.0000")  # 10 cbm
    add_card(client, cost_type="INSURANCE", basis="PCT_OF_FOB", amount="0.3500")  # 10 000 FOB

    created = {c["cost_type"]: c for c in estimates(client, container_id)["created"]}
    assert created["THC"]["amount"] == "275.00"
    assert created["DRAYAGE"]["amount"] == "30.00"  # 2000 kg / 100 x 1.50
    assert created["WAREHOUSING"]["amount"] == "120.00"  # 10 cbm x 12
    assert created["INSURANCE"]["amount"] == "35.00"  # 0.35 % of 10 000
    assert all(c["status"] == "ESTIMATE" for c in created.values())


def test_a_basis_nobody_entered_produces_nothing(client: TestClient, org: Organization) -> None:
    """The difference between an estimate and a number: no weights, no weight-based estimate."""
    container_id = seed_container(client, weight="0")
    add_card(client, cost_type="DRAYAGE", basis="PER_100KG", amount="1.5000")

    result = estimates(client, container_id)
    assert result["created"] == []
    assert "DRAYAGE" in result["skipped"]
    assert "PER_100KG" in result["skipped"]["DRAYAGE"]


def test_running_it_twice_changes_nothing(client: TestClient, org: Organization) -> None:
    container_id = seed_container(client)
    add_card(client, cost_type="THC", amount="275.00")

    first = estimates(client, container_id)
    assert len(first["created"]) == 1
    second = estimates(client, container_id)
    assert second["created"] == []
    assert second["skipped"]["THC"] == "a cost of this type already exists"
    assert len(client.get("/api/v1/costs").json()) == 1


def test_an_invoice_already_there_stops_the_estimate(client: TestClient, org: Organization) -> None:
    container_id = seed_container(client)
    add_card(client, cost_type="THC", amount="275.00")
    invoiced = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": "THC",
            "amount": "289.40",
            "currency": "EUR",
            "cost_date": "2026-03-12",
        },
    )
    assert invoiced.status_code == 201

    result = estimates(client, container_id)
    assert result["created"] == []
    assert result["skipped"]["THC"] == "a cost of this type already exists"


def test_the_estimates_reach_the_landed_cost(client: TestClient, org: Organization) -> None:
    container_id = seed_container(client)
    add_card(client, cost_type="THC", amount="275.00")
    estimates(client, container_id)
    report = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()
    assert report["totals"]["allocated"] == "275.00"
    assert report["totals"]["landed"] == "10275.00"


# ---------------------------------------------------------------------------- theoretical duty


def test_duty_is_computed_on_the_customs_value(client: TestClient, org: Organization) -> None:
    """FOB 10 000 plus 3 000 of freight, at 4.5 %: the duty follows the CIF value, not the FOB."""
    container_id = seed_container(client)
    client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": "OCEAN_FREIGHT",
            "amount": "3000.00",
            "currency": "EUR",
            "cost_date": "2026-03-12",
        },
    )
    lines = client.get("/api/v1/purchase-orders").json()
    po_id = lines[0]["id"]
    po = client.get(f"/api/v1/purchase-orders/{po_id}").json()
    line_id = po["lines"][0]["id"]
    assert (
        client.patch(
            f"/api/v1/purchase-orders/{po_id}",
            json={
                "lines": [
                    {
                        "id": line_id,
                        "line_no": 1,
                        "quantity": "1000",
                        "unit_price": "10.00",
                        "duty_rate": "0.0450",
                    }
                ]
            },
        ).status_code
        == 200
    )

    created = {c["cost_type"]: c for c in estimates(client, container_id)["created"]}
    assert created["CUSTOMS_DUTY"]["amount"] == "585.00"  # 4.5 % of 13 000
    # every loaded line has its rate and they explain the duty: spread by theoretical duty, the
    # same method the invoice that replaces this estimate will get
    assert created["CUSTOMS_DUTY"]["allocation_method"] == "BY_THEORETICAL_DUTY"


def test_a_tariff_card_supplies_the_rate_by_heading(client: TestClient, org: Organization) -> None:
    """Tariff headings are hierarchical, so the most specific card wins — as a customs schedule reads."""
    container_id = seed_container(client, hs_code="40111000")
    add_card(client, cost_type="CUSTOMS_DUTY", amount="0.0250", hs_code="4011")
    add_card(client, cost_type="CUSTOMS_DUTY", scope="SHIPMENT", amount="0.0450", hs_code="401110")

    created = {c["cost_type"]: c for c in estimates(client, container_id)["created"]}
    assert created["CUSTOMS_DUTY"]["amount"] == "450.00"  # 4.5 % of the 10 000 FOB, no freight yet


def test_the_freight_estimated_in_the_same_call_is_in_the_customs_value(
    client: TestClient, org: Organization
) -> None:
    """A freight card and a tariff rate, estimated together: the duty is taken on the value the freight
    just estimated makes — 4.5 % of 13 000, not of the goods alone."""
    container_id = seed_container(client, hs_code="40111000")
    add_card(client, cost_type="CUSTOMS_DUTY", amount="0.0450", hs_code="4011")
    add_card(client, cost_type="OCEAN_FREIGHT", amount="3000.00")

    created = {c["cost_type"]: c for c in estimates(client, container_id)["created"]}
    assert (created["OCEAN_FREIGHT"]["amount"], created["CUSTOMS_DUTY"]["amount"]) == ("3000.00", "585.00")


def test_a_bill_of_lading_card_waits_for_the_box_to_travel_under_one(
    client: TestClient, org: Organization
) -> None:
    """A card priced per bill of lading, on a box on none: the estimate had nowhere to go."""
    container_id = seed_container(client)
    add_card(client, cost_type="DRAYAGE", scope="SHIPMENT", amount="450.00")
    result = estimates(client, container_id)
    assert result["created"] == []
    assert result["skipped"]["DRAYAGE"] == "a SHIPMENT rate card needs the container to be on a shipment"


def test_a_card_priced_per_box_does_not_add_the_freight_its_bill_of_lading_carries(
    client: TestClient, org: Organization
) -> None:
    """The freight invoiced per bill of lading is the box's freight too: a card priced per box would
    have estimated it a second time. Skipped, and said, since another charge of that type is possible."""
    container_id = seed_container(client)
    voyage = client.post("/api/v1/shipments", json={"reference": "BL-1"}).json()
    assert client.patch(f"/api/v1/containers/{container_id}", json={"shipment_id": voyage["id"]}).is_success
    freight = {"scope": "SHIPMENT", "target_id": voyage["id"], "cost_type": "OCEAN_FREIGHT",
               "amount": "3000.00", "currency": "EUR", "cost_date": "2026-03-12"}  # fmt: skip
    assert client.post("/api/v1/costs", json=freight).status_code == 201
    add_card(client, cost_type="OCEAN_FREIGHT", amount="3700.00")
    result = estimates(client, container_id)
    assert result["created"] == []
    assert result["skipped"]["OCEAN_FREIGHT"] == "a cost of this type is already on the container's shipment"


def test_the_duty_estimate_takes_each_line_s_customs_value_as_the_engine_spreads_the_freight(
    client: TestClient, org: Organization
) -> None:
    """Freight spread by weight gives the heavy line three quarters of it: the light line's customs
    value is 1 000 + 250, and its 10 % duty 125 € — not 150 €, as a share of the FOB would have it."""
    lines = [
        {"line_no": 1, "sku": "LIGHT", "quantity": "100", "unit_price": "10", "unit_weight_kg": "1",
         "duty_rate": "0.10"},
        {"line_no": 2, "sku": "HEAVY", "quantity": "100", "unit_price": "10", "unit_weight_kg": "3",
         "duty_rate": "0"},
    ]  # fmt: skip
    po = client.post("/api/v1/purchase-orders", json={"po_number": "PO-W", "currency": "EUR", "lines": lines})
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()["id"]
    loads = [{"po_line_id": line["id"], "quantity": "100"} for line in po.json()["lines"]]
    assert client.put(f"/api/v1/containers/{box}/loads", json=loads).status_code == 200
    freight = {"scope": "CONTAINER", "target_id": box, "cost_type": "OCEAN_FREIGHT", "amount": "1000.00",
               "currency": "EUR", "cost_date": "2026-03-12", "allocation_method": "BY_WEIGHT"}  # fmt: skip
    assert client.post("/api/v1/costs", json=freight).status_code == 201
    created = {c["cost_type"]: c for c in estimates(client, box)["created"]}
    assert created["CUSTOMS_DUTY"]["amount"] == "125.00"


def test_no_rate_anywhere_says_so_instead_of_inventing_one(client: TestClient, org: Organization) -> None:
    container_id = seed_container(client)
    result = estimates(client, container_id)
    assert result["created"] == []
    assert result["skipped"]["CUSTOMS_DUTY"] == "no tariff rate on these lines"


# ---------------------------------------------------------------------------- isolation


def test_rate_cards_are_invisible_to_another_organization(
    client: TestClient, db: Session, org: Organization
) -> None:
    add_card(client, cost_type="THC", amount="275.00")
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert [c.amount for c in db.scalars(select(RateCard))] == [Decimal("275.0000")]
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(RateCard))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)
