"""The product catalogue: what it lends to a new order line, and what it never touches."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

TYRE = {
    "sku": "TYR-20555R16-91V",
    "description": "Pneu tourisme 205/55 R16 91V",
    "hs_code": "40111000",
    "duty_rate": "0.045",
    "unit_weight_kg": "9",
    "unit_volume_cbm": "0.05",
    "sale_price": "39.90",
}


def order(client: TestClient, number: str, line: dict[str, Any]) -> dict[str, Any]:
    res = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": number, "currency": "EUR", "lines": [{"line_no": 1, **line}]},
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def test_posting_the_same_sku_twice_updates_the_entry(client: TestClient) -> None:
    created = client.post("/api/v1/products", json=TYRE)
    assert created.status_code == 201, created.text
    again = client.post("/api/v1/products", json={"sku": " TYR-20555R16-91V ", "sale_price": "42.00"})
    assert again.status_code == 200
    assert again.json()["id"] == created.json()["id"]
    assert again.json()["sale_price"] == "42.0000"
    assert again.json()["hs_code"] == "40111000"  # what the second call did not mention is kept
    assert len(client.get("/api/v1/products").json()["products"]) == 1


def test_a_new_order_line_borrows_what_it_does_not_say(client: TestClient) -> None:
    client.post("/api/v1/products", json=TYRE)
    po = order(client, "PO-1", {"sku": "TYR-20555R16-91V", "quantity": "100", "unit_price": "14"})
    (line,) = po["lines"]
    assert line["hs_code"] == "40111000"
    assert line["duty_rate"] == "0.045"
    assert line["unit_weight_kg"] == "9.0000"
    assert line["description"] == "Pneu tourisme 205/55 R16 91V"


def test_what_the_line_says_wins_and_zero_is_something_said(client: TestClient) -> None:
    client.post("/api/v1/products", json=TYRE)
    po = order(
        client,
        "PO-2",
        {
            "sku": "TYR-20555R16-91V",
            "quantity": "100",
            "unit_price": "14",
            "duty_rate": "0",
            "unit_weight_kg": "8.5",
        },
    )
    (line,) = po["lines"]
    assert line["duty_rate"] == "0"  # duty-free on this order: not a blank
    assert line["unit_weight_kg"] == "8.5000"


def test_editing_the_catalogue_moves_no_line_already_written(client: TestClient) -> None:
    product = client.post("/api/v1/products", json=TYRE).json()
    po = order(client, "PO-3", {"sku": "TYR-20555R16-91V", "quantity": "100", "unit_price": "14"})
    client.patch(f"/api/v1/products/{product['id']}", json={"duty_rate": "0.10", "unit_weight_kg": "12"})
    (line,) = client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]
    assert (line["duty_rate"], line["unit_weight_kg"]) == ("0.045", "9.0000")
    assert client.delete(f"/api/v1/products/{product['id']}").status_code == 204
    (line,) = client.get(f"/api/v1/purchase-orders/{po['id']}").json()["lines"]
    assert line["hs_code"] == "40111000"


def test_an_imported_line_borrows_too_and_the_report_says_what(client: TestClient) -> None:
    client.post("/api/v1/products", json=TYRE)
    csv = b"po_number,sku,quantity,unit_price\nPO-9,TYR-20555R16-91V,10,14\n"
    job = client.post(
        "/api/v1/imports",
        data={"kind": "PURCHASE_ORDERS"},
        files={"file": ("orders.csv", csv, "text/csv")},
    )
    assert job.status_code == 201, job.text
    done = client.post(
        f"/api/v1/imports/{job.json()['id']}/validate", json={"mapping": job.json()["mapping"]}
    )
    assert done.status_code == 200, done.text
    (warning,) = [w for w in done.json()["report"]["warnings"] if w["code"] == "PRODUCT_DEFAULTS_APPLIED"]
    assert warning["params"]["sku"] == "TYR-20555R16-91V"
    assert "duty_rate" in warning["params"]["fields"].split(",")


def test_the_catalogue_can_be_started_from_the_orders_already_there(client: TestClient) -> None:
    assert client.post("/api/v1/organization/sample-data").status_code == 201
    client.post("/api/v1/products", json={"sku": "MAT-EVA-6040-GY", "sale_price": "49.00"})
    built = client.post("/api/v1/products/from-orders").json()
    assert built == {"created": 1, "skipped": 1}
    products = {p["sku"]: p for p in client.get("/api/v1/products").json()["products"]}
    assert products["TYR-20555R16-91V"]["hs_code"] == "40111000"
    assert products["TYR-20555R16-91V"]["duty_rate"] == "0.045"
    assert products["MAT-EVA-6040-GY"]["hs_code"] is None  # an entry that exists is left as it is
    assert client.post("/api/v1/products/from-orders").json() == {"created": 0, "skipped": 2}


def test_two_products_cannot_share_a_sku_and_search_ignores_accents(client: TestClient) -> None:
    client.post("/api/v1/products", json=TYRE)
    other = client.post(
        "/api/v1/products", json={"sku": "BAT-12V", "description": "Batterie de démarrage"}
    ).json()
    clash = client.patch(f"/api/v1/products/{other['id']}", json={"sku": "TYR-20555R16-91V"})
    assert clash.status_code == 409
    assert clash.json()["code"] == "PRODUCT_SKU_TAKEN"
    found = client.get("/api/v1/products", params={"q": "demarrage"}).json()["products"]
    assert [p["sku"] for p in found] == ["BAT-12V"]


def test_another_organization_sees_no_product_of_ours(client: TestClient) -> None:
    client.post("/api/v1/products", json=TYRE)
    theirs = client.get("/api/v1/products", headers={"X-Org-Id": str(uuid.uuid4())})
    assert theirs.json()["products"] == []
    assert Decimal(client.get("/api/v1/products").json()["products"][0]["sale_price"]) == Decimal("39.90")
