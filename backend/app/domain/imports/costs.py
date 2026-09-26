"""Reading a costs ledger: what each forwarder, haulier and customs broker billed, and on which box.

A prospect's quarter has its costs in the accounts before it has them anywhere else: the purchase
journal's lines for the forwarders — date, supplier, invoice number, label, amount — sometimes with the
container or the bill of lading in a column, more often only in the label. Read as costs they make the
quarter's landed costs, and every euro misread is a margin misstated in the audit sold on them. So:

- Actual costs only. A file carries no estimates: an estimate keyed in a spreadsheet and the invoice
  that settles it would both count.
- A journal's other lines are not charges. With an account column, only the charge accounts of the
  French chart (class 6) are read; the supplier's total, the VAT, anything else is set aside and said.
- VAT is never a cost. A line whose label is VAT is set aside, whatever a column or a person types it
  as; only the import VAT a forwarder advanced is kept, as such, out of every landed cost. A label that
  puts VAT with a charge — « droits et TVA » — is refused, and so is an amount « TTC »: its part before
  tax is not written anywhere.
- The target is a box, a bill of lading or an order the organization already has — from a column, or
  read in the label (a container by its check digit) — never created here: a cost on a box nobody
  loaded is spread on nothing. A bill of lading with one box is that box. A label naming two boxes is
  refused: which one is a person's call.
- The type comes from a column, else from the label, else from what a person said for that label or
  for the whole file; a type not read from a column is `type_inferred`, and what the audit finds on
  such a cost is a question. A label naming several charges — « débours », « fret + THC » — is refused:
  it has to be split.
- A negative line is not a cost. One that cancels a line the file writes cancels it — the same box,
  type, currency, amount and forwarder — and both are out. One that cancels a line already in the books
  is set aside and names the cost it reverses: the audit's preparation blocks on it until a person has
  taken that cost out. Any other is set aside and counted: it may refund a charge the audit would claim.
- The same invoice line never twice: identical lines of the file add up, what an earlier import of the
  same ledger wrote is recognised by the rows it came from — whatever a person changed on the cost
  since — and left alone, an invoice already in the books or waiting in the inbox is refused, the very
  line already on file (estimate or closed cost included) is refused, and a charge of the same type
  already on the same freight is refused unless a person says otherwise.
- An estimate of the same type on the same target is replaced by the cost, the pair becoming the
  variance; several, or one at the other end of the freight, are left to a person: never both counted.
- Every amount is converted at the ECB rate of its date, fixed on the cost like any other; a day with
  no rate is a refusal, not a guess. A group of lines is one cost, dated and converted at its first day.
- What the file could not write is kept with the job — the rows refused, the costs it recognised — for
  the audit's preparation to say, and for taking back an earlier file without losing lines a later one
  relied on.

The rules mirror those of the invoice's confirmation (`invoices.checks`), applied to the organization's
costs read once — a ledger has thousands of lines, and a query per rule per line is minutes.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import select

from app.adapters.extraction.regex_extractor import (
    ACRONYM_RE,
    BL_RE,
    COST_ACRONYMS,
    COST_KEYWORDS,
    NOT_A_CHARGE_RE,
)
from app.core.errors import Unprocessable
from app.core.money import q2, to_base
from app.core.tenancy import TenantSession
from app.domain.boxes import container_numbers_in
from app.domain.costing import entry
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation, compute
from app.domain.fx.service import FxService
from app.domain.imports.containers import normalize_reference
from app.domain.imports.parsing import (
    ColumnDates,
    ColumnNumbers,
    ImportError_,
    Table,
    infer_date_orders,
    parse_date,
    parse_decimal,
)
from app.domain.imports.service import ImportReport, RowIssue, _Row
from app.domain.invoices.checks import note_field
from app.domain.labels import COST_TYPE_FR
from app.domain.models import (
    Container,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    ImportJob,
    Invoice,
    InvoiceStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
)

#: « Montant TTC », « Amount incl. VAT »: an amount with the tax in it. Import VAT is recovered.
_TAX_INCLUDED = re.compile(
    r"(?:^|[^a-z])(?:ttc|tvac|incl\w*[\s_.-]*vat|vat[\s_.-]*incl\w*|toutes[\s_]taxes)", re.I
)
#: The same, said by a line's label.
_TAX_INCLUDED_LABEL = re.compile(
    r"(?<![A-Z])(?:TTC|TVAC)(?![A-Z])|T\.?V\.?A\.?\s+(?:INCLUSE|COMPRISE)|TOUTES\s+TAXES"
    r"|INCL\w*\.?\s+VAT|VAT\s+INCL"
)
#: A label that speaks of VAT — as the thing billed, or as the regime of the charge it names.
_VAT_WORD = re.compile(r"(?<![A-Z])(?:T\.?V\.?A|VAT)(?![A-Z])|AUTO-?LIQUID")
#: What a charge's line says of its VAT without billing any: freight is exempt, a service is billed
#: « hors taxes », an EU forwarder's invoice is reverse-charged, a line gives the rate it is taxed at.
_VAT_REGIME = re.compile(
    r"HORS\s+(?:T\.?V\.?A|TAXES?)|EXON[EÉ]R|(?:NON\s+)?SOUMIS|NON\s+(?:APPLICABLE|ASSUJETTI)|FRANCHISE"
    r"|ART(?:ICLE)?\.?\s*(?:259|262|283)|REVERSE\s+CHARGE|EXEMPT|ZERO[\s-]RATED|EXCL|AUTO-?LIQUID"
    r"|(?:T\.?V\.?A|VAT)\.?\s*(?:[AÀ]|DE|:)?\s*\d+(?:[.,]\d+)?\s*%"
)
#: The VAT customs levy on the goods, which a forwarder advances: tracked, never a landed cost.
_IMPORT_VAT_WORDS = re.compile(r"IMPORT|DOUANE|(?<![A-Z])DAU(?![A-Z])|AUTO-?LIQUID|CUSTOMS")
#: Charges no keyword names alone — « droits » is also a port's — but that no VAT line carries either.
_ALSO_A_CHARGE = re.compile(r"(?<![A-Z])(?:DROITS?|DUT(?:Y|IES)|ACCISES?|OCTROI)(?![A-Z])")
_CURRENCY_CODE = re.compile(r"(?<![A-Z])(EUR|USD|GBP|CNY|CHF|JPY|HKD|SGD|AED)(?![A-Z])")
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "CNY"}
#: Labels that name several charges at once, whatever words they use: they have to be split.
_MIXED = re.compile(
    r"\bD[EÉ]BOURS\b|\bDROITS\s+ET\s+TAXES\b|\bDUTIES\s+AND\s+TAXES\b|\bDISBURSEMENTS?\b", re.I
)
#: Types a file may name in its type column: the code, and the names the application gives them.
_TYPE_NAMES: dict[str, CostType] = {
    **{kind.value.casefold(): kind for kind in CostType},
    **{name.casefold(): CostType(code) for code, name in COST_TYPE_FR.items()},
    **{kind.value.replace("_", " ").casefold(): kind for kind in CostType},
}
#: Freight whose customs value it is part of and whose duty it pays — decided like any other entry.
DUTY = CostType.CUSTOMS_DUTY

#: Refusals that leave no charge out of the books: the line is there already, in the inbox, in the
#: file's own reversal, or a person judged it a duplicate. Every other refusal is money the audit
#: does not see, which its preparation says (`refused_rows`).
NOT_MISSING = frozenset(
    {
        "INVOICE_ALREADY_RECORDED",
        "INVOICE_LINE_ALREADY_RECORDED",
        "INVOICE_IN_INBOX",
        "DUPLICATE_COST",
        "REVERSED_IN_FILE",
    }
)


@dataclass
class _Read:
    """One row of a charge, read."""

    row: int
    scope: CostScope
    target_id: UUID
    target_label: str
    cost_type: CostType
    type_inferred: bool
    currency: str
    amount: Decimal
    fx: tuple[Decimal, date, str]
    cost_date: date
    vendor: str | None
    invoice_number: str | None
    label: str | None
    #: The row as its source said it, for recognising it in a later import of the same ledger.
    source: str

    @property
    def amount_base(self) -> Decimal:
        return to_base(self.amount, self.fx[0])


@dataclass
class _Credit:
    """One negative row, read: a refund. Its target and type are what the file says, if it says them —
    a credit note is counted whether or not it can be placed."""

    row: int
    currency: str
    amount: Decimal
    amount_base: Decimal
    cost_date: date
    vendor: str | None
    invoice_number: str | None
    scope: CostScope | None
    target_id: UUID | None
    cost_type: CostType | None


@dataclass
class _Group:
    """Rows of the file that are one cost: the same line of one invoice, said once or several times."""

    rows: list[_Read] = field(default_factory=list)

    @property
    def first(self) -> _Read:
        return self.rows[0]

    @property
    def anchor(self) -> _Read:
        """The row the cost is dated and converted by: its first day."""
        return min(self.rows, key=lambda row: (row.cost_date, row.row))

    @property
    def amount(self) -> Decimal:
        return q2(sum((row.amount for row in self.rows), Decimal(0)))

    @property
    def amount_base(self) -> Decimal:
        return to_base(self.amount, self.anchor.fx[0])

    @property
    def row_key(self) -> str:
        """The rows it came from, as their source said them: what a later import of the same ledger
        recognises, whatever a person has changed on the cost since."""
        return hashlib.sha256("\n".join(sorted(row.source for row in self.rows)).encode()).hexdigest()


Issue = tuple[str, str, str, dict[str, Any]]


def label_key(label: str) -> str:
    """A label as it groups: « THC MSCU4821994 » and « thc  TGHU7245086 » are one label, and the
    numbers of a file's references do not make one label per line."""
    text = BL_RE.sub(" ", label.upper())
    for number in container_numbers_in(text):
        text = text.replace(number, " ")
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text)).strip().casefold()


def types_named(label: str) -> set[CostType]:
    """Every charge a label names, its references taken out first: « Fret B/L MEDU2604417 » is
    freight, not freight and a bill-of-lading fee."""
    text = BL_RE.sub(" ", label.upper())
    if NOT_A_CHARGE_RE.search(text):
        return set()
    found = {kind for keyword, kind in COST_KEYWORDS if keyword in text}
    found |= {dict(COST_ACRONYMS)[match.group(1)] for match in ACRONYM_RE.finditer(text)}
    return found


VatVerdict = Literal["tax_included", "vat_line", "import_vat", "mixed"]


def vat_in_label(label: str) -> VatVerdict | None:
    """What a label says of the VAT, if anything:

    - `tax_included`: its amount has the tax in it (« TTC », « TVA incluse »);
    - `import_vat`: it is the import VAT a forwarder advanced — alone on its line;
    - `vat_line`: it is the invoice's own VAT, or VAT the file does not say more of: never a cost;
    - `mixed`: VAT on one line with a charge — « droits de douane et TVA »: its part is unknown;
    - None: no VAT, or a charge's line saying its VAT regime — « fret exonéré de TVA », « frais de
      dossier TVA 20 % » — which is the charge's amount before tax.
    """
    text = BL_RE.sub(" ", label.upper())
    if _TAX_INCLUDED_LABEL.search(text):
        return "tax_included"
    if not _VAT_WORD.search(text):
        return None
    charges = types_named(text) - {CostType.IMPORT_VAT}
    if charges or _ALSO_A_CHARGE.search(text):
        return None if _VAT_REGIME.search(text) else "mixed"
    if CostType.IMPORT_VAT in types_named(text) or _IMPORT_VAT_WORDS.search(text):
        return "import_vat"
    return "vat_line"


class _Books:
    """What the organization already has, read once: targets by reference, costs, invoices waiting."""

    def __init__(self, t: TenantSession) -> None:
        self.containers: dict[str, Container] = {
            c.container_number: c for c in t.db.scalars(t.q(Container).where(Container.archived_at.is_(None)))
        }
        self.container_by_id: dict[UUID, Container] = {c.id: c for c in self.containers.values()}
        self.shipments: dict[str, Shipment] = {
            normalize_reference(s.reference): s for s in t.db.scalars(t.q(Shipment))
        }
        self.boxes_of: dict[UUID, set[UUID]] = defaultdict(set)
        for container in self.containers.values():
            if container.shipment_id is not None:
                self.boxes_of[container.shipment_id].add(container.id)
        self.orders: dict[str, PurchaseOrder] = {
            order.po_number.upper(): order for order in t.db.scalars(t.q(PurchaseOrder))
        }
        self.order_of_line: dict[UUID, UUID] = dict(
            t.db.execute(
                select(PurchaseOrderLine.id, PurchaseOrderLine.po_id).where(
                    PurchaseOrderLine.org_id == t.org_id
                )
            )
            .tuples()
            .all()
        )
        self.shipment_of: dict[UUID, UUID | None] = {c.id: c.shipment_id for c in self.containers.values()}
        # Every cost, closed ones included, in the order they were written: when several answer a
        # rule, the first one written is the one named, whichever order the database returns them in.
        every = list(t.db.scalars(t.q(Cost).order_by(Cost.created_at, Cost.id)))
        standing = [cost for cost in every if cost.closed_at is None]
        replaced = {cost.supersedes_cost_id for cost in standing if cost.supersedes_cost_id is not None}
        self.actuals = [cost for cost in standing if cost.status is CostStatus.ACTUAL]
        self.estimates = [
            cost for cost in standing if cost.status is CostStatus.ESTIMATE and cost.id not in replaced
        ]
        self.by_number: dict[str, list[Cost]] = defaultdict(list)
        for cost in self.actuals:
            if number := entry.normalize_invoice_number(cost.invoice_number):
                self.by_number[number].append(cost)
        # By what they are on, so that « the same freight » is a look-up and not a pass over the books:
        # a ledger has thousands of lines, and so do the books it is compared with.
        self.actuals_on: dict[UUID | None, list[Cost]] = defaultdict(list)
        self.estimates_on: dict[UUID | None, list[Cost]] = defaultdict(list)
        for cost in self.actuals:
            self.actuals_on[_target_of(cost)].append(cost)
        for cost in self.estimates:
            self.estimates_on[_target_of(cost)].append(cost)
        # What earlier imports wrote, by the rows they came from — a cost a person closed included: a
        # line taken out on purpose is not written back by the next copy of the ledger.
        self.imported: dict[str, list[Cost]] = defaultdict(list)
        for cost in every:
            if cost.import_row_key is not None:
                self.imported[cost.import_row_key].append(cost)
        # The database's own uniqueness, `uq_costs_invoice_line`, key for key and over every status: a
        # line it would refuse is refused here first, by name, rather than as a failed write.
        self.lines: dict[tuple[object, ...], Cost] = {}
        for cost in every:
            if (key := _index_key(cost.vendor, cost.invoice_number, cost.cost_type, cost.currency,
                                  _target_of(cost))) is not None:  # fmt: skip
                self.lines.setdefault(key, cost)
        self.lines_of: dict[UUID, set[UUID]] = defaultdict(set)
        for line_id, po_id in self.order_of_line.items():
            self.lines_of[po_id].add(line_id)
        self.inbox: dict[str, list[Invoice]] = defaultdict(list)
        for invoice in t.db.scalars(
            t.q(Invoice)
            .where(
                Invoice.status.in_(
                    (InvoiceStatus.UPLOADED, InvoiceStatus.EXTRACTING, InvoiceStatus.NEEDS_REVIEW)
                )
            )
            .order_by(Invoice.created_at, Invoice.id)
        ):
            if number := entry.normalize_invoice_number(invoice.invoice_number):
                self.inbox[number].append(invoice)

    def freight(self, scope: CostScope, target_id: UUID) -> list[UUID]:
        """Every target on the same freight as this one — the rule of `invoices.checks._same_freight`:
        a box and the bill it travels under, a bill and its boxes, an order and its lines."""
        if scope is CostScope.CONTAINER:
            shipment = self.shipment_of.get(target_id)
            return [target_id, *([shipment] if shipment else [])]
        if scope is CostScope.SHIPMENT:
            return [target_id, *self.boxes_of.get(target_id, set())]
        if scope is CostScope.PO:
            return [target_id, *self.lines_of.get(target_id, set())]
        order = self.order_of_line.get(target_id)
        return [target_id, *([order] if order else [])]

    def on_freight(
        self, index: dict[UUID | None, list[Cost]], scope: CostScope, target_id: UUID, cost_type: CostType
    ) -> list[Cost]:
        return [
            cost
            for target in self.freight(scope, target_id)
            for cost in index.get(target, [])
            if cost.cost_type is cost_type
        ]

    def same_freight(self, cost: Cost, scope: CostScope, target_id: UUID) -> bool:
        """The rule of `invoices.checks._same_freight`: a box and the bill it travels under, an order
        and its lines, are one freight."""
        if scope is CostScope.CONTAINER:
            shipment = self.shipment_of.get(target_id)
            return cost.container_id == target_id or (shipment is not None and cost.shipment_id == shipment)
        if scope is CostScope.SHIPMENT:
            return cost.shipment_id == target_id or cost.container_id in self.boxes_of.get(target_id, set())
        if scope is CostScope.PO:
            return cost.po_id == target_id or (
                cost.po_line_id is not None and self.order_of_line.get(cost.po_line_id) == target_id
            )
        order = self.order_of_line.get(target_id)
        return cost.po_line_id == target_id or (order is not None and cost.po_id == order)


def _index_key(
    vendor: str | None, number: str | None, cost_type: CostType, currency: str, target: UUID | None
) -> tuple[object, ...] | None:
    """A line as `uq_costs_invoice_line` keys it — the vendor and number as written, since the index
    compares them so — or None for a line it does not hold (no vendor or no number)."""
    if vendor is None or number is None:
        return None
    return vendor, number, cost_type, currency, target


def refuse_tax_included(mapping: dict[str, str]) -> None:
    column = mapping.get("amount") or ""
    if _TAX_INCLUDED.search(column):
        raise Unprocessable(
            f"Column {column!r} holds amounts with the tax in them; map the amount before tax",
            code="AMOUNT_INCLUDES_VAT",
            column=column,
        )


def _currency_of(text: str | None) -> str | None:
    if not text:
        return None
    if match := _CURRENCY_CODE.search(text.upper()):
        return match.group(1)
    return next((code for symbol, code in _SYMBOLS.items() if symbol in text), None)


def _money(raw: str) -> str:
    """« 1 234,56 € », « EUR 1.234,56 », « (285,00) »: the number, its sign kept, the rest dropped."""
    text = raw.strip()
    negative = (text.startswith("(") and text.endswith(")")) or text.endswith("-")
    text = text.rstrip("-")
    text = _CURRENCY_CODE.sub("", text.upper())
    text = re.sub(r"[€$£¥()\s]", lambda m: " " if m.group(0).isspace() else "", text).strip()
    return f"-{text.lstrip('-')}" if negative else text


@dataclass
class _Run:
    """What one reading of the file needs, and what it keeps for the job."""

    t: TenantSession
    fx: FxService
    books: _Books
    orders: dict[str, ColumnDates]
    today: date
    header_currency: str | None
    default_type: CostType | None
    given: dict[str, CostType]
    unknown: dict[str, list[tuple[int, str, Decimal]]] = field(default_factory=lambda: defaultdict(list))
    #: The rows refused whose charge is therefore not in the books: `ImportJob.refused_rows`.
    refused: list[dict[str, Any]] = field(default_factory=list)


def apply_costs(
    t: TenantSession,
    job: ImportJob,
    table: Table,
    mapping: dict[str, str],
    report: ImportReport,
    conventions: dict[str, ColumnNumbers],
    fx: FxService | None,
) -> list[UUID]:
    """Read the ledger and write its costs, in the caller's SAVEPOINT. Returns the costs written."""
    if fx is None:  # pragma: no cover - every route passes one
        raise Unprocessable("No exchange rates to convert the amounts with", code="FX_RATE_UNAVAILABLE")
    refuse_tax_included(mapping)
    if mapping.get("credit") and not mapping.get("account"):
        # A journal's credit column holds the supplier's total of every invoice as well as its refunds:
        # only the account tells the one from the other.
        raise Unprocessable(
            "A credit column is read with the account of each line: map the account too",
            code="MAPPING_INCOMPLETE",
            one_of=["account"],
        )
    options = job.options or {}
    header_currency = _currency_of(mapping.get("amount"))
    orders = _date_orders(table, mapping, report)
    run = _Run(
        t=t,
        fx=fx,
        books=_Books(t),
        orders=orders,
        today=datetime.now(UTC).date(),
        header_currency=header_currency,
        default_type=CostType(options["default_cost_type"]) if options.get("default_cost_type") else None,
        given={
            label_key(label): CostType(kind) for label, kind in (options.get("label_types") or {}).items()
        },
    )
    _preload_rates(t, fx, table, mapping, orders, header_currency)

    lines: list[_Read] = []
    credits: list[_Credit] = []
    for idx, raw in enumerate(table.rows, start=table.header_row + 1):
        read = _read(run, _Row(idx, raw, mapping, report, conventions))
        if isinstance(read, _Credit):
            credits.append(read)
        elif read is not None:
            lines.append(read)

    groups = _group(lines)
    reversed_rows, reverses = _pair_credits(run.books, groups, credits, report)
    skipped_credits = [c for c in credits if c.row not in reversed_rows]
    written, matched, counters = _write(
        run, job, [g for g in groups if g.rows], report, bool(options.get("force_duplicates"))
    )
    report.valid_rows += len(skipped_credits)
    for credit in skipped_credits:
        reversed_cost = reverses.get(credit.row)
        report.warnings.append(
            RowIssue(
                credit.row,
                "amount",
                "CREDIT_NOTE_SKIPPED",
                "a negative line is not a cost; it is set aside and counted",
                {
                    "amount": str(credit.amount),
                    "currency": credit.currency,
                    **({"reverses_cost_id": str(reversed_cost.id)} if reversed_cost is not None else {}),
                },
            )
        )
    report.extra |= counters | {
        "unknown_labels": [
            {
                "label": entries[0][1],
                "rows": len(entries),
                "total_base": str(q2(sum((amount for _, _, amount in entries), Decimal(0)))),
            }
            for entries in run.unknown.values()
        ],
        "credit_notes_skipped": [
            {
                "row": c.row,
                "cost_date": c.cost_date.isoformat(),
                "vendor": c.vendor,
                "invoice_number": c.invoice_number,
                "amount": str(c.amount),
                "currency": c.currency,
                "amount_base": str(c.amount_base),
                "reverses_cost_id": str(reverses[c.row].id) if c.row in reverses else None,
            }
            for c in skipped_credits
        ],
        "credit_notes_skipped_base": str(q2(sum((c.amount_base for c in skipped_credits), Decimal(0)))),
        "reversed_in_file": len(reversed_rows) // 2,
    }
    report.internal |= {"matched_cost_ids": [str(i) for i in matched], "refused_rows": run.refused}
    return written


def _date_orders(table: Table, mapping: dict[str, str], report: ImportReport) -> dict[str, ColumnDates]:
    column = mapping.get("cost_date")
    orders = infer_date_orders(table, {column}) if column else {}
    read = orders.get(column) if column else None
    if read is not None and read.ambiguous:
        report.warnings.append(
            RowIssue(
                table.header_row,
                "cost_date",
                "DATE_ORDER_ASSUMED",
                f"In column {column!r} every date could be read either way; the day was read first",
                {"column": column, "example": read.example or "", "order": read.order},
            )
        )
    return orders


def _preload_rates(
    t: TenantSession,
    fx: FxService,
    table: Table,
    mapping: dict[str, str],
    orders: dict[str, ColumnDates],
    header_currency: str | None,
) -> None:
    """Every rate the file will ask for, in one call per currency: a courtesy, never a condition."""
    days: dict[str, list[date]] = defaultdict(list)
    date_column, currency_column = mapping.get("cost_date"), mapping.get("currency")
    for raw in table.rows:
        try:
            day = parse_date(
                raw.get(date_column or "", ""),
                order=orders[date_column].order if date_column in orders else "dmy",
            )
        except ImportError_:
            continue
        currency = (raw.get(currency_column or "", "") or "").strip().upper() or header_currency
        if day is not None and currency and currency != t.org.base_currency:
            days[currency].append(day)
    for currency, seen in days.items():
        fx.preload(t.db, t.org.base_currency, currency, min(seen), max(seen))


def _read(run: _Run, r: _Row) -> _Read | _Credit | None:
    """One row, read: a charge, a refund — or None, for a row set aside (said as a warning) or refused
    (said as errors, and kept in `run.refused` when its charge is therefore missing)."""
    first_issue = len(r.report.errors)
    label = r.text("label") or ""

    # A journal's other lines, and VAT, are set aside before anything is asked of them: the supplier's
    # total has no box, and it is not missing.
    if (aside := _set_aside(r, label)) is not None:
        field_name, code, params = aside
        r.warn(field_name, code, "not a charge: set aside", **params)
        r.report.valid_rows += 1
        return None
    verdict = vat_in_label(label)
    if verdict == "tax_included":
        r.error("label", "AMOUNT_INCLUDES_VAT", f"{label!r} has the tax in its amount", value=label)

    cost_date = _date(run, r)
    amount = _amount(r)
    currency = _currency(run, r, amount)
    vendor = entry.none_if_blank(r.text("vendor"))
    number = entry.number_or_none(r.text("invoice_number"))
    target, target_issue = _target(r, run.books)
    if r.failed or cost_date is None or amount is None:
        _refuse(run, r, first_issue, cost_date, amount, currency, vendor, number, target)
        return None

    kind, kind_issue = _kind(r, label, verdict, run.default_type, run.given)
    if amount < 0:
        # A refund is counted whether or not its box and its type can be read: only a person can say
        # what it refunds, and it must not vanish because its label says « avoir » and nothing else.
        rate = _rate(run, r, currency, cost_date)
        if rate is None:
            _refuse(run, r, first_issue, cost_date, amount, currency, vendor, number, target)
            return None
        return _Credit(
            row=r.row_no,
            currency=currency,
            amount=q2(amount),
            amount_base=to_base(q2(amount), rate[0]),
            cost_date=cost_date,
            vendor=vendor,
            invoice_number=number,
            scope=target[0] if target else None,
            target_id=target[1] if target else None,
            cost_type=kind[0] if kind else None,
        )
    if amount == 0:
        r.warn("amount", "ZERO_AMOUNT", "a line of nothing is not a cost")
        r.report.valid_rows += 1
        return None
    for issue in (target_issue, kind_issue):
        if issue is not None:
            field_name, code, message, params = issue
            r.error(field_name, code, message, **params)
    if kind_issue is not None and kind_issue[:2] == ("label", "COST_TYPE_UNKNOWN"):
        # Said with what hangs on it, so that a person types the label once for all its rows.
        base = _base(run, currency, cost_date, amount)
        run.unknown[label_key(label)].append((r.row_no, label, base or Decimal(0)))
    if target is None or kind is None:
        _refuse(run, r, first_issue, cost_date, amount, currency, vendor, number, target)
        return None
    rate = _rate(run, r, currency, cost_date)
    if rate is None:
        _refuse(run, r, first_issue, cost_date, amount, currency, vendor, number, target)
        return None
    scope, target_id, target_label = target
    cost_type, inferred = kind
    return _Read(
        row=r.row_no,
        scope=scope,
        target_id=target_id,
        target_label=target_label,
        cost_type=cost_type,
        type_inferred=inferred,
        currency=currency,
        amount=q2(amount),
        fx=rate,
        cost_date=cost_date,
        vendor=vendor,
        invoice_number=number,
        label=r.text("label"),
        # The row as its source wrote it — the target's cells, not the target they resolve to today: a
        # bill of lading that gains a second box does not make last month's line a new one.
        source="|".join(
            (
                cost_date.isoformat(),
                entry.normalize_vendor(vendor),
                entry.normalize_invoice_number(number),
                *(
                    re.sub(r"\s+", " ", r.text(name) or "").strip().casefold()
                    for name in ("label", "cost_type", "container_number", "shipment_reference", "po_number")
                ),
                str(q2(amount)),
                currency,
            )
        ),
    )


def _set_aside(r: _Row, label: str) -> tuple[str, str, dict[str, str]] | None:
    """A line that is not a charge, and says so: a journal's line on another account than a charge's,
    or VAT. None for a charge — or for a line whose account is not said, which is refused as such."""
    if r.mapping.get("account"):
        account = re.sub(r"\s+", "", r.text("account") or "")
        if not account:
            r.error("account", "REQUIRED", "account is empty")
            return None
        if account.startswith("445"):
            return "account", "VAT_NOT_A_COST", {"account": account}
        if not account.startswith("6"):
            return "account", "NOT_A_CHARGE_ACCOUNT", {"account": account}
    typed = _TYPE_NAMES.get(re.sub(r"\s+", " ", (r.text("cost_type") or "").strip()).casefold())
    verdict = vat_in_label(label)
    # The invoice's own VAT is never a cost, whatever a type column says of it; import VAT a column
    # types as a charge is VAT all the same. Only a column saying IMPORT_VAT keeps a VAT line, as such.
    if typed is not CostType.IMPORT_VAT and (
        verdict == "vat_line" or (verdict == "import_vat" and typed is not None)
    ):
        return "label", "VAT_NOT_A_COST", {"label": label}
    return None


def _date(run: _Run, r: _Row) -> date | None:
    raw_date = r.text("cost_date")
    if raw_date is None:
        r.error("cost_date", "REQUIRED", "cost_date is empty")
        return None
    column = r.mapping.get("cost_date") or ""
    try:
        cost_date = parse_date(raw_date, order=run.orders[column].order if column in run.orders else "dmy")
    except ImportError_ as e:
        r.error("cost_date", e.code, e.message, value=raw_date)
        return None
    if cost_date is not None and cost_date > run.today:
        r.error("cost_date", "DATE_IN_FUTURE", f"cost_date {cost_date.isoformat()} is still to come",
                value=raw_date)  # fmt: skip
        return None
    return cost_date


def _amount(r: _Row) -> Decimal | None:
    """The amount before tax: the amount column, less the credit column where a journal has one."""
    raw_amount, raw_credit = r.text("amount"), r.text("credit")
    if raw_amount is None and raw_credit is None:
        r.error("amount", "REQUIRED", "amount is empty")
        return None
    total = Decimal(0)
    for name, raw, sign in (("amount", raw_amount, 1), ("credit", raw_credit, -1)):
        if raw is None:
            continue
        convention = r.conventions.get(r.mapping.get(name) or "")
        try:
            value = parse_decimal(_money(raw), comma=convention.comma if convention else "auto")
        except ImportError_ as e:
            r.error(name, e.code, e.message, value=raw)
            return None
        if value is None:
            continue
        if q2(value) != value:
            # « 1.234 » in a column of cents is a thousands separator read as a decimal point, or a
            # currency with mils: either way, rounding it to cents would say another amount.
            r.error(name, "AMOUNT_PRECISION", f"{raw!r} has more than two decimals", value=raw)
            return None
        total += sign * value
    return total


def _currency(run: _Run, r: _Row, amount: Decimal | None) -> str:
    raw_amount = r.text("amount") or r.text("credit") or ""
    stated = (r.text("currency") or "").upper() or None
    in_amount = _currency_of(raw_amount)
    currency = stated or in_amount or run.header_currency or run.t.org.base_currency
    if stated is not None and (len(stated) != 3 or not stated.isalpha()):
        r.error("currency", "INVALID_CURRENCY", f"{stated!r} is not a 3-letter ISO code", value=stated)
    said = stated or run.header_currency
    for other in (run.header_currency, in_amount):
        if said and other and other != said:
            r.error(
                "currency",
                "AMOUNT_CURRENCY_MISMATCH",
                f"the amount says {other} and the currency {said}",
                amount=other,
                currency=said,
            )
            break
    return currency


def _rate(run: _Run, r: _Row, currency: str, day: date) -> tuple[Decimal, date, str] | None:
    try:
        resolved = run.fx.resolve(run.t.db, run.t.org.base_currency, currency, day)
    except Unprocessable:
        r.error(
            "currency",
            "FX_RATE_UNAVAILABLE",
            f"no {currency} rate is known for {day.isoformat()}",
            currency=currency,
            date=day.isoformat(),
        )
        return None
    return resolved.rate, resolved.rate_date, resolved.source


def _base(run: _Run, currency: str, day: date | None, amount: Decimal | None) -> Decimal | None:
    """The amount in the organization's currency, when it can be said without a word to the row."""
    if day is None or amount is None:
        return None
    try:
        resolved = run.fx.resolve(run.t.db, run.t.org.base_currency, currency, day)
    except Unprocessable:
        return None
    return to_base(q2(amount), resolved.rate)


def _refuse(
    run: _Run,
    r: _Row,
    first_issue: int,
    cost_date: date | None,
    amount: Decimal | None,
    currency: str,
    vendor: str | None,
    number: str | None,
    target: tuple[CostScope, UUID, str] | None,
) -> None:
    """Keep a refused row for the audit's preparation: its charge is not in the books."""
    codes = [issue.code for issue in r.report.errors[first_issue:]]
    if not codes or all(code in NOT_MISSING for code in codes):
        return
    run.refused.append(
        _refused_row(
            r.row_no,
            codes[0],
            cost_date,
            amount,
            currency,
            _base(run, currency, cost_date, amount),
            vendor,
            number,
            (target[0].value, str(target[1])) if target is not None else None,
        )
    )


def _refused_row(
    row: int,
    code: str,
    cost_date: date | None,
    amount: Decimal | None,
    currency: str,
    amount_base: Decimal | None,
    vendor: str | None,
    number: str | None,
    target: tuple[str, str] | None,
) -> dict[str, Any]:
    return {
        "row": row,
        "code": code,
        "cost_date": cost_date.isoformat() if cost_date else None,
        "amount": str(q2(amount)) if amount is not None else None,
        "currency": currency,
        "amount_base": str(amount_base) if amount_base is not None else None,
        "vendor": vendor,
        "invoice_number": number,
        "scope": target[0] if target else None,
        "target_id": target[1] if target else None,
    }


def _target(r: _Row, books: _Books) -> tuple[tuple[CostScope, UUID, str] | None, Issue | None]:
    """The box, bill or order a row is about — from its columns in that order, else from its label — or
    what stands in the way. Nothing is said to the row here: a refund is counted without a target."""
    if (raw := r.text("container_number")) is not None:
        number = re.sub(r"[\s\-]", "", raw).upper()
        if (box := books.containers.get(number)) is None:
            return None, ("container_number", "UNKNOWN_CONTAINER", f"container {raw!r} is not known",
                          {"value": raw})  # fmt: skip
        return (CostScope.CONTAINER, box.id, box.container_number), None
    if (raw := r.text("shipment_reference")) is not None:
        if (shipment := books.shipments.get(normalize_reference(raw))) is None:
            return None, ("shipment_reference", "UNKNOWN_SHIPMENT", f"bill of lading {raw!r} is not known",
                          {"value": raw})  # fmt: skip
        return _bill(books, shipment), None
    if (raw := r.text("po_number")) is not None:
        if (order := books.orders.get(raw.strip().upper())) is None:
            return None, ("po_number", "UNKNOWN_ORDER", f"order {raw!r} is not known", {"value": raw})
        return (CostScope.PO, order.id, order.po_number), None
    label = r.text("label") or ""
    numbers = container_numbers_in(label)
    if len(numbers) > 1:
        return None, (
            "label",
            "TARGET_AMBIGUOUS",
            "the label names several containers; which one is a person's call",
            {"value": label, "container_numbers": ", ".join(numbers)},
        )
    if numbers:
        # A box written with its check digit is the box meant, known or not: another reference of the
        # label does not stand in for it.
        if (box := books.containers.get(numbers[0])) is None:
            return None, ("label", "UNKNOWN_CONTAINER", f"container {numbers[0]} is not known",
                          {"value": numbers[0]})  # fmt: skip
        found: tuple[CostScope, UUID, str] | None = (CostScope.CONTAINER, box.id, box.container_number)
    else:
        found = _in_label(label, books)
    if found is None:
        return None, (
            "label",
            "NO_TARGET",
            "no container, bill of lading or order is named on this row",
            {"value": label},
        )
    r.warn("label", "TARGET_FROM_LABEL", f"{found[2]} read in the label", value=found[2])
    return found, None


def _bill(books: _Books, shipment: Shipment) -> tuple[CostScope, UUID, str]:
    """A bill of lading with one box on it is that box: the per-box rules see the cost."""
    boxes = books.boxes_of.get(shipment.id, set())
    if len(boxes) == 1:
        (box_id,) = boxes
        box = books.container_by_id[box_id]
        return CostScope.CONTAINER, box.id, box.container_number
    return CostScope.SHIPMENT, shipment.id, shipment.reference


def _in_label(label: str, books: _Books) -> tuple[CostScope, UUID, str] | None:
    """A known bill or order named in the label, alone of its kind."""
    words = {normalize_reference(word) for word in re.findall(r"[A-Z0-9][A-Z0-9/\-]{3,}", label.upper())}
    bills = [books.shipments[w] for w in words if w in books.shipments]
    orders = [books.orders[w] for w in words if w in books.orders]
    if len(bills) == 1 and not orders:
        return _bill(books, bills[0])
    if len(orders) == 1 and not bills:
        return CostScope.PO, orders[0].id, orders[0].po_number
    return None


def _kind(
    r: _Row, label: str, verdict: VatVerdict | None, default_type: CostType | None, given: dict[str, CostType]
) -> tuple[tuple[CostType, bool] | None, Issue | None]:
    """The type, and whether it was inferred rather than read in a column — or what stands in the way.
    Nothing is said to the row here: a refund is counted without a type."""
    ambiguous: Issue = ("label", "COST_TYPE_AMBIGUOUS", f"{label!r} names several charges; split the line",
                        {"value": label})  # fmt: skip
    if verdict == "mixed":
        return None, ambiguous
    if (raw := r.text("cost_type")) is not None:
        kind = _TYPE_NAMES.get(re.sub(r"\s+", " ", raw.strip()).casefold())
        if kind is None:
            return None, ("cost_type", "COST_TYPE_UNKNOWN", f"{raw!r} is not a cost type", {"value": raw})
        return (kind, False), None
    if verdict == "import_vat":
        return (CostType.IMPORT_VAT, True), None
    if _MIXED.search(label):
        return None, ambiguous
    named = types_named(label)
    if len(named) > 1:
        return None, (*ambiguous[:3], {"value": label, "types": ",".join(sorted(k.value for k in named))})
    if named:
        return (next(iter(named)), True), None
    if (kind := given.get(label_key(label))) is not None:
        return (kind, True), None
    if default_type is not None:
        return (default_type, True), None
    return None, ("label", "COST_TYPE_UNKNOWN", "no cost type can be read on this row", {"value": label})


def _group(lines: Iterable[_Read]) -> list[_Group]:
    """Identical lines of one invoice are one cost: two THC of 285 on one box, one invoice, are 570."""
    groups: dict[tuple[object, ...], _Group] = {}
    for line in lines:
        number = entry.normalize_invoice_number(line.invoice_number)
        key = (
            line.scope,
            line.target_id,
            line.cost_type,
            line.currency,
            entry.normalize_vendor(line.vendor),
            number or None,
            None if number else line.cost_date,
        )
        groups.setdefault(key, _Group()).rows.append(line)
    return list(groups.values())


def _pair_credits(
    books: _Books, groups: list[_Group], credits: list[_Credit], report: ImportReport
) -> tuple[set[int], dict[int, Cost]]:
    """A refund that cancels a line exactly — the same target, currency and amount, the same type when
    it says one, the same forwarder when both name one.

    Paired with a line this file writes, it takes it out with it: neither is a cost, and the pair is
    said. A line this file does not write — an earlier import wrote it, or the invoice is in the books
    already — is not cancelled by leaving it out: the refund is set aside, and names the cost it
    reverses (returned by the refund's row). Lines that are new to the books are paired first."""
    by_amount: dict[tuple[object, ...], list[tuple[_Group, _Read]]] = defaultdict(list)
    for group in groups:
        for line in group.rows:
            by_amount[(line.scope, line.target_id, line.currency, line.amount)].append((group, line))
    out: set[int] = set()
    reverses: dict[int, Cost] = {}
    recorded: dict[int, tuple[bool, Cost | None]] = {}

    def on_file(group: _Group) -> tuple[bool, Cost | None]:
        if id(group) not in recorded:
            recorded[id(group)] = _recorded_elsewhere(books, group)
        return recorded[id(group)]

    for credit in credits:
        if credit.scope is None:
            continue
        candidates = [
            (group, line)
            for group, line in by_amount.get(
                (credit.scope, credit.target_id, credit.currency, -credit.amount), []
            )
            if line.row not in out
            and (credit.cost_type is None or line.cost_type is credit.cost_type)
            and _same_vendor(credit.vendor, line.vendor)
        ]
        fresh = next(((g, line) for g, line in candidates if not on_file(g)[0]), None)
        if fresh is None:
            # The line is held already: leaving it out of this file would not take it out of the books.
            # A line waiting in the inbox holds no cost yet, and the refund is counted like any other.
            if candidates and (cost := on_file(candidates[0][0])[1]) is not None:
                reverses[credit.row] = cost
            continue
        group, line = fresh
        group.rows.remove(line)
        recorded.pop(id(group), None)  # a group of fewer rows may be one the books hold
        out |= {line.row, credit.row}
        for row, other in ((line.row, credit.row), (credit.row, line.row)):
            report.errors.append(
                RowIssue(
                    row,
                    "amount",
                    "REVERSED_IN_FILE",
                    f"cancelled by row {other} of the same file",
                    {"row": other, "amount": str(line.amount), "currency": line.currency},
                )
            )
    return out, reverses


def _same_vendor(first: str | None, second: str | None) -> bool:
    """One forwarder — or one of the two lines does not say which."""
    a, b = entry.normalize_vendor(first), entry.normalize_vendor(second)
    return not a or not b or a == b


def _recorded_elsewhere(books: _Books, group: _Group) -> tuple[bool, Cost | None]:
    """Whether this line is held outside the file already — written by an earlier import of the ledger,
    recorded through another door, or waiting in the inbox — and the cost that holds it, if it is one."""
    if (previous := _already_imported(books, group, set())) is not None:
        return True, previous
    refusal = _already_in_books(books, group, force=True)
    if refusal is None:
        return False, None
    return True, refusal[2]


def _write(
    run: _Run, job: ImportJob, groups: list[_Group], report: ImportReport, force_duplicates: bool
) -> tuple[list[UUID], list[UUID], dict[str, object]]:
    t, books = run.t, run.books
    matched: list[UUID] = []
    taken: set[UUID] = set()
    written: list[Cost] = []
    rows_of: dict[UUID, int] = {}
    replaced_base = Decimal(0)
    duplicates: list[_Group] = []
    comp: Computation | None = None
    # Duty this file wrote, which the computation does not hold yet: by target, and by the lines each
    # covers. On the same target it is added as it stands; on another target covering the same lines —
    # a bill over its box — only a new computation knows its share, and one is made then, and only then.
    written_duty: dict[UUID, Decimal] = defaultdict(Decimal)
    duty_on_line: dict[UUID, UUID] = {}
    # Duty last: the customs value it is weighed against includes the freight of this very file.
    for group in sorted(groups, key=lambda g: g.first.cost_type is DUTY):
        first, anchor = group.first, group.anchor
        previous = _already_imported(books, group, taken)
        if previous is not None:
            matched.append(previous.id)
            taken.add(previous.id)
            report.valid_rows += len(group.rows)
            continue
        refusal = _already_in_books(books, group, force_duplicates)
        if refusal is not None:
            code, params, _ = refusal
            duplicates.append(group)
            for row in group.rows:
                report.errors.append(RowIssue(row.row, "invoice_number", code, _SAID[code], params))
            continue
        estimates = books.on_freight(books.estimates_on, first.scope, first.target_id, first.cost_type)
        here = [e for e in estimates if _target_of(e) == first.target_id]
        if len(here) > 1 or len(estimates) > len(here):
            code = "SEVERAL_ESTIMATES" if len(here) > 1 else "ESTIMATE_OTHER_SCOPE"
            for row in group.rows:
                report.errors.append(
                    RowIssue(
                        row.row,
                        "cost_type",
                        code,
                        "an estimate of this type stands elsewhere on this freight; replace it by hand",
                        {"estimates": str(len(estimates)), "cost_type": first.cost_type.value},
                    )
                )
                run.refused.append(
                    _refused_row(
                        row.row, code, row.cost_date, row.amount, row.currency, row.amount_base, row.vendor,
                        row.invoice_number, (row.scope.value, str(row.target_id)),
                    )
                )  # fmt: skip
            continue
        amount_base = group.amount_base
        if first.cost_type is DUTY:
            if comp is None:
                t.db.flush()  # the file's freight is part of the customs value the duty is weighed on
                comp = compute(t.db, t.org)
            loads = {load.id for load in entry.loads_under(comp, first.scope, first.target_id)}
            if any(duty_on_line.get(line, first.target_id) != first.target_id for line in loads):
                t.db.flush()
                comp = compute(t.db, t.org)
                written_duty.clear()
                duty_on_line.clear()
            method = entry.default_method(
                t.db, t.org, first.scope, first.target_id, DUTY, amount_base, comp,
                pending=written_duty[first.target_id],
            )  # fmt: skip
            written_duty[first.target_id] += amount_base
            duty_on_line |= dict.fromkeys(loads, first.target_id)
        else:
            method = entry.default_method(t.db, t.org, first.scope, first.target_id, first.cost_type)
        cost = Cost(
            id=uuid4(),
            scope=first.scope,
            status=CostStatus.ACTUAL,
            cost_type=first.cost_type,
            amount=group.amount,
            currency=first.currency,
            fx_rate=anchor.fx[0],
            fx_date=anchor.fx[1],
            fx_source=anchor.fx[2],
            amount_base=amount_base,
            allocation_method=method,
            cost_date=anchor.cost_date,
            vendor=first.vendor,
            invoice_number=first.invoice_number,
            notes=(
                f"From {job.original_filename or 'a costs file'} "
                f"{'rows' if len(group.rows) > 1 else 'row'} {', '.join(str(r.row) for r in group.rows)}"
            ),
            import_job_id=job.id,
            import_row_key=group.row_key,
            type_inferred=any(row.type_inferred for row in group.rows),
        )
        setattr(cost, _KEY[first.scope], first.target_id)
        if (key := _index_key(cost.vendor, cost.invoice_number, cost.cost_type, cost.currency,
                              first.target_id)) is not None:  # fmt: skip
            books.lines[key] = cost  # a second group of the file on the same line is refused by name
        if here:
            cost.supersedes_cost_id = here[0].id
            books.estimates_on[first.target_id].remove(here[0])
            replaced_base += here[0].amount_base
        t.add(cost)
        written.append(cost)
        rows_of[cost.id] = len(group.rows)
        report.valid_rows += len(group.rows)

    t.db.flush()
    by_type: dict[CostType, list[Cost]] = defaultdict(list)
    by_currency: dict[str, list[Cost]] = defaultdict(list)
    for cost in written:
        by_type[cost.cost_type].append(cost)
        by_currency[cost.currency].append(cost)

    counters: dict[str, object] = {
        "costs_created": len(written),
        "costs_skipped": len(matched),
        "estimates_replaced": sum(1 for cost in written if cost.supersedes_cost_id is not None),
        "estimates_replaced_base": str(q2(replaced_base)),
        "duplicates": sum(len(g.rows) for g in duplicates),
        "duplicates_base": str(q2(sum((g.amount_base for g in duplicates), Decimal(0)))),
        "money": {
            # What the file adds to the landed costs: import VAT is written, and counted apart by type.
            "total_base": str(
                q2(
                    sum(
                        (c.amount_base for c in written if c.cost_type.value not in EXCLUDED_FROM_LANDED),
                        Decimal(0),
                    )
                )
            ),
            "by_type": [
                {
                    "cost_type": kind.value,
                    "rows": sum(rows_of[c.id] for c in costs),
                    "total_base": str(q2(sum((c.amount_base for c in costs), Decimal(0)))),
                }
                for kind, costs in sorted(by_type.items(), key=lambda item: item[0].value)
            ],
            "by_currency": [
                {
                    "currency": currency,
                    "rows": sum(rows_of[c.id] for c in costs),
                    "total": str(q2(sum((c.amount for c in costs), Decimal(0)))),
                    "total_base": str(q2(sum((c.amount_base for c in costs), Decimal(0)))),
                }
                for currency, costs in sorted(by_currency.items())
            ],
        },
    }
    return [cost.id for cost in written], matched, counters


_KEY = {
    CostScope.CONTAINER: "container_id",
    CostScope.SHIPMENT: "shipment_id",
    CostScope.PO: "po_id",
    CostScope.PO_LINE: "po_line_id",
}
_SAID = {
    "INVOICE_ALREADY_RECORDED": "this invoice is already in the books",
    "INVOICE_LINE_ALREADY_RECORDED": "this very invoice line is already on file",
    "INVOICE_IN_INBOX": "this invoice is waiting in the inbox; confirm or reject it there",
    "DUPLICATE_COST": "a charge of this type is already recorded on this freight",
}


def _target_of(cost: Cost) -> UUID | None:
    return cost.container_id or cost.shipment_id or cost.po_id or cost.po_line_id


def _already_imported(books: _Books, group: _Group, matched: set[UUID]) -> Cost | None:
    """The cost an earlier import of the same ledger wrote from these very rows — whatever a person
    changed on it since: its type, its amount, its box. Each is matched once: two identical lines are
    two costs only if the books hold two."""
    return next((cost for cost in books.imported.get(group.row_key, []) if cost.id not in matched), None)


def _already_in_books(
    books: _Books, group: _Group, force: bool
) -> tuple[str, dict[str, str], Cost | None] | None:
    """The refusals of the invoice's confirmation, read on the books: the invoice already recorded
    (`invoice_recorded`), the very line already on file as an estimate or a closed cost — which the
    database's uniqueness holds too — the invoice waiting in the inbox, or a charge of this type already
    on this freight (`duplicate_cost_note`, which a person may overrule). With the cost concerned, when
    there is one."""
    first = group.first
    number = entry.normalize_invoice_number(first.invoice_number)
    wanted = entry.normalize_vendor(first.vendor)
    if number:
        for cost in books.by_number.get(number, []):
            theirs = entry.normalize_vendor(cost.vendor)
            if (wanted and theirs and wanted == theirs) or (
                not (wanted and theirs) and books.same_freight(cost, first.scope, first.target_id)
            ):
                return "INVOICE_ALREADY_RECORDED", _recorded(cost), cost
    key = _index_key(first.vendor, first.invoice_number, first.cost_type, first.currency, first.target_id)
    if key is not None and (same := books.lines.get(key)) is not None:
        return "INVOICE_LINE_ALREADY_RECORDED", _recorded(same), same
    if number:
        for invoice in books.inbox.get(number, []):
            theirs = entry.normalize_vendor(invoice.vendor)
            if not wanted or not theirs or wanted == theirs:
                return (
                    "INVOICE_IN_INBOX",
                    {"invoice_id": str(invoice.id), "invoice_number": invoice.invoice_number or ""},
                    None,
                )
    if not force:
        for cost in books.on_freight(books.actuals_on, first.scope, first.target_id, first.cost_type):
            return "DUPLICATE_COST", _recorded(cost), cost
    return None


def _recorded(cost: Cost) -> dict[str, str]:
    return {
        "cost_type": cost.cost_type.value,
        "vendor": note_field(cost.vendor),
        "invoice_number": note_field(cost.invoice_number),
        "amount_base": str(cost.amount_base),
        "scope": cost.scope.value,
    }
