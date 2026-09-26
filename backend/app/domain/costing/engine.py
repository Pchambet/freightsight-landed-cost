"""Landed-cost allocation engine. Pure: no I/O, no ORM, frozen dataclasses in, allocations out.

Two passes, because customs duty depends on the freight and insurance already allocated (EU customs
value is CIF): pass 1 allocates every ordinary cost, pass 2 allocates DUTY-like costs on the CIF basis.

Rounding: Decimal, parts floored to the cent, remaining cents handed to the largest fractional
remainders, ties broken by a stable sort key. The parts of one cost always sum to its base amount.
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from uuid import UUID

CENT = Decimal("0.01")
HUNDRED = Decimal("100.00")

DUTIABLE = {"OCEAN_FREIGHT", "AIR_FREIGHT", "INSURANCE", "ORIGIN_CHARGES"}  # enter the customs value
EXCLUDED_FROM_LANDED = {"IMPORT_VAT"}  # recoverable: allocated for cash view, excluded from landed cost
DUTY_LIKE_METHODS = {"BY_CIF_VALUE", "BY_THEORETICAL_DUTY"}
#: Methods whose basis is a physical fact about the goods. A zero there is a line that pays nothing
#: for freight it did travel with — worth naming. A zero duty rate is not: it is a tariff heading at
#: 0 %, which is a fact about the goods and not an omission.
PHYSICAL_METHODS = {"BY_VALUE", "BY_QUANTITY", "BY_WEIGHT", "BY_VOLUME"}


class AllocationError(ValueError):
    code = "ALLOCATION_ERROR"


class MissingBasisError(AllocationError):
    code = "MISSING_BASIS"

    def __init__(self, method: str, load_ids: list[UUID]):
        super().__init__(f"{method}: basis missing on {len(load_ids)} load(s)")
        self.load_ids = load_ids
        # A duty that cannot be spread is missing a duty RATE, not a weight, a volume or a value:
        # the screen used to tell the user to go and look for the wrong thing.
        if method == "BY_THEORETICAL_DUTY":
            self.code = "MISSING_DUTY_RATE"


class InvalidManualSplitError(AllocationError):
    code = "INVALID_MANUAL_SPLIT"


class NoTargetError(AllocationError):
    code = "NO_TARGET"


class AllWeightsZeroError(AllocationError):
    code = "ALL_WEIGHTS_ZERO"


def quantize_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def split_largest_remainder[K: Hashable](
    amount: Decimal, weights: dict[K, Decimal], order: Sequence[K]
) -> dict[K, Decimal]:
    """Split `amount` proportionally to `weights`; sum(result) == amount, to the cent, deterministically."""
    if amount != quantize_cent(amount):
        raise AllocationError(f"amount must have at most 2 decimals, got {amount}")
    if any(w < 0 for w in weights.values()):
        raise AllocationError("weights must be >= 0")
    if amount < 0:
        # A credit note is the invoice it corrects, with a minus. Splitting the negative directly
        # would give a negative remainder, and `ranked[:-1]` is "all but the last" — a cent added to
        # almost every line. Splitting the absolute value and re-signing keeps the parts identical to
        # the ones the original piece received, which is what makes the two documents reconcile.
        return {k: -v for k, v in split_largest_remainder(-amount, weights, order).items()}
    total_weight = sum(weights.values(), Decimal(0))
    if total_weight <= 0:
        raise AllWeightsZeroError("sum of weights must be > 0")
    raw = {k: amount * weights[k] / total_weight for k in order}
    floored = {k: raw[k].quantize(CENT, rounding=ROUND_DOWN) for k in order}
    remainder = amount - sum(floored.values(), Decimal(0))
    n_cents = int((remainder / CENT).to_integral_value())
    position = {k: i for i, k in enumerate(order)}
    ranked = sorted(order, key=lambda k: (-(raw[k] - floored[k]), position[k]))
    for k in ranked[:n_cents]:
        floored[k] += CENT
    total = sum(floored.values(), Decimal(0))
    if total != amount:
        # The one invariant the whole product rests on. An `assert` disappears under `python -O` and
        # an AssertionError would fly straight through `allocate()`'s handlers into a 500 that blanks
        # every landed-cost screen; an AllocationError lands in `unallocated`, next to its cost.
        raise AllocationError(f"split of {amount} summed to {total}")
    return floored


@dataclass(frozen=True)
class Load:
    """A leaf: one PO line inside one container."""

    id: UUID
    container_id: UUID
    shipment_id: UUID | None
    po_id: UUID
    po_line_id: UUID
    quantity: Decimal
    unit_price_base: Decimal  # already converted at the PO's fixed rate
    unit_weight_kg: Decimal | None
    unit_volume_cbm: Decimal | None
    duty_rate: Decimal | None
    sort_key: tuple[str, str, int]  # (container_number, po_number, line_no)

    @property
    def fob(self) -> Decimal:
        return quantize_cent(self.quantity * self.unit_price_base)


@dataclass(frozen=True)
class ManualSplit:
    target_type: str  # container | po | po_line
    target_id: UUID
    pct: Decimal


@dataclass(frozen=True)
class Cost:
    id: UUID
    scope: str  # SHIPMENT | CONTAINER | PO | PO_LINE
    target_id: UUID
    cost_type: str
    amount_base: Decimal
    method: str
    manual_splits: tuple[ManualSplit, ...] = ()


@dataclass(frozen=True)
class Allocation:
    cost_id: UUID
    load_id: UUID
    amount_base: Decimal


@dataclass(frozen=True)
class Unallocated:
    cost_id: UUID
    code: str
    message: str
    load_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class Note:
    """A cost that *was* allocated, in a way somebody should be told about.

    Distinct from `Unallocated` on purpose: the money is placed and the totals are right, so a note
    must never be read as an amount missing from the landed cost. What it carries is a rule that was
    applied silently until now — a line that received nothing, a duty spread on CIF because a tariff
    rate is missing. "What cannot be allocated is flagged, never guessed" has to cover these too.
    """

    cost_id: UUID
    code: str
    message: str
    load_ids: tuple[UUID, ...] = ()


@dataclass
class AllocationResult:
    allocations: list[Allocation] = field(default_factory=list)
    unallocated: list[Unallocated] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    cif: dict[UUID, Decimal] = field(default_factory=dict)  # load_id -> CIF value after pass 1


_SCOPE_KEY = {"SHIPMENT": "shipment_id", "CONTAINER": "container_id", "PO": "po_id", "PO_LINE": "po_line_id"}
_CHILD_KEY = {"container": "container_id", "po": "po_id", "po_line": "po_line_id"}


def _targets(cost: Cost, loads: list[Load]) -> list[Load]:
    key = _SCOPE_KEY[cost.scope]
    return [ld for ld in loads if getattr(ld, key) == cost.target_id]


def _weight(load: Load, method: str, cif: dict[UUID, Decimal]) -> Decimal | None:
    match method:
        case "BY_VALUE":
            return load.fob
        case "BY_QUANTITY":
            return load.quantity
        case "BY_WEIGHT":
            return None if load.unit_weight_kg is None else load.quantity * load.unit_weight_kg
        case "BY_VOLUME":
            return None if load.unit_volume_cbm is None else load.quantity * load.unit_volume_cbm
        case "BY_CIF_VALUE":
            return cif[load.id]
        case "BY_THEORETICAL_DUTY":
            return None if load.duty_rate is None else load.duty_rate * cif[load.id]
    raise AllocationError(f"unknown method {method}")


def _allocate_proportional(
    cost_id: UUID,
    amount: Decimal,
    targets: list[Load],
    method: str,
    cif: dict[UUID, Decimal],
    zero_basis: list[UUID] | None = None,
) -> list[Allocation]:
    targets = sorted(targets, key=lambda ld: ld.sort_key)
    weights = {ld.id: _weight(ld, method, cif) for ld in targets}
    missing = [k for k, w in weights.items() if w is None]
    if missing:
        raise MissingBasisError(method, missing)
    if zero_basis is not None and method in PHYSICAL_METHODS:
        zero_basis += [k for k, w in weights.items() if w == 0]
    parts = split_largest_remainder(
        amount, {k: w for k, w in weights.items() if w is not None}, [ld.id for ld in targets]
    )
    return [Allocation(cost_id, lid, amt) for lid, amt in parts.items() if amt != 0]


def _allocate_manual(
    cost: Cost, targets: list[Load], fallback: str, cif: dict[UUID, Decimal], zero_basis: list[UUID]
) -> list[Allocation]:
    total_pct = sum((s.pct for s in cost.manual_splits), Decimal(0))
    if total_pct != HUNDRED:
        raise InvalidManualSplitError(f"manual splits sum to {total_pct:.2f}, expected 100.00")
    # Two lines naming the same target are one target. They used to be kept apart: the percentages
    # went into a dict where the second overwrote the first, while the order list still held both
    # entries, so the loop below allocated the whole cost twice. The API refuses the duplicate now;
    # adding the percentages up here keeps the engine's own invariant true whatever reaches it,
    # including rows written before that check existed. 60 % then 40 % on one container is 100 %.
    merged: dict[tuple[str, UUID], Decimal] = {}
    for s in cost.manual_splits:
        if s.target_type not in _CHILD_KEY:
            raise InvalidManualSplitError(f"unknown split target type {s.target_type!r}")
        key = (s.target_type, s.target_id)
        merged[key] = merged.get(key, Decimal(0)) + s.pct
    groups = {key: [ld for ld in targets if getattr(ld, _CHILD_KEY[key[0]]) == key[1]] for key in merged}
    empty = [str(target_id) for (_, target_id), lds in groups.items() if not lds]
    if empty:
        raise InvalidManualSplitError(
            f"manual split targets have no loads under this scope: {', '.join(empty)}"
        )
    order = list(merged)
    slices = split_largest_remainder(cost.amount_base, merged, order)
    out: list[Allocation] = []
    for key in order:
        out += _allocate_proportional(cost.id, slices[key], groups[key], fallback, cif, zero_basis)
    return out


def allocate(
    costs: Sequence[Cost], loads: Sequence[Load], fallback_method: str = "BY_VALUE"
) -> AllocationResult:
    """Allocate every cost down to the leaves. Never raises for data problems: they land in `unallocated`."""
    result = AllocationResult()
    sorted_loads = sorted(loads, key=lambda ld: ld.sort_key)
    cif: dict[UUID, Decimal] = {ld.id: ld.fob for ld in sorted_loads}
    pass1 = [c for c in costs if c.method not in DUTY_LIKE_METHODS]
    pass2 = [c for c in costs if c.method in DUTY_LIKE_METHODS]

    def run(cost: Cost) -> None:
        targets = _targets(cost, sorted_loads)
        zero_basis: list[UUID] = []
        try:
            if not targets:
                raise NoTargetError(f"no container loads under {cost.scope} {cost.target_id}")
            if cost.method == "MANUAL":
                allocs = _allocate_manual(cost, targets, fallback_method, cif, zero_basis)
            else:
                allocs = _allocate_proportional(
                    cost.id, cost.amount_base, targets, cost.method, cif, zero_basis
                )
        except MissingBasisError as e:
            result.unallocated.append(Unallocated(cost.id, e.code, str(e), tuple(e.load_ids)))
            return
        except AllocationError as e:
            result.unallocated.append(Unallocated(cost.id, e.code, str(e)))
            return
        result.allocations += allocs
        if zero_basis:
            result.notes.append(
                Note(
                    cost.id,
                    "ZERO_BASIS_LINES",
                    f"{len(zero_basis)} line(s) received nothing: their allocation basis is zero",
                    tuple(zero_basis),
                )
            )
        # A duty spread on the customs value when only some lines carry a tariff rate is a decision
        # about somebody's unit cost — the 0 % line pays duty it does not owe and the 12 % line is
        # under-charged. It is the right fallback (the alternative allocates nothing at all), but it
        # has to be said, with the lines that caused it.
        if cost.cost_type == "CUSTOMS_DUTY" and cost.method == "BY_CIF_VALUE":
            unrated = [ld.id for ld in targets if ld.duty_rate is None]
            if unrated and len(unrated) != len(targets):
                result.notes.append(
                    Note(
                        cost.id,
                        "DUTY_RATE_PARTIAL",
                        f"duty spread on the customs value: {len(unrated)} line(s) have no tariff rate",
                        tuple(unrated),
                    )
                )
        if cost.cost_type in DUTIABLE:
            for a in allocs:
                cif[a.load_id] += a.amount_base

    for c in sorted(pass1, key=lambda c: (c.scope, str(c.id))):
        run(c)
    for c in sorted(pass2, key=lambda c: (c.scope, str(c.id))):
        run(c)
    result.cif = cif
    return result
