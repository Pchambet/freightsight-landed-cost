from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.errors import Conflict, Unprocessable
from app.core.money import q2
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import record
from app.domain.costing.service import recompute_org
from app.domain.fx.orders import PURCHASE_ORDER_UPDATED, TYPED, header, rate_unconfirmed
from app.domain.models import Container, ContainerLoad, PurchaseOrder, PurchaseOrderLine, Supplier
from app.domain.products import service as products

router = APIRouter(prefix="/purchase-orders", tags=["purchase-orders"])


def _to_response(t: TenantSession, po: PurchaseOrder) -> schemas.PurchaseOrderResponse:
    out = schemas.PurchaseOrderResponse.model_validate(po)
    out.supplier_name = po.supplier.name if po.supplier else None
    out.rate_unconfirmed = rate_unconfirmed(po, t.org.base_currency)
    return out


def _load_po(t: TenantSession, po_id: UUID) -> PurchaseOrder:
    po = t.db.scalar(
        t.q(PurchaseOrder)
        .where(PurchaseOrder.id == po_id)
        .options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.supplier))
    )
    if po is None:
        from app.core.errors import NotFound

        raise NotFound("Purchase order")
    return po


def _resolve_po_fx(t: TenantSession, fx: FxDep, po: PurchaseOrder, manual: Decimal | None) -> None:
    """Set the order's rate, and where it came from (ecb, ecb_latest, manual, same)."""
    if po.currency == t.org.base_currency:
        po.fx_rate, po.fx_source = Decimal("1"), "same"
        return
    on_date = po.fx_date or po.order_date
    if on_date is None:
        if manual is None:
            raise Unprocessable(
                "A purchase order in a foreign currency needs fx_date or order_date, or a manual fx_rate",
                code="FX_DATE_REQUIRED",
            )
        po.fx_rate, po.fx_source = manual, "manual"
        return
    resolved = fx.resolve(t.db, t.org.base_currency, po.currency, on_date, manual)
    po.fx_rate, po.fx_date, po.fx_source = resolved.rate, on_date, resolved.source


@router.get("", response_model=list[schemas.PurchaseOrderSummary])
def list_purchase_orders(
    t: TenantDep, q: str | None = None, supplier_id: UUID | None = None
) -> list[schemas.PurchaseOrderSummary]:
    stmt = t.q(PurchaseOrder).options(selectinload(PurchaseOrder.lines), selectinload(PurchaseOrder.supplier))
    if q:
        stmt = stmt.where(PurchaseOrder.po_number.ilike(f"%{q}%"))
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    pos = list(t.db.scalars(stmt.order_by(PurchaseOrder.po_number)))
    if not pos:
        return []
    line_ids = [ln.id for po in pos for ln in po.lines]
    rows = t.db.execute(
        select(ContainerLoad.po_line_id, Container.container_number)
        .join(Container, Container.id == ContainerLoad.container_id)
        .where(ContainerLoad.po_line_id.in_(line_ids))
    ).all()
    containers_by_line: dict[UUID, set[str]] = {}
    for line_id, number in rows:
        containers_by_line.setdefault(line_id, set()).add(number)
    out = []
    for po in pos:
        numbers: set[str] = set()
        for ln in po.lines:
            numbers |= containers_by_line.get(ln.id, set())
        out.append(
            schemas.PurchaseOrderSummary(
                id=po.id,
                po_number=po.po_number,
                supplier_name=po.supplier.name if po.supplier else None,
                currency=po.currency,
                line_count=len(po.lines),
                fob_base=None
                if po.fx_rate is None
                else q2(sum((ln.quantity * ln.unit_price * po.fx_rate for ln in po.lines), Decimal(0))),
                container_numbers=sorted(numbers),
            )
        )
    return out


@router.post("", response_model=schemas.PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    payload: schemas.PurchaseOrderCreate, t: TenantDep, fx: FxDep, _: WriterDep
) -> schemas.PurchaseOrderResponse:
    existing = t.db.scalar(t.q(PurchaseOrder).where(PurchaseOrder.po_number == payload.po_number))
    if existing is not None:
        # Said with its id: a screen can offer to carry on with the order that is already there.
        raise Conflict(
            f"Purchase order {payload.po_number} already exists",
            code="PURCHASE_ORDER_EXISTS",
            existing_id=str(existing.id),
        )
    supplier_id = payload.supplier_id
    if supplier_id:
        t.get_or_404(Supplier, supplier_id, "Supplier")
    elif payload.supplier_name and payload.supplier_name.strip():
        # By name, as an import does: the supplier of that name, or a new one.
        name = payload.supplier_name.strip()
        supplier = t.db.scalar(t.q(Supplier).where(func.lower(Supplier.name) == name.lower()))
        if supplier is None:
            supplier = t.add(Supplier(name=name))
            t.db.flush()
        supplier_id = supplier.id
    po = t.add(
        PurchaseOrder(
            po_number=payload.po_number,
            supplier_id=supplier_id,
            currency=payload.currency or t.org.base_currency,
            fx_date=payload.fx_date,
            incoterm=payload.incoterm,
            order_date=payload.order_date,
        )
    )
    _resolve_po_fx(t, fx, po, payload.fx_rate)
    catalogue = products.by_sku(t.db, t.org_id, {ln.sku for ln in payload.lines if ln.sku})
    for ln in payload.lines:
        line = t.add(PurchaseOrderLine(purchase_order=po, **ln.model_dump()))
        products.fill_blanks(line, catalogue.get(ln.sku or ""))
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()
    return _to_response(t, _load_po(t, po.id))


@router.get("/{po_id}", response_model=schemas.PurchaseOrderResponse)
def get_purchase_order(po_id: UUID, t: TenantDep) -> schemas.PurchaseOrderResponse:
    return _to_response(t, _load_po(t, po_id))


@router.patch("/{po_id}", response_model=schemas.PurchaseOrderResponse)
def update_purchase_order(
    po_id: UUID, payload: schemas.PurchaseOrderUpdate, t: TenantDep, fx: FxDep, p: WriterDep
) -> schemas.PurchaseOrderResponse:
    po = _load_po(t, po_id)
    before = header(po)
    rate_day = po.fx_date or po.order_date
    had_rate = po.fx_rate is not None
    typed = po.fx_source in TYPED
    changes = payload.model_dump(exclude_unset=True, exclude={"lines", "fx_rate"})
    supplier = (
        t.get_or_404(Supplier, changes["supplier_id"], "Supplier") if changes.get("supplier_id") else None
    )
    for k, v in changes.items():
        setattr(po, k, v)
    if "supplier_id" in changes:
        po.supplier = supplier
    # The rate is looked up again when one is given, when an order without one has a new date, or
    # when the day it is the rate of has moved — by its own date only, for a rate a person typed or
    # the order file gave: an order date added to such an order used to replace it with the ECB's of
    # that day, or to fail on a currency the ECB does not quote.
    dated = "fx_date" in changes or "order_date" in changes
    day_moved = (po.fx_date or po.order_date) != rate_day and (not typed or "fx_date" in changes)
    if payload.fx_rate is not None or day_moved or (dated and not had_rate):
        try:
            _resolve_po_fx(t, fx, po, payload.fx_rate)
        except Unprocessable:
            if payload.fx_rate is not None or had_rate:
                raise
            # An order still without a rate keeps none when its new date has none either: a date
            # can be corrected on an order in a currency the ECB does not quote.
    if payload.lines is not None:
        existing = {ln.line_no: ln for ln in po.lines}
        for ln_in in payload.lines:
            if ln_in.line_no in existing:
                line = existing[ln_in.line_no]
                loaded = Decimal(
                    str(
                        t.db.scalar(
                            select(func.coalesce(func.sum(ContainerLoad.quantity), 0)).where(
                                ContainerLoad.po_line_id == line.id
                            )
                        )
                    )
                )
                if ln_in.quantity < loaded:
                    raise Unprocessable(
                        f"Line {ln_in.line_no}: {loaded} already loaded in containers, "
                        "quantity cannot drop below",
                        code="OVER_ALLOCATED",
                    )
                for k, v in ln_in.model_dump().items():
                    setattr(line, k, v)
            else:
                added = t.add(PurchaseOrderLine(purchase_order=po, **ln_in.model_dump()))
                if ln_in.sku:
                    known = products.by_sku(t.db, t.org_id, {ln_in.sku})
                    products.fill_blanks(added, known.get(ln_in.sku))
    after = header(po)
    moved = [key for key in after if after[key] != before[key]]
    if moved:
        record(
            t.db,
            t.org_id,
            actor_user_id=p.user_id,
            action=PURCHASE_ORDER_UPDATED,
            entity_type="purchase_order",
            entity_id=po.id,
            before={key: before[key] for key in moved},
            after={key: after[key] for key in moved},
        )
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()
    return _to_response(t, _load_po(t, po.id))


@router.delete("/{po_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_purchase_order(po_id: UUID, t: TenantDep, _: WriterDep) -> None:
    po = _load_po(t, po_id)
    if any(line.loads for line in po.lines):
        raise Conflict("Purchase order has lines loaded in containers; unload them first")
    t.db.delete(po)
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()
