"""Bridge between the ORM and the pure engine.

`compute()` builds the engine inputs for one organization and runs it. `recompute_org()` does the same and
materialises the result into `cost_allocations`, in the caller's transaction. A full-organization
recompute is simpler and safer than scope tracking, and it is measured rather than assumed: about
0.4 s at 3 000 loads and 1 500 costs, five years of a sixty-containers-a-year importer
(`tests/test_scale.py` holds the budget).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from app.core.money import q4
from app.domain.costing import engine
from app.domain.models import (
    AllocationMethod,
    Container,
    ContainerLoad,
    Cost,
    CostAllocation,
    CostType,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
)


@dataclass
class Computation:
    loads: list[engine.Load]
    costs: list[engine.Cost]
    result: engine.AllocationResult
    load_rows: dict[UUID, ContainerLoad]
    #: Only the costs that are allocated: an estimate that has been replaced or closed is not here.
    cost_rows: dict[UUID, Cost]
    #: `(actual, the estimate it replaced)`, which is where the variance comes from.
    superseded: list[tuple[Cost, Cost]]
    containers: dict[UUID, Container]
    lines: dict[UUID, PurchaseOrderLine]
    pos: dict[UUID, PurchaseOrder]


def _load_rows(db: Session, org_id: UUID) -> list[ContainerLoad]:
    stmt = (
        select(ContainerLoad)
        .where(ContainerLoad.org_id == org_id)
        .options(
            selectinload(ContainerLoad.container),
            # The supplier comes along because the landed-cost report groups by it, and reaching it
            # through the order one bucket at a time was a query per supplier.
            selectinload(ContainerLoad.po_line)
            .selectinload(PurchaseOrderLine.purchase_order)
            .selectinload(PurchaseOrder.supplier),
        )
    )
    return list(db.scalars(stmt))


def _cost_rows(db: Session, org_id: UUID) -> list[Cost]:
    return list(db.scalars(select(Cost).where(Cost.org_id == org_id)))


def live_and_superseded(rows: list[Cost]) -> tuple[list[Cost], list[tuple[Cost, Cost]]]:
    """Split the organization's costs into what is allocated now and what has been replaced.

    An estimate stands in for an invoice until that invoice arrives — that is the whole point, a
    landed cost that is right on the day the box lands. Once the real cost supersedes it, the
    estimate leaves the allocation but stays in the database: it is what the variance is measured
    against. An estimate closed without an invoice leaves too, and measures nothing.
    """
    by_id = {row.id: row for row in rows}
    pairs = [
        (row, by_id[row.supersedes_cost_id])
        for row in rows
        if row.supersedes_cost_id is not None and row.supersedes_cost_id in by_id
    ]
    replaced = {estimate.id for _, estimate in pairs}
    live = [row for row in rows if row.id not in replaced and row.closed_at is None]
    return live, pairs


def to_engine_load(row: ContainerLoad) -> engine.Load:
    line = row.po_line
    po = line.purchase_order
    return engine.Load(
        id=row.id,
        container_id=row.container_id,
        shipment_id=row.container.shipment_id,
        po_id=po.id,
        po_line_id=line.id,
        quantity=row.quantity,
        unit_price_base=q4(line.unit_price * po.fx_rate),
        unit_weight_kg=line.unit_weight_kg,
        unit_volume_cbm=line.unit_volume_cbm,
        duty_rate=line.duty_rate,
        sort_key=(row.container.container_number, po.po_number, line.line_no),
    )


def to_engine_cost(row: Cost, method_override: str | None = None) -> engine.Cost:
    target = row.shipment_id or row.container_id or row.po_id or row.po_line_id
    assert target is not None
    splits = tuple(
        engine.ManualSplit(s["target_type"], UUID(str(s["target_id"])), Decimal(str(s["pct"])))
        for s in (row.manual_splits or [])
    )
    method = method_override or row.allocation_method.value
    if method != "MANUAL":
        splits = ()
    return engine.Cost(
        id=row.id,
        scope=row.scope.value,
        target_id=target,
        cost_type=row.cost_type.value,
        amount_base=row.amount_base,
        method=method,
        manual_splits=splits,
    )


def compute(
    db: Session,
    org: Organization,
    *,
    method_overrides: Mapping[CostType, AllocationMethod] | None = None,
) -> Computation:
    """Run the engine over the whole organization. `method_overrides` (per cost type) is for previews."""
    load_rows = _load_rows(db, org.id)
    all_costs = _cost_rows(db, org.id)
    cost_rows, superseded = live_and_superseded(all_costs)
    overrides = method_overrides or {}
    loads = [to_engine_load(r) for r in load_rows]
    costs = [
        to_engine_cost(r, overrides[r.cost_type].value if r.cost_type in overrides else None)
        for r in cost_rows
    ]
    result = engine.allocate(costs, loads, fallback_method=org.default_allocation_method.value)
    return Computation(
        loads=loads,
        costs=costs,
        result=result,
        load_rows={r.id: r for r in load_rows},
        cost_rows={r.id: r for r in cost_rows},
        superseded=superseded,
        containers={r.container.id: r.container for r in load_rows},
        lines={r.po_line.id: r.po_line for r in load_rows},
        pos={r.po_line.purchase_order.id: r.po_line.purchase_order for r in load_rows},
    )


def recompute_org(db: Session, org: Organization) -> Computation:
    """Recompute and materialise allocations for the organization, inside the current transaction.

    Also records today's landed unit cost per SKU: the curve customers ask for is a by-product of
    every recompute, and computing it later from the allocations would be the same numbers derived a
    second way.
    """
    from datetime import UTC, datetime

    from app.domain.reporting.service import record_sku_costs

    comp = compute(db, org)
    db.execute(delete(CostAllocation).where(CostAllocation.org_id == org.id))
    db.add_all(
        CostAllocation(
            org_id=org.id, cost_id=a.cost_id, container_load_id=a.load_id, amount_base=a.amount_base
        )
        for a in comp.result.allocations
    )
    db.flush()
    record_sku_costs(db, comp, org, datetime.now(UTC).date())
    return comp
