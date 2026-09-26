"""Before an audit: what would make it wrong, and what it will not be able to say.

Built from a quarter with nothing missing — which has nothing to say — then one flaw at a time, each
check in both directions: there when the flaw is, absent when it is not. The counts the audit gives
itself agree with the preparation's."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pdfs import make_pdf
from sqlalchemy.orm import Session
from test_imports_costs import commit, ledger, preview, upload

from app.domain.models import Cost, Organization
from app.jobs import handlers

PERIOD = {"period_from": "2026-03-01", "period_to": "2026-03-31"}


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def preparation(client: TestClient, **params: str) -> list[dict[str, Any]]:
    res = client.get("/api/v1/reports/audit/preparation", params={**PERIOD, **params})
    assert res.status_code == 200, res.text
    items: list[dict[str, Any]] = res.json()["items"]
    return items


def codes(client: TestClient) -> list[str]:
    return [item["code"] for item in preparation(client)]


def item(client: TestClient, code: str) -> dict[str, Any]:
    (found,) = [i for i in preparation(client) if i["code"] == code]
    return found


def voyage(client: TestClient, reference: str = "BL-1") -> str:
    body = {"reference": reference, "origin_unlocode": "CNNGB", "destination_unlocode": "FRLEH"}
    return str(client.post("/api/v1/shipments", json=body).json()["id"])


def order(
    client: TestClient, number: str, lines: tuple[tuple[str, str | None], ...], incoterm: str | None = "FOB"
) -> dict[str, Any]:
    """An order of a hundred units at 10 € per line, each with its duty rate (None: none)."""
    body = {
        "po_number": f"PO-{number}",
        "currency": "EUR",
        **({"incoterm": incoterm} if incoterm else {}),
        "lines": [
            {"line_no": n, "sku": sku, "quantity": "100", "unit_price": "10",
             **({"duty_rate": rate} if rate is not None else {})}
            for n, (sku, rate) in enumerate(lines, start=1)
        ],
    }  # fmt: skip
    res = client.post("/api/v1/purchase-orders", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def box(
    client: TestClient,
    number: str,
    *,
    shipment: str | None,
    lines: tuple[tuple[str, str | None], ...] = (("TYRE", "0.045"),),
    incoterm: str | None = "FOB",
    iso_type: str | None = "42G1",
    dated: bool = True,
    returned: bool = True,
    loaded: bool = True,
) -> str:
    """A 40' on the voyage, discharged on 10 March, out on the 12th, back empty on the 15th, carrying a
    fresh order whole — every one of which the arguments can take away."""
    made = order(client, number, lines, incoterm)
    container = client.post("/api/v1/containers", json={"container_number": number, "iso_type": iso_type})
    assert container.status_code == 201, container.text
    box_id = str(container.json()["id"])
    patch: dict[str, Any] = {"shipment_id": shipment} if shipment else {}
    if dated:
        patch |= {"discharged_at": "2026-03-10T08:00:00Z", "gate_out_at": "2026-03-12T08:00:00Z"}
        if returned:
            patch["empty_returned_at"] = "2026-03-15T08:00:00Z"
    if patch:
        assert client.patch(f"/api/v1/containers/{box_id}", json=patch).status_code == 200
    if loaded:
        loads = [{"po_line_id": line["id"], "quantity": "100"} for line in made["lines"]]
        assert client.put(f"/api/v1/containers/{box_id}/loads", json=loads).status_code == 200
    return box_id


def cost(client: TestClient, target: str, cost_type: str, amount: str, **extra: Any) -> dict[str, Any]:
    body = {"scope": "CONTAINER", "target_id": target, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": "2026-03-14", **extra}  # fmt: skip
    res = client.post("/api/v1/costs", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def costed(client: TestClient, box_id: str) -> None:
    """Its freight and the duty its rate explains: 4.5 % of (1 000 + 3 000)."""
    cost(client, box_id, "OCEAN_FREIGHT", "3000.00", vendor="TRANSDEMO", invoice_number="F-1")
    cost(client, box_id, "CUSTOMS_DUTY", "180.00", vendor="DOUANE", invoice_number="D-1")


def priced(client: TestClient, sku: str = "TYRE") -> None:
    body = {"sku": sku, "sale_price": "60.00", "sale_currency": "EUR"}
    assert client.post("/api/v1/products", json=body).status_code == 201


def assumed(client: TestClient) -> None:
    assert client.patch("/api/v1/organization", json={"settings": {"assumed_coefficient": "1.10"}}).is_success


def complete_quarter(client: TestClient) -> tuple[str, str]:
    """One box with nothing missing: dated, loaded, routed, sized, its clocks stopped, its freight and
    duty invoiced, its article priced, the company's coefficient given."""
    shipment = voyage(client)
    box_id = box(client, "MSCU4821990", shipment=shipment)
    costed(client, box_id)
    priced(client)
    assumed(client)
    return shipment, box_id


# ---------------------------------------------------------------------------- nothing missing


def test_a_quarter_with_nothing_missing_has_nothing_to_say(client: TestClient) -> None:
    complete_quarter(client)
    body = client.get("/api/v1/reports/audit/preparation", params=PERIOD).json()
    assert (body["items"], body["base_currency"], body["period_from"]) == ([], "EUR", "2026-03-01")


def test_a_period_that_makes_no_sense_is_refused(client: TestClient) -> None:
    upside_down = {"period_from": "2026-03-31", "period_to": "2026-03-01"}
    res = client.get("/api/v1/reports/audit/preparation", params=upside_down)
    assert (res.status_code, res.json()["code"]) == (422, "PERIOD_INVALID")


# ---------------------------------------------------------------------------- what would make it wrong


def test_boxes_the_audit_would_miscount_are_named_most_urgent_first(client: TestClient) -> None:
    shipment, _ = complete_quarter(client)
    box(client, "TGHU7245081", shipment=None, dated=False)  # in no quarter at all
    empty = box(client, "TCLU1234565", shipment=shipment, loaded=False)
    cost(client, empty, "THC", "250.00")  # spread on nothing
    box(client, "CSQU3054383", shipment=shipment)  # no cost at all: said once, not three times
    no_freight = box(client, "MSKU1234565", shipment=shipment)
    cost(client, no_freight, "CUSTOMS_DUTY", "45.00")
    no_duty = box(client, "HLXU1234564", shipment=shipment)
    cost(client, no_duty, "OCEAN_FREIGHT", "3000.00")
    sold_delivered = box(client, "CMAU6089031", shipment=shipment, incoterm="CIF")  # the seller's freight
    cost(client, sold_delivered, "CUSTOMS_DUTY", "45.00")

    found = {i["code"]: i for i in preparation(client)}
    assert list(found)[:5] == [
        "CONTAINERS_WITHOUT_DATE",
        "CONTAINERS_WITHOUT_GOODS",
        "CONTAINERS_WITHOUT_COST",
        "CONTAINERS_WITHOUT_FREIGHT",
        "CONTAINERS_WITHOUT_DUTY",
    ]
    boxes = {code: [e["label"] for e in found[code]["examples"]] for code in list(found)[:5]}
    assert boxes == {
        "CONTAINERS_WITHOUT_DATE": ["TGHU7245081"],
        "CONTAINERS_WITHOUT_GOODS": ["TCLU1234565"],
        "CONTAINERS_WITHOUT_COST": ["CSQU3054383"],
        "CONTAINERS_WITHOUT_FREIGHT": ["MSKU1234565"],
        "CONTAINERS_WITHOUT_DUTY": ["HLXU1234564"],
    }
    assert found["CONTAINERS_WITHOUT_GOODS"]["amount_base"] == "250.00"
    assert {e["kind"] for e in found["CONTAINERS_WITHOUT_DATE"]["examples"]} == {"container"}
    assert all(found[code]["severity"] == "blocking" for code in boxes)
    # and the audit counts the same boxes without a date
    audit = client.get("/api/v1/reports/audit", params=PERIOD).json()
    assert audit["completeness"]["containers_without_date"] == found["CONTAINERS_WITHOUT_DATE"]["count"] == 1


def test_invoices_not_yet_in_the_books_are_named_by_forwarder_and_number(
    client: TestClient, db: Session, org: Organization
) -> None:
    complete_quarter(client)
    lines = ["TRANSDEMO SAS", "FACTURE N° FA-2026-0912", "Date : 12/03/2026", "Conteneur MSCU4821990",
             "Fret maritime CNNGB / FRLEH                  1          3 000,00  3 000,00",
             "Total HT                                                          3 000,00"]  # fmt: skip
    dropped = client.post("/api/v1/invoices", files={"file": ("a.pdf", make_pdf(lines), "application/pdf")})
    assert dropped.status_code == 201, dropped.text
    assert codes(client) == ["INVOICES_NOT_READ"]

    handlers.extract_invoice(db, org.id, uuid.UUID(dropped.json()["id"]))
    read = item(client, "INVOICES_TO_REVIEW")
    assert (read["count"], read["examples"]) == (
        1,
        [{"kind": "invoice", "id": dropped.json()["id"], "label": "TRANSDEMO SAS FA-2026-0912"}],
    )


def test_what_the_engine_could_not_place_is_said_by_reason_and_a_double_entry_by_its_box(
    client: TestClient, db: Session
) -> None:
    shipment, clean = complete_quarter(client)
    weighed = box(client, "TGHU7245081", shipment=shipment)
    costed(client, weighed)
    cost(client, weighed, "DRAYAGE", "400.00", allocation_method="BY_WEIGHT")  # no line has a weight
    twin = cost(client, clean, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-9")
    cost(client, clean, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-10")
    # the same invoice under a second spelling, written the way older rows were
    row = db.get(Cost, uuid.UUID(twin["id"]))
    assert row is not None
    row.vendor, row.invoice_number = "Transdemo SAS", "F10"
    db.flush()

    unplaced = item(client, "COSTS_UNALLOCATED")
    assert (unplaced["params"], unplaced["amount_base"], unplaced["examples"][0]["label"]) == (
        {"reason": "MISSING_BASIS"},
        "400.00",
        "TGHU7245081",
    )
    twice = item(client, "SAME_INVOICE_TWICE")
    assert (twice["count"], twice["amount_base"], twice["examples"][0]["label"]) == (
        1,
        "260.00",
        "MSCU4821990",
    )
    audit = client.get("/api/v1/reports/audit", params=PERIOD).json()
    assert len([f for f in audit["findings"] if f["code"] == "DOUBLE_ENTRY"]) == twice["count"]


# ---------------------------------------------------------------------------- what a costs file left out


def ledger_file(client: TestClient, *rows: str, name: str = "grand-livre.csv") -> dict[str, Any]:
    """A costs file, previewed and committed as the screen does it."""
    job = upload(client, ledger(*rows), name=name)
    preview(client, job, force_duplicates=True)
    commit(client, job)
    return job


def test_rows_a_costs_file_refused_block_until_a_cost_holds_them(client: TestClient) -> None:
    """The file's rows refused — no box to land on, a label no type reads — are charges no landed cost
    holds: the audit would be short of them. Said until each is held — typed by hand, read from a
    corrected file — or the file is taken back; the same row in the next copy of the ledger is one."""
    _, box_id = complete_quarter(client)
    rows = (
        "12/03/2026;TRANSDEMO;F-3;THC du mois;;285,00;EUR;;;",             # no target, dated in March
        "12/03/2026;TRANSDEMO;F-4;Prestation;;900,00;EUR;MSCU4821990;;",   # a box of March, no type
        "12/03/2026;TRANSDEMO;F-5;THC;;120,00;EUR;MSCU4821990;;",          # written
        "12/05/2026;TRANSDEMO;F-6;Prestation;;50,00;EUR;;;",               # no target, dated in May
    )  # fmt: skip
    job = ledger_file(client, *rows)
    refused = item(client, "COST_ROWS_REFUSED")
    assert (refused["severity"], refused["count"], refused["amount_base"]) == ("blocking", 2, "1185.00")
    assert refused["examples"] == [{"kind": "import", "id": job["id"], "label": "grand-livre.csv"}]

    cost(client, box_id, "INSPECTION", "900.00", vendor="TRANSDEMO", invoice_number="F-4")  # typed by hand
    assert (item(client, "COST_ROWS_REFUSED")["count"], item(client, "COST_ROWS_REFUSED")["amount_base"]) == (
        1,
        "285.00",
    )
    grown = ledger_file(client, *rows, "13/03/2026;TRANSDEMO;F-7;Camionnage;;400,00;EUR;MSCU4821990;;",
                        name="grand-livre-avril.csv")  # fmt: skip
    assert item(client, "COST_ROWS_REFUSED")["count"] == 1  # the same row, in two copies of the ledger

    assert client.delete(f"/api/v1/imports/{grown['id']}").is_success
    assert client.delete(f"/api/v1/imports/{job['id']}").is_success
    assert "COST_ROWS_REFUSED" not in codes(client)


def test_a_refund_is_counted_once_and_one_reversing_a_standing_cost_blocks_until_it_is_out(
    client: TestClient,
) -> None:
    complete_quarter(client)
    ledger_file(client, "12/03/2026;TRANSDEMO;F-5;THC;;285,00;EUR;MSCU4821990;;",
                        "20/03/2026;TRANSDEMO;AV-2;Remise;;-50,00;EUR;MSCU4821990;;")  # fmt: skip
    ledger_file(
        client,
        "12/03/2026;TRANSDEMO;F-5;THC;;285,00;EUR;MSCU4821990;;",
        "20/03/2026;TRANSDEMO;AV-2;Remise;;-50,00;EUR;MSCU4821990;;",
        "21/03/2026;TRANSDEMO;AV-5;THC;;-285,00;EUR;MSCU4821990;;",  # takes back F-5, in the books
        name="grand-livre-avril.csv",
    )
    refunds = item(client, "CREDIT_NOTES_SKIPPED")
    assert (refunds["severity"], refunds["count"], refunds["amount_base"]) == ("limits", 1, "50.00")
    reversing = item(client, "CREDIT_NOTE_REVERSES_COST")
    assert (reversing["severity"], reversing["count"], reversing["amount_base"]) == ("blocking", 1, "285.00")
    assert [e["label"] for e in reversing["examples"]] == ["MSCU4821990"]

    (thc,) = [c for c in client.get("/api/v1/costs").json() if c["invoice_number"] == "F-5"]
    assert client.delete(f"/api/v1/costs/{thc['id']}").status_code == 204  # the charge taken out
    assert "CREDIT_NOTE_REVERSES_COST" not in codes(client)
    assert item(client, "CREDIT_NOTES_SKIPPED")["count"] == 1


def test_a_refund_reversing_a_cost_on_a_box_not_loaded_yet_is_said_at_once(client: TestClient) -> None:
    """The box arrived in the period and nothing is loaded in it yet: its own item says so, and the
    refund of one of its charges is said now too, not once the goods are in."""
    complete_quarter(client)
    empty = box(client, "TGHU7245081", shipment=None, loaded=False)
    ledger_file(client, "12/03/2026;TRANSDEMO;F-5;THC;;285,00;EUR;TGHU7245081;;")
    ledger_file(client, "12/03/2026;TRANSDEMO;F-5;THC;;285,00;EUR;TGHU7245081;;",
                "21/03/2026;TRANSDEMO;AV-5;THC;;-285,00;EUR;TGHU7245081;;", name="avril.csv")  # fmt: skip
    reversing = item(client, "CREDIT_NOTE_REVERSES_COST")
    assert (reversing["count"], reversing["examples"]) == (
        1,
        [{"kind": "container", "id": empty, "label": "TGHU7245081"}],
    )
    assert "CONTAINERS_WITHOUT_GOODS" in codes(client)


def test_import_vat_is_no_money_the_audit_gets_wrong_and_no_cost_of_a_box(client: TestClient) -> None:
    """Recovered, never landed: the import VAT on a box is neither counted in the money an item
    concerns nor a cost that makes a box « costed »."""
    shipment, _ = complete_quarter(client)
    empty = box(client, "TGHU7245081", shipment=None, loaded=False)
    cost(client, empty, "THC", "285.00")
    cost(client, empty, "IMPORT_VAT", "500.00")
    assert item(client, "CONTAINERS_WITHOUT_GOODS")["amount_base"] == "285.00"
    vat_only = box(client, "CSQU3054383", shipment=shipment)
    cost(client, vat_only, "IMPORT_VAT", "500.00")
    assert [e["label"] for e in item(client, "CONTAINERS_WITHOUT_COST")["examples"]] == ["CSQU3054383"]


def test_two_lines_of_one_ledger_invoice_are_not_the_same_invoice_twice(client: TestClient) -> None:
    """The freight in dollars and its surcharge in euros, a handling on the box and one on its bill: one
    invoice of the ledger written as two costs is two lines of one document, never a double entry."""
    shipment, _ = complete_quarter(client)
    box(client, "TGHU7245081", shipment=shipment)
    ledger_file(
        client,
        "12/03/2026;TRANSDEMO;F-9;Fret maritime;;1 000,00;USD;MSCU4821990;;",
        "12/03/2026;TRANSDEMO;F-9;BAF;;200,00;EUR;MSCU4821990;;",
        "12/03/2026;TRANSDEMO;F-7;THC;;285,00;EUR;MSCU4821990;;",
        "12/03/2026;TRANSDEMO;F-7;THC;;100,00;EUR;;BL-1;",
    )
    assert "SAME_INVOICE_TWICE" not in codes(client)
    findings = client.get("/api/v1/reports/audit", params=PERIOD).json()["findings"]
    assert [f for f in findings if f["code"] == "DOUBLE_ENTRY"] == []


# ---------------------------------------------------------------------------- what it will not say


def test_what_the_audit_will_not_be_able_to_say_comes_after_and_names_what_to_fix(client: TestClient) -> None:
    shipment, _ = complete_quarter(client)
    # Two articles at two rates, the duty spread on customs value, and paid far from what they explain.
    mixed = box(client, "TGHU7245081", shipment=shipment, lines=(("TYRE", "0.045"), ("JACK", "0.10")))
    cost(client, mixed, "OCEAN_FREIGHT", "3000.00")
    cost(client, mixed, "CUSTOMS_DUTY", "900.00", allocation_method="BY_CIF_VALUE")
    # A line without a rate in a box that paid duty; a box with no route and no size, still out.
    unrated = box(client, "TCLU1234565", shipment=None, lines=(("MAT", None),), iso_type=None, returned=False)
    cost(client, unrated, "OCEAN_FREIGHT", "3000.00")
    cost(client, unrated, "CUSTOMS_DUTY", "100.00")
    cost(client, unrated, "OTHER", "75.00")
    # An order loaded in part, a cost on an order rather than a box, an estimate still open.
    split = order(client, "SPLIT", (("TYRE", "0.045"),))
    partly = box(client, "CSQU3054383", shipment=shipment, loaded=False)
    loads = [{"po_line_id": split["lines"][0]["id"], "quantity": "40"}]
    assert client.put(f"/api/v1/containers/{partly}/loads", json=loads).status_code == 200
    cost(client, partly, "OCEAN_FREIGHT", "3000.00")
    cost(client, partly, "CUSTOMS_DUTY", "153.00")  # 4.5 % of (400 + 3 000): explained to the cent
    cost(client, split["id"], "BANK_FEES", "15.00", scope="PO")
    cost(client, partly, "THC", "250.00", status="ESTIMATE")

    found = {i["code"]: i for i in preparation(client)}
    assert set(found) == {
        "PO_SPLIT",
        "LINES_WITHOUT_DUTY_RATE",
        "DUTY_GAP",
        "DUTY_SPREAD_ON_CIF",
        "CONTAINERS_WITHOUT_ROUTE",
        "CONTAINERS_WITHOUT_SIZE",
        "COSTS_NOT_ON_CONTAINER",
        "COSTS_TYPED_OTHER",
        "CLOCKS_RUNNING",
        "NO_SALE_PRICE",
        "ESTIMATES_OPEN",
    }
    assert all(i["severity"] == "limits" for i in found.values())
    assert found["PO_SPLIT"]["examples"] == [
        {"kind": "purchase_order", "id": split["id"], "label": "PO-SPLIT"}
    ]
    assert found["LINES_WITHOUT_DUTY_RATE"]["examples"] == [{"kind": "sku", "id": "MAT", "label": "MAT"}]
    assert (found["DUTY_GAP"]["params"], found["DUTY_GAP"]["examples"][0]["label"]) == (
        {"pct": "20"},
        "TGHU7245081",
    )
    assert [e["label"] for e in found["DUTY_SPREAD_ON_CIF"]["examples"]] == ["TGHU7245081"]
    assert [e["label"] for e in found["CONTAINERS_WITHOUT_ROUTE"]["examples"]] == ["TCLU1234565"]
    assert (found["COSTS_NOT_ON_CONTAINER"]["amount_base"], found["COSTS_TYPED_OTHER"]["amount_base"]) == (
        "15.00",
        "75.00",
    )
    assert found["COSTS_NOT_ON_CONTAINER"]["examples"] == [
        {"kind": "purchase_order", "id": split["id"], "label": "PO-SPLIT"}  # where the cost is recorded
    ]
    assert [e["label"] for e in found["CLOCKS_RUNNING"]["examples"]] == ["TCLU1234565"]
    assert {e["id"] for e in found["NO_SALE_PRICE"]["examples"]} == {"JACK", "MAT"}
    assert found["ESTIMATES_OPEN"]["amount_base"] == "250.00"
    audit = client.get("/api/v1/reports/audit", params=PERIOD).json()
    assert audit["headline"]["unpriced_skus"] == found["NO_SALE_PRICE"]["count"]


def test_without_the_company_s_coefficient_the_audit_cannot_give_the_gap(client: TestClient) -> None:
    shipment = voyage(client)
    costed(client, box(client, "MSCU4821990", shipment=shipment))
    priced(client)
    assert codes(client) == ["NO_ASSUMED_COEFFICIENT"]
    assert item(client, "NO_ASSUMED_COEFFICIENT") == {
        "code": "NO_ASSUMED_COEFFICIENT",
        "severity": "limits",
        "section": "margins",
        "count": 1,
        "amount_base": None,
        "params": {},
        "examples": [],
    }


def test_examples_stop_at_five_and_the_count_says_how_many(client: TestClient) -> None:
    complete_quarter(client)
    numbers = ["TGHU7245081", "TCLU1234565", "CSQU3054383", "MSKU1234565", "HLXU1234564", "CMAU6089031"]
    for number in numbers:
        box(client, number, shipment=None, dated=False)
    undated = item(client, "CONTAINERS_WITHOUT_DATE")
    assert (undated["count"], len(undated["examples"])) == (6, 5)
