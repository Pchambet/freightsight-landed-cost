"""GET /reports/overview: the first screen in one call, and never a figure of its own."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.domain.reporting.overview import periods


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    res = client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    assert res.status_code == 201, res.text
    return client


def test_the_previous_period_is_the_same_number_of_days_just_before() -> None:
    today = date(2026, 9, 17)
    assert periods(None, None, today) == (date(2026, 6, 20), today, date(2026, 3, 22), date(2026, 6, 19))
    june = periods(date(2026, 6, 1), date(2026, 6, 30), today)
    assert june == (date(2026, 6, 1), date(2026, 6, 30), date(2026, 5, 2), date(2026, 5, 31))


def test_the_period_is_the_landed_cost_report_of_the_same_dates(seeded: TestClient) -> None:
    body = seeded.get("/api/v1/reports/overview").json()
    current, previous = body["current"], body["previous"]
    assert body["base_currency"] == "EUR"
    assert current["containers"] > 0 and previous["containers"] > 0

    report = seeded.get(
        "/api/v1/reports/landed-cost",
        params={
            "group_by": "month",
            "period_from": current["period_from"],
            "period_to": current["period_to"],
        },
    ).json()
    assert current["fob"] == report["totals"]["fob"]
    assert current["landed"] == report["totals"]["landed"]
    coefficient = (Decimal(current["landed"]) / Decimal(current["fob"])).quantize(Decimal("0.0001"))
    assert Decimal(current["coefficient"]) == coefficient
    assert Decimal("1.05") < coefficient < Decimal("1.30")

    dnd = seeded.get(
        "/api/v1/reports/dnd",
        params={"period_from": current["period_from"], "period_to": current["period_to"]},
    ).json()
    assert current["demurrage_paid"] == dnd["paid"]
    assert current["demurrage_avoided"] == dnd["avoided"]
    assert current["variance_pairs"] > 0
    assert Decimal(current["variance"]) == Decimal(current["variance_actual"]) - Decimal(
        current["variance_estimated"]
    )


def test_what_is_waiting_for_somebody_today(seeded: TestClient) -> None:
    todo = seeded.get("/api/v1/reports/overview").json()["todo"]
    assert todo["invoices_to_review"] == 1 and todo["invoices_failed"] == 0
    assert todo["containers_without_cost"] == 0 and todo["containers_without_loads"] == 0
    # two landed boxes, five estimates each — and the clearance of voyage 9 that nobody invoiced,
    # left standing on purpose for the audit to find (sample_history.NEVER_INVOICED_ON)
    assert todo["estimates_awaiting_invoice"] == 11
    assert (todo["containers_overdue"], todo["containers_at_risk"]) == (1, 1)

    at_risk = seeded.get("/api/v1/reports/dnd").json()["at_risk"]
    overdue = next(line for line in at_risk if line["rule"] == "DND_AT_RISK_OVERDUE")
    ahead = next(line for line in at_risk if line["rule"] == "DND_AT_RISK_DAYS_LEFT")
    assert overdue["days_over"] in (2, 3) and overdue["days_left"] == -overdue["days_over"]
    assert overdue["daily_rate"] == "85.00"
    assert Decimal(overdue["amount_at_risk"]) == Decimal(85) * overdue["days_over"]
    assert (ahead["days_over"], ahead["amount_at_risk"]) == (0, "0.00") and ahead["days_left"] >= 0
    assert todo["amount_at_risk"] == overdue["amount_at_risk"]


def test_without_a_daily_rate_no_amount_is_made_up(client: TestClient) -> None:
    container = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    discharged = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    res = client.patch(
        f"/api/v1/containers/{container['id']}",
        json={"discharged_at": discharged, "milestone": "DISCHARGED", "free_days_demurrage": 7},
    )
    assert res.status_code == 200, res.text
    todo = client.get("/api/v1/reports/overview").json()["todo"]
    assert todo["containers_overdue"] == 1
    assert todo["amount_at_risk"] is None
    assert todo["containers_without_loads"] == 1
    (line,) = client.get("/api/v1/reports/dnd").json()["at_risk"]
    assert line["days_over"] >= 13 and line["daily_rate"] is None and line["amount_at_risk"] is None


def test_an_empty_organization_gets_zeros_and_no_coefficient(client: TestClient) -> None:
    body = client.get("/api/v1/reports/overview").json()
    assert body["current"]["coefficient"] is None
    assert body["current"]["landed"] == "0.00" and body["current"]["containers"] == 0


def test_the_first_screen_is_one_call_and_says_what_the_detailed_reports_say(seeded: TestClient) -> None:
    body = seeded.get("/api/v1/reports/overview").json()
    dates = {"period_from": body["current"]["period_from"], "period_to": body["current"]["period_to"]}
    for group, key in (("month", "by_month"), ("supplier", "by_supplier")):
        report = seeded.get("/api/v1/reports/landed-cost", params={"group_by": group, **dates}).json()
        assert body[key] == report["buckets"]
    monthly = seeded.get("/api/v1/reports/landed-cost", params={"group_by": "month", **dates}).json()
    assert body["by_cost_type"] == monthly["totals"]["by_cost_type"]
    assert len(body["skus"]) == 6
    heaviest = sorted(monthly["skus"], key=lambda s: Decimal(s["landed"]), reverse=True)[:6]
    assert [s["sku"] for s in body["skus"]] == [s["sku"] for s in heaviest]
    assert body["at_risk"] == seeded.get("/api/v1/reports/dnd").json()["at_risk"]
