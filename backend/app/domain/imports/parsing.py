"""Decode and tabulate an uploaded CSV/XLSX; suggest a column mapping. Pure functions."""

from __future__ import annotations

import csv
import io
import posixpath
import pyexpat
import re
import unicodedata
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from charset_normalizer import from_bytes

from app.domain.imports.fields import FIELDS, required_fields
from app.domain.models import ImportKind

CONTAINER_NUMBER_RE = re.compile(r"^[A-Z]{4}[0-9]{7}$")

# Target field -> accepted header spellings (normalised: lowercase, no accents, spaces -> _).
# Order matters inside a list: the first alias present in the file wins, so the spellings that
# name the unit explicitly ("poids unitaire") come before the ones that do not ("poids").
TARGET_ALIASES: dict[ImportKind, dict[str, list[str]]] = {
    ImportKind.PURCHASE_ORDERS: {
        "po_number": [
            "po_number",
            "po",
            "n_commande",
            "no_commande",
            "num_commande",
            "numero_commande",
            "n_cde",
            "no_cde",
            "cde",
            "n_bc",
            "no_bc",
            "bon_de_commande",
            "n_bon_de_commande",
            "commande",
            "order",
            "order_number",
        ],
        "supplier_name": ["supplier_name", "supplier", "fournisseur", "vendor"],
        "currency": ["currency", "devise", "cur"],
        "order_date": ["order_date", "date_commande", "date_de_commande", "date"],
        "incoterm": ["incoterm"],
        "line_no": ["line_no", "line", "ligne", "n_ligne", "pos"],
        "sku": [
            "sku",
            "reference",
            "ref",
            "reference_article",
            "ref_article",
            "code_article",
            "article",
            "item",
            "product",
            "code",
        ],
        "description": ["description", "designation", "designation_article", "libelle", "label"],
        "quantity": ["quantity", "qty", "qte", "quantite"],
        "unit_price": [
            "unit_price",
            "prix_unitaire_ht",
            "prix_unitaire",
            "pu_ht",
            "pu",
            "price",
            "prix",
        ],
        "unit_weight_kg": [
            "unit_weight_kg",
            "poids_unitaire",
            "poids_unitaire_kg",
            "unit_weight",
            "weight_kg",
            "weight",
            "poids_net",
            "poids_net_kg",
            "poids_brut",
            "poids_brut_kg",
            "poids",
            "poids_kg",
            "kg",
        ],
        "unit_volume_cbm": [
            "unit_volume_cbm",
            "volume_unitaire",
            "volume_unitaire_m3",
            "unit_volume",
            "volume_cbm",
            "volume_m3",
            "cubage",
            "volume",
            "cbm",
            "m3",
        ],
        "line_weight_kg": [
            "line_weight_kg",
            "poids_total",
            "poids_total_kg",
            "poids_total_ligne",
            "poids_total_ligne_kg",
            "poids_ligne",
            "poids_ligne_kg",
            "total_weight",
            "total_weight_kg",
            "line_weight",
        ],
        "line_volume_cbm": [
            "line_volume_cbm",
            "volume_total",
            "volume_total_m3",
            "volume_total_ligne",
            "volume_total_ligne_m3",
            "volume_ligne",
            "volume_ligne_m3",
            "total_volume",
            "total_volume_cbm",
            "line_volume",
            "cbm_total",
        ],
        "hs_code": ["hs_code", "hs", "nc", "code_nc", "taric", "tariff"],
        "duty_rate": ["duty_rate", "duty", "droits", "taux_droits"],
        "container_number": [
            "container_number",
            "container",
            "conteneur",
            "n_conteneur",
            "no_conteneur",
            "n_tc",
            "no_tc",
            "tc",
            "ctn",
            "box",
        ],
        "container_quantity": ["container_quantity", "qty_in_container", "qte_conteneur", "loaded_qty"],
    },
    ImportKind.LEGACY_PO_CONTAINER: {
        "po_number": [
            "po_number",
            "po",
            "n_commande",
            "no_commande",
            "numero_commande",
            "n_cde",
            "n_bc",
            "commande",
        ],
        "supplier_name": ["supplier_name", "supplier", "fournisseur"],
        "total_value": ["total_value", "value", "montant", "valeur", "total"],
        "container_number": ["container_number", "container", "conteneur", "n_tc"],
        "allocation_percentage": ["allocation_percentage", "allocation_pct", "allocation"],
    },
    # A tracking sheet: one row per container, or per container and order. « Réception » and
    # « livraison » are the goods reaching the warehouse, never the box reaching the port: no alias.
    ImportKind.CONTAINERS: {
        "container_number": ["container_number", "container", "conteneur", "n_conteneur", "no_conteneur",
                             "numero_conteneur", "n_tc", "no_tc", "tc", "ctn", "box", "equipment_number"],
        "iso_type": ["iso_type", "type_de_conteneur", "type_conteneur", "type_tc", "type", "taille", "size",
                     "size_type", "container_type", "equipment_type"],
        "po_numbers": ["po_numbers", "po_number", "po", "n_commandes", "n_commande", "no_commande",
                       "commandes", "commande", "n_cde", "cde", "orders", "order", "n_bc"],
        "shipment_reference": ["shipment_reference", "n_b_l", "n_bl", "no_bl", "bl", "b_l", "bl_number",
                               "b_l_number", "bill_of_lading", "connaissement", "n_connaissement", "booking",
                               "n_booking", "booking_number", "expedition", "dossier", "n_dossier",
                               "shipment"],
        "carrier": ["carrier", "carrier_scac", "scac", "compagnie_maritime", "compagnie", "armateur",
                    "shipping_line", "compagnie_scac"],
        "origin_port": ["origin_port", "port_de_chargement", "port_chargement", "pol", "port_of_loading",
                        "loading_port", "port_depart", "port_de_depart", "origine", "origin"],
        "destination_port": ["destination_port", "port_de_dechargement", "port_dechargement", "pod",
                             "port_of_discharge", "discharge_port", "port_arrivee", "port_d_arrivee",
                             "destination"],
        "etd": ["etd", "date_de_depart_prevue", "depart_prevu"],
        "eta": ["eta", "date_d_arrivee_prevue", "arrivee_prevue"],
        "ata": ["ata", "arrivee_reelle", "date_d_arrivee", "date_arrivee", "arrivee", "actual_arrival",
                "arrival_date", "arrival"],
        "discharged_at": ["discharged_at", "decharge_le", "date_de_dechargement", "date_dechargement",
                          "dechargement", "discharge_date", "discharged"],
        "gate_out_at": ["gate_out_at", "sorti_du_terminal_le", "sortie_terminal", "date_de_sortie",
                        "date_sortie",
                        "gate_out", "sortie", "enlevement", "date_d_enlevement"],
        "empty_returned_at": ["empty_returned_at", "vide_restitue_le", "restitution", "date_de_restitution",
                              "restitution_vide", "empty_return", "empty_returned", "retour_vide",
                              "date_retour_vide"],
    },
    # A costs ledger — the purchase journal's lines for the forwarders, hauliers and customs brokers.
    ImportKind.COSTS: {
        # The FEC's own names too (EcritureDate, CompAuxLib, PieceRef, EcritureLib, CompteNum, Debit,
        # Credit): every French accounting program exports it, the purchase journal included.
        "cost_date": ["cost_date", "date_du_cout", "date", "date_piece", "date_facture", "date_de_facture",
                      "date_ecriture", "invoice_date", "date_comptable", "piecedate", "ecrituredate"],
        "vendor": ["vendor", "prestataire", "fournisseur", "tiers", "libelle_tiers", "raison_sociale",
                   "supplier", "compauxlib"],
        "invoice_number": ["invoice_number", "n_facture", "no_facture", "numero_facture", "n_piece", "piece",
                           "facture", "invoice_no", "invoice", "reference_piece", "pieceref"],
        "label": ["label", "libelle", "libelle_ecriture", "designation", "description", "intitule", "objet",
                  "nature", "ecriturelib"],
        "cost_type": ["cost_type", "type_de_cout", "type_cout", "type", "type_de_frais", "nature_de_frais",
                      "categorie"],
        "amount": ["amount", "montant_ht", "montant", "montant_hors_taxes", "ht", "debit", "amount_excl_vat",
                   "net_amount"],
        "currency": ["currency", "devise", "cur"],
        "container_number": ["container_number", "n_conteneur", "conteneur", "container", "no_conteneur",
                             "n_tc", "tc"],
        "shipment_reference": ["shipment_reference", "n_b_l", "n_bl", "bl", "b_l", "bl_number",
                               "connaissement", "booking", "n_dossier", "dossier", "expedition"],
        "po_number": ["po_number", "n_commande", "commande", "po", "n_cde", "order"],
        "account": ["account", "compte", "n_compte", "no_compte", "numero_compte", "numero_de_compte",
                    "compte_general", "comptenum", "compte_num", "gl_account", "account_number"],
        "credit": ["credit", "montant_credit", "credit_amount"],
    },
    # The tariff: one row per article. « Prix » alone is a purchase price in an order file, never a
    # selling price: no alias.
    ImportKind.PRODUCTS: {
        "sku": ["sku", "reference", "ref", "reference_article", "ref_article", "code_article", "article",
                "item", "product", "code"],
        "description": ["description", "designation", "libelle", "label"],
        "hs_code": ["hs_code", "code_sh", "hs", "nc", "code_nc", "taric", "tariff", "nomenclature"],
        "duty_rate": ["duty_rate", "taux_de_droits", "taux_droits", "droits", "duty"],
        "unit_weight_kg": ["unit_weight_kg", "poids_unitaire_kg", "poids_unitaire", "poids_net",
                           "poids_net_kg",
                           "poids", "poids_kg", "weight", "weight_kg"],
        "unit_volume_cbm": ["unit_volume_cbm", "volume_unitaire_m3", "volume_unitaire", "volume", "volume_m3",
                            "cbm", "m3"],
        "sale_price": ["sale_price", "prix_de_vente_ht", "prix_de_vente", "prix_vente", "pv_ht", "pv",
                       "tarif",
                       "prix_public", "selling_price"],
        "sale_currency": ["sale_currency", "devise_de_vente", "devise_vente", "devise", "currency"],
    },
}  # fmt: skip

REQUIRED: dict[ImportKind, list[str]] = {kind: required_fields(kind) for kind in FIELDS}

# A weight or a volume column that does not name its unit is ambiguous: in a real ERP export
# "Poids (kg)" is as often the weight of the whole line as the weight of one piece, and reading a
# line total as a unit weight inflates that line's share of the freight by the quantity itself. An
# order file's ambiguous column is therefore never mapped on its own: a person maps it per unit or as
# the line's total, and the report says so next to the mapping either way.
_UNIT_EXPLICIT = ("unit", "unitaire", "par_unite", "par_piece", "_pce", "piece")
#: The order fields an ambiguous header is not mapped to without a person.
PER_UNIT_FIELDS = ("unit_weight_kg", "unit_volume_cbm")

# Header rows the exporters put above the real one ("Export commandes fournisseurs", "Généré le
# ..."), and the totals row they put under the last line, are noise between the real table and us.
HEADER_SCAN_ROWS = 12
HEADER_MIN_MATCHES = 3
#: A totals label: the word on its own or followed by more words ("Total HT", "Sous-total général"),
#: never glued to a reference ("TOTAL-500", "Total_12") nor the start of a longer word ("Totalisateur").
_TOTALS_LABEL = re.compile(
    r"^(total|totaux|sous[\s-]?total|cumul|somme|r[ée]capitulatif)(?![\w-])(?!\s*[-_/#]?\s*\d)", re.IGNORECASE
)


# Characters a Western European business file actually contains: French, plus the accents of a
# German/Spanish/Italian/Portuguese supplier name, plus the punctuation Excel writes. Everything
# else in the Latin-1 range (š ž ø å ð þ ý ¤ ¦ ¹ ¼ ...) is the signature of *another* code page
# read as cp1252 — a Czech or Polish export — and disqualifies the shortcut below.
_WESTERN_OK = frozenset(
    "àâäçéèêëîïôöùûüÿœæáíóúñãõß"
    "ÀÂÄÇÉÈÊËÎÏÔÖÙÛÜŒÆÁÍÓÚÑÃÕ"
    "°€£§µ²³«»·±"
    "\u2013\u2014\u2018\u2019\u201c\u201d\u2026"  # the dashes and curly quotes Excel writes
)
# Tried in the order of what a French SME actually produces, before the detector gets a say.
_WESTERN_CODECS = ("cp1252", "iso-8859-15", "mac_roman")

_SCIENTIFIC_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+$")
_COMMA_GROUP_RE = re.compile(r",(\d*)")
_LEADING_ZERO_COMMA_RE = re.compile(r"^[+-]?0?,\d")

CommaMeaning = Literal["auto", "decimal", "thousands"]
#: Past this no column a file's number is written to holds it — the widest, an amount to the cent, stops
#: just under — and the database refused the whole file with a 500 where the row should say so.
MAX_NUMBER = Decimal(10) ** 12


#: What one file may hold. The upload is capped at 10 MB, but a 10 MB CSV is 1.75 million rows (half a
#: gigabyte once read) and a 1.5 KB workbook can claim every cell of Excel: the reader stops at these
#: and refuses the file by name. A quarter of an importer's orders or costs is a few thousand rows.
MAX_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELLS = 2_000_000
#: What a workbook may weigh once unzipped: zipped repetition inflates a thousandfold. This bounds the
#: sheet, which is streamed; the other parts read have caps of their own.
MAX_XLSX_INFLATED_BYTES = 200 * 1024 * 1024
#: What each part read for the workbook's structure (manifest, workbook, relations, styles) may weigh
#: unzipped. 36 MB of empty `<xf/>` styles took 1.2 GB as a tree; streamed, this bounds the time. Excel's
#: own ceiling of 64 000 cell styles fits well under it.
MAX_XLSX_PART_BYTES = 8 * 1024 * 1024
#: What the shared strings may weigh unzipped: they are kept whole, one Python string each, about eight
#: times the text for short ones (33 MB of 8-letter labels took 250 MB). A hundred thousand rows with
#: three distinct labels each fit.
MAX_XLSX_STRINGS_BYTES = 24 * 1024 * 1024
#: Excel's own last row. A sheet's rows are counted against it whether they hold a value or not, and
#: a row declared past it is refused at once: a few KB of `<row/>`, or one `<row r="10000000"/>`, cost
#: minutes when only the rows holding a value were counted. A sheet formatted down to its last row —
#: what a whole column's formatting leaves in some exports — still reads, in about a second.
MAX_SHEET_ROWS = 1_048_576
#: The longest cell read, as the CSV reader's: Excel's own ceiling is 32 767 characters.
MAX_CELL_CHARS = 131_072
#: The XML elements read in a workbook, every part and every name together: each one costs a Python
#: call, and those the caps above do not count — 50 million `<v/>` inside one cell, a 200 KB upload —
#: cost 25 s. Two million cells written inline (`<c><is><t>`) and a million rows fit.
MAX_XLSX_ELEMENTS = 4 * MAX_CELLS
#: The longest tag or comment read: expat keeps one whole until it ends, so one 150 MB attribute took
#: 760 MB. A spreadsheet's tags are a few dozen bytes; a cell's text is no token, it comes in pieces.
MAX_XML_TOKEN_BYTES = 256 * 1024
#: Excel's own ceiling on a number format. Which styles are dates is openpyxl's regular expression,
#: quadratic in a format's unclosed `[`: one 8 MB format cost hours.
MAX_NUMBER_FORMAT_CHARS = 255


class ImportError_(ValueError):
    def __init__(self, code: str, message: str, **params: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params


@dataclass(frozen=True)
class ColumnNumbers:
    """What a comma means in one column, decided over the whole file rather than value by value."""

    comma: CommaMeaning
    ambiguous: bool = False
    example: str | None = None


DateOrder = Literal["dmy", "mdy"]


@dataclass(frozen=True)
class ColumnDates:
    """Whether a column writes the day or the month first, decided over the whole file."""

    order: DateOrder
    ambiguous: bool = False
    example: str | None = None


@dataclass(frozen=True)
class Table:
    columns: list[str]  # original headers, stripped
    rows: list[dict[str, str]]  # header -> raw cell, keyed by original header
    encoding: str
    delimiter: str
    header_row: int = 1  # physical line of the file the header was read from
    totals_row_ignored: bool = False


def _is_exotic(ch: str) -> bool:
    return ord(ch) > 127 and ch not in _WESTERN_OK


def _western_reading(content: bytes, codec: str) -> str | None:
    """The reading of `content` under `codec`, or None when it does not look like a Western file.

    Two things disqualify a reading: a C0/C1 control character, which no spreadsheet ever writes
    and which is exactly what a Latin-1 reading of a Windows file produces; and accented letters
    French (or a European supplier name) never uses. A Czech "Množství" read as cp1252 still
    spells a "ž" — that is the signal that the file belongs to another code page and that the
    detector, not this shortcut, should answer.
    """
    try:
        text = content.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None
    if any(unicodedata.category(ch) == "Cc" and ch not in "\t\r\n" for ch in text):
        return None
    header, _, body = text.partition("\n")
    if any(_is_exotic(ch) for ch in header):
        return None
    exotic = sum(1 for ch in body if _is_exotic(ch))
    non_ascii = sum(1 for ch in body if ord(ch) > 127)
    return None if exotic * 10 > non_ascii else text


def decode_bytes(content: bytes) -> tuple[str, str]:
    """utf-8, then the Western code pages, then the detector — in that order, and it matters.

    charset_normalizer answers iso8859_14 or mac_latin2 on a plain Sage/EBP export because the one
    accented byte it has to explain (0xB0, the "°" of "N° commande") fits those tables too. The
    header then reads "NḞ commande", and po_number — the one mandatory field of the whole flow —
    loses its automatic mapping on the most common French export there is. So a cp1252 reading
    that spells plausible French wins over any guess; the detector only gets the files no Western
    code page reads cleanly, which is what it is actually good at (cp1250, Cyrillic, CJK).
    """
    try:
        return content.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    for codec in _WESTERN_CODECS:
        text = _western_reading(content, codec)
        if text is not None:
            return text, codec
    best = from_bytes(content).best()
    if best is None:
        raise ImportError_("ENCODING_UNKNOWN", "Could not detect the file encoding.")
    return str(best), best.encoding


def sniff_delimiter(text: str) -> str:
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


def normalize_header(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = s.strip().strip('"').casefold()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def _add_template_headers() -> None:
    """The headers of the templates and of the application's own exports, as aliases of their field:
    « Code SH » and « Taux de droits », written by the exports, did not map back on their own."""
    for kind, fields in FIELDS.items():
        for field in fields:
            known = TARGET_ALIASES[kind].setdefault(field.name, [field.name])
            for header in (field.header_fr, field.header_en):
                if (name := normalize_header(header)) not in known:
                    known.append(name)


_add_template_headers()
_ALL_ALIASES = frozenset(
    alias for kind in TARGET_ALIASES.values() for aliases in kind.values() for alias in aliases
)


def is_ambiguous_unit_column(column: str) -> bool:
    """True for a weight/volume header that does not say whether it is per unit or per line."""
    normalised = normalize_header(column)
    return not any(marker in normalised for marker in _UNIT_EXPLICIT)


def _pick_header(rows: list[list[str]]) -> int:
    """The first row that reads like a header: three cells we recognise as column names.

    Three is the threshold that separates a real header from a title line or a date stamp, and a
    file whose columns we recognise fewer than three of is one the user will map by hand anyway —
    there the first physical row is still the best guess.
    """
    for index, cells in enumerate(rows[:HEADER_SCAN_ROWS]):
        if sum(1 for cell in cells if normalize_header(cell) in _ALL_ALIASES) >= HEADER_MIN_MATCHES:
            return index
    return 0


def _is_totals_row(cells: list[str], column_count: int) -> bool:
    """A trailing "Total …" line, and nothing that merely looks like one.

    Dropping a row is the one thing here that loses goods without a word, so the test is narrow on
    purpose: the first non-empty cell must BE a totals label ("Total", "Sous-total HT", "TOTAL
    GÉNÉRAL"), not start with the letters — a part number "TOTAL-500" or a "Totalisateur horaire" is
    an order line — and the row must be mostly empty, as a totals line is. A continuation row of a
    nested export (order number blank, article, designation, quantity and price filled) fails both.
    """
    filled = [c.strip() for c in cells if c.strip()]
    if not filled or len(filled) > max(2, column_count // 2):
        return False
    return _TOTALS_LABEL.match(filled[0]) is not None


def _tabulate(rows: list[list[str]], encoding: str, delimiter: str) -> Table:
    if not rows:
        raise ImportError_("EMPTY_FILE", "The file has no header row.")
    head = _pick_header(rows)
    header = [h.strip() for h in rows[head]]
    body = [values for values in rows[head + 1 :] if any(v.strip() for v in values)]
    totals = bool(body) and _is_totals_row(body[-1], len(header))
    if totals:
        body = body[:-1]
    data = [
        {header[i]: values[i].strip() for i in range(min(len(header), len(values))) if header[i]}
        for values in body
    ]
    return Table(
        columns=[h for h in header if h],
        rows=data,
        encoding=encoding,
        delimiter=delimiter,
        header_row=head + 1,
        totals_row_ignored=totals,
    )


def _too_many(what: str) -> ImportError_:
    limit = {"rows": MAX_ROWS, "columns": MAX_COLUMNS, "cells": MAX_CELLS}[what]
    return ImportError_(f"TOO_MANY_{what.upper()}", f"More than {limit} {what} in the file", limit=limit)


class _Bounded:
    """The file's non-blank rows, each without its trailing blank cells — refused by name as soon as
    it holds more rows, columns or cells than one import may, before the rest is even read."""

    def __init__(self) -> None:
        self.rows: list[list[str]] = []
        self.cells = 0

    def add(self, row: Sequence[str]) -> None:
        width = len(row)
        while width and not row[width - 1].strip():
            width -= 1
        if not width:
            return
        if width > MAX_COLUMNS:
            raise _too_many("columns")
        if len(self.rows) == MAX_ROWS:
            raise _too_many("rows")
        self.cells += width
        if self.cells > MAX_CELLS:
            raise _too_many("cells")
        self.rows.append(list(row[:width]))


def _bounded(rows: Iterable[Sequence[str]]) -> list[list[str]]:
    kept = _Bounded()
    for row in rows:
        kept.add(row)
    return kept.rows


def parse_table(content: bytes, filename: str | None = None) -> Table:
    if filename and filename.lower().endswith((".xlsx", ".xlsm")):
        return _parse_xlsx(content)
    text, encoding = decode_bytes(content)
    delimiter = sniff_delimiter(text)
    try:
        rows = _bounded(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as e:
        # A cell longer than the reader accepts (128 KB) or a stray NUL: a file problem, said as one.
        raise ImportError_("CSV_UNREADABLE", f"The file cannot be read as CSV: {e}") from e
    return _tabulate(rows, encoding, delimiter)


def _cell_text(value: object) -> str:
    """Excel keeps every number as a float; str() would betray the long ones.

    A 13-digit EAN typed in a text column comes back as 3700123456789.0 and str() writes
    '3.700123456789e+12' — an SKU nobody can match, and a quantity of 10 written '10.0'.
    """
    if value is None:
        return ""
    if isinstance(value, float):
        text = format(Decimal(str(value)), "f")
        return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"
    return str(value).strip()


def _refuse_past(part: zipfile.ZipInfo, cap: int) -> None:
    # The declared size bounds what is inflated: the zip reader stops there and fails the CRC.
    if part.file_size > cap:
        raise ImportError_(
            "XLSX_TOO_LARGE", f"{part.filename} is {part.file_size} bytes once unzipped", limit=cap // 2**20
        )


# The workbook is read with expat, one part at a time and never as a tree: openpyxl read every sheet,
# chartsheet and relation the workbook lists, padded the rows a sheet skips, and only then let us count
# anything, so a few KB of upload cost minutes and gigabytes. Only what one import needs is opened — the
# manifest, the workbook, its relations, the styles (which cells are dates), the shared strings and the
# first worksheet — each size-checked first, and every element of the sheet counts against the caps.
_SHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main "
_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types "
_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships "
_REL_ID = "http://schemas.openxmlformats.org/officeDocument/2006/relationships id"
#: The workbook part's declared types, in the order openpyxl looks for them.
_WORKBOOK_TYPES = tuple(
    f"application/vnd.{kind}.main+xml"
    for kind in (
        "ms-excel.template.macroEnabled",
        "openxmlformats-officedocument.spreadsheetml.template",
        "ms-excel.sheet.macroEnabled",
        "openxmlformats-officedocument.spreadsheetml.sheet",
    )
)
_STRINGS_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
_COLUMN_RE = re.compile(r"\$?[A-Za-z]{1,3}\$?")


def _no_dtd(*_: object) -> None:
    # Spreadsheets declare no DTD; one that does wants its entities expanded, which is never needed here.
    raise ImportError_("XLSX_UNREADABLE", "The workbook declares a DTD")


class _Budget:
    """The elements a workbook may still hold, counted across all its parts as expat meets them."""

    def __init__(self) -> None:
        self.left = MAX_XLSX_ELEMENTS

    def spent(self) -> ImportError_:
        # Past it, the file holds more than a sheet of two million cells writes: said as such.
        return _too_many("cells")

    def counted(self, start: Callable[[str, dict[str, str]], None]) -> Callable[[str, dict[str, str]], None]:
        def counting(tag: str, attrs: dict[str, str]) -> None:
            self.left -= 1
            if self.left < 0:
                raise self.spent()
            start(tag, attrs)

        return counting


def _stream(
    archive: zipfile.ZipFile,
    name: str,
    cap: int,
    start: Callable[[str, dict[str, str]], None],
    end: Callable[[str], None] | None = None,
    text: Callable[[str], None] | None = None,
) -> None:
    """Run expat over one part of the archive, checked against `cap` before a byte is inflated. `start`
    counts each element against the workbook's budget: `_Budget.counted`, or the sheet's own."""
    part = archive.getinfo(name)
    _refuse_past(part, cap)
    parser = pyexpat.ParserCreate(namespace_separator=" ")
    parser.buffer_text = True
    # Parsed as it comes, so that where the parser stands says how much of the input one unfinished
    # token holds. A token is capped, so reading it again on each chunk stays linear.
    parser.SetReparseDeferralEnabled(False)
    parser.StartDoctypeDeclHandler = _no_dtd
    parser.StartElementHandler = start
    if end is not None:
        parser.EndElementHandler = end
    if text is not None:
        parser.CharacterDataHandler = text
    fed = 0
    with archive.open(part) as source:
        while chunk := source.read(1 << 16):
            parser.Parse(chunk, False)
            fed += len(chunk)
            if fed - max(parser.CurrentByteIndex, 0) > MAX_XML_TOKEN_BYTES:
                raise ImportError_(
                    "XLSX_UNREADABLE", f"A tag of {name} runs past {MAX_XML_TOKEN_BYTES} bytes"
                )
    parser.Parse(b"", True)


def _relations(archive: zipfile.ZipFile, part: str, budget: _Budget) -> dict[str, tuple[str, str]]:
    """A part's relations: id -> (type, target), the target as a name in the archive."""
    folder, name = posixpath.split(part)
    relations: dict[str, tuple[str, str]] = {}

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_RELS_NS}Relationship":
            target = attrs.get("Target", "")
            if attrs.get("TargetMode") != "External":
                target = target[1:] if target.startswith("/") else posixpath.normpath(f"{folder}/{target}")
            relations.setdefault(attrs.get("Id", ""), (attrs.get("Type", ""), target))

    _stream(
        archive, posixpath.join(folder, "_rels", f"{name}.rels"), MAX_XLSX_PART_BYTES, budget.counted(start)
    )
    return relations


@dataclass
class _Workbook:
    sheet: str
    strings: str | None
    date1904: bool


def _locate(archive: zipfile.ZipFile, budget: _Budget) -> _Workbook:
    """The first worksheet, the shared strings and the calendar, found as openpyxl finds them: the
    workbook and the strings by their declared types, the sheet through the workbook's relations."""
    overrides: dict[str, str] = {}
    defaults: set[str] = set()

    def manifest(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_TYPES_NS}Override":
            overrides.setdefault(attrs.get("ContentType", ""), attrs.get("PartName", "")[1:])
        elif tag == f"{_TYPES_NS}Default":
            defaults.add(attrs.get("ContentType", ""))

    _stream(archive, "[Content_Types].xml", MAX_XLSX_PART_BYTES, budget.counted(manifest))
    workbook = next((overrides[kind] for kind in _WORKBOOK_TYPES if kind in overrides), None)
    if workbook is None and defaults.intersection(_WORKBOOK_TYPES):
        workbook = "xl/workbook.xml"
    if workbook is None:
        raise ImportError_("XLSX_UNREADABLE", "The file holds no workbook")

    sheets: list[str] = []
    calendar: list[str] = []

    def entries(tag: str, attrs: dict[str, str]) -> None:
        if tag == f"{_SHEET_NS}sheet":
            sheets.append(attrs.get(_REL_ID, ""))
        elif tag == f"{_SHEET_NS}workbookPr":
            calendar.append(attrs.get("date1904", ""))

    _stream(archive, workbook, MAX_XLSX_PART_BYTES, budget.counted(entries))
    relations = _relations(archive, workbook, budget)
    names = set(archive.namelist())
    for sheet_id in filter(None, sheets):
        kind, target = relations[sheet_id]
        if target in names and "chartsheet" not in kind:
            return _Workbook(
                sheet=target,
                strings=overrides.get(_STRINGS_TYPE),
                date1904=bool(calendar) and calendar[0] not in ("", "false", "f", "0"),
            )
    raise ImportError_("XLSX_UNREADABLE", "The workbook holds no worksheet")


def _date_styles(archive: zipfile.ZipFile, budget: _Budget) -> tuple[set[int], set[int]]:
    """The cell styles that show a number as a date, and those that show it as a duration."""
    from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format, is_timedelta_format

    if "xl/styles.xml" not in archive.namelist():  # where openpyxl looks, whatever the manifest says
        return set(), set()
    custom: dict[int, str] = {}
    formats: list[int] = []
    in_cell_styles = in_formats = False

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal in_cell_styles, in_formats
        # Only the formats of <numFmts> say how a cell reads: a conditional format's own, in <dxfs>,
        # may reuse an id with another code, and read as a cell's it turned a date into a number.
        if tag == f"{_SHEET_NS}numFmts":
            in_formats = True
        elif in_formats and tag == f"{_SHEET_NS}numFmt":
            custom[int(attrs["numFmtId"])] = attrs.get("formatCode", "")
        elif tag == f"{_SHEET_NS}cellXfs":
            in_cell_styles = True
        elif in_cell_styles and tag == f"{_SHEET_NS}xf":
            formats.append(int(attrs.get("numFmtId", 0)))

    def end(tag: str) -> None:
        nonlocal in_cell_styles, in_formats
        if tag == f"{_SHEET_NS}cellXfs":
            in_cell_styles = False
        elif tag == f"{_SHEET_NS}numFmts":
            in_formats = False

    _stream(archive, "xl/styles.xml", MAX_XLSX_PART_BYTES, budget.counted(start), end)
    codes = {i: custom[i] if i in custom else BUILTIN_FORMATS.get(i) for i in set(formats)}
    if any(len(code) > MAX_NUMBER_FORMAT_CHARS for code in codes.values() if code):
        raise ImportError_(
            "XLSX_UNREADABLE", f"A number format runs past {MAX_NUMBER_FORMAT_CHARS} characters"
        )
    # Each format tested once, however many styles name it.
    date_ids = {i for i, code in codes.items() if is_date_format(code)}
    duration_ids = {i for i, code in codes.items() if is_timedelta_format(code)}
    dates = {style for style, i in enumerate(formats) if i in date_ids}
    return dates, {style for style, i in enumerate(formats) if i in duration_ids}


def _too_long() -> ImportError_:
    return ImportError_("XLSX_UNREADABLE", f"A cell holds more than {MAX_CELL_CHARS} characters")


def _shared_strings(archive: zipfile.ZipFile, name: str | None, budget: _Budget) -> list[str]:
    """Each entry's text as Excel shows it: its runs joined, its phonetic reading left out."""
    strings: list[str] = []
    if name is None:
        return strings
    parts: list[str] = []
    size, capturing, phonetic = 0, False, False

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal parts, size, capturing, phonetic
        if tag == f"{_SHEET_NS}t":
            capturing = not phonetic
        elif tag == f"{_SHEET_NS}si":
            parts, size, phonetic = [], 0, False
        elif tag == f"{_SHEET_NS}rPh":
            phonetic = True

    def end(tag: str) -> None:
        nonlocal capturing, phonetic
        if tag == f"{_SHEET_NS}t":
            capturing = False
        elif tag == f"{_SHEET_NS}si":
            strings.append("".join(parts).replace("x005F_", ""))
        elif tag == f"{_SHEET_NS}rPh":
            phonetic = False

    def text(data: str) -> None:
        nonlocal size
        if capturing:
            size += len(data)
            if size > MAX_CELL_CHARS:
                raise _too_long()
            parts.append(data)

    _stream(archive, name, MAX_XLSX_STRINGS_BYTES, budget.counted(start), end, text)
    return strings


def _column(letters: str) -> int:
    if not _COLUMN_RE.fullmatch(letters):
        raise ImportError_("XLSX_UNREADABLE", f"{letters!r} is not a column")
    index = 0
    for letter in letters.lstrip("$").rstrip("$").upper():
        index = index * 26 + ord(letter) - 64
    return index


_ROW, _CELL, _VALUE = f"{_SHEET_NS}row", f"{_SHEET_NS}c", f"{_SHEET_NS}v"
_INLINE, _TEXT, _PHONETIC = f"{_SHEET_NS}is", f"{_SHEET_NS}t", f"{_SHEET_NS}rPh"


class _Sheet:
    """The first worksheet's rows, each cell read as openpyxl reads it (data only: a formula is its last
    result) and put through `_cell_text`. Every row and every cell element counts, valued or not.

    Its handlers run once per element of a sheet that can hold millions of them: they are written for
    that, with no call they can avoid."""

    def __init__(
        self, budget: _Budget, strings: list[str], dates: set[int], durations: set[int], date1904: bool
    ) -> None:
        from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, from_excel, from_ISO8601

        self.budget, self.strings, self.dates, self.durations = budget, strings, dates, durations
        self.epoch = MAC_EPOCH if date1904 else WINDOWS_EPOCH
        self.from_excel, self.from_iso = from_excel, from_ISO8601
        self.kept = _Bounded()
        self.row_elements = self.row_no = self.cell_elements = 0
        self.row: dict[int, str] = {}
        self.columns: dict[str, int] = {}
        self.column = 0
        self.kind, self.style, self.raw = "n", "", ""
        self.parts: list[str] = []
        self.size = 0
        self.capturing = self.inline = self.phonetic = False

    def start(self, tag: str, attrs: dict[str, str]) -> None:
        budget = self.budget
        budget.left -= 1
        if budget.left < 0:
            raise budget.spent()
        if tag == _CELL:
            self.cell_elements += 1
            if self.cell_elements > MAX_CELLS:
                raise _too_many("cells")
            reference = attrs.get("r")
            if reference:
                letters = reference.rstrip("0123456789")
                column = self.columns.get(letters)
                if column is None:
                    column = self.columns[letters] = _column(letters)
                self.column = column
            else:
                self.column += 1
            self.kind, self.style = attrs.get("t", "n"), attrs.get("s", "")
            self.raw, self.inline, self.phonetic = "", False, False
        elif tag == _VALUE:
            if self.kind != "inlineStr":
                self.parts, self.size, self.capturing = [], 0, True
        elif tag == _ROW:
            self.row_elements += 1
            declared = attrs.get("r")
            self.row_no = int(float(declared)) if declared else self.row_no + 1
            if self.row_elements > MAX_SHEET_ROWS or self.row_no > MAX_SHEET_ROWS:
                raise _too_many("rows")
            self.row, self.column = {}, 0
        elif tag == _INLINE:
            self.parts, self.size, self.inline = [], 0, True
        elif tag == _TEXT:
            self.capturing = self.inline and not self.phonetic
        elif tag == _PHONETIC:
            self.phonetic = True

    def end(self, tag: str) -> None:
        if tag == _VALUE:
            if self.capturing:
                self.raw, self.capturing = "".join(self.parts), False
        elif tag == _CELL:
            text = self._text()
            if text:
                if self.column > MAX_COLUMNS:
                    raise _too_many("columns")
                self.row[self.column] = text
        elif tag == _ROW:
            if self.row:
                self.kept.add([self.row.get(i, "") for i in range(1, max(self.row) + 1)])
        elif tag == _TEXT:
            self.capturing = False
        elif tag == _PHONETIC:
            self.phonetic = False

    def text(self, data: str) -> None:
        if self.capturing:
            self.size += len(data)
            if self.size > MAX_CELL_CHARS:
                raise _too_long()
            self.parts.append(data)

    def _text(self) -> str:
        kind, raw = self.kind, self.raw
        if kind == "inlineStr":
            return "".join(self.parts).strip() if self.inline else ""
        if not raw:
            return ""
        if kind == "s":
            return self.strings[int(raw)].strip()
        if kind == "n":
            style = int(self.style) if self.dates and self.style else 0
            if style not in self.dates and raw.isascii() and raw.isdigit() and (raw[0] != "0" or raw == "0"):
                return raw  # what str(int(raw)) writes, without the round trip: most cells of a sheet
            number = float(raw) if "." in raw or "E" in raw or "e" in raw else int(raw)
            if style not in self.dates:
                return _cell_text(number)
            try:
                return _cell_text(self.from_excel(number, self.epoch, timedelta=style in self.durations))
            except (OverflowError, ValueError):
                return "#VALUE!"
        if kind == "b":
            return str(bool(int(raw)))
        if kind == "d":
            return _cell_text(self.from_iso(raw))
        return raw.strip()


def _read_first_sheet(archive: zipfile.ZipFile) -> list[list[str]]:
    budget = _Budget()
    workbook = _locate(archive, budget)
    strings = _shared_strings(archive, workbook.strings, budget)
    sheet = _Sheet(budget, strings, *_date_styles(archive, budget), workbook.date1904)
    _stream(archive, workbook.sheet, MAX_XLSX_INFLATED_BYTES, sheet.start, sheet.end, sheet.text)
    return sheet.kept.rows


def _parse_xlsx(content: bytes) -> Table:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        # An old .xls renamed .xlsx, a CSV saved under the wrong name, a truncated download.
        raise ImportError_("XLSX_UNREADABLE", "The file is not an Excel workbook (.xlsx)") from e
    with archive:
        inflated = sum(item.file_size for item in archive.infolist())
        if inflated > MAX_XLSX_INFLATED_BYTES:
            raise ImportError_(
                "XLSX_TOO_LARGE",
                f"The workbook is {inflated} bytes once unzipped",
                limit=MAX_XLSX_INFLATED_BYTES // (1024 * 1024),
            )
        try:
            rows = _read_first_sheet(archive)
        except ImportError_:
            raise
        except Exception as e:
            # A missing part, broken XML, a value that is not what its cell says: the file's fault, and
            # the person can do something about it; a 500 tells them nothing.
            raise ImportError_("XLSX_UNREADABLE", f"The workbook cannot be read: {type(e).__name__}") from e
    return _tabulate(rows, "xlsx", "")


def suggest_mapping(kind: ImportKind, columns: list[str]) -> dict[str, str]:
    """target field -> source column, by alias match. An order file's weight or volume column that
    does not say per unit or per line is left unmapped (see `_UNIT_EXPLICIT`)."""
    normalised = {normalize_header(c): c for c in columns}
    mapping: dict[str, str] = {}
    for target, aliases in TARGET_ALIASES[kind].items():
        for alias in aliases:
            column = normalised.get(alias)
            if column is None or column in mapping.values():
                continue
            per_unit = kind is ImportKind.PURCHASE_ORDERS and target in PER_UNIT_FIELDS
            if per_unit and is_ambiguous_unit_column(column):
                continue
            mapping[target] = column
            break
    return mapping


def looks_like(kind: ImportKind, column: str) -> str | None:
    """The per-unit field an order file's unmapped column reads like — weight or volume — if any."""
    if kind is not ImportKind.PURCHASE_ORDERS:
        return None
    name = normalize_header(column)
    return next((field for field in PER_UNIT_FIELDS if name in TARGET_ALIASES[kind][field]), None)


def header_signature(columns: list[str]) -> str:
    return "|".join(sorted(normalize_header(c) for c in columns))


def infer_comma_conventions(table: Table, columns: set[str]) -> dict[str, ColumnNumbers]:
    """Decide what a comma means in each numeric column, over the whole file.

    A single value settles it for the column: a comma followed by anything other than three digits
    ("12,5", "0,045", "1,2345") can only be a decimal comma, and a comma followed by exactly three
    digits in a value that also carries a dot or a space ("12,500.50", "1 234,500") can only be a
    thousands separator. Judging value by value is what made "1,250" kg — one kilo and a quarter,
    written by any French spreadsheet — read as 1 250 kg and swallow a whole container's freight.
    """
    conventions: dict[str, ColumnNumbers] = {}
    unsettled: dict[str, str] = {}
    for column in columns:
        decimal_example: str | None = None
        thousands_example: str | None = None
        comma_example: str | None = None
        for row in table.rows:
            value = (row.get(column) or "").strip()
            if "," not in value:
                continue
            comma_example = comma_example or value
            if _SCIENTIFIC_RE.match(value.replace(" ", "")):
                continue  # rejected row by row; it says nothing about the column
            groups = _COMMA_GROUP_RE.findall(value)
            if any(len(g) != 3 for g in groups) or _LEADING_ZERO_COMMA_RE.match(value):
                # "0,850" — nothing but a zero before the comma, so it is a fraction, whatever
                # number of digits follows: a duty rate, a volume in cubic metres, a light part.
                decimal_example = decimal_example or value
            elif len(groups) > 1 or "." in value:
                thousands_example = thousands_example or value
            elif " " in value or "\u00a0" in value or "\u202f" in value:
                # "1 234,500" — a space groups the thousands, so the comma cannot: nobody writes a
                # thousands separator twice. This is the one place we read the audit's rule the
                # other way round, because a space is a French habit and the comma follows it.
                decimal_example = decimal_example or value
        if decimal_example is not None:
            conventions[column] = ColumnNumbers("decimal", example=decimal_example)
        elif thousands_example is not None:
            conventions[column] = ColumnNumbers("thousands", example=thousands_example)
        elif comma_example is not None:
            unsettled[column] = comma_example
    # Nothing in these columns decides: "1,250" alone is genuinely both readings. One file is written
    # one way, so a convention another column proves holds for them too — a Google Sheets FR export
    # uses "," as its delimiter and still writes "12,5". When no column proves anything, or columns
    # prove both, the delimiter decides: ";" is a French spreadsheet (Excel FR switches it precisely
    # because the comma is taken). Either way the report says so, since the difference is a factor of a
    # thousand on a freight allocation basis.
    proven = {c.comma for c in conventions.values()}
    comma: CommaMeaning = (
        proven.pop() if len(proven) == 1 else "decimal" if table.delimiter == ";" else "thousands"
    )
    for column, example in unsettled.items():
        conventions[column] = ColumnNumbers(comma, ambiguous=True, example=example)
    return conventions


_SLASHED_DATE_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})(?!\d)")
_ISO_DATE_RE = re.compile(r"^\s*(\d{4})[\-/](\d{1,2})[\-/](\d{1,2})(?!\d)")
#: The FEC's own dates — every French accounting export — are eight digits, year first: « 20260312 ».
_FEC_DATE_RE = re.compile(r"^\s*((?:19|20)\d{2})(\d{2})(\d{2})\s*$")


def infer_date_orders(table: Table, columns: set[str]) -> dict[str, ColumnDates]:
    """Decide, for each date column, whether the day or the month comes first.

    One value settles it for the column, as for decimal commas: a first number above 12 can only be a
    day, a second number above 12 can only be a day written second. Reading "03/04/2026" as the 3rd of
    April when the sheet came from a US system moves a container into another month, silently — and a
    quarter's audit with it. When nothing decides, the day comes first, as in France, and the report
    says so.
    """
    orders: dict[str, ColumnDates] = {}
    for column in columns:
        day_first: str | None = None
        month_first: str | None = None
        example: str | None = None
        for row in table.rows:
            value = (row.get(column) or "").strip()
            match = _SLASHED_DATE_RE.match(value)
            if not match:
                continue
            example = example or value
            first, second = int(match.group(1)), int(match.group(2))
            if first > 12 >= second:
                day_first = day_first or value
            elif second > 12 >= first:
                month_first = month_first or value
        if month_first and not day_first:
            orders[column] = ColumnDates("mdy", example=month_first)
        elif day_first:
            orders[column] = ColumnDates("dmy", example=day_first)
        elif example is not None:
            orders[column] = ColumnDates("dmy", ambiguous=True, example=example)
    return orders


def parse_date(raw: str | None, *, order: DateOrder = "dmy") -> date | None:
    """Accepts '2026-07-01', '2026-07-01 00:00:00' (a spreadsheet's date cell), '01/07/2026',
    '01.07.2026', '01-07-26', '20260701' (an accounting export's). Blank -> None. Two-digit years are
    this century's."""
    if raw is None or not raw.strip():
        return None
    iso = _ISO_DATE_RE.match(raw) or _FEC_DATE_RE.match(raw)
    slashed = _SLASHED_DATE_RE.match(raw)
    try:
        if iso:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        if slashed:
            first, second, year = int(slashed.group(1)), int(slashed.group(2)), int(slashed.group(3))
            day, month = (first, second) if order == "dmy" else (second, first)
            return date(year + 2000 if year < 100 else year, month, day)
    except ValueError as e:
        raise ImportError_("NOT_A_DATE", f"{raw!r} is not a date") from e
    raise ImportError_("NOT_A_DATE", f"{raw!r} is not a date")


def parse_decimal(raw: str | None, *, comma: CommaMeaning = "auto") -> Decimal | None:
    """Accepts '12500.50', '12 500,50', '12.500,50', '12,500.50', '4,5 %'. Blank -> None.

    `comma` is what the column as a whole says a lone comma means; "auto" keeps the per-value
    guess, which is all a single value out of context allows.
    """
    if raw is None:
        return None
    s = raw.strip().replace(" ", "").replace("\u00a0", "").replace("\u202f", "").replace("%", "")
    if not s:
        return None
    if "," in s and _SCIENTIFIC_RE.match(s):
        # "3,70012E+12" is a French spreadsheet showing a number too wide for its column. Reading
        # it as English would drop the comma and return 3.70012E+17: a hundred thousand times the
        # value, silently. There is no safe reading, so the row says so instead.
        raise ImportError_(
            "AMBIGUOUS_SCIENTIFIC", f"{raw!r} mixes a decimal comma and an exponent; retype it in full"
        )
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        # A lone comma with three digits after it is the one ambiguous shape: "12,500" is an
        # English twelve thousand five hundred, "1,250" a French one and a quarter. The column
        # decides it when it can (see infer_comma_conventions); without a column, a second comma
        # can only be a thousands separator, and nothing before the comma but a zero can only be a
        # decimal — "0,045" is a duty rate, "0,012" a volume in cubic metres, and reading them as
        # 45 and 12 was a silent thousandfold error.
        if "," in head:
            s = s.replace(",", "")
        elif comma == "decimal":
            s = f"{head}.{tail}"
        elif comma == "thousands":
            s = s.replace(",", "")
        elif len(tail) in (1, 2) or head.lstrip("-") in ("", "0"):
            s = f"{head}.{tail}"
        else:
            s = s.replace(",", "")
    try:
        value = Decimal(s)
    except InvalidOperation as e:
        raise ImportError_("NOT_A_NUMBER", f"{raw!r} is not a number") from e
    if not value.is_finite():
        # « Infinity » and « NaN » are numbers to Decimal, and the first division by one raised a 500.
        raise ImportError_("NOT_A_NUMBER", f"{raw!r} is not a number")
    if abs(value) >= MAX_NUMBER:
        raise ImportError_("NUMBER_TOO_LARGE", f"{raw!r} is larger than any figure the books hold")
    return value
