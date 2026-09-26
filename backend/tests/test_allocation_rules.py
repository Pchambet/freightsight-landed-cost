"""The rules the engine applies to somebody's money, through the API that writes them down.

Duplicate manual splits, lines that receive nothing, duty spread on CIF for want of a tariff rate:
each of them used to change a landed cost without a word on any screen.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import Organization


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def order(client: TestClient, po_number: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    res = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": po_number, "currency": "EUR", "order_date": "2026-01-15", "lines": lines},
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def container(client: TestClient, number: str, shipment_id: str | None = None) -> str:
    res = client.post(
        "/api/v1/containers",
        json={"container_number": number, **({"shipment_id": shipment_id} if shipment_id else {})},
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def load(client: TestClient, container_id: str, loads: list[dict[str, str]]) -> None:
    res = client.put(f"/api/v1/containers/{container_id}/loads", json=loads)
    assert res.status_code == 200, res.text


def report(client: TestClient, container_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()
    return body


# ---------------------------------------------------------------------------- manual splits


def two_containers(client: TestClient) -> tuple[str, str, str]:
    shipment = client.post("/api/v1/shipments", json={"reference": "MEDUSH2604417"})
    assert shipment.status_code == 201, shipment.text
    shipment_id = str(shipment.json()["id"])
    po = order(
        client,
        "PO-A",
        [
            {"line_no": 1, "quantity": "600", "unit_price": "10.00"},
            {"line_no": 2, "quantity": "400", "unit_price": "10.00"},
        ],
    )
    first, second = (
        container(client, "MSCU4821990", shipment_id),
        container(client, "TGHU7245081", shipment_id),
    )
    load(client, first, [{"po_line_id": po["lines"][0]["id"], "quantity": "600"}])
    load(client, second, [{"po_line_id": po["lines"][1]["id"], "quantity": "400"}])
    return shipment_id, first, second


def manual_cost(shipment_id: str, splits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "scope": "SHIPMENT",
        "target_id": shipment_id,
        "cost_type": "OCEAN_FREIGHT",
        "amount": "1000.00",
        "currency": "EUR",
        "cost_date": "2026-02-10",
        "allocation_method": "MANUAL",
        "manual_splits": splits,
    }


def test_a_manual_split_naming_one_container_twice_is_refused(client: TestClient, org: Organization) -> None:
    """The slip is banal — two lines on one container because the wrong target type was picked — and
    it used to push twice the freight into the customer's own stock valuation."""
    shipment_id, first, _ = two_containers(client)
    refused = client.post(
        "/api/v1/costs",
        json=manual_cost(
            shipment_id,
            [
                {"target_type": "container", "target_id": first, "pct": "60.00"},
                {"target_type": "container", "target_id": first, "pct": "40.00"},
            ],
        ),
    )
    assert refused.status_code == 422
    assert refused.json()["code"] == "DUPLICATE_MANUAL_SPLIT"


def test_the_patch_refuses_the_duplicate_too(client: TestClient, org: Organization) -> None:
    shipment_id, first, second = two_containers(client)
    created = client.post(
        "/api/v1/costs",
        json=manual_cost(
            shipment_id,
            [
                {"target_type": "container", "target_id": first, "pct": "60.00"},
                {"target_type": "container", "target_id": second, "pct": "40.00"},
            ],
        ),
    )
    assert created.status_code == 201, created.text
    patched = client.patch(
        f"/api/v1/costs/{created.json()['id']}",
        json={
            "manual_splits": [
                {"target_type": "container", "target_id": second, "pct": "70.00"},
                {"target_type": "container", "target_id": second, "pct": "30.00"},
            ]
        },
    )
    assert patched.status_code == 422
    assert patched.json()["code"] == "DUPLICATE_MANUAL_SPLIT"
    # and the cost kept the split it had
    assert report(client, first)["totals"]["allocated"] == "600.00"


def test_every_allocated_cost_sums_back_to_its_piece(client: TestClient, org: Organization) -> None:
    """The global invariant, checked by the endpoint that exists to check it."""
    shipment_id, first, second = two_containers(client)
    assert (
        client.post(
            "/api/v1/costs",
            json=manual_cost(
                shipment_id,
                [
                    {"target_type": "container", "target_id": first, "pct": "55.55"},
                    {"target_type": "container", "target_id": second, "pct": "44.45"},
                ],
            ),
        ).status_code
        == 201
    )
    for payload in (
        {"cost_type": "THC", "amount": "275.00", "scope": "CONTAINER", "target_id": first},
        {"cost_type": "CUSTOMS_DUTY", "amount": "1234.57", "scope": "SHIPMENT", "target_id": shipment_id},
        {"cost_type": "IMPORT_VAT", "amount": "2000.01", "scope": "SHIPMENT", "target_id": shipment_id},
    ):
        res = client.post("/api/v1/costs", json={"currency": "EUR", "cost_date": "2026-02-10", **payload})
        assert res.status_code == 201, res.text

    integrity = client.get("/api/v1/reports/integrity").json()
    assert integrity["mismatches"] == []
    assert integrity["ok"] is True


# ---------------------------------------------------------------------------- lines that get nothing


def test_a_free_sample_that_receives_no_freight_is_named(client: TestClient, org: Organization) -> None:
    """ "What cannot be allocated is flagged, never guessed" has to cover the line that gets zero."""
    po = order(
        client,
        "PO-A",
        [
            {"line_no": 1, "sku": "TYRE-205", "quantity": "100", "unit_price": "10.00"},
            {"line_no": 2, "sku": "SAMPLE", "quantity": "2", "unit_price": "0.00"},
        ],
    )
    cnt = container(client, "MSCU4821990")
    load(
        client,
        cnt,
        [
            {"po_line_id": po["lines"][0]["id"], "quantity": "100"},
            {"po_line_id": po["lines"][1]["id"], "quantity": "2"},
        ],
    )
    created = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": cnt,
            "cost_type": "OCEAN_FREIGHT",
            "amount": "1000.00",
            "currency": "EUR",
            "cost_date": "2026-02-10",
        },
    )
    assert created.status_code == 201, created.text

    body = report(client, cnt)
    (note,) = body["notes"]
    assert note["code"] == "ZERO_BASIS_LINES"
    assert note["load_labels"] == ["PO-A #2 SAMPLE"]
    assert body["warnings"] == []  # the money is placed: this is not an unallocated amount
    assert body["totals"]["unallocated"] == "0.00"
    assert body["totals"]["allocated"] == "1000.00"


# ---------------------------------------------------------------------------- duty without a rate


def test_duty_spread_on_cif_names_the_lines_that_have_no_tariff_rate(
    client: TestClient, org: Organization
) -> None:
    """Nine lines at 12 % and one with no HS code: the tenth used to quietly pay duty it does not owe."""
    po = order(
        client,
        "PO-A",
        [
            {"line_no": 1, "sku": "TYRE-205", "quantity": "100", "unit_price": "10.00", "duty_rate": "0.12"},
            {"line_no": 2, "sku": "NEW-REF", "quantity": "100", "unit_price": "10.00"},
        ],
    )
    cnt = container(client, "MSCU4821990")
    load(
        client,
        cnt,
        [
            {"po_line_id": po["lines"][0]["id"], "quantity": "100"},
            {"po_line_id": po["lines"][1]["id"], "quantity": "100"},
        ],
    )
    created = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": cnt,
            "cost_type": "CUSTOMS_DUTY",
            "amount": "240.00",
            "currency": "EUR",
            "cost_date": "2026-02-10",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["allocation_method"] == "BY_CIF_VALUE"  # the fallback, as before

    body = report(client, cnt)
    (note,) = body["notes"]
    assert note["code"] == "DUTY_RATE_PARTIAL"
    assert note["load_labels"] == ["PO-A #2 NEW-REF"]
    assert sum(Decimal(line["by_cost_type"]["CUSTOMS_DUTY"]) for line in body["lines"]) == Decimal("240.00")


def test_a_tariff_rate_on_every_line_is_the_quiet_case(client: TestClient, org: Organization) -> None:
    po = order(
        client,
        "PO-A",
        [{"line_no": 1, "quantity": "100", "unit_price": "10.00", "duty_rate": "0.12"}],
    )
    cnt = container(client, "MSCU4821990")
    load(client, cnt, [{"po_line_id": po["lines"][0]["id"], "quantity": "100"}])
    created = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": cnt,
            "cost_type": "CUSTOMS_DUTY",
            "amount": "120.00",
            "currency": "EUR",
            "cost_date": "2026-02-10",
        },
    )
    assert created.json()["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert report(client, cnt)["notes"] == []
