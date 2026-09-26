"""Reports that answer a question someone actually asks at month end."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1 import schemas
from app.core.errors import NotFound
from app.core.money import q2
from app.core.tenancy import TenantDep
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.report import estimate_allocations
from app.domain.costing.service import compute
from app.domain.models import CostType, RateBasis, RateCard
from app.domain.reporting import service as reporting
from app.domain.reporting.audit import Audit, audit_report
from app.domain.reporting.overview import overview as build_overview
from app.domain.reporting.preparation import audit_preparation as prepare_audit
from app.domain.reporting.skus import sku_report

router = APIRouter(prefix="/reports", tags=["reports"])

GroupBy = Literal["supplier", "route", "month", "cost_type"]

ZERO = Decimal("0.00")


def month_bounds(period: str) -> tuple[date, date]:
    year, month = int(period[:4]), int(period[5:7])
    start = date(year, month, 1)
    end = date(year + (month == 12), (month % 12) + 1, 1)
    return start, end


@router.get("/variance", response_model=schemas.VarianceReport)
def variance(
    t: TenantDep,
    period: str = Query(pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
) -> schemas.VarianceReport:
    """Estimated against invoiced, for the invoices dated in one month.

    Grouped by cost type and by purchase order, because those are the two questions: which charge
    surprised us, and which order carries the surprise. The split by order is the engine's own
    allocation of each invoice, not a proportion invented here.
    """
    start, end = month_bounds(period)
    comp = compute(t.db, t.org)
    estimate_alloc = estimate_allocations(comp)

    by_type: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    by_po: dict[UUID, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    po_numbers: dict[UUID, str] = {}
    pairs = 0

    for actual, estimate in comp.superseded:
        if not (start <= actual.cost_date < end):
            continue
        if actual.cost_type.value in EXCLUDED_FROM_LANDED:
            # Import VAT is recoverable and is not part of any landed cost we report; a variance on
            # it would be the one line of this page that the container reports do not carry, which is
            # exactly how two screens end up telling a different story about the same month.
            continue
        pairs += 1
        by_type[actual.cost_type.value][1] += actual.amount_base
        by_type[estimate.cost_type.value][0] += estimate.amount_base

        for allocation in comp.result.allocations:
            if allocation.cost_id != actual.id:
                continue
            po_id = comp.load_rows[allocation.load_id].po_line.po_id
            by_po[po_id][1] += allocation.amount_base
        for load_id, amount in estimate_alloc.get(estimate.id, {}).items():
            po_id = comp.load_rows[load_id].po_line.po_id
            by_po[po_id][0] += amount

    for po_id in by_po:
        po = comp.pos.get(po_id)
        po_numbers[po_id] = po.po_number if po is not None else str(po_id)

    estimated = sum((values[0] for values in by_type.values()), ZERO)
    actual_total = sum((values[1] for values in by_type.values()), ZERO)
    return schemas.VarianceReport(
        period=period,
        base_currency=t.org.base_currency,
        estimated=q2(estimated),
        actual=q2(actual_total),
        variance=q2(actual_total - estimated),
        pairs=pairs,
        by_cost_type=[
            schemas.VarianceRow(
                key=cost_type,
                label=cost_type,
                estimated=q2(values[0]),
                actual=q2(values[1]),
                variance=q2(values[1] - values[0]),
            )
            for cost_type, values in sorted(by_type.items())
        ],
        by_purchase_order=[
            schemas.VarianceRow(
                key=str(po_id),
                label=po_numbers[po_id],
                estimated=q2(values[0]),
                actual=q2(values[1]),
                variance=q2(values[1] - values[0]),
            )
            for po_id, values in sorted(by_po.items(), key=lambda item: po_numbers[item[0]])
        ],
    )


def _bucket(bucket: reporting.Bucket) -> schemas.ReportBucket:
    return schemas.ReportBucket(
        key=bucket.key,
        label=bucket.label,
        fob=bucket.fob,
        allocated=bucket.allocated,
        vat=bucket.vat,
        landed=bucket.landed,
        quantity=bucket.quantity,
        load_count=bucket.load_count,
        freight_share_pct=bucket.freight_share,
        by_cost_type=dict(bucket.by_cost_type),
    )


@router.get("/landed-cost", response_model=schemas.LandedCostAnalysis)
def landed_cost(
    t: TenantDep,
    group_by: GroupBy = "supplier",
    period_from: date | None = None,
    period_to: date | None = None,
) -> schemas.LandedCostAnalysis:
    """Landed cost by supplier, route, month or charge, over the period the goods landed in.

    The period is about arrival, not invoicing: a forwarder billing six weeks late must not move last
    quarter's numbers.
    """
    report = reporting.landed_cost_report(
        compute(t.db, t.org),
        t.org,
        group_by=group_by,
        period_from=period_from,
        period_to=period_to,
    )
    return schemas.LandedCostAnalysis(
        base_currency=report.base_currency,
        group_by=group_by,
        period_from=report.period_from,
        period_to=report.period_to,
        buckets=[_bucket(b) for b in report.buckets],
        skus=[
            schemas.ReportSkuLine(
                sku=line.sku,
                quantity=line.quantity,
                fob=line.fob,
                landed=line.landed,
                unit_landed_cost=line.unit_landed_cost,
                load_count=line.load_count,
            )
            for line in report.skus
        ],
        totals=_bucket(report.totals),
    )


@router.get("/dnd", response_model=schemas.DndAnalysis)
def demurrage_and_detention(
    t: TenantDep,
    period_from: date | None = None,
    period_to: date | None = None,
    basis: Literal["cost_date", "arrival"] = "cost_date",
) -> schemas.DndAnalysis:
    """What demurrage and detention cost, and what acting in time plausibly saved.

    Paid is a fact. Avoided is an estimate that each line justifies in words, and that does not exist
    at all without the organization's own daily rate: a made-up saving is the kind of figure that
    destroys trust in every other number on the page.

    `basis=arrival` counts what the boxes that arrived in the period cost, whenever it was invoiced —
    the audit's reading, since detention is billed after the empty comes back.
    """
    card = t.db.scalar(
        t.q(RateCard).where(RateCard.cost_type == CostType.DEMURRAGE, RateCard.basis == RateBasis.FLAT)
    )
    report = reporting.dnd_report(
        t.db,
        t.org,
        period_from=period_from,
        period_to=period_to,
        daily_rate=card.amount if card else None,
        basis=basis,
    )
    return schemas.DndAnalysis(
        base_currency=report.base_currency,
        period_from=report.period_from,
        period_to=report.period_to,
        paid=report.paid,
        avoided=report.avoided,
        estimable=report.estimable,
        not_estimable=report.not_estimable,
        lines=[
            schemas.DndReportLine(
                container_id=line.container_id,
                container_number=line.container_number,
                paid=line.paid,
                avoided=line.avoided,
                rule=line.rule,
                rule_code=line.rule_code,
                days=line.days,
                daily_rate=line.daily_rate,
                days_over=line.days_over,
            )
            for line in report.lines
        ],
        at_risk=[schemas.DndAtRiskLine.model_validate(line) for line in report.at_risk],
    )


@router.get("/overview", response_model=schemas.OverviewResponse)
def overview(
    t: TenantDep, period_from: date | None = None, period_to: date | None = None
) -> schemas.OverviewResponse:
    """The first screen in one call: the period (the last ninety days by default) against the same
    number of days before it, and what is waiting for somebody today. Every figure is the one the
    detailed report gives for the same dates."""
    found = build_overview(t.db, compute(t.db, t.org), t.org, period_from=period_from, period_to=period_to)
    return schemas.OverviewResponse(
        base_currency=found.base_currency,
        current=schemas.OverviewFigures.model_validate(found.current),
        previous=schemas.OverviewFigures.model_validate(found.previous),
        todo=schemas.OverviewTodo.model_validate(found.todo),
        by_month=[_bucket(b) for b in found.by_month],
        by_supplier=[_bucket(b) for b in found.by_supplier],
        by_cost_type=found.by_cost_type,
        skus=[
            schemas.ReportSkuLine(
                sku=line.sku,
                quantity=line.quantity,
                fob=line.fob,
                landed=line.landed,
                unit_landed_cost=line.unit_landed_cost,
                load_count=line.load_count,
            )
            for line in found.skus
        ],
        at_risk=[schemas.DndAtRiskLine.model_validate(line) for line in found.at_risk],
    )


@router.get("/audit", response_model=schemas.AuditReport)
def audit(t: TenantDep, period_from: date, period_to: date) -> schemas.AuditReport:
    """The audit of a period: the margin a purchase-price pricing overstates, what is worth a claim
    to the forwarders, what demurrage cost, the measured coefficient against the assumed one, and
    how complete the figures are. One computation; every section's total is the platform's. A period
    that ends before it starts, or outside the years 2000 to 2100, is refused with PERIOD_INVALID."""
    found = audit_report(t.db, compute(t.db, t.org), t.org, period_from, period_to)
    return _audit_schema(found)


@router.get("/audit/preparation", response_model=schemas.AuditPreparation)
def audit_preparation(t: TenantDep, period_from: date, period_to: date) -> schemas.AuditPreparation:
    """Before auditing a period: what would make the audit wrong — boxes with no date, no goods, no
    freight, invoices not yet read, costs the engine could not place, one invoice recorded twice —
    then what it will not be able to say. Counts, amounts and examples by id; the same computation and
    the same rules as the audit. A period that makes no sense is refused with PERIOD_INVALID."""
    found = prepare_audit(t.db, compute(t.db, t.org), t.org, period_from, period_to)
    return schemas.AuditPreparation.model_validate(found)


def _audit_schema(found: Audit) -> schemas.AuditReport:
    return schemas.AuditReport(
        base_currency=found.base_currency,
        period_from=found.period_from,
        period_to=found.period_to,
        as_of=found.as_of,
        headline=schemas.AuditHeadline.model_validate(found.headline),
        margins=[schemas.AuditMargin.model_validate(m) for m in found.margins],
        findings=[schemas.AuditFinding.model_validate(f) for f in found.findings],
        demurrage=[schemas.AuditDemurrageLine.model_validate(d) for d in found.demurrage],
        coefficient_by_month=[schemas.AuditCoefficient.model_validate(b) for b in found.coefficient_by_month],
        coefficient_by_supplier=[
            schemas.AuditCoefficient.model_validate(b) for b in found.coefficient_by_supplier
        ],
        coefficient=found.coefficient,
        assumed_coefficient=found.assumed_coefficient,
        completeness=schemas.AuditCompleteness.model_validate(found.completeness),
        rules=schemas.AuditRules.model_validate(found.rules),
    )


@router.get("/skus", response_model=schemas.SkuListResponse)
def skus(
    t: TenantDep,
    period_from: date | None = None,
    period_to: date | None = None,
    q: str | None = Query(default=None, max_length=80),
) -> schemas.SkuListResponse:
    """Every article that travelled in the period: quantity, FOB, landed cost, the weighted unit
    cost, how the last arrival compares with the one before, and the margin when the catalogue holds
    a selling price."""
    found = sku_report(
        t.db, compute(t.db, t.org), t.org, period_from=period_from, period_to=period_to, query=q
    )
    return schemas.SkuListResponse(
        base_currency=t.org.base_currency,
        skus=[schemas.SkuSummary.model_validate(s) for s in found],
    )


@router.get("/skus/detail", response_model=schemas.SkuDetailResponse)
def sku_detail(
    t: TenantDep,
    sku: str = Query(min_length=1, max_length=120),
    period_from: date | None = None,
    period_to: date | None = None,
) -> schemas.SkuDetailResponse:
    """One article, one point per container that carried it, oldest first, each with the cost types
    that made up its landed cost. The SKU is a parameter because a SKU may contain a slash."""
    found = sku_report(
        t.db, compute(t.db, t.org), t.org, period_from=period_from, period_to=period_to, only_sku=sku
    )
    if not found:
        raise NotFound("SKU")
    (summary,) = found
    return schemas.SkuDetailResponse(
        base_currency=t.org.base_currency,
        summary=schemas.SkuSummary.model_validate(summary),
        containers=[schemas.SkuBox.model_validate(box) for box in summary.boxes],
        arrivals=[schemas.SkuArrival.model_validate(point) for point in summary.points],
    )


@router.get("/sku/{sku}/history", response_model=schemas.SkuHistoryResponse)
def sku_history(sku: str, t: TenantDep) -> schemas.SkuHistoryResponse:
    """How this SKU's landed unit cost moved, one point per day it was recomputed."""
    points = reporting.sku_history(t.db, t.org, sku)
    return schemas.SkuHistoryResponse(
        sku=sku,
        base_currency=t.org.base_currency,
        points=[schemas.SkuHistoryPoint.model_validate(point) for point in points],
    )
