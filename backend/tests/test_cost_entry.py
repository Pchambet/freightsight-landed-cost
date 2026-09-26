"""A cost comes in by several doors — typed by hand, confirmed from an invoice, estimated from the rate
cards — and lands the same way through each: the same duty method, one cost per charge and target, and
the same invoice never twice, whatever spelling each copy came with."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from invoice_fixtures import FRENCH_ONE_CONTAINER, FRENCH_TWO_CONTAINERS
from pdfs import make_pdf
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.domain.costing.entry import (
    books_lock_key,
    lock_books,
    normalize_invoice_number,
    normalize_vendor,
    same_invoice,
)
from app.domain.models import Organization
from app.jobs import handlers

DUTY = "CUSTOMS_DUTY"


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def container_with(client: TestClient, number: str, lines: Sequence[tuple[str, str | None]]) -> str:
    """A container discharged in March holding one order: a hundred units worth 1 000 € per line, each
    with its duty rate (None: the line has none)."""
    body = {
        "po_number": f"PO-{number}",
        "currency": "EUR",
        "lines": [
            {"line_no": n, "sku": sku, "quantity": "100", "unit_price": "10",
             **({"duty_rate": rate} if rate is not None else {})}
            for n, (sku, rate) in enumerate(lines, start=1)
        ],
    }  # fmt: skip
    po = client.post("/api/v1/purchase-orders", json=body).json()
    box = client.post("/api/v1/containers", json={"container_number": number}).json()
    assert client.patch(
        f"/api/v1/containers/{box['id']}", json={"discharged_at": "2026-03-10T08:00:00Z"}
    ).is_success
    loads = [{"po_line_id": line["id"], "quantity": "100"} for line in po["lines"]]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
    return str(box["id"])


def duty_by_sku(client: TestClient, box: str) -> dict[str, Decimal]:
    report = client.get(f"/api/v1/landed-costs/containers/{box}").json()
    return {line["sku"]: Decimal(line["by_cost_type"].get(DUTY, "0")) for line in report["lines"]}


def typed(client: TestClient, box: str, cost_type: str, amount: str, **extra: Any) -> dict[str, Any]:
    body = {"scope": "CONTAINER", "target_id": box, "cost_type": cost_type, "amount": amount,
            "currency": "EUR", "cost_date": "2026-03-12", **extra}  # fmt: skip
    res = client.post("/api/v1/costs", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def read_invoice(
    client: TestClient, db: Session, org: Organization, text: list[str]
) -> tuple[str, list[dict[str, Any]]]:
    invoice_id = client.post(
        "/api/v1/invoices", files={"file": ("facture.pdf", make_pdf(text), "application/pdf")}
    ).json()["id"]
    assert handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id)) == "NEEDS_REVIEW"
    lines: list[dict[str, Any]] = client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]
    return invoice_id, lines


def accept(client: TestClient, invoice_id: str, line: dict[str, Any], **patch: Any) -> None:
    res = client.patch(f"/api/v1/invoices/{invoice_id}/lines/{line['id']}", json={"accepted": True, **patch})
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------- the duty method


def test_a_jack_at_zero_per_cent_pays_no_duty_whichever_door_the_duty_came_in_by(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Tyres at 4.5 % and a jack at 0 % in one box: customs took 4.5 % of the tyres. Spread by customs
    value the jack would pay half of it. Typed, estimated or confirmed, it now pays nothing."""
    typed_box = container_with(client, "TCLU1234565", [("TYRE", "0.045"), ("JACK", "0")])
    assert typed(client, typed_box, DUTY, "45.00")["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert duty_by_sku(client, typed_box) == {"TYRE": Decimal("45.00"), "JACK": Decimal("0.00")}

    estimated_box = container_with(client, "TGHU7245081", [("TYRE", "0.045"), ("JACK", "0")])
    estimates = client.post(f"/api/v1/containers/{estimated_box}/estimates").json()["created"]
    (estimate,) = [e for e in estimates if e["cost_type"] == DUTY]
    assert (estimate["amount"], estimate["allocation_method"]) == ("45.00", "BY_THEORETICAL_DUTY")
    assert duty_by_sku(client, estimated_box)["JACK"] == Decimal("0.00")

    confirmed_box = container_with(client, "MSCU4821990", [("TYRE", "0.045"), ("JACK", "0")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    clearance = next(line for line in lines if line["description"].startswith("Dedouanement"))
    accept(client, invoice_id, clearance, cost_type=DUTY, amount="45.00")
    assert client.post(f"/api/v1/invoices/{invoice_id}/confirm").status_code == 200
    on_box = client.get("/api/v1/costs", params={"target_id": confirmed_box}).json()
    (duty,) = [c for c in on_box if c["cost_type"] == DUTY]
    assert duty["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert duty_by_sku(client, confirmed_box) == {"TYRE": Decimal("45.00"), "JACK": Decimal("0.00")}


def test_rates_that_are_all_zero_leave_the_duty_spread_by_customs_value(client: TestClient) -> None:
    """Nothing to spread on: theoretical duty would leave the whole duty unallocated."""
    box = container_with(client, "TCLU1234565", [("A", "0"), ("B", "0")])
    assert typed(client, box, DUTY, "50.00")["allocation_method"] == "BY_CIF_VALUE"
    assert duty_by_sku(client, box) == {"A": Decimal("25.00"), "B": Decimal("25.00")}


def test_default_zeros_that_do_not_explain_the_duty_paid_are_not_trusted_to_split_it(
    client: TestClient,
) -> None:
    """An ERP that writes 0 % on every product: nine lines at 0 %, one at 4.5 %, and customs took 450 €
    — ten times what the rates explain. By theoretical duty the one rated line would carry all 450."""
    box = container_with(client, "TCLU1234565", [(f"L{n}", "0") for n in range(9)] + [("RATED", "0.045")])
    assert typed(client, box, DUTY, "450.00")["allocation_method"] == "BY_CIF_VALUE"
    assert set(duty_by_sku(client, box).values()) == {Decimal("45.00")}
    # and within the guard the rates are trusted again
    near = container_with(client, "TGHU7245081", [("TYRE", "0.045"), ("JACK", "0")])
    assert typed(client, near, DUTY, "52.00")["allocation_method"] == "BY_THEORETICAL_DUTY"  # 45 + 15.6 %


def test_an_invoice_s_duty_is_weighed_against_the_customs_value_its_own_freight_makes(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Duty 250 € printed above freight 3 000 € on one invoice. The customs value includes the
    freight: 10 % of (1 000 + 1 500) is 250, the rates explain the duty and the jack at 0 % pays none.
    Weighed before the freight, 250 against 100 would look unexplained, and the jack would pay half."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.10"), ("JACK", "0")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    by_type = {line["cost_type"]: line for line in lines}
    accept(client, invoice_id, by_type["OCEAN_FREIGHT"], cost_type=DUTY, amount="250.00")  # line 1
    accept(client, invoice_id, by_type["DRAYAGE"], cost_type="OCEAN_FREIGHT", amount="3000.00")  # line 4
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text

    on_box = client.get("/api/v1/costs", params={"target_id": box}).json()
    (duty,) = [c for c in on_box if c["cost_type"] == DUTY]
    assert duty["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert duty_by_sku(client, box) == {"TYRE": Decimal("250.00"), "JACK": Decimal("0.00")}


def test_a_duty_billed_in_two_goes_is_weighed_with_what_was_already_paid(client: TestClient) -> None:
    """Customs bill a box's duty in two goes, 100 € then 20 €: the rates explain the 120 € paid in all.
    The second, weighed alone against 120 €, would look unexplained and be spread onto the jack."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.12"), ("JACK", "0")])
    assert typed(client, box, DUTY, "100.00")["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert typed(client, box, DUTY, "20.00")["allocation_method"] == "BY_THEORETICAL_DUTY"
    assert duty_by_sku(client, box) == {"TYRE": Decimal("120.00"), "JACK": Decimal("0.00")}


def test_a_cost_retyped_to_or_from_duty_takes_the_method_of_what_it_became(client: TestClient) -> None:
    """A duty typed as freight by mistake and corrected, or the other way round: the method was the old
    type's, and a freight spread by duty rates loads the whole of it onto the rated line."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045"), ("JACK", "0")])
    duty = typed(client, box, DUTY, "45.00")
    assert duty["allocation_method"] == "BY_THEORETICAL_DUTY"

    as_freight = client.patch(f"/api/v1/costs/{duty['id']}", json={"cost_type": "OCEAN_FREIGHT"})
    assert as_freight.status_code == 200, as_freight.text
    assert as_freight.json()["allocation_method"] == "BY_VALUE"
    back = client.patch(f"/api/v1/costs/{duty['id']}", json={"cost_type": DUTY})
    assert back.status_code == 200, back.text
    assert back.json()["allocation_method"] == "BY_THEORETICAL_DUTY"

    # A method somebody chose stays theirs when the new type leaves it valid.
    counted = typed(client, box, "THC", "100.00", allocation_method="BY_QUANTITY")
    retyped = client.patch(f"/api/v1/costs/{counted['id']}", json={"cost_type": "DRAYAGE"})
    assert retyped.json()["allocation_method"] == "BY_QUANTITY"


# ---------------------------------------------------------------------------- one invoice, many lines


def test_a_freight_and_its_bunker_surcharge_on_one_invoice_are_one_cost(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Two lines of one type on one container: it was a database error, a 500 on an ordinary invoice."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    freight = next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT")
    baf = next(line for line in lines if line["cost_type"] == "THC")  # read as THC, corrected by the reviewer
    accept(client, invoice_id, freight)
    accept(client, invoice_id, baf, cost_type="OCEAN_FREIGHT")

    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["costs_created"] == 1
    (cost,) = client.get("/api/v1/costs", params={"target_id": box}).json()
    assert (cost["cost_type"], cost["amount"]) == ("OCEAN_FREIGHT", "3275.00")
    assert cost["notes"].endswith("lines 1, 2")
    after = client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]
    assert {line["cost_id"] for line in after if line["accepted"]} == {cost["id"]}


def test_one_invoice_billing_the_same_charge_on_two_containers_is_confirmed(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The handling of each box of a bill of lading, one line per box: the other shape that failed."""
    first = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    second = container_with(client, "TGHU7245081", [("TYRE", "0.045")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_TWO_CONTAINERS)
    handlings = [line for line in lines if line["cost_type"] == "THC"]
    assert {line["target_id"] for line in handlings} == {first, second}
    for line in handlings:
        accept(client, invoice_id, line)
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["costs_created"] == 2


# ---------------------------------------------------------------------------- the same invoice twice


def test_one_invoice_is_recognised_under_any_spelling() -> None:
    assert normalize_vendor("Transdemo SAS") == normalize_vendor("TRANSDEMO") == "transdemo"
    assert normalize_vendor("KUEHNE + NAGEL SAS") == normalize_vendor("Kuehne Nagel") == "kuehnenagel"
    assert (
        normalize_invoice_number("FA-2026-0412") == normalize_invoice_number("fa 2026 0412") == "FA20260412"
    )
    assert same_invoice("TRANSDEMO", "F-0412", "Transdemo SAS", "F0412")
    assert same_invoice(None, "F-0412", "Transdemo SAS", "F0412")  # one copy names no forwarder
    assert not same_invoice("TRANSDEMO", "F-0412", "SCHELDE NV", "F0412")  # two forwarders' "0412"
    assert not same_invoice("TRANSDEMO", "", "TRANSDEMO", "")  # no number, nothing to say


def test_an_invoice_already_in_the_books_is_refused_whatever_its_spelling_and_type(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Keyed in from the forwarder's statement on Monday — its total, as one freight — then its PDF
    confirmed on Tuesday: every line of it would count twice, the handling included. Not a charge
    that looks like another, which a person may overrule: the same document."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    typed(client, box, "OCEAN_FREIGHT", "3830.00", vendor="Transdemo", invoice_number="FA 2026 0912")
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    accept(client, invoice_id, next(line for line in lines if line["cost_type"] == "THC"))

    for payload in (None, {"force": True}):
        refused = client.post(f"/api/v1/invoices/{invoice_id}/confirm", json=payload)
        assert (refused.status_code, refused.json()["code"]) == (422, "INVOICE_ALREADY_RECORDED")
    (seen,) = refused.json()["errors"]
    assert seen["message"].startswith("@recorded|OCEAN_FREIGHT|Transdemo|FA 2026 0912|3830.00|CONTAINER|")
    assert refused.json()["forceable"] is True  # a neighbouring spelling: a person may say otherwise


def test_a_copy_of_a_confirmed_invoice_is_refused_even_when_forced_and_never_fails(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The same PDF sent twice: the same forwarder and number to the letter, the same line on the same
    box. Not a neighbouring spelling a person may overrule but the very line on file, which the
    database refuses anyway: no override is offered, and forcing it is the same refusal, not a
    server error."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    first_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    accept(client, first_id, next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT"))
    assert client.post(f"/api/v1/invoices/{first_id}/confirm").status_code == 200

    duplicata = [
        line.replace("Acme Import SAS", "Acme Import SAS - DUPLICATA") for line in FRENCH_ONE_CONTAINER
    ]
    copy_id, copy_lines = read_invoice(client, db, org, duplicata)
    accept(client, copy_id, next(line for line in copy_lines if line["cost_type"] == "OCEAN_FREIGHT"))
    for payload in (None, {"force_recorded": True}, {"force": True, "force_recorded": True}):
        refused = client.post(f"/api/v1/invoices/{copy_id}/confirm", json=payload)
        assert refused.status_code == 422, refused.text
        assert (refused.json()["code"], refused.json()["forceable"]) == ("INVOICE_ALREADY_RECORDED", False)
    assert [c["cost_type"] for c in client.get("/api/v1/costs", params={"target_id": box}).json()] == [
        "OCEAN_FREIGHT"
    ]


def test_a_line_spelled_to_the_letter_like_one_on_file_is_refused_whatever_its_status(
    client: TestClient,
) -> None:
    """An estimate typed with the forwarder's number, then the invoice typed with the same: the
    database's own index does not tell the statuses apart. Refused by name, not a server error."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    typed(client, box, "THC", "260.00", status="ESTIMATE", vendor="TRANSDEMO", invoice_number="Q-7")
    body = {"scope": "CONTAINER", "target_id": box, "cost_type": "THC", "amount": "265.00",
            "currency": "EUR", "cost_date": "2026-03-12", "vendor": "TRANSDEMO",
            "invoice_number": "Q-7"}  # fmt: skip
    refused = client.post("/api/v1/costs", json=body)
    assert (refused.status_code, refused.json()["code"]) == (422, "INVOICE_LINE_ALREADY_RECORDED")


def test_a_person_may_say_it_is_another_invoice_and_their_word_is_kept(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Two documents under one number — a forwarder that starts its numbering again every year — are
    the reviewer's call: confirmed on their word, which the confirmation and the history both keep.
    The duplicate check's own override may come with it; each opens only its own door."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    typed(client, box, "OCEAN_FREIGHT", "3830.00", vendor="Transdemo", invoice_number="FA 2026 0912")
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    accept(client, invoice_id, next(line for line in lines if line["cost_type"] == "THC"))

    confirmed = client.post(
        f"/api/v1/invoices/{invoice_id}/confirm", json={"force": True, "force_recorded": True}
    )
    assert confirmed.status_code == 200, confirmed.text
    word = "@forced_recorded|FA-2026-0912|TRANSDEMO SAS"
    assert word in confirmed.json()["notes"]
    history = client.get("/api/v1/audit-log", params={"entity_id": invoice_id}).json()["entries"]
    (entry,) = [e for e in history if e["action"] == "invoice.confirmed"]
    assert entry["after"]["notes"] == [word]


def test_the_same_invoice_line_keyed_in_twice_is_refused_on_the_box_and_on_its_bill_of_lading(
    client: TestClient,
) -> None:
    """« TRANSDEMO / F-0412 » keyed in on Monday, « Transdemo SAS / F0412 » on Tuesday: one charge,
    counted twice in the landed cost. Refused by name with what is already there — on the box, and on
    the bill of lading it travelled under."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    voyage = client.post("/api/v1/shipments", json={"reference": "BL-1"}).json()
    assert client.patch(f"/api/v1/containers/{box}", json={"shipment_id": voyage["id"]}).status_code == 200
    first = typed(client, box, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-0412")

    again = {"scope": "CONTAINER", "target_id": box, "cost_type": "THC", "amount": "260.00",
             "currency": "EUR", "cost_date": "2026-03-12", "vendor": "Transdemo SAS",
             "invoice_number": "F0412"}  # fmt: skip
    on_the_bill = {**again, "scope": "SHIPMENT", "target_id": voyage["id"], "invoice_number": "f 0412"}
    for body in (again, on_the_bill):
        refused = client.post("/api/v1/costs", json=body)
        assert (refused.status_code, refused.json()["code"]) == (422, "INVOICE_LINE_ALREADY_RECORDED")
        (seen,) = refused.json()["errors"]
        assert seen["message"].startswith("@recorded|THC|TRANSDEMO|F-0412|260.00|CONTAINER|")

    # Another line of the same invoice, and another forwarder's 0412, are other charges.
    typed(client, box, "DRAYAGE", "425.00", vendor="Transdemo SAS", invoice_number="F0412")
    typed(client, box, "THC", "260.00", vendor="SCHELDE NV", invoice_number="F-0412")
    # Renumbering a cost into the line already there is the same double entry; editing that line
    # itself is not a double entry of itself.
    other = typed(client, box, "THC", "260.00", vendor="TRANSDEMO", invoice_number="F-0413")
    renumbered = client.patch(f"/api/v1/costs/{other['id']}", json={"invoice_number": "F 0412"})
    assert (renumbered.status_code, renumbered.json()["code"]) == (422, "INVOICE_LINE_ALREADY_RECORDED")
    assert client.patch(f"/api/v1/costs/{first['id']}", json={"vendor": "Transdemo SAS"}).status_code == 200


def test_two_lines_of_one_invoice_are_two_lines_and_never_a_double_entry(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The freight in dollars and its security surcharge in euros, both freight: one confirmation writes
    them as two costs, one per currency. They are two lines of one document — the audit does not tell
    the reader to delete one of them, and the manual door lets the same two lines in too."""
    container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    accept(
        client,
        invoice_id,
        next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT"),
        currency="USD",
    )
    accept(
        client,
        invoice_id,
        next(line for line in lines if line["cost_type"] == "THC"),
        cost_type="OCEAN_FREIGHT",
    )
    confirmed = client.post(f"/api/v1/invoices/{invoice_id}/confirm")
    assert (confirmed.status_code, confirmed.json()["costs_created"]) == (200, 2)
    audit = client.get(
        "/api/v1/reports/audit", params={"period_from": "2026-03-01", "period_to": "2026-03-31"}
    )
    assert [f for f in audit.json()["findings"] if f["code"] == "DOUBLE_ENTRY"] == []

    by_hand = container_with(client, "TGHU7245081", [("TYRE", "0.045")])
    typed(
        client,
        by_hand,
        "OCEAN_FREIGHT",
        "3000.00",
        currency="USD",
        vendor="TRANSDEMO",
        invoice_number="F-0412",
    )
    typed(client, by_hand, "OCEAN_FREIGHT", "15.00", vendor="TRANSDEMO", invoice_number="F-0412")
    voyage = client.post("/api/v1/shipments", json={"reference": "BL-2"}).json()
    assert client.patch(f"/api/v1/containers/{by_hand}", json={"shipment_id": voyage["id"]}).is_success
    surcharge = {"scope": "SHIPMENT", "target_id": voyage["id"], "cost_type": "OCEAN_FREIGHT",
                 "amount": "40.00", "currency": "EUR", "cost_date": "2026-03-12", "vendor": "TRANSDEMO",
                 "invoice_number": "F-0412"}  # fmt: skip
    assert client.post("/api/v1/costs", json=surcharge).status_code == 201  # on the bill, another amount


def test_an_empty_invoice_number_or_forwarder_is_no_value(client: TestClient) -> None:
    """ "" stored as a number is a number to the database's own uniqueness, and every empty one the same:
    the second cost of a forwarder typed without a number was a server error."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    for _ in range(2):
        made = typed(client, box, "THC", "260.00", vendor="TRANSDEMO", invoice_number="")
        assert made["invoice_number"] is None
        estimate = typed(
            client, box, "DRAYAGE", "400.00", status="ESTIMATE", vendor="  ", invoice_number="Q-7"
        )
        assert estimate["vendor"] is None


def test_a_line_re_currencied_onto_one_on_file_is_refused_before_its_rate_is_fetched(
    client: TestClient,
) -> None:
    """Moving a cost to a currency and a day whose rate is not held yet fetches and saves that rate at
    once: the edited row went with it, into the database's own refusal, before the check had looked."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    euros = typed(client, box, "OCEAN_FREIGHT", "3000.00", vendor="TRANSDEMO", invoice_number="F-0412")
    typed(
        client, box, "OCEAN_FREIGHT", "3000.00", currency="USD", vendor="TRANSDEMO", invoice_number="F-0412"
    )
    moved = client.patch(f"/api/v1/costs/{euros['id']}", json={"currency": "USD", "cost_date": "2026-02-02"})
    assert (moved.status_code, moved.json()["code"]) == (422, "INVOICE_LINE_ALREADY_RECORDED")


def test_saying_it_is_another_invoice_does_not_write_a_line_already_on_file(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The freight keyed in by hand as « Transdemo / FA 2026 0912 », then the PDF's own freight line:
    the forwarder is named on both copies, its numbers are its own — the same line, which no word of
    the reviewer's can make two. Refused with no override offered, as the manual door refuses it."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    typed(client, box, "OCEAN_FREIGHT", "3830.00", vendor="Transdemo", invoice_number="FA 2026 0912")
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    accept(client, invoice_id, next(line for line in lines if line["cost_type"] == "OCEAN_FREIGHT"))
    refused = client.post(
        f"/api/v1/invoices/{invoice_id}/confirm", json={"force": True, "force_recorded": True}
    )
    assert (refused.status_code, refused.json()["code"], refused.json()["forceable"]) == (
        422,
        "INVOICE_ALREADY_RECORDED",
        False,
    )
    assert [c["cost_type"] for c in client.get("/api/v1/costs", params={"target_id": box}).json()] == [
        "OCEAN_FREIGHT"
    ]


def test_a_duty_on_the_box_and_one_on_its_bill_of_lading_are_weighed_together(
    client: TestClient, db: Session, org: Organization
) -> None:
    """One invoice, half the duty on the box and half on its bill of lading: the second half is weighed
    with the first among the duty paid, and what the rates explain in all is spread by them."""
    box = container_with(client, "MSCU4821990", [("TYRE", "0.12"), ("JACK", "0")])
    voyage = client.post("/api/v1/shipments", json={"reference": "BL-1"}).json()
    assert client.patch(f"/api/v1/containers/{box}", json={"shipment_id": voyage["id"]}).is_success
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    by_type = {line["cost_type"]: line for line in lines}
    accept(client, invoice_id, by_type["OCEAN_FREIGHT"], cost_type=DUTY, amount="60.00")
    accept(client, invoice_id, by_type["DRAYAGE"], cost_type=DUTY, amount="60.00", scope="SHIPMENT",
           target_id=voyage["id"])  # fmt: skip
    assert client.post(f"/api/v1/invoices/{invoice_id}/confirm").status_code == 200
    on_the_bill = [c for c in client.get("/api/v1/costs", params={"target_id": voyage["id"]}).json()]
    assert [c["allocation_method"] for c in on_the_bill] == ["BY_THEORETICAL_DUTY"]  # 60 + 60 = 12 % of 1 000


def test_a_method_somebody_chose_stays_theirs_when_a_cost_moves_to_duty(client: TestClient) -> None:
    box = container_with(client, "MSCU4821990", [("TYRE", "0.045"), ("JACK", "0")])
    counted = typed(client, box, "OCEAN_FREIGHT", "45.00", allocation_method="BY_QUANTITY")
    as_duty = client.patch(f"/api/v1/costs/{counted['id']}", json={"cost_type": DUTY})
    assert (as_duty.status_code, as_duty.json()["allocation_method"]) == (200, "BY_QUANTITY")


def test_the_header_the_reader_took_is_corrected_before_confirming_and_not_after(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A date read as the invoice's number is a false « already recorded » today and a missed double
    entry tomorrow: the reviewer corrects it on the invoice, and the history says who did."""
    container_with(client, "MSCU4821990", [("TYRE", "0.045")])
    invoice_id, lines = read_invoice(client, db, org, FRENCH_ONE_CONTAINER)
    corrected = client.patch(
        f"/api/v1/invoices/{invoice_id}", json={"invoice_number": " FA-2026-0999 ", "vendor": ""}
    )
    assert corrected.status_code == 200, corrected.text
    assert (corrected.json()["invoice_number"], corrected.json()["vendor"]) == ("FA-2026-0999", None)
    history = client.get("/api/v1/audit-log", params={"entity_id": invoice_id}).json()["entries"]
    (entry,) = [e for e in history if e["action"] == "invoice.corrected"]
    assert (entry["before"]["vendor"], entry["after"]["vendor"]) == ("TRANSDEMO SAS", None)

    accept(client, invoice_id, lines[0])
    assert client.post(f"/api/v1/invoices/{invoice_id}/confirm").status_code == 200
    (cost,) = [c for c in client.get("/api/v1/costs").json() if c["invoice_number"] == "FA-2026-0999"]
    assert cost["vendor"] is None
    late = client.patch(f"/api/v1/invoices/{invoice_id}", json={"invoice_number": "FA-1"})
    assert (late.status_code, late.json()["code"]) == (422, "INVALID_STATUS")


def test_the_doors_that_write_an_organization_s_costs_take_turns(
    db: Session, engine: Engine, org_id: uuid.UUID
) -> None:
    """Every door reads the books to refuse a line already there, then writes: two at once would each
    read without the other's line. While one holds an organization's books, another request waits for
    them — and only for that organization's."""
    lock_books(db, org_id)
    with engine.connect() as other_request:
        try_lock = text("SELECT pg_try_advisory_xact_lock(:space, :org)")
        space, org = books_lock_key(org_id)
        taken = other_request.execute(try_lock, {"space": space, "org": org}).scalar()
        space, org = books_lock_key(uuid.uuid4())
        free = other_request.execute(try_lock, {"space": space, "org": org}).scalar()
        other_request.rollback()
    assert (taken, free) == (False, True)
