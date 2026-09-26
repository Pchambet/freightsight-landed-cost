from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.v1 import schemas
from app.api.v1.deps import WriterDep
from app.core.errors import Conflict
from app.core.tenancy import TenantDep
from app.domain.models import PurchaseOrder, Supplier

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("", response_model=list[schemas.SupplierResponse])
def list_suppliers(t: TenantDep) -> list[Supplier]:
    return list(t.db.scalars(t.q(Supplier).order_by(Supplier.name)))


@router.post("", response_model=schemas.SupplierResponse, status_code=status.HTTP_201_CREATED)
def create_supplier(payload: schemas.SupplierCreate, t: TenantDep, _: WriterDep) -> Supplier:
    if t.db.scalar(t.q(Supplier).where(Supplier.name == payload.name)):
        raise Conflict("A supplier with this name already exists")
    sup = t.add(Supplier(**payload.model_dump()))
    t.db.commit()
    t.db.refresh(sup)
    return sup


@router.patch("/{supplier_id}", response_model=schemas.SupplierResponse)
def update_supplier(
    supplier_id: UUID, payload: schemas.SupplierUpdate, t: TenantDep, _: WriterDep
) -> Supplier:
    sup = t.get_or_404(Supplier, supplier_id, "Supplier")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(sup, k, v)
    t.db.commit()
    t.db.refresh(sup)
    return sup


@router.delete("/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_supplier(supplier_id: UUID, t: TenantDep, _: WriterDep) -> None:
    sup = t.get_or_404(Supplier, supplier_id, "Supplier")
    if t.db.scalar(select(PurchaseOrder.id).where(PurchaseOrder.supplier_id == sup.id).limit(1)):
        raise Conflict("Supplier is referenced by purchase orders")
    t.db.delete(sup)
    t.db.commit()
