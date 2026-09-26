"""What people write about a container — its number in a ledger label, "1x40HQ", "CMA CGM", "Anvers" —
read into what the product can check and compare. Nothing is guessed: what cannot be told stays None."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.domain.boxes import container_numbers_in, is_container_number, iso_size_type, length_of, scac_of
from app.domain.imports.parsing import (
    ColumnDates,
    DateOrder,
    ImportError_,
    Table,
    infer_date_orders,
    parse_date,
)
from app.domain.ports import unlocode
from app.domain.sample_history import container_number


def test_a_container_number_is_trusted_only_with_its_check_digit() -> None:
    assert is_container_number("CSQU3054383")  # the standard's own example
    assert is_container_number("csqu 305438 3")
    assert not is_container_number("CSQU3054384")  # one digit off: the check digit catches it
    assert not is_container_number("CSQ3054383")
    for serial in (1, 482199, 999999):
        assert is_container_number(container_number("MSCU", serial))


def test_the_numbers_written_in_a_ledger_label_are_found_and_only_those() -> None:
    label = "FACT 0412 TRANSDEMO DOS 5678 CSQU3054383 FRET + THC"
    assert container_numbers_in(label) == ["CSQU3054383"]
    assert container_numbers_in("DOS CSQU3054384 et CSQU3054383, CSQU3054383") == ["CSQU3054383"]
    assert container_numbers_in("Facture 2026-0412 du 12/03") == []


@pytest.mark.parametrize(
    ("written", "iso"),
    [
        ("40HC", "45G1"), ("40' HC", "45G1"), ("1x40HQ", "45G1"), ("40 HC", "45G1"),
        ("20GP", "22G1"), ("20'DV", "22G1"), ("40DV", "42G1"), ("40 pieds", "42G1"),
        ("45HC", "L5G1"), ("45G1", "45G1"), ("22G1", "22G1"), ("L5G1", "L5G1"),
        ("40RF", "45R1"), ("20OT", "22U1"), ("conteneur", None), ("", None), (None, None),
    ],
)  # fmt: skip
def test_the_size_type_a_spreadsheet_means(written: str | None, iso: str | None) -> None:
    assert iso_size_type(written) == iso


def test_the_length_the_audit_compares_on() -> None:
    assert (length_of("45G1"), length_of("22G1"), length_of("L5G1"), length_of(None)) == (
        "40",
        "20",
        "45",
        "",
    )


def test_a_line_is_named_by_its_four_letters() -> None:
    assert scac_of("CMA CGM") == "CMDU" and scac_of("MSC") == "MSCU" and scac_of("Hapag-Lloyd") == "HLCU"
    assert scac_of("MAEU") == "MAEU" and scac_of("sudu") == "SUDU"  # a code nobody listed is still a code
    assert scac_of("Compagnie du Ponant") is None and scac_of("") is None


def test_a_port_is_looked_up_by_name_before_code() -> None:
    assert unlocode("NINGBO") == "CNNGB" and unlocode("Le Havre") == "FRLEH" and unlocode("Anvers") == "BEANR"
    assert unlocode("GENOA") == "ITGOA"  # not the Georgian code GE-NOA it looks like
    assert unlocode("CN NGB") == "CNNGB" and unlocode("frleh") == "FRLEH"
    assert unlocode("XXABC") is None and unlocode("Mars") is None and unlocode(None) is None


# ---------------------------------------------------------------------------- dates in a spreadsheet


def _table(values: list[str]) -> Table:
    return Table(columns=["ETA"], rows=[{"ETA": v} for v in values], encoding="utf-8", delimiter=";")


def test_one_date_settles_whether_the_day_or_the_month_comes_first() -> None:
    assert infer_date_orders(_table(["03/04/2026", "17/04/2026"]), {"ETA"})["ETA"] == ColumnDates(
        "dmy", example="17/04/2026"
    )
    assert infer_date_orders(_table(["03/04/2026", "04/17/2026"]), {"ETA"})["ETA"] == ColumnDates(
        "mdy", example="04/17/2026"
    )
    # nothing decides: the French order, and the report says so
    assert infer_date_orders(_table(["03/04/2026", ""]), {"ETA"})["ETA"] == ColumnDates(
        "dmy", ambiguous=True, example="03/04/2026"
    )
    assert infer_date_orders(_table(["2026-04-03"]), {"ETA"}) == {}  # ISO needs no guess


@pytest.mark.parametrize(
    ("raw", "order", "expected"),
    [
        ("2026-07-01", "dmy", date(2026, 7, 1)),
        ("2026-07-01 00:00:00", "dmy", date(2026, 7, 1)),  # a spreadsheet's date cell
        ("01/07/2026", "dmy", date(2026, 7, 1)),
        ("07/01/2026", "mdy", date(2026, 7, 1)),
        ("01.07.26", "dmy", date(2026, 7, 1)),
        ("1-7-2026", "dmy", date(2026, 7, 1)),
        ("", "dmy", None),
    ],
)
def test_a_date_is_read_in_the_order_its_column_uses(
    raw: str, order: DateOrder, expected: date | None
) -> None:
    assert parse_date(raw, order=order) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "demain", "13/13/2026", "2026-13-01"])
def test_what_is_not_a_date_says_so(raw: str) -> None:
    with pytest.raises(ImportError_) as caught:
        parse_date(raw)
    assert caught.value.code == "NOT_A_DATE"


def test_a_container_keeps_its_type_as_the_iso_code_whatever_notation_came_in(client: TestClient) -> None:
    """The audit compares boxes by the length their code gives; "40HC" and "40' HC" are one box."""
    made = client.post("/api/v1/containers", json={"container_number": "CSQU3054383", "iso_type": "40' HC"})
    assert made.status_code == 201, made.text
    assert made.json()["iso_type"] == "45G1"
    url = f"/api/v1/containers/{made.json()['id']}"
    assert client.patch(url, json={"iso_type": "1x20GP"}).json()["iso_type"] == "22G1"
    assert client.patch(url, json={"iso_type": "Box spéciale"}).json()["iso_type"] == "Box spéciale"  # kept
    assert client.patch(url, json={"iso_type": ""}).json()["iso_type"] is None
