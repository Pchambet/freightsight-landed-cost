"""Producing estimates from what the organization already knows.

Three sources, and not one of them guesses:

  * a rate card — the organization's own price list, entered by a person;
  * theoretical duty — the tariff rate carried by the purchase-order line, applied to the customs
    value the engine already computes;
  * nothing at all. A cost type with no card and no rate produces no estimate, because an invented
    number is worse than a missing one: the completeness gauge is there to say what is missing.

An estimate is never produced for a scope that already has a cost of that type, estimate or invoice.
Running this twice changes nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.money import q2, to_base
from app.domain.costing import engine, entry
from app.domain.costing.service import Computation, compute
from app.domain.fx.service import FxService
from app.domain.models import (
    Container,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    Organization,
    RateBasis,
    RateCard,
)

logger = logging.getLogger(__name__)

HUNDRED = Decimal("100")


@dataclass(frozen=True)
class Basis:
    """The physical facts of one container, on which a rate card is applied."""

    fob_base: Decimal
    weight_kg: Decimal
    volume_cbm: Decimal
    container_count: int = 1


@dataclass(frozen=True)
class Estimated:
    created: list[Cost]
    skipped: dict[str, str]  # cost type -> why nothing was created


def basis_for(comp: Computation, container_id: UUID) -> Basis:
    loads = [ld for ld in comp.loads if ld.container_id == container_id]
    return Basis(
        fob_base=sum((ld.fob for ld in loads), Decimal("0.00")),
        weight_kg=sum(((ld.unit_weight_kg or Decimal(0)) * ld.quantity for ld in loads), Decimal(0)),
        volume_cbm=sum(((ld.unit_volume_cbm or Decimal(0)) * ld.quantity for ld in loads), Decimal(0)),
    )


def amount_for(card: RateCard, basis: Basis) -> Decimal | None:
    """What the card says this container costs, or None when the basis is unknown.

    A weight-based rate on a container whose weights nobody entered produces nothing at all: that is
    the difference between an estimate and a number.
    """
    match card.basis:
        case RateBasis.FLAT:
            return q2(card.amount)
        case RateBasis.PER_100KG:
            if basis.weight_kg <= 0:
                return None
            return q2(card.amount * basis.weight_kg / HUNDRED)
        case RateBasis.PER_CBM:
            if basis.volume_cbm <= 0:
                return None
            return q2(card.amount * basis.volume_cbm)
        case RateBasis.PCT_OF_FOB:
            if basis.fob_base <= 0:
                return None
            return q2(card.amount * basis.fob_base / HUNDRED)
    raise AssertionError(card.basis)  # pragma: no cover


def duty_rate_for(line_hs_code: str | None, cards: list[RateCard]) -> Decimal | None:
    """The tariff rate for an HS code, from the longest card prefix that matches it.

    Tariff headings are hierarchical — 4011 is tyres, 401110 is car tyres — so the most specific card
    wins, which is how a customs schedule is read.
    """
    if not line_hs_code:
        return None
    candidates = [
        card
        for card in cards
        if card.hs_code and line_hs_code.replace(" ", "").startswith(card.hs_code.replace(" ", ""))
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda card: len(card.hs_code or ""))
    return best.amount


def theoretical_duty(comp: Computation, container_id: UUID, cards: list[RateCard]) -> Decimal | None:
    """Duty on the customs value: the tariff rate of each line applied to its CIF share.

    The CIF basis is the engine's, not a second implementation of it — the same rule that will be
    used when the real customs invoice arrives.
    """
    loads = [ld for ld in comp.loads if ld.container_id == container_id]
    if not loads:
        return None
    total = Decimal("0.00")
    seen_rate = False
    for load in loads:
        rate = load.duty_rate if load.duty_rate is not None else duty_rate_for(_hs_code(comp, load), cards)
        if rate is None:
            continue
        seen_rate = True
        # Each line's customs value as the engine gives it after its first pass: its FOB and what it
        # received of the freight and insurance, spread by their own methods — by weight, by volume —
        # not by a share of FOB, which is the same only when the organization spreads by value.
        total += comp.result.cif.get(load.id, load.fob) * rate
    return q2(total) if seen_rate and total > 0 else None


def _hs_code(comp: Computation, load: engine.Load) -> str | None:
    line = comp.lines.get(load.po_line_id)
    return line.hs_code if line is not None else None


def estimate_container(
    db: Session,
    fx: FxService,
    org: Organization,
    container: Container,
    *,
    created_by: UUID | None = None,
    on_date: date | None = None,
) -> Estimated:
    """Create the estimates this container is missing, in the caller's transaction."""
    comp = compute(db, org)
    cards = list(db.scalars(select(RateCard).where(RateCard.org_id == org.id)))
    basis = basis_for(comp, container.id)
    cost_date = on_date or datetime.now(UTC).date()

    # What is already on this box's freight — the box, or the bill of lading it travels under — and on
    # the bill of lading's — the bill, or any box on it. A freight invoiced per bill of lading is the
    # box's freight too: a card priced per box would add it a second time, and the reverse.
    voyage = (
        set(
            db.scalars(
                select(Container.id).where(
                    Container.org_id == org.id, Container.shipment_id == container.shipment_id
                )
            )
        )
        if container.shipment_id is not None
        else set()
    )
    on_box: dict[CostType, set[CostScope]] = defaultdict(set)
    on_voyage: dict[CostType, set[CostScope]] = defaultdict(set)
    for cost in comp.cost_rows.values():
        on_its_bill = container.shipment_id is not None and cost.shipment_id == container.shipment_id
        if cost.container_id == container.id or on_its_bill:
            on_box[cost.cost_type].add(cost.scope)
        if on_its_bill or cost.container_id in voyage:
            on_voyage[cost.cost_type].add(cost.scope)
    created: list[Cost] = []
    skipped: dict[str, str] = {}

    for card in sorted(cards, key=lambda c: (c.cost_type.value, c.scope.value)):
        if card.scope not in (CostScope.CONTAINER, CostScope.SHIPMENT):
            skipped[card.cost_type.value] = f"{card.scope.value} rate cards are not applied yet"
            continue
        if card.cost_type is CostType.CUSTOMS_DUTY:
            continue  # duty comes from the tariff rates below, not from a flat card
        if card.scope is CostScope.SHIPMENT and container.shipment_id is None:
            skipped[card.cost_type.value] = "a SHIPMENT rate card needs the container to be on a shipment"
            continue
        present = (on_box if card.scope is CostScope.CONTAINER else on_voyage).get(card.cost_type, set())
        if card.scope in present:
            skipped[card.cost_type.value] = "a cost of this type already exists"
            continue
        if present:
            # The same charge recorded at the other end of the freight: another charge of the same
            # type is possible — origin and destination handling — but only a person can say so.
            skipped[card.cost_type.value] = (
                "a cost of this type is already on the container's shipment"
                if card.scope is CostScope.CONTAINER
                else "a cost of this type is already on a container of this shipment"
            )
            continue
        amount = amount_for(card, basis)
        if amount is None:
            skipped[card.cost_type.value] = f"nothing to apply {card.basis.value} to on this container"
            continue
        created.append(
            _estimate(
                db,
                fx,
                org,
                container,
                cost_type=card.cost_type,
                scope=card.scope,
                amount=amount,
                currency=card.currency,
                cost_date=cost_date,
                created_by=created_by,
                note=f"Estimated from the {card.basis.value} rate card",
                comp=comp,
            )
        )

    if created:
        # Duty is taken on the customs value, and the freight just estimated is part of it: without
        # this, a box whose freight card is applied in the same call got the duty of its goods alone.
        db.flush()
        comp = compute(db, org)
    if CostType.CUSTOMS_DUTY not in on_box:
        duty = theoretical_duty(
            comp, container.id, [c for c in cards if c.cost_type is CostType.CUSTOMS_DUTY]
        )
        if duty is not None:
            created.append(
                _estimate(
                    db,
                    fx,
                    org,
                    container,
                    cost_type=CostType.CUSTOMS_DUTY,
                    scope=CostScope.CONTAINER,
                    amount=duty,
                    currency=org.base_currency,
                    cost_date=cost_date,
                    created_by=created_by,
                    note="Theoretical duty on the customs value, from the tariff rates on the lines",
                    comp=comp,
                )
            )
        else:
            skipped[CostType.CUSTOMS_DUTY.value] = "no tariff rate on these lines"
    else:
        skipped[CostType.CUSTOMS_DUTY.value] = "a cost of this type already exists"

    db.flush()
    return Estimated(created=created, skipped=skipped)


def _estimate(
    db: Session,
    fx: FxService,
    org: Organization,
    container: Container,
    *,
    cost_type: CostType,
    scope: CostScope,
    amount: Decimal,
    currency: str,
    cost_date: date,
    created_by: UUID | None,
    note: str,
    comp: Computation,
) -> Cost:
    resolved = fx.resolve(db, org.base_currency, currency, cost_date)
    target_id = container.id if scope is CostScope.CONTAINER else container.shipment_id
    assert target_id is not None  # a SHIPMENT card is only applied to a container that has one
    # The same method as the invoice that will replace this estimate, so that the variance between
    # the two is a difference of amounts, not of how they were spread.
    method = entry.default_method(
        db, org, scope, target_id, cost_type, to_base(q2(amount), resolved.rate), comp
    )
    cost = Cost(
        org_id=org.id,
        scope=scope,
        container_id=container.id if scope is CostScope.CONTAINER else None,
        shipment_id=container.shipment_id if scope is CostScope.SHIPMENT else None,
        cost_type=cost_type,
        amount=q2(amount),
        currency=currency,
        fx_rate=resolved.rate,
        fx_date=resolved.rate_date,
        fx_source=resolved.source,
        amount_base=to_base(q2(amount), resolved.rate),
        allocation_method=method,
        cost_date=cost_date,
        status=CostStatus.ESTIMATE,
        notes=note,
        created_by=created_by,
    )
    db.add(cost)
    db.flush()
    return cost
