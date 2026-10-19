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
from app.domain.costing.engine import EXCLUDED_FROM_LANDED, Load
from app.domain.costing.service import Computation, charged_containers, held_containers
from app.domain.costing.value import coefficient, missing_code
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
    #: The value of every line, charged or not: what the goods cost is a fact. None while one of the
    #: lines has no value (see `costing.value`).
    fob: Decimal | None = ZERO
    allocated: Decimal = ZERO
    vat: Decimal = ZERO
    #: The landed cost of the lines whose container carries a charge (see `charged_containers`), and
    #: the value and quantity those lines make up. `landed` and `charged_fob` are None while one of
    #: those lines has no value, and when none of the bucket's containers carries a charge.
    landed: Decimal | None = ZERO
    charged_fob: Decimal | None = ZERO
    charged_quantity: Decimal = Decimal(0)
    charged_lines: int = 0
    quantity: Decimal = Decimal(0)
    load_count: int = 0
    by_cost_type: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    fob_missing_lines: int = 0
    #: The part of `landed` still resting on estimates, import VAT excluded: the audit's rule.
    estimated: Decimal = ZERO
    #: landed / charged_fob. Left None on a row of one charge, where it would be that charge's share.
    coefficient: Decimal | None = None
    #: The containers with goods here and no charge at all: in `fob`, in no landed figure.
    uncharged: set[UUID] = field(default_factory=set)
    #: Of those, the ones whose charges are entered but held, and the orders with no rate holding them
    #: (`held_containers`): never to be told as containers "with no charge".
    held: set[UUID] = field(default_factory=set)
    held_orders: set[str] = field(default_factory=set)
    #: The known value of the goods those containers carry, and how many of their lines have none: what
    #: a figure over the charged containers leaves out, given even while part of it is unknown.
    uncharged_fob: Decimal = ZERO
    uncharged_fob_missing_lines: int = 0

    @property
    def fob_missing(self) -> str | None:
        return missing_code(self.fob_missing_lines)

    @property
    def containers_without_charge(self) -> int:
        return len(self.uncharged)

    @property
    def containers_held(self) -> int:
        return len(self.held)

    @property
    def freight_share(self) -> Decimal | None:
        """Freight as a percentage of the goods it was charged on. None when there are none."""
        if self.charged_fob is None or self.charged_fob <= 0:
            return None
        freight = sum((v for k, v in self.by_cost_type.items() if k in FREIGHT_TYPES), ZERO)
        return (freight / self.charged_fob * Decimal(100)).quantize(Decimal("0.01"))

    def add(
        self,
        load: Load,
        box: UUID,
        charged: bool,
        allocated: Decimal,
        vat: Decimal,
        estimated: Decimal,
        held: tuple[str, ...] | None = None,
    ) -> None:
        """One line. An unknown value makes the bucket's FOB unknown, and its landed cost too when the
        line is charged; a line whose container carries no charge stays out of the landed figures."""
        self.allocated += allocated
        self.vat += vat
        self.estimated += estimated
        self.quantity += load.quantity
        self.load_count += 1
        if load.fob is None:
            self.fob_missing_lines += 1
            self.fob = None
        elif self.fob is not None:
            self.fob += load.fob
        if not charged:
            self.uncharged.add(box)
            if load.fob is None:
                self.uncharged_fob_missing_lines += 1
            else:
                self.uncharged_fob += load.fob
            if held is not None:
                self.held.add(box)
                self.held_orders.update(held)
            return
        self.charged_lines += 1
        self.charged_quantity += load.quantity
        if load.fob is None:
            self.landed = self.charged_fob = None
        elif self.landed is not None and self.charged_fob is not None:
            self.charged_fob += load.fob
            self.landed += load.fob + allocated

    def close(self) -> None:
        """Rounded, and with no landed figure at all when not one of its lines was charged: a sum over
        nothing would read as a landed cost of 0."""
        if self.load_count and not self.charged_lines:
            self.landed = self.charged_fob = None
        self.coefficient = coefficient(self.landed, self.charged_fob)
        _rounded(self)


@dataclass
class SkuLine:
    sku: str
    quantity: Decimal
    fob: Decimal | None
    #: Over the lines whose container carries a charge, like the buckets' (see `Bucket`).
    landed: Decimal | None
    unit_landed_cost: Decimal | None
    coefficient: Decimal | None
    load_count: int
    estimated: Decimal
    containers_without_charge: int
    fob_missing_lines: int = 0
    containers_held: int = 0
    held_orders: list[str] = field(default_factory=list)
    #: The quantity, and the unit purchase price, of the same goods as `unit_landed_cost`: the lines
    #: whose container carries a charge. A row that mixed both bases read 21,25 € bought and 30,95 €
    #: landed on two different sets of chairs.
    charged_quantity: Decimal = Decimal(0)
    unit_charged_fob: Decimal | None = None
    #: The goods' value of those same lines: what `landed` is to be read against.
    charged_fob: Decimal | None = None

    @property
    def fob_missing(self) -> str | None:
        return missing_code(self.fob_missing_lines)


@dataclass
class UnratedOrder:
    id: UUID
    po_number: str


@dataclass
class LandedCostReport:
    base_currency: str
    group_by: str
    period_from: date | None
    period_to: date | None
    buckets: list[Bucket]
    skus: list[SkuLine]
    totals: Bucket
    #: The orders of the period whose lines have no value: where the rate a screen asks for is entered.
    orders_without_fx_rate: list[UnratedOrder] = field(default_factory=list)


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
    estimated: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        per_load[allocation.load_id][cost.cost_type.value] += allocation.amount_base
        if cost.status is CostStatus.ESTIMATE and cost.cost_type.value not in EXCLUDED_FROM_LANDED:
            estimated[allocation.load_id][cost.cost_type.value] += allocation.amount_base
    charged = charged_containers(comp)
    held = held_containers(comp)

    buckets: dict[str, Bucket] = {}
    skus: dict[str, Bucket] = {}
    totals = Bucket(key="total", label="total")
    estimated_by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    unrated: dict[UUID, str] = {}

    for load in comp.loads:
        row = comp.load_rows[load.id]
        container = row.container
        landed_on = arrival_date(container)
        if period_from and (landed_on is None or landed_on < period_from):
            continue
        if period_to and (landed_on is None or landed_on > period_to):
            continue

        if load.fob is None:
            unrated[load.po_id] = row.po_line.purchase_order.po_number
        by_type = per_load.get(load.id, {})
        vat = sum((v for k, v in by_type.items() if k in EXCLUDED_FROM_LANDED), ZERO)
        allocated = sum((v for k, v in by_type.items() if k not in EXCLUDED_FROM_LANDED), ZERO)
        key, label = _bucket_key(group_by, row, container, landed_on)
        sku = row.po_line.sku
        targets = [buckets.setdefault(key, Bucket(key=key, label=label)), totals]
        if sku:
            targets.append(skus.setdefault(sku, Bucket(key=sku, label=sku)))
        still_estimated = estimated.get(load.id, {})
        for cost_type, amount in still_estimated.items():
            estimated_by_type[cost_type] += amount
        part = sum(still_estimated.values(), ZERO)
        for bucket in targets:
            bucket.add(
                load, container.id, container.id in charged, allocated, vat, part, held.get(container.id)
            )
        for bucket in targets[:2]:
            for cost_type, amount in by_type.items():
                bucket.by_cost_type[cost_type] += amount

    for bucket in (*buckets.values(), totals, *skus.values()):
        bucket.close()
    if group_by == "cost_type":
        buckets = _by_cost_type(totals, estimated_by_type)

    return LandedCostReport(
        base_currency=org.base_currency,
        group_by=group_by,
        period_from=period_from,
        period_to=period_to,
        buckets=sorted(buckets.values(), key=lambda b: b.label),
        skus=[_sku_line(skus[sku]) for sku in sorted(skus)],
        totals=totals,
        orders_without_fx_rate=[
            UnratedOrder(po_id, number) for po_id, number in sorted(unrated.items(), key=lambda item: item[1])
        ],
    )


def _sku_line(bucket: Bucket) -> SkuLine:
    # Weighted by quantity, which is what a weighted average is: the same SKU landed twice at
    # different costs is one number, not the mean of two.
    unit: Decimal | None = None
    if bucket.landed is not None:
        unit = q4(bucket.landed / bucket.charged_quantity) if bucket.charged_quantity else Decimal(0)
    return SkuLine(
        sku=bucket.key,
        quantity=bucket.quantity,
        fob=bucket.fob,
        landed=bucket.landed,
        unit_landed_cost=unit,
        coefficient=bucket.coefficient,
        load_count=bucket.load_count,
        estimated=bucket.estimated,
        containers_without_charge=bucket.containers_without_charge,
        fob_missing_lines=bucket.fob_missing_lines,
        containers_held=bucket.containers_held,
        held_orders=sorted(bucket.held_orders),
        charged_quantity=bucket.charged_quantity,
        charged_fob=bucket.charged_fob,
        unit_charged_fob=(
            q4(bucket.charged_fob / bucket.charged_quantity)
            if bucket.charged_fob is not None and bucket.charged_lines and bucket.charged_quantity
            else None
        ),
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


def _by_cost_type(totals: Bucket, estimated: dict[str, Decimal]) -> dict[str, Bucket]:
    """One bucket per charge, carrying the FOB it was charged on so shares stay computable."""
    out: dict[str, Bucket] = {}
    for cost_type, amount in totals.by_cost_type.items():
        bucket = Bucket(
            key=cost_type,
            label=cost_type,
            fob=totals.fob,
            charged_fob=totals.charged_fob,
            load_count=totals.load_count,
            fob_missing_lines=totals.fob_missing_lines,
            uncharged=totals.uncharged,
            held=totals.held,
            held_orders=totals.held_orders,
        )
        bucket.allocated = amount if cost_type not in EXCLUDED_FROM_LANDED else ZERO
        bucket.vat = amount if cost_type in EXCLUDED_FROM_LANDED else ZERO
        # The import-VAT row used to carry its amount as a landed cost of its own, in the one table
        # whose whole point is which charge weighs what. A recoverable tax weighs nothing there.
        bucket.landed = bucket.allocated
        bucket.estimated = q2(estimated.get(cost_type, ZERO))
        bucket.by_cost_type[cost_type] = amount
        out[cost_type] = bucket
    return out


def _rounded(bucket: Bucket) -> Bucket:
    bucket.fob = None if bucket.fob is None else q2(bucket.fob)
    bucket.charged_fob = None if bucket.charged_fob is None else q2(bucket.charged_fob)
    bucket.uncharged_fob = q2(bucket.uncharged_fob)
    bucket.allocated = q2(bucket.allocated)
    bucket.vat = q2(bucket.vat)
    bucket.estimated = q2(bucket.estimated)
    bucket.landed = None if bucket.landed is None else q2(bucket.landed)
    bucket.by_cost_type = {k: q2(v) for k, v in sorted(bucket.by_cost_type.items())}
    return bucket


# ---------------------------------------------------------------------------- SKU history


def record_sku_costs(db: Session, comp: Computation, org: Organization, on_date: date) -> int:
    """Write today's landed unit cost for every SKU. Returns how many SKUs were recorded.

    Called after each recompute. One row per SKU per day: recomputing twice on the same day corrects
    the day rather than adding a second point, so the curve is a history of facts about the goods,
    not of button presses. An article one of whose lines has no value gets no point that day: its
    unit cost is not known, and a point over its other lines would be a different article's curve.
    A line whose container carries no charge is left out, as from every landed figure; an article
    with nothing else gets no point.
    """
    per_load: dict[UUID, Decimal] = defaultdict(lambda: ZERO)
    for allocation in comp.result.allocations:
        cost = comp.cost_rows[allocation.cost_id]
        if cost.cost_type.value not in EXCLUDED_FROM_LANDED:
            per_load[allocation.load_id] += allocation.amount_base
    charged = charged_containers(comp)

    totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal(0), ZERO, ZERO, Decimal(0)])
    unknown: set[str] = set()
    for load in comp.loads:
        sku = comp.load_rows[load.id].po_line.sku
        if not sku or load.container_id not in charged:
            continue
        if load.fob is None:
            unknown.add(sku)
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
    for sku in unknown:
        totals.pop(sku, None)
    for sku in [sku for sku in existing if sku not in totals]:
        db.delete(existing.pop(sku))  # the day is corrected, not left at what it was before
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
