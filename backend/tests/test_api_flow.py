"""End-to-end through /api/v1 on a real Postgres: the T5 reference case built via the API, multi-currency,
customs duty two-pass, preview overrides, integrity, tenancy."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient


def post(client: TestClient, path: str, json: Any, expect: int = 201) -> dict[str, Any]:
    res = client.post(path, json=json)
    assert res.status_code == expect, f"{path}: {res.status_code} {res.text}"
    return dict(res.json())


def setup_t5(client: TestClient) -> dict[str, Any]:
    """PO-A 1000 x 10.00 (2 kg/u) split 600/400 over CNT1/CNT2; PO-B 500 x 20.00 (5 kg/u) in CNT1."""
    sup = post(client, "/api/v1/suppliers", {"name": "Ardent Tyres"})
    po_a = post(
        client,
        "/api/v1/purchase-orders",
        {
            "po_number": "PO-A",
            "supplier_id": sup["id"],
            "lines": [
                {"line_no": 1, "sku": "A", "quantity": "1000", "unit_price": "10.00", "unit_weight_kg": "2"}
            ],
        },
    )
    po_b = post(
        client,
        "/api/v1/purchase-orders",
        {
            "po_number": "PO-B",
            "lines": [
                {"line_no": 1, "sku": "B", "quantity": "500", "unit_price": "20.00", "unit_weight_kg": "5"}
            ],
        },
    )
    ship = post(client, "/api/v1/shipments", {"reference": "MSCUBL001", "carrier_scac": "MSCU"})
    cnt1 = post(client, "/api/v1/containers", {"container_number": "MSCU1234567", "shipment_id": ship["id"]})
    cnt2 = post(client, "/api/v1/containers", {"container_number": "CMAU9876543", "shipment_id": ship["id"]})
    line_a, line_b = po_a["lines"][0]["id"], po_b["lines"][0]["id"]
    res = client.put(
        f"/api/v1/containers/{cnt1['id']}/loads",
        json=[{"po_line_id": line_a, "quantity": "600"}, {"po_line_id": line_b, "quantity": "500"}],
    )
    assert res.status_code == 200, res.text
    res = client.put(
        f"/api/v1/containers/{cnt2['id']}/loads", json=[{"po_line_id": line_a, "quantity": "400"}]
    )
    assert res.status_code == 200, res.text
    return {
        "po_a": po_a,
        "po_b": po_b,
        "ship": ship,
        "cnt1": cnt1,
        "cnt2": cnt2,
        "line_a": line_a,
        "line_b": line_b,
    }


def test_me_and_organization(client: TestClient, org_id: uuid.UUID) -> None:
    me = client.get("/api/v1/me").json()
    assert me["org_id"] == str(org_id) and me["role"] == "OWNER" and me["via"] == "dev"
    org = client.get("/api/v1/organization").json()
    assert org["base_currency"] == "EUR" and org["default_allocation_method"] == "BY_VALUE"
    res = client.patch(
        "/api/v1/organization", json={"name": "ACME Import", "default_allocation_method": "MANUAL"}
    )
    assert res.status_code == 422
    res = client.patch("/api/v1/organization", json={"name": "ACME Import", "free_days_demurrage": 7})
    assert res.status_code == 200 and res.json()["free_days_demurrage"] == 7


def test_the_language_an_organization_is_written_to_in(client: TestClient) -> None:
    assert client.get("/api/v1/organization").json()["locale"] == "fr"
    assert client.patch("/api/v1/organization", json={"locale": "en"}).json()["locale"] == "en"

    # a language we cannot write is refused rather than quietly turned back into French: the setting
    # would read as saved, and the e-mails would arrive in another language than the one chosen
    res = client.patch("/api/v1/organization", json={"locale": "de"})
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "ORG_LOCALE_UNKNOWN"
    assert client.get("/api/v1/organization").json()["locale"] == "en"


def test_t5_through_the_api(client: TestClient) -> None:
    s = setup_t5(client)
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "DRAYAGE",
            "amount": "300.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": s["cnt2"]["id"],
            "cost_type": "DRAYAGE",
            "amount": "200.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "SHIPMENT",
            "target_id": s["ship"]["id"],
            "cost_type": "OCEAN_FREIGHT",
            "amount": "3000.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
            "allocation_method": "BY_WEIGHT",
        },
    )

    cnt1 = client.get(f"/api/v1/landed-costs/containers/{s['cnt1']['id']}").json()
    assert cnt1["warnings"] == []
    # every cost here is an invoice, so the whole allocation is "actual", none of it was forecast,
    # and there is no pair to take a variance on
    assert cnt1["totals"] == {
        "estimated": "0.00",
        "actual": "2766.67",
        "variance": "0.00",
        "matched_estimated": "0.00",
        "matched_actual": "0.00",
        "unforecast_actual": "2766.67",
        "fob": "16000.00",
        "allocated": "2766.67",
        "vat": "0.00",
        "landed": "18766.67",
        "unallocated": "0.00",
    }
    by_po = {ln["po_number"]: ln for ln in cnt1["lines"]}
    assert by_po["PO-A"]["allocated"] == "912.50" and by_po["PO-A"]["unit_landed_cost"] == "11.5208"
    assert by_po["PO-B"]["allocated"] == "1854.17" and by_po["PO-B"]["unit_landed_cost"] == "23.7083"
    assert cnt1["by_cost_type"] == {"DRAYAGE": "300.00", "OCEAN_FREIGHT": "2466.67"}

    po_a = client.get(f"/api/v1/landed-costs/purchase-orders/{s['po_a']['id']}").json()
    assert po_a["totals"]["allocated"] == "1645.83" and po_a["totals"]["landed"] == "11645.83"
    # the PO ledger lists the container and shipment costs that reached its lines
    assert sorted(c["cost_type"] for c in po_a["costs"]) == ["DRAYAGE", "DRAYAGE", "OCEAN_FREIGHT"]

    ship = client.get(f"/api/v1/landed-costs/shipments/{s['ship']['id']}").json()
    assert ship["totals"]["allocated"] == "3500.00"

    integrity = client.get("/api/v1/reports/integrity").json()
    assert integrity["ok"] and integrity["costs_checked"] == 3

    summary = {c["container_number"]: c for c in client.get("/api/v1/containers").json()}
    assert summary["MSCU1234567"]["allocated_base"] == "2766.67"
    assert summary["MSCU1234567"]["po_numbers"] == ["PO-A", "PO-B"]
    assert summary["MSCU1234567"]["cost_types_present"] == ["DRAYAGE", "OCEAN_FREIGHT"]


def test_preview_override_does_not_persist(client: TestClient) -> None:
    s = setup_t5(client)
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "SHIPMENT",
            "target_id": s["ship"]["id"],
            "cost_type": "OCEAN_FREIGHT",
            "amount": "3000.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    before = client.get(f"/api/v1/landed-costs/containers/{s['cnt1']['id']}").json()
    assert before["by_cost_type"]["OCEAN_FREIGHT"] == "2400.00"  # by value: 16000 / 20000
    preview = post(
        client,
        "/api/v1/landed-costs/preview",
        {"container_id": s["cnt1"]["id"], "method_overrides": {"OCEAN_FREIGHT": "BY_WEIGHT"}},
        expect=200,
    )
    assert preview["by_cost_type"]["OCEAN_FREIGHT"] == "2466.67"
    after = client.get(f"/api/v1/landed-costs/containers/{s['cnt1']['id']}").json()
    assert after["by_cost_type"]["OCEAN_FREIGHT"] == "2400.00"


def test_multicurrency_cost_uses_fixed_rate(client: TestClient) -> None:
    s = setup_t5(client)
    cost = post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "THC",
            "amount": "1000.00",
            "currency": "USD",
            "cost_date": "2026-09-01",
        },
    )
    assert cost["fx_rate"] == "0.92" and cost["fx_source"] == "ecb" and cost["amount_base"] == "920.00"
    manual = post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "BL_FEE",
            "amount": "100.00",
            "currency": "USD",
            "cost_date": "2026-09-01",
            "fx_rate": "0.9",
        },
    )
    assert manual["fx_source"] == "manual" and manual["amount_base"] == "90.00"
    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "THC",
            "amount": "1",
            "currency": "GBP",
            "cost_date": "2026-09-01",
        },
    )
    assert res.status_code == 422 and res.json()["code"] == "FX_RATE_UNAVAILABLE"
    rate = client.get("/api/v1/fx-rates", params={"quote": "USD", "on_date": "2026-09-01"}).json()
    assert rate["rate"] == "0.92" and rate["source"] == "ecb"


def test_customs_duty_default_method_and_two_passes(client: TestClient) -> None:
    po = post(
        client,
        "/api/v1/purchase-orders",
        {
            "po_number": "PO-1",
            "lines": [
                {"line_no": 1, "quantity": "1000", "unit_price": "10.00", "duty_rate": "0.045"},
                {"line_no": 2, "quantity": "100", "unit_price": "50.00", "duty_rate": "0"},
            ],
        },
    )
    cnt = post(client, "/api/v1/containers", {"container_number": "MSCU1234567"})
    client.put(
        f"/api/v1/containers/{cnt['id']}/loads",
        json=[
            {"po_line_id": po["lines"][0]["id"], "quantity": "1000"},
            {"po_line_id": po["lines"][1]["id"], "quantity": "100"},
        ],
    )
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": cnt["id"],
            "cost_type": "OCEAN_FREIGHT",
            "amount": "1500.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": cnt["id"],
            "cost_type": "INSURANCE",
            "amount": "150.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    duty = post(
        client,
        "/api/v1/costs",
        {
            "scope": "PO",
            "target_id": po["id"],
            "cost_type": "CUSTOMS_DUTY",
            "amount": "499.50",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    assert duty["allocation_method"] == "BY_THEORETICAL_DUTY"
    vat = post(
        client,
        "/api/v1/costs",
        {
            "scope": "PO",
            "target_id": po["id"],
            "cost_type": "IMPORT_VAT",
            "amount": "3429.90",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    assert vat["allocation_method"] == "BY_VALUE"

    rep = client.get(f"/api/v1/landed-costs/purchase-orders/{po['id']}").json()
    lines = {ln["line_no"]: ln for ln in rep["lines"]}
    assert lines[1]["landed"] == "11599.50" and lines[1]["unit_landed_cost"] == "11.5995"
    assert lines[2]["landed"] == "5550.00"
    assert rep["totals"]["vat"] == "3429.90" and rep["totals"]["landed"] == "17149.50"

    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "PO",
            "target_id": po["id"],
            "cost_type": "DRAYAGE",
            "amount": "1",
            "currency": "EUR",
            "cost_date": "2026-09-01",
            "allocation_method": "BY_CIF_VALUE",
        },
    )
    assert res.status_code == 422 and res.json()["code"] == "METHOD_NOT_ALLOWED"


def test_missing_basis_is_a_warning_not_a_guess(client: TestClient) -> None:
    po = post(
        client,
        "/api/v1/purchase-orders",
        {"po_number": "PO-1", "lines": [{"line_no": 1, "quantity": "10", "unit_price": "1"}]},
    )
    cnt = post(client, "/api/v1/containers", {"container_number": "MSCU1234567"})
    client.put(
        f"/api/v1/containers/{cnt['id']}/loads", json=[{"po_line_id": po["lines"][0]["id"], "quantity": "10"}]
    )
    post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": cnt["id"],
            "cost_type": "DRAYAGE",
            "amount": "100.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
            "allocation_method": "BY_WEIGHT",
        },
    )
    rep = client.get(f"/api/v1/landed-costs/containers/{cnt['id']}").json()
    assert rep["totals"]["unallocated"] == "100.00" and rep["totals"]["allocated"] == "0.00"
    assert rep["warnings"][0]["code"] == "MISSING_BASIS"
    assert rep["warnings"][0]["message"].endswith(": PO-1 #1")
    assert client.get("/api/v1/reports/integrity").json()["ok"]


def test_over_allocation_refused_and_line_shrink_refused(client: TestClient) -> None:
    po = post(
        client,
        "/api/v1/purchase-orders",
        {"po_number": "PO-1", "lines": [{"line_no": 1, "quantity": "100", "unit_price": "1"}]},
    )
    line = po["lines"][0]["id"]
    c1 = post(client, "/api/v1/containers", {"container_number": "MSCU1234567"})
    c2 = post(client, "/api/v1/containers", {"container_number": "CMAU9876543"})
    assert (
        client.put(
            f"/api/v1/containers/{c1['id']}/loads", json=[{"po_line_id": line, "quantity": "60"}]
        ).status_code
        == 200
    )
    res = client.put(f"/api/v1/containers/{c2['id']}/loads", json=[{"po_line_id": line, "quantity": "50"}])
    assert res.status_code == 422 and res.json()["code"] == "OVER_ALLOCATED"
    assert res.json()["errors"][0]["field"] == "PO-1 line 1"
    assert "110 would be loaded" in res.json()["errors"][0]["message"]
    res = client.patch(
        f"/api/v1/purchase-orders/{po['id']}",
        json={"lines": [{"line_no": 1, "quantity": "50", "unit_price": "1"}]},
    )
    assert res.status_code == 422 and res.json()["code"] == "OVER_ALLOCATED"


def test_manual_milestone_sets_timestamps_and_lfd(client: TestClient) -> None:
    cnt = post(client, "/api/v1/containers", {"container_number": "MSCU1234567"})
    res = client.patch(
        f"/api/v1/containers/{cnt['id']}",
        json={"milestone": "DISCHARGED", "discharged_at": "2026-01-01T08:00:00Z", "free_days_demurrage": 7},
    )
    body = res.json()
    assert (
        res.status_code == 200 and body["last_free_day"] == "2026-01-07" and body["dnd_risk"] == "INCURRING"
    )
    # Picked up: the terminal clock stops, and the detention clock on returning the empty starts.
    res = client.patch(f"/api/v1/containers/{cnt['id']}", json={"milestone": "GATE_OUT_FULL"})
    body = res.json()
    assert body["gate_out_at"] is not None
    assert body["detention_deadline"] is not None
    assert body["dnd_risk"] == "LOW"  # seven free days to bring the box back

    res = client.patch(f"/api/v1/containers/{cnt['id']}", json={"milestone": "GATE_IN_EMPTY_RETURN"})
    assert res.json()["dnd_risk"] == "NONE"  # the box is back: nothing is running


def test_orgs_do_not_see_each_other(client: TestClient) -> None:
    s = setup_t5(client)
    other = {"X-Org-Id": str(uuid.uuid4())}
    assert client.get("/api/v1/containers", headers=other).json() == []
    assert client.get(f"/api/v1/containers/{s['cnt1']['id']}", headers=other).status_code == 404
    assert client.get(f"/api/v1/landed-costs/containers/{s['cnt1']['id']}", headers=other).status_code == 404
    res = client.post(
        "/api/v1/costs",
        headers=other,
        json={
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "THC",
            "amount": "1",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    assert res.status_code == 404


def test_missing_credentials_is_401(client: TestClient) -> None:
    res = client.get("/api/v1/containers", headers={"X-Org-Id": ""})
    assert res.status_code in (401, 422)


def test_cost_delete_and_container_archive(client: TestClient) -> None:
    s = setup_t5(client)
    cost = post(
        client,
        "/api/v1/costs",
        {
            "scope": "CONTAINER",
            "target_id": s["cnt1"]["id"],
            "cost_type": "DRAYAGE",
            "amount": "300.00",
            "currency": "EUR",
            "cost_date": "2026-09-01",
        },
    )
    assert client.delete(f"/api/v1/containers/{s['cnt1']['id']}").status_code == 409
    assert client.delete(f"/api/v1/costs/{cost['id']}").status_code == 204
    assert client.delete(f"/api/v1/containers/{s['cnt1']['id']}").status_code == 204
    assert [c["container_number"] for c in client.get("/api/v1/containers").json()] == ["CMAU9876543"]
    assert client.get("/api/v1/reports/integrity").json()["ok"]
