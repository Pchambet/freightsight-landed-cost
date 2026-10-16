"""What a landed figure rests on: the part still estimated, and the containers it leaves out.

A container with goods and no charge at all — no freight, no duty, not even an estimate — would only
add its FOB as a "landed cost". Every landed figure leaves it out (its goods stay in the FOB, a fact)
and says how many it left out: the overview, the landed-cost analysis, the articles and their last
arrival, the audit and the cost-price export. Each also says how much of its landed cost still rests
on estimates, import VAT excluded, by the audit's own rule.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import SkuCostHistory

TODAY = datetime.now(UTC)
# Both boxes in one month whatever the day the suite runs: the 5th and the 20th of last month.
LAST_MONTH = TODAY.replace(day=1) - timedelta(days=1)
FIRST, SECOND = LAST_MONTH.replace(day=5), LAST_MONTH.replace(day=20)
MONTH = f"{FIRST:%Y-%m}"
PERIOD = {"period_from": f"{MONTH}-01", "period_to": TODAY.date().isoformat()}


def cost(box: str, cost_type: str, amount: str, status: str = "ACTUAL") -> dict[str, str]:
    return {"scope": "CONTAINER", "target_id": box, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": FIRST.date().isoformat(), "status": status,
            "allocation_method": "BY_QUANTITY"}  # fmt: skip


@pytest.fixture
def boxes(client: TestClient) -> dict[str, Any]:
    """Article A (Acme, 100 € each, sold 200 €): 10 in a first box charged 300 € of freight and 100 €
    of brokerage still estimated (and 50 € of estimated import VAT); 10 more, with 5 of article B
    (Lyon, 50 €), in a second box that landed later and carries no charge at all."""
    orders = {}
    for number, supplier, sku, quantity, price in (
        ("PO-1", "Acme", "A", "20", "100"),
        ("PO-2", "Lyon", "B", "5", "50"),
    ):
        line = {"line_no": 1, "sku": sku, "quantity": quantity, "unit_price": price}
        body = {"po_number": number, "currency": "EUR", "supplier_name": supplier,
                "order_date": "2026-09-01", "lines": [line]}  # fmt: skip
        res = client.post("/api/v1/purchase-orders", json=body)
        assert res.status_code == 201, res.text
        orders[sku] = res.json()["lines"][0]["id"]
    assert client.post("/api/v1/products", json={"sku": "A", "sale_price": "200"}).status_code == 201
    found = {}
    for number, landed_on, loads in (
        ("MSCU1111111", FIRST, [("A", "10")]),
        ("MSCU2222222", SECOND, [("A", "10"), ("B", "5")]),
    ):
        box = client.post("/api/v1/containers", json={"container_number": number}).json()
        moved = {"discharged_at": landed_on.isoformat(), "milestone": "DISCHARGED"}
        assert client.patch(f"/api/v1/containers/{box['id']}", json=moved).status_code == 200
        carried = [{"po_line_id": orders[sku], "quantity": quantity} for sku, quantity in loads]
        assert client.put(f"/api/v1/containers/{box['id']}/loads", json=carried).status_code == 200
        found[number] = box["id"]
    first = found["MSCU1111111"]
    for charge in (
        cost(first, "OCEAN_FREIGHT", "300.00"),
        cost(first, "CUSTOMS_BROKERAGE", "100.00", "ESTIMATE"),
        cost(first, "IMPORT_VAT", "50.00", "ESTIMATE"),
    ):
        assert client.post("/api/v1/costs", json=charge).status_code == 201
    return {"charged": first, "bare": found["MSCU2222222"]}


def basis(body: dict[str, Any]) -> tuple[Any, ...]:
    return (
        body["fob"],
        body["landed"],
        body["coefficient"],
        body["estimated"],
        body["containers_without_charge"],
    )


def test_the_overview_leaves_the_bare_container_out_of_landed_and_says_what_is_estimated(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    body = client.get("/api/v1/reports/overview", params=PERIOD).json()
    current = body["current"]
    # 2 250 € of goods, of which the 1 000 € in the charged box landed at 1 400 €: 1.40, not 1.18.
    assert basis(current) == ("2250.00", "1400.00", "1.4000", "100.00", 1)
    assert current["charged_fob"] == "1000.00" and current["containers"] == 2
    assert sum(b["containers_without_charge"] for b in body["by_month"]) == 1
    skus = {s["sku"]: s for s in body["skus"]}
    assert (skus["A"]["landed"], skus["A"]["unit_landed_cost"], skus["A"]["coefficient"]) == (
        "1400.00",
        "140.0000",
        "1.4000",
    )
    assert skus["A"]["estimated"] == "100.00" and skus["A"]["containers_without_charge"] == 1
    assert (skus["B"]["landed"], skus["B"]["unit_landed_cost"], skus["B"]["coefficient"]) == (
        None,
        None,
        None,
    )
    assert skus["B"]["fob"] == "250.00" and skus["B"]["containers_without_charge"] == 1


def test_the_overview_estimate_is_the_audit_completeness_estimate(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    overview = client.get("/api/v1/reports/overview", params=PERIOD).json()["current"]
    audit = client.get("/api/v1/reports/audit", params=PERIOD).json()
    assert overview["estimated"] == audit["completeness"]["estimated"] == "100.00"
    assert overview["landed"] == audit["completeness"]["landed"]
    assert overview["coefficient"] == audit["coefficient"]
    assert audit["completeness"]["containers_without_cost"] == 1
    (month,) = audit["coefficient_by_month"]
    assert (month["fob"], month["landed"], month["coefficient"]) == ("1000.00", "1400.00", "1.4000")
    assert month["containers_without_charge"] == 1
    # Article A measured on what the charged box brought in; B, never charged, has nothing to measure.
    (margin,) = audit["margins"]
    assert (margin["sku"], margin["quantity"], margin["approach_costs"]) == ("A", "10.0000", "400.00")
    assert (margin["margin_on_fob_pct"], margin["margin_real_pct"]) == ("50.0", "30.0")


def test_the_landed_cost_analysis_leaves_the_bare_container_out_of_every_landed_figure(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    body = client.get("/api/v1/reports/landed-cost", params={**PERIOD, "group_by": "supplier"}).json()
    totals = body["totals"]
    assert basis(totals) == ("2250.00", "1400.00", "1.4000", "100.00", 1)
    assert totals["freight_share_pct"] == "30.00"  # 300 € of freight on the 1 000 € it was charged on
    buckets = {b["label"]: b for b in body["buckets"]}
    assert basis(buckets["Acme"]) == ("2000.00", "1400.00", "1.4000", "100.00", 1)
    # Nothing of Lyon's was charged: no landed figure at all, rather than 0 or its FOB.
    assert basis(buckets["Lyon"]) == ("250.00", None, None, "0.00", 1)
    assert buckets["Lyon"]["fob_missing"] is None
    (a, b) = body["skus"]
    assert (a["sku"], a["quantity"], a["unit_landed_cost"], a["containers_without_charge"]) == (
        "A",
        "20.0000",
        "140.0000",
        1,
    )
    assert (b["sku"], b["landed"], b["unit_landed_cost"]) == ("B", None, None)

    by_type = client.get("/api/v1/reports/landed-cost", params={**PERIOD, "group_by": "cost_type"}).json()
    rows = {b["key"]: b for b in by_type["buckets"]}
    assert rows["CUSTOMS_BROKERAGE"]["estimated"] == "100.00" and rows["OCEAN_FREIGHT"]["estimated"] == "0.00"
    assert rows["IMPORT_VAT"]["estimated"] == "0.00"  # not a landed cost, so never "still estimated" either
    assert all(row["coefficient"] is None for row in rows.values())


def test_an_article_averages_and_compares_only_its_charged_containers(
    client: TestClient, db: Session, boxes: dict[str, Any]
) -> None:
    skus = {s["sku"]: s for s in client.get("/api/v1/reports/skus").json()["skus"]}
    a = skus["A"]
    assert (a["quantity"], a["fob"], a["unit_fob"]) == ("20.0000", "2000.00", "100.0000")
    assert (a["landed"], a["unit_landed"], a["coefficient"]) == ("1400.00", "140.0000", "1.4000")
    assert (a["margin_unit"], a["margin_pct"]) == ("60.0000", "30.0")
    # The bare box landed last, but has no landed cost: the last arrival compared is the charged one.
    assert (a["last_unit_landed"], a["last_arrival_on"]) == ("140.0000", FIRST.date().isoformat())
    assert a["change_pct"] is None and a["containers_without_charge"] == 1
    assert skus["B"]["unit_landed"] is None and skus["B"]["containers_without_charge"] == 1

    detail = client.get("/api/v1/reports/skus/detail", params={"sku": "A"}).json()
    bare = next(box for box in detail["containers"] if box["container_number"] == "MSCU2222222")
    assert (bare["without_charge"], bare["landed"], bare["unit_landed"], bare["fob"]) == (
        True,
        None,
        None,
        "1000.00",
    )
    arrivals = {p["container_number"]: p for p in detail["arrivals"]}
    assert arrivals["MSCU2222222"]["without_charge"] and arrivals["MSCU2222222"]["unit_landed"] is None
    assert not arrivals["MSCU1111111"]["without_charge"]

    history = {row.sku: row for row in db.scalars(select(SkuCostHistory))}
    assert str(history["A"].unit_landed_cost) == "140.0000" and "B" not in history


def test_the_container_report_marks_the_lines_of_a_bare_container(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    bare = client.get(f"/api/v1/landed-costs/containers/{boxes['bare']}").json()
    assert bare["lines"] and all(line["without_charge"] for line in bare["lines"])
    charged = client.get(f"/api/v1/landed-costs/containers/{boxes['charged']}").json()
    assert not any(line["without_charge"] for line in charged["lines"])


def test_the_cost_price_export_takes_no_cost_price_from_a_bare_container(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    res = client.get("/api/v1/exports/sku-costs.csv", params={"basis": "last"})
    assert res.status_code == 200, res.text
    rows = list(csv.reader(io.StringIO(res.text.lstrip("﻿")), delimiter=";"))
    by_sku = {row[0]: row for row in rows[1:]}
    assert "B" not in by_sku  # its only box has no charge
    assert by_sku["A"][2] == "140,0000" and by_sku["A"][7] == "MSCU1111111"


def test_a_charge_on_the_bare_container_brings_it_back_into_every_figure(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    assert (
        client.post("/api/v1/costs", json=cost(boxes["bare"], "OCEAN_FREIGHT", "125.00")).status_code == 201
    )
    current = client.get("/api/v1/reports/overview", params=PERIOD).json()["current"]
    assert basis(current) == ("2250.00", "2775.00", "1.2333", "100.00", 0)
    assert current["charged_fob"] == current["fob"]


def test_an_article_never_charged_is_neither_priced_nor_unpriced_in_the_audit_or_its_preparation(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    assert client.post("/api/v1/products", json={"sku": "B", "sale_price": "90"}).status_code == 201
    audit = client.get("/api/v1/reports/audit", params=PERIOD).json()
    assert (audit["headline"]["priced_skus"], audit["headline"]["unpriced_skus"]) == (1, 0)
    assert audit["headline"]["margin_overstated"] == "400.00"
    preparation = client.get("/api/v1/reports/audit/preparation", params=PERIOD).json()
    assert "NO_SALE_PRICE" not in {item["code"] for item in preparation["items"]}


def test_closing_the_month_leaves_its_cost_price_export_as_it_was(
    client: TestClient, boxes: dict[str, Any]
) -> None:
    def prices() -> dict[str, tuple[str, str, str]]:
        res = client.get("/api/v1/exports/sku-costs.csv", params={"period": MONTH, "locale": "en"})
        assert res.status_code == 200, res.text
        rows = csv.DictReader(io.StringIO(res.text.lstrip("\ufeff")))
        return {r["sku"]: (r["unit_landed_cost"], r["quantity"], r["containers"]) for r in rows}

    live = prices()
    assert live == {"A": ("140.0000", "10.0000", "MSCU1111111")}
    closed = client.post(f"/api/v1/periods/{MONTH}/close")
    assert closed.status_code == 201, closed.text
    assert prices() == live  # the frozen month takes no cost price from the bare box either
