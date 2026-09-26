"""The extractor that always works: text out of the PDF, then patterns.

It is the fallback, so it has no dependency on any key, any network and anyone's uptime. It reads
French and English forwarder invoices, which is what the customers get: « Facture n° », « THC »,
« surestaries », amounts as 1 234,56 and as 1,234.56.

It is not clever, and it does not pretend to be: what it cannot pin down comes back with a low
confidence and no cost type, which puts the line in front of a person — where it was going anyway.
"""

from __future__ import annotations

import io
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.domain.invoices.ports import (
    ExtractedLine,
    ExtractionContext,
    ExtractionFailed,
    ExtractionInput,
    FreightInvoice,
)
from app.domain.models import CostType

logger = logging.getLogger(__name__)

CONTAINER_RE = re.compile(r"\b([A-Z]{4})[ -]?(\d{7})\b")
BL_RE = re.compile(r"\b(?:B/?L|BILL OF LADING|CONNAISSEMENT)[\s:n°#]*([A-Z0-9]{6,20})\b", re.IGNORECASE)
PO_RE = re.compile(r"\b(?:PO|P\.O\.|COMMANDE)[\s:n°#-]*([A-Z0-9][A-Z0-9-]{3,20})\b", re.IGNORECASE)
INVOICE_NO_RE = re.compile(
    r"(?:FACTURE|INVOICE|AVOIR|CREDIT\s+NOTE|NOTE\s+DE\s+CR[EÉ]DIT)\s*(?P<label>N[°ºo]\.?|NO\.?|NUMBER|#)?\s*[:.]?\s*"
    # A number has a digit in it: « avoir confirmation de… » is a sentence, not a credit note.
    r"(?=[A-Z0-9/-]*\d)(?P<number>[A-Z0-9][A-Z0-9/-]{3,24})",
    re.IGNORECASE,
)
#: « N° facture », « Numéro de la facture »: the label of a number written before the word, not after it.
_LABEL_BEFORE_RE = re.compile(
    r"\b(?:N[°ºo]\.?|NO\.?|NUM[EÉ]RO|NUMBER)\s*(?:DE\s+(?:LA\s+)?|OF\s+)?\W*\Z", re.IGNORECASE
)
#: What an invoice says, on the same line, before the number of another one it cancels or replaces.
_OTHER_INVOICE_RE = re.compile(r"\b(?:REMPLACE|ANNULE|REPLACES|CANCELS|AVOIR\s+SUR)\b", re.IGNORECASE)
#: What precedes "facture" when the words that follow are its date, not its number: « Date facture :
#: 12/03/2026 », « Échéance facture ». Read as a number, two invoices of one day become one document.
#: Whole words, on the same line: « Updated invoice » and « Due Date: » above « Invoice No » are not.
_DATED_WORDS_RE = re.compile(r"\b(?:DATE|[EÉ]CH[EÉ]ANCE|DATED)[^\w\n]{0,3}\Z", re.IGNORECASE)
_NUMERIC_DATE_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")
_YEAR_FIRST_DATE_RE = re.compile(r"^(\d{4})[/.-](\d{1,2})[/.-](\d{1,2})$")
_MONTH_NAME_DATE_RE = re.compile(
    r"^\d{1,2}-?(JAN|FEB|FEV|MAR|APR|AVR|MAY|MAI|JUN|JUI|JUL|AUG|AOU|SEP|OCT|NOV|DEC)[A-Z]*-?(\d{2}|\d{4})$",
    re.IGNORECASE,
)


def _is_a_date(text: str) -> bool:
    """A calendar date — 12/03/2026, 2026/03/12, 12-MAR-2026 — and not a number that has the shape
    of one: « 26-09-0412 » is a year, a month and a sequence, and 0412 is no year."""
    if match := _NUMERIC_DATE_RE.match(text):
        day, month, year = (int(part) for part in match.groups())
        return 1 <= day <= 31 and 1 <= month <= 12 and (len(match.group(3)) == 2 or 1990 <= year <= 2100)
    if match := _YEAR_FIRST_DATE_RE.match(text):
        year, month, day = (int(part) for part in match.groups())
        return 1990 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31
    return _MONTH_NAME_DATE_RE.match(text) is not None


# French invoices group thousands with a space, often a non-breaking one.
DATE_RE = re.compile(r"\b(\d{2})[/.-](\d{2})[/.-](\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b")
AMOUNT_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:[\u00a0 .,]\d{3})*(?:[.,]\d{1,2})?(?![\d])")
CURRENCY_RE = re.compile(r"\b(EUR|USD|GBP|CNY|CHF)\b|([€$£])")
CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
#: What an invoice puts after the amount in the last column, and nothing else.
TRAILING_NOISE_RE = re.compile(r"[\s]*(?:EUR|USD|GBP|CNY|CHF|€|\$|£|HT|TTC|TVAC|\*+)?[\s.]*$", re.IGNORECASE)

#: The minus signs a PDF actually carries: the keyboard hyphen, and the typographic minus that most
#: invoice fonts print instead of it.
MINUS_SIGNS = ("-", "\u2212")
#: A refund, in the words French and English invoices head it with. « Avoir » is also an ordinary
#: verb, so it only counts in the header block, where a document states what it is.
CREDIT_NOTE_RE = re.compile(r"\bAVOIRS?\b|\bNOTE\s+DE\s+CR[EÉ]DIT\b|\bCREDIT\s+NOTE\b", re.IGNORECASE)
#: How far down the page a document is still introducing itself.
HEADER_LINES = 12

TOTAL_WORDS = (
    "TOTAL TTC", "TOTAL À PAYER", "TOTAL A PAYER", "NET À PAYER", "NET A PAYER", "MONTANT TTC",
    "MONTANT TOTAL", "TOTAL DUE", "AMOUNT DUE", "TOTAL",
)  # fmt: skip
VAT_WORDS = ("TVA", "VAT", "T.V.A")
#: « TVA FR12812345678 » is the supplier's registration number, not an amount of tax. Same for a
#: line that only states the taxable base.
VAT_NUMBER_RE = re.compile(r"\bTVA\s*[A-Z]{2}\s*\d{6,}|\bVAT\s*(?:NO|NUMBER|N[°º])", re.IGNORECASE)
SUBTOTAL_WORDS = ("TOTAL HT", "MONTANT HT", "BASE HT", "NET HT", "SOUS-TOTAL", "SUBTOTAL", "TOTAL EXCL")

#: Words that name a charge, in the two languages the customers' invoices come in.
COST_KEYWORDS: tuple[tuple[str, CostType], ...] = (
    # Advanced by the forwarder with the duty, and recoverable: it is a row of the invoice, and the
    # one cost type that never enters a landed cost. It has to be named before « DOUANE » is met.
    ("TVA IMPORT", CostType.IMPORT_VAT),
    ("TVA A L'IMPORT", CostType.IMPORT_VAT),
    ("TVA À L'IMPORT", CostType.IMPORT_VAT),
    ("TVA DOUANE", CostType.IMPORT_VAT),
    ("IMPORT VAT", CostType.IMPORT_VAT),
    ("THC", CostType.THC),
    ("TERMINAL HANDLING", CostType.THC),
    ("MANUTENTION", CostType.THC),
    ("SURESTARIE", CostType.DEMURRAGE),
    ("DEMURRAGE", CostType.DEMURRAGE),
    ("DETENTION", CostType.DETENTION),
    ("DÉTENTION", CostType.DETENTION),
    ("CAMIONNAGE", CostType.DRAYAGE),
    ("DRAYAGE", CostType.DRAYAGE),
    ("LIVRAISON", CostType.DRAYAGE),
    ("TRUCKING", CostType.DRAYAGE),
    # Swapping a box between two trucks is a haulage operation, billed by the haulier.
    ("ECHANGE DE CONTENEUR", CostType.DRAYAGE),
    ("ÉCHANGE DE CONTENEUR", CostType.DRAYAGE),
    ("FRET AERIEN", CostType.AIR_FREIGHT),
    ("AIR FREIGHT", CostType.AIR_FREIGHT),
    ("FRET MARITIME", CostType.OCEAN_FREIGHT),
    ("OCEAN FREIGHT", CostType.OCEAN_FREIGHT),
    ("SEA FREIGHT", CostType.OCEAN_FREIGHT),
    ("FRET", CostType.OCEAN_FREIGHT),
    ("FREIGHT", CostType.OCEAN_FREIGHT),
    ("ASSURANCE", CostType.INSURANCE),
    ("INSURANCE", CostType.INSURANCE),
    ("DROITS DE DOUANE", CostType.CUSTOMS_DUTY),
    ("CUSTOMS DUTY", CostType.CUSTOMS_DUTY),
    ("DEDOUANEMENT", CostType.CUSTOMS_BROKERAGE),
    ("DÉDOUANEMENT", CostType.CUSTOMS_BROKERAGE),
    ("CUSTOMS CLEARANCE", CostType.CUSTOMS_BROKERAGE),
    ("HONORAIRES DOUANE", CostType.CUSTOMS_BROKERAGE),
    # The customs agent's own fee, as his invoice names it — « honoraires d'agréé en douane », HAD.
    ("HONORAIRES D'AGREE", CostType.CUSTOMS_BROKERAGE),
    ("HONORAIRES D'AGRÉÉ", CostType.CUSTOMS_BROKERAGE),
    ("FRAIS DE DOSSIER", CostType.BL_FEE),
    ("BL FEE", CostType.BL_FEE),
    ("B/L", CostType.BL_FEE),
    ("BILL OF LADING", CostType.BL_FEE),
    ("CONNAISSEMENT", CostType.BL_FEE),
    # Releasing the bill of lading is what the « frais de release » on a forwarder's invoice buys.
    ("RELEASE", CostType.BL_FEE),
    ("ENTREPOSAGE", CostType.WAREHOUSING),
    # What a terminal or a forwarder calls storage in French: « entreposage » is a bonded warehouse,
    # « magasinage » and « stationnement » are the box sitting on the terminal, which is the charge.
    ("MAGASINAGE", CostType.WAREHOUSING),
    ("STATIONNEMENT", CostType.WAREHOUSING),
    ("DEPOTAGE", CostType.WAREHOUSING),
    ("DÉPOTAGE", CostType.WAREHOUSING),
    ("STORAGE", CostType.WAREHOUSING),
    ("WAREHOUS", CostType.WAREHOUSING),
    ("INSPECTION", CostType.INSPECTION),
    # Customs pulling the box for a look, by scanner or by hand: billed as an inspection.
    ("VISITE DOUANE", CostType.INSPECTION),
    ("VISITE DOUANIERE", CostType.INSPECTION),
    ("VISITE DOUANIÈRE", CostType.INSPECTION),
    ("SCANNER", CostType.INSPECTION),
    ("SCANNING", CostType.INSPECTION),
    ("FRAIS BANCAIRES", CostType.BANK_FEES),
    ("BANK FEE", CostType.BANK_FEES),
    ("FRAIS ORIGINE", CostType.ORIGIN_CHARGES),
    ("ORIGIN CHARGE", CostType.ORIGIN_CHARGES),
)

#: The charges that ocean invoices name by their initials, which cannot be looked for as substrings:
#: « ENS » sits inside « DÉPENSES », « HAD » inside a dozen words. They are matched as whole words.
#:
#: The surcharges (BAF, CAF, LSS, PSS, ISPS) are the freight rate cut into pieces by the carrier's
#: tariff — one voyage, billed in five lines — so they land on OCEAN_FREIGHT, where they are
#: allocated exactly as the base freight is; the wording keeps the detail for whoever reads it.
COST_ACRONYMS: tuple[tuple[str, CostType], ...] = (
    ("BAF", CostType.OCEAN_FREIGHT),
    ("CAF", CostType.OCEAN_FREIGHT),
    ("LSS", CostType.OCEAN_FREIGHT),
    ("PSS", CostType.OCEAN_FREIGHT),
    ("ISPS", CostType.OCEAN_FREIGHT),
    ("ENS", CostType.CUSTOMS_BROKERAGE),
    ("T1", CostType.CUSTOMS_BROKERAGE),
    ("HAD", CostType.CUSTOMS_BROKERAGE),
)
ACRONYM_RE = re.compile(rf"(?<![A-Z0-9])({'|'.join(k for k, _ in COST_ACRONYMS)})(?![A-Z0-9])")
#: « Valeur CAF » is the customs value of the goods — the same three letters as the currency
#: surcharge, on a line that is not a charge at all. Reading it as freight would invent thousands.
NOT_A_CHARGE_RE = re.compile(
    r"VALEUR\s+CAF|CAF\s+VALUE|VALEUR\s+EN\s+DOUANE|CUSTOMS\s+VALUE|VALEUR\s+(?:DE\s+LA\s+)?MARCHANDISE"
    r"|TAUX\s+DE\s+CHANGE|EXCHANGE\s+RATE|\bCOURS\b|POIDS\s+(?:BRUT|NET)|(?:GROSS|NET)\s+WEIGHT"
)
#: The tax of the invoice itself, printed as a row of the table. The import VAT a forwarder advanced
#: is a charge; this one is what the totals are for.
VAT_ROW_RE = re.compile(r"^(?:T\.?V\.?A\.?|VAT)\b(?!\s+(?:IMPORT|[AÀ]\s+L'IMPORT|DOUANE))", re.IGNORECASE)


def text_of(document: ExtractionInput) -> str:
    """The text of the PDF, one line per *row of the page*.

    `pypdf` can hand the text back in the order the file draws it, or rebuilt from where each piece
    sits on the page. The first is what this used to read, and it is only a table when the file
    happens to draw its cells row by row: the accounting packages that draw a column at a time give
    every label first and every amount afterwards, and not one charge could be read. The second
    keeps a row a row whatever the drawing order, and keeps the gap between two columns as a run of
    spaces — which is also what tells « 2   285,00 » (two handlings at 285) from « 2 285,00 ».
    """
    if document.content_type != "application/pdf":
        raise ExtractionFailed(
            "Only PDF invoices can be read without a vision model; this one is an image. "
            "Enter its costs by hand, or configure the LLM extractor.",
            code="NO_TEXT_LAYER",
        )
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(document.content))
        pages = [_page_text(page) for page in reader.pages]
    except Exception as exc:
        raise ExtractionFailed(f"This PDF could not be read: {exc}", code="UNREADABLE_PDF") from exc
    text = "\n".join(pages).strip()
    if not text:
        raise ExtractionFailed(
            "This PDF has no text layer — it is probably a scan. Enter its costs by hand, or "
            "configure the LLM extractor.",
            code="NO_TEXT_LAYER",
        )
    return text


def _page_text(page: object) -> str:
    try:
        laid_out: str = page.extract_text(extraction_mode="layout") or ""  # type: ignore[attr-defined]
    except Exception:  # a font the layout reader cannot measure: the drawing order is better than nothing
        logger.warning("layout text extraction failed, falling back to drawing order", exc_info=True)
        laid_out = ""
    if laid_out.strip():
        return laid_out.translate(SPACES)
    plain: str = page.extract_text() or ""  # type: ignore[attr-defined]
    return plain.translate(SPACES)


#: The spaces French typography groups thousands with — no-break, narrow no-break, thin, figure —
#: read as the ordinary one: « 12 336,89 » is one amount whichever of them the font carries.
SPACES = {0x00A0: " ", 0x202F: " ", 0x2009: " ", 0x2007: " "}


#: A cell: words separated by single spaces. Two spaces or more is the gap between two columns —
#: one space is inside a label, or inside « 2 450,00 ».
CELL_RE = re.compile(r"[^\s]+(?: [^\s]+)*")


def cells_of(row: str) -> list[str]:
    return [match.group(0) for match in CELL_RE.finditer(row.replace("\t", "  "))]


def parse_amount(raw: str) -> Decimal | None:
    """Read an amount written either way round: 1 234,56 and 1,234.56 are the same money.

    The rule is the one a person uses: whichever of `.` or `,` comes last is the decimal separator,
    and a lone separator followed by exactly three digits is a thousands group.
    """
    text = raw.strip().replace("\u00a0", "").replace(" ", "")
    if not text or not any(ch.isdigit() for ch in text):
        return None
    last_dot, last_comma = text.rfind("."), text.rfind(",")
    # « 65,000,00 » is two cells of a table run together, not sixty-five thousand: nobody writes the
    # thousands and the cents with the same sign. Better no amount than that one.
    separator = text[max(last_dot, last_comma)] if max(last_dot, last_comma) >= 0 else ""
    if separator and text.count(separator) > 1 and len(text) - max(last_dot, last_comma) - 1 != 3:
        return None
    if last_dot >= 0 and last_comma >= 0:
        decimal_at = max(last_dot, last_comma)
        integer = re.sub(r"[.,]", "", text[:decimal_at])
        fraction = text[decimal_at + 1 :]
    elif last_dot >= 0 or last_comma >= 0:
        decimal_at = max(last_dot, last_comma)
        integer, fraction = text[:decimal_at].replace(".", "").replace(",", ""), text[decimal_at + 1 :]
        if len(fraction) == 3:  # 3 000 or 4,200: a thousands group, not centimes
            integer, fraction = integer + fraction, ""
    else:
        integer, fraction = text, ""
    if not integer.isdigit() or (fraction and not fraction.isdigit()):
        return None
    try:
        return Decimal(f"{integer}.{(fraction or '00')[:2]:0<2}")
    except InvalidOperation:  # pragma: no cover
        return None


def is_negative(text: str, start: int, end: int) -> bool:
    """Whether the amount at `text[start:end]` is written as a negative one.

    Four notations reach us from real documents and all four mean the same refund: a minus glued to
    the figure, a minus loose in front of it, the typographic minus the PDF font carries instead of
    the hyphen, a minus printed after the figure, and the accountant's parentheses. Reading any of
    them as a charge turns a credit note into money owed, twice over — once as a refund never
    received, once as a cost never incurred.
    """
    before = text[:start].rstrip(" \u00a0")
    after = text[end:].lstrip(" \u00a0")
    if before.endswith(MINUS_SIGNS) or after.startswith(MINUS_SIGNS):
        return True
    return before.endswith("(") and after.startswith(")")


def trailing_amount(line: str) -> Decimal | None:
    """The rightmost number, signed, but only when the line actually *ends* on it.

    An invoice table puts the money in the last column. A header sentence that happens to name a
    charge — « Dossier · B/L MEDUSH2604417 · Conteneur MSCU1234567 (40' HC) · Navire… » — has its
    numbers in the middle, and reading 40 as a bill-of-lading fee is exactly the kind of quiet
    nonsense a review is supposed to be spared. What a figure may still be followed by is its own
    sign: a trailing minus, or the parenthesis that closes an accounting negative.
    """
    stripped = TRAILING_NOISE_RE.sub("", line.rstrip())
    matches = list(AMOUNT_RE.finditer(stripped))
    if not matches:
        return None
    last = matches[-1]
    negative = is_negative(stripped, last.start(), last.end())
    rest = stripped[last.end() :].strip(" \u00a0")
    if rest not in ("", *MINUS_SIGNS) and not (negative and rest == ")"):
        return None
    value = parse_amount(last.group(0))
    if value is None:
        return None
    return -value if negative else value


def describe(line: str) -> str:
    """The words of a table row, without the numeric columns that follow them.

    « Fret maritime Shanghai / Le Havre (Ocean freight) 1 2 450,00 2 450,00 » is a description and
    three columns — quantity, unit price, amount — and showing the columns back to whoever is
    reviewing the charge is showing them the table's plumbing. Only a run of numbers that reaches
    the end of the line is cut, so a quantity inside the wording (« Surestaries 5 jours ») stays.
    """
    tail_free = TRAILING_NOISE_RE.sub("", line)  # the € or the EUR the last column ends with
    matches = list(AMOUNT_RE.finditer(tail_free))
    if not matches:
        return line
    cut = len(tail_free)
    for match in reversed(matches):
        if tail_free[match.end() : cut].strip():
            break  # something other than whitespace between the columns: the wording starts here
        cut = match.start()
    return _without_separators(tail_free[:cut]) or line


#: What an invoice leaves between the wording and its columns, once the numbers are gone.
COLUMN_SEPARATORS = " \t\u00b7\u2013\u2014-\u2212:*"


def _without_separators(head: str) -> str:
    """Trim what joined the wording to the columns, and nothing that belongs to a word."""
    head = head.rstrip(COLUMN_SEPARATORS)
    # « Surestaries 5 jours x 80,00 » leaves a dangling multiplication sign. Bordeaux keeps its
    # last letter: the sign stands alone, the letter does not.
    while head[-1:] in ("x", "\u00d7") and (len(head) == 1 or head[-2:-1].isspace()):
        head = head[:-1].rstrip(COLUMN_SEPARATORS)
    return head


def find_currency(text: str) -> str | None:
    match = CURRENCY_RE.search(text)
    if not match:
        return None
    return match.group(1) or CURRENCY_SYMBOLS.get(match.group(2) or "")


def currencies_in(text: str) -> list[str]:
    """Every currency the document names, in the order it names them, without repeats."""
    seen: list[str] = []
    for match in CURRENCY_RE.finditer(text):
        code = match.group(1) or CURRENCY_SYMBOLS.get(match.group(2) or "")
        if code and code not in seen:
            seen.append(code)
    return seen


def document_currency(lines: list[str], upper_lines: list[str]) -> tuple[str | None, list[str]]:
    """What the invoice is denominated in, and every currency it mentions.

    The one thing that must not decide is the first currency word on the page: a forwarder prints
    the day's USD/EUR rate in its header and its dollar IBAN in its footer, and a EUR invoice read
    as USD is converted twice — around 8 % of landed cost, silently, with a perfectly valid rate.

    So the totals line comes first: that is where a document commits to what it wants to be paid in.
    Failing that, the currency most of the charge lines carry — a majority of rows beats a sentence.
    """
    named = currencies_in("\n".join(lines))
    totals = [
        raw
        for raw, upper in zip(lines, upper_lines, strict=True)
        if any(word in upper for word in (*TOTAL_WORDS, *SUBTOTAL_WORDS))
    ]
    for raw in reversed(totals):  # invoices repeat their totals; the last one is the one to pay
        stated = find_currency(raw)
        if stated:
            return stated, named
    counted = Counter(
        code for raw in lines if trailing_amount(raw) is not None and (code := find_currency(raw)) is not None
    )
    if not counted:
        return None, named
    return counted.most_common(1)[0][0], named


def find_date(text: str) -> date | None:
    for match in DATE_RE.finditer(text):
        try:
            if match.group(1):
                return datetime(  # noqa: DTZ001 - an invoice date has no time zone
                    int(match.group(3)), int(match.group(2)), int(match.group(1))
                ).date()
            return datetime(  # noqa: DTZ001
                int(match.group(4)), int(match.group(5)), int(match.group(6))
            ).date()
        except ValueError:
            continue  # 32/13/2026 and friends
    return None


def cost_type_of(line: str) -> CostType | None:
    upper = line.upper()
    for keyword, cost_type in COST_KEYWORDS:
        if keyword in upper:
            return cost_type
    if NOT_A_CHARGE_RE.search(upper):
        return None
    match = ACRONYM_RE.search(upper)
    if match:
        return dict(COST_ACRONYMS)[match.group(1)]
    return None


def containers_in(text: str) -> list[str]:
    seen: list[str] = []
    for match in CONTAINER_RE.finditer(text.upper()):
        number = f"{match.group(1)}{match.group(2)}"
        if number not in seen:
            seen.append(number)
    return seen


#: What the heading row of a charge table is made of, in the two languages.
TABLE_HEADINGS = (
    "DESIGNATION", "DÉSIGNATION", "LIBELLE", "LIBELLÉ", "PRESTATION", "DESCRIPTION", "MONTANT", "AMOUNT",
    "QTE", "QTÉ", "QUANTITE", "QUANTITÉ", "QTY", "PU", "P.U", "PRIX", "RATE", "TAUX", "DEVISE", "CUR",
    "CODE", "TVA", "VAT", "BASIS", "TAXABLE", "NON TAXABLE", "CHARGE",
)  # fmt: skip
#: Where the table stops: the first row that *begins* with one of these.
TABLE_END = (
    "TOTAL", "NET A PAYER", "NET À PAYER", "NET HT", "BASE HT", "MONTANT HT", "MONTANT TTC", "MONTANT TVA",
    "MONTANT A PAYER", "MONTANT À PAYER", "AMOUNT DUE",
)  # fmt: skip
#: Rows of the table that carry a figure and are not a charge.
NOT_A_ROW = ("SOUS-TOTAL", "SOUS TOTAL", "SOUS-TOTAUX", "SUBTOTAL", "REPORT", "A REPORTER", "À REPORTER",
             "CARRIED", "BROUGHT FORWARD")  # fmt: skip
#: A money amount as an invoice table prints it: with its cents. « Page 1 / 2 » ends on a number too.
WITH_CENTS_RE = re.compile(r"[.,]\d{2}\)?[\s\u00a0]*[-\u2212]?$")
#: What may follow the amount in its own column: a VAT code (« E », « N », « T2 »), a VAT rate.
VAT_CODE_RE = re.compile(r"^(?:[A-Z]{1,2}\d?|\d{1,2}(?:[.,]\d{1,2})?\s*%)$")
#: A line reference printed before the wording: « P001 », « 0010 », « FRT ».
LINE_CODE_RE = re.compile(r"^(?:[A-Z]{0,4}\d{1,5}|[A-Z]{2,4})$")
#: A cell that is a figure and nothing else, with whatever sign or currency it is printed with.
NUMBER_CELL_RE = re.compile(
    r"^\(?[-\u2212]?\s*(?:EUR|USD|GBP|CNY|CHF|€|\$|£)?\s*\d[\d\s\u00a0.,]*\)?\s*[-\u2212]?\s*(?:EUR|USD|GBP|CNY|CHF|€|\$|£|%)?$"
)
CURRENCY_CELL_RE = re.compile(r"^(?:EUR|USD|GBP|CNY|CHF|€|\$|£)$")


def heading_row(rows: list[list[str]]) -> int | None:
    """The index of the row that heads the charge table, or None when the page has no such row."""
    for index, cells in enumerate(rows):
        if len(cells) < 2:
            continue
        headings = sum(1 for cell in cells if cell.upper().rstrip(" .:").startswith(TABLE_HEADINGS))
        if headings >= 2:
            return index
    return None


@dataclass(frozen=True)
class TableRow:
    label: str
    amount: Decimal
    #: The row carries its own currency and only one money column: the amount is in that currency.
    currency: str | None


#: A row of the table whose wording names no charge we know. The money is read as surely as on any
#: other row — it is the cost type that is missing — so it stays "to review", not "to retype".
UNNAMED_CONFIDENCE = 0.5
AMOUNT_HEADINGS = ("MONTANT", "AMOUNT", "TOTAL", "NET")
#: Headings over a column of figures. « TVA » is one only when it holds rates, which the figures
#: themselves settle: a column of « E » and « N » is no column of figures.
NUMERIC_HEADINGS = (*AMOUNT_HEADINGS, "QTE", "QTÉ", "QUANTITE", "QUANTITÉ", "QTY", "PU", "P.U", "PRIX",
                    "RATE", "TAUX", "TVA", "VAT", "NON TAXABLE", "TAXABLE")  # fmt: skip


def amount_position(heading: list[str]) -> tuple[int, int] | None:
    """Which column of figures holds the money, when the heading row says so: its rank counted from
    the right, and how many columns of figures there are.

    « Montant HT | TVA % » prints a rate, with its cents, to the right of the money: the rightmost
    figure of a row is then 20,00 and not the charge. The last heading naming an amount wins —
    « Montant devise | Taux | Montant EUR » ends on the converted one. The rank is only ever applied
    to a row that has a figure in every column: where a cell is blank, counting from the right would
    point one column off, and the row falls back on its last column holding money.

    (Where a figure sits on the page would settle it better than counting, but the text `pypdf` lays
    out keeps the order of the columns and not their alignment: the same column ends ten characters
    apart from one row to the next, depending on the width of the wording before it.)
    """
    over_figures = [
        cell.upper() for cell in heading if cell.upper().rstrip(" .:%").startswith(NUMERIC_HEADINGS)
    ]
    named = [i for i, cell in enumerate(over_figures) if cell.startswith(AMOUNT_HEADINGS)]
    if not named:
        return None
    return len(over_figures) - 1 - named[-1], len(over_figures)


def read_row(cells: list[str], position: tuple[int, int] | None = None) -> TableRow | None:
    """A row of the table as wording and money, or None when it carries no money.

    Without a named amount column the money is the last column that holds any: « Non taxable |
    Taxable » rows print 0,00 — or nothing — in the column that does not apply, and a quantity, a
    unit price or a rate come before the amount, never after it. What may come after it is a VAT
    code, which is not a number.
    """
    if len(cells) < 2:
        return None
    body = list(cells)
    figures: list[Decimal | None] = []  # right to left; None for a column that holds no money
    while len(body) > 1:
        cell = body[-1]
        if CURRENCY_CELL_RE.match(cell):
            body.pop()  # « EUR » next to the amount is part of it, not a column of its own
        elif VAT_CODE_RE.match(cell):
            figures.append(None)
            body.pop()
        elif NUMBER_CELL_RE.match(cell):
            value = trailing_amount(cell)
            with_cents = WITH_CENTS_RE.search(TRAILING_NOISE_RE.sub("", cell)) is not None
            figures.append(value if with_cents else None)  # a quantity or a rate: a column, not money
            body.pop()
        else:
            break
    charged = [value for value in figures if value]
    if not charged:
        return None
    amount = charged[0]
    if position is not None and len(figures) == position[1]:
        under = figures[position[0]]
        if not under:
            return None  # every column is there and the amount one holds nothing: not a charge
        amount = under
    if len(body) > 1 and LINE_CODE_RE.match(body[0]):
        body = body[1:]
    label = " ".join(body).strip()
    if label.endswith(":"):
        return None  # « Valeur en douane : 28 450,00 » is a fact about the file, not a charge
    # One money column and a currency on the row: the amount is in it. Two money columns is a
    # conversion printed on the line, and the last one is already in the invoice's currency.
    named = find_currency(" ".join(cells)) if len(charged) == 1 else None
    return TableRow(label, amount, named)


def _words(text: str) -> bool:
    """Three letters in a row: a wording, as opposed to a code, a unit or a stray figure."""
    return re.search(r"[^\W\d_]{3}", text) is not None


class RegexExtractor:
    name = "regex"

    def extract(self, document: ExtractionInput, context: ExtractionContext) -> FreightInvoice:
        text = text_of(document)
        laid_out = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
        # The rows with their column gaps are for the table; everything that reads a sentence — a
        # total, a date, a number — reads them with the gaps closed.
        lines = [" ".join(cells_of(ln)) for ln in laid_out]
        upper_lines = [ln.upper() for ln in lines]
        text = "\n".join(lines)

        stated_currency, named_currencies = document_currency(lines, upper_lines)
        currency = stated_currency or context.base_currency
        total = self._labelled(lines, upper_lines, TOTAL_WORDS, exclude=SUBTOTAL_WORDS)
        subtotal = self._labelled(lines, upper_lines, SUBTOTAL_WORDS)
        vat = self._vat(lines, upper_lines, total, subtotal)
        invoice_number = None
        passed_a_date = False
        for match in INVOICE_NO_RE.finditer(text):
            candidate = match.group("number").strip(" .:")
            same_line = text[text.rfind("\n", 0, match.start()) + 1 : match.start()]
            if _is_a_date(candidate) or _DATED_WORDS_RE.search(same_line[-12:]):
                passed_a_date = True
                continue  # the date of the invoice, not its number
            if _OTHER_INVOICE_RE.search(same_line):
                continue  # « annule et remplace la facture N° FA-0400 »: another invoice's number
            if passed_a_date and match.group("label") is None and not _LABEL_BEFORE_RE.search(same_line):
                # Once a « facture » has turned out to be a date, only a number labelled as one — N°,
                # No, # — is taken: no number beats the wrong one.
                continue
            invoice_number = candidate
            break

        charges = self._table(laid_out, currency) or self._by_keyword(lines, upper_lines, currency)

        notes: list[str] = []
        if CREDIT_NOTE_RE.search("\n".join(lines[:HEADER_LINES])):
            notes.append("@credit_note|header")
        if len(named_currencies) > 1:
            others = [c for c in named_currencies if c != currency]
            notes.append("|".join(["@currency_guess", currency, *others]))
        if not charges:
            notes.append("No charge line recognised; every amount needs entering by hand.")

        heads = [cells_of(ln)[0] for ln in laid_out[:3]]
        vendor = next((h for h in heads if not h.upper().startswith(("FACTURE", "INVOICE", "AVOIR"))), None)
        return FreightInvoice(
            vendor=vendor,
            invoice_number=invoice_number,
            invoice_date=find_date(text),
            currency=currency,
            total=f"{total:.2f}" if total is not None else None,
            subtotal=f"{subtotal:.2f}" if subtotal is not None else None,
            vat=f"{vat:.2f}" if vat is not None else None,
            lines=charges,
            container_numbers=containers_in(text),
            bl_numbers=[m.group(1) for m in BL_RE.finditer(text)],
            po_numbers=[m.group(1).upper() for m in PO_RE.finditer(text)],
            confidence=0.6 if charges else 0.2,
            notes=notes,
        )

    def _table(self, lines: list[str], currency: str) -> list[ExtractedLine]:
        """Every row of the charge table, whether or not its wording is one we know.

        This used to keep only the rows naming a charge from a list of keywords, and drop the rest
        without a word: « Frais de sûreté portuaire », « Taxe d'escale », « Pesage VGM » never reached
        the screen, the arithmetic check then said the invoice did not add up, and the reader was
        left to find which lines were missing by comparing with the PDF. A row of the table is a
        charge; one we cannot name comes back without a cost type and with a lower confidence, which
        is a question put to a person instead of a line hidden from them.
        """
        rows = [cells_of(line) for line in lines]
        start = heading_row(rows)
        position = amount_position(rows[start]) if start is not None else None
        if start is None:
            # No heading row — sections titled « Débours », « Prestations » and nothing over the
            # columns. The rows are still rows: a wording, a gap, an amount with its cents. What the
            # top of a page holds (addresses, references, dates) has no such amount, so the table is
            # taken to start at the first row that does; without one there is no table to follow.
            start = next((i for i, row in enumerate(rows) if self._worded(self._charge(row))), None)
            if start is None:
                return []
            start -= 1
        charges: list[ExtractedLine] = []
        container: str | None = None
        carried: str | None = None  # the first line of a wording that runs over two
        for cells in rows[start + 1 :]:
            joined = " ".join(cells)
            if cells[0].upper().startswith(TABLE_END):
                break
            row = self._charge(cells, position)
            if row is not None and not _words(row.label) and carried is None:
                row = None  # a figure next to a code or a unit, and no wording above to complete
            if row is None:
                # « Conteneur HLXU4377125 » on a line of its own heads the charges of that box.
                named = containers_in(joined)
                if (
                    len(named) == 1
                    and len(cells) == 1
                    and not _words(
                        CONTAINER_RE.sub("", joined.upper()).replace("CONTENEUR", "").replace("CONTAINER", "")
                    )
                ):
                    container, carried = named[0], None
                else:
                    carried = joined if len(cells) == 1 and _words(joined) else None
                continue
            label = row.label
            # « Fret maritime Shanghai / Le Havre, navire MSC GÜLSÜN, » then « voyage FE437W … 2 450,00 »:
            # one charge written over two lines. The first line is the one that names it.
            if carried is not None and (carried.endswith(",") or not label[:1].isupper()):
                label = f"{carried} {label}"
            carried = None
            cost_type = cost_type_of(label)
            charges.append(
                ExtractedLine(
                    description=label,
                    amount=f"{row.amount:.2f}",
                    currency=row.currency or currency,
                    cost_type=cost_type,
                    container_number=next(iter(containers_in(joined)), container),
                    confidence=0.6 if cost_type is not None else UNNAMED_CONFIDENCE,
                )
            )
        return charges

    @staticmethod
    def _worded(row: TableRow | None) -> bool:
        return row is not None and _words(row.label)

    @staticmethod
    def _charge(cells: list[str], position: tuple[int, int] | None = None) -> TableRow | None:
        """The row as a charge, unless it is one of the rows of a table that are not."""
        upper = " ".join(cells).upper()
        if any(word in upper for word in NOT_A_ROW) or NOT_A_CHARGE_RE.search(upper):
            return None
        if VAT_ROW_RE.match(upper) or upper.startswith(TABLE_END):
            return None
        return read_row(cells, position)

    def _by_keyword(self, lines: list[str], upper_lines: list[str], currency: str) -> list[ExtractedLine]:
        """Without a table to follow — no heading row — only a line that names a charge is one."""
        charges: list[ExtractedLine] = []
        for raw, upper in zip(lines, upper_lines, strict=True):
            cost_type = cost_type_of(raw)
            if cost_type is None:
                continue
            if any(word in upper for word in (*TOTAL_WORDS, *VAT_WORDS, *SUBTOTAL_WORDS)):
                continue  # a total that happens to name a charge is not a charge
            amount = trailing_amount(raw)
            if amount is None or amount == 0:
                continue  # a charge of nothing is a table rule or a heading, not money
            charges.append(
                ExtractedLine(
                    description=describe(raw),
                    amount=f"{amount:.2f}",
                    currency=find_currency(raw) or currency,
                    cost_type=cost_type,
                    container_number=next(iter(containers_in(raw)), None),
                    confidence=0.6,
                )
            )
        return charges

    def _vat(
        self,
        lines: list[str],
        upper_lines: list[str],
        total: Decimal | None,
        subtotal: Decimal | None,
    ) -> Decimal | None:
        """The tax, which is the hardest number on a French invoice to read.

        Arithmetic first: when the invoice states both a total and a subtotal, the tax is their
        difference and no wording can mislead us. Only then fall back to reading a labelled line,
        skipping the ones that carry a registration number rather than an amount.

        The difference has to point the same way as the invoice itself: on a credit note every
        figure is negative, and the tax with them. A gap that points the other way means one of the
        two totals was misread, and a tax invented from it would be worse than none.
        """
        if total is not None and subtotal is not None:
            difference = total - subtotal
            if difference == 0 or (difference < 0) == (total < 0):
                return difference
        candidates = [
            (raw, upper)
            for raw, upper in zip(lines, upper_lines, strict=True)
            if any(word in upper for word in VAT_WORDS) and not VAT_NUMBER_RE.search(raw)
        ]
        if not candidates:
            return None
        return self._labelled([c[0] for c in candidates], [c[1] for c in candidates], VAT_WORDS)

    def _labelled(
        self,
        lines: list[str],
        upper_lines: list[str],
        words: tuple[str, ...],
        exclude: tuple[str, ...] = (),
    ) -> Decimal | None:
        """The amount on the last *row* naming one of these words. Invoices repeat their totals.

        A row ends on its figure; a sentence does not, and a footer naming the word is a sentence.
        « Fret et THC exonérés de TVA (art. 262 II-14° CGI) … IBAN FR76 3000 4000 0500 » is the last
        line of a real invoice that names the tax, and it once made the VAT 76.
        """
        found: Decimal | None = None
        for raw, upper in zip(lines, upper_lines, strict=True):
            if any(word in upper for word in exclude):
                continue
            if any(word in upper for word in words):
                amount = trailing_amount(raw)
                if amount is not None:
                    found = amount
        return found
