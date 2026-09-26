"""The product catalogue: one entry per article, holding what no single order knows."""

from __future__ import annotations

import base64
import binascii
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from sqlalchemy.exc import IntegrityError

from app.api.v1 import schemas
from app.api.v1.deps import WriterDep
from app.core.errors import Conflict, Unprocessable
from app.core.tenancy import TenantDep
from app.domain.models import Product
from app.domain.products.service import create_from_orders
from app.domain.search.service import fold

router = APIRouter(prefix="/products", tags=["products"])

TAKEN = "Another product of this organization already has this SKU"


def _cursor(sku: str) -> str:
    return base64.urlsafe_b64encode(sku.encode()).decode()


def _after(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, binascii.Error) as exc:
        raise Unprocessable("This cursor is not one of ours", code="INVALID_CURSOR") from exc


@router.get("", response_model=schemas.ProductPage)
def list_products(
    t: TenantDep,
    q: str | None = Query(default=None, max_length=80),
    cursor: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> schemas.ProductPage:
    """The catalogue in SKU order. `q` matches the SKU or the description, accents and case ignored."""
    stmt = t.q(Product).order_by(Product.sku)
    if cursor:
        stmt = stmt.where(Product.sku > _after(cursor))
    rows = list(t.db.scalars(stmt if q else stmt.limit(limit + 1)))
    if q:
        needle = fold(q).strip()
        rows = [p for p in rows if needle in fold(p.sku) or needle in fold(p.description or "")]
    page = rows[:limit]
    return schemas.ProductPage(
        products=[schemas.ProductResponse.model_validate(p) for p in page],
        next_cursor=_cursor(page[-1].sku) if len(rows) > limit and page else None,
    )


@router.post("", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def upsert_product(payload: schemas.ProductIn, response: Response, t: TenantDep, _: WriterDep) -> Product:
    """Create the entry, or update the one that already has this SKU (then 200, not 201): a catalogue
    is filled from files and from screens alike, and neither should have to ask first."""
    product = t.db.scalar(t.q(Product).where(Product.sku == payload.sku))
    if product is None:
        product = t.add(Product(**payload.model_dump()))
    else:
        for name, value in payload.model_dump(exclude_unset=True).items():
            setattr(product, name, value)
        response.status_code = status.HTTP_200_OK
    t.db.commit()
    t.db.refresh(product)
    return product


@router.post("/from-orders", response_model=schemas.ProductsFromOrders)
def products_from_orders(t: TenantDep, _: WriterDep) -> schemas.ProductsFromOrders:
    """One entry per article already ordered, carrying the latest description, tariff heading, duty
    rate, weight and volume any order gave it. Entries that exist are left exactly as they are."""
    created, skipped = create_from_orders(t.db, t.org_id)
    t.db.commit()
    return schemas.ProductsFromOrders(created=created, skipped=skipped)


@router.patch("/{product_id}", response_model=schemas.ProductResponse)
def update_product(product_id: UUID, payload: schemas.ProductUpdate, t: TenantDep, _: WriterDep) -> Product:
    product = t.get_or_404(Product, product_id, "Product")
    changes = payload.model_dump(exclude_unset=True)
    if "sku" in changes:
        sku = (changes["sku"] or "").strip()
        if not sku:
            raise Unprocessable("sku must not be blank", code="PRODUCT_SKU_BLANK")
        changes["sku"] = sku
    for name, value in changes.items():
        setattr(product, name, value)
    try:
        t.db.commit()
    except IntegrityError as exc:
        t.db.rollback()
        raise Conflict(TAKEN, code="PRODUCT_SKU_TAKEN") from exc
    t.db.refresh(product)
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: UUID, t: TenantDep, _: WriterDep) -> None:
    """Order lines keep what they copied: deleting an entry moves no landed cost."""
    t.db.delete(t.get_or_404(Product, product_id, "Product"))
    t.db.commit()
