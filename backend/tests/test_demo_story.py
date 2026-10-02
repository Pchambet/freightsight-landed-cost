"""Pins the figures the README's worked example quotes to the sample data and the demo invoice, so a
quoted figure can never drift from what the application shows.

Two states, both asserted against the real API — sample data loaded, then the demo invoice confirmed
whole (every line is safe to accept: nothing here is a pre-existing ACTUAL cost for the invoice to
duplicate):

  * before the invoice — every container carries only ESTIMATE costs, from the rate cards
    `seed_sample_data` writes;
  * after the invoice — MSCU4821990's five estimates are superseded by Transdemo's five actual
    lines, and the estimated -> actual -> variance screen has a real, non-zero variance to show.
    TGHU7245081 is never invoiced in the demo, so it stays at its estimate throughout — the two
    containers' tyre unit cost is intentionally different.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from pdfs import make_pdf
from sqlalchemy.orm import Session

from app.domain.models import Organization
from app.domain.sample_data import (
    DEMO_INVOICE_CUSTOMS_BROKERAGE,
    DEMO_INVOICE_CUSTOMS_DUTY,
    DEMO_INVOICE_DRAYAGE,
    DEMO_INVOICE_OCEAN_FREIGHT,
    DEMO_INVOICE_THC,
    DEMO_INVOICE_TOTAL,
    DEMO_INVOICE_VAT,
    demo_invoice_lines,
)
from app.jobs import handlers


def _load_sample_data(client: TestClient) -> dict[str, str]:
    res = client.post("/api/v1/organization/sample-data")
    assert res.status_code == 201, res.text
    containers = {c["container_number"]: c["id"] for c in client.get("/api/v1/containers").json()}
    return containers


def test_estimate_figures_before_any_invoice(client: TestClient) -> None:
    """Sample data loaded, before any invoice: the FOB -> estimated landed cost figures on both
    containers."""
    containers = _load_sample_data(client)

    mscu = client.get(f"/api/v1/landed-costs/containers/{containers['MSCU4821990']}").json()
    by_sku = {ln["sku"]: ln for ln in mscu["lines"]}
    assert by_sku["TYR-20555R16-91V"]["unit_landed_cost"] == "17.8305"  # "~17,83 €"
    assert by_sku["MAT-EVA-6040-GY"]["unit_landed_cost"] == "25.9444"  # "~25,94 €"
    assert mscu["totals"]["landed"] == "26345.10"
    assert mscu["completeness"] == "0.00"  # nothing invoiced yet

    tghu = client.get(f"/api/v1/landed-costs/containers/{containers['TGHU7245081']}").json()
    assert tghu["lines"][0]["unit_landed_cost"] == "18.5354"  # "~18,54 €" — different container mix


def test_confirming_the_whole_invoice_produces_a_real_variance(
    client: TestClient, db: Session, org_id: uuid.UUID
) -> None:
    """Demo invoice confirmed: accept every line (no duplicate, see the module docstring), and check
    the unit cost moves to the ACTUAL figure with a real, non-zero variance."""
    containers = _load_sample_data(client)
    mscu_id = containers["MSCU4821990"]

    pdf = make_pdf(demo_invoice_lines(datetime.now(UTC).date()))
    upload = client.post(
        "/api/v1/invoices", files={"file": ("facture-transdemo-demo.pdf", pdf, "application/pdf")}
    )
    assert upload.status_code == 201, upload.text
    invoice_id = upload.json()["id"]

    org = db.get(Organization, org_id)
    assert org is not None
    status = handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id))
    assert status == "NEEDS_REVIEW"

    invoice = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert len(invoice["lines"]) == 5  # freight, THC, customs brokerage, haulage, customs duty
    assert {ln["cost_type"] for ln in invoice["lines"]} == {
        "OCEAN_FREIGHT",
        "THC",
        "CUSTOMS_BROKERAGE",
        "DRAYAGE",
        "CUSTOMS_DUTY",
    }
    # The whole point of seeding estimates instead of pre-existing actuals: every line is safe to
    # accept, none of them collides with something already on the container.
    assert all(ln["notes"] is None or "@duplicate_cost" not in (ln["notes"] or "") for ln in invoice["lines"])

    for line in invoice["lines"]:
        r = client.patch(f"/api/v1/invoices/{invoice_id}/lines/{line['id']}", json={"accepted": True})
        assert r.status_code == 200, r.text

    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["costs_created"] == 5
    assert len(body["superseded_estimate_ids"]) == 5  # every estimate replaced, none left dangling
    assert body["notes"] == []  # no "pick one by hand" note: each estimate matched exactly one line

    report = client.get(f"/api/v1/landed-costs/containers/{mscu_id}").json()
    assert report["totals"]["actual"] == str(
        DEMO_INVOICE_OCEAN_FREIGHT
        + DEMO_INVOICE_THC
        + DEMO_INVOICE_CUSTOMS_BROKERAGE
        + DEMO_INVOICE_DRAYAGE
        + DEMO_INVOICE_CUSTOMS_DUTY
    )
    assert report["totals"]["estimated"] == "0.00"  # every estimate on this container was superseded
    assert report["totals"]["landed"] == "26558.79"
    assert report["totals"]["variance"] == "213.69"
    assert report["completeness"] == "1.00"

    by_sku = {ln["sku"]: ln for ln in report["lines"]}
    assert by_sku["TYR-20555R16-91V"]["unit_landed_cost"] == "17.9750"  # README: 17.98 €
    assert by_sku["MAT-EVA-6040-GY"]["unit_landed_cost"] == "26.1550"  # README: 26.16 €

    # TGHU never saw this invoice: it is exactly where it was before, still an estimate.
    tghu = client.get(f"/api/v1/landed-costs/containers/{containers['TGHU7245081']}").json()
    assert tghu["totals"]["allocated"] == "5442.50"
    assert tghu["totals"]["estimated"] == "5442.50"

    # Variance report: the invoice date sample_data.py chooses (a few days before "today") falls in
    # the current month, so the estimate/actual pairs this confirm() just created show up here, and
    # the report is never empty on a fresh demo organization.
    period = datetime.now(UTC).date().strftime("%Y-%m")
    variance = client.get(f"/api/v1/reports/variance?period={period}").json()
    assert variance["pairs"] == 5
    assert variance["estimated"] == "5845.10"
    assert variance["actual"] == "6058.79"
    assert variance["variance"] == "213.69"
    # The duty estimate was taken on the customs value with the estimated freight in it, so the duty
    # moves only by what the dearer real freight adds to that value: 11.79 €, not the freight's whole
    # share of it.
    by_type = {row["key"]: row["variance"] for row in variance["by_cost_type"]}
    assert (by_type["OCEAN_FREIGHT"], by_type["CUSTOMS_DUTY"]) == ("215.40", "11.79")


def test_demo_invoice_arithmetic_holds() -> None:
    """The PDF's own printed total must add up, or the extractor caps confidence and the review
    screen shows a low-confidence banner instead of a clean confirm."""
    assert (
        (
            DEMO_INVOICE_OCEAN_FREIGHT
            + DEMO_INVOICE_THC
            + DEMO_INVOICE_CUSTOMS_BROKERAGE
            + DEMO_INVOICE_DRAYAGE
            + DEMO_INVOICE_CUSTOMS_DUTY
        )
        * Decimal("0.20")
    ).quantize(Decimal("0.01")) == DEMO_INVOICE_VAT
    assert Decimal("7270.55") == DEMO_INVOICE_TOTAL


def test_at_risk_container_stays_at_risk_whatever_today_is(client: TestClient) -> None:
    """The dates are all relative to `now`, so MSCU4821990 must read as demurrage-at-risk today and
    on every future day this is run — see sample_data.py's docstring."""
    res = client.post("/api/v1/organization/sample-data")
    assert res.status_code == 201, res.text
    containers = {c["container_number"]: c for c in client.get("/api/v1/containers").json()}
    # MEDIUM/HIGH/INCURRING all read as "at risk" on screen; only NONE/LOW would break the story.
    assert containers["MSCU4821990"]["dnd_risk"] in ("MEDIUM", "HIGH", "INCURRING")
