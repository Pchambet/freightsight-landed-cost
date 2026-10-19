"""Credits: a refund or a discount lowers the invoiced cost it corrects, and every reduction is kept.

A credit note used to be refused at confirmation with « enter the refund by hand », and nothing could
enter it by hand: a cost cannot be negative. A refund never lowered a landed cost.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from invoice_fixtures import CREDIT_NOTE_MINUS_GLUED, CREDIT_NOTE_POSITIVE_FIGURES, FRENCH_WITH_DISCOUNT
from pdfs import make_pdf
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import CostCredit, Invoice, InvoiceLine, Organization
from app.jobs import handlers

BOX = "MSCU4821990"


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


@pytest.fixture
def box(client: TestClient, org: Organization) -> str:
    """A container of 100 + 300 units at 10 €: a cost on it is split 1 to 3 by value."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={
            "po_number": "PO-1",
            "currency": "EUR",
            "lines": [
                {"line_no": 1, "sku": "A", "quantity": "100", "unit_price": "10"},
                {"line_no": 2, "sku": "B", "quantity": "300", "unit_price": "10"},
            ],
        },
    )
    assert po.status_code == 201, po.text
    container_id = str(client.post("/api/v1/containers", json={"container_number": BOX}).json()["id"])
    loads = [{"po_line_id": line["id"], "quantity": line["quantity"]} for line in po.json()["lines"]]
    assert client.put(f"/api/v1/containers/{container_id}/loads", json=loads).status_code == 200
    return container_id


def charge(
    client: TestClient,
    target: str,
    amount: str = "3000.00",
    *,
    cost_type: str = "OCEAN_FREIGHT",
    currency: str = "EUR",
    number: str | None = "FA-2026-0912",
    status: str = "ACTUAL",
) -> dict[str, Any]:
    res = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": target, "cost_type": cost_type, "amount": amount,
              "currency": currency, "cost_date": "2026-03-12", "vendor": "TRANSDEMO SAS",
              "invoice_number": number, "status": status},
    )  # fmt: skip
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def credit(client: TestClient, cost_id: str, amount: str, number: str | None = "AV-1", **extra: str) -> Any:
    return client.post(
        f"/api/v1/costs/{cost_id}/credits",
        json={"amount": amount, "credit_number": number, "credit_date": "2026-04-02", **extra},
    )


def allocated(client: TestClient, container_id: str) -> dict[str, str]:
    """What the engine put on each article of the container, by SKU."""
    report = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()
    return {line["sku"]: line["allocated"] for line in report["lines"]}


def cost_of(client: TestClient, cost_id: str) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/api/v1/costs/{cost_id}").json()
    return body


# ---------------------------------------------------------------------------- entered by hand


def test_a_refund_entered_by_hand_lowers_the_cost_and_every_figure_built_on_it(
    client: TestClient, box: str
) -> None:
    freight = charge(client, box)
    assert allocated(client, box) == {"A": "750.00", "B": "2250.00"}

    res = credit(client, freight["id"], "400.00", vendor="Transdemo")
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["amount"], body["amount_base"], body["credited"]) == ("2600.00", "2600.00", "400.00")
    (kept,) = body["credits"]
    assert (kept["amount"], kept["credit_number"], kept["vendor"], kept["credit_date"]) == (
        "400.00",
        "AV-1",
        "Transdemo",
        "2026-04-02",
    )
    assert kept["invoice_id"] is None
    # the amount invoiced stays readable: what is left plus what was refunded
    assert Decimal(body["amount"]) + Decimal(body["credited"]) == Decimal("3000.00")
    # the engine reads the cost as it now stands, and so does the container's report
    assert allocated(client, box) == {"A": "650.00", "B": "1950.00"}
    report = client.get(f"/api/v1/landed-costs/containers/{box}").json()
    assert report["totals"]["allocated"] == "2600.00"
    (listed,) = report["costs"]
    assert (listed["credited"], [c["id"] for c in listed["credits"]]) == ("400.00", [kept["id"]])


def test_a_refund_in_a_foreign_currency_keeps_the_cost_s_own_rate(client: TestClient, box: str) -> None:
    freight = charge(client, box, "1000.00", currency="USD")
    assert (freight["fx_rate"], freight["amount_base"]) == ("0.92", "920.00")
    body = credit(client, freight["id"], "250.00").json()
    assert (body["amount"], body["amount_base"], body["fx_rate"]) == ("750.00", "690.00", "0.92")


def test_a_refund_taken_back_gives_the_cost_its_amount_back(client: TestClient, box: str) -> None:
    freight = charge(client, box)
    kept = credit(client, freight["id"], "400.00").json()["credits"][0]

    res = client.delete(f"/api/v1/costs/{freight['id']}/credits/{kept['id']}")
    assert res.status_code == 200, res.text
    assert (res.json()["amount"], res.json()["credited"], res.json()["credits"]) == ("3000.00", "0.00", [])
    assert allocated(client, box) == {"A": "750.00", "B": "2250.00"}
    again = client.delete(f"/api/v1/costs/{freight['id']}/credits/{kept['id']}")
    assert (again.status_code, again.json()["code"]) == (404, "CREDIT_NOT_FOUND")


def test_a_refund_and_its_removal_are_in_the_history_with_raw_values(client: TestClient, box: str) -> None:
    freight = charge(client, box)
    kept = credit(client, freight["id"], "400.00").json()["credits"][0]
    client.delete(f"/api/v1/costs/{freight['id']}/credits/{kept['id']}")

    entries = client.get("/api/v1/audit-log", params={"entity_id": freight["id"]}).json()["entries"]
    removed, credited = entries[0], entries[1]
    assert credited["action"] == "cost.credited"
    # `currency` says what `amount` and `credited` are in: the history used to print them in euros.
    assert credited["before"] == {
        "cost_type": "OCEAN_FREIGHT",
        "amount": "3000.00",
        "currency": "EUR",
        "amount_base": "3000.00",
    }
    assert credited["after"] == {
        "cost_type": "OCEAN_FREIGHT",
        "amount": "2600.00",
        "currency": "EUR",
        "amount_base": "2600.00",
        "credited": "400.00",
        "credit_number": "AV-1",
        "credit_vendor": "TRANSDEMO SAS",  # the cost's forwarder, when none is given
        "credit_date": "2026-04-02",
    }
    assert removed["action"] == "cost.credit_removed"
    assert removed["before"] == credited["after"]
    assert removed["after"] == credited["before"]


def test_a_refund_larger_than_what_the_cost_still_amounts_to_is_refused(client: TestClient, box: str) -> None:
    freight = charge(client, box, "500.00")
    assert credit(client, freight["id"], "300.00").status_code == 201
    res = credit(client, freight["id"], "250.00", number="AV-2")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_EXCEEDS_COST"
    assert res.json()["params"] == {"credit": "250.00", "remaining": "200.00", "currency": "EUR"}
    # all of it is allowed: the cost then amounts to nothing, never less
    assert credit(client, freight["id"], "200.00", number="AV-2").json()["amount"] == "0.00"
    assert allocated(client, box) == {"A": "0.00", "B": "0.00"}


def test_a_refund_in_another_currency_is_refused_not_converted(client: TestClient, box: str) -> None:
    freight = charge(client, box, "1000.00", currency="USD")
    res = credit(client, freight["id"], "100.00", currency="EUR")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_CURRENCY_MISMATCH"
    assert res.json()["params"] == {"credit_currency": "EUR", "cost_currency": "USD"}


def test_an_estimate_is_no_money_paid_and_has_nothing_to_refund(client: TestClient, box: str) -> None:
    estimate = charge(client, box, number=None, status="ESTIMATE")
    res = credit(client, estimate["id"], "100.00")
    assert (res.status_code, res.json()["code"]) == (422, "CREDIT_ON_ESTIMATE")


def test_the_same_credit_note_twice_on_one_cost_is_refused(client: TestClient, box: str) -> None:
    freight = charge(client, box)
    assert credit(client, freight["id"], "100.00", number="AV-0412").status_code == 201
    res = credit(client, freight["id"], "100.00", number="av 0412")
    assert (res.status_code, res.json()["code"]) == (422, "CREDIT_ALREADY_RECORDED")
    assert res.json()["params"] == {"credit_number": "av 0412"}


def test_a_credited_cost_keeps_its_net_amount_until_its_credits_are_taken_back(
    client: TestClient, box: str
) -> None:
    """Typing over the amount of a credited cost would leave the refund reducing a figure it was never
    taken off; deleting it would take the refund with it, unseen."""
    freight = charge(client, box)
    credit(client, freight["id"], "400.00")
    for change in ({"amount": "3000.00"}, {"currency": "USD"}, {"status": "ESTIMATE"}, {"cost_type": "THC"}):
        res = client.patch(f"/api/v1/costs/{freight['id']}", json=change)
        assert (res.status_code, res.json()["code"]) == (422, "COST_HAS_CREDITS"), change
        assert res.json()["params"] == {"credit_number": "AV-1", "count": "1"}
    res = client.delete(f"/api/v1/costs/{freight['id']}")
    assert (res.status_code, res.json()["code"]) == (422, "COST_HAS_CREDITS")
    # what does not touch the amount is still an ordinary edit, the amount it already has included
    assert (
        client.patch(f"/api/v1/costs/{freight['id']}", json={"notes": "remise négociée"}).status_code == 200
    )
    assert client.patch(f"/api/v1/costs/{freight['id']}", json={"amount": "2600.00"}).status_code == 200


def test_a_closed_month_shows_a_refund_as_the_drift_any_late_correction_is(
    client: TestClient, box: str
) -> None:
    """No rule of its own: a refund after the close moves the month's live figure, and the closed
    figure stays — the CFO reads a negative drift naming the cost, as for a cost edited after it."""
    landed = client.patch(f"/api/v1/containers/{box}", json={"discharged_at": "2026-03-10T08:00:00Z"})
    assert landed.status_code == 200, landed.text
    freight = charge(client, box)
    assert client.post("/api/v1/periods/2026-03/close").status_code == 201

    credit(client, freight["id"], "400.00")
    (line,) = client.get("/api/v1/periods/2026-03").json()["drift_lines"]
    assert (line["frozen_landed"], line["live_landed"], line["difference"]) == (
        "7000.00",
        "6600.00",
        "-400.00",
    )
    assert [(c["cost_id"], c["amount_base"]) for c in line["costs_changed"]] == [(freight["id"], "2600.00")]


def test_another_organization_sees_no_credit(
    client: TestClient, db: Session, box: str, org_id: uuid.UUID
) -> None:
    freight = charge(client, box)
    credit(client, freight["id"], "400.00")
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert len(list(db.scalars(select(CostCredit)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(CostCredit))) == []
        set_current_org(db, None)
        assert list(db.scalars(select(CostCredit))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)
    other = client.post(
        f"/api/v1/costs/{freight['id']}/credits",
        headers={"X-Org-Id": str(uuid.uuid4())},
        json={"amount": "1.00", "credit_date": "2026-04-02"},
    )
    assert other.status_code == 404


# ---------------------------------------------------------------------------- read from a credit note


def upload_and_read(client: TestClient, db: Session, org: Organization, lines: list[str]) -> dict[str, Any]:
    invoice_id = client.post(
        "/api/v1/invoices", files={"file": ("avoir.pdf", make_pdf(lines), "application/pdf")}
    ).json()["id"]
    assert handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id)) == "NEEDS_REVIEW"
    body: dict[str, Any] = client.get(f"/api/v1/invoices/{invoice_id}").json()
    return body


def accept(client: TestClient, invoice: dict[str, Any], **extra: Any) -> None:
    for line in invoice["lines"]:
        res = client.patch(
            f"/api/v1/invoices/{invoice['id']}/lines/{line['id']}", json={"accepted": True, **extra}
        )
        assert res.status_code == 200, res.text


def test_a_credit_note_proposes_the_one_cost_it_can_reduce_and_reduces_it_once_accepted(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    freight = charge(client, box)
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    assert note["credit_note"] is True
    (line,) = note["lines"]
    assert (line["amount"], line["refund"], line["credit_cost_id"]) == ("-2450.00", True, None)
    assert [(c["id"], c["amount"], c["invoice_number"]) for c in line["credit_candidates"]] == [
        (freight["id"], "3000.00", "FA-2026-0912")
    ]

    # nothing is taken off before a person accepts the line
    assert cost_of(client, freight["id"])["amount"] == "3000.00"
    accept(client, note)
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["costs_created"], body["credits_created"], body["credited_cost_ids"]) == (
        0,
        1,
        [freight["id"]],
    )
    assert [c["container_number"] for c in body["containers"]] == [BOX]
    assert body["invoice"]["lines"][0]["credit_cost_id"] == freight["id"]

    reduced = cost_of(client, freight["id"])
    assert (reduced["amount"], reduced["credited"]) == ("550.00", "2450.00")
    (kept,) = reduced["credits"]
    assert (kept["credit_number"], kept["vendor"], kept["credit_date"], kept["invoice_id"]) == (
        "A-2026-118",
        "TRANSDEMO SAS",
        "2026-03-14",
        note["id"],
    )
    assert allocated(client, box) == {"A": "137.50", "B": "412.50"}
    # in the history twice: on the cost, and on the credit note's confirmation
    (on_cost,) = [
        e
        for e in client.get("/api/v1/audit-log", params={"entity_id": freight["id"]}).json()["entries"]
        if e["action"] == "cost.credited"
    ]
    assert on_cost["after"]["credited"] == "2450.00"
    (confirmed,) = client.get("/api/v1/audit-log", params={"entity_id": note["id"]}).json()["entries"]
    assert confirmed["after"]["credit_ids"] == [kept["id"]]

    # a credit read from a document is taken back with the document, which says why
    taken = client.delete(f"/api/v1/costs/{freight['id']}/credits/{kept['id']}")
    assert (taken.status_code, taken.json()["code"]) == (422, "CREDIT_FROM_INVOICE")


def test_reopening_a_credit_note_gives_back_what_it_took_off(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    freight = charge(client, box)
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    accept(client, note)
    assert client.post(f"/api/v1/invoices/{note['id']}/confirm").status_code == 200

    res = client.post(f"/api/v1/invoices/{note['id']}/reopen")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "NEEDS_REVIEW"
    # the line keeps the cost it named, for the next confirmation
    assert res.json()["lines"][0]["credit_cost_id"] == freight["id"]
    restored = cost_of(client, freight["id"])
    assert (restored["amount"], restored["credits"]) == ("3000.00", [])
    actions = [
        e["action"]
        for e in client.get("/api/v1/audit-log", params={"entity_id": freight["id"]}).json()["entries"]
    ]
    assert actions[:2] == ["cost.credit_removed", "cost.credited"]


def test_an_invoice_whose_cost_a_credit_has_reduced_is_not_reopened_under_it(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    from invoice_fixtures import FRENCH_ONE_CONTAINER

    bill = upload_and_read(client, db, org, FRENCH_ONE_CONTAINER)
    client.patch(f"/api/v1/invoices/{bill['id']}/lines/{bill['lines'][0]['id']}", json={"accepted": True})
    (cost_id,) = client.post(f"/api/v1/invoices/{bill['id']}/confirm").json()["cost_ids"]
    credit(client, cost_id, "100.00")
    res = client.post(f"/api/v1/invoices/{bill['id']}/reopen")
    assert (res.status_code, res.json()["code"]) == (422, "COST_HAS_CREDITS")
    assert cost_of(client, cost_id)["amount"] == "2900.00"


def test_a_credit_note_with_nothing_to_reduce_is_refused_by_name(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    assert note["lines"][0]["credit_candidates"] == []
    accept(client, note)
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_WITHOUT_COST"
    assert res.json()["params"] == {"line": "1", "cost_type": "OCEAN_FREIGHT", "currency": "EUR"}
    assert client.get(f"/api/v1/invoices/{note['id']}").json()["status"] == "NEEDS_REVIEW"


def test_a_credit_note_against_a_cost_in_another_currency_is_refused(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    charge(client, box, "4000.00", currency="USD")
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    accept(client, note)
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_CURRENCY_MISMATCH"
    assert res.json()["params"] == {"line": "1", "credit_currency": "EUR", "cost_currency": "USD"}


def test_a_credit_note_larger_than_the_cost_is_refused_and_takes_nothing_off(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    freight = charge(client, box, "2000.00")
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    accept(client, note)
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_EXCEEDS_COST"
    assert res.json()["params"] == {
        "credit": "2450.00",
        "remaining": "2000.00",
        "currency": "EUR",
        "line": "1",
    }
    assert (cost_of(client, freight["id"])["amount"], cost_of(client, freight["id"])["credits"]) == (
        "2000.00",
        [],
    )


def test_with_several_costs_to_reduce_a_person_names_the_one(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    first = charge(client, box, "3000.00")
    second = charge(client, box, "2500.00", number="FA-2026-0950")
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    assert {c["id"] for c in note["lines"][0]["credit_candidates"]} == {first["id"], second["id"]}
    accept(client, note)
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 422
    assert res.json()["code"] == "CREDIT_COST_REQUIRED"
    assert res.json()["params"] == {"line": "1", "count": "2"}

    # a cost that is not one of them is refused too: it is not this type on this target
    elsewhere = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"}).json()["id"]
    stray = charge(client, elsewhere, number="FA-2026-0999")
    accept(client, note, credit_cost_id=stray["id"])
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert (res.json()["code"], res.json()["params"]) == ("CREDIT_COST_MISMATCH", {"line": "1"})

    accept(client, note, credit_cost_id=second["id"])
    assert client.post(f"/api/v1/invoices/{note['id']}/confirm").status_code == 200
    assert (cost_of(client, first["id"])["amount"], cost_of(client, second["id"])["amount"]) == (
        "3000.00",
        "50.00",
    )


def test_a_refund_line_keeps_its_minus_sign_when_a_person_corrects_it(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """The review screen sends a line back whole — its amount with its sign — whatever a person
    changed on it: choosing the cost a refund reduces must not be refused for the minus it carries."""
    charge(client, box, "3000.00")
    second = charge(client, box, "2500.00", number="FA-2026-0950")
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    (line,) = note["lines"]
    whole = {k: line[k] for k in ("amount", "currency", "cost_type", "scope", "target_id")}
    res = client.patch(
        f"/api/v1/invoices/{note['id']}/lines/{line['id']}",
        json={**whole, "accepted": True, "credit_cost_id": second["id"]},
    )
    assert res.status_code == 200, res.text
    assert client.post(f"/api/v1/invoices/{note['id']}/confirm").status_code == 200
    assert cost_of(client, second["id"])["amount"] == "50.00"

    # a line of nothing is still no line
    zero = client.patch(f"/api/v1/invoices/{note['id']}/lines/{line['id']}", json={"amount": "0.00"})
    assert zero.status_code == 422


def test_a_refund_line_the_reading_missed_is_added_with_its_minus_sign(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """A credit note printed with minus signs: the line the reading missed is typed as it is printed,
    and the refund is the sum of both, not one netted against the other."""
    freight = charge(client, box, "3000.00")
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    added = client.post(
        f"/api/v1/invoices/{note['id']}/lines",
        json={"description": "Remise fret", "amount": "-300.00", "cost_type": "OCEAN_FREIGHT",
              "scope": "CONTAINER", "target_id": box},
    )  # fmt: skip
    assert added.status_code == 201, added.text
    accept(client, client.get(f"/api/v1/invoices/{note['id']}").json())
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 200, res.text
    assert (cost_of(client, freight["id"])["amount"], cost_of(client, freight["id"])["credited"]) == (
        "250.00",
        "2750.00",
    )


def test_a_credit_note_printed_with_positive_figures_is_still_a_refund(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    demurrage = charge(client, box, "600.00", cost_type="DEMURRAGE")
    note = upload_and_read(client, db, org, CREDIT_NOTE_POSITIVE_FIGURES)
    (line,) = note["lines"]
    assert (line["amount"], line["cost_type"], line["refund"]) == ("360.00", "DEMURRAGE", True)
    accept(client, note)
    # a line a person added and left out — a rounding — does not turn the document's figures round
    client.post(f"/api/v1/invoices/{note['id']}/lines", json={"description": "Arrondi", "amount": "0.01"})
    invoice = db.get(Invoice, uuid.UUID(note["id"]))
    assert invoice is not None
    invoice.lines[-1].amount = Decimal("-0.01")
    db.commit()
    assert client.post(f"/api/v1/invoices/{note['id']}/confirm").status_code == 200
    assert cost_of(client, demurrage["id"])["amount"] == "240.00"


def test_choosing_another_target_forgets_the_cost_a_refund_line_named(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    freight = charge(client, box)
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    line = note["lines"][0]
    url = f"/api/v1/invoices/{note['id']}/lines/{line['id']}"
    assert (
        client.patch(url, json={"credit_cost_id": freight["id"]}).json()["lines"][0]["credit_cost_id"]
        == freight["id"]
    )
    # the same values sent again — what the review screen does on every save — change nothing
    same = client.patch(url, json={"scope": "CONTAINER", "target_id": box, "currency": "EUR"})
    assert same.json()["lines"][0]["credit_cost_id"] == freight["id"]
    moved = client.patch(url, json={"cost_type": "THC"})
    assert moved.json()["lines"][0]["credit_cost_id"] is None


def test_a_refund_line_cannot_name_a_cost_that_is_not_on_file(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """A cost deleted while the review screen was open, or an id that is not this organization's: the
    line is refused by name, not left to the database's foreign key as a server error."""
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    line = note["lines"][0]
    gone = str(uuid.uuid4())
    res = client.patch(f"/api/v1/invoices/{note['id']}/lines/{line['id']}", json={"credit_cost_id": gone})
    assert (res.status_code, res.json()["code"], res.json()["params"]) == (
        422,
        "CREDIT_COST_MISMATCH",
        {"line": "1"},
    )
    batch = client.patch(
        f"/api/v1/invoices/{note['id']}/lines", json={"lines": [{"id": line["id"], "credit_cost_id": gone}]}
    )
    assert (batch.status_code, batch.json()["code"]) == (422, "CREDIT_COST_MISMATCH")
    assert client.get(f"/api/v1/invoices/{note['id']}").json()["lines"][0]["credit_cost_id"] is None


def test_two_currencies_never_add_up_on_one_cost(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """A refund in dollars named on a cost in euros would be taken off it as euros."""
    freight = charge(client, box)
    note = upload_and_read(client, db, org, CREDIT_NOTE_MINUS_GLUED)
    added = client.post(
        f"/api/v1/invoices/{note['id']}/lines",
        json={"description": "Fret USD", "amount": "100.00", "currency": "USD", "cost_type": "OCEAN_FREIGHT",
              "scope": "CONTAINER", "target_id": box},
    ).json()["lines"][-1]  # fmt: skip
    line = db.get(InvoiceLine, uuid.UUID(added["id"]))
    assert line is not None
    line.amount = Decimal("-100.00")
    db.commit()
    accept(client, client.get(f"/api/v1/invoices/{note['id']}").json(), credit_cost_id=freight["id"])
    res = client.post(f"/api/v1/invoices/{note['id']}/confirm")
    assert res.status_code == 422
    assert (res.json()["code"], res.json()["params"]) == (
        "CREDIT_CURRENCY_MISMATCH",
        {"line": "2", "credit_currency": "USD", "cost_currency": "EUR"},
    )
    assert cost_of(client, freight["id"])["amount"] == "3000.00"


def test_a_line_corrected_into_a_charge_forgets_the_cost_it_named(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """Read as -2 450 with a cost named, then corrected to +2 450 on a bill: it is a charge, and is
    shown and confirmed as one."""
    freight = charge(client, box)
    from invoice_fixtures import FRENCH_ONE_CONTAINER

    bill = upload_and_read(client, db, org, FRENCH_ONE_CONTAINER)
    invoice = db.get(Invoice, uuid.UUID(bill["id"]))
    assert invoice is not None
    invoice.invoice_number = "FA-2026-1001"
    first = db.get(InvoiceLine, uuid.UUID(bill["lines"][0]["id"]))
    assert first is not None
    first.amount, first.credit_cost_id = Decimal("-150.00"), uuid.UUID(freight["id"])
    db.commit()
    url = f"/api/v1/invoices/{bill['id']}/lines/{first.id}"
    assert client.get(f"/api/v1/invoices/{bill['id']}").json()["lines"][0]["refund"] is True
    corrected = client.patch(url, json={"amount": "150.00", "accepted": True}).json()["lines"][0]
    assert corrected["refund"] is False
    res = client.post(f"/api/v1/invoices/{bill['id']}/confirm", json={"force": True})
    assert res.status_code == 200, res.text
    assert (res.json()["costs_created"], res.json()["credits_created"]) == (1, 0)
    assert res.json()["invoice"]["lines"][0]["credit_cost_id"] is None
    assert cost_of(client, freight["id"])["amount"] == "3000.00"


# ---------------------------------------------------------------------------- refunds inside an invoice


def test_a_discount_next_to_its_charge_only_lowers_that_charge(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    bill = upload_and_read(client, db, org, FRENCH_WITH_DISCOUNT)
    _, discount = bill["lines"]
    assert (discount["amount"], discount["cost_type"]) == ("-150.00", None)
    client.patch(f"/api/v1/invoices/{bill['id']}/lines/{discount['id']}", json={"cost_type": "OCEAN_FREIGHT"})
    accept(client, bill)
    assert all(not ln["refund"] for ln in client.get(f"/api/v1/invoices/{bill['id']}").json()["lines"])

    res = client.post(f"/api/v1/invoices/{bill['id']}/confirm")
    assert res.status_code == 200, res.text
    assert (res.json()["costs_created"], res.json()["credits_created"]) == (1, 0)
    (cost,) = client.get("/api/v1/costs").json()
    assert (cost["amount"], cost["credits"]) == ("2300.00", [])
    assert {ln["cost_id"] for ln in res.json()["invoice"]["lines"]} == {cost["id"]}


def test_a_negative_line_with_nothing_to_net_against_reduces_the_cost_on_file(
    client: TestClient, db: Session, org: Organization, box: str
) -> None:
    """A late discount printed on the next invoice: nothing of its type on it to lower, so it lowers
    the charge already on file — accepted by a person, on the cost proposed."""
    freight = charge(client, box)
    from invoice_fixtures import FRENCH_ONE_CONTAINER

    bill = upload_and_read(client, db, org, FRENCH_ONE_CONTAINER)
    invoice = db.get(Invoice, uuid.UUID(bill["id"]))
    assert invoice is not None
    invoice.invoice_number = "FA-2026-1001"
    first = db.get(InvoiceLine, uuid.UUID(bill["lines"][0]["id"]))
    assert first is not None and first.cost_type is not None
    first.amount = Decimal("-150.00")  # the ocean freight line, as a discount
    db.commit()

    body = client.get(f"/api/v1/invoices/{bill['id']}").json()
    refund, *charges = body["lines"]
    assert (refund["refund"], [c["id"] for c in refund["credit_candidates"]]) == (True, [freight["id"]])
    assert not any(line["refund"] for line in charges)
    accept(client, body)
    res = client.post(f"/api/v1/invoices/{bill['id']}/confirm")
    assert res.status_code == 200, res.text
    assert (res.json()["costs_created"], res.json()["credits_created"]) == (3, 1)
    reduced = cost_of(client, freight["id"])
    assert (reduced["amount"], reduced["credits"][0]["credit_number"]) == ("2850.00", "FA-2026-1001")


# ---------------------------------------------------------------------------- the other doors


def test_a_credited_line_keyed_in_again_on_its_order_is_still_the_line_on_file(
    client: TestClient, box: str
) -> None:
    """The guard against one invoice line counted twice compares the amount invoiced. A refund lowers
    what the cost amounts to, never what was invoiced: the same line typed again at the other end of
    the same order is the same 3 000 € charge, refused by name."""
    (listed,) = client.get("/api/v1/purchase-orders").json()
    po = client.get(f"/api/v1/purchase-orders/{listed['id']}").json()
    on_order = client.post(
        "/api/v1/costs",
        json={"scope": "PO", "target_id": po["id"], "cost_type": "OCEAN_FREIGHT", "amount": "3000.00",
              "currency": "EUR", "cost_date": "2026-03-12", "vendor": "TRANSDEMO SAS",
              "invoice_number": "FA-2026-0912"},
    ).json()  # fmt: skip
    assert credit(client, on_order["id"], "400.00").status_code == 201
    again = client.post(
        "/api/v1/costs",
        json={"scope": "PO_LINE", "target_id": po["lines"][0]["id"], "cost_type": "OCEAN_FREIGHT",
              "amount": "3000.00", "currency": "EUR", "cost_date": "2026-03-12", "vendor": "Transdemo",
              "invoice_number": "FA 2026 0912"},
    )  # fmt: skip
    assert (again.status_code, again.json()["code"]) == (422, "INVOICE_LINE_ALREADY_RECORDED")


def test_a_costs_file_whose_cost_a_credit_has_reduced_is_not_taken_back_under_it(
    client: TestClient, box: str
) -> None:
    """Taking a ledger back deletes its costs: a credit on one would go with it, out of the history
    and off the credit note that took it. Refused, as deleting the cost by hand is."""
    from test_imports_costs import commit, ledger, preview, upload

    job = upload(client, ledger(f"12/03/2026;TRANSDEMO;F-1;Fret maritime;;3 000,00;EUR;{BOX};;"))
    preview(client, job)
    commit(client, job)
    (freight,) = client.get("/api/v1/costs", params={"target_id": box}).json()
    assert credit(client, freight["id"], "400.00").status_code == 201

    res = client.delete(f"/api/v1/imports/{job['id']}")
    assert (res.status_code, res.json()["code"]) == (422, "COST_HAS_CREDITS")
    assert res.json()["params"] == {"credit_number": "AV-1", "count": "1"}
    assert (cost_of(client, freight["id"])["amount"], len(cost_of(client, freight["id"])["credits"])) == (
        "2600.00",
        1,
    )
