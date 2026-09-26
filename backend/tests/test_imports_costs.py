"""Reading a costs ledger — the money half of a prospect's quarter. Every rule in both directions: what
is read and on which target, what is refused and why, what is set aside and counted, and that the
same invoice line never lands twice, whatever door it came in by or how often the file is dropped."""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from invoice_fixtures import FRENCH_ONE_CONTAINER
from pdfs import make_pdf
from previews import commit_previewed
from sqlalchemy.orm import Session

from app.domain.boxes import check_digit
from app.domain.models import Cost, Organization
from app.jobs import handlers

HEADER = (
    "Date du coût;Prestataire;N° facture;Libellé;Type de coût;Montant HT;Devise;"
    "N° conteneur;N° B/L;N° commande"
)
THC_F1 = "12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;"
FREIGHT_F1 = "12/03/2026;TRANSDEMO;F-1;Fret maritime;;3 000,00;EUR;MSCU4821994;;"
MARCH = {"period_from": "2026-03-01", "period_to": "2026-03-31"}


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


@pytest.fixture
def world(client: TestClient) -> dict[str, str]:
    """Two boxes on bill BL-A, a third alone on bill BL-C, all discharged in March, one order on the
    first: what a ledger's lines land on."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": "PO-1", "currency": "EUR", "lines": [
            {"line_no": 1, "sku": "TYRE", "quantity": "100", "unit_price": "10", "duty_rate": "0.10"},
            {"line_no": 2, "sku": "JACK", "quantity": "100", "unit_price": "10", "duty_rate": "0"}]},
    ).json()  # fmt: skip
    ids: dict[str, str] = {"PO-1": po["id"]}
    for reference in ("BL-A", "BL-C"):
        ids[reference] = client.post("/api/v1/shipments", json={"reference": reference}).json()["id"]
    for number, bill in (("MSCU4821994", "BL-A"), ("TGHU7245086", "BL-A"), ("CSQU3054383", "BL-C")):
        box = client.post("/api/v1/containers", json={"container_number": number, "iso_type": "42G1"}).json()
        patch = {"shipment_id": ids[bill], "discharged_at": "2026-03-10T12:00:00Z"}
        assert client.patch(f"/api/v1/containers/{box['id']}", json=patch).is_success
        ids[number] = box["id"]
    loads = [{"po_line_id": line["id"], "quantity": "100"} for line in po["lines"]]
    assert client.put(f"/api/v1/containers/{ids['MSCU4821994']}/loads", json=loads).status_code == 200
    return ids


def ledger(*rows: str, header: str = HEADER) -> bytes:
    return ("﻿" + "\n".join([header, *rows]) + "\n").encode()


def upload(client: TestClient, content: bytes, name: str = "grand-livre.csv") -> dict[str, Any]:
    res = client.post("/api/v1/imports", data={"kind": "COSTS"}, files={"file": (name, content, "text/csv")})
    assert res.status_code == 201, res.text
    job: dict[str, Any] = res.json()
    return job


def preview(client: TestClient, job: dict[str, Any], **options: Any) -> dict[str, Any]:
    """The preview's report — also when every row is refused, where it comes with the refusal."""
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"], **options})
    if res.status_code == 422 and res.json()["code"] == "NO_VALID_ROWS":
        report: dict[str, Any] = res.json()["report"]
        return report
    assert res.status_code == 200, res.text
    report = res.json()["report"]
    return report


def commit(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    res = commit_previewed(client, job["id"])
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def by_row(issues: list[dict[str, Any]]) -> dict[int, str]:
    return {issue["row"]: issue["code"] for issue in issues}


def costs_on(client: TestClient, target: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = client.get("/api/v1/costs", params={"target_id": target}).json()
    return found


# ---------------------------------------------------------------------------- what is read


def test_a_ledger_lands_on_its_boxes_bills_and_orders_with_its_types_read_or_inferred(
    client: TestClient, world: dict[str, str]
) -> None:
    job = upload(client, ledger(
        "12/03/2026;TRANSDEMO;F-1;Fret maritime;;3 000,00 €;;MSCU4821994;;",
        "12/03/2026;TRANSDEMO;F-1;THC destination;Manutention portuaire (THC);285,00;EUR;MSCU4821994;;",
        "12/03/2026;TRANSDEMO;F-1;THC destination;Manutention portuaire (THC);285,00;EUR;MSCU4821994;;",
        "13/03/2026;TRANSPORTS LEMAIRE;L-9;Camionnage MSCU4821994 Le Havre;;425,00;EUR;;;",
        "14/03/2026;TRANSDEMO;F-2;Frais de dossier;;45,00;EUR;;BL-C;",
        "14/03/2026;TRANSDEMO;F-2;Frais de dossier;;60,00;EUR;;bl-a;",
        "15/03/2026;DOUANE;D-1;Droits de douane;;120,00;EUR;;;PO-1",
    ))  # fmt: skip
    report = preview(client, job)
    assert (report["errors"], report["valid_rows"], report["costs_created"]) == ([], 7, 6)
    assert by_row(report["warnings"]) == {5: "TARGET_FROM_LABEL"}
    assert report["money"]["total_base"] == "4220.00"
    assert {row["cost_type"]: (row["rows"], row["total_base"]) for row in report["money"]["by_type"]} == {
        "BL_FEE": (2, "105.00"),
        "CUSTOMS_DUTY": (1, "120.00"),
        "DRAYAGE": (1, "425.00"),
        "OCEAN_FREIGHT": (1, "3000.00"),
        "THC": (2, "570.00"),  # two identical lines of one invoice are one cost
    }
    assert report["purchase_orders_created"] is None
    assert costs_on(client, world["MSCU4821994"]) == []  # a preview writes nothing

    committed = commit(client, job)
    assert committed["can_undo"] is True
    on_box = {c["cost_type"]: c for c in costs_on(client, world["MSCU4821994"])}
    assert (on_box["THC"]["amount"], on_box["THC"]["notes"]) == ("570.00", "From grand-livre.csv rows 3, 4")
    alone = costs_on(client, world["CSQU3054383"])  # a bill with one box is that box
    assert [(c["scope"], c["amount"]) for c in alone] == [("CONTAINER", "45.00")]
    shared = costs_on(client, world["BL-A"])  # a bill with two boxes keeps the bill
    assert [(c["scope"], c["amount"]) for c in shared] == [("SHIPMENT", "60.00")]
    assert [c["scope"] for c in costs_on(client, world["PO-1"])] == ["PO"]


def test_what_cannot_be_read_right_is_refused_by_name(client: TestClient, world: dict[str, str]) -> None:
    taxed = upload(client, ledger("12/03/2026;X;F-1;THC;;285,00;EUR;MSCU4821994;;",
                                  header=HEADER.replace("Montant HT", "Montant TTC")))  # fmt: skip
    assert "amount" not in taxed["mapping"]  # never proposed as the amount...
    mapping = {**taxed["mapping"], "amount": "Montant TTC"}  # ...and refused when a person maps it
    res = client.post(f"/api/v1/imports/{taxed['id']}/validate", json={"mapping": mapping})
    assert (res.status_code, res.json()["code"]) == (422, "AMOUNT_INCLUDES_VAT")

    job = upload(client, ledger(
        "12/03/2026;X;F-1;THC;;285,00;EUR;MSKU1234565;;",       # 2: a box nobody has
        "12/03/2026;X;F-2;THC;;285,00;EUR;;BL-Z;",              # 3: a bill nobody has
        "12/03/2026;X;F-3;THC;;285,00;EUR;;;PO-9",              # 4: an order nobody has
        "12/03/2026;X;F-4;THC du mois;;285,00;EUR;;;",          # 5: nothing to land on
        "12/03/2026;X;F-5;Débours;;285,00;EUR;MSCU4821994;;",   # 6: several charges in one
        "12/03/2026;X;F-6;Fret + THC;;285,00;EUR;MSCU4821994;;",
        "12/03/2026;X;F-7;THC;;100,00 $;EUR;MSCU4821994;;",     # 8: dollars in a euro column
        "12/03/2026;X;F-8;THC;;100,00;JPY;MSCU4821994;;",       # 9: no rate for that currency
        "12/03/2026;X;F-9;Prestation spéciale;;80,00;EUR;MSCU4821994;;",
        "12/03/2026;X;F-10;Prestation spéciale;;20,00;EUR;TGHU7245086;;",
        "12/03/2026;X;F-11;THC;Transport express;20,00;EUR;TGHU7245086;;",
    ))  # fmt: skip
    report = preview(client, job)
    assert by_row(report["errors"]) == {
        2: "UNKNOWN_CONTAINER",
        3: "UNKNOWN_SHIPMENT",
        4: "UNKNOWN_ORDER",
        5: "NO_TARGET",
        6: "COST_TYPE_AMBIGUOUS",
        7: "COST_TYPE_AMBIGUOUS",
        8: "AMOUNT_CURRENCY_MISMATCH",
        9: "FX_RATE_UNAVAILABLE",
        10: "COST_TYPE_UNKNOWN",
        11: "COST_TYPE_UNKNOWN",
        12: "COST_TYPE_UNKNOWN",  # a type column that names no type
    }
    assert report["unknown_labels"] == [{"label": "Prestation spéciale", "rows": 2, "total_base": "100.00"}]

    typed = preview(client, job, label_types={"Prestation spéciale": "INSPECTION"})
    assert 10 not in by_row(typed["errors"]) and 11 not in by_row(typed["errors"])
    assert typed["costs_created"] == 2


def test_a_negative_line_cancels_its_twin_or_is_set_aside_and_counted(
    client: TestClient, world: dict[str, str]
) -> None:
    job = upload(client, ledger(
        "12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;",
        "12/03/2026;TRANSDEMO;AV-1;THC;;-285,00;EUR;MSCU4821994;;",  # cancels the line above
        "12/03/2026;TRANSDEMO;AV-2;Remise fret;Fret maritime;(50,00);EUR;MSCU4821994;;",  # a refund
        "12/03/2026;TRANSDEMO;F-3;Fret maritime;;3 000,00;EUR;MSCU4821994;;",
    ))  # fmt: skip
    report = preview(client, job)
    assert by_row(report["errors"]) == {2: "REVERSED_IN_FILE", 3: "REVERSED_IN_FILE"}
    assert report["reversed_in_file"] == 1
    assert report["credit_notes_skipped"] == [
        {"row": 4, "cost_date": "2026-03-12", "vendor": "TRANSDEMO", "invoice_number": "AV-2",
         "amount": "-50.00", "currency": "EUR", "amount_base": "-50.00", "reverses_cost_id": None}
    ]  # fmt: skip
    assert (report["credit_notes_skipped_base"], report["costs_created"]) == ("-50.00", 1)


# ---------------------------------------------------------------------------- the same line never twice


def typed(client: TestClient, target: str, cost_type: str, amount: str, **extra: Any) -> dict[str, Any]:
    body = {"scope": extra.pop("scope", "CONTAINER"), "target_id": target, "cost_type": cost_type,
            "amount": amount, "currency": "EUR", "cost_date": "2026-03-11", **extra}  # fmt: skip
    res = client.post("/api/v1/costs", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def test_what_the_books_already_hold_is_refused_and_a_person_may_overrule_a_neighbour(
    client: TestClient, db: Session, org: Organization, world: dict[str, str]
) -> None:
    typed(
        client, world["MSCU4821994"], "OCEAN_FREIGHT", "3000.00", vendor="Transdemo SAS", invoice_number="F 1"
    )
    typed(client, world["TGHU7245086"], "THC", "250.00", vendor="TERMINAL", invoice_number="T-7")
    waiting = client.post(
        "/api/v1/invoices", files={"file": ("f.pdf", make_pdf(FRENCH_ONE_CONTAINER), "application/pdf")}
    ).json()["id"]
    handlers.extract_invoice(db, org.id, uuid.UUID(waiting))  # FA-2026-0912, waiting in review

    job = upload(client, ledger(
        "12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;",  # the invoice F-1 is in the books
        "12/03/2026;TRANSDEMO;FA-2026-0912;THC;;275,00;EUR;CSQU3054383;;",  # its PDF is in the inbox
        "12/03/2026;TRANSDEMO;F-8;THC;;250,00;EUR;TGHU7245086;;",  # a handling already on that box
    ))  # fmt: skip
    report = preview(client, job)
    assert by_row(report["errors"]) == {
        2: "INVOICE_ALREADY_RECORDED",
        3: "INVOICE_IN_INBOX",
        4: "DUPLICATE_COST",
    }
    assert (report["duplicates"], report["duplicates_base"]) == (3, "810.00")
    overruled = preview(client, job, force_duplicates=True)
    assert by_row(overruled["errors"]) == {2: "INVOICE_ALREADY_RECORDED", 3: "INVOICE_IN_INBOX"}
    commit(client, job)
    page = client.get("/api/v1/audit-log", params={"entity_id": job["id"]}).json()
    assert page["labels"][job["id"]] == "grand-livre.csv"  # the history names the file, not an id
    (entry,) = [e for e in page["entries"] if e["action"] == "import.committed"]
    assert entry["after"]["force_duplicates"] is True and len(entry["after"]["cost_ids"]) == 1


def test_an_estimate_on_the_line_s_target_is_replaced_and_any_other_is_left_to_a_person(
    client: TestClient, world: dict[str, str]
) -> None:
    replaced = typed(client, world["MSCU4821994"], "THC", "250.00", status="ESTIMATE")
    typed(client, world["TGHU7245086"], "DRAYAGE", "400.00", status="ESTIMATE")
    typed(client, world["TGHU7245086"], "DRAYAGE", "420.00", status="ESTIMATE")
    typed(client, world["BL-C"], "OCEAN_FREIGHT", "3000.00", status="ESTIMATE", scope="SHIPMENT")
    job = upload(client, ledger(
        "12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;",
        "12/03/2026;TRANSDEMO;F-2;Camionnage;;410,00;EUR;TGHU7245086;;",
        "12/03/2026;TRANSDEMO;F-3;Fret maritime;;3 100,00;EUR;CSQU3054383;;",
    ))  # fmt: skip
    report = preview(client, job)
    assert by_row(report["errors"]) == {3: "SEVERAL_ESTIMATES", 4: "ESTIMATE_OTHER_SCOPE"}
    assert (report["estimates_replaced"], report["estimates_replaced_base"]) == (1, "250.00")
    commit(client, job)
    (thc,) = [c for c in costs_on(client, world["MSCU4821994"]) if c["status"] == "ACTUAL"]
    assert thc["supersedes_cost_id"] == replaced["id"]


def test_a_preview_binds_its_commit_and_the_same_ledger_is_never_read_twice(
    client: TestClient, world: dict[str, str]
) -> None:
    first = ledger("12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;")
    job = upload(client, first)
    refused = client.post(f"/api/v1/imports/{job['id']}/commit", json={})
    assert (refused.status_code, refused.json()["code"]) == (409, "PREVIEW_REQUIRED")
    preview(client, job)
    commit(client, job)
    again = client.post(
        "/api/v1/imports", data={"kind": "COSTS"}, files={"file": ("g.csv", first, "text/csv")}
    )
    assert (again.status_code, again.json()["code"], again.json()["existing_id"]) == (
        409,
        "FILE_ALREADY_IMPORTED",
        job["id"],
    )

    # The next month's export of the same ledger: what is already in is recognised and left alone.
    grown = upload(
        client,
        ledger(
            "12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;",
            "13/03/2026;TRANSDEMO;F-2;Fret maritime;;3 000,00;EUR;MSCU4821994;;",
        ),
    )
    report = preview(client, grown)
    assert (report["costs_skipped"], report["costs_created"], report["errors"]) == (1, 1, [])
    # Between the preview and the commit, the freight gets keyed in by hand: the same file would now
    # do something else, and the person is shown what before anything is written.
    typed(client, world["MSCU4821994"], "OCEAN_FREIGHT", "3000.00", vendor="TRANSDEMO", invoice_number="F-2")
    changed = commit_previewed(client, grown["id"])
    assert (changed.status_code, changed.json()["code"]) == (409, "PREVIEW_CHANGED")
    assert by_row(changed.json()["report"]["errors"]) == {3: "INVOICE_ALREADY_RECORDED"}
    after = client.get(f"/api/v1/imports/{grown['id']}").json()
    assert after["report"]["costs_created"] == 0
    # The new figures are a new preview: the refusal names it, and committing it is committing them.
    assert changed.json()["preview_key"] == after["preview_key"]


def test_a_costs_file_is_taken_back_whole_and_once(client: TestClient, world: dict[str, str]) -> None:
    estimate = typed(client, world["MSCU4821994"], "THC", "250.00", status="ESTIMATE")
    job = upload(client, ledger(THC_F1, FREIGHT_F1))
    preview(client, job)
    commit(client, job)
    assert [j["can_undo"] for j in client.get("/api/v1/imports").json()] == [True]

    undone = client.delete(f"/api/v1/imports/{job['id']}")
    assert undone.status_code == 200, undone.text
    assert undone.json() == {"costs_deleted": 2, "estimates_reopened": 1}
    assert [c["id"] for c in costs_on(client, world["MSCU4821994"])] == [estimate["id"]]  # standing again
    again = client.delete(f"/api/v1/imports/{job['id']}")
    assert (again.status_code, again.json()["code"]) == (409, "IMPORT_ALREADY_UNDONE")
    assert client.get(f"/api/v1/imports/{job['id']}").json()["can_undo"] is False
    # and the same file can come in again once taken back
    assert upload(client, ledger(THC_F1, FREIGHT_F1))


def test_a_file_that_is_not_costs_is_not_taken_back(client: TestClient, world: dict[str, str]) -> None:
    orders = client.post(
        "/api/v1/imports",
        data={"kind": "PURCHASE_ORDERS"},
        files={"file": ("po.csv", "N° commande;Quantité;Prix unitaire\nPO-9;10;5\n".encode(), "text/csv")},
    ).json()
    client.post(f"/api/v1/imports/{orders['id']}/commit", json={})
    refused = client.delete(f"/api/v1/imports/{orders['id']}")
    assert (refused.status_code, refused.json()["code"]) == (422, "IMPORT_NOT_UNDOABLE")


# ---------------------------------------------------------------------------- the money it makes


def test_the_ledger_s_duty_is_weighed_against_the_customs_value_its_own_freight_makes(
    client: TestClient, world: dict[str, str]
) -> None:
    """Tyres at 10 %, a jack at 0 %, freight 3 000 € and duty 250 € in the same file: 10 % of
    (1 000 + 1 500). Weighed without the file's freight the jack would pay half of it."""
    job = upload(
        client, ledger("15/03/2026;DOUANE;D-1;Droits de douane;;250,00;EUR;MSCU4821994;;", FREIGHT_F1)
    )
    preview(client, job)
    commit(client, job)
    (duty,) = [c for c in costs_on(client, world["MSCU4821994"]) if c["cost_type"] == "CUSTOMS_DUTY"]
    assert duty["allocation_method"] == "BY_THEORETICAL_DUTY"


def test_what_the_audit_finds_on_an_inferred_type_is_a_question(
    client: TestClient, world: dict[str, str]
) -> None:
    """The same handling twice from one forwarder is a fact — unless the type was read in the label:
    then it may be two different charges the label words alike."""
    job = upload(client, ledger("12/03/2026;TRANSDEMO;F-1;THC;;285,00;EUR;MSCU4821994;;",
                                "20/03/2026;TRANSDEMO;F-9;THC;;285,00;EUR;MSCU4821994;;"))  # fmt: skip
    preview(client, job)
    commit(client, job)
    findings = client.get("/api/v1/reports/audit", params=MARCH).json()["findings"]
    (duplicate,) = [f for f in findings if f["code"] == "DUPLICATE_CHARGE"]
    assert (duplicate["confidence"], Decimal(duplicate["amount"])) == ("to_check", Decimal("285.00"))


# ---------------------------------------------------------------------------- VAT, never a cost


def test_vat_is_never_a_cost_whatever_the_label_says_or_the_file_s_default_type(
    client: TestClient, world: dict[str, str]
) -> None:
    """Only the import VAT a forwarder advanced is kept — as such, out of every landed cost. The
    invoice's own VAT is set aside, even typed as a charge by a column; VAT on one line with a charge,
    or an amount with the tax in it, is refused: its part before tax is written nowhere."""
    job = upload(client, ledger(
        "12/03/2026;DOUANE;D-1;TVA sur importation;;500,00;EUR;MSCU4821994;;",       # 2
        "12/03/2026;DOUANE;D-2;Autoliquidation TVA;;400,00;EUR;MSCU4821994;;",       # 3
        "12/03/2026;TRANSDEMO;F-5;TVA 20%;;57,00;EUR;CSQU3054383;;",                 # 4
        "12/03/2026;DOUANE;D-3;Droits de douane et TVA;;700,00;EUR;TGHU7245086;;",   # 5
        "12/03/2026;DOUANE;D-4;Droits + TVA import;;300,00;EUR;TGHU7245086;;",       # 6
        "12/03/2026;TRANSDEMO;F-6;Fret maritime exonéré de TVA;;1 000,00;EUR;CSQU3054383;;",  # 7
        "12/03/2026;TRANSDEMO;F-6;Frais de dossier TVA 20 %;;45,00;EUR;CSQU3054383;;",        # 8
        "12/03/2026;TRANSDEMO;F-7;THC TTC;;342,00;EUR;CSQU3054383;;",                          # 9
        "12/03/2026;TRANSDEMO;F-8;TVA;THC;57,00;EUR;CSQU3054383;;",                            # 10
    ))  # fmt: skip
    for report in (preview(client, job), preview(client, job, default_cost_type="OTHER")):
        assert by_row(report["errors"]) == {
            5: "COST_TYPE_AMBIGUOUS",
            6: "COST_TYPE_AMBIGUOUS",
            9: "AMOUNT_INCLUDES_VAT",
        }
        assert sorted(w["row"] for w in report["warnings"] if w["code"] == "VAT_NOT_A_COST") == [4, 10]
        by_type = {t["cost_type"]: t["total_base"] for t in report["money"]["by_type"]}
        assert by_type == {"IMPORT_VAT": "900.00", "OCEAN_FREIGHT": "1000.00", "BL_FEE": "45.00"}
        assert report["money"]["total_base"] == "1045.00"  # what the file adds to the landed costs
    commit(client, job)
    written = {(c["cost_type"], c["amount"]) for box in ("MSCU4821994", "CSQU3054383", "TGHU7245086")
               for c in costs_on(client, world[box])}  # fmt: skip
    assert not {kind for kind, _ in written} & {"OTHER", "THC", "CUSTOMS_DUTY"}


def test_a_purchase_journal_is_read_by_its_accounts_and_only_its_charges_are_costs(
    client: TestClient, world: dict[str, str]
) -> None:
    """The accounts' own export — the FEC, one line per account — maps itself: the charge in the debit
    of a class 6 account is a cost, its VAT and the supplier's total are set aside, a refund in the
    credit of a charge account is counted."""
    journal = ledger(
        "20260312;TRANSDEMO;F-1;THC MSCU4821994;6241000;285,00;",
        "20260312;TRANSDEMO;F-1;THC MSCU4821994;44566;57,00;",
        "20260312;TRANSDEMO;F-1;THC MSCU4821994;401TRANSI;;342,00",
        "20260320;TRANSDEMO;AV-1;Avoir THC MSCU4821994;6241000;;85,00",
        header="EcritureDate;CompAuxLib;PieceRef;EcritureLib;CompteNum;Debit;Credit",
    )
    job = upload(client, journal)
    assert job["mapping"] == {
        "cost_date": "EcritureDate", "vendor": "CompAuxLib", "invoice_number": "PieceRef",
        "label": "EcritureLib", "account": "CompteNum", "amount": "Debit", "credit": "Credit",
    }  # fmt: skip
    report = preview(client, job)
    assert report["errors"] == []
    set_aside = {w["row"]: (w["code"], w["params"]) for w in report["warnings"] if w["field"] == "account"}
    assert set_aside == {
        3: ("VAT_NOT_A_COST", {"account": "44566"}),
        4: ("NOT_A_CHARGE_ACCOUNT", {"account": "401TRANSI"}),
    }
    assert (report["costs_created"], report["money"]["total_base"]) == (1, "285.00")
    assert [(c["row"], c["amount"]) for c in report["credit_notes_skipped"]] == [(5, "-85.00")]

    # A credit column without the accounts would read every supplier's total as a refund.
    without = {k: v for k, v in job["mapping"].items() if k != "account"}
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": without})
    assert (res.status_code, res.json()["code"], res.json()["one_of"]) == (
        422,
        "MAPPING_INCOMPLETE",
        ["account"],
    )


# ---------------------------------------------------------------------------- refunds


def test_a_refund_of_a_line_already_in_the_books_is_counted_and_names_the_cost_it_reverses(
    client: TestClient, world: dict[str, str]
) -> None:
    """Next month's ledger holds a line the first import wrote, and its reversal. Leaving both out of
    this file would leave the line in the books: the refund is set aside, counted, and names it."""
    first = upload(client, ledger(THC_F1))
    preview(client, first)
    commit(client, first)
    (thc,) = costs_on(client, world["MSCU4821994"])
    typed(client, world["TGHU7245086"], "DRAYAGE", "400.00", vendor="LEMAIRE", invoice_number="L-1")
    grown = upload(client, ledger(
        THC_F1,
        "20/03/2026;TRANSDEMO;AV-1;THC;;-285,00;EUR;MSCU4821994;;",
        "21/03/2026;LEMAIRE;L-1;Camionnage;;400,00;EUR;TGHU7245086;;",   # keyed in by hand already
        "22/03/2026;LEMAIRE;AV-2;Camionnage;;-400,00;EUR;TGHU7245086;;",  # and reversed
        "21/03/2026;TRANSDEMO;F-2;Fret maritime;;3 000,00;EUR;MSCU4821994;;",
    ), name="grand-livre-2.csv")  # fmt: skip
    report = preview(client, grown)
    assert by_row(report["errors"]) == {4: "INVOICE_ALREADY_RECORDED"}
    assert (report["costs_skipped"], report["costs_created"], report["reversed_in_file"]) == (1, 1, 0)
    (drayage,) = costs_on(client, world["TGHU7245086"])
    assert {c["row"]: c["reverses_cost_id"] for c in report["credit_notes_skipped"]} == {
        3: thc["id"],
        5: drayage["id"],
    }


def test_a_refund_from_another_forwarder_cancels_nothing_of_the_first(
    client: TestClient, world: dict[str, str]
) -> None:
    job = upload(client, ledger(
        THC_F1,
        "20/03/2026;TERMINAL;AV-9;THC;;-285,00;EUR;MSCU4821994;;",
        "20/03/2026;;AV-10;THC;;-285,00;EUR;TGHU7245086;;",
        "21/03/2026;TRANSDEMO;F-3;THC;;285,00;EUR;TGHU7245086;;",
    ))  # fmt: skip
    report = preview(client, job)
    # A refund naming no forwarder may cancel anyone's line: rows 4 and 5 go together.
    assert by_row(report["errors"]) == {4: "REVERSED_IN_FILE", 5: "REVERSED_IN_FILE"}
    assert [c["row"] for c in report["credit_notes_skipped"]] == [3]
    assert report["costs_created"] == 1


# ---------------------------------------------------------------------------- what the database would refuse


def test_a_number_of_dashes_is_no_number_and_the_line_already_on_file_is_refused_by_name(
    client: TestClient, world: dict[str, str]
) -> None:
    """Two « - » of one forwarder on one box, on two days, were one invoice line to the database and
    two to every rule: the second write failed. An estimate carrying an invoice's line did the same."""
    dashes = upload(client, ledger(
        "12/03/2026;TRANSDEMO;-;THC;;285,00;EUR;MSCU4821994;;",
        "13/03/2026;TRANSDEMO;-;THC;;100,00;EUR;MSCU4821994;;",
    ))  # fmt: skip
    assert preview(client, dashes, force_duplicates=True)["costs_created"] == 2
    commit(client, dashes)
    assert {c["invoice_number"] for c in costs_on(client, world["MSCU4821994"])} == {None}

    typed(
        client,
        world["TGHU7245086"],
        "THC",
        "250.00",
        status="ESTIMATE",
        vendor="TERMINAL",
        invoice_number="T-1",
    )
    job = upload(client, ledger("12/03/2026;TERMINAL;T-1;THC;;250,00;EUR;TGHU7245086;;"))
    assert by_row(preview(client, job)["errors"]) == {2: "INVOICE_LINE_ALREADY_RECORDED"}
    manual = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": world["CSQU3054383"], "cost_type": "THC", "amount": "10",
              "currency": "EUR", "cost_date": "2026-03-11", "invoice_number": " — "},
    )  # fmt: skip
    assert manual.json()["invoice_number"] is None


def test_a_collision_the_reading_could_not_see_is_said_not_a_server_error(
    client: TestClient, world: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.domain.imports import costs as reading

    monkeypatch.setattr(reading, "_already_in_books", lambda *_: None)  # a rule that misses the line
    typed(client, world["MSCU4821994"], "THC", "285.00", vendor="TRANSDEMO", invoice_number="F-1")
    job = upload(client, ledger(THC_F1))
    res = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]})
    assert (res.status_code, res.json()["code"]) == (409, "INVOICE_LINE_ALREADY_RECORDED")


# ---------------------------------------------------------------------------- targets


def test_a_label_naming_two_boxes_or_a_box_nobody_has_lands_nowhere(
    client: TestClient, world: dict[str, str]
) -> None:
    other = "MSKU123456" + str(check_digit("MSKU123456"))
    job = upload(client, ledger(
        f"12/03/2026;TRANSDEMO;F-1;THC MSCU4821994 {other};;570,00;EUR;;;",
        f"12/03/2026;TRANSDEMO;F-2;THC {other} BL-A;;285,00;EUR;;;",
    ))  # fmt: skip
    report = preview(client, job)
    assert by_row(report["errors"]) == {2: "TARGET_AMBIGUOUS", 3: "UNKNOWN_CONTAINER"}
    (ambiguous,) = [e for e in report["errors"] if e["row"] == 2]
    assert ambiguous["params"]["container_numbers"] == f"MSCU4821994, {other}"


# ---------------------------------------------------------------------------- the next copy of the ledger


def test_a_line_a_person_corrected_is_recognised_in_the_next_copy_of_the_ledger(
    client: TestClient, db: Session, world: dict[str, str]
) -> None:
    """A line without a number, re-typed by hand after the import: the next export of the ledger still
    holds it as it was, and it is the same line — not a new one written again."""
    row = "12/03/2026;TRANSDEMO;;THC;;285,00;EUR;MSCU4821994;;"
    first = upload(client, ledger(row))
    preview(client, first)
    commit(client, first)
    (cost,) = costs_on(client, world["MSCU4821994"])
    assert db.get(Cost, uuid.UUID(cost["id"])).type_inferred is True  # type: ignore[union-attr]
    assert client.patch(f"/api/v1/costs/{cost['id']}", json={"cost_type": "BL_FEE"}).status_code == 200
    db.expire_all()
    assert db.get(Cost, uuid.UUID(cost["id"])).type_inferred is False  # type: ignore[union-attr]  # a person said it
    grown = upload(client, ledger(row, "21/03/2026;TRANSDEMO;F-2;Fret maritime;;3 000,00;EUR;MSCU4821994;;"))
    report = preview(client, grown)
    assert (report["costs_skipped"], report["costs_created"], report["errors"]) == (1, 1, [])
    commit(client, grown)
    assert sorted(c["cost_type"] for c in costs_on(client, world["MSCU4821994"])) == [
        "BL_FEE",
        "OCEAN_FREIGHT",
    ]


def test_a_file_a_later_copy_stands_on_is_taken_back_after_it(
    client: TestClient, world: dict[str, str]
) -> None:
    first = upload(client, ledger(THC_F1))
    preview(client, first)
    commit(client, first)
    grown = upload(client, ledger(THC_F1, FREIGHT_F1.replace("F-1", "F-2")), name="grand-livre-avril.csv")
    preview(client, grown)
    commit(client, grown)

    refused = client.delete(f"/api/v1/imports/{first['id']}")
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert (body["code"], body["files"], body["import_ids"]) == (
        "IMPORT_RELIED_ON",
        ["grand-livre-avril.csv"],
        [grown["id"]],
    )
    assert len(costs_on(client, world["MSCU4821994"])) == 2  # nothing was taken
    assert client.delete(f"/api/v1/imports/{grown['id']}").json() == {
        "costs_deleted": 1,
        "estimates_reopened": 0,
    }
    assert client.delete(f"/api/v1/imports/{first['id']}").json() == {
        "costs_deleted": 1,
        "estimates_reopened": 0,
    }
    assert costs_on(client, world["MSCU4821994"]) == []


# ---------------------------------------------------------------------------- the preview a person saw


def test_the_commit_writes_the_preview_the_person_saw_not_the_last_one_run(
    client: TestClient, world: dict[str, str]
) -> None:
    """Two tabs on one file: the first previews and leaves the duplicates out, the second lets them
    in. The first tab's import writes nothing it did not show: it is shown the other figures."""
    typed(client, world["MSCU4821994"], "THC", "250.00", vendor="TERMINAL", invoice_number="T-7")
    job = upload(client, ledger(THC_F1, FREIGHT_F1))
    first = client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]}).json()
    second = client.post(
        f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"], "force_duplicates": True}
    ).json()
    assert first["preview_key"] != second["preview_key"]

    without = client.post(f"/api/v1/imports/{job['id']}/commit", json={})
    assert (without.status_code, without.json()["code"]) == (409, "PREVIEW_REQUIRED")
    stale = client.post(f"/api/v1/imports/{job['id']}/commit", json={"preview_key": first["preview_key"]})
    assert (stale.status_code, stale.json()["code"]) == (409, "PREVIEW_CHANGED")
    assert (stale.json()["preview_key"], stale.json()["report"]["costs_created"]) == (
        second["preview_key"],
        2,
    )
    assert [c["cost_type"] for c in costs_on(client, world["MSCU4821994"])] == ["THC"]  # nothing written

    done = client.post(
        f"/api/v1/imports/{job['id']}/commit", json={"preview_key": stale.json()["preview_key"]}
    )
    assert done.status_code == 200, done.text
    assert len(costs_on(client, world["MSCU4821994"])) == 3


# ---------------------------------------------------------------------------- amounts


def test_an_amount_is_never_rounded_nor_read_in_another_currency_than_it_says(
    client: TestClient, world: dict[str, str]
) -> None:
    job = upload(client, ledger(
        "12/03/2026;X;F-1;THC;;285.00;EUR;MSCU4821994;;",
        "12/03/2026;X;F-2;THC;;1.234;EUR;TGHU7245086;;",   # three decimals: a thousands separator?
    ))  # fmt: skip
    assert by_row(preview(client, job)["errors"]) == {3: "AMOUNT_PRECISION"}

    header = "Date du coût;Prestataire;N° facture;Libellé;Montant HT (EUR);N° conteneur"
    job = upload(client, ledger("12/03/2026;X;F-1;THC;$100.00;MSCU4821994", header=header))
    job["mapping"]["amount"] = "Montant HT (EUR)"
    (error,) = preview(client, job)["errors"]
    assert (error["code"], error["params"]) == (
        "AMOUNT_CURRENCY_MISMATCH",
        {"amount": "USD", "currency": "EUR"},
    )


def test_lines_of_one_invoice_on_several_days_are_one_cost_dated_and_converted_on_the_first(
    client: TestClient, world: dict[str, str]
) -> None:
    job = upload(client, ledger(
        "16/03/2026;TRANSDEMO;F-1;THC;;100,00;USD;MSCU4821994;;",
        "12/03/2026;TRANSDEMO;F-1;THC;;200,00;USD;MSCU4821994;;",
    ))  # fmt: skip
    preview(client, job)
    commit(client, job)
    (cost,) = costs_on(client, world["MSCU4821994"])
    assert (cost["amount"], cost["cost_date"]) == ("300.00", "2026-03-12")
    assert Decimal(cost["amount_base"]) == (Decimal("300.00") * Decimal(cost["fx_rate"])).quantize(
        Decimal("0.01")
    )


# ---------------------------------------------------------------------------- at the size of a ledger


def test_a_year_of_ledger_is_previewed_committed_and_recognised_within_budget(client: TestClient) -> None:
    """Two thousand lines on forty boxes: a preview, its commit, and next month's export of the same
    ledger — every line recognised — each in seconds, not minutes. The rules read the books once."""
    assert client.get("/api/v1/organization").status_code == 200
    line = {"line_no": 1, "sku": "A", "quantity": "100000", "unit_price": "10", "duty_rate": "0.045"}
    po = client.post(
        "/api/v1/purchase-orders", json={"po_number": "PO-1", "currency": "EUR", "lines": [line]}
    ).json()
    boxes = []
    for n in range(40):
        body = f"MSCU{100000 + n:06d}"
        number = body + str(check_digit(body))
        box = client.post("/api/v1/containers", json={"container_number": number}).json()
        client.patch(f"/api/v1/containers/{box['id']}", json={"discharged_at": "2026-03-10T12:00:00Z"})
        loads = [{"po_line_id": po["lines"][0]["id"], "quantity": "100"}]
        client.put(f"/api/v1/containers/{box['id']}/loads", json=loads)
        boxes.append(number)
    kinds = ["THC", "Fret maritime", "Camionnage", "Dédouanement", "Droits de douane"]
    rows = [
        f"12/03/2026;V{i % 7};F-{i};{kinds[i % 5]};;{100 + i % 50},00;EUR;{boxes[i % 40]};;"
        for i in range(2000)
    ]
    content = ledger(*rows)

    def timed(action: Any) -> tuple[float, Any]:
        started = time.perf_counter()
        result = action()
        return time.perf_counter() - started, result

    job = upload(client, content)
    previewing, report = timed(lambda: preview(client, job, force_duplicates=True))
    committing, _ = timed(lambda: commit(client, job))
    grown = upload(client, ledger(*rows, f"13/03/2026;V1;F-9999;THC;;120,00;EUR;{boxes[0]};;"))
    recognising, again = timed(lambda: preview(client, grown, force_duplicates=True))
    assert (report["costs_created"], again["costs_skipped"], again["costs_created"]) == (2000, 2000, 1)
    measured = {"preview": previewing, "commit": committing, "re-import preview": recognising}
    print("\n" + "\n".join(f"{seconds:6.2f} s  {step}" for step, seconds in measured.items()))
    late = {step: round(seconds, 2) for step, seconds in measured.items() if seconds > 8.0}
    assert not late, f"over budget: {late}"
