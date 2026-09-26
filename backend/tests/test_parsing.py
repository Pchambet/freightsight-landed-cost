from __future__ import annotations

import io
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.imports.parsing import (
    ColumnNumbers,
    ImportError_,
    header_signature,
    infer_comma_conventions,
    is_ambiguous_unit_column,
    normalize_header,
    parse_decimal,
    parse_table,
    suggest_mapping,
)
from app.domain.models import ImportKind

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_decimal_formats() -> None:
    assert parse_decimal("12500.50") == Decimal("12500.50")
    assert parse_decimal("12 500,50") == Decimal("12500.50")
    assert parse_decimal("12.500,50") == Decimal("12500.50")
    assert parse_decimal("12,500.50") == Decimal("12500.50")
    assert parse_decimal("1,500") == Decimal("1500")
    assert parse_decimal("4,5 %") == Decimal("4.5")
    assert parse_decimal("") is None
    with pytest.raises(ImportError_):
        parse_decimal("12,5 pcs")


def test_normalize_header_strips_accents_and_punctuation() -> None:
    assert normalize_header("N° commande") == "n_commande"
    assert normalize_header(" Qté ") == "qte"
    assert normalize_header("Prix unitaire (EUR)") == "prix_unitaire_eur"


def test_legacy_fixture_and_mapping() -> None:
    table = parse_table((FIXTURES / "legacy_po_container.csv").read_bytes())
    assert table.columns == [
        "po_number",
        "supplier_name",
        "total_value",
        "container_number",
        "allocation_percentage",
    ]
    assert len(table.rows) == 3
    mapping = suggest_mapping(ImportKind.LEGACY_PO_CONTAINER, table.columns)
    assert mapping == {c: c for c in table.columns}


def test_excel_fr_bom_semicolon_and_aliases() -> None:
    content = (
        "﻿N° commande;Fournisseur;Réf;Qté;Prix unitaire;Poids;Conteneur\n"
        "PO-1;Société Générale Pneus;TYRE-205;600;12,50;2,1;MSCU1234567\n"
    ).encode()
    table = parse_table(content)
    assert table.delimiter == ";" and table.encoding == "utf-8"
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, table.columns)
    assert mapping == {
        "po_number": "N° commande",
        "supplier_name": "Fournisseur",
        "sku": "Réf",
        "quantity": "Qté",
        "unit_price": "Prix unitaire",
        "unit_weight_kg": "Poids",
        "container_number": "Conteneur",
    }
    assert table.rows[0]["Fournisseur"] == "Société Générale Pneus"


def test_cp1252_export_decodes() -> None:
    table = parse_table("po_number,supplier,qty,price\nPO-1,Société Générale,1,100\n".encode("cp1252"))
    assert table.rows[0]["supplier"] == "Société Générale"
    assert table.encoding.lower() in ("cp1252", "windows-1252", "latin_1", "iso8859_1", "cp1250", "cp1254")


def test_header_signature_is_order_independent() -> None:
    assert header_signature(["B", "a"]) == header_signature(["A", "b"])


def test_missing_header_row() -> None:
    with pytest.raises(ImportError_) as exc:
        parse_table(b"")
    assert exc.value.code == "EMPTY_FILE"


def test_a_lone_comma_is_read_the_same_way_for_the_whole_column() -> None:
    """A weight of "1,250" is one kilo and a quarter in any French spreadsheet. Read value by
    value it became 1 250 kg, and in an allocation by weight that one line swallowed the freight
    of the whole container. One other value of the same column settles it: "7,25" or "0,850" can
    only be decimal, and the column follows."""
    table = parse_table(
        b"po_number;sku;quantity;unit_price;poids\nPO-1;A;10;12,50;1,250\nPO-2;B;20;7,90;0,850\n"
    )
    conventions = infer_comma_conventions(table, {"poids", "unit_price"})
    assert conventions["poids"].comma == "decimal" and not conventions["poids"].ambiguous
    assert parse_decimal("1,250", comma=conventions["poids"].comma) == Decimal("1.250")
    assert parse_decimal("1,250", comma="thousands") == Decimal("1250")


def test_a_column_where_every_comma_could_be_either_is_flagged_not_guessed() -> None:
    table = parse_table(b"po_number;sku;quantity;poids\nPO-1;A;10;1,250\nPO-2;B;20;3,500\n")
    convention = infer_comma_conventions(table, {"poids"})["poids"]
    # ";" is what Excel FR writes precisely because the comma is the decimal separator.
    assert convention.comma == "decimal" and convention.ambiguous and convention.example == "1,250"

    # A file written with "," as the delimiter is not a French spreadsheet: the English reading
    # stays the default there, and is flagged just the same.
    english = parse_table(b'po_number,sku,quantity,weight\nPO-1,A,10,"1,250"\n')
    assert infer_comma_conventions(english, {"weight"})["weight"] == ColumnNumbers(
        "thousands", ambiguous=True, example="1,250"
    )


def test_a_column_with_a_dot_and_three_digit_commas_is_english() -> None:
    table = parse_table(b'po_number,sku,price\nPO-1,A,"12,500.50"\nPO-2,B,"1,250.00"\n')
    assert infer_comma_conventions(table, {"price"})["price"].comma == "thousands"
    assert parse_decimal("12,500.50", comma="thousands") == Decimal("12500.50")


def test_a_number_in_scientific_notation_with_a_comma_is_refused() -> None:
    """Excel FR shows a number too wide for its column as "3,70012E+12". Dropping the comma gave
    3.70012E+17 — a hundred thousand times the value, accepted without a word."""
    with pytest.raises(ImportError_) as exc:
        parse_decimal("3,70012E+12")
    assert exc.value.code == "AMBIGUOUS_SCIENTIFIC"
    assert parse_decimal("3.70012E+12") == Decimal("3.70012E+12")  # unambiguous, still accepted


def test_the_header_is_found_under_a_title_block_and_the_totals_row_is_dropped() -> None:
    table = parse_table((FIXTURES / "imports" / "sage100_commandes.csv").read_bytes())
    assert table.header_row == 3 and table.totals_row_ignored
    assert table.columns[0] == "N° commande" and table.columns[-1] == "N° TC"
    assert len(table.rows) == 3
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, table.columns)
    assert mapping["po_number"] == "N° commande"
    assert mapping["sku"] == "Réf. article" and mapping["unit_price"] == "PU HT"
    assert mapping["unit_weight_kg"] == "Poids (kg)" and mapping["unit_volume_cbm"] == "Volume (m3)"
    assert mapping["container_number"] == "N° TC"


def test_ebp_and_odoo_column_names_are_recognised() -> None:
    ebp = parse_table((FIXTURES / "imports" / "ebp_commandes.csv").read_bytes())
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, ebp.columns)
    assert mapping["po_number"] == "N° cde" and mapping["sku"] == "Code article"
    assert mapping["description"] == "Libellé" and mapping["unit_price"] == "Prix unitaire HT"
    assert mapping["unit_weight_kg"] == "Poids net" and mapping["unit_volume_cbm"] == "Cubage"

    odoo = parse_table((FIXTURES / "imports" / "odoo_lignes_commande.csv").read_bytes())
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, odoo.columns)
    assert mapping["po_number"] == "Commande" and mapping["order_date"] == "Date de commande"
    assert mapping["sku"] == "Article" and mapping["quantity"] == "Quantité"


def test_a_weight_or_volume_column_that_does_not_name_its_unit_is_ambiguous() -> None:
    assert is_ambiguous_unit_column("Poids (kg)") and is_ambiguous_unit_column("Volume")
    assert is_ambiguous_unit_column("Cubage") and is_ambiguous_unit_column("Poids brut")
    assert not is_ambiguous_unit_column("Poids unitaire (kg)")
    assert not is_ambiguous_unit_column("unit_weight_kg")
    assert not is_ambiguous_unit_column("Volume unitaire (m³)")


def test_an_xlsx_keeps_long_numbers_readable_and_finds_its_header() -> None:
    """Excel stores every number as a float: str() turns a 13-digit EAN into '3.70012e+12' and a
    quantity of 10 into '10.0'. And a Sage xlsx puts its title where the header is expected."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Export commandes fournisseurs", None, None, None])
    ws.append(["Généré le 17/09/2026", None, None, None])
    ws.append(["po_number", "sku", "quantity", "unit_price"])
    ws.append(["PO-1", 3700123456789, 10, 12.5])
    buffer = io.BytesIO()
    wb.save(buffer)

    table = parse_table(buffer.getvalue(), "export.xlsx")
    assert table.header_row == 3
    assert table.columns == ["po_number", "sku", "quantity", "unit_price"]
    assert table.rows == [
        {"po_number": "PO-1", "sku": "3700123456789", "quantity": "10", "unit_price": "12.5"}
    ]


def test_an_order_line_that_starts_with_the_word_total_is_not_a_totals_row() -> None:
    """The last line of a nested export, order number blank, whose article is called TOTAL-500: it is
    goods, and dropping it would also push its share of the freight onto every other line."""
    content = (
        b"po_number;fournisseur;sku;designation;quantite;prix_unitaire\n"
        b"PO-1;Acme;A1;Cable;10;12,50\n"
        b";;A2;Moteur;5;8,00\n"
        b";;TOTAL-500;Totalisateur horaire;3;99,00\n"
    )
    table = parse_table(content)
    assert not table.totals_row_ignored
    assert [row["sku"] for row in table.rows] == ["A1", "A2", "TOTAL-500"]


@pytest.mark.parametrize("label", ["Total", "TOTAL HT", "Sous-total", "Total général", "Totaux"])
def test_a_real_totals_line_is_still_dropped(label: str) -> None:
    header = "po_number;fournisseur;sku;designation;quantite;prix_unitaire"
    content = f"{header}\nPO-1;Acme;A1;Cable;10;12,50\n{label};;;;10;125,00\n".encode()
    table = parse_table(content)
    assert table.totals_row_ignored and len(table.rows) == 1
