"""The second set: layouts written to break the reader, before looking at whether they would.

The first set (`corpus.py`) is the same four invoices in nine layouts, and once the reader handles a
layout it handles it four times: a score of 100 % there says the families are covered, not that
invoices are. Each case here is one trap, met on real forwarder and carrier documents, laid out by
hand. They are scored apart so the two figures are never averaged into a comfortable one.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from bench_invoices_corpus import LEFT, RIGHT, Case, Expected
from bench_invoices_pdf import Cell, make_pdf

from app.domain.models import CostType

F, T, B, D, H = (
    CostType.OCEAN_FREIGHT,
    CostType.THC,
    CostType.CUSTOMS_BROKERAGE,
    CostType.DRAYAGE,
    CostType.CUSTOMS_DUTY,
)
NBSP = "\u00a0"  # French thousands. The narrow one (U+202F) is not in this writer's Type 1 encoding:
# the reader's handling of it is covered by a unit test instead.


def _top(number: str, issued: str, *, word: str = "FACTURE N°") -> list[Cell]:
    return [
        Cell(LEFT, 800, "ATLANTIQUE TRANSIT & LOGISTIQUE", 13),
        Cell(LEFT, 785, "Terminal de France, 76700 Gonfreville-l'Orcher - TVA FR28451234567", 8),
        Cell(330, 750, f"{word} {number}", 11),
        Cell(330, 736, f"Date : {issued}"),
        Cell(LEFT, 750, "Client : RoulezBio SAS"),
        Cell(LEFT, 720, "Conteneur MSCU4821990 - B/L MEDUSH2604417"),
    ]


def _rows(
    y: float, rows: Sequence[tuple[str, ...]], xs: Sequence[tuple[float, str]]
) -> tuple[list[Cell], float]:
    cells: list[Cell] = []
    for row in rows:
        for column, (text, (x, align)) in enumerate(zip(row, xs, strict=False)):
            if text:
                cells.append(Cell(x, y, text, align=align, column=column))
        y -= 16
    return cells, y


def _expect(number: str, issued: date, lines: Sequence[tuple[str, CostType | None]], *, vat: str = "0",
            net_printed: bool = True, recoverable: str = "0") -> Expected:  # fmt: skip
    amounts = tuple((Decimal(amount), cost_type) for amount, cost_type in lines)
    net = sum((a for a, _ in amounts), Decimal(0))
    return Expected(
        number, issued, "EUR", net if net_printed else None, net + Decimal(vat), amounts, ("MSCU4821990",)
    )


def vat_rate_after_the_amount() -> Case:
    """Désignation | Qté | PU | Montant HT | TVA % — the rightmost figure is a rate, not money."""
    xs = [(LEFT, "left"), (330.0, "right"), (400.0, "right"), (490.0, "right"), (RIGHT, "right")]
    head = [("Désignation", "Qté", "P.U. HT", "Montant HT", "TVA %")]
    rows = [
        ("Fret maritime Ningbo / Le Havre", "1", "2 710,00", "2 710,00", "0,00"),
        ("THC destination", "1", "262,00", "262,00", "0,00"),
        ("Dédouanement import", "1", "135,00", "135,00", "20,00"),
        ("Camionnage Le Havre - Lyon", "1", "465,00", "465,00", "20,00"),
    ]
    cells, y = _rows(670, head + rows, xs)
    cells += [Cell(400, y - 20, "Total HT"), Cell(RIGHT, y - 20, "3 572,00", align="right")]
    cells += [Cell(400, y - 34, "TVA 20,00 %"), Cell(RIGHT, y - 34, "120,00", align="right")]
    cells += [Cell(400, y - 48, "Total TTC EUR"), Cell(RIGHT, y - 48, "3 692,00", align="right")]
    lines = [("2710.00", F), ("262.00", T), ("135.00", B), ("465.00", D)]
    expected = _expect("FA-26-0917", date(2026, 9, 17), lines, vat="120.00")
    return Case(
        "taux de TVA après le montant",
        "pièges",
        make_pdf([_top("FA-26-0917", "17/09/2026") + cells]),
        expected,
    )


def wording_on_two_lines() -> Case:
    xs = [(LEFT, "left"), (RIGHT, "right")]
    rows = [
        ("Désignation", "Montant HT"),
        ("Fret maritime Shanghai / Le Havre, navire MSC GÜLSÜN,", ""),
        ("voyage FE437W, conteneur 40' HC", "2 450,00"),
        ("Frais de manutention portuaire et de sûreté", "285,00"),
        ("Honoraires d'agréé en douane, déclaration IM4", ""),
        ("n° 26FR00760012345678", "120,00"),
    ]
    cells, y = _rows(670, rows, xs)
    cells += [Cell(400, y - 20, "Total HT"), Cell(RIGHT, y - 20, "2 855,00", align="right")]
    cells += [Cell(400, y - 34, "Total TTC EUR"), Cell(RIGHT, y - 34, "2 855,00", align="right")]
    expected = _expect("FA-26-0918", date(2026, 9, 17), [("2450.00", F), ("285.00", None), ("120.00", B)])
    return Case(
        "libellé sur deux lignes", "pièges", make_pdf([_top("FA-26-0918", "17/09/2026") + cells]), expected
    )


def narrow_spaces_and_symbols() -> Case:
    xs = [(LEFT, "left"), (RIGHT, "right")]
    rows = [
        ("Libellé", "Montant"),
        ("Fret maritime", f"2{NBSP}450,00 €"),
        ("Surcharge carburant BAF", "310,00 €"),
        ("Droits de douane", f"12{NBSP}336,89 €"),
        ("Remise commerciale", "-150,00 €"),
    ]
    cells, y = _rows(670, rows, xs)
    cells += [Cell(400, y - 20, "Net à payer"), Cell(RIGHT, y - 20, f"14{NBSP}946,89 €", align="right")]
    lines = [("2450.00", F), ("310.00", F), ("12336.89", H), ("-150.00", None)]
    expected = _expect("FA-26-0919", date(2026, 9, 17), lines, net_printed=False)
    return Case(
        "espace insécable et symbole €",
        "pièges",
        make_pdf([_top("FA-26-0919", "17/09/2026") + cells]),
        expected,
    )


def money_above_the_table() -> Case:
    """No heading row, and figures with cents in the file block: they are not charges."""
    top = [
        *_top("FA-26-0920", "17/09/2026"),
        Cell(LEFT, 700, "Valeur en douane :"), Cell(200, 700, "28 450,00 EUR"),
        Cell(LEFT, 686, "Poids brut :"), Cell(200, 686, "12 450,00 kg"),
        Cell(LEFT, 672, "Cours douane USD :"), Cell(200, 672, "0,86"),
    ]  # fmt: skip
    xs = [(LEFT, "left"), (RIGHT, "right")]
    rows = [("Fret maritime", "2 450,00"), ("THC", "285,00"), ("Taxe de port", "48,00")]
    cells, y = _rows(630, rows, xs)
    cells += [Cell(400, y - 20, "Total HT"), Cell(RIGHT, y - 20, "2 783,00", align="right")]
    cells += [Cell(400, y - 34, "Total TTC EUR"), Cell(RIGHT, y - 34, "2 783,00", align="right")]
    expected = _expect("FA-26-0920", date(2026, 9, 17), [("2450.00", F), ("285.00", T), ("48.00", None)])
    return Case("montants au-dessus du tableau", "pièges", make_pdf([top + cells]), expected)


def vat_row_inside_the_table() -> Case:
    """An English statement: the VAT is a row of the table, and the total follows it."""
    xs = [(LEFT, "left"), (470.0, "left"), (RIGHT, "right")]
    rows = [
        ("DESCRIPTION", "CUR", "AMOUNT"),
        ("SEA FREIGHT SHANGHAI - LE HAVRE", "EUR", "2,450.00"),
        ("DESTINATION THC", "EUR", "285.00"),
        ("DELIVERY ORDER FEE", "EUR", "65.00"),
        ("CUSTOMS CLEARANCE", "EUR", "120.00"),
        ("VAT 20% ON 185.00", "EUR", "37.00"),
    ]
    cells, y = _rows(670, rows, xs)
    cells += [Cell(330, y - 20, "TOTAL AMOUNT DUE EUR"), Cell(RIGHT, y - 20, "2,957.00", align="right")]
    expected = _expect("INV-260921", date(2026, 9, 17),
                       [("2450.00", F), ("285.00", T), ("65.00", None), ("120.00", B)], vat="37.00",
                       net_printed=False)  # fmt: skip
    top = _top("INV-260921", "17/09/2026", word="INVOICE No.")
    return Case("ligne de TVA dans le tableau", "pièges", make_pdf([top + cells]), expected)


def import_vat_disbursed() -> Case:
    """The forwarder advanced the duty and the import VAT: both are rows, and the VAT is recoverable."""
    xs = [(LEFT, "left"), (RIGHT, "right")]
    rows = [
        ("Désignation", "Montant"),
        ("Droits de douane (débours)", "1 336,89"),
        ("TVA à l'importation (débours)", "5 957,38"),
        ("Honoraires d'agréé en douane", "95,00"),
        ("Avance de fonds 1,5 %", "109,41"),
    ]
    cells, y = _rows(670, rows, xs)
    cells += [Cell(400, y - 20, "Total HT"), Cell(RIGHT, y - 20, "7 498,68", align="right")]
    cells += [Cell(400, y - 34, "TVA 20 %"), Cell(RIGHT, y - 34, "40,88", align="right")]
    cells += [Cell(400, y - 48, "Total TTC EUR"), Cell(RIGHT, y - 48, "7 539,56", align="right")]
    expected = _expect("FA-26-0922", date(2026, 9, 17),
                       [("1336.89", H), ("5957.38", CostType.IMPORT_VAT), ("95.00", B), ("109.41", None)],
                       vat="40.88")  # fmt: skip
    return Case(
        "TVA import en débours", "pièges", make_pdf([_top("FA-26-0922", "17/09/2026") + cells]), expected
    )


def totals_without_the_word_total() -> Case:
    xs = [(LEFT, "left"), (RIGHT, "right")]
    rows = [("Prestation", "Montant HT"), ("Fret maritime", "2 450,00"), ("Frais de dossier", "45,00")]
    cells, y = _rows(670, rows, xs)
    cells += [Cell(400, y - 20, "Montant HT"), Cell(RIGHT, y - 20, "2 495,00", align="right")]
    cells += [Cell(400, y - 34, "Montant TVA"), Cell(RIGHT, y - 34, "9,00", align="right")]
    cells += [Cell(400, y - 48, "Net à payer EUR"), Cell(RIGHT, y - 48, "2 504,00", align="right")]
    expected = _expect(
        "FA-26-0923", date(2026, 9, 17), [("2450.00", F), ("45.00", CostType.BL_FEE)], vat="9.00"
    )
    return Case(
        "totaux sans le mot « total »",
        "pièges",
        make_pdf([_top("FA-26-0923", "17/09/2026") + cells]),
        expected,
    )


def cases() -> list[Case]:
    return [
        vat_rate_after_the_amount(),
        wording_on_two_lines(),
        narrow_spaces_and_symbols(),
        money_above_the_table(),
        vat_row_inside_the_table(),
        import_vat_disbursed(),
        totals_without_the_word_total(),
    ]
