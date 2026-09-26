"""Pure derivation: events in, container state out. No database, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.domain.models import Container, ContainerMilestone, DndRisk, MilestoneCode, Organization
from app.domain.tracking.manual import manual_event
from app.domain.tracking.state import (
    apply_snapshot,
    derive,
    derive_dnd,
    eta_shift,
    free_days_for,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def at(days: float) -> datetime:
    return NOW + timedelta(days=days)


def test_milestone_is_the_furthest_actual_event() -> None:
    events = [
        manual_event(MilestoneCode.LOADED, at(-20)),
        manual_event(MilestoneCode.DISCHARGED, at(-2)),
        manual_event(MilestoneCode.VESSEL_ARRIVED, at(-3)),
        manual_event(MilestoneCode.GATE_OUT_FULL, at(3), is_estimate=True),
    ]
    snapshot = derive(events, NOW)
    assert snapshot.milestone is ContainerMilestone.DISCHARGED
    assert snapshot.discharged_at == at(-2)
    assert snapshot.ata == at(-3)
    assert snapshot.gate_out_at is None  # an estimate is not an arrival


def test_future_actual_and_transshipment_do_not_move_the_box() -> None:
    events = [
        manual_event(MilestoneCode.TRANSSHIPMENT_DISCHARGED, at(-5)),
        manual_event(MilestoneCode.DISCHARGED, at(5)),  # carrier publishing ahead of time
    ]
    snapshot = derive(events, NOW)
    # a transshipment leg is timeline detail and has no equivalent on the container itself
    assert snapshot.milestone is ContainerMilestone.BOOKED
    assert snapshot.discharged_at is None


def test_eta_is_the_latest_estimated_arrival() -> None:
    events = [
        manual_event(MilestoneCode.VESSEL_ARRIVED, at(2), is_estimate=True),
        manual_event(MilestoneCode.VESSEL_ARRIVED, at(6), is_estimate=True),
    ]
    assert derive(events, NOW).eta == at(6)


def test_apply_snapshot_never_walks_the_milestone_backwards() -> None:
    container = Container(container_number="MSCU1234567", milestone=ContainerMilestone.GATE_OUT_FULL)
    apply_snapshot(container, derive([manual_event(MilestoneCode.DISCHARGED, at(-1))], NOW))
    assert container.milestone is ContainerMilestone.GATE_OUT_FULL
    assert container.discharged_at == at(-1)  # the timestamp is still recorded


def test_last_free_day_and_risk_bands() -> None:
    org = Organization(name="Acme", free_days_demurrage=5, settings={})

    def risk(
        discharged_days: float,
        gate_out_days: float | None = None,
        empty_days: float | None = None,
    ) -> DndRisk:
        container = Container(
            container_number="MSCU1234567",
            discharged_at=at(discharged_days),
            gate_out_at=None if gate_out_days is None else at(gate_out_days),
            empty_returned_at=None if empty_days is None else at(empty_days),
        )
        derive_dnd(container, org, NOW)
        return container.dnd_risk

    discharged_today = Container(container_number="MSCU1234567", discharged_at=at(0))
    derive_dnd(discharged_today, org, NOW)
    assert discharged_today.last_free_day == at(4).date()  # discharge + 5 - 1, calendar days

    picked_up = Container(container_number="MSCU1234567", discharged_at=at(-2), gate_out_at=at(0))
    derive_dnd(picked_up, org, NOW)
    assert picked_up.detention_deadline == at(6).date()  # gate-out + 7 - 1

    assert risk(0) == DndRisk.LOW  # four days left
    assert risk(-2) == DndRisk.MEDIUM
    assert risk(-3) == DndRisk.HIGH
    assert risk(-10) == DndRisk.INCURRING
    # Picked up: the terminal clock stops and the detention clock starts, seven free days by default.
    assert risk(-10, gate_out_days=-1) == DndRisk.LOW  # five days left to return the empty
    assert risk(-10, gate_out_days=-6) == DndRisk.HIGH
    assert risk(-10, gate_out_days=-9) == DndRisk.INCURRING  # detention is running
    assert risk(-10, gate_out_days=-9, empty_days=-8) == DndRisk.NONE  # the box is back


def test_free_days_container_then_carrier_then_org_default() -> None:
    org = Organization(name="Acme", free_days_demurrage=5, settings={"free_days": {"MSCU": 9}})
    container = Container(container_number="MSCU1234567", carrier_scac="MSCU")
    assert free_days_for(container, org) == 9
    container.carrier_scac = "CMDU"
    assert free_days_for(container, org) == 5
    container.free_days_demurrage = 3
    assert free_days_for(container, org) == 3


def test_eta_shift_ignores_noise_and_flags_two_days() -> None:
    assert eta_shift(None, at(1)) is None  # a first ETA is not a change
    assert eta_shift(at(1), at(1) + timedelta(hours=6)) is None
    assert eta_shift(at(1), at(1) + timedelta(hours=13)) == (Decimal("13.0"), "info")
    assert eta_shift(at(1), at(1) + timedelta(hours=60)) == (Decimal("60.0"), "warning")
    assert eta_shift(at(1), at(1) - timedelta(hours=60)) == (Decimal("-60.0"), "warning")
