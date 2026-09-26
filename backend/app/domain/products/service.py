"""The product catalogue: filling the blanks of a new order line, and being built from old ones.

The rule that matters is in `fill_blanks`: the catalogue gives a line what the line does not say, at
the moment the line is written, and is never consulted again for it. A landed cost that has been
computed, shown and perhaps pushed to an ERP does not move because a catalogue entry was edited.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Product, PurchaseOrder, PurchaseOrderLine

#: What a catalogue entry and an order line both know, and the entry may therefore lend.
LENDABLE = ("description", "hs_code", "duty_rate", "unit_weight_kg", "unit_volume_cbm")


def by_sku(db: Session, org_id: UUID, skus: set[str]) -> dict[str, Product]:
    if not skus:
        return {}
    rows = db.scalars(select(Product).where(Product.org_id == org_id, Product.sku.in_(skus)))
    return {product.sku: product for product in rows}


def fill_blanks(line: PurchaseOrderLine, product: Product | None) -> list[str]:
    """Copy onto `line` what it leaves empty and the catalogue knows. Returns the fields it filled.

    A zero is a value (a duty-free article has a rate of 0): only None is a blank.
    """
    if product is None:
        return []
    filled = []
    for name in LENDABLE:
        if getattr(line, name) is None and getattr(product, name) is not None:
            setattr(line, name, getattr(product, name))
            filled.append(name)
    return filled


def create_from_orders(db: Session, org_id: UUID) -> tuple[int, int]:
    """One catalogue entry per article already ordered and not yet in the catalogue, carrying the
    latest thing each order said about it. Returns (created, skipped because already there)."""
    lines = db.execute(
        select(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrderLine.org_id == org_id, PurchaseOrderLine.sku.is_not(None))
        # Newest order first: for each field, the first value met is the most recent one known.
        .order_by(PurchaseOrder.order_date.desc().nulls_last(), PurchaseOrder.created_at.desc())
    ).scalars()
    known: dict[str, dict[str, object]] = {}
    for line in lines:
        assert line.sku is not None
        facts = known.setdefault(line.sku, {})
        for name in LENDABLE:
            value = getattr(line, name)
            if value is not None and name not in facts:
                facts[name] = value

    existing = set(by_sku(db, org_id, set(known)))
    for sku, facts in known.items():
        if sku not in existing:
            db.add(Product(org_id=org_id, sku=sku, **facts))
    db.flush()
    return len(known) - len(existing), len(existing)
