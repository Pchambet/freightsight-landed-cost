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

from app.core.money import q2, q4
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation
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
    fob: Decimal
    landed: Decimal
    coefficient: Decimal | None
    variance_estimated: Decimal
    variance_actual: Decimal
    variance: Decimal
    variance_pairs: int
    demurrage_paid: Decimal
    demurrage_avoided: Decimal


@dataclass
class Todo:
    invoices_to_review: int
    invoices_failed: int
    containers_without_cost: int
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
        # The articles that weigh the most in the period: what a first screen has room for.
        skus=sorted(monthly.skus, key=lambda s: s.landed, reverse=True)[:TOP_SKUS],
        at_risk=exposure,
    )


def _figures(
    db: Session, comp: Computation, org: Organization, start: date, end: date, rate: Decimal | None
) -> Figures:
    totals = landed_cost_report(comp, org, group_by="month", period_from=start, period_to=end).totals
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
        coefficient=q4(totals.landed / totals.fob) if totals.fob else None,
        variance_estimated=q2(estimated),
        variance_actual=q2(actual),
        variance=q2(actual - estimated),
        variance_pairs=pairs,
        demurrage_paid=dnd.paid,
        demurrage_avoided=dnd.avoided,
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
    costed = {comp.load_rows[a.load_id].container_id for a in comp.result.allocations}

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
        containers_without_loads=sum(1 for c in active if c.id not in loaded),
        estimates_awaiting_invoice=waiting,
        containers_overdue=len(overdue),
        containers_at_risk=len(exposure) - len(overdue),
        amount_at_risk=q2(sum(amounts, ZERO)) if rate is not None else None,
    )
