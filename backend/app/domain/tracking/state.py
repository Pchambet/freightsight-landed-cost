"""Pure derivation: from a container's event history to its state. No database, no clock of its own.

Two axes, deliberately separate:
  * `milestone`  — where the box is, from the events (this module);
  * `tracking_state` — the health of the subscription (set by the ingestion service).

Demurrage is the money question: `last_free_day = discharged_at + free_days - 1` in the port's own
calendar days, as soon as a real (non-estimated) DISCHARGED **at the port of destination** exists, and
the risk band is recomputed from it. Detention is the same shape, one clock later: it runs from the
gate-out until the empty is back.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from app.domain.models import Container, ContainerMilestone, DndRisk, MilestoneCode, Organization
from app.domain.tracking.calendar import local_date, zone_for
from app.domain.tracking.ports import CONTAINER_MILESTONE, ORDER, ProviderEvent

#: An ETA that moves by less than this is noise; past `ETA_WARNING_HOURS` it is a warning.
DEFAULT_DEMURRAGE_DAYS = 5
DEFAULT_DETENTION_DAYS = 7

ETA_SHIFT_HOURS = 12
ETA_WARNING_HOURS = 48

#: The bands in the order they get worse. A band that goes down is not news; a band that goes up is.
RISK_ORDER = (DndRisk.NONE, DndRisk.LOW, DndRisk.MEDIUM, DndRisk.HIGH, DndRisk.INCURRING)

ClockKind = Literal["demurrage", "detention"]


@dataclass(frozen=True)
class ContainerSnapshot:
    milestone: ContainerMilestone
    eta: datetime | None
    ata: datetime | None
    discharged_at: datetime | None
    gate_out_at: datetime | None
    empty_returned_at: datetime | None
    #: Where the retained discharge happened, when the event said so. This is what the free days are
    #: counted in the calendar of.
    discharge_unlocode: str | None = None
    #: False when we had to guess which discharge was the real one — several discharges and no
    #: destination LOCODE to check them against. The clock still starts; the alert says it is unsure.
    discharge_port_confirmed: bool = True


@dataclass(frozen=True)
class DndClock:
    """Whichever clock is running now. There is only ever one, and it is the one costing money today."""

    kind: ClockKind
    deadline: date
    days_left: int

    @property
    def risk(self) -> DndRisk:
        return risk_band(self.days_left)

    @property
    def days_elapsed(self) -> int:
        """Days of demurrage or detention already run, zero while the deadline is still ahead."""
        return max(-self.days_left, 0)


def _first_actual(events: Sequence[ProviderEvent], code: MilestoneCode) -> datetime | None:
    times = [e.occurred_at for e in events if e.code == code]
    return min(times) if times else None


def _at_destination(
    events: Sequence[ProviderEvent], code: MilestoneCode, destination: str | None
) -> tuple[ProviderEvent | None, bool]:
    """Which of several same-code events is the one at the port of destination, and how sure we are.

    A transhipment discharge is a discharge: the box comes off a ship and goes onto another one, and
    a provider that does not distinguish the two (Shipsgo's `DISC`, anything typed by hand) hands us
    a discharge at Tanger Med for a box bound for Le Havre. Starting the demurrage clock there raises
    "demurrage is running" for a container still at sea, and — because the first actual used to win —
    the real Le Havre discharge never corrected it.

    So: the event at the destination if we know the destination and one matches; nothing at all when
    every event we have is demonstrably at another port; otherwise the **last** one, because the box
    only stops moving once, and flagged as unconfirmed when there was more than one to choose from.
    """
    matching = sorted((e for e in events if e.code == code), key=lambda e: e.occurred_at)
    if not matching:
        return None, True
    if destination:
        wanted = destination.strip().upper()
        at_pod = [e for e in matching if (e.location_unlocode or "").strip().upper() == wanted]
        if at_pod:
            return at_pod[0], True
        if all(e.location_unlocode for e in matching):
            return None, True  # every one of them is at another port: the box is still in transit
    return matching[-1], len(matching) == 1


def derive(
    events: Sequence[ProviderEvent],
    now: datetime | None = None,
    *,
    destination_unlocode: str | None = None,
) -> ContainerSnapshot:
    """Milestone and timestamps from the events. Estimates never move the milestone, and neither does
    an actual event dated in the future — carriers do publish those."""
    moment = now or datetime.now(UTC)
    actual = [e for e in events if not e.is_estimate and e.occurred_at <= moment]
    discharge, confirmed = _at_destination(actual, MilestoneCode.DISCHARGED, destination_unlocode)
    arrival, _ = _at_destination(actual, MilestoneCode.VESSEL_ARRIVED, destination_unlocode)
    # A discharge at another port must not move the container either: the screen would show it
    # "discharged" at a port it is nowhere near.
    elsewhere = {
        id(e)
        for e in actual
        if e.code in (MilestoneCode.DISCHARGED, MilestoneCode.VESSEL_ARRIVED)
        and e is not discharge
        and e is not arrival
    }
    ranked = [e.code for e in actual if e.code in ORDER and id(e) not in elsewhere]
    milestone = ContainerMilestone.BOOKED
    for code in sorted(ranked, key=ORDER.index):
        milestone = CONTAINER_MILESTONE.get(code, milestone)
    estimates = sorted(
        (e for e in events if e.is_estimate and e.code == MilestoneCode.VESSEL_ARRIVED),
        key=lambda e: e.occurred_at,
    )
    return ContainerSnapshot(
        milestone=milestone,
        eta=estimates[-1].occurred_at if estimates else None,
        ata=arrival.occurred_at if arrival else None,
        discharged_at=discharge.occurred_at if discharge else None,
        gate_out_at=_first_actual(actual, MilestoneCode.GATE_OUT_FULL),
        empty_returned_at=_first_actual(actual, MilestoneCode.GATE_IN_EMPTY_RETURN),
        discharge_unlocode=discharge.location_unlocode if discharge else None,
        discharge_port_confirmed=confirmed,
    )


def free_days_for(container: Container, org: Organization, *, detention: bool = False) -> int:
    """Free days: the container's own value, else the org's per-carrier setting, else the org default.

    The exact counting rule differs by terminal and by carrier, so it is a setting, never a guess.
    `detention` asks for the empty-return clock rather than the terminal one.
    """
    own = container.free_days_detention if detention else container.free_days_demurrage
    if own is not None:
        return own
    key = "free_days_detention" if detention else "free_days"
    per_carrier = org.settings.get(key) if isinstance(org.settings, dict) else None
    if isinstance(per_carrier, dict) and container.carrier_scac:
        value = per_carrier.get(container.carrier_scac)
        if isinstance(value, int):
            return value
    # The column defaults, repeated for an organization that has not been through the database yet.
    fallback = DEFAULT_DETENTION_DAYS if detention else DEFAULT_DEMURRAGE_DAYS
    org_value = org.free_days_detention if detention else org.free_days_demurrage
    return org_value if org_value is not None else fallback


def risk_band(days_left: int) -> DndRisk:
    if days_left < 0:
        return DndRisk.INCURRING
    if days_left <= 1:
        return DndRisk.HIGH
    if days_left <= 3:
        return DndRisk.MEDIUM
    return DndRisk.LOW


def risk_rose(before: DndRisk | None, after: DndRisk | None) -> bool:
    """True when the band got worse. The clock only ever gets worse; a downgrade is not news."""
    if after is None or after is DndRisk.NONE:
        return False
    if before is None:
        return True
    return RISK_ORDER.index(after) > RISK_ORDER.index(before)


def port_zone(container: Container, org: Organization) -> ZoneInfo:
    """The calendar this container's free days are counted in.

    The port of destination first — it is the terminal that counts the days and issues the invoice —
    then whatever the organization set, then Paris.
    """
    shipment = container.shipment if container.shipment_id is not None else None
    destination = shipment.destination_unlocode if shipment is not None else None
    setting = org.settings.get("timezone") if isinstance(org.settings, dict) else None
    return zone_for(destination, setting if isinstance(setting, str) else None)


def running_clock(
    container: Container, org: Organization, now: datetime | None = None, *, zone: ZoneInfo | None = None
) -> DndClock | None:
    """Which clock is running and how it stands, or None when nothing is running.

    Nothing is running before the box lands and once the empty is back — those are the two states
    where saying anything at all would be an invention.
    """
    moment = now or datetime.now(UTC)
    where = zone or port_zone(container, org)
    today = local_date(moment, where)
    if container.empty_returned_at is not None:
        return None
    if container.gate_out_at is not None:
        days = free_days_for(container, org, detention=True)
        deadline = local_date(container.gate_out_at, where) + timedelta(days=days - 1)
        return DndClock("detention", deadline, (deadline - today).days)
    if container.discharged_at is not None:
        days = free_days_for(container, org)
        deadline = local_date(container.discharged_at, where) + timedelta(days=days - 1)
        return DndClock("demurrage", deadline, (deadline - today).days)
    return None


def derive_dnd(container: Container, org: Organization, now: datetime | None = None) -> None:
    """Set `last_free_day`, `detention_deadline` and `dnd_risk` in place. Port-local calendar days.

    Two clocks, one after the other: demurrage runs at the terminal from discharge until the box is
    picked up, then detention runs from pickup until the empty is back. The risk shown is whichever
    one is running now.
    """
    moment = now or datetime.now(UTC)
    where = port_zone(container, org)

    container.last_free_day = (
        local_date(container.discharged_at, where) + timedelta(days=free_days_for(container, org) - 1)
        if container.discharged_at
        else None
    )
    container.detention_deadline = (
        local_date(container.gate_out_at, where)
        + timedelta(days=free_days_for(container, org, detention=True) - 1)
        if container.gate_out_at
        else None
    )

    clock = running_clock(container, org, moment, zone=where)
    container.dnd_risk = clock.risk if clock is not None else DndRisk.NONE


def apply_snapshot(container: Container, snapshot: ContainerSnapshot) -> None:
    """Move the container forward. Timestamps already known are kept: the first actual wins, and a
    provider that stops reporting a milestone never erases it."""
    if ORDER.index(MilestoneCode(snapshot.milestone.value)) >= ORDER.index(
        MilestoneCode(container.milestone.value)
    ):
        container.milestone = snapshot.milestone
    for field_name in ("ata", "discharged_at", "gate_out_at", "empty_returned_at"):
        value = getattr(snapshot, field_name)
        if value is not None:
            setattr(container, field_name, value)


def eta_shift(previous: datetime | None, new: datetime | None) -> tuple[Decimal, str] | None:
    """`(delta_hours, severity)` when an ETA *move* is worth recording, else None. Decimal, never float.

    A first ETA is not a change: it is recorded on the container, not in the history."""
    if new is None or previous is None:
        return None
    seconds = Decimal(int((new - previous).total_seconds()))
    hours = (seconds / Decimal(3600)).quantize(Decimal("0.1"))
    if abs(hours) < Decimal(ETA_SHIFT_HOURS):
        return None
    return (hours, "warning" if abs(hours) > Decimal(ETA_WARNING_HOURS) else "info")
