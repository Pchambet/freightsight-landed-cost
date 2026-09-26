"""One search box over everything a user refers to by name: a container, an order, an article, an
invoice, a supplier, a shipment.

People type what they have in front of them — "mscu 482", "po2026 14", "zhejiang", "tapis" — so a
reference is compared with its punctuation and spacing removed, and a name with its accents and case
removed. Matching happens here rather than in SQL on purpose: an importer has a few thousand of each
at most, the comparison rules are the product's own, and they stay the same on any database.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.models import Container, Invoice, PurchaseOrder, PurchaseOrderLine, Shipment, Supplier

Kind = Literal["container", "purchase_order", "sku", "invoice", "supplier", "shipment"]
#: The order results come in when they match equally well.
KINDS: tuple[Kind, ...] = ("container", "purchase_order", "sku", "invoice", "supplier", "shipment")
MIN_QUERY = 2


@dataclass(frozen=True)
class Hit:
    kind: Kind
    id: UUID | None
    label: str
    sublabel: str | None
    sku: str | None = None


def fold(text: str) -> str:
    """Lower case, no accents: "Zhèjiang" and "zhejiang" are the same supplier."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def squeeze(text: str) -> str:
    """A reference as typed from memory: "MSCU 482199-0", "po 2026/014" — letters and digits only."""
    return "".join(c for c in fold(text) if c.isalnum())


def _rank(needle: str, haystack: str) -> int | None:
    """0 the whole thing, 1 its beginning, 2 somewhere inside, None not at all."""
    if not needle or needle not in haystack:
        return None
    if haystack == needle:
        return 0
    return 1 if haystack.startswith(needle) else 2


def search(db: Session, org_id: UUID, query: str, limit: int = 20) -> list[Hit]:
    words, reference = fold(query).strip(), squeeze(query)
    if len(words) < MIN_QUERY:
        return []

    def by_reference(value: str | None) -> int | None:
        return _rank(reference, squeeze(value)) if value and reference else None

    def by_name(value: str | None) -> int | None:
        return _rank(words, fold(value)) if value else None

    def best(*ranks: int | None) -> int | None:
        found = [r for r in ranks if r is not None]
        return min(found) if found else None

    ranked: list[tuple[int, int, str, Hit]] = []

    def keep(rank: int | None, hit: Hit) -> None:
        if rank is not None:
            ranked.append((rank, KINDS.index(hit.kind), hit.label, hit))

    containers = db.execute(
        select(Container.id, Container.container_number, Shipment.reference)
        .outerjoin(Shipment, Shipment.id == Container.shipment_id)
        .where(Container.org_id == org_id, Container.archived_at.is_(None))
    )
    for id_, number, shipment_reference in containers:
        keep(by_reference(number), Hit("container", id_, number, shipment_reference))

    orders = db.execute(
        select(PurchaseOrder.id, PurchaseOrder.po_number, Supplier.name)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(PurchaseOrder.org_id == org_id)
    )
    for id_, number, supplier_name in orders:
        keep(by_reference(number), Hit("purchase_order", id_, number, supplier_name))

    # An article is known by its SKU, and remembered by what it is: "tapis" finds MAT-EVA-6040-GY.
    # The description that names it is the longest one seen, which is usually the most complete.
    articles: dict[str, str | None] = {}
    lines = db.execute(
        select(PurchaseOrderLine.sku, PurchaseOrderLine.description)
        .where(PurchaseOrderLine.org_id == org_id, PurchaseOrderLine.sku.is_not(None))
        .distinct()
    )
    for sku, description in lines:
        if len(description or "") >= len(articles.get(sku) or ""):
            articles[sku] = description or articles.get(sku)
    for sku, description in articles.items():
        rank = best(by_reference(sku), _shifted(by_name(description)))
        keep(rank, Hit("sku", None, sku, description, sku=sku))

    invoices = db.execute(
        select(Invoice.id, Invoice.invoice_number, Invoice.vendor).where(
            Invoice.org_id == org_id, Invoice.invoice_number.is_not(None)
        )
    )
    for id_, number, vendor in invoices:
        keep(by_reference(number), Hit("invoice", id_, number, vendor))

    for id_, name, country in db.execute(
        select(Supplier.id, Supplier.name, Supplier.country).where(Supplier.org_id == org_id)
    ):
        keep(by_name(name), Hit("supplier", id_, name, country))

    for id_, shipment_ref, carrier in db.execute(
        select(Shipment.id, Shipment.reference, Shipment.carrier_scac).where(Shipment.org_id == org_id)
    ):
        keep(by_reference(shipment_ref), Hit("shipment", id_, shipment_ref, carrier))

    ranked.sort(key=lambda entry: entry[:3])
    return [hit for *_, hit in ranked[:limit]]


def _shifted(rank: int | None) -> int | None:
    """A match on the description counts, but never ahead of a match on the reference itself."""
    return None if rank is None else min(rank + 1, 2)
