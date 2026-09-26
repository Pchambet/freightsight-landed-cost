"""An article seen through its arrivals: what each container made it cost, and what that leaves.

`sku_cost_history` records the cumulative unit cost on each day the numbers were recomputed — a
history of the organization's knowledge. This report is a history of the *goods*: one point per
container that carried the article, dated when it landed, with the cost types that made up its
landed cost. It is computed live from the engine, so each point can be explained down to the invoice.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import q2, q4
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation
from app.domain.models import CostStatus, Organization, Product
from app.domain.reporting.service import arrival_date
from app.domain.search.service import fold

ZERO = Decimal("0.00")


@dataclass
class SkuArrival:
    load_id: UUID
    container_id: UUID
    container_number: str
    po_id: UUID
    po_number: str
    supplier_name: str | None
    arrived_on: date | None
    #: The date is a plan (the shipment's ETA), not a discharge or an arrival on record.
    arrival_is_estimate: bool
    quantity: Decimal
    fob: Decimal
    landed: Decimal
    unit_fob: Decimal
    unit_landed: Decimal
    by_cost_type: dict[str, Decimal]
    unit_by_cost_type: dict[str, Decimal]
    #: The part of `landed` that still rests on estimates rather than invoices.
    estimated: Decimal


@dataclass
class SkuBox:
    """The article in one container, whatever number of order lines carried it there: the unit of
    "the last arrival" and "the one before", and what a curve plots one point for."""

    container_id: UUID
    container_number: str
    arrived_on: date | None
    arrival_is_estimate: bool
    quantity: Decimal
    fob: Decimal
    landed: Decimal
    unit_fob: Decimal
    unit_landed: Decimal
    by_cost_type: dict[str, Decimal]
    unit_by_cost_type: dict[str, Decimal]
    estimated: Decimal


@dataclass
class SkuSummary:
    sku: str
    description: str | None = None
    quantity: Decimal = Decimal(0)
    fob: Decimal = ZERO
    landed: Decimal = ZERO
    unit_fob: Decimal = Decimal(0)
    unit_landed: Decimal = Decimal(0)
    last_unit_landed: Decimal | None = None
    previous_unit_landed: Decimal | None = None
    change_pct: Decimal | None = None
    arrivals: int = 0
    last_arrival_on: date | None = None
    has_estimates: bool = False
    product_id: UUID | None = None
    sale_price: Decimal | None = None
    margin_unit: Decimal | None = None
    margin_pct: Decimal | None = None
    #: The same two, against the last container that landed: the stock about to be sold.
    last_margin_unit: Decimal | None = None
    last_margin_pct: Decimal | None = None
    points: list[SkuArrival] = field(default_factory=list)
    boxes: list[SkuBox] = field(default_factory=list)


def sku_report(
    db: Session,
    comp: Computation,
    org: Organization,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
    query: str | None = None,
    only_sku: str | None = None,
) -> list[SkuSummary]:
    """Every article that travelled in the period, with its arrivals oldest first."""
    per_load: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    estimated: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if cost.cost_type.value in EXCLUDED_FROM_LANDED:
            continue
        per_load[allocation.load_id][cost.cost_type.value] += allocation.amount_base
        if cost.status is CostStatus.ESTIMATE:
            estimated[allocation.load_id] += allocation.amount_base

    summaries: dict[str, SkuSummary] = {}
    for load in comp.loads:
        row = comp.load_rows[load.id]
        sku = row.po_line.sku
        if not sku or (only_sku is not None and sku != only_sku):
            continue
        container = row.container
        landed_on = arrival_date(container)
        if period_from and (landed_on is None or landed_on < period_from):
            continue
        if period_to and (landed_on is None or landed_on > period_to):
            continue

        by_type = {k: q2(v) for k, v in sorted(per_load.get(load.id, {}).items())}
        landed = load.fob + sum(by_type.values(), ZERO)
        order = row.po_line.purchase_order
        summary = summaries.setdefault(sku, SkuSummary(sku=sku))
        # The most complete description seen names the article.
        if len(row.po_line.description or "") > len(summary.description or ""):
            summary.description = row.po_line.description
        summary.points.append(
            SkuArrival(
                load_id=load.id,
                container_id=container.id,
                container_number=container.container_number,
                po_id=order.id,
                po_number=order.po_number,
                supplier_name=order.supplier.name if order.supplier is not None else None,
                arrived_on=landed_on,
                arrival_is_estimate=container.discharged_at is None and container.ata is None,
                quantity=load.quantity,
                fob=q2(load.fob),
                landed=q2(landed),
                unit_fob=_per_unit(load.fob, load.quantity),
                unit_landed=_per_unit(landed, load.quantity),
                by_cost_type=by_type,
                unit_by_cost_type={k: _per_unit(v, load.quantity) for k, v in by_type.items()},
                estimated=q2(estimated.get(load.id, ZERO)),
            )
        )

    if query:
        needle = fold(query).strip()
        summaries = {
            sku: s
            for sku, s in summaries.items()
            if needle in fold(sku) or needle in fold(s.description or "")
        }

    products = {
        p.sku: p
        for p in db.scalars(select(Product).where(Product.org_id == org.id, Product.sku.in_(summaries)))
    }
    for summary in summaries.values():
        _summarise(summary, products.get(summary.sku), org)
    return sorted(summaries.values(), key=lambda s: s.sku)


def _box(points: list[SkuArrival]) -> SkuBox:
    quantity = sum((p.quantity for p in points), Decimal(0))
    fob = sum((p.fob for p in points), ZERO)
    landed = sum((p.landed for p in points), ZERO)
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for point in points:
        for cost_type, amount in point.by_cost_type.items():
            by_type[cost_type] += amount
    first = points[0]
    return SkuBox(
        container_id=first.container_id,
        container_number=first.container_number,
        arrived_on=first.arrived_on,
        arrival_is_estimate=first.arrival_is_estimate,
        quantity=quantity,
        fob=q2(fob),
        landed=q2(landed),
        unit_fob=_per_unit(fob, quantity),
        unit_landed=_per_unit(landed, quantity),
        by_cost_type=dict(sorted(by_type.items())),
        unit_by_cost_type={k: _per_unit(v, quantity) for k, v in sorted(by_type.items())},
        estimated=q2(sum((p.estimated for p in points), ZERO)),
    )


def _per_unit(amount: Decimal, quantity: Decimal) -> Decimal:
    return q4(amount / quantity) if quantity else Decimal(0)


def _summarise(summary: SkuSummary, product: Product | None, org: Organization) -> None:
    summary.points.sort(key=lambda p: (p.arrived_on is None, p.arrived_on or date.max, p.container_number))
    quantity = sum((p.quantity for p in summary.points), Decimal(0))
    fob = sum((p.fob for p in summary.points), ZERO)
    landed = sum((p.landed for p in summary.points), ZERO)
    summary.quantity, summary.fob, summary.landed = quantity, q2(fob), q2(landed)
    # Weighted by quantity: the same article landed twice at two costs is one number, not their mean.
    summary.unit_fob, summary.unit_landed = _per_unit(fob, quantity), _per_unit(landed, quantity)
    summary.has_estimates = any(p.estimated > 0 for p in summary.points)
    summary.arrivals = len({p.container_id for p in summary.points})

    grouped: dict[UUID, list[SkuArrival]] = defaultdict(list)
    for point in summary.points:
        grouped[point.container_id].append(point)
    summary.boxes = [_box(points) for points in grouped.values()]
    summary.boxes.sort(key=lambda b: (b.arrived_on is None, b.arrived_on or date.max, b.container_number))

    # "Last" and "previous" are containers that have landed: a box still at sea has an estimate of a
    # cost, and comparing it with an invoiced one would announce a variation nobody has paid yet.
    landed_boxes = [b for b in summary.boxes if b.arrived_on is not None and not b.arrival_is_estimate]
    if landed_boxes:
        summary.last_unit_landed = landed_boxes[-1].unit_landed
        summary.last_arrival_on = landed_boxes[-1].arrived_on
    if len(landed_boxes) >= 2 and landed_boxes[-2].unit_landed:
        before, after = landed_boxes[-2].unit_landed, landed_boxes[-1].unit_landed
        summary.previous_unit_landed = before
        summary.change_pct = ((after - before) / before * 100).quantize(Decimal("0.1"))

    if product is None:
        return
    summary.product_id = product.id
    # No conversion is invented: a price in another currency is shown, and no margin is derived.
    if product.sale_price is None or product.sale_currency not in (None, org.base_currency):
        summary.sale_price = product.sale_price
        return
    summary.sale_price = product.sale_price
    summary.margin_unit = q4(product.sale_price - summary.unit_landed)
    if summary.last_unit_landed is not None:
        summary.last_margin_unit = q4(product.sale_price - summary.last_unit_landed)
    if product.sale_price:
        summary.margin_pct = (summary.margin_unit / product.sale_price * 100).quantize(Decimal("0.1"))
        if summary.last_margin_unit is not None:
            summary.last_margin_pct = (summary.last_margin_unit / product.sale_price * 100).quantize(
                Decimal("0.1")
            )
