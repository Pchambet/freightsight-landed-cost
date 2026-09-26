"""Reference cases T1-T9, executed against the pure engine."""

from __future__ import annotations

import inspect
import random
from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.domain.costing.engine import (
    AllocationError,
    AllocationResult,
    Cost,
    Load,
    ManualSplit,
    allocate,
    split_largest_remainder,
)

D = Decimal


def load(
    container: str,
    po: str,
    line_no: int,
    qty: str,
    price: str,
    *,
    kg: str | None = None,
    cbm: str | None = None,
    duty: str | None = None,
    container_id: UUID | None = None,
    shipment_id: UUID | None = None,
    po_id: UUID | None = None,
    line_id: UUID | None = None,
) -> Load:
    return Load(
        id=line_id or uuid4(),
        container_id=container_id or uuid4(),
        shipment_id=shipment_id,
        po_id=po_id or uuid4(),
        po_line_id=uuid4(),
        quantity=D(qty),
        unit_price_base=D(price),
        unit_weight_kg=None if kg is None else D(kg),
        unit_volume_cbm=None if cbm is None else D(cbm),
        duty_rate=None if duty is None else D(duty),
        sort_key=(container, po, line_no),
    )


def cost(
    scope: str,
    target: UUID,
    amount: str,
    ctype: str = "DRAYAGE",
    method: str = "BY_VALUE",
    splits: Sequence[ManualSplit] = (),
) -> Cost:
    return Cost(uuid4(), scope, target, ctype, D(amount), method, tuple(splits))


def parts(result: AllocationResult, cost_id: UUID) -> list[Decimal]:
    return [a.amount_base for a in result.allocations if a.cost_id == cost_id]


def test_t1_by_value_reconciles_to_cent() -> None:
    cnt = uuid4()
    a = load("CNT1", "PO-A", 1, "1", "12500.50", container_id=cnt)
    b = load("CNT1", "PO-B", 1, "1", "8400.00", container_id=cnt)
    c = cost("CONTAINER", cnt, "1000.00")
    r = allocate([c], [a, b])
    assert parts(r, c.id) == [D("598.10"), D("401.90")]
    assert r.unallocated == []


def test_t2_quantity_tie_break_deterministic() -> None:
    cnt = uuid4()
    loads = [load("CNT1", f"PO-{i}", 1, "10", "1", container_id=cnt) for i in range(3)]
    c = cost("CONTAINER", cnt, "100.00", method="BY_QUANTITY")
    for _ in range(5):
        r = allocate([c], loads)
        assert parts(r, c.id) == [D("33.34"), D("33.33"), D("33.33")]


def test_t3_multicurrency_weight() -> None:
    cnt = uuid4()
    a = load("CNT1", "PO-A", 1, "600", "10", kg="2", container_id=cnt)
    b = load("CNT1", "PO-B", 1, "400", "10", kg="2", container_id=cnt)
    # 2000.00 EUR at 1.0850 -> 2170.00 USD base, done by the service; the engine sees amount_base
    c = cost("CONTAINER", cnt, "2170.00", ctype="OCEAN_FREIGHT", method="BY_WEIGHT")
    r = allocate([c], [a, b])
    assert parts(r, c.id) == [D("1302.00"), D("868.00")]


def test_t4_duty_theoretical_vs_cif() -> None:
    cnt, po = uuid4(), uuid4()
    l1 = load("CNT1", "PO-1", 1, "1000", "10.00", duty="0.045", container_id=cnt, po_id=po)
    l2 = load("CNT1", "PO-1", 2, "100", "50.00", duty="0", container_id=cnt, po_id=po)
    freight = cost("CONTAINER", cnt, "1500.00", ctype="OCEAN_FREIGHT")
    insurance = cost("CONTAINER", cnt, "150.00", ctype="INSURANCE")
    duty_theo = cost("PO", po, "499.50", ctype="CUSTOMS_DUTY", method="BY_THEORETICAL_DUTY")
    r = allocate([freight, insurance, duty_theo], [l1, l2])
    assert parts(r, freight.id) == [D("1000.00"), D("500.00")]
    assert parts(r, insurance.id) == [D("100.00"), D("50.00")]
    assert r.cif[l1.id] == D("11100.00") and r.cif[l2.id] == D("5550.00")
    assert parts(r, duty_theo.id) == [D("499.50")]  # l2 gets 0 and is omitted

    duty_cif = cost("PO", po, "499.50", ctype="CUSTOMS_DUTY", method="BY_CIF_VALUE")
    r2 = allocate([freight, insurance, duty_cif], [l1, l2])
    assert parts(r2, duty_cif.id) == [D("333.00"), D("166.50")]

    vat = cost("PO", po, "3429.90", ctype="IMPORT_VAT")
    r3 = allocate([freight, insurance, duty_theo, vat], [l1, l2])
    assert sum(parts(r3, vat.id)) == D("3429.90")


def test_t5_po_split_across_containers_and_shipment_cost() -> None:
    ship, cnt1, cnt2, po_a, po_b = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    a1 = load("CNT1", "PO-A", 1, "600", "10.00", kg="2", container_id=cnt1, shipment_id=ship, po_id=po_a)
    a2 = load("CNT2", "PO-A", 1, "400", "10.00", kg="2", container_id=cnt2, shipment_id=ship, po_id=po_a)
    b1 = load("CNT1", "PO-B", 1, "500", "20.00", kg="5", container_id=cnt1, shipment_id=ship, po_id=po_b)
    dray1 = cost("CONTAINER", cnt1, "300.00")
    dray2 = cost("CONTAINER", cnt2, "200.00")
    ocean = cost("SHIPMENT", ship, "3000.00", ctype="OCEAN_FREIGHT", method="BY_WEIGHT")
    r = allocate([dray1, dray2, ocean], [a1, a2, b1])
    assert parts(r, dray1.id) == [D("112.50"), D("187.50")]
    assert parts(r, dray2.id) == [D("200.00")]
    assert parts(r, ocean.id) == [
        D("800.00"),
        D("1666.67"),
        D("533.33"),
    ]  # sorted by sort_key: CNT1/A, CNT1/B, CNT2/A
    total = sum(a.amount_base for a in r.allocations)
    assert total == D("3500.00")


def test_t6_manual_split_cascade_and_150pct_rejected() -> None:
    cnt, po_a, po_b = uuid4(), uuid4(), uuid4()
    a1 = load("CNT1", "PO-A", 1, "1", "6000", container_id=cnt, po_id=po_a)
    a2 = load("CNT1", "PO-A", 2, "1", "2000", container_id=cnt, po_id=po_a)
    b1 = load("CNT1", "PO-B", 1, "1", "5000", container_id=cnt, po_id=po_b)
    ok = cost(
        "CONTAINER",
        cnt,
        "1000.00",
        method="MANUAL",
        splits=[ManualSplit("po", po_a, D("70")), ManualSplit("po", po_b, D("30"))],
    )
    r = allocate([ok], [a1, a2, b1])
    assert parts(r, ok.id) == [D("525.00"), D("175.00"), D("300.00")]

    bad = cost(
        "CONTAINER",
        cnt,
        "1000.00",
        method="MANUAL",
        splits=[ManualSplit("po", po_a, D("100")), ManualSplit("po", po_b, D("50"))],
    )
    r2 = allocate([bad], [a1, a2, b1])
    assert parts(r2, bad.id) == []
    assert [u.code for u in r2.unallocated] == ["INVALID_MANUAL_SPLIT"]
    assert "150.00" in r2.unallocated[0].message


def test_a_duty_that_cannot_be_spread_says_it_is_a_duty_rate_that_is_missing() -> None:
    """The banner used to say "weight, volume or value missing" and send the user to the wrong column."""
    cnt = uuid4()
    a = load("CNT1", "PO-A", 1, "10", "1", duty="0.045", container_id=cnt)
    b = load("CNT1", "PO-B", 1, "10", "1", container_id=cnt)  # no tariff rate
    c = cost("CONTAINER", cnt, "120.00", ctype="CUSTOMS_DUTY", method="BY_THEORETICAL_DUTY")
    r = allocate([c], [a, b])
    assert r.allocations == []
    assert r.unallocated[0].code == "MISSING_DUTY_RATE"
    assert r.unallocated[0].load_ids == (b.id,)


def test_t7_missing_basis_no_silent_fallback() -> None:
    cnt = uuid4()
    a = load("CNT1", "PO-A", 1, "10", "1", kg="2", container_id=cnt)
    b = load("CNT1", "PO-B", 1, "10", "1", container_id=cnt)  # no weight
    c = cost("CONTAINER", cnt, "100.00", method="BY_WEIGHT")
    r = allocate([c], [a, b])
    assert r.allocations == []
    assert r.unallocated[0].code == "MISSING_BASIS"
    assert r.unallocated[0].load_ids == (b.id,)


def test_t8_no_loads_under_scope() -> None:
    c = cost("CONTAINER", uuid4(), "400.00")
    r = allocate([c], [])
    assert r.unallocated[0].code == "NO_TARGET"


def test_t9_split_property() -> None:
    rng = random.Random(7)
    for _ in range(2000):
        n = rng.randint(1, 50)
        order = [f"k{i}" for i in range(n)]
        weights = {k: D(rng.randint(1, 100_000)) for k in order}
        amount = (D(rng.randint(1, 10_000_000_00)) / 100).quantize(D("0.01"))
        result = split_largest_remainder(amount, weights, order)
        assert sum(result.values()) == amount
        total_w = sum(weights.values())
        for k in order:
            assert result[k] >= 0
            assert abs(result[k] - amount * weights[k] / total_w) < D("0.01")


def test_split_rejects_bad_input() -> None:
    with pytest.raises(AllocationError):
        split_largest_remainder(D("1.005"), {"a": D(1)}, ["a"])
    with pytest.raises(AllocationError):
        split_largest_remainder(D("1.00"), {"a": D(0)}, ["a"])


def test_a_credit_note_splits_like_the_invoice_it_corrects() -> None:
    """A negative amount used to hand a cent to almost everybody and then die on a bare assert."""
    order = ["a", "b", "c"]
    weights = {"a": D(1), "b": D(1), "c": D(1)}
    credit = split_largest_remainder(D("-10.00"), weights, order)
    assert sum(credit.values()) == D("-10.00")
    assert credit == {k: -v for k, v in split_largest_remainder(D("10.00"), weights, order).items()}

    # the cent that cannot be divided lands on the same line, with the sign of the piece
    assert split_largest_remainder(D("-0.01"), weights, order) == {"a": D("-0.01"), "b": D(0), "c": D(0)}


def test_the_split_invariant_is_an_allocation_error_not_an_assert() -> None:
    """`python -O` strips asserts; the invariant that guards every euro must survive that flag."""
    import app.domain.costing.engine as engine_module

    source = inspect.getsource(engine_module.split_largest_remainder)
    assert "assert " not in source


def test_a_manual_split_naming_the_same_target_twice_allocates_it_once() -> None:
    """Two lines on the same container is one container: the cost used to be allocated at 200 %."""
    cnt, c1 = uuid4(), uuid4()
    a = load("CNT1", "PO-A", 1, "1", "600", container_id=c1, shipment_id=cnt)
    b = load("CNT1", "PO-B", 1, "1", "400", container_id=c1, shipment_id=cnt)
    duplicated = cost(
        "SHIPMENT",
        cnt,
        "1000.00",
        method="MANUAL",
        splits=[ManualSplit("container", c1, D("60")), ManualSplit("container", c1, D("40"))],
    )
    r = allocate([duplicated], [a, b])
    assert sum(parts(r, duplicated.id)) == D("1000.00")
    assert r.unallocated == []


def test_a_zero_value_line_is_named_rather_than_left_silent() -> None:
    """A free sample weighs nothing in BY_VALUE and gets nothing; saying so is the whole product."""
    cnt = uuid4()
    paid = load("CNT1", "PO-A", 1, "1", "1000.00", container_id=cnt)
    sample = load("CNT1", "PO-A", 2, "1", "0.00", container_id=cnt)
    c = cost("CONTAINER", cnt, "1000.00")
    r = allocate([c], [paid, sample])
    assert parts(r, c.id) == [D("1000.00")]
    assert [n.code for n in r.notes] == ["ZERO_BASIS_LINES"]
    assert r.notes[0].load_ids == (sample.id,)
    assert r.notes[0].cost_id == c.id


def test_a_zero_duty_rate_is_a_tariff_fact_not_a_missing_basis() -> None:
    cnt, po = uuid4(), uuid4()
    l1 = load("CNT1", "PO-1", 1, "1000", "10.00", duty="0.045", container_id=cnt, po_id=po)
    l2 = load("CNT1", "PO-1", 2, "100", "50.00", duty="0", container_id=cnt, po_id=po)
    duty = cost("PO", po, "450.00", ctype="CUSTOMS_DUTY", method="BY_THEORETICAL_DUTY")
    assert allocate([duty], [l1, l2]).notes == []


def test_duty_on_a_cif_basis_names_the_lines_that_have_no_tariff_rate() -> None:
    """The fallback to CIF is a decision about someone's landed cost: it has to be said out loud."""
    cnt, po = uuid4(), uuid4()
    rated = load("CNT1", "PO-1", 1, "1000", "10.00", duty="0.12", container_id=cnt, po_id=po)
    unrated = load("CNT1", "PO-1", 2, "100", "50.00", container_id=cnt, po_id=po)
    duty = cost("PO", po, "1000.00", ctype="CUSTOMS_DUTY", method="BY_CIF_VALUE")
    r = allocate([duty], [rated, unrated])
    assert sum(parts(r, duty.id)) == D("1000.00")
    assert [n.code for n in r.notes] == ["DUTY_RATE_PARTIAL"]
    assert r.notes[0].load_ids == (unrated.id,)

    # every line without a rate is the ordinary case, and CIF is then the only basis there is
    plain = allocate([duty], [unrated])
    assert plain.notes == []


def test_every_allocated_cost_sums_back_to_its_own_amount() -> None:
    """The global invariant, over a deliberately awkward mixture: manual, duty, zero lines, credits."""
    ship, cnt1, cnt2, po_a, po_b = uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
    loads = [
        load(
            "CNT1",
            "PO-A",
            1,
            "600",
            "10.00",
            kg="2",
            duty="0.12",
            container_id=cnt1,
            shipment_id=ship,
            po_id=po_a,
        ),
        load(
            "CNT1",
            "PO-B",
            1,
            "500",
            "20.00",
            kg="5",
            duty="0",
            container_id=cnt1,
            shipment_id=ship,
            po_id=po_b,
        ),
        load(
            "CNT2",
            "PO-A",
            2,
            "400",
            "12.50",
            kg="1",
            duty="0.12",
            container_id=cnt2,
            shipment_id=ship,
            po_id=po_a,
        ),
        load("CNT1", "PO-A", 3, "10", "0.00", kg="0", container_id=cnt1, shipment_id=ship, po_id=po_a),
    ]
    costs = [
        cost("SHIPMENT", ship, "3000.00", ctype="OCEAN_FREIGHT", method="BY_WEIGHT"),
        cost("CONTAINER", cnt1, "275.00", ctype="THC"),
        cost("PO", po_a, "1234.57", ctype="CUSTOMS_DUTY", method="BY_CIF_VALUE"),
        cost(
            "SHIPMENT",
            ship,
            "900.00",
            method="MANUAL",
            splits=[ManualSplit("container", cnt1, D("55")), ManualSplit("container", cnt2, D("45"))],
        ),
    ]
    r = allocate(costs, loads)
    assert r.unallocated == []
    for c in costs:
        assert sum(parts(r, c.id)) == c.amount_base
