"""The first screen in one call: what the period cost against the one before, and what needs a person.

Nothing here is a new figure. The landed cost is the landed-cost report's, the variance is the
variance report's rule (an invoice that replaced an estimate, dated in the period), demurrage is the
demurrage report's. The page that opens the product must not be the one place where a number is
computed differently from the screen that explains it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.money import q2
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation, charged_containers, held_containers
from app.domain.models import (
    Container,
    CostStatus,
    CostType,
    DndRisk,
    Invoice,
    InvoiceStatus,
    Organization,
    RateBasis,
    RateCard,
)
from app.domain.reporting.service import (
    Bucket,
    DndAtRiskLine,
    SkuLine,
    UnratedOrder,
    arrival_date,
    dnd_report,
    landed_cost_report,
)

ZERO = Decimal("0.00")
DEFAULT_DAYS = 90
TOP_SKUS = 6


@dataclass
class Figures:
    period_from: date
    period_to: date
    containers: int
    #: None, with `landed` and `coefficient`, while a line of the period has no value.
    fob: Decimal | None
    #: Over the containers that carry a charge, and the value of their goods: see `Bucket`.
    landed: Decimal | None
    charged_fob: Decimal | None
    coefficient: Decimal | None
    #: The part of `landed` still resting on estimates: the audit's completeness for the same dates.
    estimated: Decimal
    containers_without_charge: int
    containers_held: int
    held_orders: list[str]
    #: The known value of the goods of those containers, what a figure over the charged containers
    #: leaves out, and how many of their lines have no value.
    uncharged_fob: Decimal
    uncharged_fob_missing_lines: int
    #: The orders of the period with no exchange rate, where the rate is entered.
    orders_without_fx_rate: list[UnratedOrder]
    variance_estimated: Decimal
    variance_actual: Decimal
    variance: Decimal
    variance_pairs: int
    demurrage_paid: Decimal
    demurrage_avoided: Decimal
    fob_missing: str | None = None
    fob_missing_lines: int = 0


@dataclass
class Todo:
    invoices_to_review: int
    invoices_failed: int
    containers_without_cost: int
    #: Of those, the ones whose charges are entered but held (`held_containers`), by number, and the
    #: orders with no rate holding them: what to open, and where the rate goes.
    containers_held: int
    held_containers: list[str]
    held_orders: list[UnratedOrder]
    containers_without_loads: int
    estimates_awaiting_invoice: int
    containers_overdue: int
    containers_at_risk: int
    amount_at_risk: Decimal | None


@dataclass
class Overview:
    base_currency: str
    current: Figures
    previous: Figures
    todo: Todo
    #: The period seen three ways, from the one computation that produced the figures above: the
    #: first screen used to pay the engine four times to say the same thing.
    by_month: list[Bucket]
    by_supplier: list[Bucket]
    by_cost_type: dict[str, Decimal]
    skus: list[SkuLine]
    at_risk: list[DndAtRiskLine]


def periods(period_from: date | None, period_to: date | None, today: date) -> tuple[date, date, date, date]:
    """The period asked for (the last ninety days by default) and the same number of days before it."""
    end = period_to or today
    start = period_from or end - timedelta(days=DEFAULT_DAYS - 1)
    if start > end:
        start, end = end, start
    length = (end - start).days + 1
    return start, end, start - timedelta(days=length), start - timedelta(days=1)


def overview(
    db: Session,
    comp: Computation,
    org: Organization,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
) -> Overview:
    today = datetime.now(UTC).date()
    start, end, before_start, before_end = periods(period_from, period_to, today)
    card = db.scalar(
        select(RateCard).where(
            RateCard.org_id == org.id,
            RateCard.cost_type == CostType.DEMURRAGE,
            RateCard.basis == RateBasis.FLAT,
        )
    )
    rate = card.amount if card is not None else None
    monthly = landed_cost_report(comp, org, group_by="month", period_from=start, period_to=end)
    suppliers = landed_cost_report(comp, org, group_by="supplier", period_from=start, period_to=end)
    exposure = dnd_report(db, org, daily_rate=rate).at_risk
    return Overview(
        base_currency=org.base_currency,
        current=_figures(db, comp, org, start, end, rate),
        previous=_figures(db, comp, org, before_start, before_end, rate),
        todo=_todo(db, comp, org, rate, exposure),
        by_month=monthly.buckets,
        by_supplier=suppliers.buckets,
        by_cost_type={k: q2(v) for k, v in sorted(monthly.totals.by_cost_type.items())},
        # The articles that weigh the most in the period: what a first screen has room for. One
        # whose landed cost is not known cannot be ranked, and comes after those that can.
        skus=sorted(monthly.skus, key=lambda s: (s.landed is not None, s.landed or ZERO), reverse=True)[
            :TOP_SKUS
        ],
        at_risk=exposure,
    )


def _figures(
    db: Session, comp: Computation, org: Organization, start: date, end: date, rate: Decimal | None
) -> Figures:
    report = landed_cost_report(comp, org, group_by="month", period_from=start, period_to=end)
    totals = report.totals
    landed_boxes = {
        comp.load_rows[load.id].container_id
        for load in comp.loads
        if (landed_on := arrival_date(comp.load_rows[load.id].container)) is not None
        and start <= landed_on <= end
    }
    estimated = actual = ZERO
    pairs = 0
    for invoice, estimate in comp.superseded:
        if not (start <= invoice.cost_date <= end) or invoice.cost_type.value in EXCLUDED_FROM_LANDED:
            continue
        pairs += 1
        estimated += estimate.amount_base
        actual += invoice.amount_base
    dnd = dnd_report(db, org, period_from=start, period_to=end, daily_rate=rate)
    return Figures(
        period_from=start,
        period_to=end,
        containers=len(landed_boxes),
        fob=totals.fob,
        landed=totals.landed,
        charged_fob=totals.charged_fob,
        coefficient=totals.coefficient,
        estimated=totals.estimated,
        containers_without_charge=totals.containers_without_charge,
        containers_held=totals.containers_held,
        held_orders=sorted(totals.held_orders),
        uncharged_fob=totals.uncharged_fob,
        uncharged_fob_missing_lines=totals.uncharged_fob_missing_lines,
        orders_without_fx_rate=report.orders_without_fx_rate,
        variance_estimated=q2(estimated),
        variance_actual=q2(actual),
        variance=q2(actual - estimated),
        variance_pairs=pairs,
        demurrage_paid=dnd.paid,
        demurrage_avoided=dnd.avoided,
        fob_missing=totals.fob_missing,
        fob_missing_lines=totals.fob_missing_lines,
    )


def _todo(
    db: Session, comp: Computation, org: Organization, rate: Decimal | None, exposure: list[DndAtRiskLine]
) -> Todo:
    def invoices(status: InvoiceStatus) -> int:
        return (
            db.scalar(
                select(func.count(Invoice.id)).where(Invoice.org_id == org.id, Invoice.status == status)
            )
            or 0
        )

    active = list(
        db.scalars(select(Container).where(Container.org_id == org.id, Container.archived_at.is_(None)))
    )
    loaded = {comp.load_rows[load.id].container_id for load in comp.loads}
    costed = charged_containers(comp)
    held = held_containers(comp)
    boxes = sorted(c.container_number for c in active if c.id in held)
    numbers = {order for c in active for order in held.get(c.id, ())}
    unrated = (UnratedOrder(po.id, po.po_number) for po in comp.pos.values() if po.po_number in numbers)
    orders = sorted(unrated, key=lambda o: o.po_number)

    landed = {c.id for c in active if c.discharged_at is not None or c.ata is not None}
    waiting = sum(
        1
        for cost in comp.cost_rows.values()
        if cost.status is CostStatus.ESTIMATE and cost.container_id in landed
    )

    overdue = [line for line in exposure if line.dnd_risk is DndRisk.INCURRING]
    amounts = [line.amount_at_risk for line in exposure if line.amount_at_risk is not None]
    return Todo(
        invoices_to_review=invoices(InvoiceStatus.NEEDS_REVIEW),
        invoices_failed=invoices(InvoiceStatus.FAILED),
        containers_without_cost=sum(1 for c in active if c.id in loaded and c.id not in costed),
        containers_held=len(boxes),
        held_containers=boxes,
        held_orders=orders,
        containers_without_loads=sum(1 for c in active if c.id not in loaded),
        estimates_awaiting_invoice=waiting,
        containers_overdue=len(overdue),
        containers_at_risk=len(exposure) - len(overdue),
        amount_at_risk=q2(sum(amounts, ZERO)) if rate is not None else None,
    )
