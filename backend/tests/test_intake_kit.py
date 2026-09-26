"""A prospect's quarter, from its own files to its audit — the pipeline's measurable end.

The kit: the tariff, the orders, the containers' tracking, the costs ledger, and two forwarder PDFs,
one of them already in the ledger. Loaded, the preparation has nothing blocking left, the audit says
what the quarter cost with nothing counted twice, and the same kit loaded in another order gives the
same audit."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pdfs import make_pdf
from previews import commit_previewed
from sqlalchemy.orm import Session

from app.domain.models import Organization
from app.jobs import handlers

KIT = Path(__file__).parent / "fixtures" / "intake"
MARCH = {"period_from": "2026-03-01", "period_to": "2026-03-31"}
ALREADY_IN_THE_LEDGER = [
    "TRANSDEMO SAS",
    "FACTURE N° FA-2026-0912",
    "Date : 12/03/2026",
    "Conteneur MSCU4821994",
    "",
    "Designation                                  Quantite   PU        Montant",
    "Fret maritime CNNGB / FRLEH                  1          3 915,40  3 915,40",
    "THC destination Le Havre                     1          268,00    268,00",
    "Dedouanement import                          1          142,50    142,50",
    "Camionnage Le Havre - Rouen                  1          396,00    396,00",
    "",
    "Total HT                                                          4 721,90",
    "TVA 20%                                                             944,38",
    "Total TTC                                                         5 666,28 EUR",
]
NEW_INVOICE = [
    "TRANSDEMO SAS",
    "FACTURE N° FA-2026-0990",
    "Date : 21/03/2026",
    "Conteneur TGHU7245086",
    "",
    "Designation                                  Quantite   PU        Montant",
    "Visite douane scanner                        1          180,00    180,00",
    "",
    "Total HT                                                          180,00",
    "TVA 20%                                                            36,00",
    "Total TTC                                                         216,00 EUR",
]


def load(client: TestClient, kind: str, name: str) -> dict[str, Any]:
    job = client.post(
        "/api/v1/imports", data={"kind": kind}, files={"file": (name, (KIT / name).read_bytes(), "text/csv")}
    ).json()
    previewed = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    assert previewed.status_code == 200, previewed.text
    assert previewed.json()["report"]["errors"] == [], (name, previewed.json()["report"]["errors"])
    committed = commit_previewed(client, job["id"])
    assert committed.status_code == 200, committed.text
    report: dict[str, Any] = committed.json()["report"]
    return report


def read(client: TestClient, db: Session, org: Organization, text: list[str]) -> str:
    invoice_id = client.post(
        "/api/v1/invoices", files={"file": ("facture.pdf", make_pdf(text), "application/pdf")}
    ).json()["id"]
    assert handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id)) == "NEEDS_REVIEW"
    for line in client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]:
        assert client.patch(
            f"/api/v1/invoices/{invoice_id}/lines/{line['id']}", json={"accepted": True}
        ).is_success
    return str(invoice_id)


def quarter(client: TestClient, db: Session, org: Organization, order: tuple[tuple[str, str], ...]) -> None:
    """The kit, loaded in `order`, then the two PDFs."""
    reports = {kind: load(client, kind, name) for kind, name in order}
    assert reports["CONTAINERS"]["loads_created"] == 4  # three orders, each alone in its box
    assert (reports["COSTS"]["costs_created"], len(reports["COSTS"]["credit_notes_skipped"])) == (9, 1)

    # The PDF of an invoice the ledger already holds: the very lines, to the letter — refused, with no
    # override offered, and set aside.
    copy = read(client, db, org, ALREADY_IN_THE_LEDGER)
    refused = client.post(f"/api/v1/invoices/{copy}/confirm", json={"force": True, "force_recorded": True})
    assert (refused.status_code, refused.json()["code"], refused.json()["forceable"]) == (
        422,
        "INVOICE_ALREADY_RECORDED",
        False,
    )
    assert client.post(f"/api/v1/invoices/{copy}/reject").is_success
    # An invoice the ledger does not have yet: confirmed.
    new = read(client, db, org, NEW_INVOICE)
    confirmed = client.post(f"/api/v1/invoices/{new}/confirm")
    assert confirmed.status_code == 200, confirmed.text


def the_audit(client: TestClient) -> dict[str, Any]:
    """The audit, without the ids that differ between two organizations."""
    body = client.get("/api/v1/reports/audit", params=MARCH).json()
    for finding in body["findings"]:
        finding.pop("container_id")
        finding.pop("cost_ids")
    for line in body["demurrage"]:
        line.pop("container_id")
    body.pop("as_of")
    return dict(body)


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def test_a_prospect_s_quarter_comes_in_whole_and_the_audit_counts_nothing_twice(
    client: TestClient, db: Session, org: Organization
) -> None:
    catalogue_first = (
        ("PRODUCTS", "tarif.csv"),
        ("PURCHASE_ORDERS", "commandes.csv"),
        ("CONTAINERS", "suivi.csv"),
        ("COSTS", "frais.csv"),
    )
    quarter(client, db, org, catalogue_first)

    preparation = client.get("/api/v1/reports/audit/preparation", params=MARCH).json()["items"]
    assert [item["code"] for item in preparation if item["severity"] == "blocking"] == []
    assert {item["code"]: item["count"] for item in preparation} == {
        "CREDIT_NOTES_SKIPPED": 1,  # the refund the ledger holds is set aside, and said
        "NO_ASSUMED_COEFFICIENT": 1,
    }

    audit = the_audit(client)
    assert audit["findings"] == []
    assert audit["completeness"]["containers"] == 2
    # FOB 22 300 + 16 800; costs 3 915,40 + 268 + 142,50 + 396 + 1 320 + 3 800 + 268 + 142,50 + 927
    # + the new invoice's 180: each euro once.
    assert (audit["completeness"]["fob"], audit["completeness"]["landed"]) == ("39100.00", "50459.40")
    margins = {m["sku"]: m for m in audit["margins"]}
    assert set(margins) == {"TYR-20555R16", "MAT-EVA-6040", "JACK-2T"}
    assert margins["JACK-2T"]["sale_price"] == "45.0000"
    assert audit["headline"]["priced_skus"] == 3

    # The same kit, loaded in another order — the orders before the tariff — in another organization.
    other = uuid.uuid4()
    client.headers["X-Org-Id"] = str(other)
    assert client.get("/api/v1/organization").status_code == 200
    second = db.get(Organization, other)
    assert second is not None
    tariff_after_orders = (catalogue_first[1], catalogue_first[0], *catalogue_first[2:])
    quarter(client, db, second, tariff_after_orders)
    assert the_audit(client) == audit
