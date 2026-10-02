"""POST /api/v1/organization/sample-data: the demo dataset is the T5 reference case, is created once, and
never leaks between organizations."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Container, Cost, PurchaseOrder


def test_sample_data_seeds_the_reference_case(client: TestClient) -> None:
    res = client.post("/api/v1/organization/sample-data")
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["suppliers"] == 2 and body["purchase_orders"] == 2
    # 5 ESTIMATE costs per container (freight, THC, customs brokerage, haulage, customs duty) — no
    # ACTUAL cost is seeded, so confirming the demo invoice always replaces an estimate, never adds
    # a duplicate. See sample_data.py's docstring.
    assert body["containers"] == 2 and body["costs"] == 10
    assert len(body["container_ids"]) == 2

    containers = {c["container_number"]: c for c in client.get("/api/v1/containers").json()}
    assert sorted(containers) == ["MSCU4821990", "TGHU7245081"]
    cnt1, cnt2 = containers["MSCU4821990"], containers["TGHU7245081"]
    assert cnt1["milestone"] == "DISCHARGED"
    assert cnt1["last_free_day"] is not None and cnt1["dnd_risk"] == "MEDIUM"
    # gated out: demurrage stopped at the terminal, detention started on the empty
    assert cnt2["last_free_day"] is not None
    assert cnt2["detention_deadline"] is not None
    assert cnt2["dnd_risk"] == "LOW"

    # Everything here is an ESTIMATE (rate cards), not yet an invoice — see sample_data.py.
    assert cnt1["allocated_base"] == "5845.10"
    assert cnt1["po_numbers"] == ["PO-2026-014", "PO-2026-015"]
    assert cnt1["cost_types_present"] == [
        "CUSTOMS_BROKERAGE",
        "CUSTOMS_DUTY",
        "DRAYAGE",
        "OCEAN_FREIGHT",
        "THC",
    ]

    report = client.get(f"/api/v1/landed-costs/containers/{cnt1['id']}").json()
    assert report["warnings"] == []
    assert report["totals"]["fob"] == "20500.00"
    assert report["totals"]["allocated"] == "5845.10"
    assert report["totals"]["landed"] == "26345.10"
    assert report["totals"]["vat"] == "0.00"
    # Nothing has been invoiced yet: every euro on this container is still an estimate, so the
    # completeness gauge the front shows as "postes facturés" reads 0, honestly.
    assert report["totals"]["estimated"] == "5845.10"
    assert report["totals"]["actual"] == "0.00"
    assert report["totals"]["variance"] == "0.00"
    assert report["completeness"] == "0.00"
    by_sku = {ln["sku"]: ln for ln in report["lines"]}
    # The duty goes by each line's own rate: the tyre at 4.5 % carries less of it than the mat at 6.5 %.
    assert by_sku["TYR-20555R16-91V"]["unit_landed_cost"] == "17.8305"
    assert by_sku["MAT-EVA-6040-GY"]["unit_landed_cost"] == "25.9444"

    report2 = client.get(f"/api/v1/landed-costs/containers/{cnt2['id']}").json()
    assert report2["totals"]["fob"] == "16800.00"
    assert report2["totals"]["allocated"] == "5442.50"
    assert report2["lines"][0]["unit_landed_cost"] == "18.5354"

    po_id = client.get("/api/v1/purchase-orders").json()[0]["id"]
    po = client.get(f"/api/v1/landed-costs/purchase-orders/{po_id}").json()
    assert po["totals"]["allocated"] == "8315.40" and po["totals"]["landed"] == "35615.40"

    integrity = client.get("/api/v1/reports/integrity").json()
    assert integrity["ok"] and integrity["costs_checked"] == 10

    alerts = client.get("/api/v1/alerts").json()
    mscu_alerts = [a for a in alerts if a.get("container_id") == cnt1["id"] and a.get("kind") == "DND_RISK"]
    assert len(mscu_alerts) == 1
    assert mscu_alerts[0]["severity"] == "warning"
    # TGHU already left the terminal, but it is still inside its detention window (LOW risk, not
    # NONE): raise_dnd_risk_alert fires for any risk short of NONE, so it gets its own low-severity
    # alert rather than none at all.
    tghu_alerts = [a for a in alerts if a.get("container_id") == cnt2["id"] and a.get("kind") == "DND_RISK"]
    assert len(tghu_alerts) == 1
    assert tghu_alerts[0]["severity"] == "info"


def test_sample_data_is_loaded_only_once(client: TestClient, db: Session) -> None:
    assert client.post("/api/v1/organization/sample-data").status_code == 201
    again = client.post("/api/v1/organization/sample-data")
    assert again.status_code == 409 and again.json()["code"] == "CONFLICT"
    assert len(client.get("/api/v1/containers").json()) == 2
    assert len(client.get("/api/v1/purchase-orders").json()) == 2
    assert len(client.get("/api/v1/costs").json()) == 10


def test_sample_data_refused_when_the_organization_already_has_data(client: TestClient) -> None:
    assert client.post("/api/v1/containers", json={"container_number": "MSCU1234567"}).status_code == 201
    res = client.post("/api/v1/organization/sample-data")
    assert res.status_code == 409
    assert len(client.get("/api/v1/containers").json()) == 1


def test_sample_data_is_isolated_per_organization(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    other = uuid.uuid4()
    assert client.post("/api/v1/organization/sample-data").status_code == 201
    assert (
        client.post("/api/v1/organization/sample-data", headers={"X-Org-Id": str(other)}).status_code == 201
    )

    for org in (org_id, other):
        containers = client.get("/api/v1/containers", headers={"X-Org-Id": str(org)}).json()
        assert len(containers) == 2

    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert {c.org_id for c in db.scalars(select(Container))} == {org_id}
        assert {p.org_id for p in db.scalars(select(PurchaseOrder))} == {org_id}
        assert {c.org_id for c in db.scalars(select(Cost))} == {org_id}
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)
