"""Reading a containers' tracking sheet: when each box arrived, under which bill, carrying which orders.

The orders say what was bought; this sheet says when it landed. Without it a quarter's boxes have no
date and no period counts them — the audit of that quarter is empty, and says nothing wrong. One row
per container, or per container and order: the same box on several rows says the same thing on each,
or the file contradicts itself and the box is left out, by name.

What is read:
- the box: its number (ISO 6346, check digit and all), its size-type (ISO, from « 40HC » or
  « 1x40HQ »), its line (SCAC), and the dates that happened to it — arrival, discharge, gate out,
  empty return — kept as the day they say, at noon UTC, so that no time zone moves one into the next
  month;
- its bill of lading: the reference, the ports (UN/LOCODE, from « Ningbo » or « CN NGB »), the
  departure and the expected arrival — which is where a box without an arrival of its own takes its
  period from;
- the orders it carried: an order that travelled in this box alone and is loaded nowhere yet is
  loaded whole; an order spread over several boxes is left to a person, who alone knows how much went
  where (PO_SPLIT).

A date either happened or is expected. Only one that happened moves a box along — never an ETA,
never a day still to come — and one in the future is a typing error. A box a tracking provider
follows keeps the provider's dates.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from itertools import pairwise
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.core.tenancy import TenantSession
from app.domain.boxes import is_container_number, iso_size_type, scac_of
from app.domain.imports.parsing import ColumnDates, ImportError_, Table, infer_date_orders, parse_date
from app.domain.imports.service import ImportReport, RowIssue, _Row
from app.domain.models import (
    Container,
    ContainerLoad,
    ContainerMilestone,
    CostScope,
    Invoice,
    InvoiceStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
    Shipment,
    TrackingState,
)
from app.domain.ports import unlocode
from app.domain.reporting.service import arrival_date

DATE_FIELDS = ("etd", "eta", "ata", "discharged_at", "gate_out_at", "empty_returned_at")
#: Dates that happened, in the order a box goes through them: none can be in the future, each comes
#: after the one before, and each says where the box got to.
HAPPENED = ("ata", "discharged_at", "gate_out_at", "empty_returned_at")
MILESTONE_OF = {
    "ata": ContainerMilestone.VESSEL_ARRIVED,
    "discharged_at": ContainerMilestone.DISCHARGED,
    "gate_out_at": ContainerMilestone.GATE_OUT_FULL,
    "empty_returned_at": ContainerMilestone.GATE_IN_EMPTY_RETURN,
}
_MILESTONES = list(ContainerMilestone)
#: A provider feeds these boxes' dates: a spreadsheet does not overwrite them.
TRACKED = (TrackingState.PENDING, TrackingState.ACTIVE)
#: « PO-1, PO-2 », « PO-1 / PO-2 », « PO-1+PO-2 »: several orders in one cell.
_ORDERS_SEPARATOR = re.compile(r"\s*[,;/+|]\s*|\s{2,}")
MAX_REFERENCE = 64
NOON = time(12, tzinfo=UTC)


@dataclass
class _Box:
    """What the file says about one container, over all its rows."""

    number: str
    rows: list[int] = field(default_factory=list)
    values: dict[str, object] = field(default_factory=dict)
    orders: set[str] = field(default_factory=set)
    refused: bool = False


def normalize_reference(raw: str) -> str:
    """A bill of lading as it compares: « medu 2604417 » and « MEDU2604417 » are one bill."""
    return re.sub(r"\s+", "", raw).upper()


def apply_containers(t: TenantSession, table: Table, mapping: dict[str, str], report: ImportReport) -> None:
    orders = infer_date_orders(table, {mapping[f] for f in DATE_FIELDS if mapping.get(f)})
    decided = {read.order for read in orders.values() if not read.ambiguous}
    if len(decided) == 1:
        # One system wrote the sheet, and wrote every date the same way: a « 15/03/2026 » in one column
        # settles the columns whose dates never pass the twelfth.
        (order,) = decided
        orders = {
            column: ColumnDates(order, example=read.example) if read.ambiguous else read
            for column, read in orders.items()
        }
    _say_assumed_orders(table, mapping, orders, report)
    today = datetime.now(UTC).date()
    boxes: dict[str, _Box] = {}
    #: Orders named on rows that place no box: they travelled in a box this file does not load, so no
    #: other box of the file holds them whole.
    elsewhere: set[str] = set()
    for idx, raw in enumerate(table.rows, start=table.header_row + 1):
        r = _Row(idx, raw, mapping, report)
        read = _read_row(r, orders, today)
        if read is None:
            elsewhere |= _orders_named(r)
            continue
        number, values, row_orders = read
        box = boxes.setdefault(number, _Box(number))
        box.rows.append(idx)
        box.orders |= row_orders
        for name, value in values.items():
            known = box.values.get(name)
            if known is not None and known != value:
                r.error(
                    name,
                    "CONFLICT_IN_FILE",
                    f"{number}: {name} is {known} on another row and {value} on this one",
                    container_number=number,
                    first=str(known),
                    second=str(value),
                )
                box.refused = True
            else:
                box.values[name] = value
    _refuse_contradicting_bills(boxes, report)

    counters = {
        "containers_created": 0,
        "containers_updated": 0,
        "containers_dated": 0,
        "shipments_created": 0,
        "loads_created": 0,
        "po_split": 0,
        "invoices_matched": 0,
    }
    placed: dict[str, Container] = {}
    for box in boxes.values():
        if box.refused:
            continue
        placed[box.number] = _write_box(t, box, counters, report)
        report.valid_rows += len(box.rows)
    _load_orders(t, boxes, placed, elsewhere, counters, report)
    counters["invoices_matched"] = _match_waiting_invoices(t, placed)
    report.extra |= counters


def _say_assumed_orders(
    table: Table, mapping: dict[str, str], orders: dict[str, ColumnDates], report: ImportReport
) -> None:
    for name in DATE_FIELDS:
        column = mapping.get(name)
        read = orders.get(column) if column else None
        if read is not None and read.ambiguous:
            report.warnings.append(
                RowIssue(
                    table.header_row,
                    name,
                    "DATE_ORDER_ASSUMED",
                    f"In column {column!r} every date could be read either way; the day was read first "
                    f"(e.g. {read.example!r})",
                    {"column": column, "example": read.example or "", "order": read.order},
                )
            )


def _read_row(
    r: _Row, orders: dict[str, ColumnDates], today: date
) -> tuple[str, dict[str, object], set[str]] | None:
    """The box a row is about and what it says of it, or None when the row cannot be used."""
    raw_number = r.text("container_number")
    if raw_number is None:
        r.error("container_number", "REQUIRED", "container_number is empty")
        return None
    number = re.sub(r"[\s\-]", "", raw_number).upper()
    if not re.fullmatch(r"[A-Z]{4}\d{7}", number):
        r.error(
            "container_number",
            "INVALID_CONTAINER_NUMBER",
            f"{raw_number!r} is not an ISO 6346 number",
            value=raw_number,
        )
        return None
    if not is_container_number(number):
        # Eleven characters of the right shape with the wrong check digit are a typing error: taken as
        # they are, they would make a second box nobody ships anything in.
        r.error(
            "container_number",
            "CONTAINER_CHECK_DIGIT",
            f"{number}: the check digit is wrong",
            value=raw_number,
        )
        return None

    values: dict[str, object] = {}
    if (size := r.text("iso_type")) is not None:
        if (iso := iso_size_type(size)) is None:
            r.warn("iso_type", "ISO_TYPE_UNKNOWN", f"{size!r} is not a size-type we can read", value=size)
        else:
            values["iso_type"] = iso
    if (reference := r.text("shipment_reference")) is not None:
        if len(reference) > MAX_REFERENCE:
            r.error(
                "shipment_reference",
                "FIELD_TOO_LONG",
                "the bill of lading reference is too long",
                value=reference[:MAX_REFERENCE],
            )
        else:
            values["shipment_reference"] = normalize_reference(reference)
    if (line := r.text("carrier")) is not None:
        if (scac := scac_of(line)) is None:
            r.warn("carrier", "CARRIER_UNKNOWN", f"{line!r} is not a shipping line we can name", value=line)
        else:
            values["carrier"] = scac
    for name in ("origin_port", "destination_port"):
        if (port := r.text(name)) is not None:
            if (code := unlocode(port)) is None:
                r.warn(name, "PORT_UNKNOWN", f"{port!r} is not a port we can name", value=port)
            else:
                values[name] = code
    for name in DATE_FIELDS:
        raw_date = r.text(name)
        if raw_date is None:
            continue
        column = r.mapping.get(name) or ""
        try:
            day = parse_date(raw_date, order=orders[column].order if column in orders else "dmy")
        except ImportError_ as e:
            r.error(name, e.code, e.message, value=raw_date)
            continue
        if day is None:
            continue
        if name in HAPPENED and day > today:
            r.error(name, "DATE_IN_FUTURE", f"{name} {day.isoformat()} has not happened yet", value=raw_date)
            continue
        values[name] = day
    _check_order(r, values)

    if r.failed:
        return None
    return number, values, _orders_named(r)


def _orders_named(r: _Row) -> set[str]:
    cell = r.text("po_numbers")
    return {part.strip().upper() for part in _ORDERS_SEPARATOR.split(cell or "") if part.strip()}


def _check_order(r: _Row, values: dict[str, object]) -> None:
    """A departure after the arrival it leads to, a box out before it was unloaded: one of the two
    dates is wrong, and the file does not say which."""
    present = [name for name in HAPPENED if name in values]
    for first, second in [("etd", "eta"), *pairwise(present)]:
        a, b = values.get(first), values.get(second)
        if isinstance(a, date) and isinstance(b, date) and b < a:
            r.error(
                second,
                "DATES_OUT_OF_ORDER",
                f"{second} {b.isoformat()} comes before {first} {a.isoformat()}",
                first=first,
                second=second,
            )


def _refuse_contradicting_bills(boxes: dict[str, _Box], report: ImportReport) -> None:
    """One bill of lading, one departure, one expected arrival, one pair of ports: two boxes saying
    otherwise leave the file contradicting itself, and every box of that bill out."""
    boxes_of: dict[str, list[_Box]] = defaultdict(list)
    for box in boxes.values():
        reference = box.values.get("shipment_reference")
        if not isinstance(reference, str) or box.refused:
            continue
        boxes_of[reference].append(box)
    for reference, members in boxes_of.items():
        for name in ("etd", "eta", "origin_port", "destination_port", "carrier"):
            seen = {box.values[name] for box in members if name in box.values}
            if len(seen) > 1:
                for box in members:
                    box.refused = True
                report.errors.append(
                    RowIssue(
                        members[0].rows[0],
                        name,
                        "CONFLICT_IN_FILE",
                        f"bill {reference}: {name} differs between its containers",
                        {"shipment_reference": reference, "values": ", ".join(sorted(map(str, seen)))},
                    )
                )
                break


def _at_noon(day: date) -> datetime:
    return datetime.combine(day, NOON)


def _write_box(t: TenantSession, box: _Box, counters: dict[str, int], report: ImportReport) -> Container:
    first_row = box.rows[0]
    container = t.db.scalar(
        t.q(Container).where(Container.container_number == box.number, Container.archived_at.is_(None))
    )
    created = container is None
    if container is None:
        container = t.add(Container(container_number=box.number))
        t.db.flush()
        counters["containers_created"] += 1
    was_dated = arrival_date(container) is not None
    changed = False

    reference = box.values.get("shipment_reference")
    if isinstance(reference, str):
        shipment = t.db.scalar(
            t.q(Shipment).where(func.upper(func.replace(Shipment.reference, " ", "")) == reference)
        )
        if shipment is None:
            shipment = t.add(Shipment(reference=reference))
            t.db.flush()
            counters["shipments_created"] += 1
        for name, column in (
            ("etd", "etd"),
            ("eta", "eta"),
            ("origin_port", "origin_unlocode"),
            ("destination_port", "destination_unlocode"),
            ("carrier", "carrier_scac"),
        ):
            value = box.values.get(name)
            if value is not None and getattr(shipment, column) != value:
                setattr(shipment, column, value)
        if container.shipment_id is not None and container.shipment_id != shipment.id:
            report.warnings.append(
                RowIssue(
                    first_row,
                    "shipment_reference",
                    "SHIPMENT_CHANGED",
                    f"{box.number} moves to bill {reference}",
                    {"container_number": box.number, "shipment_reference": reference},
                )
            )
        if container.shipment_id != shipment.id:
            container.shipment_id, changed = shipment.id, True
            container.shipment = shipment

    for name, column in (("iso_type", "iso_type"), ("carrier", "carrier_scac")):
        value = box.values.get(name)
        if value is not None and getattr(container, column) != value:
            setattr(container, column, value)
            changed = True

    if container.tracking_state in TRACKED:
        if any(name in box.values for name in (*HAPPENED, "eta")):
            report.warnings.append(
                RowIssue(
                    first_row,
                    "",
                    "TRACKED_CONTAINER",
                    f"{box.number} is followed by a tracking provider; its dates are the provider's",
                    {"container_number": box.number},
                )
            )
    else:
        eta = box.values.get("eta")
        if isinstance(eta, date) and container.eta != _at_noon(eta):
            container.eta, changed = _at_noon(eta), True
        for name in HAPPENED:
            day = box.values.get(name)
            if isinstance(day, date) and getattr(container, name) != _at_noon(day):
                setattr(container, name, _at_noon(day))
                changed = True
                if _MILESTONES.index(MILESTONE_OF[name]) > _MILESTONES.index(container.milestone):
                    container.milestone = MILESTONE_OF[name]
    t.db.flush()
    if changed and not created:
        counters["containers_updated"] += 1
    if not was_dated and arrival_date(container) is not None:
        counters["containers_dated"] += 1
    _say_open_clocks(container, first_row, report)
    return container


def _say_open_clocks(container: Container, row: int, report: ImportReport) -> None:
    """What the file leaves open, before anyone counts days on it."""
    if container.discharged_at is not None and container.gate_out_at is None:
        report.warnings.append(
            RowIssue(
                row,
                "gate_out_at",
                "CLOCK_LEFT_RUNNING",
                f"{container.container_number} was discharged and never left the terminal: its "
                f"demurrage runs until a date says otherwise",
                {"container_number": container.container_number},
            )
        )
    if container.ata is not None and container.discharged_at is None:
        report.warnings.append(
            RowIssue(
                row,
                "discharged_at",
                "NO_DISCHARGE_DATE",
                f"{container.container_number} arrived without a discharge date: its free days "
                f"cannot be counted",
                {"container_number": container.container_number},
            )
        )
    if arrival_date(container) is None:
        report.warnings.append(
            RowIssue(
                row,
                "",
                "ARRIVAL_UNKNOWN",
                f"{container.container_number} has no arrival and no expected arrival: no period counts it",
                {"container_number": container.container_number},
            )
        )


def _load_orders(
    t: TenantSession,
    boxes: dict[str, _Box],
    placed: dict[str, Container],
    elsewhere: set[str],
    counters: dict[str, int],
    report: ImportReport,
) -> None:
    """An order that travelled in one box of the file, and is loaded nowhere yet, is loaded whole in
    it. Anything else is a quantity per box that only a person knows — an order also named on a row
    refused, or on a box the file contradicts, travelled in another box too."""
    named: dict[str, list[_Box]] = defaultdict(list)
    for box in boxes.values():
        for number in box.orders:
            named[number].append(box)
    named = {number: carriers for number, carriers in named.items()
             if any(box.number in placed for box in carriers)}  # fmt: skip
    if not named:
        return
    orders = {
        order.po_number.upper(): order
        for order in t.db.scalars(
            t.q(PurchaseOrder).where(
                func.upper(PurchaseOrder.po_number).in_(list(named)),
                PurchaseOrder.status != PurchaseOrderStatus.CANCELLED,
            )
        )
    }
    for number, carriers in sorted(named.items()):
        order = orders.get(number)
        if order is None:
            report.warnings.append(
                RowIssue(
                    carriers[0].rows[0],
                    "po_numbers",
                    "PO_UNKNOWN",
                    f"order {number} is not known: import the orders before the tracking",
                    {"po_number": number},
                )
            )
            continue
        lines = list(t.db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == order.id)))
        loaded_on: dict[UUID, set[UUID]] = defaultdict(set)
        if lines:
            for load in t.db.scalars(
                select(ContainerLoad).where(ContainerLoad.po_line_id.in_([line.id for line in lines]))
            ):
                loaded_on[load.container_id].add(load.po_line_id)
        targets = {placed[box.number].id for box in carriers if box.number in placed}
        if len(carriers) == 1 and number not in elsewhere and not loaded_on:
            (container_id,) = targets
            for line in lines:
                t.add(ContainerLoad(container_id=container_id, po_line_id=line.id, quantity=line.quantity))
                counters["loads_created"] += 1
            t.db.flush()
        elif not targets <= set(loaded_on):
            counters["po_split"] += 1
            report.warnings.append(
                RowIssue(
                    carriers[0].rows[0],
                    "po_numbers",
                    "PO_SPLIT",
                    f"order {number} is in several containers, or loaded elsewhere already: say how "
                    f"much of it went in each",
                    {
                        "po_number": number,
                        "container_numbers": ", ".join(sorted(box.number for box in carriers)),
                    },
                )
            )


UNKNOWN_CONTAINER = "@unknown_container|"


def _match_waiting_invoices(t: TenantSession, placed: dict[str, Container]) -> int:
    """Invoices being reviewed that named a box nobody had yet point at it now — what their reading
    would have done had the box existed: a line that named its own box goes to it, and the lines of an
    invoice that names one box alone go to that one, saying so as the reading says it."""
    if not placed:
        return 0
    matched: set[UUID] = set()
    waiting = t.db.scalars(
        select(Invoice)
        .where(Invoice.org_id == t.org_id, Invoice.status == InvoiceStatus.NEEDS_REVIEW)
        .options(selectinload(Invoice.lines))
    )
    for invoice in waiting:
        reading = invoice.raw.get("reading") if isinstance(invoice.raw, dict) else None
        named = {str(n).upper() for n in (reading or {}).get("container_numbers") or []}
        single = next(iter(named)) if len(named) == 1 else None
        for line in invoice.lines:
            if line.target_id is not None:
                continue
            notes = [note.strip() for note in (line.notes or "").split(";") if note.strip()]
            own = next(
                (n.removeprefix(UNKNOWN_CONTAINER) for n in notes if n.startswith(UNKNOWN_CONTAINER)), None
            )
            number = own or single
            if number is None or number not in placed:
                continue
            line.scope, line.target_id = CostScope.CONTAINER, placed[number].id
            kept = [n for n in notes if not n.startswith(UNKNOWN_CONTAINER)]
            line.notes = "; ".join(kept if own else [*kept, f"@single_container|{number}"]) or None
            matched.add(invoice.id)
    t.db.flush()
    return len(matched)
