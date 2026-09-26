from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.v1 import schemas
from app.core.tenancy import TenantDep, TenantSession
from app.domain.costing import report as rpt
from app.domain.costing.service import Computation, compute
from app.domain.models import Container, Cost, CostAllocation, PurchaseOrder, Shipment

router = APIRouter(tags=["landed-costs"])


def _serialize(r: rpt.Report) -> schemas.LandedCostReport:
    return schemas.LandedCostReport(
        base_currency=r.base_currency,
        totals={
            "fob": r.fob,
            "allocated": r.allocated,
            "vat": r.vat,
            "landed": r.landed,
            "unallocated": r.unallocated,
            "estimated": r.estimated,
            "actual": r.actual,
            "variance": r.variance,
            "matched_estimated": r.matched_estimated,
            "matched_actual": r.matched_actual,
            "unforecast_actual": r.unforecast_actual,
        },
        matched_pairs=r.matched_pairs,
        completeness=r.completeness,
        by_cost_type=r.by_cost_type,
        by_cost_type_detail={
            k: schemas.CostTypeSplit(estimated=v.estimated, actual=v.actual)
            for k, v in r.by_cost_type_detail.items()
        },
        lines=[schemas.ReportLine(**ln.__dict__) for ln in r.lines],
        costs=[schemas.CostResponse.model_validate(c) for c in r.costs],
        warnings=[schemas.ReportWarning(**w.__dict__) for w in r.warnings],
        notes=[schemas.ReportNote(**n.__dict__) for n in r.notes],
    )


def _report_for(t: TenantSession, comp: Computation, req: schemas.PreviewRequest) -> rpt.Report:
    if req.container_id:
        t.get_or_404(Container, req.container_id, "Container")
        return rpt.container_report(comp, t.org.base_currency, req.container_id)
    if req.shipment_id:
        t.get_or_404(Shipment, req.shipment_id, "Shipment")
        return rpt.shipment_report(comp, t.org.base_currency, req.shipment_id)
    assert req.po_id
    t.get_or_404(PurchaseOrder, req.po_id, "Purchase order")
    return rpt.purchase_order_report(comp, t.org.base_currency, req.po_id)


@router.get("/landed-costs/containers/{container_id}", response_model=schemas.LandedCostReport)
def container_landed_costs(container_id: UUID, t: TenantDep) -> schemas.LandedCostReport:
    return _serialize(_report_for(t, compute(t.db, t.org), schemas.PreviewRequest(container_id=container_id)))


@router.get("/landed-costs/shipments/{shipment_id}", response_model=schemas.LandedCostReport)
def shipment_landed_costs(shipment_id: UUID, t: TenantDep) -> schemas.LandedCostReport:
    return _serialize(_report_for(t, compute(t.db, t.org), schemas.PreviewRequest(shipment_id=shipment_id)))


@router.get("/landed-costs/purchase-orders/{po_id}", response_model=schemas.LandedCostReport)
def po_landed_costs(po_id: UUID, t: TenantDep) -> schemas.LandedCostReport:
    return _serialize(_report_for(t, compute(t.db, t.org), schemas.PreviewRequest(po_id=po_id)))


@router.post("/landed-costs/preview", response_model=schemas.LandedCostReport)
def preview(req: schemas.PreviewRequest, t: TenantDep) -> schemas.LandedCostReport:
    """Same report with per-cost-type method overrides, nothing persisted. Powers the UI live switch."""
    comp = compute(t.db, t.org, method_overrides=req.method_overrides)
    return _serialize(_report_for(t, comp, req))


@router.get("/reports/integrity", response_model=schemas.IntegrityReport)
def integrity(t: TenantDep) -> schemas.IntegrityReport:
    """Every cost is either fully allocated (Σ allocations == amount_base) or reported as unallocated."""
    comp = compute(t.db, t.org)
    unallocated = {u.cost_id for u in comp.result.unallocated}
    rows = t.db.execute(
        select(CostAllocation.cost_id, func.sum(CostAllocation.amount_base))
        .where(CostAllocation.org_id == t.org.id)
        .group_by(CostAllocation.cost_id)
    ).all()
    sums: dict[UUID, Decimal] = {row[0]: Decimal(str(row[1])) for row in rows}
    mismatches = []
    for cost in t.db.scalars(t.q(Cost)):
        allocated = Decimal(str(sums.get(cost.id, 0)))
        if cost.id in unallocated:
            if allocated != 0:
                mismatches.append({"cost_id": str(cost.id), "reason": "unallocated but has allocations"})
        elif allocated != cost.amount_base:
            mismatches.append(
                {
                    "cost_id": str(cost.id),
                    "reason": f"allocated {allocated} != amount_base {cost.amount_base}",
                }
            )
    return schemas.IntegrityReport(
        ok=not mismatches, costs_checked=len(comp.cost_rows), mismatches=mismatches
    )
