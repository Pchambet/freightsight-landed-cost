"""POST /organization/sample-data?profile=history: six months that the reports can draw.

The seed only places inputs; every figure asserted here comes out of the product's own code — the
engine, the estimates, the demurrage report, the invoice reader. What is pinned is what the screens
built on this dataset rely on: a trend over several months, a variance, demurrage paid and avoided,
a container at risk today, a line the duty cannot be spread over, an invoice waiting for its reader.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.domain.sample_history import VOYAGES, container_number


@pytest.fixture
def history(client: TestClient) -> dict[str, Any]:
    res = client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def test_a_container_number_carries_its_real_check_digit() -> None:
    assert container_number("CSQU", 305438) == "CSQU3054383"  # the ISO 6346 worked example


def test_it_is_twenty_containers_three_suppliers_and_orders_that_span_two_boxes(
    client: TestClient, history: dict[str, Any]
) -> None:
    assert history["containers"] == len(VOYAGES) == 20
    assert history["suppliers"] == 3
    orders = client.get("/api/v1/purchase-orders").json()
    assert len(orders) == history["purchase_orders"]
    assert any(len(order["container_numbers"]) == 2 for order in orders)
    assert {order["currency"] for order in orders} == {"USD", "EUR"}


def test_the_landed_cost_report_has_a_month_by_month_story(
    client: TestClient, history: dict[str, Any]
) -> None:
    report = client.get("/api/v1/reports/landed-cost", params={"group_by": "month"}).json()
    months = [bucket for bucket in report["buckets"] if Decimal(bucket["landed"]) > 0]
    assert len(months) >= 6
    shares = [Decimal(bucket["landed"]) / Decimal(bucket["fob"]) for bucket in months]
    # the freight climbs and eases off: the landed/FOB coefficient is not flat
    assert max(shares) - min(shares) > Decimal("0.01")


def test_most_boxes_were_invoiced_and_the_variance_report_says_by_how_much(
    client: TestClient, history: dict[str, Any]
) -> None:
    costs = client.get("/api/v1/costs").json()
    freight = [c for c in costs if c["cost_type"] == "OCEAN_FREIGHT"]
    invoiced = {c["container_id"] for c in freight if c["status"] == "ACTUAL"}
    assert len(invoiced) == 16  # the other four: two at sea, two on the terminal with no invoice yet
    assert Counter(c["currency"] for c in freight) == {"USD": 36}  # 20 quotes, 16 invoices

    today = datetime.now(UTC).date()
    months = {(today - timedelta(days=30 * back)).strftime("%Y-%m") for back in range(7)}
    reports = [client.get("/api/v1/reports/variance", params={"period": m}).json() for m in sorted(months)]
    with_pairs = [r for r in reports if r["pairs"] > 0]
    assert len(with_pairs) >= 4
    variances = [Decimal(row["variance"]) for r in with_pairs for row in r["by_cost_type"]]
    assert any(v > 0 for v in variances) and any(v < 0 for v in variances)


def test_demurrage_was_paid_twice_avoided_once_and_two_boxes_are_at_risk_today(
    client: TestClient, history: dict[str, Any]
) -> None:
    report = client.get("/api/v1/reports/dnd").json()
    assert Decimal(report["paid"]) == Decimal("425.00")  # 2 days + 3 days at 85 €
    assert Decimal(report["avoided"]) > 0
    rules = {line["rule"] for line in report["at_risk"]}
    assert rules == {"DND_AT_RISK_OVERDUE", "DND_AT_RISK_DAYS_LEFT"}


def test_one_line_has_no_tariff_rate_and_the_container_says_so(
    client: TestClient, history: dict[str, Any]
) -> None:
    at_risk = next(i for i, v in enumerate(VOYAGES) if v.fate == "at_risk")
    container_id = history["container_ids"][at_risk]
    report = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()
    (note,) = [n for n in report["notes"] if n["code"] == "DUTY_RATE_PARTIAL"]
    assert any("CHAIN-SNOW-9MM" in label for label in note["load_labels"])


def test_an_invoice_is_waiting_to_be_reviewed_with_its_lines_read(
    client: TestClient, history: dict[str, Any]
) -> None:
    invoices = client.get("/api/v1/invoices").json()
    assert [i["status"] for i in invoices] == ["NEEDS_REVIEW"]
    detail = client.get(f"/api/v1/invoices/{invoices[0]['id']}").json()
    assert len(detail["lines"]) == 4
    assert detail["extractor"] == "regex"
    assert Decimal(detail["subtotal_amount"]) == Decimal("3274.60")
