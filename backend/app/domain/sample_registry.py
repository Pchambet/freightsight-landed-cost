"""Taking the demo dataset away again.

What is the demo's is what the register says (`sample_objects`), plus what only exists because of it:
the costs, alerts and tracking events of a demo container die with the container, whoever created
them — a cost typed on a container that never existed is part of playing with the demo. What is *not*
the demo's, and stops the deletion, is real data tied to it: a line of the user's own order loaded in
a demo container, a cost that came from an invoice the user uploaded, a draft pushed to their ERP.
Those are refused by name rather than deleted along.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Delete, delete, select
from sqlalchemy.orm import Session

from app.domain.costing.service import recompute_org
from app.domain.documents.ports import DocumentStore
from app.domain.models import (
    Alert,
    Container,
    ContainerLoad,
    Cost,
    Document,
    ErpPush,
    EtaHistory,
    Invoice,
    InvoiceLine,
    Organization,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    RateCard,
    SampleObject,
    Shipment,
    SkuCostHistory,
    Supplier,
    TrackingEvent,
    TrackingSubscription,
)

#: What the dataset creates, and the name each kind goes by in the register.
REGISTERED = {
    "supplier": Supplier,
    "purchase_order": PurchaseOrder,
    "po_line": PurchaseOrderLine,
    "shipment": Shipment,
    "container": Container,
    "container_load": ContainerLoad,
    "cost": Cost,
    "rate_card": RateCard,
    "document": Document,
    "invoice": Invoice,
    "alert": Alert,
}
#: Registered one by one by the dataset that creates them (`register`), never swept up by
#: `register_everything`: an organization with no order can already hold its own product sheets.
NAMED = {"product": Product}


@dataclass(frozen=True)
class Blocker:
    code: str
    #: The container or order number a person would recognise.
    field: str


def register_everything(db: Session, org_id: UUID) -> int:
    """Call right after the dataset is loaded: the organization was empty, so all of it is the demo's."""
    known = {
        (kind, object_id)
        for kind, object_id in db.execute(
            select(SampleObject.kind, SampleObject.object_id).where(SampleObject.org_id == org_id)
        )
    }
    added = 0
    for kind, model in REGISTERED.items():
        for object_id in db.scalars(select(model.id).where(model.org_id == org_id)):  # type: ignore[attr-defined]
            if (kind, object_id) not in known:
                db.add(SampleObject(org_id=org_id, kind=kind, object_id=object_id))
                added += 1
    db.flush()
    return added


def register(db: Session, org_id: UUID, kind: str, object_ids: Iterable[UUID]) -> None:
    """Mark as the demo's what the dataset created where the user may already have things of that kind."""
    db.add_all(SampleObject(org_id=org_id, kind=kind, object_id=object_id) for object_id in object_ids)
    db.flush()


def has_sample_data(db: Session, org_id: UUID) -> bool:
    return db.scalar(select(SampleObject.id).where(SampleObject.org_id == org_id).limit(1)) is not None


def _ids(db: Session, org_id: UUID) -> dict[str, set[UUID]]:
    found: dict[str, set[UUID]] = {kind: set() for kind in (*REGISTERED, *NAMED)}
    rows = db.execute(select(SampleObject.kind, SampleObject.object_id).where(SampleObject.org_id == org_id))
    for kind, object_id in rows:
        found.setdefault(kind, set()).add(object_id)
    return found


def blockers(db: Session, org_id: UUID) -> list[Blocker]:
    ids = _ids(db, org_id)
    containers = {
        c.id: c.container_number
        for c in db.scalars(select(Container).where(Container.id.in_(ids["container"])))
    }
    orders = {
        po.id: po.po_number
        for po in db.scalars(select(PurchaseOrder).where(PurchaseOrder.id.in_(ids["purchase_order"])))
    }
    line_order: dict[UUID, UUID] = {
        line_id: po_id
        for line_id, po_id in db.execute(
            select(PurchaseOrderLine.id, PurchaseOrderLine.po_id).where(
                PurchaseOrderLine.id.in_(ids["po_line"])
            )
        )
    }
    found: list[Blocker] = []

    # The user's own goods in a demo container, or the demo's goods in the user's own container.
    loads = db.scalars(
        select(ContainerLoad).where(
            ContainerLoad.org_id == org_id,
            ContainerLoad.container_id.in_(ids["container"]) | ContainerLoad.po_line_id.in_(ids["po_line"]),
        )
    )
    for load in loads:
        ours = load.container_id in ids["container"], load.po_line_id in ids["po_line"]
        if all(ours):
            continue
        name = containers.get(load.container_id) or orders.get(line_order.get(load.po_line_id, load.id), "")
        found.append(Blocker("OWN_LOAD_ON_SAMPLE", name))

    # A cost that came out of an invoice the user uploaded themselves, sitting on a demo object.
    own_invoice_costs = db.execute(
        select(Cost)
        .join(InvoiceLine, InvoiceLine.cost_id == Cost.id)
        .where(Cost.org_id == org_id, InvoiceLine.invoice_id.not_in(ids["invoice"] or {UUID(int=0)}))
    ).scalars()
    for cost in own_invoice_costs:
        target = _target_name(cost, containers, orders, line_order, ids)
        if target is not None:
            found.append(Blocker("OWN_COST_ON_SAMPLE", target))

    pushes = db.scalars(
        select(ErpPush).where(ErpPush.org_id == org_id, ErpPush.container_id.in_(ids["container"]))
    )
    for push in pushes:
        found.append(Blocker("ERP_PUSH_ON_SAMPLE", containers.get(push.container_id, "")))

    unique = sorted({(b.code, b.field) for b in found})
    return [Blocker(code, name) for code, name in unique]


def _target_name(
    cost: Cost,
    containers: dict[UUID, str],
    orders: dict[UUID, str],
    line_order: dict[UUID, UUID],
    ids: dict[str, set[UUID]],
) -> str | None:
    """What demo object this cost sits on, by the name a person knows it by — or None if on none."""
    if cost.container_id in containers:
        return containers[cost.container_id]
    if cost.po_id in orders:
        return orders[cost.po_id]
    if cost.po_line_id in line_order:
        return orders.get(line_order[cost.po_line_id], "")
    if cost.shipment_id in ids["shipment"]:
        return ""
    return None


def delete_sample_data(db: Session, store: DocumentStore, org: Organization) -> Counter[str]:
    """Delete the demo dataset and everything that only existed because of it. The caller has
    checked `blockers()` and commits. Returns how many of each kind went."""
    ids = _ids(db, org.id)
    gone: Counter[str] = Counter()

    def remove(kind: str, statement: Delete) -> None:
        result = db.execute(statement)
        gone[kind] += getattr(result, "rowcount", 0) or 0

    on_demo = (
        Cost.container_id.in_(ids["container"])
        | Cost.shipment_id.in_(ids["shipment"])
        | Cost.po_id.in_(ids["purchase_order"])
        | Cost.po_line_id.in_(ids["po_line"])
    )
    remove("costs", delete(Cost).where(Cost.org_id == org.id, on_demo | Cost.id.in_(ids["cost"])))

    documents = list(
        db.scalars(select(Document).where(Document.org_id == org.id, Document.id.in_(ids["document"])))
    )
    remove("invoices", delete(Invoice).where(Invoice.org_id == org.id, Invoice.id.in_(ids["invoice"])))
    for document in documents:
        still_used = db.scalar(select(Invoice.id).where(Invoice.document_id == document.id).limit(1))
        if still_used is None:
            store.delete(org.id, document.storage_key)
            db.delete(document)
    db.flush()

    in_demo_box = ids["container"]
    remove("alerts", delete(Alert).where(Alert.org_id == org.id, Alert.container_id.in_(in_demo_box)))
    for model in (TrackingEvent, EtaHistory, TrackingSubscription):
        db.execute(delete(model).where(model.org_id == org.id, model.container_id.in_(in_demo_box)))
    db.execute(
        delete(ContainerLoad).where(
            ContainerLoad.org_id == org.id,
            ContainerLoad.container_id.in_(in_demo_box) | ContainerLoad.po_line_id.in_(ids["po_line"]),
        )
    )
    remove("containers", delete(Container).where(Container.org_id == org.id, Container.id.in_(in_demo_box)))
    remove("shipments", delete(Shipment).where(Shipment.org_id == org.id, Shipment.id.in_(ids["shipment"])))
    db.execute(
        delete(PurchaseOrderLine).where(
            PurchaseOrderLine.org_id == org.id, PurchaseOrderLine.po_id.in_(ids["purchase_order"])
        )
    )
    remove(
        "purchase_orders",
        delete(PurchaseOrder).where(
            PurchaseOrder.org_id == org.id, PurchaseOrder.id.in_(ids["purchase_order"])
        ),
    )
    remove("rate_cards", delete(RateCard).where(RateCard.org_id == org.id, RateCard.id.in_(ids["rate_card"])))
    # A demo supplier the user has since ordered from is theirs now.
    kept = set(db.scalars(select(PurchaseOrder.supplier_id).where(PurchaseOrder.org_id == org.id)))
    remove(
        "suppliers",
        delete(Supplier).where(Supplier.org_id == org.id, Supplier.id.in_(ids["supplier"] - kept)),
    )

    still_ordered = select(PurchaseOrderLine.sku).where(
        PurchaseOrderLine.org_id == org.id, PurchaseOrderLine.sku.is_not(None)
    )
    db.execute(
        delete(SkuCostHistory).where(
            SkuCostHistory.org_id == org.id, SkuCostHistory.sku.not_in(still_ordered)
        )
    )
    # And, like a supplier, a demo product sheet whose article the user's own orders now carry.
    remove(
        "products",
        delete(Product).where(
            Product.org_id == org.id, Product.id.in_(ids["product"]), Product.sku.not_in(still_ordered)
        ),
    )
    db.execute(delete(SampleObject).where(SampleObject.org_id == org.id))
    db.flush()
    db.expire_all()
    recompute_org(db, org)
    return gone
