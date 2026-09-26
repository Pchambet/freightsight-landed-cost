"""The invoice-reading bench, as a ratchet: what is read today must still be read tomorrow.

`tests/bench_invoices_*.py` builds real PDF tables in the layouts forwarders use and scores the rule-based
reader on them. The figures below are the measured ones; they only ever go up. The rule that is not a
figure: a reading may be incomplete, never *silently* — a missed or invented line must fail the
arithmetic check, because that is what puts the invoice in front of a person with a warning.
"""

from __future__ import annotations

from decimal import Decimal

import bench_invoices_hard as hard
from bench_invoices_corpus import cases
from bench_invoices_scoring import run

from app.adapters.extraction.regex_extractor import SPACES, parse_amount, read_row


def test_every_layout_family_is_read_whole_row_by_row_and_column_by_column() -> None:
    scores, overall, by_family = run(cases())
    assert overall.cases == 48 and len(by_family) == 9
    wrong = [s.case.name for s in scores if s.lines_wrong or s.failure or not all(s.header.values())]
    assert wrong == []
    assert overall.type_accuracy == 1.0


def test_the_traps_are_read_too_and_none_of_them_silently_wrong() -> None:
    scores, overall, _ = run(hard.cases())
    assert [s.case.name for s in scores if s.silent] == []
    assert [s.case.name for s in scores if s.lines_wrong or s.failure] == []
    assert overall.invented_lines == 0


def test_the_figure_under_the_amount_heading_wins_over_the_rightmost_one() -> None:
    position = (1, 4)  # Qté | P.U. | Montant HT | TVA %  →  the amount is second from the right
    row = read_row(["Dédouanement import", "1", "135,00", "135,00", "20,00"], position)
    assert row is not None and row.amount == Decimal("135.00")
    # a row with a blank cell cannot be counted from the right: its last column holding money is used
    short = read_row(["THC destination", "1", "262,00", "262,00"], position)
    assert short is not None and short.amount == Decimal("262.00")


def test_two_cells_run_together_are_not_an_amount() -> None:
    assert parse_amount("65,000,00") is None  # « 65,00 » and « 0,00 », drawn right to left
    assert parse_amount("1.234.567") == Decimal("1234567.00")
    assert parse_amount("1,234,567.89") == Decimal("1234567.89")


def test_the_narrow_no_break_space_groups_thousands_like_any_other() -> None:
    assert parse_amount("12 336,89".translate(SPACES)) == Decimal("12336.89")  # noqa: RUF001
    assert parse_amount("2 450,00".translate(SPACES)) == Decimal("2450.00")  # noqa: RUF001
