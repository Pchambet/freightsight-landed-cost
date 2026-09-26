"""GET /reports/skus and /reports/skus/detail: an article seen through the containers that carried it."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

TYRE = "TYR-20555R16-91V"


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    res = client.post("/api/v1/organization/sample-data", params={"profile": "history"})
    assert res.status_code == 201, res.text
    return client


def detail(client: TestClient, sku: str, **params: str) -> dict[str, Any]:
    res = client.get("/api/v1/reports/skus/detail", params={"sku": sku, **params})
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def test_every_article_that_travelled_is_listed_with_its_weighted_unit_cost(seeded: TestClient) -> None:
    body = seeded.get("/api/v1/reports/skus").json()
    assert body["base_currency"] == "EUR"
    skus = {s["sku"]: s for s in body["skus"]}
    assert len(skus) == 8
    tyre = skus[TYRE]
    assert tyre["description"] == "Pneu 205/55 R16 91V"
    assert Decimal(tyre["unit_landed"]) == (Decimal(tyre["landed"]) / Decimal(tyre["quantity"])).quantize(
        Decimal("0.0001")
    )
    assert Decimal(tyre["unit_landed"]) > Decimal(tyre["unit_fob"]) > 0
    assert tyre["arrivals"] >= 8 and tyre["has_estimates"] is True  # two of its boxes are not invoiced
    # the demo's catalogue prices the tyre; the jack has a sheet and no price, so no margin either
    assert tyre["product_id"] is not None and Decimal(tyre["sale_price"]) == Decimal("24.90")
    jack = skus["JACK-HYD-2T"]
    assert jack["product_id"] is not None and jack["sale_price"] is None and jack["margin_unit"] is None


def test_an_arrival_is_a_container_and_its_cost_types_add_up(seeded: TestClient) -> None:
    body = detail(seeded, TYRE)
    arrivals = body["arrivals"]
    assert isinstance(arrivals, list) and len(arrivals) >= 8
    dates = [a["arrived_on"] for a in arrivals]
    assert dates == sorted(dates)  # oldest first
    for arrival in arrivals:
        costs = sum((Decimal(v) for v in arrival["by_cost_type"].values()), Decimal(0))
        assert Decimal(arrival["landed"]) == Decimal(arrival["fob"]) + costs
        assert "IMPORT_VAT" not in arrival["by_cost_type"]
        assert arrival["container_number"] and arrival["po_number"] and arrival["supplier_name"]
    at_sea = [a for a in arrivals if a["arrival_is_estimate"]]
    assert at_sea and all(Decimal(a["estimated"]) > 0 for a in at_sea)


def test_the_change_compares_the_two_last_boxes_that_landed_not_one_still_at_sea(seeded: TestClient) -> None:
    body = detail(seeded, TYRE)
    summary, arrivals = body["summary"], body["arrivals"]
    landed = [a for a in arrivals if not a["arrival_is_estimate"]]
    last, previous = Decimal(landed[-1]["unit_landed"]), Decimal(landed[-2]["unit_landed"])
    assert Decimal(summary["last_unit_landed"]) == last
    assert Decimal(summary["previous_unit_landed"]) == previous
    expected = ((last - previous) / previous * 100).quantize(Decimal("0.1"))
    assert Decimal(summary["change_pct"]) == expected
    assert summary["last_arrival_on"] == landed[-1]["arrived_on"]


def test_the_freight_of_the_summer_shows_in_the_unit_cost_of_a_tyre(seeded: TestClient) -> None:
    freight = [Decimal(a["unit_by_cost_type"]["OCEAN_FREIGHT"]) for a in detail(seeded, TYRE)["arrivals"]]
    assert max(freight) > min(freight) * Decimal("1.3")


def test_a_selling_price_in_the_organizations_currency_gives_a_margin(seeded: TestClient) -> None:
    seeded.post("/api/v1/products", json={"sku": TYRE, "sale_price": "39.90"})
    summary = detail(seeded, TYRE)["summary"]
    unit = Decimal(summary["unit_landed"])
    assert Decimal(summary["margin_unit"]) == Decimal("39.90") - unit
    expected = ((Decimal("39.90") - unit) / Decimal("39.90") * 100).quantize(Decimal("0.1"))
    assert Decimal(summary["margin_pct"]) == expected

    last = Decimal(summary["last_unit_landed"])
    assert Decimal(summary["last_margin_unit"]) == Decimal("39.90") - last
    assert Decimal(summary["last_margin_pct"]) == (
        (Decimal("39.90") - last) / Decimal("39.90") * 100
    ).quantize(Decimal("0.1"))

    # a price in dollars is shown, and no margin is made up from it
    seeded.post("/api/v1/products", json={"sku": TYRE, "sale_price": "45.00", "sale_currency": "USD"})
    dollars = detail(seeded, TYRE)["summary"]
    assert dollars["sale_price"] == "45.0000" and dollars["margin_unit"] is None
    assert dollars["last_margin_unit"] is None


def test_the_list_can_be_searched_and_narrowed_to_a_period(seeded: TestClient) -> None:
    found = seeded.get("/api/v1/reports/skus", params={"q": "chaines"}).json()["skus"]
    assert [s["sku"] for s in found] == ["CHAIN-SNOW-9MM"]
    everything = detail(seeded, TYRE)["arrivals"]
    first_landing = everything[0]["arrived_on"]
    only = detail(seeded, TYRE, period_from=first_landing, period_to=first_landing)
    assert len(only["arrivals"]) == 1
    assert only["summary"]["previous_unit_landed"] is None


def test_an_unknown_sku_is_not_found_and_a_slash_is_no_trouble(seeded: TestClient) -> None:
    assert seeded.get("/api/v1/reports/skus/detail", params={"sku": "NOPE/42"}).status_code == 404


def test_two_order_lines_of_one_article_in_one_box_are_one_arrival(client: TestClient) -> None:
    """The change compares containers. An article on two order lines of the last box must not be
    compared with itself."""
    lines = [
        {"line_no": n, "sku": "A", "quantity": "10", "unit_price": price}
        for n, price in ((1, "10"), (2, "12"))
    ]
    po = client.post("/api/v1/purchase-orders", json={"po_number": "PO-1", "currency": "EUR", "lines": lines})
    assert po.status_code == 201, po.text
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    loads = [{"po_line_id": line["id"], "quantity": "10"} for line in po.json()["lines"]]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200

    body = detail(client, "A")
    assert len(body["arrivals"]) == 2
    (only,) = body["containers"]
    assert (only["quantity"], only["fob"], only["unit_fob"]) == ("20.0000", "220.00", "11.0000")
    assert body["summary"]["arrivals"] == 1
