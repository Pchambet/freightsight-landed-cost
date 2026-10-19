"""An order's exchange rate: where it came from, which ones a person still has to look at, and the
missing ones the provider can give later.

An order's rate moves every landed cost it carries, so each writer says where it took it from
(`PurchaseOrder.fx_source`). Two kinds are a person's word and are never replaced by a lookup: a rate
typed on the order and a rate given in the order file.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.domain.audit.service import record
from app.domain.fx.service import FxService, ResolvedRate
from app.domain.models import Organization, PurchaseOrder

#: An order's header was edited — its exchange rate above all: it moves every landed cost the order
#: carries, closed months' drift included, so the log keeps the rate before and after and its source.
PURCHASE_ORDER_UPDATED = "purchase_order.updated"

#: Typed on the order, or given by the order file.
MANUAL, IMPORT = "manual", "import"
TYPED = frozenset({MANUAL, IMPORT})


def rate_unconfirmed(po: PurchaseOrder, base_currency: str) -> bool:
    """A foreign order at a rate of exactly 1, with no date and no source: the mark of the import that
    wrote 1 when it had no rate, before a missing rate was left empty. Dollars may be read as euros
    in its value; it stays to be checked until a person confirms or sets the rate, which records a
    source."""
    return (
        po.currency != base_currency
        and po.fx_rate == Decimal(1)
        and po.fx_date is None
        and po.fx_source is None
    )


def header(po: PurchaseOrder) -> dict[str, object]:
    """What the history keeps of an order's header, before and after a change."""
    return {
        "supplier": po.supplier.name if po.supplier else None,
        "incoterm": po.incoterm,
        "order_date": po.order_date,
        "fx_rate": po.fx_rate,
        "fx_date": po.fx_date,
        "fx_source": po.fx_source,
    }


def resolve_missing(db: Session, fx: FxService, org: Organization) -> list[PurchaseOrder]:
    """Give the orders still without a rate the one the provider now has, each with the entry a person
    would see in the history. Returns the orders that got one.

    A missing rate used to be retried only by a later import of the same file, and the ERP sync reads
    only what changed in the ERP: an order left without a rate during an outage stayed so. The day is
    the one a person gave the rate (`fx_date`, kept when its lookup failed), else the one the import
    asked about — the order date, else the day the order was recorded. A currency the provider does
    not quote stays missing, asked again at the next run.

    The orders are locked as they are read: a person setting a rate meanwhile would otherwise see it
    replaced by the provider's. One a person is editing right now is left to the next run.
    """
    orders = db.scalars(
        select(PurchaseOrder)
        .where(PurchaseOrder.org_id == org.id, PurchaseOrder.fx_rate.is_(None))
        .order_by(PurchaseOrder.po_number)
        .with_for_update(skip_locked=True)
    )
    asked: dict[tuple[str, date], ResolvedRate | None] = {}
    found = []
    for po in orders:
        on_date = po.fx_date or po.order_date or po.created_at.date()
        key = (po.currency, on_date)
        if key not in asked:
            try:
                asked[key] = fx.resolve(db, org.base_currency, po.currency, on_date)
            except Unprocessable:
                asked[key] = None
        resolved = asked[key]
        if resolved is None:
            continue
        before = header(po)
        po.fx_rate, po.fx_date, po.fx_source = resolved.rate, resolved.rate_date, resolved.source
        after = header(po)
        moved = [key for key in after if after[key] != before[key]]
        record(
            db,
            org.id,
            actor_user_id=None,
            action=PURCHASE_ORDER_UPDATED,
            entity_type="purchase_order",
            entity_id=po.id,
            before={key: before[key] for key in moved},
            after={key: after[key] for key in moved},
        )
        found.append(po)
    return found
