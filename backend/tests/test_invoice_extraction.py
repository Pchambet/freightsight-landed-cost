"""Reading invoices: the pattern extractor on three fixtures, and what we check afterwards."""

from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import httpx
import pytest
from conftest import fake_fetcher
from fastapi.testclient import TestClient
from invoice_fixtures import (
    CREDIT_NOTE_MINUS_GLUED,
    CREDIT_NOTE_PARENTHESES,
    CREDIT_NOTE_POSITIVE_FIGURES,
    CREDIT_NOTE_TRAILING_MINUS,
    CREDIT_NOTE_UNICODE_MINUS,
    CREDIT_NOTES,
    DOLLAR_IBAN_IN_FOOTER,
    EXCHANGE_RATE_IN_HEADER,
    FRENCH_ONE_CONTAINER,
    FRENCH_TWO_CONTAINERS,
    FRENCH_WITH_DISCOUNT,
    USD_INVOICE,
)
from pdfs import make_pdf, make_pdf_without_text
from sqlalchemy.orm import Session

from app.adapters.extraction.llm_extractor import LlmExtractor
from app.adapters.extraction.regex_extractor import RegexExtractor, describe, trailing_amount
from app.domain.fx.service import FxService
from app.domain.invoices.checks import CAPPED_CONFIDENCE, UNVERIFIED_TOTAL_NOTE, arithmetic_holds, check
from app.domain.invoices.ports import (
    ExtractedLine,
    ExtractionContext,
    ExtractionFailed,
    ExtractionInput,
    ExtractorNotConfigured,
    FreightInvoice,
)
from app.domain.invoices.registry import get_extractors
from app.domain.models import CostScope, CostType, Organization

CONTAINER_A = uuid.uuid4()
CONTAINER_B = uuid.uuid4()
CONTEXT = ExtractionContext(
    base_currency="EUR",
    containers={"MSCU4821990": CONTAINER_A, "TGHU7245081": CONTAINER_B},
)


def read(lines: list[str], context: ExtractionContext = CONTEXT) -> FreightInvoice:
    return RegexExtractor().extract(
        ExtractionInput(make_pdf(lines), "application/pdf", "facture.pdf"), context
    )


# ---------------------------------------------------------------------------- the pattern extractor


def test_a_french_forwarder_invoice_is_read_in_full() -> None:
    invoice = read(FRENCH_ONE_CONTAINER)

    assert invoice.vendor == "TRANSDEMO SAS"
    assert invoice.invoice_number == "FA-2026-0912"
    assert invoice.invoice_date == date(2026, 3, 12)
    assert invoice.currency == "EUR"
    assert invoice.total == "4596.00"
    assert invoice.vat == "766.00"
    assert invoice.container_numbers == ["MSCU4821990"]

    charges = {ln.cost_type: ln.amount for ln in invoice.lines}
    assert charges == {
        CostType.OCEAN_FREIGHT: "3000.00",
        CostType.THC: "275.00",
        CostType.CUSTOMS_BROKERAGE: "130.00",
        CostType.DRAYAGE: "425.00",
    }


def test_the_date_of_the_invoice_is_not_taken_for_its_number() -> None:
    """« Date facture : 12/03/2026 » above the number: read as the number, every invoice of one day
    from one forwarder would be the same document to the guard against double entry."""
    for dated in ("Date facture : 12/03/2026", "Date facture : 2026/03/12"):
        lines = [*FRENCH_ONE_CONTAINER[:2], "FACTURE", dated, "N° facture : FA-2026-0912",
                 *FRENCH_ONE_CONTAINER[4:]]  # fmt: skip
        assert read(lines).invoice_number == "FA-2026-0912", dated


@pytest.mark.parametrize(
    ("header", "number"),
    [
        (["FACTURE N° 26-09-0412"], "26-09-0412"),  # a year, a month and a sequence: 0412 is no year
        (["Due Date:", "Invoice No: INV-0412"], "INV-0412"),  # the date's words end on the line above
        (["Montant du", "Facture N° FA-0412"], "FA-0412"),
        (["Updated invoice INV-0412"], "INV-0412"),  # « dated » inside a word is not the word
        (["Date de la facture : 2026/03/12"], None),
        (["Invoice: 12-MAR-2026"], None),
        # after the date, a number labelled as one is still taken, however far down the header runs
        (
            ["Date facture : 12/03/2026", *(f"Dossier {n}" for n in range(6)), "FACTURE N° FA-2026-0912"],
            "FA-2026-0912",
        ),
        (["Date facture : 12/03/2026", "Numéro de la facture : FA-77"], "FA-77"),
        # the number of the invoice this one cancels, labelled or not, is not its own
        (["Cette facture annule et remplace la facture N° FA-0400", "FACTURE N° FA-0412"], "FA-0412"),
        # a date, then — far below the header — the number of an invoice this one replaces
        (
            [
                "Date facture : 12/03/2026",
                *(f"Ref {n}" for n in range(14)),
                "annule et remplace la facture FA-0400",
            ],
            None,
        ),
    ],
)
def test_a_number_is_told_from_a_date_by_the_calendar_and_by_where_it_stands(
    header: list[str], number: str | None
) -> None:
    lines = ["TRANSDEMO SAS", *header, "Conteneur MSCU4821990",
             "Fret maritime CNNGB / FRLEH                  1          3 000,00  3 000,00"]  # fmt: skip
    assert read(lines).invoice_number == number


def test_the_charge_is_the_rightmost_amount_on_the_line() -> None:
    """« THC 2 x 137,50  275,00 » is a charge of 275, not of 2 or of 137.50."""
    assert trailing_amount("THC destination Le Havre    2    137,50    275,00") == Decimal("275.00")
    assert trailing_amount("Fret maritime CNNGB / FRLEH   1   3 000,00   3 000,00") == Decimal("3000.00")
    assert trailing_amount("Ocean freight               4,200.00") == Decimal("4200.00")
    assert trailing_amount("Nothing numeric here") is None


def test_totals_and_vat_are_not_mistaken_for_charges() -> None:
    invoice = read(FRENCH_ONE_CONTAINER)
    descriptions = " ".join(ln.description for ln in invoice.lines).upper()
    assert "TOTAL" not in descriptions
    assert "TVA" not in descriptions


def test_an_invoice_naming_two_containers_attributes_each_line() -> None:
    invoice = read(FRENCH_TWO_CONTAINERS)
    assert set(invoice.container_numbers) == {"MSCU4821990", "TGHU7245081"}
    by_container = {(ln.cost_type, ln.container_number) for ln in invoice.lines}
    assert (CostType.THC, "MSCU4821990") in by_container
    assert (CostType.THC, "TGHU7245081") in by_container
    assert (CostType.DEMURRAGE, "MSCU4821990") in by_container
    assert (CostType.DRAYAGE, "TGHU7245081") in by_container


def test_a_scan_fails_with_a_reason_a_person_can_act_on() -> None:
    with pytest.raises(ExtractionFailed) as raised:
        RegexExtractor().extract(ExtractionInput(make_pdf_without_text(), "application/pdf"), CONTEXT)
    assert raised.value.code == "NO_TEXT_LAYER"
    assert "by hand" in raised.value.message


def test_a_photograph_is_refused_by_the_pattern_extractor() -> None:
    with pytest.raises(ExtractionFailed):
        RegexExtractor().extract(ExtractionInput(b"\xff\xd8\xff", "image/jpeg"), CONTEXT)


def test_broken_bytes_fail_cleanly() -> None:
    with pytest.raises(ExtractionFailed):
        RegexExtractor().extract(ExtractionInput(b"%PDF-1.4\nnot really", "application/pdf"), CONTEXT)


# ---------------------------------------------------------------------------- the checks


def test_arithmetic_that_holds_and_arithmetic_that_does_not() -> None:
    invoice = read(FRENCH_ONE_CONTAINER)
    holds, difference = arithmetic_holds(invoice)
    assert holds and difference == Decimal("0.00")

    invoice.lines.pop()  # the forwarder's drayage line goes missing
    holds, difference = arithmetic_holds(invoice)
    assert not holds
    assert difference == Decimal("-425.00")


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def fx() -> FxService:
    return FxService(fake_fetcher)


def test_a_missing_line_caps_the_confidence_and_says_which_way(db: Session, org: Organization) -> None:
    invoice = read(FRENCH_ONE_CONTAINER)
    invoice.confidence = 0.9
    invoice.lines.pop()

    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 12)
    )
    assert checked.confidence == CAPPED_CONFIDENCE
    assert "@arithmetic_mismatch" in " ".join(checked.notes)


def test_every_line_is_charged_to_the_only_container_named(db: Session, org: Organization) -> None:
    invoice = read(FRENCH_ONE_CONTAINER)
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 12)
    )
    assert [ln.scope for ln in checked.lines] == [CostScope.CONTAINER] * 4
    assert {ln.target_id for ln in checked.lines} == {CONTAINER_A}
    assert all("@single_container|MSCU4821990" in " ".join(ln.notes) for ln in checked.lines)


def test_an_invoiced_cost_of_the_same_type_is_flagged(db: Session, org: Organization) -> None:
    from app.domain.models import (
        AllocationMethod,
        Container,
        ContainerMilestone,
        Cost,
        CostScope,
        CostStatus,
        TrackingState,
    )

    db.add(
        Container(
            id=CONTAINER_A,
            org_id=org.id,
            container_number="MSCU4821990",
            milestone=ContainerMilestone.DISCHARGED,
            tracking_state=TrackingState.MANUAL,
        )
    )
    db.add(
        Cost(
            org_id=org.id,
            scope=CostScope.CONTAINER,
            container_id=CONTAINER_A,
            cost_type=CostType.OCEAN_FREIGHT,
            amount=Decimal("3000.00"),
            currency="EUR",
            fx_rate=Decimal("1"),
            fx_date=date(2026, 3, 1),
            fx_source="same",
            amount_base=Decimal("3000.00"),
            allocation_method=AllocationMethod.BY_WEIGHT,
            cost_date=date(2026, 3, 1),
            status=CostStatus.ACTUAL,
            vendor="Kuehne + Nagel France",
            invoice_number="KN-26-118377",
        )
    )
    db.flush()

    invoice = read(FRENCH_ONE_CONTAINER)
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 12)
    )
    freight = next(ln for ln in checked.lines if ln.cost_type == CostType.OCEAN_FREIGHT)
    assert any(n.startswith("@duplicate_cost|OCEAN_FREIGHT|") for n in freight.notes)
    assert freight.confidence <= CAPPED_CONFIDENCE


def test_the_freight_already_recorded_on_the_shipment_is_flagged_too(db: Session, org: Organization) -> None:
    """The duplicate that costs the most is the one that used to go through: a forwarder bills the
    ocean freight per container while the same freight is already on file at shipment level, which
    is how it was quoted. Scope-for-scope, that comparison found nothing — 3 000 € counted twice
    with a green tick, on the demo's own data."""
    from app.domain.models import (
        AllocationMethod,
        Container,
        ContainerMilestone,
        Cost,
        CostStatus,
        Shipment,
        TrackingState,
    )

    shipment = Shipment(org_id=org.id, reference="MEDUSH2604417")
    db.add(shipment)
    db.flush()
    db.add(
        Container(
            id=CONTAINER_A,
            org_id=org.id,
            shipment_id=shipment.id,
            container_number="MSCU4821990",
            milestone=ContainerMilestone.DISCHARGED,
            tracking_state=TrackingState.MANUAL,
        )
    )
    db.add(
        Cost(
            org_id=org.id,
            scope=CostScope.SHIPMENT,
            shipment_id=shipment.id,
            cost_type=CostType.OCEAN_FREIGHT,
            amount=Decimal("3000.00"),
            currency="EUR",
            fx_rate=Decimal("1"),
            fx_date=date(2026, 3, 1),
            fx_source="same",
            amount_base=Decimal("3000.00"),
            allocation_method=AllocationMethod.BY_WEIGHT,
            cost_date=date(2026, 3, 1),
            status=CostStatus.ACTUAL,
            vendor="Transdemo SAS",
        )
    )
    db.flush()

    checked = check(
        db,
        fx(),
        read(FRENCH_ONE_CONTAINER),
        CONTEXT,
        org_id=org.id,
        base_currency="EUR",
        invoice_date=date(2026, 3, 12),
    )
    freight = next(ln for ln in checked.lines if ln.cost_type == CostType.OCEAN_FREIGHT)
    note = next(n for n in freight.notes if n.startswith("@duplicate_cost|"))
    assert note.endswith("|SHIPMENT")  # and the screen can say where it already sits
    assert freight.confidence <= CAPPED_CONFIDENCE
    # a charge of another type on the same shipment is still not a duplicate of this one
    thc = next(ln for ln in checked.lines if ln.cost_type == CostType.THC)
    assert not any(n.startswith("@duplicate_cost|") for n in thc.notes)


def test_a_container_we_do_not_have_is_dropped_and_said_out_loud(db: Session, org: Organization) -> None:
    """The model is handed the organization's containers; anything else it returns is not a target."""
    invoice = FreightInvoice(
        currency="EUR",
        lines=[
            ExtractedLine(
                description="THC",
                amount="275.00",
                cost_type=CostType.THC,
                container_number="ZZZU0000000",
                confidence=0.9,
            )
        ],
    )
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 12)
    )
    (line,) = checked.lines
    assert line.scope is None and line.target_id is None
    assert line.confidence <= CAPPED_CONFIDENCE
    assert "@unknown_container|ZZZU0000000" in " ".join(line.notes)


def test_a_currency_without_a_rate_marks_the_line_for_review(db: Session, org: Organization) -> None:
    invoice = read(USD_INVOICE)
    assert invoice.currency == "USD"
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 5)
    )
    assert checked.lines  # the charges were read
    for line in checked.lines:
        assert line.currency == "USD"
    # the fake fetcher knows USD, so nothing is flagged; an unknown currency is another story
    invoice.lines[0].currency = "SEK"
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 5)
    )
    assert "@no_fx_rate|SEK" in " ".join(checked.lines[0].notes)
    assert checked.lines[0].confidence <= CAPPED_CONFIDENCE


def test_several_containers_asks_for_a_second_look(db: Session, org: Organization) -> None:
    invoice = read(FRENCH_TWO_CONTAINERS)
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 18)
    )
    assert "@multi_container" in " ".join(checked.notes)
    targets = {ln.target_id for ln in checked.lines}
    assert targets == {CONTAINER_A, CONTAINER_B}


# ---------------------------------------------------------------------------- the model extractor


def test_the_model_extractor_says_which_key_is_missing() -> None:
    with pytest.raises(ExtractorNotConfigured) as raised:
        LlmExtractor(None, "claude-opus-5").extract(
            ExtractionInput(make_pdf(FRENCH_ONE_CONTAINER), "application/pdf"), CONTEXT
        )
    assert "EXTRACTION_API_KEY" in raised.value.message


def llm(handler) -> LlmExtractor:  # type: ignore[no-untyped-def]
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return LlmExtractor("sk-test", "claude-opus-5", api_url="https://api.test/v1/messages", client=client)


def test_the_model_is_given_the_containers_and_its_answer_is_validated() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen["prompt"] = body["messages"][0]["content"]
        seen["api_key"] = request.headers["x-api-key"]
        answer = {
            "vendor": "TRANSDEMO SAS",
            "invoice_number": "FA-2026-0912",
            "invoice_date": "2026-03-12",
            "currency": "EUR",
            "total": "4596.00",
            "vat": "766.00",
            "lines": [
                {
                    "description": "Fret maritime",
                    "amount": "3000.00",
                    "cost_type": "OCEAN_FREIGHT",
                    "container_number": "MSCU4821990",
                    "confidence": 0.95,
                }
            ],
            "container_numbers": ["MSCU4821990"],
            "confidence": 0.9,
        }
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(answer)}]})

    invoice = llm(handler).extract(
        ExtractionInput(make_pdf(FRENCH_ONE_CONTAINER), "application/pdf"), CONTEXT
    )
    assert "MSCU4821990" in str(seen["prompt"])  # it recognises references, it does not invent them
    assert seen["api_key"] == "sk-test"
    assert invoice.lines[0].amount == "3000.00"  # a string all the way to Decimal
    assert invoice.confidence == 0.9


def test_a_fenced_answer_is_still_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        text = '```json\n{"lines": [], "confidence": 0.4}\n```'
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})

    assert (
        llm(handler)
        .extract(ExtractionInput(make_pdf(FRENCH_ONE_CONTAINER), "application/pdf"), CONTEXT)
        .confidence
        == 0.4
    )


def test_an_off_shape_answer_is_a_failed_reading_not_a_half_trusted_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = {"lines": [{"description": "Fret", "amount": "3000.00", "confidence": 4.2}]}
        return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(answer)}]})

    with pytest.raises(ExtractionFailed) as raised:
        llm(handler).extract(ExtractionInput(make_pdf(FRENCH_ONE_CONTAINER), "application/pdf"), CONTEXT)
    assert raised.value.code == "EXTRACTOR_SHAPE"


def test_a_service_error_is_reported_not_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(529, text="overloaded")

    with pytest.raises(ExtractionFailed):
        llm(handler).extract(ExtractionInput(make_pdf(FRENCH_ONE_CONTAINER), "application/pdf"), CONTEXT)


def test_the_patterns_are_always_available_and_the_model_only_with_a_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.settings import get_settings

    get_settings.cache_clear()
    assert [e.name for e in get_extractors()] == ["regex"]

    monkeypatch.setenv("EXTRACTION_API_KEY", "sk-test")
    get_settings.cache_clear()
    assert [e.name for e in get_extractors()] == ["llm", "regex"]
    get_settings.cache_clear()


# ---------------------------------------------------------------------------- a realistic invoice


def test_a_realistic_forwarder_invoice_reads_exactly(db: Session, org: Organization) -> None:
    """The one fixture nobody wrote to be easy: a French forwarder invoice laid out like a real one,
    generated as a demo document, which caught two bugs the hand-written lines did not: a header line
    naming a B/L and a container size, and a VAT line stating its taxable base."""
    from pathlib import Path

    pdf = (Path(__file__).parent / "fixtures" / "facture_transitaire_fr.pdf").read_bytes()
    invoice = RegexExtractor().extract(
        ExtractionInput(pdf, "application/pdf", "facture_transitaire_fr.pdf"), CONTEXT
    )

    assert invoice.invoice_number == "FAC-2026-08871"
    assert invoice.invoice_date == date(2026, 9, 5)
    # The three totals the invoice prints, each read rather than inferred from the others.
    assert invoice.subtotal == "3350.00"
    assert invoice.vat == "167.00"  # computed as TTC - HT, not read off the label
    assert invoice.total == "3517.00"
    assert invoice.container_numbers == ["MSCU1234567"]

    charges = [(ln.cost_type, ln.amount) for ln in invoice.lines]
    assert charges == [
        (CostType.OCEAN_FREIGHT, "2450.00"),
        (CostType.THC, "285.00"),
        (CostType.BL_FEE, "65.00"),
        (CostType.CUSTOMS_BROKERAGE, "120.00"),
        (CostType.DRAYAGE, "430.00"),
    ]
    # and what a reviewer reads is the charge, not the table's quantity and unit-price columns
    assert [ln.description for ln in invoice.lines] == [
        "Fret maritime Shanghai / Le Havre (Ocean freight)",
        "THC destination Le Havre (Terminal Handling Charges)",
        "Frais de B/L (Bill of lading fee)",
        "Dédouanement import (customs brokerage)",
        "Camionnage Le Havre → entrepôt client (drayage)",
    ]
    holds, difference = arithmetic_holds(invoice)
    assert holds and difference == Decimal("0.00")


def test_the_description_keeps_the_wording_and_drops_the_columns() -> None:
    """A table row is wording followed by quantity, unit price and amount. Only a run of numbers
    that reaches the end of the line is a set of columns; one inside the wording is part of it."""
    assert (
        describe("Fret maritime Shanghai / Le Havre (Ocean freight) 1 2 450,00 2 450,00")
        == "Fret maritime Shanghai / Le Havre (Ocean freight)"
    )
    assert describe("THC destination Le Havre 1 285,00 285,00") == "THC destination Le Havre"
    # a quantity that belongs to the wording survives, because words follow it
    assert describe("Surestaries 5 jours x 80,00 400,00") == "Surestaries 5 jours"
    # the sign of a multiplication goes; the last letter of a word that happens to be x stays
    assert describe("Camionnage Bordeaux 1 250,00 250,00") == "Camionnage Bordeaux"
    # the currency the last column ends with is not a reason to give up and show the whole row
    assert describe("Dédouanement import 120,00 €") == "Dédouanement import"
    assert describe("Frais de B/L 65,00 EUR") == "Frais de B/L"
    assert describe("Conteneur 40' HC — manutention 150,00") == "Conteneur 40' HC — manutention"
    # nothing to cut, nothing cut
    assert describe("Frais de dossier") == "Frais de dossier"
    # and a row that is only numbers keeps something rather than becoming empty
    assert describe("1 2 450,00 2 450,00") == "1 2 450,00 2 450,00"


def test_a_missed_line_is_caught_by_the_invoice_s_own_pre_tax_total() -> None:
    """The failure that matters: a charge the patterns did not recognise.

    Against the grand total it hides behind the VAT — when the tax cannot be read, a total short by
    exactly the missing charge looks like a total short by exactly the tax. The invoice's own
    « Total HT » has no such excuse in it.
    """
    missing_the_bl_fee = FreightInvoice(
        currency="EUR",
        subtotal="3350.00",
        vat=None,  # unreadable, as it is on plenty of layouts
        total="3517.00",
        lines=[
            ExtractedLine(description="Fret maritime", amount="2450.00"),
            ExtractedLine(description="THC", amount="285.00"),
            ExtractedLine(description="Dédouanement", amount="120.00"),
            ExtractedLine(description="Camionnage", amount="430.00"),
        ],
    )
    holds, difference = arithmetic_holds(missing_the_bl_fee)
    assert not holds
    assert difference == Decimal("-65.00")  # exactly the charge nobody saw

    complete = missing_the_bl_fee.model_copy(
        update={
            "lines": [
                *missing_the_bl_fee.lines,
                ExtractedLine(description="Frais de B/L", amount="65.00"),
            ]
        }
    )
    holds, difference = arithmetic_holds(complete)
    assert holds and difference == Decimal("0.00")


def test_without_a_stated_pre_tax_total_the_old_check_still_applies() -> None:
    """Not every invoice prints one; the grand total is then the only yardstick there is."""
    invoice = FreightInvoice(
        currency="EUR",
        total="3517.00",
        vat="167.00",
        lines=[ExtractedLine(description="Fret maritime", amount="3350.00")],
    )
    holds, difference = arithmetic_holds(invoice)
    assert holds and difference == Decimal("0.00")


def test_a_header_naming_a_charge_is_not_a_charge() -> None:
    """« … · B/L MEDUSH2604417 · Conteneur MSCU1234567 (40' HC) · Navire … » once became a
    bill-of-lading fee of 40. A charge line ends on its amount; a sentence does not."""
    from app.adapters.extraction.regex_extractor import trailing_amount

    header = "Dossier import LEH-26-4417 · B/L MEDUSH2604417 · Conteneur MSCU1234567 (40' HC) · Navire"
    assert trailing_amount(header) is None
    assert trailing_amount("Frais de B/L (Bill of lading fee) 1 65,00 65,00") == Decimal("65.00")
    assert trailing_amount("Total TTC 3 517,00 EUR") == Decimal("3517.00")


def test_a_registration_number_is_not_an_amount_of_tax() -> None:
    lines = [
        "TRANSDEMO SAS - TVA FR12812345678",
        "Fret maritime 2 400,00",
        "Total HT 2 400,00",
        "TVA 20 % (sur prestations taxables : 835,00) 167,00",
        "Total TTC 2 567,00",
    ]
    invoice = read(lines)
    assert invoice.vat == "167.00"


def test_a_legal_mention_naming_the_tax_is_not_the_tax() -> None:
    """That invoice's last line — an exemption article and an IBAN — names « TVA » and ends on
    « FR76 3000 4000 0500 ». Read as the last labelled amount, the VAT of that invoice was 76.

    Without a stated « Total HT » nothing else stands in the way, which is the layout half of our
    invoices use. A total is a table row and ends on its figure; this is a sentence."""
    lines = [
        "TRANSIT MARITIME ATLANTIQUE SAS",
        "Facture n° FAC-2026-08871",
        "Fret maritime Shanghai / Le Havre 2 450,00",
        "TVA 20 % (sur prestations taxables : 835,00) 167,00",
        "Total TTC 2 617,00",
        "Fret maritime et THC exonérés de TVA (art. 262 II-14° CGI). Paiement à 30 jours par "
        "virement. IBAN FR76 3000 4000 0500",
    ]
    assert read(lines).vat == "167.00"


# ---------------------------------------------------------------------------- credit notes


@pytest.mark.parametrize("lines", CREDIT_NOTES)
def test_a_credit_note_is_never_read_as_a_charge(lines: list[str], db: Session, org: Organization) -> None:
    """Five refunds, written the five ways they are written. Each one adds up to the cent — which
    is the whole problem: the arithmetic check blesses them, so it cannot be what decides."""
    invoice = read(lines)
    holds, difference = arithmetic_holds(invoice)
    assert holds and difference == Decimal("0.00")

    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 22)
    )
    assert any(note.startswith("@credit_note|") for note in checked.notes), checked.notes
    assert checked.confidence <= CAPPED_CONFIDENCE


def test_the_sign_of_a_refund_survives_to_the_review_screen() -> None:
    """A minus glued to the figure, one in parentheses, one printed after it: all three used to come
    back as money owed, and the review screen had nothing to show for it."""
    assert read(CREDIT_NOTE_MINUS_GLUED).lines[0].amount == "-2450.00"
    assert read(CREDIT_NOTE_PARENTHESES).lines[0].amount == "-275.00"
    assert read(CREDIT_NOTE_TRAILING_MINUS).lines[0].amount == "-1200.00"
    assert read(CREDIT_NOTE_MINUS_GLUED).total == "-2940.00"
    assert read(CREDIT_NOTE_PARENTHESES).vat == "-55.00"


def test_the_typographic_minus_is_read_as_a_minus() -> None:
    """A PDF font prints the typographic minus (U+2212) rather than the keyboard hyphen, and often
    sets it off with a space. The fixture is read as text: our test PDF writer is latin-1."""
    from app.adapters.extraction.regex_extractor import CREDIT_NOTE_RE, HEADER_LINES

    charge, subtotal, total = (
        CREDIT_NOTE_UNICODE_MINUS[5],
        CREDIT_NOTE_UNICODE_MINUS[7],
        CREDIT_NOTE_UNICODE_MINUS[-1],
    )
    assert trailing_amount(charge) == Decimal("-425.00")
    assert trailing_amount(subtotal) == Decimal("-425.00")
    assert trailing_amount(total) == Decimal("-510.00")
    # the sign belongs to the amount, not to the wording it was printed next to
    assert describe(charge) == "Camionnage Le Havre - Rouen"
    assert CREDIT_NOTE_RE.search("\n".join(CREDIT_NOTE_UNICODE_MINUS[:HEADER_LINES]))


def test_a_credit_note_whose_figures_are_all_positive_is_caught_by_its_heading() -> None:
    """« AVOIR N° A-2026-211 » with positive figures below is the commonest of the five, and the
    only thing that distinguishes it from a bill is the word."""
    invoice = read(CREDIT_NOTE_POSITIVE_FIGURES)
    assert invoice.lines[0].amount == "360.00"
    assert "@credit_note|header" in invoice.notes


def test_a_credit_note_line_is_shown_and_refused_rather_than_dropped(db: Session, org: Organization) -> None:
    invoice = read(CREDIT_NOTE_MINUS_GLUED)
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 14)
    )
    (line,) = checked.lines
    assert line.amount == Decimal("-2450.00")
    assert "@negative_line|-2450.00" in " ".join(line.notes)


def test_a_discount_the_patterns_cannot_name_is_read_and_left_for_a_person_to_name(
    db: Session, org: Organization
) -> None:
    """« Remise commerciale » is no charge word. The line used to be dropped, and the reviewer told
    150 € were missing; it is now a row of the table like any other — read, with its minus, without
    a cost type and with less confidence, so the invoice adds up and the question is on the screen.
    """
    invoice = read(FRENCH_WITH_DISCOUNT)
    assert [(ln.amount, ln.cost_type) for ln in invoice.lines] == [
        ("2450.00", CostType.OCEAN_FREIGHT),
        ("-150.00", None),
    ]
    assert invoice.lines[1].confidence < invoice.lines[0].confidence
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 31)
    )
    assert not [note for note in checked.notes if note.startswith("@arithmetic_mismatch")]


def test_a_discount_a_model_did_read_stays_on_the_invoice_instead_of_vanishing(
    db: Session, org: Organization
) -> None:
    """The silent half of the same defect: a negative line used to be dropped by the checks while
    `arithmetic_holds` went on counting it, so the screen showed one line of 2 450 €, the landed
    cost was 150 € too high, and the tick next to it was green."""
    invoice = FreightInvoice(
        currency="EUR",
        subtotal="2300.00",
        lines=[
            ExtractedLine(description="Fret maritime", amount="2450.00", cost_type=CostType.OCEAN_FREIGHT),
            ExtractedLine(description="Remise commerciale", amount="-150.00", confidence=0.9),
        ],
    )
    holds, difference = arithmetic_holds(invoice)
    assert holds and difference == Decimal("0.00")

    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 31)
    )
    assert [ln.amount for ln in checked.lines] == [Decimal("2450.00"), Decimal("-150.00")]
    assert "@negative_line|-150.00" in " ".join(checked.lines[1].notes)
    # one charge and one credit is a discount, not a credit note: the document still asks for money
    assert not any(note.startswith("@credit_note|") for note in checked.notes)


def test_a_line_the_reading_could_not_price_is_named_rather_than_dropped(
    db: Session, org: Organization
) -> None:
    invoice = FreightInvoice(
        currency="EUR",
        subtotal="275.00",
        lines=[
            ExtractedLine(description="THC", amount="275.00", cost_type=CostType.THC),
            ExtractedLine(description="Frais divers", amount="à confirmer"),
        ],
    )
    checked = check(
        db, fx(), invoice, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 3, 12)
    )
    assert len(checked.lines) == 1
    assert "@dropped_line|Frais divers|à confirmer" in checked.notes
    assert checked.confidence <= CAPPED_CONFIDENCE


# ---------------------------------------------------------------------------- the currency


def test_the_currency_comes_from_the_totals_not_from_the_first_word_on_the_page() -> None:
    """The forwarder prints the day's USD/EUR rate in its header; the invoice is in euros. Read as
    dollars it loses 8 % of the landed cost, with a perfectly valid rate and no note anywhere."""
    invoice = read(EXCHANGE_RATE_IN_HEADER)
    assert invoice.currency == "EUR"
    assert "@currency_guess|EUR|USD" in invoice.notes


def test_a_dollar_account_under_the_totals_does_not_make_a_dollar_invoice() -> None:
    invoice = read(DOLLAR_IBAN_IN_FOOTER)
    assert invoice.currency == "EUR"
    assert "@currency_guess|EUR|USD" in invoice.notes


def test_an_invoice_naming_one_currency_says_nothing_about_it() -> None:
    assert read(FRENCH_ONE_CONTAINER).notes == []
    assert read(USD_INVOICE).currency == "USD"


# ---------------------------------------------------------------------------- the charge words


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Magasinage conteneur 5 jours 210,00", CostType.WAREHOUSING),
        ("Frais de stationnement terminal 95,00", CostType.WAREHOUSING),
        ("Dépotage entrepôt Gennevilliers 340,00", CostType.WAREHOUSING),
        ("Surestaries 4 jours x 80,00 320,00", CostType.DEMURRAGE),
        ("Détention conteneur 3 jours 150,00", CostType.DETENTION),
        ("Surcharge BAF 185,00", CostType.OCEAN_FREIGHT),
        ("CAF 3 % 73,50", CostType.OCEAN_FREIGHT),
        ("LSS low sulphur surcharge 45,00", CostType.OCEAN_FREIGHT),
        ("PSS peak season surcharge 120,00", CostType.OCEAN_FREIGHT),
        ("ISPS destination 12,00", CostType.OCEAN_FREIGHT),
        ("Déclaration ENS 35,00", CostType.CUSTOMS_BROKERAGE),
        ("Document de transit T1 55,00", CostType.CUSTOMS_BROKERAGE),
        ("HAD honoraires 90,00", CostType.CUSTOMS_BROKERAGE),
        ("Honoraires d'agréé en douane 90,00", CostType.CUSTOMS_BROKERAGE),
        ("DTHC Le Havre 285,00", CostType.THC),
        ("Frais de B/L 65,00", CostType.BL_FEE),
        ("Frais de release 45,00", CostType.BL_FEE),
        ("Frais de dossier 55,00", CostType.BL_FEE),
        ("Échange de conteneur 180,00", CostType.DRAYAGE),
        ("Visite douane scanner 145,00", CostType.INSPECTION),
    ],
)
def test_the_words_french_forwarder_invoices_actually_use(line: str, expected: CostType) -> None:
    from app.adapters.extraction.regex_extractor import cost_type_of

    assert cost_type_of(line) == expected


def test_an_acronym_is_a_word_and_not_a_run_of_letters() -> None:
    """« CAF » is a currency surcharge and also the customs value of the goods; « ENS » sits inside
    « dépenses ». Three letters found anywhere would invent thousands of euros of freight."""
    from app.adapters.extraction.regex_extractor import cost_type_of

    assert cost_type_of("Valeur CAF déclarée 12 500,00") is None
    assert cost_type_of("Dépenses avancées pour votre compte 240,00") is None
    assert cost_type_of("Port1 quai nord 40,00") is None


def test_a_reading_with_no_total_to_check_against_says_it_is_unverified(
    db: Session, org: Organization
) -> None:
    """« It adds up » cannot be said of a reading that found no total to add up to: a missed line
    would then be invisible, which is the one failure this pipeline must not have."""
    unread_totals = FreightInvoice(
        currency="EUR",
        lines=[ExtractedLine(description="Fret maritime", amount="2450.00", currency="EUR", confidence=0.9)],
        confidence=0.9,
    )
    checked = check(
        db, fx(), unread_totals, CONTEXT, org_id=org.id, base_currency="EUR", invoice_date=date(2026, 9, 17)
    )
    assert UNVERIFIED_TOTAL_NOTE in checked.notes
    assert checked.confidence <= CAPPED_CONFIDENCE
