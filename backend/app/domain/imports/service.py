"""Import jobs: parse → validate (dry run under a SAVEPOINT) → commit. Same code path both times."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select

from app.core.errors import Conflict, Unprocessable
from app.core.tenancy import TenantSession
from app.domain.costing.service import recompute_org
from app.domain.fx.service import FxService
from app.domain.imports.fields import REQUIRED_ONE_OF
from app.domain.imports.parsing import (
    CONTAINER_NUMBER_RE,
    REQUIRED,
    ColumnNumbers,
    ImportError_,
    Table,
    header_signature,
    infer_comma_conventions,
    is_ambiguous_unit_column,
    parse_decimal,
    parse_table,
    suggest_mapping,
)
from app.domain.models import (
    Container,
    ContainerLoad,
    ImportJob,
    ImportKind,
    ImportMapping,
    ImportStatus,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
)
from app.domain.products import service as products

#: Columns whose commas have to be read as a number, and whose convention is therefore inferred
#: over the whole file before the first row is parsed.
NUMERIC_FIELDS = (
    "quantity",
    "unit_price",
    "unit_weight_kg",
    "unit_volume_cbm",
    "duty_rate",
    "line_no",
    "container_quantity",
    "total_value",
    "allocation_percentage",
    "amount",
    "credit",
    "sale_price",
)
#: Fields an export may state once, on the first row of an order, and leave blank underneath.
FORWARD_FILLED_FIELDS = ("po_number", "supplier_name", "currency")


@dataclass
class RowIssue:
    """One thing to say about one row. `message` is the English fallback; `code` + `params` are
    what the front translates — a report that shows "CODE FX_RATE_DEFAULTED" to a CFO says
    nothing, "taux 1,000000 au 17/09/2026, faute de taux connu" says everything."""

    row: int
    field: str
    code: str
    message: str
    #: Not `field(default_factory=dict)`: the attribute named `field` above hides the dataclasses
    #: helper in this class body. Every issue states its params, even when they are empty.
    params: dict[str, Any]


@dataclass
class ImportReport:
    row_count: int = 0
    valid_rows: int = 0
    header_row: int = 1
    totals_row_ignored: bool = False
    forward_filled_rows: int = 0
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    purchase_orders_created: int = 0
    purchase_orders_updated: int = 0
    lines_created: int = 0
    lines_updated: int = 0
    containers_created: int = 0
    loads_created: int = 0
    loads_updated: int = 0
    suppliers_created: int = 0
    kind: ImportKind = ImportKind.PURCHASE_ORDERS
    #: The counters and figures of the kinds read in their own module — containers, costs, products —
    #: as the report states them. A counter of another kind is absent, never zero.
    extra: dict[str, Any] = field(default_factory=dict)
    #: What a commit keeps on the job and never shows: the costs a costs file recognised as written by
    #: an earlier import, the rows it refused. Not part of the report a preview is compared by.
    internal: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        common = {
            "row_count": self.row_count,
            "valid_rows": self.valid_rows,
            "header_row": self.header_row,
            "totals_row_ignored": self.totals_row_ignored,
            "forward_filled_rows": self.forward_filled_rows,
            "errors": [e.__dict__ for e in self.errors],
            "warnings": [w.__dict__ for w in self.warnings],
        }
        if self.kind in (ImportKind.PURCHASE_ORDERS, ImportKind.LEGACY_PO_CONTAINER):
            common |= {
                "purchase_orders_created": self.purchase_orders_created,
                "purchase_orders_updated": self.purchase_orders_updated,
                "lines_created": self.lines_created,
                "lines_updated": self.lines_updated,
                "containers_created": self.containers_created,
                "loads_created": self.loads_created,
                "loads_updated": self.loads_updated,
                "suppliers_created": self.suppliers_created,
            }
        return common | self.extra


def create_job(t: TenantSession, kind: ImportKind, content: bytes, filename: str | None) -> ImportJob:
    try:
        table = parse_table(content, filename)
    except ImportError_ as e:
        raise Unprocessable(e.message, code=e.code) from e
    signature = header_signature(table.columns)
    saved = t.db.scalar(
        t.q(ImportMapping).where(ImportMapping.kind == kind, ImportMapping.header_signature == signature)
    )
    mapping = dict(saved.mapping) if saved else suggest_mapping(kind, table.columns)
    sha256 = hashlib.sha256(content).hexdigest()
    if kind is ImportKind.COSTS and (previous := find_duplicate(t, sha256, kind)) is not None:
        # The same ledger twice is every cost twice; the file already in is opened instead. The other
        # kinds write what their file says, and saying it again changes nothing.
        raise Conflict(
            "This costs file is already imported", code="FILE_ALREADY_IMPORTED", existing_id=str(previous.id)
        )
    job = ImportJob(
        kind=kind,
        original_filename=filename,
        sha256=sha256,
        content=content,
        encoding=table.encoding,
        delimiter=table.delimiter,
        columns=table.columns,
        mapping=mapping,
        row_count=len(table.rows),
        created_by=t.principal.user_id,
    )
    t.add(job)
    t.db.flush()
    return job


def _check_mapping(kind: ImportKind, mapping: dict[str, str], columns: list[str]) -> None:
    missing = [f for f in REQUIRED[kind] if not mapping.get(f)]
    if missing:
        raise Unprocessable(
            f"Mapping is missing required field(s): {', '.join(missing)}", code="MAPPING_INCOMPLETE"
        )
    for group in REQUIRED_ONE_OF.get(kind, ()):
        if not any(mapping.get(f) for f in group):
            raise Unprocessable(
                f"Map at least one of: {', '.join(group)}", code="MAPPING_INCOMPLETE", one_of=list(group)
            )
    unknown = [c for c in mapping.values() if c and c not in columns]
    if unknown:
        raise Unprocessable(
            f"Mapped column(s) not in file: {', '.join(unknown)}", code="MAPPING_UNKNOWN_COLUMN"
        )


def run_job(
    t: TenantSession,
    job: ImportJob,
    mapping: dict[str, str],
    *,
    dry_run: bool,
    on_error: str = "skip_rows",
    fx: FxService | None = None,
    expected: dict[str, Any] | None = None,
) -> ImportReport:
    """Apply the import inside a SAVEPOINT. Dry run rolls it back; commit keeps it and marks the job DONE.

    `expected` is the report of the preview a commit is bound to: when the books have moved since and
    the same file would now do something else, nothing is written and the new report is refused with
    it (PREVIEW_CHANGED) — a person approved figures, not a file."""
    _check_mapping(job.kind, mapping, list(job.columns))
    table = parse_table(bytes(job.content), job.original_filename)
    report = ImportReport(
        row_count=len(table.rows),
        header_row=table.header_row,
        totals_row_ignored=table.totals_row_ignored,
        kind=job.kind,
    )
    conventions = infer_comma_conventions(table, {mapping[f] for f in NUMERIC_FIELDS if mapping.get(f)})
    _report_file_notes(table, mapping, conventions, report)

    nested = t.db.begin_nested()
    try:
        if job.kind is ImportKind.PURCHASE_ORDERS:
            _apply_purchase_orders(t, table, mapping, report, conventions, fx)
        elif job.kind is ImportKind.CONTAINERS:
            from app.domain.imports.containers import apply_containers

            apply_containers(t, table, mapping, report)
        elif job.kind is ImportKind.COSTS:
            from app.domain.imports.costs import apply_costs

            apply_costs(t, job, table, mapping, report, conventions, fx)
        elif job.kind is ImportKind.PRODUCTS:
            from app.domain.imports.products import apply_products

            apply_products(t, table, mapping, report, conventions)
        else:
            _apply_legacy(t, table, mapping, report, conventions)
        if report.errors and on_error == "abort":
            raise Unprocessable(
                f"{len(report.errors)} row(s) have errors and on_error=abort", code="IMPORT_ABORTED"
            )
        if report.valid_rows == 0:
            raise Unprocessable("No row could be imported.", code="NO_VALID_ROWS", report=report.to_dict())
        if expected is not None and report.to_dict() != expected:
            raise Conflict(
                "The books have moved since the preview: look at the figures again before importing",
                code="PREVIEW_CHANGED",
                report=report.to_dict(),
            )
        recompute_org(t.db, t.org)
        if dry_run:
            nested.rollback()
        else:
            nested.commit()
    except Exception:
        if nested.is_active:
            nested.rollback()
        raise

    job.mapping = mapping
    job.error_count = len(report.errors)
    job.report = report.to_dict()
    job.status = ImportStatus.DONE if not dry_run else ImportStatus.VALIDATED
    if not dry_run:
        job.committed_at = func.now()
        if job.kind is ImportKind.COSTS:
            job.matched_cost_ids = list(report.internal.get("matched_cost_ids", []))
            job.refused_rows = list(report.internal.get("refused_rows", []))
        _remember_mapping(t, job, mapping)
    t.db.flush()
    return report


def _remember_mapping(t: TenantSession, job: ImportJob, mapping: dict[str, str]) -> None:
    signature = header_signature(list(job.columns))
    row = t.db.scalar(
        t.q(ImportMapping).where(ImportMapping.kind == job.kind, ImportMapping.header_signature == signature)
    )
    if row is None:
        t.add(ImportMapping(kind=job.kind, header_signature=signature, mapping=mapping))
    else:
        row.mapping = mapping


def _report_file_notes(
    table: Table,
    mapping: dict[str, str],
    conventions: dict[str, ColumnNumbers],
    report: ImportReport,
) -> None:
    """What the reader decided about the file itself, before any row: these belong in the preview,
    because each one is a reading the user may have to contradict."""
    line = table.header_row
    if table.header_row > 1:
        report.warnings.append(
            RowIssue(
                line,
                "",
                "HEADER_ROW_DETECTED",
                f"Column names were read from line {table.header_row}; the lines above were skipped",
                {"line": table.header_row},
            )
        )
    if table.totals_row_ignored:
        report.warnings.append(
            RowIssue(line, "", "TOTALS_ROW_IGNORED", "The last row reads as a totals row and was ignored", {})
        )
    for target, column in sorted(mapping.items()):
        convention = conventions.get(column)
        if target in NUMERIC_FIELDS and convention is not None and convention.ambiguous:
            read_as = "a decimal separator" if convention.comma == "decimal" else "a thousands separator"
            report.warnings.append(
                RowIssue(
                    line,
                    target,
                    "DECIMAL_COMMA_ASSUMED",
                    f"In column {column!r} every comma could be either; read as {read_as} "
                    f"(e.g. {convention.example!r})",
                    {
                        "column": column,
                        "example": convention.example or "",
                        "comma": convention.comma,
                    },
                )
            )
        if target in ("unit_weight_kg", "unit_volume_cbm") and is_ambiguous_unit_column(column):
            report.warnings.append(
                RowIssue(
                    line,
                    target,
                    "UNIT_COLUMN_AMBIGUOUS",
                    f"Column {column!r} does not say whether it holds one unit or the whole line; "
                    f"it is read as one unit",
                    {"column": column},
                )
            )


# ---------------------------------------------------------------------------- row helpers


class _Row:
    def __init__(
        self,
        row_no: int,
        raw: dict[str, str],
        mapping: dict[str, str],
        report: ImportReport,
        conventions: dict[str, ColumnNumbers] | None = None,
    ):
        self.row_no, self.raw, self.mapping, self.report = row_no, raw, mapping, report
        self.conventions = conventions or {}
        self.failed = False

    def text(self, field: str) -> str | None:
        col = self.mapping.get(field)
        if not col:
            return None
        v = self.raw.get(col, "").strip()
        return v or None

    def required(self, field: str) -> str | None:
        v = self.text(field)
        if v is None:
            self.error(field, "REQUIRED", f"{field} is empty")
        return v

    def decimal(self, field: str, *, required: bool = False, positive: bool = False) -> Decimal | None:
        raw = self.text(field)
        if raw is None:
            if required:
                self.error(field, "REQUIRED", f"{field} is empty")
            return None
        column = self.mapping.get(field) or ""
        convention = self.conventions.get(column)
        try:
            v = parse_decimal(raw, comma=convention.comma if convention else "auto")
        except ImportError_ as e:
            self.error(field, e.code, e.message, value=raw)
            return None
        if v is not None and positive and v <= 0:
            self.error(field, "NOT_POSITIVE", f"{field} must be > 0, got {v}", value=str(v))
            return None
        return v

    def rate(self, field: str) -> Decimal | None:
        """A rate as order files and tariffs write it: « 4,5 % » and « 4,5 » are 0.045 — and so is
        « 0,045 ». A cell with its per cent sign is a per cent whatever its size: « 1 % » is 0.01, where
        a bare « 1 » could only be read as a whole."""
        value = self.decimal(field)
        if value is not None and ("%" in (self.text(field) or "") or value > 1):
            return value / 100
        return value

    def date(self, field: str) -> date | None:
        raw = self.text(field)
        if raw is None:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d"):
            try:
                from datetime import datetime

                return datetime.strptime(raw[:10], fmt).date()  # noqa: DTZ007
            except ValueError:
                continue
        self.error(field, "NOT_A_DATE", f"{raw!r} is not a date", value=raw)
        return None

    def container(self, field: str = "container_number") -> str | None:
        raw = self.text(field)
        if raw is None:
            return None
        v = raw.upper().replace(" ", "")
        if not CONTAINER_NUMBER_RE.match(v):
            self.error(field, "INVALID_CONTAINER_NUMBER", f"{raw!r} is not an ISO 6346 number", value=raw)
            return None
        return v

    def error(self, field: str, code: str, message: str, **params: Any) -> None:
        self.failed = True
        self.report.errors.append(RowIssue(self.row_no, field, code, message, params))

    def warn(self, field: str, code: str, message: str, **params: Any) -> None:
        self.report.warnings.append(RowIssue(self.row_no, field, code, message, params))


def _get_or_create_supplier(t: TenantSession, name: str | None, report: ImportReport) -> Supplier | None:
    if not name:
        return None
    sup = t.db.scalar(t.q(Supplier).where(Supplier.name == name))
    if sup is None:
        sup = t.add(Supplier(name=name))
        t.db.flush()
        report.suppliers_created += 1
    return sup


def _get_or_create_container(t: TenantSession, number: str, report: ImportReport) -> Container:
    c = t.db.scalar(
        t.q(Container).where(Container.container_number == number, Container.archived_at.is_(None))
    )
    if c is None:
        c = t.add(Container(container_number=number))
        t.db.flush()
        report.containers_created += 1
    return c


def _upsert_load(
    t: TenantSession, container: Container, line: PurchaseOrderLine, qty: Decimal, r: _Row
) -> None:
    other_qty = t.db.scalar(
        select(func.coalesce(func.sum(ContainerLoad.quantity), 0)).where(
            ContainerLoad.po_line_id == line.id, ContainerLoad.container_id != container.id
        )
    )
    if Decimal(str(other_qty)) + qty > line.quantity:
        loaded = Decimal(str(other_qty)) + qty
        r.error(
            "container_quantity",
            "OVER_ALLOCATED",
            f"{loaded} loaded exceeds line quantity {line.quantity}",
            loaded=str(loaded),
            line_quantity=str(line.quantity),
            container_number=container.container_number,
        )
        return
    load = t.db.scalar(
        select(ContainerLoad).where(
            ContainerLoad.container_id == container.id, ContainerLoad.po_line_id == line.id
        )
    )
    if load is None:
        t.add(ContainerLoad(container_id=container.id, po_line_id=line.id, quantity=qty))
        t.db.flush()
        r.report.loads_created += 1
    elif load.quantity != qty:
        load.quantity = qty
        r.report.loads_updated += 1


def _find_line_without_number(
    t: TenantSession,
    po: PurchaseOrder | None,
    sku: str | None,
    description: str | None,
    occurrence: int = 0,
) -> PurchaseOrderLine | None:
    """Rows without line_no identify their line by SKU, else description, else the PO's single line.

    `occurrence` is how many distinct lines of that SKU the file has already described: the second
    one matches the second line of the order, so re-importing the same file twice keeps writing the
    same two lines instead of piling up new ones.
    """
    if po is None:
        return None
    if sku or description:
        criterion = PurchaseOrderLine.sku == sku if sku else PurchaseOrderLine.description == description
        return t.db.scalar(
            select(PurchaseOrderLine)
            .where(PurchaseOrderLine.po_id == po.id, criterion)
            .order_by(PurchaseOrderLine.line_no)
            .offset(occurrence)
            .limit(1)
        )
    if occurrence:
        return None
    lines = list(t.db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id)))
    return lines[0] if len(lines) == 1 else None


def _forward_filled_rows(table: Table, mapping: dict[str, str], report: ImportReport) -> list[dict[str, str]]:
    """An Odoo-style nested export states the order once and leaves the cell blank underneath.

    Seven lines out of eight of such an order carry a SKU, a quantity and a price but no order
    number, and were failing on REQUIRED — an import that silently kept one line per order. The
    number (and the supplier and currency stated with it) is carried down, but only onto a row that
    does carry a SKU: a genuinely empty row still has nothing to attach to an order.
    """
    po_column = mapping.get("po_number")
    identity_column = mapping.get("sku") or mapping.get("description")
    if not po_column or not identity_column:
        return table.rows
    carried: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    for raw in table.rows:
        row = raw
        if not raw.get(po_column, "").strip() and raw.get(identity_column, "").strip() and carried:
            row = {**raw, **{c: v for c, v in carried.items() if not raw.get(c, "").strip()}}
            report.forward_filled_rows += 1
        rows.append(row)
        if row.get(po_column, "").strip():
            carried = {
                column: row[column].strip()
                for column in (mapping.get(f) for f in FORWARD_FILLED_FIELDS)
                if column and row.get(column, "").strip()
            }
    if report.forward_filled_rows:
        report.warnings.append(
            RowIssue(
                table.header_row,
                "po_number",
                "PO_NUMBER_FORWARD_FILLED",
                f"{report.forward_filled_rows} row(s) carry no order number and were attached to the "
                f"order above them",
                {"rows": report.forward_filled_rows, "column": po_column},
            )
        )
    return rows


def _set_order_rate(t: TenantSession, fx: FxService | None, po: PurchaseOrder, r: _Row) -> None:
    """Give a foreign-currency order the ECB rate of its date, and say which one was used.

    It used to get 1 every time, with a warning: a 40 000 $ order counted as 40 000 €, a FOB about
    15 % too high until somebody noticed, and with it every share allocated by value. The rate of
    the order date (today without one) is what the purchase-order screen applies; the import now
    does the same. Only when no rate can be had does the order fall back to 1, and the warning says
    so with the three things a CFO needs: which rate, of which day, from where.
    """
    on_date = po.order_date or datetime.now(UTC).date()
    resolved = None
    if fx is not None:
        try:
            resolved = fx.resolve(t.db, t.org.base_currency, po.currency, on_date)
        except Unprocessable:
            resolved = None
    if resolved is None:
        r.warn(
            "currency",
            "FX_RATE_DEFAULTED",
            f"PO in {po.currency}: no rate known, fx_rate set to 1; adjust it on the PO",
            currency=po.currency,
            base_currency=t.org.base_currency,
            rate="1",
            rate_date=on_date.isoformat(),
            source="DEFAULT",
        )
        return
    po.fx_rate, po.fx_date = resolved.rate, resolved.rate_date
    r.warn(
        "currency",
        "FX_RATE_APPLIED",
        f"PO in {po.currency}: ECB rate {resolved.rate} of {resolved.rate_date.isoformat()} applied",
        currency=po.currency,
        base_currency=t.org.base_currency,
        rate=str(resolved.rate),
        rate_date=resolved.rate_date.isoformat(),
        source=resolved.source,
    )


def _apply_purchase_orders(
    t: TenantSession,
    table: Table,
    mapping: dict[str, str],
    report: ImportReport,
    conventions: dict[str, ColumnNumbers] | None = None,
    fx: FxService | None = None,
) -> None:
    seen_lines: set[tuple[str, int]] = set()
    catalogue: dict[str, Product | None] = {}
    # (po, sku or description, nth line with that identity) -> line_no, and what that line said.
    # The "nth" is what tells a line repeated for a second container (same quantity and price) from
    # a genuinely second line of the same article, which used to be absorbed into the first one.
    implicit_lines: dict[tuple[str, str, int], int] = {}
    line_values: dict[tuple[str, str, int], tuple[Decimal, Decimal]] = {}
    occurrences: dict[tuple[str, str], int] = {}
    next_line_no: dict[str, int] = {}
    rows = _forward_filled_rows(table, mapping, report)
    for idx, raw in enumerate(rows, start=table.header_row + 1):
        r = _Row(idx, raw, mapping, report, conventions)
        po_number = r.required("po_number")
        quantity = r.decimal("quantity", required=True)
        if quantity is not None and quantity < 0:
            # A return is a credit, not an order line: writing it as one would understate the
            # landed cost of the goods that were actually shipped. Refusing it by name at least
            # tells the user it is the product that cannot do this yet, not their file.
            r.error(
                "quantity",
                "RETURN_NOT_SUPPORTED",
                "a negative quantity is a return; returns and credit notes cannot be imported yet",
                value=str(quantity),
            )
        elif quantity is not None and quantity == 0:
            r.error("quantity", "NOT_POSITIVE", "quantity must be > 0, got 0", value="0")
        unit_price = r.decimal("unit_price", required=True)
        if unit_price is not None and unit_price < 0:
            r.error("unit_price", "NEGATIVE", "unit_price must be >= 0", value=str(unit_price))
        declared_currency = r.text("currency")
        currency = (declared_currency or t.org.base_currency).upper()
        if len(currency) != 3 or not currency.isalpha():
            r.error(
                "currency", "INVALID_CURRENCY", f"{currency!r} is not a 3-letter ISO code", value=currency
            )
        line_no_raw = r.decimal("line_no")
        weight = r.decimal("unit_weight_kg")
        volume = r.decimal("unit_volume_cbm")
        duty = r.rate("duty_rate")
        order_date = r.date("order_date")
        container_number = r.container()
        container_qty = r.decimal("container_quantity", positive=True)
        if r.failed or po_number is None or quantity is None or unit_price is None:
            continue

        po = t.db.scalar(t.q(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
        if po is not None and declared_currency and po.currency != currency:
            # The price would be stored as it stands and read everywhere else in the currency of
            # the order — USD read as CNY is a landed cost off by seven. One order, one currency.
            r.error(
                "currency",
                "CURRENCY_MISMATCH",
                f"{po_number} is in {po.currency}; this row says {currency}",
                po_number=po_number,
                po_currency=po.currency,
                row_currency=currency,
            )
            continue
        sku = r.text("sku")
        repeated_line = False
        if line_no_raw is not None:
            line_no = int(line_no_raw)
            if (po_number, line_no) in seen_lines:
                r.error(
                    "line_no",
                    "DUPLICATE_IN_FILE",
                    f"line {line_no} of {po_number} appears twice",
                    po_number=po_number,
                    line_no=line_no,
                )
                continue
            seen_lines.add((po_number, line_no))
        else:
            identity = sku or r.text("description") or ""
            key = (po_number, identity)
            seen = occurrences.get(key, 0)
            previous = (po_number, identity, seen - 1)
            if seen and line_values.get(previous) == (quantity, unit_price):
                # The same line said twice: the shape of a file that repeats the order line once
                # per container. Only the container part of the row has anything left to say.
                line_no, repeated_line = implicit_lines[previous], True
            else:
                existing_line = _find_line_without_number(t, po, sku, r.text("description"), seen)
                if existing_line is not None:
                    line_no = existing_line.line_no
                else:
                    if po_number not in next_line_no:
                        current_max = (
                            t.db.scalar(
                                select(func.coalesce(func.max(PurchaseOrderLine.line_no), 0)).where(
                                    PurchaseOrderLine.po_id == po.id
                                )
                            )
                            if po is not None
                            else 0
                        )
                        next_line_no[po_number] = int(current_max or 0)
                    next_line_no[po_number] += 1
                    line_no = next_line_no[po_number]
                if seen:
                    r.warn(
                        "sku",
                        "DUPLICATE_SKU_NEW_LINE",
                        f"{identity!r} appears again on {po_number} with another quantity or price; "
                        f"imported as line {line_no}",
                        po_number=po_number,
                        sku=identity,
                        line_no=line_no,
                    )
                implicit_lines[(po_number, identity, seen)] = line_no
                line_values[(po_number, identity, seen)] = (quantity, unit_price)
                occurrences[key] = seen + 1

        supplier = _get_or_create_supplier(t, r.text("supplier_name"), report)
        if po is None:
            po = t.add(
                PurchaseOrder(
                    po_number=po_number,
                    supplier_id=supplier.id if supplier else None,
                    currency=currency,
                    fx_rate=Decimal("1"),
                    order_date=order_date,
                )
            )
            t.db.flush()
            report.purchase_orders_created += 1
            if currency != t.org.base_currency:
                _set_order_rate(t, fx, po, r)
        else:
            changed = False
            if supplier and po.supplier_id != supplier.id:
                po.supplier_id, changed = supplier.id, True
            if order_date and po.order_date != order_date:
                po.order_date, changed = order_date, True
            if changed:
                report.purchase_orders_updated += 1

        line = t.db.scalar(
            select(PurchaseOrderLine).where(
                PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.line_no == line_no
            )
        )
        # What the order says, and what someone may have added by hand. The first follows the
        # source; the second is only ever overwritten by a value, never by a blank: an ERP that
        # knows no weights, or a file without the column, must not erase the weights a person
        # typed here so that freight could be split by weight. That erasure would be silent and
        # would repeat at every sync.
        source = {
            "sku": sku,
            "description": r.text("description"),
            "quantity": quantity,
            "unit_price": unit_price,
        }
        enrichment = {
            "hs_code": r.text("hs_code"),
            "unit_weight_kg": weight,
            "unit_volume_cbm": volume,
            "duty_rate": duty,
        }
        if line is None:
            line = t.add(PurchaseOrderLine(po_id=po.id, line_no=line_no, **source, **enrichment))
            # What the file leaves blank and the catalogue knows is copied onto the new line, once:
            # a tariff rate typed on the product does not have to be typed again on every order.
            if sku:
                if sku not in catalogue:
                    catalogue[sku] = products.by_sku(t.db, t.org_id, {sku}).get(sku)
                lent = products.fill_blanks(line, catalogue[sku])
                if lent:
                    r.warn(
                        "sku",
                        "PRODUCT_DEFAULTS_APPLIED",
                        f"{sku}: {', '.join(lent)} taken from the product catalogue",
                        sku=sku,
                        fields=",".join(lent),
                    )
            t.db.flush()
            report.lines_created += 1
        elif not repeated_line:
            updates = {**source, **{k: v for k, v in enrichment.items() if v is not None}}
            if any(getattr(line, k) != v for k, v in updates.items()):
                for k, v in updates.items():
                    setattr(line, k, v)
                report.lines_updated += 1

        if container_number:
            container = _get_or_create_container(t, container_number, report)
            _upsert_load(t, container, line, container_qty or quantity, r)
            if r.failed:
                continue
        report.valid_rows += 1


def _apply_legacy(
    t: TenantSession,
    table: Table,
    mapping: dict[str, str],
    report: ImportReport,
    conventions: dict[str, ColumnNumbers] | None = None,
) -> None:
    """Phase 0 format: one row per PO with total_value and an optional container. One line per PO."""
    pct_seen = False
    for idx, raw in enumerate(table.rows, start=table.header_row + 1):
        r = _Row(idx, raw, mapping, report, conventions)
        po_number = r.required("po_number")
        total = r.decimal("total_value") or Decimal("0")
        container_number = r.container()
        pct = r.decimal("allocation_percentage")
        if pct is not None and not pct_seen:
            pct_seen = True
            r.warn(
                "allocation_percentage",
                "DEPRECATED_COLUMN",
                "allocation_percentage is ignored; costs are allocated by the organization's default method",
                column=mapping.get("allocation_percentage", ""),
            )
        if r.failed or po_number is None:
            continue
        supplier = _get_or_create_supplier(t, r.text("supplier_name"), report)
        po = t.db.scalar(t.q(PurchaseOrder).where(PurchaseOrder.po_number == po_number))
        if po is None:
            po = t.add(
                PurchaseOrder(
                    po_number=po_number,
                    supplier_id=supplier.id if supplier else None,
                    currency=t.org.base_currency,
                )
            )
            t.db.flush()
            report.purchase_orders_created += 1
        line = t.db.scalar(
            select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id, PurchaseOrderLine.line_no == 1)
        )
        if line is None:
            line = t.add(PurchaseOrderLine(po_id=po.id, line_no=1, quantity=Decimal("1"), unit_price=total))
            t.db.flush()
            report.lines_created += 1
        elif line.unit_price != total:
            line.unit_price = total
            report.lines_updated += 1
        if container_number:
            container = _get_or_create_container(t, container_number, report)
            _upsert_load(t, container, line, Decimal("1"), r)
            if r.failed:
                continue
        report.valid_rows += 1


def preview_key(job: ImportJob) -> str | None:
    """The preview a job holds — its mapping, its options, its figures — as one key: what a person
    approved, which the commit names. Two previews that differ in anything a person saw differ in it."""
    if job.status is not ImportStatus.VALIDATED:
        return None
    seen = {"mapping": job.mapping, "options": job.options or {}, "report": job.report}
    return hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()[:32]


def find_duplicate(t: TenantSession, sha256: str, kind: ImportKind) -> ImportJob | None:
    """The same file of this kind, committed and not taken back."""
    return t.db.scalar(
        t.q(ImportJob).where(
            ImportJob.sha256 == sha256,
            ImportJob.kind == kind,
            ImportJob.status == ImportStatus.DONE,
            ImportJob.undone_at.is_(None),
        )
    )


def ensure_not_done(job: ImportJob) -> None:
    if job.status is ImportStatus.DONE:
        raise Conflict("This import has already been committed", code="IMPORT_ALREADY_DONE")
