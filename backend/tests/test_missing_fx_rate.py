"""An order with no exchange rate has no value in the books' currency, and no figure counts it as 0.

An import that cannot find a rate leaves the order's rate missing (migration 0031). Its lines then have
no FOB, and so no landed cost: every total, unit cost, coefficient and margin that would include them
is null, with `fob_missing` (the code) and `fob_missing_lines` (how many lines) beside it, while the
costs placed on those lines are still counted. A month holding such an order is not closed, and a rate
set by hand on the order makes every figure whole again.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.models import PurchaseOrder, SkuCostHistory

LANDED_ON = datetime.now(UTC) - timedelta(days=3)
PERIOD = f"{LANDED_ON:%Y-%m}"
MISSING = {"fob_missing": "FX_RATE_MISSING", "fob_missing_lines": 1}
WHOLE = {"fob_missing": None, "fob_missing_lines": 0}


def charge(box: str, cost_type: str, amount: str) -> dict[str, str]:
    return {"scope": "CONTAINER", "target_id": box, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": LANDED_ON.date().isoformat(),
            "allocation_method": "BY_QUANTITY"}  # fmt: skip


@pytest.fixture
def no_rate(client: TestClient, db: Session) -> dict[str, Any]:
    """One box: 10 x 100 $ from a supplier whose order has no rate, 10 x 100 € from another, 400 € of
    freight split by quantity, and both articles sold 200 €."""
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    moved = {"discharged_at": LANDED_ON.isoformat(), "milestone": "DISCHARGED"}
    assert client.patch(f"/api/v1/containers/{box['id']}", json=moved).status_code == 200
    orders: dict[str, dict[str, Any]] = {}
    for number, currency, supplier, sku in (("PO-USD", "USD", "Acme", "A"), ("PO-EUR", "EUR", "Lyon", "B")):
        line = {"line_no": 1, "sku": sku, "quantity": "10", "unit_price": "100"}
        body = {"po_number": number, "currency": currency, "supplier_name": supplier,
                "order_date": "2026-09-01", "lines": [line]}  # fmt: skip
        res = client.post("/api/v1/purchase-orders", json=body)
        assert res.status_code == 201, res.text
        orders[currency] = res.json()
        assert client.post("/api/v1/products", json={"sku": sku, "sale_price": "200"}).status_code == 201
    loads = [{"po_line_id": order["lines"][0]["id"], "quantity": "10"} for order in orders.values()]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
    assert client.post("/api/v1/costs", json=charge(box["id"], "OCEAN_FREIGHT", "400.00")).status_code == 201
    # What an import leaves when no rate can be had: the rate missing, never 1.
    usd = UUID(orders["USD"]["id"])
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == usd).values(fx_rate=None, fx_date=None))
    db.flush()
    return {"box": box["id"], "usd": orders["USD"]["id"]}


def gap(body: dict[str, Any]) -> dict[str, Any]:
    return {"fob_missing": body["fob_missing"], "fob_missing_lines": body["fob_missing_lines"]}


def read_csv(res: Any) -> list[dict[str, str]]:
    assert res.status_code == 200, res.text
    return list(csv.DictReader(io.StringIO(res.text.lstrip("﻿"))))


def test_the_container_report_leaves_the_line_without_value_out_of_no_total(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    report = client.get(f"/api/v1/landed-costs/containers/{no_rate['box']}").json()
    lines = {ln["po_number"]: ln for ln in report["lines"]}
    usd, eur = lines["PO-USD"], lines["PO-EUR"]
    assert (usd["fob"], usd["landed"], usd["unit_landed_cost"], usd["fob_missing"]) == (
        None,
        None,
        None,
        "FX_RATE_MISSING",
    )
    assert usd["allocated"] == "200.00"  # the freight it travelled with is a known cost
    assert (eur["fob"], eur["landed"], eur["fob_missing"]) == ("1000.00", "1200.00", None)
    assert report["totals"]["fob"] is None and report["totals"]["landed"] is None
    assert report["totals"]["allocated"] == "400.00"
    assert gap(report) == MISSING


def test_the_overview_gives_no_coefficient_built_on_a_missing_value(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    body = client.get("/api/v1/reports/overview").json()
    current = body["current"]
    assert (current["fob"], current["landed"], current["coefficient"]) == (None, None, None)
    assert gap(current) == MISSING
    (month,) = body["by_month"]
    assert month["fob"] is None and month["freight_share_pct"] is None and gap(month) == MISSING
    skus = {s["sku"]: s for s in body["skus"]}
    assert skus["A"]["landed"] is None and gap(skus["A"]) == MISSING
    assert skus["B"]["landed"] == "1200.00" and gap(skus["B"]) == WHOLE


def test_the_landed_cost_analysis_says_which_bucket_lacks_a_value(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    body = client.get("/api/v1/reports/landed-cost", params={"group_by": "supplier"}).json()
    buckets = {b["label"]: b for b in body["buckets"]}
    assert buckets["Acme"]["fob"] is None and buckets["Acme"]["landed"] is None
    assert buckets["Acme"]["allocated"] == "200.00" and gap(buckets["Acme"]) == MISSING
    assert (buckets["Lyon"]["fob"], buckets["Lyon"]["landed"]) == ("1000.00", "1200.00")
    assert gap(buckets["Lyon"]) == WHOLE
    assert body["totals"]["fob"] is None and gap(body["totals"]) == MISSING
    by_type = client.get("/api/v1/reports/landed-cost", params={"group_by": "cost_type"}).json()
    (freight,) = by_type["buckets"]
    assert freight["allocated"] == "400.00" and freight["fob"] is None and gap(freight) == MISSING


def test_an_article_without_value_has_no_unit_cost_and_no_margin(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    skus = {s["sku"]: s for s in client.get("/api/v1/reports/skus").json()["skus"]}
    a, b = skus["A"], skus["B"]
    for field in (
        "fob",
        "landed",
        "unit_fob",
        "unit_landed",
        "last_unit_landed",
        "margin_unit",
        "margin_pct",
    ):
        assert a[field] is None, field
    assert a["quantity"] == "10.0000" and gap(a) == MISSING
    assert (b["unit_landed"], b["margin_unit"], b["margin_pct"]) == ("120.0000", "80.0000", "40.0")
    assert gap(b) == WHOLE

    detail = client.get("/api/v1/reports/skus/detail", params={"sku": "A"}).json()
    (box,) = detail["containers"]
    (arrival,) = detail["arrivals"]
    assert box["unit_landed"] is None and gap(box) == MISSING
    assert (
        arrival["fob"] is None and arrival["landed"] is None and arrival["fob_missing"] == "FX_RATE_MISSING"
    )


def test_the_container_list_says_its_goods_value_is_incomplete(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    (box,) = client.get("/api/v1/containers").json()
    assert box["fob_base"] is None and gap(box) == MISSING
    assert box["allocated_base"] == "400.00"


def test_the_exports_leave_the_cells_empty_and_name_the_reason(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    rows = read_csv(client.get("/api/v1/exports/landed-costs.csv", params={"locale": "en"}))
    usd = next(r for r in rows if r["po_number"] == "PO-USD")
    assert (usd["fob"], usd["landed"], usd["unit_landed_cost"]) == ("", "", "")
    assert usd["fob_missing"] == "FX_RATE_MISSING" and usd["allocated"] == "200.00"
    eur = next(r for r in rows if r["po_number"] == "PO-EUR")
    assert (eur["fob"], eur["landed"], eur["fob_missing"]) == ("1000.00", "1200.00", "")

    prices = {
        r["sku"]: r for r in read_csv(client.get("/api/v1/exports/sku-costs.csv", params={"locale": "en"}))
    }
    assert prices["A"]["unit_landed_cost"] == "" and prices["A"]["fob_missing"] == "FX_RATE_MISSING"
    assert prices["B"]["unit_landed_cost"] == "120.0000" and prices["B"]["fob_missing"] == ""


def test_a_month_holding_an_order_without_rate_is_not_closed(
    client: TestClient, db: Session, no_rate: dict[str, Any]
) -> None:
    (month,) = client.get("/api/v1/periods").json()["periods"]
    assert (month["fob"], month["landed"], month["coefficient"]) == (None, None, None)
    assert gap(month) == MISSING
    readiness = client.get(f"/api/v1/periods/{PERIOD}").json()["readiness"]
    assert readiness["orders_without_fx_rate"] == [{"id": no_rate["usd"], "po_number": "PO-USD"}]

    res = client.post(f"/api/v1/periods/{PERIOD}/close")
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "PERIOD_ORDERS_WITHOUT_FX_RATE"
    assert res.json()["params"] == {"orders": "PO-USD"}
    assert client.get(f"/api/v1/periods/{PERIOD}").json()["summary"]["status"] == "open"


def test_the_audit_gives_no_margin_or_coefficient_built_on_a_missing_value(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    dates = {"period_from": (LANDED_ON - timedelta(days=10)).date().isoformat(),
             "period_to": LANDED_ON.date().isoformat()}  # fmt: skip
    audit = client.get("/api/v1/reports/audit", params=dates).json()
    assert audit["coefficient"] is None and audit["completeness"]["landed"] is None
    margins = {m["sku"]: m for m in audit["margins"]}
    assert margins["A"]["landed"] is None and margins["A"]["approach_costs"] is None
    assert margins["A"]["margin_real_pct"] is None
    assert margins["B"]["approach_costs"] == "200.00"
    # Article A is priced, and its overstated margin cannot be said: the headline does not pretend.
    assert audit["headline"]["margin_overstated"] is None


def test_a_rate_set_by_hand_makes_every_figure_whole(
    client: TestClient, db: Session, no_rate: dict[str, Any]
) -> None:
    res = client.patch(f"/api/v1/purchase-orders/{no_rate['usd']}", json={"fx_rate": "0.92"})
    assert res.status_code == 200, res.text
    assert Decimal(res.json()["fx_rate"]) == Decimal("0.92")

    report = client.get(f"/api/v1/landed-costs/containers/{no_rate['box']}").json()
    assert (report["totals"]["fob"], report["totals"]["landed"]) == ("1920.00", "2320.00")
    assert gap(report) == WHOLE and all(ln["fob_missing"] is None for ln in report["lines"])
    current = client.get("/api/v1/reports/overview").json()["current"]
    assert current["coefficient"] == str((Decimal("2320") / Decimal("1920")).quantize(Decimal("0.0001")))
    assert gap(current) == WHOLE
    a = next(s for s in client.get("/api/v1/reports/skus").json()["skus"] if s["sku"] == "A")
    assert (a["unit_landed"], a["margin_unit"]) == ("112.0000", "88.0000")
    (box,) = client.get("/api/v1/containers").json()
    assert box["fob_base"] == "1920.00" and gap(box) == WHOLE
    assert db.scalar(select(SkuCostHistory.unit_landed_cost).where(SkuCostHistory.sku == "A")) == Decimal(
        "112.0000"
    )
    closed = client.post(f"/api/v1/periods/{PERIOD}/close")
    assert closed.status_code == 201, closed.text
    assert (closed.json()["fob"], closed.json()["landed"]) == ("1920.00", "2320.00")


def test_no_unit_cost_is_recorded_for_an_article_whose_value_is_missing(
    client: TestClient, db: Session, no_rate: dict[str, Any]
) -> None:
    # Any write recomputes the organization and records the day's unit costs.
    assert client.post("/api/v1/costs", json=charge(no_rate["box"], "DRAYAGE", "100.00")).status_code == 201
    recorded = dict(db.execute(select(SkuCostHistory.sku, SkuCostHistory.unit_landed_cost)).tuples().all())
    assert set(recorded) == {"B"}


def test_a_report_sent_by_link_says_what_it_could_not_value(
    client: TestClient, no_rate: dict[str, Any]
) -> None:
    created = client.post(f"/api/v1/containers/{no_rate['box']}/share", json={})
    assert created.status_code == 201, created.text
    assert created.json()["landed"] is None
    opened = client.post("/api/v1/public/shared-reports/open", json={"token": created.json()["token"]})
    assert opened.status_code == 200, opened.text
    report = opened.json()["report"]
    assert report["totals"]["landed"] is None and gap(report) == MISSING


def test_the_audit_and_its_preparation_count_the_same_unpriced_articles(
    client: TestClient, db: Session, no_rate: dict[str, Any]
) -> None:
    # Article A is priced in the books' currency and its value is unknown: priced, its margin unknown.
    # C, in a second box from an order with no rate either, is priced in dollars: no margin here, so
    # unpriced in both, whether its value is known or not.
    order = {"po_number": "PO-C", "currency": "USD", "supplier_name": "Acme", "order_date": "2026-09-01",
             "lines": [{"line_no": 1, "sku": "C", "quantity": "5", "unit_price": "10"}]}  # fmt: skip
    res = client.post("/api/v1/purchase-orders", json=order)
    assert res.status_code == 201, res.text
    product = {"sku": "C", "sale_price": "30", "sale_currency": "USD"}
    assert client.post("/api/v1/products", json=product).status_code == 201
    box = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"}).json()
    moved = {"discharged_at": LANDED_ON.isoformat(), "milestone": "DISCHARGED"}
    assert client.patch(f"/api/v1/containers/{box['id']}", json=moved).status_code == 200
    loads = [{"po_line_id": res.json()["lines"][0]["id"], "quantity": "5"}]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
    assert client.post("/api/v1/costs", json=charge(box["id"], "DRAYAGE", "50.00")).status_code == 201
    db.execute(update(PurchaseOrder).where(PurchaseOrder.po_number == "PO-C").values(fx_rate=None))
    db.flush()

    dates = {"period_from": (LANDED_ON - timedelta(days=10)).date().isoformat(),
             "period_to": LANDED_ON.date().isoformat()}  # fmt: skip
    audit = client.get("/api/v1/reports/audit", params=dates).json()
    assert {m["sku"]: m["sale_price"] is not None for m in audit["margins"]} == {
        "A": True,
        "B": True,
        "C": False,
    }
    assert (audit["headline"]["priced_skus"], audit["headline"]["unpriced_skus"]) == (2, 1)
    preparation = client.get("/api/v1/reports/audit/preparation", params=dates).json()
    found = {item["code"]: item for item in preparation["items"]}
    assert [e["id"] for e in found["NO_SALE_PRICE"]["examples"]] == ["C"]
