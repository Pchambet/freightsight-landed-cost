from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain.imports import parsing
from app.domain.imports.fields import FIELDS
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


@pytest.mark.parametrize("value", ["Infinity", "-inf", "NaN", "sNaN"])
def test_a_value_that_is_no_finite_number_is_refused_by_name(value: str) -> None:
    """Decimal reads « Infinity » and « NaN » as numbers: a line total divided by them raised a 500."""
    with pytest.raises(ImportError_) as exc:
        parse_decimal(value)
    assert exc.value.code == "NOT_A_NUMBER"


@pytest.mark.parametrize("value", ["1e30", "1E+12", "1 000 000 000 000", "-1e12"])
def test_a_number_no_column_of_the_books_holds_is_refused_by_name(value: str) -> None:
    with pytest.raises(ImportError_) as exc:
        parse_decimal(value)
    assert exc.value.code == "NUMBER_TOO_LARGE"
    assert parse_decimal("999 999 999 999,99") == Decimal("999999999999.99")


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
    assert parse_decimal("3.70012E+5") == Decimal("370012")  # unambiguous, still accepted


def test_the_header_is_found_under_a_title_block_and_the_totals_row_is_dropped() -> None:
    table = parse_table((FIXTURES / "imports" / "sage100_commandes.csv").read_bytes())
    assert table.header_row == 3 and table.totals_row_ignored
    assert table.columns[0] == "N° commande" and table.columns[-1] == "N° TC"
    assert len(table.rows) == 3
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, table.columns)
    assert mapping["po_number"] == "N° commande"
    assert mapping["sku"] == "Réf. article" and mapping["unit_price"] == "PU HT"
    # « Poids (kg) » is as often the line's weight as one piece's: a person says which (see below)
    assert "unit_weight_kg" not in mapping and "unit_volume_cbm" not in mapping
    assert mapping["container_number"] == "N° TC"


def test_ebp_and_odoo_column_names_are_recognised() -> None:
    ebp = parse_table((FIXTURES / "imports" / "ebp_commandes.csv").read_bytes())
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, ebp.columns)
    assert mapping["po_number"] == "N° cde" and mapping["sku"] == "Code article"
    assert mapping["description"] == "Libellé" and mapping["unit_price"] == "Prix unitaire HT"
    assert "unit_weight_kg" not in mapping and "unit_volume_cbm" not in mapping

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


def test_a_weight_is_mapped_per_unit_or_per_line_only_when_its_header_says_which() -> None:
    """A line total read as a unit weight multiplies that line's share of the freight by its quantity:
    two lines of 50 kg each, 100 and 10 pieces, split 3 000 € as 2 727,27 / 272,73 instead of 1 500 /
    1 500. A header that does not say which is left for a person to map, to one field or the other."""
    columns = ["po_number", "sku", "Poids unitaire (kg)", "Volume total (m3)", "Poids brut", "Cubage"]
    mapping = suggest_mapping(ImportKind.PURCHASE_ORDERS, columns)
    assert (
        mapping["unit_weight_kg"] == "Poids unitaire (kg)"
        and mapping["line_volume_cbm"] == "Volume total (m3)"
    )
    assert "Poids brut" not in mapping.values() and "Cubage" not in mapping.values()
    assert suggest_mapping(ImportKind.PURCHASE_ORDERS, ["Poids total ligne (kg)"]) == {
        "line_weight_kg": "Poids total ligne (kg)"
    }
    # a tariff is one row per article: its weight can only be one piece's
    assert suggest_mapping(ImportKind.PRODUCTS, ["sku", "Poids"])["unit_weight_kg"] == "Poids"


@pytest.mark.parametrize("kind", list(FIELDS))
def test_a_template_written_with_the_api_headers_maps_itself(kind: ImportKind) -> None:
    for language in ("header_fr", "header_en"):
        columns = [getattr(field, language) for field in FIELDS[kind]]
        assert suggest_mapping(kind, columns) == {
            field.name: getattr(field, language) for field in FIELDS[kind]
        }, language


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


def _xlsx(*rows: list[object]) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _with_stray_cell(content: bytes) -> bytes:
    """The same workbook with one empty formatted cell in the very last cell of the sheet, XFD1048576,
    and a sheet that says it spans all of Excel: what a slip of the mouse in a real file does."""
    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension ref="[^"]*"', b'<dimension ref="A1:XFD1048576"', data)
                stray = b'<row r="1048576"><c r="XFD1048576" s="0"/></row>'
                data = data.replace(b"</sheetData>", stray + b"</sheetData>")
            target.writestr(item, data)
    return out.getvalue()


def test_a_sheet_that_claims_every_row_and_column_of_excel_is_read_by_its_cells() -> None:
    """A 1.5 KB workbook with a cell at XFD1048576 made openpyxl pad every row to 16 384 columns and
    yield a million rows: 143 GB projected, the API killed for every tenant."""
    content = _with_stray_cell(_xlsx(["po_number", "sku", "quantity"], ["PO-1", "A", 10]))
    started = time.monotonic()
    table = parse_table(content, "commandes.xlsx")
    assert time.monotonic() - started < 5
    assert table.columns == ["po_number", "sku", "quantity"]
    assert table.rows == [{"po_number": "PO-1", "sku": "A", "quantity": "10"}]


def test_a_file_over_the_row_cap_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsing, "MAX_ROWS", 3)
    with pytest.raises(ImportError_) as exc:
        parse_table(b"po_number;sku\nPO-1;A\nPO-2;B\nPO-3;C\n")
    assert exc.value.code == "TOO_MANY_ROWS" and exc.value.params == {"limit": 3}
    with pytest.raises(ImportError_) as exc:
        parse_table(_xlsx(["po_number", "sku"], ["PO-1", "A"], ["PO-2", "B"], ["PO-3", "C"]), "f.xlsx")
    assert exc.value.code == "TOO_MANY_ROWS"
    # blank rows are not rows
    assert len(parse_table(b"po_number;sku\n\n\nPO-1;A\n;\nPO-2;B\n").rows) == 2


def test_a_file_over_the_column_or_cell_cap_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    wide = ";".join(f"c{i}" for i in range(parsing.MAX_COLUMNS + 1)).encode()
    with pytest.raises(ImportError_) as exc:
        parse_table(wide + b"\n" + b"1;" * parsing.MAX_COLUMNS + b"1\n")
    assert exc.value.code == "TOO_MANY_COLUMNS" and exc.value.params == {"limit": parsing.MAX_COLUMNS}
    with pytest.raises(ImportError_) as exc:
        far_right: list[object] = ["PO-1", *[None] * (parsing.MAX_COLUMNS - 1), "far"]
        parse_table(_xlsx(["po_number", "sku"], far_right), "f.xlsx")
    assert exc.value.code == "TOO_MANY_COLUMNS"
    # empty cells past the last column are no column at all
    assert parse_table(b"po_number;sku" + b";" * 300 + b"\nPO-1;A\n").columns == ["po_number", "sku"]

    monkeypatch.setattr(parsing, "MAX_CELLS", 5)
    with pytest.raises(ImportError_) as exc:
        parse_table(b"po_number;sku\nPO-1;A\nPO-2;B\n")
    assert exc.value.code == "TOO_MANY_CELLS" and exc.value.params == {"limit": 5}


def test_a_workbook_that_inflates_past_the_cap_is_refused_before_it_is_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shared strings are loaded whole even in read-only mode: a few KB of zipped repetition is
    gigabytes once inflated. The sizes the archive declares are checked before anything is read."""
    monkeypatch.setattr(parsing, "MAX_XLSX_INFLATED_BYTES", 1000)
    with pytest.raises(ImportError_) as exc:
        parse_table(_xlsx(["po_number", "sku"], ["PO-1", "A"]), "f.xlsx")
    assert exc.value.code == "XLSX_TOO_LARGE"


def _with_part(content: bytes, name: str, data: bytes, content_type: str, rel_type: str) -> bytes:
    """The same workbook with one more part, declared in the manifest and the workbook's relations."""
    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    ns = "http://schemas.openxmlformats.org"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename == name:
                continue
            part = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                override = f'<Override PartName="/{name}" ContentType="{content_type}"/></Types>'
                part = part.replace(b"</Types>", override.encode())
            if item.filename == "xl/_rels/workbook.xml.rels":
                rel = f'<Relationship Id="rId99" Type="{ns}/officeDocument/2006/relationships/{rel_type}" '
                rel += f'Target="/{name}"/></Relationships>'
                part = part.replace(b"</Relationships>", rel.encode())
            target.writestr(item, part)
        target.writestr(name, data)
    return out.getvalue()


def test_a_workbook_part_parsed_whole_is_refused_past_its_own_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """openpyxl builds a tree of every part but the sheet: an 89 KB workbook whose styles hold two
    million empty `<xf/>` — 36 MB unzipped, far under the archive's cap — took 22 s and 1.2 GB."""
    monkeypatch.setattr(parsing, "MAX_XLSX_PART_BYTES", 20_000)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    styles = f'<styleSheet {ns}><cellXfs count="5000">{"<xf/>" * 5000}</cellXfs></styleSheet>'.encode()
    content = _xlsx(["po_number", "sku"], ["PO-1", "A"])
    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = styles if item.filename == "xl/styles.xml" else source.read(item.filename)
            target.writestr(item, data)
    with pytest.raises(ImportError_) as exc:
        parse_table(out.getvalue(), "f.xlsx")
    assert exc.value.code == "XLSX_TOO_LARGE"

    # the sheet itself is streamed and capped by its rows, not by this
    rows: list[list[object]] = [[f"PO-{i}", "A"] for i in range(2000)]
    long = _xlsx(["po_number", "sku"], *rows)
    assert zipfile.ZipFile(io.BytesIO(long)).getinfo("xl/worksheets/sheet1.xml").file_size > 20_000
    assert len(parse_table(long, "f.xlsx").rows) == 2000


def test_a_workbook_whose_shared_strings_inflate_past_their_cap_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared strings are read whole, one Python string each: a 414 KB workbook of ten million
    "x" took 49 s and a gigabyte before a single row was looked at."""
    monkeypatch.setattr(parsing, "MAX_XLSX_STRINGS_BYTES", 20_000)
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    strings = f'<sst {ns} count="2000" uniqueCount="2000">{"<si><t>x</t></si>" * 2000}</sst>'.encode()
    content = _with_part(
        _xlsx(["po_number", "sku"], ["PO-1", "A"]),
        "xl/sharedStrings.xml",
        strings,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
        "sharedStrings",
    )
    with pytest.raises(ImportError_) as exc:
        parse_table(content, "f.xlsx")
    assert exc.value.code == "XLSX_TOO_LARGE"
    monkeypatch.setattr(parsing, "MAX_XLSX_STRINGS_BYTES", 40_000)
    assert parse_table(content, "f.xlsx").rows == [{"po_number": "PO-1", "sku": "A"}]


def _rewritten(content: bytes, parts: dict[str, bytes | Callable[[bytes], bytes]]) -> bytes:
    """The same workbook with some parts replaced, or rewritten from what they hold."""
    source, out = zipfile.ZipFile(io.BytesIO(content)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            change = parts.pop(item.filename, data)
            target.writestr(item, change(data) if callable(change) else change)
        for name, added in parts.items():
            target.writestr(name, added(b"") if callable(added) else added)
    return out.getvalue()


def _with_rows(content: bytes, rows: bytes) -> bytes:
    sheet = "xl/worksheets/sheet1.xml"
    return _rewritten(content, {sheet: lambda data: data.replace(b"</sheetData>", rows + b"</sheetData>")})


def test_every_row_element_counts_against_the_sheet_whatever_it_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows holding no value were dropped before anything counted them: 3 million `<row/>` in a 28 KB
    upload took 18 s and 330 MB, and one `<row r="10000000"/>` made the reader pad ten million rows."""
    content = _xlsx(["po_number", "sku"], ["PO-1", "A"])
    started = time.monotonic()
    with pytest.raises(ImportError_) as exc:
        parse_table(_with_rows(content, b'<row r="10000000"/>'), "f.xlsx")
    assert exc.value.code == "TOO_MANY_ROWS" and exc.value.params == {"limit": parsing.MAX_ROWS}
    assert time.monotonic() - started < 1

    monkeypatch.setattr(parsing, "MAX_SHEET_ROWS", 1000)
    with pytest.raises(ImportError_) as exc:
        parse_table(_with_rows(content, b"<row/>" * 999), "f.xlsx")
    assert exc.value.code == "TOO_MANY_ROWS"
    with pytest.raises(ImportError_) as exc:
        parse_table(_with_rows(content, b'<row r="1001"/>'), "f.xlsx")
    assert exc.value.code == "TOO_MANY_ROWS"
    # up to the sheet's last row, formatted rows with nothing in them are read past, not kept
    honest = _with_rows(
        content, b"".join(b'<row r="%d" s="1" customFormat="1"/>' % i for i in range(3, 1001))
    )
    assert parse_table(honest, "f.xlsx").rows == [{"po_number": "PO-1", "sku": "A"}]


def test_every_cell_element_counts_against_the_cap_valued_or_not(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eight million empty `<c/>` in a 160 KB upload took 15 s and 2.9 GB, the row then found blank."""
    monkeypatch.setattr(parsing, "MAX_CELLS", 100)
    content = _with_rows(
        _xlsx(["po_number", "sku"], ["PO-1", "A"]), b'<row r="3">' + b'<c s="0"/>' * 97 + b"</row>"
    )
    with pytest.raises(ImportError_) as exc:
        parse_table(content, "f.xlsx")
    assert exc.value.code == "TOO_MANY_CELLS" and exc.value.params == {"limit": 100}


def test_only_the_first_worksheet_is_read_and_nothing_it_points_to() -> None:
    """A chartsheet was read whole whatever its size (32 MB inflated took 600 MB), and every sheet entry
    parsed the sheet's relations again (30 entries on one 4 MB part: 6 s). Neither is opened any more:
    here both are not even XML, and the file reads."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["po_number", "sku"])
    wb.active.append(["PO-1", "A"])
    wb.create_chartsheet(index=0)
    buffer = io.BytesIO()
    wb.save(buffer)
    entries = b"".join(b'<sheet name="S%d" sheetId="%d" r:id="rId2"/>' % (i, i + 10) for i in range(30))
    content = _rewritten(
        buffer.getvalue(),
        {
            "xl/chartsheets/sheet1.xml": b"\x00" * 1000,
            "xl/worksheets/_rels/sheet1.xml.rels": b"\x00" * 1000,
            "xl/workbook.xml": lambda data: data.replace(b"</sheets>", entries + b"</sheets>"),
        },
    )
    assert parse_table(content, "f.xlsx").rows == [{"po_number": "PO-1", "sku": "A"}]


def test_a_cell_longer_than_a_spreadsheet_holds_is_refused_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """One 150 MB cell was read whole into memory (400 MB) before anything looked at it."""
    monkeypatch.setattr(parsing, "MAX_CELL_CHARS", 1000)
    with pytest.raises(ImportError_) as exc:
        parse_table(_xlsx(["po_number", "sku"], ["PO-1", "x" * 1001]), "f.xlsx")
    assert exc.value.code == "XLSX_UNREADABLE"
    longest = parse_table(_xlsx(["po_number", "sku"], ["PO-1", "x" * 1000]), "f.xlsx")
    assert longest.rows[0]["sku"] == "x" * 1000


def test_every_xml_element_of_the_workbook_counts_whatever_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only rows and cells were counted: 50 million `<v/>` inside one cell, or unknown tags beside the
    rows, are a 200 KB upload that cost 25 s of the API's one process, each element a Python call."""
    monkeypatch.setattr(parsing, "MAX_XLSX_ELEMENTS", 300)
    content = _xlsx(["po_number", "sku"], ["PO-1", "A"])
    assert parse_table(content, "f.xlsx").rows == [{"po_number": "PO-1", "sku": "A"}]
    for filler in (b'<row r="3"><c r="A3">' + b"<v/>" * 300 + b"</c></row>", b"<x/>" * 300):
        with pytest.raises(ImportError_) as exc:
            parse_table(_with_rows(content, filler), "f.xlsx")
        assert exc.value.code == "TOO_MANY_CELLS"
    ns = b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    styles = b"<styleSheet %s><cellXfs>%s</cellXfs></styleSheet>" % (ns, b"<xf/>" * 300)
    with pytest.raises(ImportError_) as exc:
        parse_table(_rewritten(content, {"xl/styles.xml": styles}), "f.xlsx")
    assert exc.value.code == "TOO_MANY_CELLS"


def test_a_conditional_format_s_number_format_does_not_change_a_cell_s() -> None:
    """A conditional format brings its own number formats, in `<dxfs>`, and may reuse a cell format's
    id: only those of `<numFmts>` say how a cell reads, so a date stays a date."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["po_number", "order_date"])
    wb.active.append(["PO-1", date(2026, 7, 15)])
    wb.active["B2"].number_format = "dd/mm/yyyy"
    buffer = io.BytesIO()
    wb.save(buffer)
    content = buffer.getvalue()
    baseline = parse_table(content, "f.xlsx").rows
    styles = zipfile.ZipFile(io.BytesIO(content)).read("xl/styles.xml").decode()
    (date_id,) = re.findall(r'numFmtId="(\d+)" formatCode="dd/mm/yyyy"', styles)
    dxf = f'<dxfs count="1"><dxf><numFmt numFmtId="{date_id}" formatCode="0.00"/></dxf></dxfs>'
    styles = re.sub(r"<dxfs[^>]*/>|<dxfs.*?</dxfs>", "", styles)
    styles = styles.replace("</styleSheet>", dxf + "</styleSheet>")
    assert parse_table(_rewritten(content, {"xl/styles.xml": styles.encode()}), "f.xlsx").rows == baseline


def test_a_number_format_longer_than_excel_writes_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Which styles are dates is openpyxl's test, a regular expression quadratic in a format's unclosed
    `[`: one 40 000-character format named by fifty styles, a 4 KB upload, cost 52 s — and one 8 MB format,
    hours. Excel's own formats stop at 255 characters, and each is tested once, not once per style."""
    ns = b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'

    def styled(code: bytes) -> bytes:
        styles = b'<styleSheet %s><numFmts><numFmt numFmtId="164" formatCode="%s"/></numFmts>' % (ns, code)
        styles += b"<cellXfs>%s</cellXfs></styleSheet>" % (b'<xf numFmtId="164"/>' * 50)
        return _rewritten(_xlsx(["po_number", "sku"], ["PO-1", "A"]), {"xl/styles.xml": styles})

    started = time.monotonic()
    with pytest.raises(ImportError_) as exc:
        parse_table(styled(b"[" * 40_000), "f.xlsx")
    assert exc.value.code == "XLSX_UNREADABLE" and time.monotonic() - started < 1
    calls: list[str] = []

    def is_date_format(code: str) -> bool:
        calls.append(code)
        return False

    monkeypatch.setattr("openpyxl.styles.numbers.is_date_format", is_date_format)
    assert parse_table(styled(b"[" * 255), "f.xlsx").rows == [{"po_number": "PO-1", "sku": "A"}]
    assert calls.count("[" * 255) == 1


def test_an_xml_token_larger_than_a_spreadsheet_writes_is_refused_as_it_comes() -> None:
    """Expat keeps a tag or a comment whole until it ends: one 150 MB attribute, a 154 KB upload, took
    760 MB — its text, then a Python string of it. A cell's text comes in pieces and is not one."""
    content = _xlsx(["po_number", "sku"], ["PO-1", "A"])
    huge = b"a" * (parsing.MAX_XML_TOKEN_BYTES + 2 * 65536)  # seen once the next chunk of 64 KB is in
    for token in (b'<row r="3" x="%s"/>' % huge, b"<!--%s-->" % huge, b"<%s/>" % huge):
        with pytest.raises(ImportError_) as exc:
            parse_table(_with_rows(content, token), "f.xlsx")
        assert exc.value.code == "XLSX_UNREADABLE"
    cell = b'<row r="3"><c r="A3" t="inlineStr"><is><t>%s</t></is></c></row>' % (
        b"x" * parsing.MAX_CELL_CHARS
    )
    assert (
        len(parse_table(_with_rows(content, cell), "f.xlsx").rows[1]["po_number"]) == parsing.MAX_CELL_CHARS
    )


def _shared_strings(content: bytes) -> bytes:
    """The same workbook with its text in a shared strings part, as Excel writes it: one entry in rich
    runs, one with a phonetic reading that is not part of the text."""
    found: list[bytes] = []

    def to_index(match: re.Match[bytes]) -> bytes:
        found.append(match.group(2))
        return match.group(1) + b' t="s"><v>%d</v></c>' % (len(found) - 1)

    sheet = zipfile.ZipFile(io.BytesIO(content)).read("xl/worksheets/sheet1.xml")
    sheet = re.sub(rb'(<c r="[A-Z]+\d+")[^>]*t="inlineStr"><is><t>([^<]*)</t></is></c>', to_index, sheet)
    entries = [b"<si><t>%s</t></si>" % text for text in found]
    entries[0] = b"<si><r><t>po_</t></r><r><rPr><b/></rPr><t>number</t></r></si>"
    entries[1] = b'<si><t>sku</t><rPh sb="0" eb="1"><t>SKU-PHONETIC</t></rPh></si>'
    ns = b'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    strings = b"<sst %s>%s</sst>" % (ns, b"".join(entries))
    return _with_part(
        _rewritten(content, {"xl/worksheets/sheet1.xml": sheet}),
        "xl/sharedStrings.xml",
        strings,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml",
        "sharedStrings",
    )


@pytest.mark.parametrize("epoch", ["1900", "1904"])
@pytest.mark.parametrize("shared", [False, True], ids=["inline", "shared"])
def test_an_xlsx_reads_each_kind_of_cell_as_excel_shows_it(epoch: str, shared: bool) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl.utils.datetime import CALENDAR_MAC_1904

    wb = openpyxl.Workbook()
    if epoch == "1904":
        wb.epoch = CALENDAR_MAC_1904
    ws = wb.active
    ws.append(["po_number", "sku", "order_date", "quantity", "unit_price", "flag", "duration"])
    # a spreadsheet's dates are naive: Excel has no time zone
    ws.append(["PO-1", "A", datetime(2026, 7, 1), 10, 0.1, True, None])  # noqa: DTZ001
    ws.append(["PO-2", "B", datetime(2026, 7, 2, 14, 30), 3700123456789, 1e-7, False, timedelta(hours=30)])  # noqa: DTZ001
    ws["G3"].number_format = "[h]:mm:ss"
    ws["H2"] = "=1+1"
    buffer = io.BytesIO()
    wb.save(buffer)
    content = _shared_strings(buffer.getvalue()) if shared else buffer.getvalue()

    table = parse_table(content, "f.xlsx")
    assert table.columns == ["po_number", "sku", "order_date", "quantity", "unit_price", "flag", "duration"]
    assert table.rows == [
        {"po_number": "PO-1", "sku": "A", "order_date": "2026-07-01 00:00:00", "quantity": "10",
         "unit_price": "0.1", "flag": "True"},
        {"po_number": "PO-2", "sku": "B", "order_date": "2026-07-02 14:30:00", "quantity": "3700123456789",
         "unit_price": "0.0000001", "flag": "False", "duration": "1 day, 6:00:00"},
    ]  # fmt: skip


@pytest.mark.parametrize(
    "content",
    [
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512,
        b"po_number;sku\nPO-1;A\n",
        b"PK\x03\x04garbage",
    ],
    ids=["xls-renamed", "csv-renamed", "truncated"],
)
def test_a_file_that_is_not_a_workbook_is_refused_by_name(content: bytes) -> None:
    with pytest.raises(ImportError_) as exc:
        parse_table(content, "ledger.xlsx")
    assert exc.value.code == "XLSX_UNREADABLE"


def test_a_csv_cell_longer_than_the_reader_accepts_is_refused_by_name() -> None:
    with pytest.raises(ImportError_) as exc:
        parse_table(b'po_number;sku\nPO-1;"' + b"x" * 200_000 + b'"\n')
    assert exc.value.code == "CSV_UNREADABLE"


def test_a_column_nothing_settles_follows_the_convention_another_column_proves() -> None:
    """Google Sheets FR exports with "," and quotes "12,5": the price column proves the decimal comma,
    and the weight column's "1,250" was still read as 1 250 kg because the delimiter was not ";"."""
    table = parse_table(b'po_number,sku,unit_price,weight\nPO-1,A,"12,5","1,250"\nPO-2,B,"7,9","2,500"\n')
    conventions = infer_comma_conventions(table, {"unit_price", "weight"})
    assert conventions["weight"] == ColumnNumbers("decimal", ambiguous=True, example="1,250")

    # and the other way round: a column that proves the English reading settles its neighbours
    english = parse_table(b'po_number;sku;unit_price;weight\nPO-1;A;"1,250.50";"1,250"\n')
    assert infer_comma_conventions(english, {"unit_price", "weight"})["weight"].comma == "thousands"
