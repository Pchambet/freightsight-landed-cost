"""The inbox end to end: upload, review, confirm — and the rule that nothing becomes a cost on its own."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from invoice_fixtures import (
    CREDIT_NOTE_MINUS_GLUED,
    FRENCH_ONE_CONTAINER,
    FRENCH_TWO_CONTAINERS,
    USD_INVOICE,
)
from pdfs import make_pdf, make_pdf_without_text
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Cost, Document, Invoice, Organization
from app.jobs import handlers


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def upload(client: TestClient, content: bytes, filename: str = "facture.pdf"):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/invoices",
        files={"file": (filename, content, "application/pdf")},
    )


def seed_container(client: TestClient, number: str = "MSCU4821990") -> str:
    res = client.post("/api/v1/containers", json={"container_number": number})
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def run_extraction(db: Session, org: Organization, invoice_id: str) -> str:
    """What the worker does, called directly."""
    return handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id))


# ---------------------------------------------------------------------------- upload


def test_an_upload_answers_immediately_and_queues_the_reading(
    client: TestClient, db: Session, org: Organization
) -> None:
    res = upload(client, make_pdf(FRENCH_ONE_CONTAINER))
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "UPLOADED"
    assert body["lines"] == []
    assert body["document_url"] == f"/api/v1/invoices/{body['id']}/document"

    queued = db.execute(
        text("SELECT task_name, args FROM procrastinate_jobs WHERE task_name = 'invoices.extract'")
    ).all()
    assert len(queued) == 1
    assert queued[0].args["invoice_id"] == body["id"]


def test_a_file_that_is_not_a_document_is_refused(client: TestClient, org: Organization) -> None:
    res = upload(client, b"MZ\x90\x00 windows binary", "facture.pdf")
    assert res.status_code == 415
    assert res.json()["code"] == "UNSUPPORTED_DOCUMENT_TYPE"


def test_the_same_file_twice_is_refused_with_the_first_one(client: TestClient, org: Organization) -> None:
    content = make_pdf(FRENCH_ONE_CONTAINER)
    first = upload(client, content)
    assert first.status_code == 201
    second = upload(client, content, "facture-copie.pdf")
    assert second.status_code == 409
    assert second.json()["document_id"] == first.json()["document_id"]


def test_the_document_comes_back_through_the_api(client: TestClient, org: Organization) -> None:
    content = make_pdf(FRENCH_ONE_CONTAINER)
    invoice_id = upload(client, content).json()["id"]
    res = client.get(f"/api/v1/invoices/{invoice_id}/document")
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content == content


# ---------------------------------------------------------------------------- reading


def test_a_read_invoice_waits_for_a_person(client: TestClient, db: Session, org: Organization) -> None:
    seed_container(client)
    invoice_id = upload(client, make_pdf(FRENCH_ONE_CONTAINER)).json()["id"]

    assert run_extraction(db, org, invoice_id) == "NEEDS_REVIEW"

    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert body["status"] == "NEEDS_REVIEW"
    assert body["vendor"] == "TRANSDEMO SAS"
    assert body["invoice_number"] == "FA-2026-0912"
    assert body["total_amount"] == "4596.00"
    # the three figures the document prints, kept apart: the HT is the one it states, not 4596 - 766
    assert body["subtotal_amount"] == "3830.00"
    assert body["vat_amount"] == "766.00"
    assert body["extractor"] == "regex"
    # « regex » and 0.60 are ours; what the screen shows a finance director is the pair beside them
    assert body["extractor_kind"] == "RULES"
    assert body["confidence_band"] == "MEDIUM"
    assert {line["confidence_band"] for line in body["lines"]} == {"MEDIUM"}
    assert len(body["lines"]) == 4
    # nothing is accepted, so nothing can become a cost yet
    assert all(line["accepted"] is False for line in body["lines"])


def test_an_invoice_that_states_no_pre_tax_total_says_so(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Null is not zero. An invoice that prints only a grand total leaves the HT empty rather than
    inventing one, which is what lets the screen show a figure only when the document stated it."""
    seed_container(client)
    invoice_id = upload(client, make_pdf(USD_INVOICE)).json()["id"]
    assert run_extraction(db, org, invoice_id) == "NEEDS_REVIEW"

    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert body["total_amount"] == "4510.00"
    assert body["subtotal_amount"] is None
    assert body["vat_amount"] is None


def test_an_unreadable_scan_fails_with_its_reason(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id = upload(client, make_pdf_without_text(), "scan.pdf").json()["id"]
    assert run_extraction(db, org, invoice_id) == "FAILED"
    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert body["status"] == "FAILED"
    assert "no text layer" in body["error"].lower()
    assert body["lines"] == []


def test_a_retry_reads_the_document_again(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id = upload(client, make_pdf_without_text(), "scan.pdf").json()["id"]
    run_extraction(db, org, invoice_id)

    res = client.post(f"/api/v1/invoices/{invoice_id}/retry")
    assert res.status_code == 200
    assert res.json()["status"] == "UPLOADED"
    assert res.json()["error"] is None
    # The queueing lock is per invoice, so a retry while a reading is still queued adds nothing:
    # one pending job per invoice is exactly what we want.
    queued = db.execute(
        text("SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'invoices.extract'")
    ).scalar()
    assert queued == 1


# ---------------------------------------------------------------------------- reviewing and confirming


def review(client: TestClient, db: Session, org: Organization) -> tuple[str, list[dict]]:  # type: ignore[type-arg]
    container_id = seed_container(client)
    invoice_id = upload(client, make_pdf(FRENCH_ONE_CONTAINER)).json()["id"]
    run_extraction(db, org, invoice_id)
    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert {line["target_id"] for line in body["lines"]} == {container_id}
    return invoice_id, body["lines"]


def test_confirming_nothing_is_refused(client: TestClient, db: Session, org: Organization) -> None:
    """The rule: costs come from acceptances, and there are none."""
    invoice_id, _ = review(client, db, org)
    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "NO_ACCEPTED_LINES"
    assert list(db.scalars(select(Cost))) == []


def test_accepted_lines_become_costs_in_one_go(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id, lines = review(client, db, org)
    for line in lines[:2]:
        res = client.patch(f"/api/v1/invoices/{invoice_id}/lines/{line['id']}", json={"accepted": True})
        assert res.status_code == 200

    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["costs_created"] == 2
    assert body["invoice"]["status"] == "CONFIRMED"

    costs = client.get("/api/v1/costs").json()
    assert len(costs) == 2
    assert {c["cost_type"] for c in costs} == {"OCEAN_FREIGHT", "THC"}
    assert all(c["invoice_number"] == "FA-2026-0912" for c in costs)
    assert all(c["cost_date"] == "2026-03-12" for c in costs)  # the invoice's own date
    # the accepted lines now point at their costs
    after = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert sum(1 for line in after["lines"] if line["cost_id"]) == 2


def test_a_correction_is_what_gets_written(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id, lines = review(client, db, org)
    line = lines[0]
    res = client.patch(
        f"/api/v1/invoices/{invoice_id}/lines/{line['id']}",
        json={"amount": "2950.00", "cost_type": "AIR_FREIGHT", "accepted": True},
    )
    assert res.status_code == 200
    client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    (cost,) = client.get("/api/v1/costs").json()
    assert cost["amount"] == "2950.00"
    assert cost["cost_type"] == "AIR_FREIGHT"


def test_one_bad_line_leaves_no_cost_at_all(client: TestClient, db: Session, org: Organization) -> None:
    """All of them or none: a target that is not ours stops the whole confirmation."""
    invoice_id, lines = review(client, db, org)
    for line in lines[:2]:
        client.patch(f"/api/v1/invoices/{invoice_id}/lines/{line['id']}", json={"accepted": True})
    stranger = lines[2]
    client.patch(
        f"/api/v1/invoices/{invoice_id}/lines/{stranger['id']}",
        json={"accepted": True, "target_id": str(uuid.uuid4())},
    )

    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "UNKNOWN_TARGET"
    assert client.get("/api/v1/costs").json() == []
    assert client.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "NEEDS_REVIEW"


def test_an_accepted_line_without_a_target_is_named(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id = upload(client, make_pdf(FRENCH_TWO_CONTAINERS)).json()["id"]
    run_extraction(db, org, invoice_id)  # no container exists, so no line has a target
    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert body["lines"], body
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{body['lines'][0]['id']}", json={"accepted": True})
    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "LINE_INCOMPLETE"
    assert res.json()["errors"][0]["field"] == "line 1"


def test_a_confirmed_invoice_is_history(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id, lines = review(client, db, org)
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"accepted": True})
    client.post(f"/api/v1/invoices/{invoice_id}/confirm")

    assert client.post(f"/api/v1/invoices/{invoice_id}/confirm").status_code == 422
    assert (
        client.patch(
            f"/api/v1/invoices/{invoice_id}/lines/{lines[1]['id']}", json={"accepted": True}
        ).status_code
        == 404
    )
    assert client.post(f"/api/v1/invoices/{invoice_id}/reject").status_code == 404
    assert (
        client.post(
            f"/api/v1/invoices/{invoice_id}/lines", json={"amount": "50.00", "description": "Frais"}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------- adding what was missed


def test_the_reviewer_can_add_the_line_the_arithmetic_says_is_missing(
    client: TestClient, db: Session, org: Organization
) -> None:
    """« Il manque 220 € » is only worth printing if the screen it prints on can answer it."""
    invoice_id, lines = review(client, db, org)
    container_id = lines[0]["target_id"]

    res = client.post(
        f"/api/v1/invoices/{invoice_id}/lines",
        json={
            "description": "Frais de B/L",
            "amount": "65.00",
            "cost_type": "BL_FEE",
            "scope": "CONTAINER",
            "target_id": container_id,
        },
    )
    assert res.status_code == 201, res.text
    added = res.json()["lines"][-1]
    assert added["line_no"] == len(lines) + 1
    assert added["accepted"] is False  # nothing becomes a cost because it was typed
    assert Decimal(added["confidence"]) == 0
    assert added["confidence_band"] == "LOW"
    assert added["notes"] == "@added_by_human"
    assert added["currency"] == "EUR"  # the invoice's own

    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{added['id']}", json={"accepted": True})
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    (cost,) = client.get("/api/v1/costs").json()
    assert (cost["cost_type"], cost["amount"]) == ("BL_FEE", "65.00")


def test_a_line_added_by_hand_still_needs_a_target(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id, _ = review(client, db, org)
    added = client.post(
        f"/api/v1/invoices/{invoice_id}/lines",
        json={"description": "Frais divers", "amount": "1200.00"},
    ).json()["lines"][-1]
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{added['id']}", json={"accepted": True})
    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "LINE_INCOMPLETE"


# ---------------------------------------------------------------------------- refunds


def credit_note(client: TestClient, db: Session, org: Organization) -> tuple[str, list[dict]]:  # type: ignore[type-arg]
    seed_container(client)
    invoice_id = upload(client, make_pdf(CREDIT_NOTE_MINUS_GLUED), "avoir.pdf").json()["id"]
    run_extraction(db, org, invoice_id)
    return invoice_id, client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]


def test_a_credit_note_is_shown_as_one_and_never_confirmed(
    client: TestClient, db: Session, org: Organization
) -> None:
    """An « AVOIR » of 2 450 € used to be indistinguishable from a bill: positive lines, arithmetic
    holding, confidence untouched. Confirmed, it put a 2 450 € charge where a 2 450 € refund was —
    an error of 4 900 € on the container, and in the customer's stock valuation at the next push."""
    invoice_id, lines = credit_note(client, db, org)
    body = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert body["total_amount"] == "-2940.00"
    assert [line["amount"] for line in lines] == ["-2450.00"]
    assert any(note.startswith("@credit_note|") for note in body["notes"])
    assert body["confidence_band"] in ("LOW", "MEDIUM")

    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"accepted": True})
    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_NOTE_MANUAL"
    assert client.get("/api/v1/costs").json() == []


def test_a_negative_line_cannot_become_a_cost(client: TestClient, db: Session, org: Organization) -> None:
    """The database refuses a negative cost; the refusal has to arrive before that, by name."""
    from app.domain.models import Invoice, InvoiceLine

    invoice_id, lines = review(client, db, org)
    invoice = db.get(Invoice, uuid.UUID(invoice_id))
    assert invoice is not None
    invoice.raw = {"reading": {}, "notes": []}  # not read as a credit note: one discount line
    line = db.get(InvoiceLine, uuid.UUID(lines[0]["id"]))
    assert line is not None
    line.amount, line.accepted = Decimal("-150.00"), True
    db.commit()

    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "NEGATIVE_COST_UNSUPPORTED"
    assert res.json()["errors"][0]["message"] == "@negative_line|-150.00"
    assert client.get("/api/v1/costs").json() == []


def test_rejecting_creates_nothing(client: TestClient, db: Session, org: Organization) -> None:
    invoice_id, lines = review(client, db, org)
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"accepted": True})
    res = client.post(f"/api/v1/invoices/{invoice_id}/reject")
    assert res.status_code == 200
    assert res.json()["status"] == "REJECTED"
    assert client.get("/api/v1/costs").json() == []
    assert client.post(f"/api/v1/invoices/{invoice_id}/confirm").status_code == 422


def test_a_rejected_invoice_can_be_reopened_for_review(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id, lines = review(client, db, org)
    client.post(f"/api/v1/invoices/{invoice_id}/reject")
    reopened = client.post(f"/api/v1/invoices/{invoice_id}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "NEEDS_REVIEW"
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[1]['id']}", json={"accepted": True})
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["costs_created"] == 1


def test_the_list_can_be_filtered_by_status(client: TestClient, db: Session, org: Organization) -> None:
    seed_container(client)
    first = upload(client, make_pdf(FRENCH_ONE_CONTAINER)).json()["id"]
    upload(client, make_pdf(FRENCH_TWO_CONTAINERS), "autre.pdf")
    run_extraction(db, org, first)

    everything = client.get("/api/v1/invoices").json()
    assert len(everything) == 2
    needing = client.get("/api/v1/invoices", params={"invoice_status": "NEEDS_REVIEW"}).json()
    assert [i["id"] for i in needing] == [first]


def test_invoices_are_invisible_to_another_organization(
    client: TestClient, db: Session, org: Organization
) -> None:
    review(client, db, org)
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert len(list(db.scalars(select(Invoice)))) == 1
        assert len(list(db.scalars(select(Document)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(Invoice))) == []
        assert list(db.scalars(select(Document))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)


def test_the_fx_rate_is_the_one_of_the_invoice_date(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id, lines = review(client, db, org)
    client.patch(
        f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}",
        json={"accepted": True, "currency": "USD"},
    )
    client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    (cost,) = client.get("/api/v1/costs").json()
    assert cost["currency"] == "USD"
    assert cost["fx_date"] == "2026-03-12"
    assert Decimal(cost["amount_base"]) == Decimal(cost["amount"]) * Decimal(cost["fx_rate"])


# ---------------------------------------------------------------------------- the duplicate check


def manual_cost(client: TestClient, container_id: str, *, cost_type: str, amount: str) -> str:
    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": cost_type,
            "amount": amount,
            "currency": "EUR",
            "cost_date": "2026-03-10",
            "status": "ACTUAL",
        },
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def test_a_cost_keyed_in_since_the_reading_stops_the_confirmation(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Monday the logistics manager keys in the freight to close the month; the invoice was read on
    Friday, when there was nothing to warn about. Tuesday's confirmation used to put 3 000 € of
    freight on the container twice, because the duplicate check only ever ran at extraction time."""
    invoice_id, lines = review(client, db, org)
    freight = next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT")
    assert "@duplicate_cost" not in (freight["notes"] or "")  # nothing existed when it was read
    manual_cost(client, freight["target_id"], cost_type="OCEAN_FREIGHT", amount="3000.00")
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{freight['id']}", json={"accepted": True})

    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "DUPLICATE_COST"
    assert res.json()["errors"][0]["message"].startswith("@duplicate_cost|OCEAN_FREIGHT|")
    assert len(client.get("/api/v1/costs").json()) == 1  # only the one keyed in by hand
    assert client.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "NEEDS_REVIEW"

    forced = client.post(f"/api/v1/invoices/{invoice_id}/confirm", json={"force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["costs_created"] == 1
    assert forced.json()["notes"][0].startswith("@forced_duplicate|1|OCEAN_FREIGHT|")


# ---------------------------------------------------------------------------- undoing a confirmation


def test_a_confirmation_can_be_taken_back_while_nothing_has_left_for_the_erp(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Nine lines are plain, the tenth waits for the forwarder's answer. Confirming the nine used to
    freeze the invoice for good: the correction had to be keyed in as a cost of its own, unlinked,
    and flagged as a duplicate on the next invoice."""
    invoice_id, lines = review(client, db, org)
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"accepted": True})
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm").json()
    assert confirmed["costs_created"] == 1

    reopened = client.post(f"/api/v1/invoices/{invoice_id}/reopen")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == "NEEDS_REVIEW"
    assert client.get("/api/v1/costs").json() == []  # what it wrote, it took back
    assert all(line["cost_id"] is None for line in reopened.json()["lines"])

    entries = client.get("/api/v1/audit-log", params={"action": "invoice.reopened"}).json()["entries"]
    assert entries[0]["after"]["costs_deleted"] == confirmed["cost_ids"]

    # and the correction is made on the invoice it came from
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"amount": "2950.00"})
    again = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert again.status_code == 200, again.text
    (cost,) = client.get("/api/v1/costs").json()
    assert cost["amount"] == "2950.00"


def test_a_confirmation_already_in_the_customers_erp_is_not_ours_to_undo(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Once a cost has been written into a landed cost over there, deleting it here would leave
    their stock valuation carrying a figure nothing on our side remembers."""
    from app.domain.models import Container, ErpConnection, ErpKind, ErpPush

    invoice_id, lines = review(client, db, org)
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{lines[0]['id']}", json={"accepted": True})
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm").json()
    connection = ErpConnection(
        org_id=org.id,
        kind=ErpKind.ODOO,
        url="https://erp.example.test",
        database="fstest",
        login="admin",
        api_key_sealed=b"sealed",
    )
    db.add(connection)
    db.flush()
    db.add(
        ErpPush(
            org_id=org.id,
            connection_id=connection.id,
            container_id=db.scalars(select(Container)).first().id,  # type: ignore[union-attr]
            cost_ids=confirmed["cost_ids"],
            carried={},
            odoo_model="stock.landed.cost",
            odoo_id=42,
            odoo_name="LC/2026/0042",
            status="done",
        )
    )
    db.commit()

    res = client.post(f"/api/v1/invoices/{invoice_id}/reopen")
    assert res.status_code == 422
    assert res.json()["code"] == "COSTS_PUSHED_TO_ERP"
    assert res.json()["errors"][0]["message"] == "@pushed_cost|LC/2026/0042|done"
    assert len(client.get("/api/v1/costs").json()) == 1  # nothing was taken back
    assert client.get(f"/api/v1/invoices/{invoice_id}").json()["status"] == "CONFIRMED"


# ---------------------------------------------------------------------------- replacing an estimate


def estimate_on(client: TestClient, container_id: str, *, cost_type: str, amount: str) -> str:
    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": cost_type,
            "amount": amount,
            "currency": "EUR",
            "cost_date": "2026-03-01",
            "status": "ESTIMATE",
        },
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


def test_confirming_replaces_the_single_estimate_that_was_waiting(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id, lines = review(client, db, org)
    freight = next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT")
    container_id = freight["target_id"]
    estimate_id = estimate_on(client, container_id, cost_type="OCEAN_FREIGHT", amount="3000.00")
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{freight['id']}", json={"accepted": True})

    res = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert res.status_code == 200, res.text
    assert res.json()["superseded_estimate_ids"] == [estimate_id]

    costs = {c["id"]: c for c in client.get("/api/v1/costs").json()}
    created = costs[res.json()["cost_ids"][0]]
    assert created["supersedes_cost_id"] == estimate_id  # the quote stepped aside for the invoice
    assert created["status"] == "ACTUAL"
    assert costs[estimate_id]["status"] == "ESTIMATE"  # and is still on file, as the comparison


def test_several_estimates_are_left_alone_and_the_confirmation_says_so(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Which of two open estimates an invoice settles is a judgement; guessing would silently drop a
    real estimate out of the landed cost."""
    invoice_id, lines = review(client, db, org)
    freight = next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT")
    container_id = freight["target_id"]
    estimate_on(client, container_id, cost_type="OCEAN_FREIGHT", amount="2000.00")
    estimate_on(client, container_id, cost_type="OCEAN_FREIGHT", amount="1000.00")
    client.patch(f"/api/v1/invoices/{invoice_id}/lines/{freight['id']}", json={"accepted": True})

    body = client.post(f"/api/v1/invoices/{invoice_id}/confirm").json()
    assert body["superseded_estimate_ids"] == []
    # A code the front end words in each language: the lines it came from, how many, of what type.
    assert body["notes"] == [f"@several_estimates|{freight['line_no']}|2|OCEAN_FREIGHT"]

    created = {c["id"]: c for c in client.get("/api/v1/costs").json()}[body["cost_ids"][0]]
    assert created["supersedes_cost_id"] is None  # both estimates are still standing
