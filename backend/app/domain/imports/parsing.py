"""Decode and tabulate an uploaded CSV/XLSX; suggest a column mapping. Pure functions."""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

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


#: What one file may hold. The upload is capped at 10 MB, but a 10 MB CSV is 1.75 million rows (half a
#: gigabyte once read) and a 1.5 KB workbook can claim every cell of Excel: the reader stops at these
#: and refuses the file by name. A quarter of an importer's orders or costs is a few thousand rows.
MAX_ROWS = 100_000
MAX_COLUMNS = 200
MAX_CELLS = 2_000_000
#: What a workbook may weigh once unzipped: zipped repetition inflates a thousandfold. Only the sheet
#: is streamed, so this bounds the sheet; the parts read whole have caps of their own.
MAX_XLSX_INFLATED_BYTES = 200 * 1024 * 1024
#: What each part openpyxl parses whole into a tree (styles, workbook, manifest, relations, properties)
#: may weigh unzipped. The tree costs about thirty times the text: 36 MB of empty `<xf/>` styles took
#: 1.2 GB. Excel's own ceiling of 64 000 cell styles fits well under it.
MAX_XLSX_PART_BYTES = 8 * 1024 * 1024
#: What the shared strings may weigh unzipped: they are read whole too, one Python string each, about
#: eight times the text for short ones (33 MB of 8-letter labels took 250 MB). A hundred thousand rows
#: with three distinct labels each fit.
MAX_XLSX_STRINGS_BYTES = 24 * 1024 * 1024


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


def _bounded(rows: Iterable[Sequence[str]]) -> list[list[str]]:
    """The file's non-blank rows, each without its trailing blank cells — refused by name as soon as
    it holds more rows, columns or cells than one import may, before the rest is even read."""
    kept: list[list[str]] = []
    cells = 0
    for row in rows:
        width = len(row)
        while width and not row[width - 1].strip():
            width -= 1
        if not width:
            continue
        if width > MAX_COLUMNS:
            raise ImportError_(
                "TOO_MANY_COLUMNS", f"More than {MAX_COLUMNS} columns in the file", limit=MAX_COLUMNS
            )
        if len(kept) == MAX_ROWS:
            raise ImportError_("TOO_MANY_ROWS", f"More than {MAX_ROWS} rows in the file", limit=MAX_ROWS)
        cells += width
        if cells > MAX_CELLS:
            raise ImportError_("TOO_MANY_CELLS", f"More than {MAX_CELLS} cells in the file", limit=MAX_CELLS)
        kept.append(list(row[:width]))
    return kept


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


class _Parts(zipfile.ZipFile):
    """The workbook's archive as openpyxl reads it: every part it parses whole goes through `read`,
    the sheet and the shared strings through `open`, streamed."""

    def read(self, name: str | zipfile.ZipInfo, pwd: bytes | None = None) -> bytes:
        _refuse_past(name if isinstance(name, zipfile.ZipInfo) else self.getinfo(name), MAX_XLSX_PART_BYTES)
        return super().read(name, pwd)


def _load_workbook(content: bytes) -> Any:
    try:
        from openpyxl.reader.excel import ExcelReader
        from openpyxl.xml.constants import SHARED_STRINGS
    except ImportError as e:  # pragma: no cover
        raise ImportError_("XLSX_UNSUPPORTED", "XLSX support is not installed.") from e

    class Reader(ExcelReader):  # type: ignore[misc]
        def read_strings(self) -> None:
            # Found as openpyxl finds them, by their declared type: the part's name is the file's to choose.
            strings = self.package.find(SHARED_STRINGS)
            if strings is not None:
                _refuse_past(self.archive.getinfo(strings.PartName[1:]), MAX_XLSX_STRINGS_BYTES)
            super().read_strings()

    reader = Reader(io.BytesIO(content), read_only=True, data_only=True)
    reader.archive.close()
    reader.archive = _Parts(io.BytesIO(content))
    reader.read()
    return reader.wb


def _parse_xlsx(content: bytes) -> Table:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            inflated = sum(item.file_size for item in archive.infolist())
    except zipfile.BadZipFile as e:
        # An old .xls renamed .xlsx, a CSV saved under the wrong name, a truncated download.
        raise ImportError_("XLSX_UNREADABLE", "The file is not an Excel workbook (.xlsx)") from e
    if inflated > MAX_XLSX_INFLATED_BYTES:
        raise ImportError_(
            "XLSX_TOO_LARGE",
            f"The workbook is {inflated} bytes once unzipped",
            limit=MAX_XLSX_INFLATED_BYTES // (1024 * 1024),
        )
    try:
        wb = _load_workbook(content)
        try:
            ws = wb.worksheets[0]
            # The sheet's own <dimension> is not believed: one stray formatted cell at XFD1048576 made
            # openpyxl pad every row to 16 384 columns and yield a million of them. Without it, rows
            # are as wide as their last cell, and one column past the cap is enough to refuse.
            ws.reset_dimensions()
            # The rows a stray cell far below the table leaves in between are skipped before any cell
            # is looked at: a million of them is a second, not a minute.
            values = ws.iter_rows(values_only=True, max_col=MAX_COLUMNS + 1)
            rows = _bounded([_cell_text(v) for v in row] for row in values if row.count(None) != len(row))
        finally:
            wb.close()
    except ImportError_:
        raise
    except Exception as e:
        if isinstance(e.__cause__, ImportError_):
            raise e.__cause__ from None  # a cap, wrapped by openpyxl in its own ValueError
        # Whatever openpyxl trips on inside a zip — a missing part, broken XML — is the file's fault,
        # and the person can do something about it; a 500 tells them nothing.
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
        return Decimal(s)
    except InvalidOperation as e:
        raise ImportError_("NOT_A_NUMBER", f"{raw!r} is not a number") from e
