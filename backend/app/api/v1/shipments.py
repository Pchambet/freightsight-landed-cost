from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.v1 import schemas
from app.api.v1.deps import WriterDep
from app.core.errors import Conflict
from app.core.tenancy import TenantDep
from app.domain.costing.service import recompute_org
from app.domain.models import Container, Shipment

router = APIRouter(prefix="/shipments", tags=["shipments"])


@router.get("", response_model=list[schemas.ShipmentResponse])
def list_shipments(t: TenantDep) -> list[Shipment]:
    return list(t.db.scalars(t.q(Shipment).order_by(Shipment.reference)))


@router.post("", response_model=schemas.ShipmentResponse, status_code=status.HTTP_201_CREATED)
def create_shipment(payload: schemas.ShipmentCreate, t: TenantDep, _: WriterDep) -> Shipment:
    if t.db.scalar(t.q(Shipment).where(Shipment.reference == payload.reference)):
        raise Conflict(f"Shipment {payload.reference} already exists")
    sh = t.add(Shipment(**payload.model_dump()))
    t.db.commit()
    t.db.refresh(sh)
    return sh


@router.get("/{shipment_id}", response_model=schemas.ShipmentResponse)
def get_shipment(shipment_id: UUID, t: TenantDep) -> Shipment:
    return t.get_or_404(Shipment, shipment_id, "Shipment")


@router.patch("/{shipment_id}", response_model=schemas.ShipmentResponse)
def update_shipment(
    shipment_id: UUID, payload: schemas.ShipmentUpdate, t: TenantDep, _: WriterDep
) -> Shipment:
    sh = t.get_or_404(Shipment, shipment_id, "Shipment")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(sh, k, v)
    t.db.commit()
    t.db.refresh(sh)
    return sh


@router.delete("/{shipment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shipment(shipment_id: UUID, t: TenantDep, _: WriterDep) -> None:
    sh = t.get_or_404(Shipment, shipment_id, "Shipment")
    for c in t.db.scalars(t.q(Container).where(Container.shipment_id == sh.id)):
        c.shipment_id = None
    t.db.delete(sh)
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()
