from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1 import schemas
from app.api.v1.deps import WriterDep
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.money import q2
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import LOADS_REPLACED, record
from app.domain.costing.service import compute, recompute_org
from app.domain.models import (
    Container,
    ContainerLoad,
    ContainerMilestone,
    Cost,
    CostType,
    DndRisk,
    PurchaseOrderLine,
    Shipment,
)
from app.domain.periods import service as periods
from app.domain.tracking.state import derive_dnd

router = APIRouter(prefix="/containers", tags=["containers"])

_MILESTONE_TIMESTAMP = {
    ContainerMilestone.VESSEL_ARRIVED: "ata",
    ContainerMilestone.DISCHARGED: "discharged_at",
    ContainerMilestone.GATE_OUT_FULL: "gate_out_at",
    ContainerMilestone.GATE_IN_EMPTY_RETURN: "empty_returned_at",
}


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _get(t: TenantSession, container_id: UUID) -> Container:
    c = t.db.scalar(
        t.q(Container)
        .where(Container.id == container_id, Container.archived_at.is_(None))
        .options(
            selectinload(Container.loads)
            .selectinload(ContainerLoad.po_line)
            .selectinload(PurchaseOrderLine.purchase_order)
        )
    )
    if c is None:
        raise NotFound("Container")
    return c


@router.get("", response_model=list[schemas.ContainerSummary])
def list_containers(
    t: TenantDep,
    milestone: ContainerMilestone | None = None,
    shipment_id: UUID | None = None,
    dnd_risk: DndRisk | None = None,
) -> list[schemas.ContainerSummary]:
    stmt = (
        t.q(Container)
        .where(Container.archived_at.is_(None))
        .options(
            selectinload(Container.loads)
            .selectinload(ContainerLoad.po_line)
            .selectinload(PurchaseOrderLine.purchase_order),
            selectinload(Container.shipment),
        )
    )
    if milestone:
        stmt = stmt.where(Container.milestone == milestone)
    if shipment_id:
        stmt = stmt.where(Container.shipment_id == shipment_id)
    if dnd_risk:
        stmt = stmt.where(Container.dnd_risk == dnd_risk)
    containers = list(t.db.scalars(stmt.order_by(Container.container_number)))
    comp = compute(t.db, t.org)
    allocated: dict[UUID, Decimal] = {}
    for a in comp.result.allocations:
        ld = comp.load_rows.get(a.load_id)
        if ld is not None:
            allocated[ld.container_id] = allocated.get(ld.container_id, Decimal(0)) + a.amount_base
    cost_types: dict[UUID, set[str]] = {}
    for cst in comp.cost_rows.values():
        if cst.container_id:
            cost_types.setdefault(cst.container_id, set()).add(cst.cost_type.value)
        elif cst.shipment_id:
            for cont in containers:
                if cont.shipment_id == cst.shipment_id:
                    cost_types.setdefault(cont.id, set()).add(cst.cost_type.value)
    frozen_in = periods.frozen_periods(t.db, t.org_id)
    out = []
    for c in containers:
        fob = sum((ld.fob for ld in comp.loads if ld.container_id == c.id), Decimal(0))
        s = schemas.ContainerSummary.model_validate(c)
        s.closed_period = frozen_in.get(c.id)
        s.shipment_reference = c.shipment.reference if c.shipment else None
        s.po_numbers = sorted({ld.po_line.purchase_order.po_number for ld in c.loads})
        s.load_count = len(c.loads)
        s.fob_base = q2(fob)
        s.allocated_base = q2(allocated.get(c.id, Decimal(0)))
        s.cost_types_present = [CostType(v) for v in sorted(cost_types.get(c.id, set()))]
        out.append(s)
    return out


@router.post("", response_model=schemas.ContainerResponse, status_code=status.HTTP_201_CREATED)
def create_container(payload: schemas.ContainerCreate, t: TenantDep, _: WriterDep) -> Container:
    existing = t.db.scalar(
        t.q(Container).where(
            Container.container_number == payload.container_number, Container.archived_at.is_(None)
        )
    )
    if existing is not None:
        raise Conflict(
            f"Container {payload.container_number} already exists",
            code="CONTAINER_EXISTS",
            existing_id=str(existing.id),
        )
    if payload.shipment_id:
        t.get_or_404(Shipment, payload.shipment_id, "Shipment")
    c = t.add(Container(**payload.model_dump()))
    t.db.commit()
    t.db.refresh(c)
    return c


@router.get("/{container_id}", response_model=schemas.ContainerDetail)
def get_container(container_id: UUID, t: TenantDep) -> schemas.ContainerDetail:
    detail = schemas.ContainerDetail.model_validate(_get(t, container_id))
    # What the accounts hold of this container, if a month was closed with it in.
    frozen = periods.frozen_container(t.db, t.org, container_id, lambda: compute(t.db, t.org))
    if frozen is not None:
        detail.closed_period, detail.frozen_landed, detail.drift = (
            frozen.period,
            frozen.frozen_landed,
            frozen.drift,
        )
    return detail


@router.patch("/{container_id}", response_model=schemas.ContainerResponse)
def update_container(
    container_id: UUID, payload: schemas.ContainerUpdate, t: TenantDep, _: WriterDep
) -> Container:
    """Manual tracking: milestone, timestamps, free days. A tracking provider replaces this in Phase 2."""
    c = _get(t, container_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("shipment_id"):
        t.get_or_404(Shipment, changes["shipment_id"], "Shipment")
    for k, v in changes.items():
        setattr(c, k, v)
    if payload.milestone is not None:
        field = _MILESTONE_TIMESTAMP.get(payload.milestone)
        if field and getattr(c, field) is None:
            setattr(c, field, datetime.now(UTC))
    derive_dnd(c, t.org)
    t.db.flush()
    if "shipment_id" in changes:
        recompute_org(t.db, t.org)
    t.db.commit()
    t.db.refresh(c)
    return c


@router.delete("/{container_id}", status_code=status.HTTP_204_NO_CONTENT)
def archive_container(container_id: UUID, t: TenantDep, _: WriterDep) -> None:
    c = _get(t, container_id)
    if t.db.scalar(select(Cost.id).where(Cost.container_id == c.id).limit(1)):
        raise Conflict("Container has costs attached; delete them first")
    c.archived_at = datetime.now(UTC)
    for ld in list(c.loads):
        t.db.delete(ld)
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()


@router.get("/{container_id}/loads", response_model=list[schemas.LoadResponse])
def get_loads(container_id: UUID, t: TenantDep) -> list[schemas.LoadResponse]:
    c = _get(t, container_id)
    return [
        _load_response(ld)
        for ld in sorted(c.loads, key=lambda ld: (ld.po_line.purchase_order.po_number, ld.po_line.line_no))
    ]


def _load_response(ld: ContainerLoad) -> schemas.LoadResponse:
    return schemas.LoadResponse(
        id=ld.id,
        po_line_id=ld.po_line_id,
        po_id=ld.po_line.po_id,
        po_number=ld.po_line.purchase_order.po_number,
        line_no=ld.po_line.line_no,
        sku=ld.po_line.sku,
        quantity=ld.quantity,
        line_quantity=ld.po_line.quantity,
    )


@router.put("/{container_id}/loads", response_model=list[schemas.LoadResponse])
def replace_loads(
    container_id: UUID, payload: list[schemas.LoadIn], t: TenantDep, p: WriterDep
) -> list[schemas.LoadResponse]:
    """Replace the full list of loads for this container. Refuses to over-allocate a PO line."""
    c = _get(t, container_id)
    if len({p.po_line_id for p in payload}) != len(payload):
        raise Unprocessable("Each po_line_id may appear once", code="DUPLICATE_LINE")
    wanted = {p.po_line_id: p.quantity for p in payload}
    lines = {
        ln.id: ln
        for ln in t.db.scalars(
            t.q(PurchaseOrderLine)
            .where(PurchaseOrderLine.id.in_(wanted))
            .options(selectinload(PurchaseOrderLine.purchase_order))
        )
    }
    missing = [str(i) for i in wanted if i not in lines]
    if missing:
        raise NotFound(f"Purchase order line(s) {', '.join(missing)}")
    errors = []
    for line_id, qty in wanted.items():
        elsewhere = Decimal(
            str(
                t.db.scalar(
                    select(func.coalesce(func.sum(ContainerLoad.quantity), 0)).where(
                        ContainerLoad.po_line_id == line_id, ContainerLoad.container_id != c.id
                    )
                )
            )
        )
        if elsewhere + qty > lines[line_id].quantity:
            line = lines[line_id]
            label = f"{line.purchase_order.po_number} line {line.line_no}" + (
                f" ({line.sku})" if line.sku else ""
            )
            errors.append(
                {
                    "field": label,
                    "code": "OVER_ALLOCATED",
                    "message": (
                        f"{_qty(elsewhere + qty)} would be loaded across containers, "
                        f"line quantity is {_qty(line.quantity)}"
                    ),
                }
            )
    if errors:
        raise Unprocessable("Some lines would be over-allocated", code="OVER_ALLOCATED", errors=errors)
    before = {str(ld.po_line_id): str(ld.quantity) for ld in c.loads}
    existing = {ld.po_line_id: ld for ld in c.loads}
    for line_id, ld in existing.items():
        if line_id not in wanted:
            t.db.delete(ld)
    for line_id, qty in wanted.items():
        if line_id in existing:
            existing[line_id].quantity = qty
        else:
            t.add(ContainerLoad(container_id=c.id, po_line_id=line_id, quantity=qty))
    t.db.flush()
    # What is in the box decides how every cost is split, so this is one of the changes someone will
    # want explained later.
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=LOADS_REPLACED,
        entity_type="container",
        entity_id=c.id,
        before=before,
        after={str(line_id): str(qty) for line_id, qty in wanted.items()},
    )
    recompute_org(t.db, t.org)
    t.db.commit()
    return get_loads(container_id, t)
