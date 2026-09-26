"""The reports a finance person asks for, built on the same computation as everything else.

There is one engine and one set of allocations; these are views over it. Nothing here re-derives a
landed cost, because two ways of computing the same number is a guarantee that one of them is wrong.

The period axis is **when the goods landed** — the arrival date, falling back through what we know
about a container — rather than when the invoice was dated. A landed cost belongs to the shipment
that carried it, and a forwarder billing six weeks late must not move last quarter's numbers.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import q2, q4
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation
from app.domain.models import (
    Container,
    ContainerMilestone,
    Cost,
    CostStatus,
    CostType,
    DndRisk,
    Organization,
    SkuCostHistory,
)
from app.domain.tracking.calendar import local_date
from app.domain.tracking.state import port_zone

ZERO = Decimal("0.00")
UNKNOWN = "unknown"
#: What "freight" means in the freight-to-goods ratio.
FREIGHT_TYPES = {CostType.OCEAN_FREIGHT.value, CostType.AIR_FREIGHT.value}


@dataclass
class Bucket:
    key: str
    label: str
    fob: Decimal = ZERO
    allocated: Decimal = ZERO
    vat: Decimal = ZERO
    landed: Decimal = ZERO
    quantity: Decimal = Decimal(0)
    load_count: int = 0
    by_cost_type: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))

    @property
    def freight_share(self) -> Decimal | None:
        """Freight as a percentage of the goods. None when there are no goods to compare it to."""
        if self.fob <= 0:
            return None
        freight = sum((v for k, v in self.by_cost_type.items() if k in FREIGHT_TYPES), ZERO)
        return (freight / self.fob * Decimal(100)).quantize(Decimal("0.01"))


@dataclass
class SkuLine:
    sku: str
    quantity: Decimal
    fob: Decimal
    landed: Decimal
    unit_landed_cost: Decimal
    load_count: int


@dataclass
class LandedCostReport:
    base_currency: str
    group_by: str
    period_from: date | None
    period_to: date | None
    buckets: list[Bucket]
    skus: list[SkuLine]
    totals: Bucket


def arrival_date(container: Container) -> date | None:
    """When the goods landed, as well as this container knows.

    Discharge is the truth; arrival is the next best thing; a shipment's expected arrival is a plan
    but a dated one. A container with none of these is not in any month yet, and is left out rather
    than filed under today.
    """
    for moment in (container.discharged_at, container.ata):
        if moment is not None:
            return moment.date()
    if container.shipment is not None and container.shipment.eta is not None:
        return container.shipment.eta
    return None


def _route(container: Container) -> tuple[str, str]:
    shipment = container.shipment
    origin = (shipment.origin_unlocode if shipment else None) or None
    destination = (shipment.destination_unlocode if shipment else None) or None
    if origin and destination:
        key = f"{origin}-{destination}"
        return key, f"{origin} → {destination}"
    # Said plainly rather than dressed up: a route we do not know is not a route called "other".
    return UNKNOWN, "unknown route"


def landed_cost_report(
    comp: Computation,
    org: Organization,
    *,
    group_by: str = "supplier",
    period_from: date | None = None,
    period_to: date | None = None,
) -> LandedCostReport:
    per_load: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        per_load[allocation.load_id][cost.cost_type.value] += allocation.amount_base

    buckets: dict[str, Bucket] = {}
    skus: dict[str, SkuLine] = {}
    totals = Bucket(key="total", label="total")

    for load in comp.loads:
        row = comp.load_rows[load.id]
        container = row.container
        landed_on = arrival_date(container)
        if period_from and (landed_on is None or landed_on < period_from):
            continue
        if period_to and (landed_on is None or landed_on > period_to):
            continue

        by_type = per_load.get(load.id, {})
        vat = sum((v for k, v in by_type.items() if k in EXCLUDED_FROM_LANDED), ZERO)
        allocated = sum((v for k, v in by_type.items() if k not in EXCLUDED_FROM_LANDED), ZERO)

        key, label = _bucket_key(group_by, row, container, landed_on)
        for bucket in (buckets.setdefault(key, Bucket(key=key, label=label)), totals):
            bucket.fob += load.fob
            bucket.allocated += allocated
            bucket.vat += vat
            bucket.landed += load.fob + allocated
            bucket.quantity += load.quantity
            bucket.load_count += 1
            for cost_type, amount in by_type.items():
                bucket.by_cost_type[cost_type] += amount

        sku = row.po_line.sku
        if sku:
            line = skus.setdefault(sku, SkuLine(sku, Decimal(0), ZERO, ZERO, Decimal(0), 0))
            line.quantity += load.quantity
            line.fob += load.fob
            line.landed += load.fob + allocated
            line.load_count += 1

    if group_by == "cost_type":
        buckets = _by_cost_type(totals)

    for line in skus.values():
        # Weighted by quantity, which is what a weighted average is: the same SKU landed twice at
        # different costs is one number, not the mean of two.
        line.unit_landed_cost = q4(line.landed / line.quantity) if line.quantity else Decimal(0)
        line.fob, line.landed = q2(line.fob), q2(line.landed)

    return LandedCostReport(
        base_currency=org.base_currency,
        group_by=group_by,
        period_from=period_from,
        period_to=period_to,
        buckets=sorted((_rounded(b) for b in buckets.values()), key=lambda b: b.label),
        skus=sorted(skus.values(), key=lambda s: s.sku),
        totals=_rounded(totals),
    )


def _bucket_key(group_by: str, row: object, container: Container, landed_on: date | None) -> tuple[str, str]:
    match group_by:
        case "supplier":
            supplier = row.po_line.purchase_order.supplier  # type: ignore[attr-defined]
            name = supplier.name if supplier else None
            return (name or UNKNOWN, name or "unknown supplier")
        case "route":
            return _route(container)
        case "month":
            if landed_on is None:
                return (UNKNOWN, "no arrival date")
            return (f"{landed_on:%Y-%m}", f"{landed_on:%Y-%m}")
        case "cost_type":
            return ("all", "all")  # regrouped afterwards, from the totals
    raise ValueError(f"unknown group_by {group_by!r}")


def _by_cost_type(totals: Bucket) -> dict[str, Bucket]:
    """One bucket per charge, carrying the FOB it was charged on so shares stay computable."""
    out: dict[str, Bucket] = {}
    for cost_type, amount in totals.by_cost_type.items():
        bucket = Bucket(key=cost_type, label=cost_type, fob=totals.fob, load_count=totals.load_count)
        bucket.allocated = amount if cost_type not in EXCLUDED_FROM_LANDED else ZERO
        bucket.vat = amount if cost_type in EXCLUDED_FROM_LANDED else ZERO
        # The import-VAT row used to carry its amount as a landed cost of its own, in the one table
        # whose whole point is which charge weighs what. A recoverable tax weighs nothing there.
        bucket.landed = bucket.allocated
        bucket.by_cost_type[cost_type] = amount
        out[cost_type] = bucket
    return out


def _rounded(bucket: Bucket) -> Bucket:
    bucket.fob = q2(bucket.fob)
    bucket.allocated = q2(bucket.allocated)
    bucket.vat = q2(bucket.vat)
    bucket.landed = q2(bucket.landed)
    bucket.by_cost_type = {k: q2(v) for k, v in sorted(bucket.by_cost_type.items())}
    return bucket


# ---------------------------------------------------------------------------- SKU history


def record_sku_costs(db: Session, comp: Computation, org: Organization, on_date: date) -> int:
    """Write today's landed unit cost for every SKU. Returns how many SKUs were recorded.

    Called after each recompute. One row per SKU per day: recomputing twice on the same day corrects
    the day rather than adding a second point, so the curve is a history of facts about the goods,
    not of button presses.
    """
    per_load: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if cost.cost_type.value not in EXCLUDED_FROM_LANDED:
            per_load[allocation.load_id] += allocation.amount_base

    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), ZERO, ZERO, Decimal(0)])
    for load in comp.loads:
        sku = comp.load_rows[load.id].po_line.sku
        if not sku:
            continue
        entry = totals[sku]
        entry[0] += load.quantity
        entry[1] += load.fob
        entry[2] += load.fob + per_load.get(load.id, ZERO)
        entry[3] += 1

    existing = {
        row.sku: row
        for row in db.scalars(
            select(SkuCostHistory).where(
                SkuCostHistory.org_id == org.id, SkuCostHistory.recorded_on == on_date
            )
        )
    }
    for sku, (quantity, fob, landed, count) in totals.items():
        unit = q4(landed / quantity) if quantity else Decimal(0)
        row = existing.get(sku)
        if row is None:
            row = SkuCostHistory(org_id=org.id, sku=sku, recorded_on=on_date)
            db.add(row)
        row.quantity, row.fob_base, row.landed_base = quantity, q2(fob), q2(landed)
        row.unit_landed_cost, row.load_count = unit, int(count)
    db.flush()
    return len(totals)


def sku_history(db: Session, org: Organization, sku: str) -> list[SkuCostHistory]:
    return list(
        db.scalars(
            select(SkuCostHistory)
            .where(SkuCostHistory.org_id == org.id, SkuCostHistory.sku == sku)
            .order_by(SkuCostHistory.recorded_on)
        )
    )


# ---------------------------------------------------------------------------- demurrage


#: Why an avoided demurrage figure is what it is (see DndLine).
AvoidedRule = Literal["DND_AVOIDED_ESTIMATED", "DND_AVOIDED_NO_RATE", "DND_AVOIDED_NO_ALERT"]


@dataclass
class DndLine:
    container_id: UUID
    container_number: str
    paid: Decimal
    avoided: Decimal | None
    #: The same as `rule_code` and its values, in an English sentence: kept until the screens read the
    #: code, never to be shown.
    rule: str
    #: Why `avoided` is what it is. DND_AVOIDED_ESTIMATED: `days` between the first risk alert and the
    #: pickup, times `daily_rate`. DND_AVOIDED_NO_RATE: no DEMURRAGE rate card, so no figure.
    #: DND_AVOIDED_NO_ALERT: no risk alert or no pickup on record, nothing to estimate.
    rule_code: AvoidedRule
    days: int | None = None
    daily_rate: Decimal | None = None
    #: Days the box stayed on the terminal past its last free day, when both dates are known: the
    #: demurrage days that were paid for.
    days_over: int | None = None


@dataclass
class DndAtRiskLine:
    container_id: UUID
    container_number: str
    dnd_risk: DndRisk
    last_free_day: date | None
    rule: str
    #: Days already past the last free day, zero while it is still ahead; and how many are left.
    days_over: int = 0
    days_left: int | None = None
    #: The organization's own daily rate, and what the days already run come to at that rate. None
    #: without a rate card: there is no default, and no figure is made up.
    daily_rate: Decimal | None = None
    amount_at_risk: Decimal | None = None


@dataclass
class DndReport:
    base_currency: str
    period_from: date | None
    period_to: date | None
    paid: Decimal
    avoided: Decimal
    estimable: int
    not_estimable: int
    lines: list[DndLine]
    at_risk: list[DndAtRiskLine]


def dnd_report(
    db: Session,
    org: Organization,
    *,
    period_from: date | None = None,
    period_to: date | None = None,
    daily_rate: Decimal | None = None,
    basis: Literal["cost_date", "arrival"] = "cost_date",
) -> DndReport:
    """What demurrage and detention actually cost, and what acting in time plausibly saved.

    Paid is a fact: invoiced DEMURRAGE and DETENTION costs. Avoided is an estimate, and every line
    carries the rule that produced it — days between the first risk alert and the pickup, times the
    organization's own daily rate. Without a rate card there is no number at all, because a made-up
    saving is exactly the kind of figure that destroys trust in a report.

    `basis` says what puts a charge in the period. `cost_date`, the screen's: what was invoiced in it.
    `arrival`, the audit's: what the boxes that arrived in it cost, whenever it was invoiced —
    detention comes after the empty is returned, often in the next quarter.
    """
    by_invoice_date = basis == "cost_date"
    from app.domain.models import Alert, AlertKind

    charges = list(
        db.scalars(
            select(Cost).where(
                Cost.org_id == org.id,
                Cost.status == CostStatus.ACTUAL,
                Cost.closed_at.is_(None),
                Cost.cost_type.in_([CostType.DEMURRAGE, CostType.DETENTION]),
                *([Cost.cost_date >= period_from] if period_from and by_invoice_date else []),
                *([Cost.cost_date <= period_to] if period_to and by_invoice_date else []),
            )
        )
    )
    containers = {
        container.id: container
        for container in db.scalars(select(Container).where(Container.org_id == org.id))
    }
    alerts = db.scalars(
        select(Alert)
        .where(Alert.org_id == org.id, Alert.kind == AlertKind.DND_RISK)
        .order_by(Alert.created_at)
    )
    first_alert: dict[UUID, Alert] = {}
    for raised in alerts:
        if raised.container_id is not None:
            first_alert.setdefault(raised.container_id, raised)

    paid_by_container: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for charge in charges:
        if charge.container_id is not None:
            paid_by_container[charge.container_id] += charge.amount_base

    lines: list[DndLine] = []
    total_paid, total_avoided = ZERO, ZERO
    estimable = not_estimable = 0
    for container_id, container in containers.items():
        paid = paid_by_container.get(container_id, ZERO)
        alert = first_alert.get(container_id)
        picked_up = container.gate_out_at
        in_period = _in_period(container, period_from, period_to)
        if not by_invoice_date and not in_period:
            continue
        if paid == ZERO and (alert is None or picked_up is None or not in_period):
            continue

        avoided: Decimal | None = None
        days: int | None = None
        code: AvoidedRule
        if alert is None or picked_up is None:
            code = "DND_AVOIDED_NO_ALERT"
            rule = "no risk alert and no pickup recorded: nothing to estimate"
            not_estimable += 1
        elif daily_rate is None:
            code = "DND_AVOIDED_NO_RATE"
            rule = (
                "not estimable: add a DEMURRAGE rate card (its amount is read as a daily rate) "
                "and this becomes a number"
            )
            not_estimable += 1
        else:
            # Counted in the port's calendar, like the free days they are measured against: a box
            # collected at 00:14 in Le Havre was collected that day, whatever day it was in UTC.
            where = port_zone(container, org)
            days = max((local_date(picked_up, where) - local_date(alert.created_at, where)).days, 0)
            avoided = q2(Decimal(days) * daily_rate)
            code = "DND_AVOIDED_ESTIMATED"
            rule = f"{days} day(s) between the first risk alert and pickup x {daily_rate} per day"
            estimable += 1
            total_avoided += avoided

        total_paid += paid
        last_free = container.last_free_day
        lines.append(
            DndLine(
                container_id=container_id,
                container_number=container.container_number,
                paid=q2(paid),
                avoided=avoided,
                rule=rule,
                rule_code=code,
                days=days,
                daily_rate=daily_rate if code == "DND_AVOIDED_ESTIMATED" else None,
                days_over=(
                    max((local_date(picked_up, port_zone(container, org)) - last_free).days, 0)
                    if picked_up is not None and last_free is not None
                    else None
                ),
            )
        )

    at_risk: list[DndAtRiskLine] = []
    terminal_left = {
        ContainerMilestone.GATE_OUT_FULL,
        ContainerMilestone.DELIVERED,
        ContainerMilestone.GATE_IN_EMPTY_RETURN,
    }
    risky = {DndRisk.MEDIUM, DndRisk.HIGH, DndRisk.INCURRING}
    now = datetime.now(UTC)
    for container in containers.values():
        if container.archived_at is not None:
            continue
        if container.dnd_risk not in risky:
            continue
        if container.milestone in terminal_left or container.gate_out_at is not None:
            continue
        if not _in_period(container, period_from, period_to):
            continue
        lfd = container.last_free_day
        today = local_date(now, port_zone(container, org))  # the last free day's own calendar
        # A code, not a sentence. This one reached the report a CFO reads, in English and with an
        # ISO date, because it was written here as prose. The screen has `last_free_day` and today's
        # date already, so it can say it in the reader's language and their date format.
        if lfd is None:
            rule = "DND_AT_RISK_NO_LFD"
        elif (lfd - today).days < 0:
            rule = "DND_AT_RISK_OVERDUE"
        else:
            rule = "DND_AT_RISK_DAYS_LEFT"
        days_left = (lfd - today).days if lfd is not None else None
        days_over = max(-days_left, 0) if days_left is not None else 0
        at_risk.append(
            DndAtRiskLine(
                container_id=container.id,
                container_number=container.container_number,
                dnd_risk=container.dnd_risk,
                last_free_day=lfd,
                rule=rule,
                days_over=days_over,
                days_left=days_left,
                daily_rate=daily_rate,
                amount_at_risk=q2(Decimal(days_over) * daily_rate) if daily_rate is not None else None,
            )
        )

    return DndReport(
        base_currency=org.base_currency,
        period_from=period_from,
        period_to=period_to,
        paid=q2(total_paid),
        avoided=q2(total_avoided),
        estimable=estimable,
        not_estimable=not_estimable,
        lines=sorted(lines, key=lambda line: line.container_number),
        at_risk=sorted(at_risk, key=lambda line: line.last_free_day or date.max),
    )


def _in_period(container: Container, period_from: date | None, period_to: date | None) -> bool:
    landed_on = arrival_date(container)
    if landed_on is None:
        return period_from is None and period_to is None
    if period_from and landed_on < period_from:
        return False
    return not (period_to and landed_on > period_to)
