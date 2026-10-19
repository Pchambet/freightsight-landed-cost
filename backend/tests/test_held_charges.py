"""A container whose charges are on file but held is not a container "with no charge".

A charge spread on value cannot be placed while one of the orders it reaches has no exchange rate:
the engine leaves it unallocated, and nothing reaches the box. Every landed figure leaves such a box
out, as it leaves out one with no charge at all — but says it apart, with the orders whose rate is
missing, so that nobody concludes the forwarder's invoice was never entered and enters it again.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domain.models import PurchaseOrder

LANDED_ON = datetime.now(UTC) - timedelta(days=3)
PERIOD = f"{LANDED_ON:%Y-%m}"
HELD = {"containers_held": 1, "held_orders": ["PO-VND"]}


def charge(box: str, cost_type: str, amount: str, status: str = "ACTUAL") -> dict[str, str]:
    return {"scope": "CONTAINER", "target_id": box, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": LANDED_ON.date().isoformat(), "status": status,
            "allocation_method": "BY_VALUE"}  # fmt: skip


def order(client: TestClient, number: str, currency: str, sku: str, quantity: str) -> dict[str, Any]:
    line = {"line_no": 1, "sku": sku, "quantity": quantity, "unit_price": "100"}
    body = {"po_number": number, "currency": currency, "supplier_name": "Acme", "order_date": "2026-09-01",
            "lines": [line]}  # fmt: skip
    res = client.post("/api/v1/purchase-orders", json=body)
    assert res.status_code == 201, res.text
    return dict(res.json())


def landed_box(client: TestClient, number: str, loads: list[tuple[dict[str, Any], str]]) -> str:
    box = client.post("/api/v1/containers", json={"container_number": number}).json()
    moved = {"discharged_at": LANDED_ON.isoformat(), "milestone": "DISCHARGED"}
    assert client.patch(f"/api/v1/containers/{box['id']}", json=moved).status_code == 200
    carried = [{"po_line_id": po["lines"][0]["id"], "quantity": quantity} for po, quantity in loads]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=carried).status_code == 200
    return str(box["id"])


@pytest.fixture
def held(client: TestClient, db: Session) -> dict[str, Any]:
    """One box with 18 tables of an order in euros and 10 of an order in dollars with no rate, and two
    invoiced charges spread on value (145 € and 380 €): neither can be placed. A second box with 5
    tables and nothing entered for it at all."""
    eur = order(client, "PO-EUR", "EUR", "TAB", "23")
    vnd = order(client, "PO-VND", "USD", "TAB", "10")
    box = landed_box(client, "CMAU4437293", [(eur, "18"), (vnd, "10")])
    bare = landed_box(client, "MSCU6715564", [(eur, "5")])
    for cost_type, amount in (("OCEAN_FREIGHT", "145.00"), ("THC", "380.00")):
        assert client.post("/api/v1/costs", json=charge(box, cost_type, amount)).status_code == 201
    missing = {"fx_rate": None, "fx_date": None, "fx_source": None}
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == UUID(vnd["id"])).values(**missing))
    db.flush()
    return {"box": box, "bare": bare, "eur": eur["id"], "vnd": vnd["id"]}


def held_part(body: dict[str, Any]) -> dict[str, Any]:
    return {"containers_held": body["containers_held"], "held_orders": body["held_orders"]}


def test_the_container_says_its_charges_are_held_and_counts_them_as_invoiced(
    client: TestClient, held: dict[str, Any]
) -> None:
    report = client.get(f"/api/v1/landed-costs/containers/{held['box']}").json()
    assert report["containers_without_charge"] == 1 and held_part(report) == HELD
    assert all(line["charges_held"] and line["held_orders"] == ["PO-VND"] for line in report["lines"])
    # Invoiced, not placed: the strip counts them, and knows which cost types were billed.
    assert report["totals"]["actual"] == "0.00" and report["totals"]["unallocated_actual"] == "525.00"
    assert report["totals"]["invoiced"] == "525.00"
    detail = report["by_cost_type_detail"]
    assert detail["OCEAN_FREIGHT"]["actual"] == "145.00" and detail["THC"]["actual"] == "380.00"

    bare = client.get(f"/api/v1/landed-costs/containers/{held['bare']}").json()
    assert bare["containers_without_charge"] == 1 and held_part(bare) == {
        "containers_held": 0,
        "held_orders": [],
    }
    assert not any(line["charges_held"] for line in bare["lines"])


def test_the_order_in_euros_names_the_order_that_holds_its_container(
    client: TestClient, held: dict[str, Any]
) -> None:
    report = client.get(f"/api/v1/landed-costs/purchase-orders/{held['eur']}").json()
    assert report["containers_without_charge"] == 2 and held_part(report) == HELD


def test_the_order_with_no_rate_counts_the_charges_its_rate_holds(
    client: TestClient, held: dict[str, Any]
) -> None:
    """Its container's invoices are on file, held by this very order: its page must not read "nothing
    invoiced" and invite a second deposit of the same invoices."""
    report = client.get(f"/api/v1/landed-costs/purchase-orders/{held['vnd']}").json()
    assert report["totals"]["unallocated_actual"] == "525.00" and report["totals"]["invoiced"] == "525.00"
    assert {w["code"] for w in report["warnings"]} == {"MISSING_FX_RATE"} and len(report["warnings"]) == 2
    assert report["by_cost_type_detail"]["THC"]["actual"] == "380.00"
    # The order in euros does not hold them: they are not its charges to settle.
    eur = client.get(f"/api/v1/landed-costs/purchase-orders/{held['eur']}").json()
    assert eur["warnings"] == [] and eur["totals"]["invoiced"] == "0.00"


def test_the_goods_left_out_are_given_at_their_known_value_with_the_orders_to_rate(
    client: TestClient, held: dict[str, Any]
) -> None:
    """2 300 € of tables in euros are known, the 10 in dollars are not: the known part is given, with
    the lines it lacks, never a dash for the whole."""
    unrated = [{"id": held["vnd"], "po_number": "PO-VND"}]
    current = client.get("/api/v1/reports/overview").json()["current"]
    assert (current["uncharged_fob"], current["uncharged_fob_missing_lines"]) == ("2300.00", 1)
    assert current["orders_without_fx_rate"] == unrated

    analysis = client.get("/api/v1/reports/landed-cost", params={"group_by": "supplier"}).json()
    totals = analysis["totals"]
    assert (totals["uncharged_fob"], totals["uncharged_fob_missing_lines"]) == ("2300.00", 1)
    assert (totals["charged_lines"], totals["charged_quantity"]) == (0, "0.0000")
    assert analysis["orders_without_fx_rate"] == unrated

    # What needs a person names the box and the order whose rate releases its charges.
    todo = client.get("/api/v1/reports/overview").json()["todo"]
    assert (todo["held_containers"], todo["held_orders"]) == (["CMAU4437293"], unrated)


def test_every_summary_tells_the_held_container_from_the_bare_one(
    client: TestClient, held: dict[str, Any]
) -> None:
    overview = client.get("/api/v1/reports/overview").json()
    assert overview["current"]["containers_without_charge"] == 2 and held_part(overview["current"]) == HELD
    assert overview["todo"]["containers_without_cost"] == 2 and overview["todo"]["containers_held"] == 1
    (sku,) = overview["skus"]
    assert sku["containers_without_charge"] == 2 and held_part(sku) == HELD

    analysis = client.get("/api/v1/reports/landed-cost", params={"group_by": "supplier"}).json()
    assert held_part(analysis["totals"]) == HELD and held_part(analysis["buckets"][0]) == HELD

    skus = client.get("/api/v1/reports/skus").json()["skus"]
    assert held_part(skus[0]) == HELD
    detail = client.get("/api/v1/reports/skus/detail", params={"sku": "TAB"}).json()
    boxes = {box["container_number"]: box for box in detail["containers"]}
    assert boxes["CMAU4437293"]["charges_held"] and not boxes["MSCU6715564"]["charges_held"]

    month = client.get(f"/api/v1/periods/{PERIOD}").json()
    assert held_part(month["summary"]) == HELD
    readiness = month["readiness"]
    assert readiness["containers_without_cost"] == 2 and held_part(readiness) == HELD


def test_the_landed_cost_file_tells_the_held_box_from_the_bare_one(
    client: TestClient, held: dict[str, Any]
) -> None:
    res = client.get("/api/v1/exports/landed-costs.csv", params={"locale": "en"})
    assert res.status_code == 200, res.text
    rows = [row for row in csv.DictReader(io.StringIO(res.text.lstrip("﻿"))) if row["row_type"] == "LINE"]
    flags = {(r["container_number"], r["without_charge"], r["charges_held"], r["held_orders"]) for r in rows}
    assert flags == {("CMAU4437293", "true", "true", "PO-VND"), ("MSCU6715564", "true", "false", "")}


def test_the_audit_counts_the_held_box_apart(client: TestClient, held: dict[str, Any]) -> None:
    day = LANDED_ON.date().isoformat()
    audit = client.get("/api/v1/reports/audit", params={"period_from": day, "period_to": day}).json()
    completeness = audit["completeness"]
    assert (completeness["containers_without_cost"], completeness["containers_held"]) == (2, 1)
    (month,) = audit["coefficient_by_month"]
    assert (month["containers_without_charge"], month["containers_held"]) == (2, 1)


def test_the_audit_margin_counts_the_held_box_apart(client: TestClient, held: dict[str, Any]) -> None:
    """The article is charged in a third box, so it has a margin row: the held box is not "no charge"."""
    po = order(client, "PO-EUR2", "EUR", "TAB", "4")
    box = landed_box(client, "TGHU7245081", [(po, "4")])
    assert client.post("/api/v1/costs", json=charge(box, "OCEAN_FREIGHT", "50.00")).status_code == 201
    day = LANDED_ON.date().isoformat()
    audit = client.get("/api/v1/reports/audit", params={"period_from": day, "period_to": day}).json()
    (margin,) = audit["margins"]
    assert (margin["containers_without_charge"], margin["containers_held"]) == (2, 1)


def test_the_landed_cost_file_keeps_each_holding_order_apart(client: TestClient, db: Session) -> None:
    """An order number may hold a space: two of them are never run together in one cell."""
    first, second = order(client, "PO 77", "USD", "TAB", "2"), order(client, "PO 78", "USD", "TAB", "2")
    box = landed_box(client, "TGHU7245081", [(first, "2"), (second, "2")])
    assert client.post("/api/v1/costs", json=charge(box, "OCEAN_FREIGHT", "90.00")).status_code == 201
    missing = {"fx_rate": None, "fx_date": None, "fx_source": None}
    ids = [UUID(first["id"]), UUID(second["id"])]
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id.in_(ids)).values(**missing))
    db.flush()
    res = client.get("/api/v1/exports/landed-costs.csv", params={"locale": "en"})
    rows = [row for row in csv.DictReader(io.StringIO(res.text.lstrip("﻿"))) if row["row_type"] == "LINE"]
    assert {r["held_orders"] for r in rows} == {"PO 77, PO 78"}
