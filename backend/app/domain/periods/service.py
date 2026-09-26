"""Closing a month, and saying what moved since.

A close freezes, line by line, the landed cost of the containers that arrived in the month — the
figure that went into the accounts. Nothing is locked afterwards: the forwarder's invoice that
arrives six weeks late is a fact, and refusing to record it would make the product lie about the
container. What the close guarantees is that the closed figure never moves again, and that the
difference between it and today's figure is a number of its own, per container, with the costs that
caused it: that is what gets booked as an adjustment in the open month.

A month is the month a container *arrived* in (discharge, else arrival, else the shipment's ETA), the
same rule as every report.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.money import q2, q4
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation
from app.domain.models import (
    AuditLog,
    Cost,
    CostStatus,
    Invoice,
    InvoiceStatus,
    Organization,
    PeriodClose,
    PeriodCloseAccrual,
    User,
)
from app.domain.models import PeriodCloseLine as FrozenLine
from app.domain.reporting.service import arrival_date

ZERO = Decimal("0.00")


@dataclass
class LiveLine:
    load_id: UUID
    container_id: UUID
    container_number: str
    arrived_on: date
    po_number: str
    sku: str | None
    description: str | None
    quantity: Decimal
    fob: Decimal
    landed: Decimal
    by_cost_type: dict[str, Decimal]
    estimated: Decimal


@dataclass
class Summary:
    period: str
    status: str  # open | closed
    containers: int
    fob: Decimal
    landed: Decimal
    coefficient: Decimal | None
    by_cost_type: dict[str, Decimal]
    closed_at: datetime | None = None
    closed_by_name: str | None = None
    #: Today's landed cost of the month. Equal to `landed` while the month is open.
    live_landed: Decimal = ZERO
    #: Live landed cost minus the frozen one, and how many containers it comes from. None while open.
    drift: Decimal | None = None
    drift_containers: int | None = None
    #: What of the drift has been booked as an adjustment, and what is still asking to be.
    drift_acknowledged: Decimal | None = None
    drift_outstanding: Decimal | None = None
    acknowledged_at: datetime | None = None
    acknowledged_note: str | None = None


@dataclass
class EstimatedBox:
    container_id: UUID
    container_number: str
    estimated: Decimal


@dataclass
class Readiness:
    """What is better settled before closing. Never a refusal: the month is the accountant's to close."""

    containers_with_estimates: list[EstimatedBox]
    #: What of this month's landed cost still rests on estimates: the figure that decides whether to
    #: close now or wait for the invoices.
    estimated_total: Decimal
    invoices_to_review: int
    unallocated_costs: int
    containers_without_cost: int


@dataclass
class ChangedCost:
    cost_id: UUID
    cost_type: str
    vendor: str | None
    invoice_number: str | None
    amount_base: Decimal
    changed_at: datetime


@dataclass
class Drift:
    container_id: UUID
    container_number: str
    frozen_landed: Decimal
    live_landed: Decimal
    difference: Decimal
    #: costs: its costs or its loads changed. left_period / joined_period: its arrival date did, and
    #: it now lands in another month — or in this one, which it was not in when the month was closed.
    reason: str = "costs"
    costs_changed: list[ChangedCost] = field(default_factory=list)
    costs_deleted: int = 0


@dataclass
class Accrual:
    """Received, not yet invoiced: what an estimate still stands for on a container that has landed."""

    container_id: UUID
    container_number: str
    arrived_on: date | None
    cost_type: str
    amount_base: Decimal


@dataclass
class Detail:
    summary: Summary
    readiness: Readiness | None
    drift_lines: list[Drift]
    #: Frozen for a closed month, today's for an open one.
    accruals: list[Accrual] = field(default_factory=list)
    accruals_total: Decimal = ZERO


def period_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def live_lines(comp: Computation) -> dict[str, list[LiveLine]]:
    """Today's landed cost, line by line, filed under the month each container arrived in."""
    per_load: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    estimated: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if cost.cost_type.value in EXCLUDED_FROM_LANDED:
            continue
        per_load[allocation.load_id][cost.cost_type.value] += allocation.amount_base
        if cost.status is CostStatus.ESTIMATE:
            estimated[allocation.load_id] += allocation.amount_base

    months: dict[str, list[LiveLine]] = defaultdict(list)
    for load in comp.loads:
        row = comp.load_rows[load.id]
        landed_on = arrival_date(row.container)
        if landed_on is None:
            continue
        by_type = {k: q2(v) for k, v in sorted(per_load.get(load.id, {}).items())}
        months[period_of(landed_on)].append(
            LiveLine(
                load_id=load.id,
                container_id=row.container_id,
                container_number=row.container.container_number,
                arrived_on=landed_on,
                po_number=row.po_line.purchase_order.po_number,
                sku=row.po_line.sku,
                description=row.po_line.description,
                quantity=load.quantity,
                fob=q2(load.fob),
                landed=q2(load.fob) + sum(by_type.values(), ZERO),
                by_cost_type=by_type,
                estimated=q2(estimated.get(load.id, ZERO)),
            )
        )
    return months


def _totals(lines: list[LiveLine] | list[FrozenLine]) -> tuple[int, Decimal, Decimal, dict[str, Decimal]]:
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for line in lines:
        for cost_type, amount in line.by_cost_type.items():
            by_type[cost_type] += Decimal(str(amount))
    fob = sum((line.fob for line in lines), ZERO)
    landed = sum((line.landed for line in lines), ZERO)
    return len({line.container_id for line in lines}), q2(fob), q2(landed), dict(sorted(by_type.items()))


def _closes(db: Session, org: Organization) -> dict[str, PeriodClose]:
    rows = db.scalars(
        select(PeriodClose)
        .where(PeriodClose.org_id == org.id)
        .options(selectinload(PeriodClose.lines), selectinload(PeriodClose.accruals))
    )
    return {close.period: close for close in rows}


def _name(db: Session, user_id: UUID | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return (user.name or user.email) if user is not None else None


def _summary(
    db: Session,
    comp: Computation,
    org: Organization,
    period: str,
    live: list[LiveLine],
    close: PeriodClose | None,
) -> Summary:
    if close is None:
        containers, fob, landed, by_type = _totals(live)
        coefficient = q4(landed / fob) if fob else None
        return Summary(period, "open", containers, fob, landed, coefficient, by_type, live_landed=landed)
    # The drift of the list is the sum of the drift lines of the detail: the two cannot disagree.
    moved = _drift(db, comp, org, close, live)
    drift = q2(sum((line.difference for line in moved), ZERO))
    return Summary(
        period,
        "closed",
        close.containers,
        close.fob,
        close.landed,
        q4(close.landed / close.fob) if close.fob else None,
        {k: Decimal(str(v)) for k, v in close.by_cost_type.items()},
        closed_at=close.closed_at,
        closed_by_name=_name(db, close.closed_by),
        live_landed=_totals(live)[2],
        drift=drift,
        drift_containers=len(moved),
        drift_acknowledged=close.acknowledged_drift,
        drift_outstanding=q2(drift - close.acknowledged_drift),
        acknowledged_at=close.acknowledged_at,
        acknowledged_note=close.acknowledged_note,
    )


def summaries(db: Session, comp: Computation, org: Organization) -> list[Summary]:
    live, closes = live_lines(comp), _closes(db, org)
    months = sorted(set(live) | set(closes), reverse=True)
    return [_summary(db, comp, org, month, live.get(month, []), closes.get(month)) for month in months]


def detail(db: Session, comp: Computation, org: Organization, period: str) -> Detail:
    live = live_lines(comp).get(period, [])
    close = _closes(db, org).get(period)
    if close is None and not live:
        raise NotFound("Period", code="PERIOD_EMPTY")
    summary = _summary(db, comp, org, period, live, close)
    if close is None:
        pending = accruals_at(comp, period)
        return Detail(summary, _readiness(db, comp, org, live), [], pending, _sum(pending))
    frozen = [
        Accrual(a.container_id, a.container_number, a.arrived_on, a.cost_type, a.amount_base)
        for a in close.accruals
    ]
    return Detail(summary, None, _drift(db, comp, org, close, live), frozen, _sum(frozen))


def _sum(accruals: list[Accrual]) -> Decimal:
    return q2(sum((a.amount_base for a in accruals), ZERO))


def month_end(period: str) -> date:
    year, month = int(period[:4]), int(period[5:7])
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def accruals_at(comp: Computation, period: str) -> list[Accrual]:
    """The invoices not yet received at the end of `period`: every estimate no invoice has replaced,
    on a container that had landed by the last day of the month — this month or an earlier one, since
    a forwarder six weeks late is late across two closings. Import VAT is not a charge, and is left out.

    It is today's view of that date: an estimate invoiced since is no longer in it. The close freezes it.
    """
    last_day = month_end(period)
    found: dict[tuple[UUID, str], Accrual] = {}
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if cost.status is not CostStatus.ESTIMATE or cost.cost_type.value in EXCLUDED_FROM_LANDED:
            continue
        container = comp.load_rows[allocation.load_id].container
        landed_on = arrival_date(container)
        if landed_on is None or landed_on > last_day:
            continue
        if container.discharged_at is None and container.ata is None:
            continue  # an ETA is a plan: nothing has been received yet
        key = (container.id, cost.cost_type.value)
        entry = found.setdefault(
            key, Accrual(container.id, container.container_number, landed_on, cost.cost_type.value, ZERO)
        )
        entry.amount_base += allocation.amount_base
    for entry in found.values():
        entry.amount_base = q2(entry.amount_base)
    return sorted(found.values(), key=lambda a: (a.container_number, a.cost_type))


def _readiness(db: Session, comp: Computation, org: Organization, live: list[LiveLine]) -> Readiness:
    estimated: dict[UUID, EstimatedBox] = {}
    for line in live:
        if line.estimated > 0:
            box = estimated.setdefault(
                line.container_id, EstimatedBox(line.container_id, line.container_number, ZERO)
            )
            box.estimated += line.estimated
    in_month = {line.container_id for line in live}
    costed = {comp.load_rows[a.load_id].container_id for a in comp.result.allocations}
    to_review = db.scalars(
        select(Invoice.id).where(Invoice.org_id == org.id, Invoice.status == InvoiceStatus.NEEDS_REVIEW)
    )
    return Readiness(
        containers_with_estimates=sorted(estimated.values(), key=lambda b: b.container_number),
        estimated_total=q2(sum((box.estimated for box in estimated.values()), ZERO)),
        invoices_to_review=len(list(to_review)),
        unallocated_costs=len(comp.result.unallocated),
        containers_without_cost=len(in_month - costed),
    )


def _drift(
    db: Session, comp: Computation, org: Organization, close: PeriodClose, live: list[LiveLine]
) -> list[Drift]:
    frozen: dict[UUID, tuple[str, Decimal]] = {}
    for line in close.lines:
        number, total = frozen.get(line.container_id, (line.container_number, ZERO))
        frozen[line.container_id] = (number, total + line.landed)
    today: dict[UUID, tuple[str, Decimal]] = {}
    for current in live:
        number, total = today.get(current.container_id, (current.container_number, ZERO))
        today[current.container_id] = (number, total + current.landed)

    # The costs touched since the close, by the container their allocation lands on.
    touched: dict[UUID, dict[UUID, Cost]] = defaultdict(dict)
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if max(cost.created_at, cost.updated_at) > close.closed_at:
            touched[comp.load_rows[allocation.load_id].container_id][cost.id] = cost
    deleted: dict[UUID, int] = defaultdict(int)
    gone = db.scalars(
        select(AuditLog).where(
            AuditLog.org_id == org.id, AuditLog.action == "cost.deleted", AuditLog.at > close.closed_at
        )
    )
    for entry in gone:
        container = (entry.before or {}).get("container_id")
        if container:
            deleted[UUID(container)] += 1

    lines = []
    for container_id in sorted(set(frozen) | set(today), key=lambda c: (frozen.get(c) or today[c])[0]):
        number = (frozen.get(container_id) or today[container_id])[0]
        was = frozen[container_id][1] if container_id in frozen else ZERO
        now = today[container_id][1] if container_id in today else ZERO
        if q2(now - was) == 0:
            continue
        if container_id in frozen and container_id in today:
            reason = "costs"
        else:
            reason = "left_period" if container_id in frozen else "joined_period"
        changed = sorted(
            touched.get(container_id, {}).values(), key=lambda c: max(c.created_at, c.updated_at)
        )
        lines.append(
            Drift(
                container_id=container_id,
                container_number=number,
                frozen_landed=q2(was),
                live_landed=q2(now),
                difference=q2(now - was),
                reason=reason,
                costs_changed=[
                    ChangedCost(
                        cost_id=c.id,
                        cost_type=c.cost_type.value,
                        vendor=c.vendor,
                        invoice_number=c.invoice_number,
                        amount_base=c.amount_base,
                        changed_at=max(c.created_at, c.updated_at),
                    )
                    for c in changed
                ],
                costs_deleted=deleted.get(container_id, 0),
            )
        )
    return lines


def close_period(
    db: Session, comp: Computation, org: Organization, period: str, closed_by: UUID | None
) -> PeriodClose:
    today = datetime.now(UTC).date()
    if period > period_of(today):
        raise Unprocessable("A month that has not started cannot be closed", code="PERIOD_IN_FUTURE")
    if period in _closes(db, org):
        raise Conflict("This month is already closed", code="PERIOD_ALREADY_CLOSED")
    live = live_lines(comp).get(period, [])
    if not live:
        raise Unprocessable("No container arrived in this month", code="PERIOD_EMPTY")
    containers, fob, landed, by_type = _totals(live)
    close = PeriodClose(
        org_id=org.id,
        period=period,
        closed_by=closed_by,
        base_currency=org.base_currency,
        containers=containers,
        fob=fob,
        landed=landed,
        by_cost_type={k: f"{v:.2f}" for k, v in by_type.items()},
    )
    for line in live:
        close.lines.append(
            FrozenLine(
                org_id=org.id,
                container_id=line.container_id,
                container_number=line.container_number,
                arrived_on=line.arrived_on,
                load_id=line.load_id,
                po_number=line.po_number,
                sku=line.sku,
                description=line.description,
                quantity=line.quantity,
                fob=line.fob,
                landed=line.landed,
                by_cost_type={k: f"{v:.2f}" for k, v in line.by_cost_type.items()},
            )
        )
    for pending in accruals_at(comp, period):
        close.accruals.append(
            PeriodCloseAccrual(
                org_id=org.id,
                container_id=pending.container_id,
                container_number=pending.container_number,
                arrived_on=pending.arrived_on,
                cost_type=pending.cost_type,
                amount_base=pending.amount_base,
            )
        )
    db.add(close)
    db.flush()
    return close


def acknowledge_drift(
    db: Session, comp: Computation, org: Organization, period: str, by: UUID | None, note: str | None
) -> PeriodClose:
    """The drift as it stands has been booked as an adjustment in the open month. The closed month
    stops asking for it; a cost that arrives later moves the drift again, and it asks for the rest."""
    close = _closes(db, org).get(period)
    if close is None:
        raise NotFound("Closed period", code="PERIOD_NOT_CLOSED")
    moved = _drift(db, comp, org, close, live_lines(comp).get(period, []))
    close.acknowledged_drift = q2(sum((line.difference for line in moved), ZERO))
    close.acknowledged_at = datetime.now(UTC)
    close.acknowledged_by = by
    close.acknowledged_note = (note or "").strip() or None
    db.flush()
    return close


def reopen_period(db: Session, org: Organization, period: str) -> PeriodClose:
    close = _closes(db, org).get(period)
    if close is None:
        raise NotFound("Closed period", code="PERIOD_NOT_CLOSED")
    db.delete(close)
    db.flush()
    return close


def frozen_periods(db: Session, org_id: UUID) -> dict[UUID, str]:
    """Which close holds each container: a padlock in a list costs one query, not one per row."""
    rows = db.execute(
        select(FrozenLine.container_id, PeriodClose.period)
        .join(PeriodClose, PeriodClose.id == FrozenLine.close_id)
        .where(PeriodClose.org_id == org_id)
        .distinct()
    )
    return {container_id: period for container_id, period in rows}


@dataclass
class FrozenBox:
    period: str
    frozen_landed: Decimal
    drift: Decimal


def frozen_container(
    db: Session, org: Organization, container_id: UUID, computed: Callable[[], Computation]
) -> FrozenBox | None:
    """The close this container was frozen in, at how much, and what has moved since — whatever month
    it lands in today. The engine only runs for a container some close actually holds."""
    found = db.execute(
        select(PeriodClose.period, FrozenLine.landed)
        .join(FrozenLine, FrozenLine.close_id == PeriodClose.id)
        .where(PeriodClose.org_id == org.id, FrozenLine.container_id == container_id)
    ).all()
    if not found:
        return None
    frozen = sum((landed for _, landed in found), ZERO)
    live = sum(
        (
            line.landed
            for lines in live_lines(computed()).values()
            for line in lines
            if line.container_id == container_id
        ),
        ZERO,
    )
    return FrozenBox(found[0][0], q2(frozen), q2(live - frozen))
