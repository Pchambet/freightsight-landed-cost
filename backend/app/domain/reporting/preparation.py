"""Before an audit: what would make it wrong, and what it will not be able to say.

A prospect's quarter comes in as files — orders, the containers' tracking, the costs ledger, the
tariff — and each one missing or half-read changes the audit without a word: a box with no date is in
no quarter, a box bought FOB with no freight has its margin overstated by the whole freight, a duty
spread on customs value charges duty to the article at 0 %. Each check counts what it finds and names
examples by raw ids; the sentence and the remedy are the screen's.

Two severities. `blocking`: the audit would state a wrong figure. `limits`: the audit is right but
cannot say something — a margin with no sale price, a charge it has nothing to compare with.

The same rules as the audit, read from the same computation: a box is in the period by
`arrival_date`, and its costs are those whose allocation lands on it or that are attached to it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.money import q2
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.entry import DUTY_GUARD, normalize_invoice_number, normalize_vendor
from app.domain.costing.service import Computation
from app.domain.models import (
    AllocationMethod,
    Container,
    Cost,
    CostStatus,
    CostType,
    ImportJob,
    ImportKind,
    ImportStatus,
    Incoterm,
    Invoice,
    InvoiceStatus,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseOrderStatus,
)
from app.domain.reporting.audit import (
    FREIGHT,
    _assumed,
    _costs_of,
    _double_entries,
    _number,
    _period_containers,
    _size,
    check_period,
    documents_of,
)
from app.domain.reporting.service import UNKNOWN, _route, arrival_date
from app.domain.reporting.skus import sku_report

Severity = Literal["blocking", "limits"]
Section = Literal["period", "findings", "margins", "demurrage"]
ExampleKind = Literal["container", "invoice", "purchase_order", "sku", "import"]
Code = Literal[
    "CONTAINERS_WITHOUT_DATE",
    "CONTAINERS_WITHOUT_GOODS",
    "CONTAINERS_WITHOUT_COST",
    "CONTAINERS_WITHOUT_FREIGHT",
    "CONTAINERS_WITHOUT_DUTY",
    "INVOICES_NOT_READ",
    "INVOICES_TO_REVIEW",
    "COST_ROWS_REFUSED",
    "COSTS_UNALLOCATED",
    "SAME_INVOICE_TWICE",
    "CREDIT_NOTE_REVERSES_COST",
    "CREDIT_NOTES_SKIPPED",
    "PO_SPLIT",
    "LINES_WITHOUT_DUTY_RATE",
    "DUTY_GAP",
    "DUTY_SPREAD_ON_CIF",
    "CONTAINERS_WITHOUT_ROUTE",
    "CONTAINERS_WITHOUT_SIZE",
    "COSTS_NOT_ON_CONTAINER",
    "COSTS_TYPED_OTHER",
    "CLOCKS_RUNNING",
    "NO_SALE_PRICE",
    "NO_ASSUMED_COEFFICIENT",
    "ESTIMATES_OPEN",
]

#: What each check is, and the order the screen shows them in: what makes the audit wrong first, in
#: the order a person puts it right (dates, goods, costs, invoices), then what it will not say.
CHECKS: dict[Code, tuple[Severity, Section]] = {
    "CONTAINERS_WITHOUT_DATE": ("blocking", "period"),
    "CONTAINERS_WITHOUT_GOODS": ("blocking", "margins"),
    "CONTAINERS_WITHOUT_COST": ("blocking", "margins"),
    "CONTAINERS_WITHOUT_FREIGHT": ("blocking", "margins"),
    "CONTAINERS_WITHOUT_DUTY": ("blocking", "margins"),
    "INVOICES_NOT_READ": ("blocking", "findings"),
    "INVOICES_TO_REVIEW": ("blocking", "findings"),
    "COST_ROWS_REFUSED": ("blocking", "margins"),
    "COSTS_UNALLOCATED": ("blocking", "margins"),
    "SAME_INVOICE_TWICE": ("blocking", "margins"),
    "CREDIT_NOTE_REVERSES_COST": ("blocking", "margins"),
    "CREDIT_NOTES_SKIPPED": ("limits", "findings"),
    "PO_SPLIT": ("limits", "margins"),
    "LINES_WITHOUT_DUTY_RATE": ("limits", "margins"),
    "DUTY_GAP": ("limits", "margins"),
    "DUTY_SPREAD_ON_CIF": ("limits", "margins"),
    "CONTAINERS_WITHOUT_ROUTE": ("limits", "findings"),
    "CONTAINERS_WITHOUT_SIZE": ("limits", "findings"),
    "COSTS_NOT_ON_CONTAINER": ("limits", "findings"),
    "COSTS_TYPED_OTHER": ("limits", "findings"),
    "CLOCKS_RUNNING": ("limits", "demurrage"),
    "NO_SALE_PRICE": ("limits", "margins"),
    "NO_ASSUMED_COEFFICIENT": ("limits", "margins"),
    "ESTIMATES_OPEN": ("limits", "margins"),
}

#: Enough for a person to see what is meant and click through; `count` says how many there are.
MAX_EXAMPLES = 5

#: The incoterms under which the main carriage is the buyer's to pay — and so a cost of the box. A
#: box whose order names no incoterm is counted with them: the freight is either missing or the
#: seller's, and only a person can say which.
BUYER_PAYS_FREIGHT = frozenset({Incoterm.EXW, Incoterm.FCA, Incoterm.FOB})

#: Invoices whose content the product does not know yet: dropped, being read, or unreadable.
NOT_READ = (InvoiceStatus.UPLOADED, InvoiceStatus.EXTRACTING, InvoiceStatus.FAILED)


@dataclass
class Example:
    kind: ExampleKind
    #: A uuid — or, for a `sku`, the article code itself: what the screen's link needs.
    id: str
    label: str


@dataclass
class Item:
    code: Code
    severity: Severity
    section: Section
    count: int
    #: The money concerned, in the organization's currency, where money is what is at stake.
    amount_base: Decimal | None = None
    params: dict[str, str] = field(default_factory=dict)
    examples: list[Example] = field(default_factory=list)


@dataclass
class Preparation:
    base_currency: str
    period_from: date
    period_to: date
    as_of: date
    #: Only what was found, blocking first, in the order of `CHECKS`.
    items: list[Item]


def audit_preparation(
    db: Session,
    comp: Computation,
    org: Organization,
    period_from: date,
    period_to: date,
    *,
    today: date | None = None,
) -> Preparation:
    check_period(period_from, period_to)
    period = _Period(db, comp, org, period_from, period_to)
    found: list[Item] = [
        *period.without_date(),
        *period.without_goods(),
        *period.without_cost(),
        *period.without_freight(),
        *period.without_duty(),
        *period.invoices("INVOICES_NOT_READ", NOT_READ),
        *period.invoices("INVOICES_TO_REVIEW", (InvoiceStatus.NEEDS_REVIEW,)),
        *period.refused_rows(),
        *period.unallocated(),
        *period.double_entries(),
        *period.credit_notes(),
        *period.po_split(),
        *period.lines_without_rate(),
        *period.duty_gap(),
        *period.duty_on_cif(),
        *period.without_route_or_size(),
        *period.costs_not_on_a_box(),
        *period.typed_other(),
        *period.clocks_running(),
        *period.no_sale_price(),
        *period.no_assumed_coefficient(),
        *period.estimates_open(),
    ]
    order = list(CHECKS)
    found.sort(key=lambda item: order.index(item.code))
    return Preparation(
        base_currency=org.base_currency,
        period_from=period_from,
        period_to=period_to,
        as_of=today or datetime.now(UTC).date(),
        items=found,
    )


def _item(
    code: Code,
    count: int,
    examples: Iterable[Example] = (),
    *,
    amount_base: Decimal | None = None,
    params: dict[str, str] | None = None,
) -> list[Item]:
    """The item a check found, or nothing when it found nothing: the screen lists only what is there."""
    if count <= 0:
        return []
    severity, section = CHECKS[code]
    return [
        Item(
            code=code,
            severity=severity,
            section=section,
            count=count,
            amount_base=q2(amount_base) if amount_base is not None else None,
            params=params or {},
            examples=list(examples)[:MAX_EXAMPLES],
        )
    ]


def _box(container: Container) -> Example:
    return Example("container", str(container.id), container.container_number)


def _row_key(row: dict[str, Any]) -> tuple[object, ...]:
    """A row of a ledger as two copies of the ledger both hold it."""
    return (
        normalize_vendor(row.get("vendor")),
        normalize_invoice_number(row.get("invoice_number")),
        row.get("cost_date"),
        row.get("amount"),
        row.get("currency"),
    )


def _is_held(row: dict[str, Any], held: dict[str, set[tuple[object, ...]]]) -> bool:
    """A refused row some cost of the books now holds — typed by hand, read again from a corrected file
    — by the same forwarder (or one of the two unnamed), for the same amount."""
    if row.get("amount") is None:
        return False
    amount, currency, vendor = (
        Decimal(row["amount"]),
        row.get("currency"),
        normalize_vendor(row.get("vendor")),
    )
    if number := normalize_invoice_number(row.get("invoice_number")):
        return any(
            a == amount and c == currency and (not vendor or not v or v == vendor)
            for v, a, c in held.get(number, set())
        )
    return any(
        d == row.get("cost_date") and a == amount and c == currency and (not vendor or not v or v == vendor)
        for d, v, a, c in held.get("", set())
    )


class _Period:
    """One computation read once for every check; each method is one check."""

    def __init__(
        self, db: Session, comp: Computation, org: Organization, period_from: date, period_to: date
    ) -> None:
        self.db, self.comp, self.org = db, comp, org
        self.period_from, self.period_to = period_from, period_to
        self.every = {row.container_id: row.container for row in comp.load_rows.values()}
        self.boxes = _period_containers(comp, period_from, period_to)
        # The costs of a landed cost: import VAT is recovered, and never money the audit gets wrong. It
        # is left out of every check and every amount here, as it is out of every landed cost.
        self.landed = {
            cost_id: cost
            for cost_id, cost in comp.cost_rows.items()
            if cost.cost_type.value not in EXCLUDED_FROM_LANDED
        }
        self.costs_in = {
            cost_id: cost for cost_id, cost in _costs_of(comp, self.boxes).items() if cost_id in self.landed
        }
        # What lands on each box of the period, by allocation or by being attached to it.
        self.on_box: dict[UUID, set[UUID]] = defaultdict(set)
        self.in_period: dict[UUID, Decimal] = defaultdict(Decimal)
        for allocation in comp.result.allocations:
            box = comp.load_rows[allocation.load_id].container_id
            if box in self.boxes and allocation.cost_id in self.landed:
                self.on_box[box].add(allocation.cost_id)
                self.in_period[allocation.cost_id] += allocation.amount_base
        for cost in self.landed.values():
            if cost.container_id in self.boxes:
                self.on_box[cost.container_id].add(cost.id)
        self.loads_of: dict[UUID, list[UUID]] = defaultdict(list)
        for load in comp.loads:
            if load.container_id in self.boxes:
                self.loads_of[load.container_id].append(load.id)
        self._unloaded: list[Container] | None = None
        self._empty: dict[UUID, Container] | None = None
        self._ledgers: list[ImportJob] | None = None

    # -------------------------------------------------------------------------------- helpers

    def _types_on(self, box: UUID) -> set[CostType]:
        return {self.comp.cost_rows[cost_id].cost_type for cost_id in self.on_box.get(box, ())}

    def _money(self, costs: Iterable[Cost]) -> Decimal:
        """What these costs put on the period: the part of their allocation that lands on its boxes,
        and the whole of those the engine could not place."""
        return sum(
            (self.in_period.get(cost.id, cost.amount_base) for cost in costs),
            Decimal(0),
        )

    def _order_of(self, cost: Cost) -> Example | None:
        po_id = cost.po_id
        if po_id is None and cost.po_line_id is not None:
            line = self.comp.lines.get(cost.po_line_id)
            po_id = line.po_id if line is not None else None
        if po_id is None or po_id not in self.comp.pos:
            return None
        return Example("purchase_order", str(po_id), self.comp.pos[po_id].po_number)

    def _where(self, cost: Cost) -> Example | None:
        """Where a person goes to see a cost: its box, else its order. A cost has no page of its own."""
        container_id, number = _number(self.comp, self.every, cost)
        if container_id is not None and number is not None:
            return Example("container", str(container_id), number)
        return self._order_of(cost)

    def _where_even_empty(self, cost: Cost) -> Example | None:
        """Where a cost is, a box of the period with nothing loaded yet included."""
        if (box := self._arrived_empty().get(cost.container_id)) is not None:  # type: ignore[arg-type]
            return _box(box)
        return self._where(cost)

    def _recorded_on(self, cost: Cost) -> Example | None:
        """Where a cost is recorded, for the costs whose fault is where they are recorded: its order,
        or else the box its bill of lading carried."""
        return self._order_of(cost) or self._where(cost)

    def _places(
        self, costs: Iterable[Cost], where: Callable[[Cost], Example | None] | None = None
    ) -> list[Example]:
        seen: dict[str, Example] = {}
        for cost in costs:
            place = (where or self._where)(cost)
            if place is not None:
                seen.setdefault(place.id, place)
        return list(seen.values())

    def _arrived_empty(self) -> dict[UUID, Container]:
        """The boxes that arrived in the period with nothing loaded, by id, in number order, read once."""
        if self._empty is None:
            self._empty = {
                c.id: c
                for c in self._unloaded_containers()
                if (landed := arrival_date(c)) is not None and self.period_from <= landed <= self.period_to
            }
        return self._empty

    def _unloaded_containers(self) -> list[Container]:
        """The organization's boxes with no goods on them, read once."""
        if self._unloaded is None:
            self._unloaded = list(
                self.db.scalars(
                    select(Container)
                    .where(
                        Container.org_id == self.org.id,
                        Container.archived_at.is_(None),
                        Container.id.not_in(list(self.comp.containers)),
                    )
                    .options(selectinload(Container.shipment))
                    .order_by(Container.container_number)
                )
            )
        return self._unloaded

    # -------------------------------------------------------------------------------- blocking

    def without_date(self) -> list[Item]:
        """Boxes that carry goods or costs and have no date at all: no period counts them, whichever
        one is audited. The same count as the audit's own `completeness.containers_without_date`."""
        charged = {cost.container_id for cost in self.landed.values() if cost.container_id}
        undated = [c for c in self.comp.containers.values() if arrival_date(c) is None]
        undated += [c for c in self._unloaded_containers() if c.id in charged and arrival_date(c) is None]
        ids = {c.id for c in undated}
        money = sum(
            (
                a.amount_base
                for a in self.comp.result.allocations
                if self.comp.load_rows[a.load_id].container_id in ids and a.cost_id in self.landed
            ),
            Decimal(0),
        )
        allocated = {a.cost_id for a in self.comp.result.allocations}
        money += sum(
            (c.amount_base for c in self.landed.values() if c.container_id in ids and c.id not in allocated),
            Decimal(0),
        )
        undated.sort(key=lambda c: c.container_number)
        return _item("CONTAINERS_WITHOUT_DATE", len(undated), map(_box, undated), amount_base=money)

    def without_goods(self) -> list[Item]:
        """Boxes that arrived in the period with nothing loaded: their costs are spread on nothing, and
        the orders they carried are in no landed cost."""
        empty = self._arrived_empty()
        money = sum((c.amount_base for c in self.landed.values() if c.container_id in empty), Decimal(0))
        return _item("CONTAINERS_WITHOUT_GOODS", len(empty), map(_box, empty.values()), amount_base=money)

    def without_cost(self) -> list[Item]:
        bare = [c for c in self._sorted_boxes() if not self.on_box.get(c.id)]
        return _item("CONTAINERS_WITHOUT_COST", len(bare), map(_box, bare))

    def without_freight(self) -> list[Item]:
        """Boxes whose freight is the buyer's to pay — or whose orders do not say — and that carry no
        freight at all, estimated or invoiced: their landed cost is short of the whole carriage. A box
        with no cost at all is said once, by CONTAINERS_WITHOUT_COST."""
        freight = {CostType(kind) for kind in FREIGHT}
        missing = [
            box
            for box in self._sorted_boxes()
            if (types := self._types_on(box.id))
            and not types & freight
            and any(
                po.incoterm is None or po.incoterm in BUYER_PAYS_FREIGHT for po in self._orders_on(box.id)
            )
        ]
        return _item("CONTAINERS_WITHOUT_FREIGHT", len(missing), map(_box, missing))

    def without_duty(self) -> list[Item]:
        """Boxes with goods that pay duty and no duty on them, estimated or invoiced. A box with no cost
        at all is said once, by CONTAINERS_WITHOUT_COST."""
        missing = [
            box
            for box in self._sorted_boxes()
            if (types := self._types_on(box.id))
            and CostType.CUSTOMS_DUTY not in types
            and any((self.comp.lines[line_id].duty_rate or 0) > 0 for line_id in self._lines_on(box.id))
        ]
        return _item("CONTAINERS_WITHOUT_DUTY", len(missing), map(_box, missing))

    def invoices(self, code: Code, statuses: tuple[InvoiceStatus, ...]) -> list[Item]:
        """Invoices whose costs are not in the books yet. Counted over the organization: until an
        invoice is read, nothing says which period it belongs to."""
        found = list(
            self.db.scalars(
                select(Invoice)
                .where(Invoice.org_id == self.org.id, Invoice.status.in_(statuses))
                .options(selectinload(Invoice.document))
                .order_by(Invoice.created_at)
            )
        )
        examples = (
            Example(
                "invoice",
                str(invoice.id),
                " ".join(filter(None, (invoice.vendor, invoice.invoice_number)))
                or invoice.document.filename
                or "",
            )
            for invoice in found
        )
        return _item(code, len(found), examples)

    def refused_rows(self) -> list[Item]:
        """Rows of a costs file the import refused — no box, a label no type reads, no rate for the
        day — whose charge is therefore in no landed cost. Said until a cost holds each one (typed by
        hand, or read from a corrected file) or the file is taken back; the same row in two copies of
        the ledger is one row. In the period by its box's arrival when the row named a known box, by its
        own date otherwise, and in every period when it has neither: nothing says where it belongs."""
        held = self._held()
        rows = [(job, row) for job in self._costs_files() for row in job.refused_rows or []]
        named = {
            UUID(row["target_id"])
            for _, row in rows
            if row.get("scope") == "CONTAINER" and row.get("target_id")
        }
        boxes = dict(self.every)
        if missing := named - set(boxes):
            boxes |= {
                c.id: c
                for c in self.db.scalars(
                    select(Container).where(Container.org_id == self.org.id, Container.id.in_(missing))
                )
            }
        seen: set[tuple[object, ...]] = set()
        found: list[dict[str, Any]] = []
        examples: dict[str, Example] = {}
        for job, row in rows:
            key = _row_key(row)
            if key in seen or not self._row_in_period(row, boxes) or _is_held(row, held):
                continue
            seen.add(key)
            found.append(row)
            examples.setdefault(str(job.id), Example("import", str(job.id), job.original_filename or ""))
        return _item(
            "COST_ROWS_REFUSED",
            len(found),
            examples.values(),
            amount_base=sum(
                (abs(Decimal(row["amount_base"])) for row in found if row.get("amount_base")), Decimal(0)
            ),
        )

    def unallocated(self) -> list[Item]:
        """Costs the engine could not place, one item per reason: the remedy is not the same."""
        by_reason: dict[str, list[Cost]] = defaultdict(list)
        for missing in self.comp.result.unallocated:
            cost = self.costs_in.get(missing.cost_id)
            if cost is not None and cost not in by_reason[missing.code]:
                by_reason[missing.code].append(cost)
        items: list[Item] = []
        for reason in sorted(by_reason):
            costs = by_reason[reason]
            items += _item(
                "COSTS_UNALLOCATED",
                len(costs),
                self._places(costs),
                amount_base=sum((c.amount_base for c in costs), Decimal(0)),
                params={"reason": reason},
            )
        return items

    def double_entries(self) -> list[Item]:
        """One invoice recorded twice: the landed cost counts it twice until one copy goes."""
        pairs = _double_entries(self.comp, self.every, self.costs_in, documents_of(self.db, self.costs_in))
        examples = {
            str(f.container_id): Example("container", str(f.container_id), f.container_number)
            for f in pairs
            if f.container_id is not None and f.container_number is not None
        }
        return _item(
            "SAME_INVOICE_TWICE",
            len(pairs),
            examples.values(),
            amount_base=sum((f.amount for f in pairs), Decimal(0)),
        )

    # -------------------------------------------------------------------------------- limits

    def credit_notes(self) -> list[Item]:
        """Refunds costs files set aside — a cost cannot be negative — the same one in two copies of a
        ledger counted once. A refund that reverses a cost still standing on a box of the period —
        the file held the line and its reversal, the books the line — blocks: the audit would count a
        charge the forwarder took back (CREDIT_NOTE_REVERSES_COST). Any other dated in the period may
        refund a charge the audit would claim: said (CREDIT_NOTES_SKIPPED). Amounts by their size."""
        notes: dict[tuple[object, ...], tuple[dict[str, Any], ImportJob]] = {}
        for job in self._costs_files():
            for note in (job.report or {}).get("credit_notes_skipped") or []:
                key = _row_key(note)
                if key not in notes or (
                    note.get("reverses_cost_id") and not notes[key][0].get("reverses_cost_id")
                ):
                    notes[key] = (note, job)
        reversing: list[tuple[dict[str, Any], Cost]] = []
        plain: list[tuple[dict[str, Any], ImportJob]] = []
        for note, job in notes.values():
            if reversed_id := note.get("reverses_cost_id"):
                # Taken out or closed since: the refund is applied, and there is nothing left to say. On
                # a box of the period, goods loaded or not yet: the box's own item does not say this.
                cost = self.landed.get(UUID(reversed_id))
                if cost is not None and (
                    cost.id in self.costs_in or cost.container_id in self._arrived_empty()
                ):
                    reversing.append((note, cost))
            elif self.period_from <= date.fromisoformat(note["cost_date"]) <= self.period_to:
                plain.append((note, job))
        return [
            *_item(
                "CREDIT_NOTE_REVERSES_COST",
                len(reversing),
                self._places((cost for _, cost in reversing), self._where_even_empty),
                amount_base=sum((abs(Decimal(note["amount_base"])) for note, _ in reversing), Decimal(0)),
            ),
            *_item(
                "CREDIT_NOTES_SKIPPED",
                len(plain),
                {
                    str(job.id): Example("import", str(job.id), job.original_filename or "")
                    for _, job in plain
                }.values(),
                amount_base=sum((abs(Decimal(note["amount_base"])) for note, _ in plain), Decimal(0)),
            ),
        ]

    def po_split(self) -> list[Item]:
        """Orders of the period loaded only in part: the rest of the goods is in no box, or in a box a
        person still has to fill in."""
        pos = {self.comp.lines[line_id].po_id for box in self.boxes for line_id in self._lines_on(box)}
        if not pos:
            return []
        loaded: dict[UUID, Decimal] = defaultdict(Decimal)
        for load in self.comp.loads:
            loaded[self.comp.load_rows[load.id].po_line_id] += load.quantity
        partial: dict[UUID, PurchaseOrder] = {}
        rows = self.db.execute(
            select(PurchaseOrderLine, PurchaseOrder)
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
            .where(
                PurchaseOrderLine.org_id == self.org.id,
                PurchaseOrderLine.po_id.in_(pos),
                PurchaseOrder.status != PurchaseOrderStatus.CANCELLED,
            )
        )
        for line, order in rows.tuples():
            if loaded.get(line.id, Decimal(0)) < line.quantity:
                partial[order.id] = order
        ordered = sorted(partial.values(), key=lambda o: o.po_number)
        return _item(
            "PO_SPLIT", len(ordered), (Example("purchase_order", str(o.id), o.po_number) for o in ordered)
        )

    def lines_without_rate(self) -> list[Item]:
        """Lines with no duty rate in boxes that paid duty: the duty is spread on customs value there,
        whatever each article's own rate is."""
        lines = sorted(
            {
                line_id
                for box in self.boxes
                if CostType.CUSTOMS_DUTY in self._types_on(box)
                for line_id in self._lines_on(box)
                if self.comp.lines[line_id].duty_rate is None
            },
            key=lambda line_id: (self.comp.lines[line_id].sku or "", str(line_id)),
        )
        examples: dict[str, Example] = {}
        for line_id in lines:
            line = self.comp.lines[line_id]
            if line.sku:
                examples.setdefault(line.sku, Example("sku", line.sku, line.sku))
            elif line.po_id in self.comp.pos:
                order = self.comp.pos[line.po_id]
                examples.setdefault(str(order.id), Example("purchase_order", str(order.id), order.po_number))
        return _item("LINES_WITHOUT_DUTY_RATE", len(lines), examples.values())

    def duty_gap(self) -> list[Item]:
        """Boxes whose lines all have a rate, and whose invoiced duty the rates do not explain within
        the guard: a rate is wrong, or a duty is missing or counted twice."""
        gaps = []
        for box in self._sorted_boxes():
            load_ids = self.loads_of.get(box.id, [])
            rates = [self.comp.load_rows[load_id].po_line.duty_rate for load_id in load_ids]
            if (
                not load_ids
                or any(rate is None for rate in rates)
                or not any(rate and rate > 0 for rate in rates)
            ):
                continue
            paid = sum(
                (
                    a.amount_base
                    for a in self.comp.result.allocations
                    if a.load_id in load_ids
                    and self.comp.cost_rows[a.cost_id].cost_type is CostType.CUSTOMS_DUTY
                    and self.comp.cost_rows[a.cost_id].status is CostStatus.ACTUAL
                ),
                Decimal(0),
            )
            if not paid:
                continue  # no invoiced duty yet: CONTAINERS_WITHOUT_DUTY or ESTIMATES_OPEN say it
            theoretical = sum(
                (
                    (self.comp.load_rows[load_id].po_line.duty_rate or Decimal(0))
                    * self.comp.result.cif.get(load_id, Decimal(0))
                    for load_id in load_ids
                ),
                Decimal(0),
            )
            if abs(paid - theoretical) > theoretical * DUTY_GUARD:
                gaps.append(box)
        return _item("DUTY_GAP", len(gaps), map(_box, gaps), params={"pct": str(int(DUTY_GUARD * 100))})

    def duty_on_cif(self) -> list[Item]:
        """Boxes whose invoiced duty is spread on customs value while their articles pay different
        rates: each article's share of the duty is then not its own. With one rate, it is the same."""
        spread = []
        for box in self._sorted_boxes():
            rates = {self.comp.lines[line_id].duty_rate for line_id in self._lines_on(box.id)} - {None}
            on_cif = any(
                self.comp.cost_rows[cost_id].cost_type is CostType.CUSTOMS_DUTY
                and self.comp.cost_rows[cost_id].status is CostStatus.ACTUAL
                and self.comp.cost_rows[cost_id].allocation_method is AllocationMethod.BY_CIF_VALUE
                for cost_id in self.on_box.get(box.id, ())
            )
            if on_cif and len(rates) > 1:
                spread.append(box)
        return _item("DUTY_SPREAD_ON_CIF", len(spread), map(_box, spread))

    def without_route_or_size(self) -> list[Item]:
        """Boxes the "far above its route" rule compares with nothing."""
        boxes = self._sorted_boxes()
        no_route = [c for c in boxes if _route(c)[0] == UNKNOWN]
        no_size = [c for c in boxes if not _size(c)]
        return [
            *_item("CONTAINERS_WITHOUT_ROUTE", len(no_route), map(_box, no_route)),
            *_item("CONTAINERS_WITHOUT_SIZE", len(no_size), map(_box, no_size)),
        ]

    def costs_not_on_a_box(self) -> list[Item]:
        """Costs of the period recorded on a bill of lading or an order: allocated, but out of reach of
        the rules that compare a box's charges."""
        costs = self._costs(lambda cost: cost.container_id is None)
        return _item(
            "COSTS_NOT_ON_CONTAINER",
            len(costs),
            self._places(costs, self._recorded_on),
            amount_base=self._money(costs),
        )

    def typed_other(self) -> list[Item]:
        costs = self._costs(lambda cost: cost.cost_type is CostType.OTHER)
        return _item("COSTS_TYPED_OTHER", len(costs), self._places(costs), amount_base=self._money(costs))

    def clocks_running(self) -> list[Item]:
        """Boxes still at the terminal, or not yet returned empty: their demurrage or detention is not
        over, and the audit cannot give its total."""
        running = [
            c
            for c in self._sorted_boxes()
            if (c.discharged_at is not None and c.gate_out_at is None)
            or (c.gate_out_at is not None and c.empty_returned_at is None)
        ]
        return _item("CLOCKS_RUNNING", len(running), map(_box, running))

    def no_sale_price(self) -> list[Item]:
        """Articles of the period without a selling price in the books' currency: no margin for them.
        The same count as the audit's `unpriced_skus`."""
        articles = sku_report(
            self.db, self.comp, self.org, period_from=self.period_from, period_to=self.period_to
        )
        unpriced = [a for a in articles if a.sale_price is None or a.margin_unit is None or a.sale_price <= 0]
        return _item("NO_SALE_PRICE", len(unpriced), (Example("sku", a.sku, a.sku) for a in unpriced))

    def no_assumed_coefficient(self) -> list[Item]:
        return _item("NO_ASSUMED_COEFFICIENT", 1 if _assumed(self.org) is None else 0)

    def estimates_open(self) -> list[Item]:
        costs = self._costs(lambda cost: cost.status is CostStatus.ESTIMATE)
        return _item("ESTIMATES_OPEN", len(costs), self._places(costs), amount_base=self._money(costs))

    # -------------------------------------------------------------------------------- reading

    def _costs_files(self) -> list[ImportJob]:
        """The costs files in the books — committed, not taken back — in the order they came, read once."""
        if self._ledgers is None:
            self._ledgers = list(
                self.db.scalars(
                    select(ImportJob)
                    .where(
                        ImportJob.org_id == self.org.id,
                        ImportJob.kind == ImportKind.COSTS,
                        ImportJob.status == ImportStatus.DONE,
                        ImportJob.undone_at.is_(None),
                    )
                    .order_by(ImportJob.committed_at, ImportJob.id)
                )
            )
        return self._ledgers

    def _held(self) -> dict[str, set[tuple[object, ...]]]:
        """The invoice lines the books hold, as a refused row is compared with them: by forwarder,
        number, amount and currency; without a number, by day instead."""
        held: dict[str, set[tuple[object, ...]]] = defaultdict(set)
        for cost in self.comp.cost_rows.values():
            if cost.status is not CostStatus.ACTUAL:
                continue
            if number := normalize_invoice_number(cost.invoice_number):
                held[number].add((normalize_vendor(cost.vendor), cost.amount, cost.currency))
            else:
                held[""].add((cost.cost_date.isoformat(), normalize_vendor(cost.vendor), cost.amount,
                              cost.currency))  # fmt: skip
        return held

    def _row_in_period(self, row: dict[str, Any], boxes: dict[UUID, Container]) -> bool:
        if row.get("scope") == "CONTAINER" and row.get("target_id"):
            box = boxes.get(UUID(row["target_id"]))
            landed = arrival_date(box) if box is not None else None
            if landed is not None:
                return self.period_from <= landed <= self.period_to
        day = row.get("cost_date")
        return day is None or self.period_from <= date.fromisoformat(day) <= self.period_to

    def _sorted_boxes(self) -> list[Container]:
        return sorted(self.boxes.values(), key=lambda c: c.container_number)

    def _lines_on(self, box: UUID) -> set[UUID]:
        return {self.comp.load_rows[load_id].po_line_id for load_id in self.loads_of.get(box, [])}

    def _orders_on(self, box: UUID) -> list[PurchaseOrder]:
        ids = {self.comp.lines[line_id].po_id for line_id in self._lines_on(box)}
        return [self.comp.pos[po_id] for po_id in ids if po_id in self.comp.pos]

    def _costs(self, keep: Callable[[Cost], bool]) -> list[Cost]:
        return sorted(
            (cost for cost in self.costs_in.values() if keep(cost)),
            key=lambda c: (c.cost_date, c.created_at),
        )
