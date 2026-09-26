"""The tracking port: one neutral vocabulary that every provider is translated into.

Modelled on what Vizion, Terminal49 and project44 have in common (DCSA-ish): you *subscribe* with an
identifier and a carrier SCAC, you receive *snapshots* of milestones (never deltas), and each milestone
carries a standardised code, a timestamp, planned/actual, a UN/LOCODE and a vessel/voyage.

Nothing here knows about HTTP, SQLAlchemy or any vendor. Adapters live in `app/adapters/tracking/`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

from app.core.errors import DomainError, Unauthorized
from app.domain.models import ContainerMilestone, MilestoneCode

#: Chronological order used to pick "where the box is now". `UNKNOWN` is deliberately absent: an
#: unmapped provider event is stored and shown, but never moves the container forward.
ORDER: tuple[MilestoneCode, ...] = (
    MilestoneCode.BOOKED,
    MilestoneCode.GATE_OUT_EMPTY_ORIGIN,
    MilestoneCode.GATE_IN_FULL_ORIGIN,
    MilestoneCode.LOADED,
    MilestoneCode.VESSEL_DEPARTED,
    MilestoneCode.TRANSSHIPMENT_ARRIVED,
    MilestoneCode.TRANSSHIPMENT_DISCHARGED,
    MilestoneCode.TRANSSHIPMENT_LOADED,
    MilestoneCode.TRANSSHIPMENT_DEPARTED,
    MilestoneCode.VESSEL_ARRIVED,
    MilestoneCode.DISCHARGED,
    MilestoneCode.AVAILABLE_FOR_PICKUP,
    MilestoneCode.GATE_OUT_FULL,
    MilestoneCode.RAIL_LOADED,
    MilestoneCode.RAIL_DEPARTED,
    MilestoneCode.RAIL_ARRIVED,
    MilestoneCode.RAIL_UNLOADED,
    MilestoneCode.DELIVERED,
    MilestoneCode.GATE_IN_EMPTY_RETURN,
)

#: Codes that also move `containers.milestone`. The rest (empty pickup at origin, transshipment legs,
#: rail legs, unknown) are timeline detail.
CONTAINER_MILESTONE: dict[MilestoneCode, ContainerMilestone] = {
    MilestoneCode.BOOKED: ContainerMilestone.BOOKED,
    MilestoneCode.GATE_IN_FULL_ORIGIN: ContainerMilestone.GATE_IN_FULL_ORIGIN,
    MilestoneCode.LOADED: ContainerMilestone.LOADED,
    MilestoneCode.VESSEL_DEPARTED: ContainerMilestone.VESSEL_DEPARTED,
    MilestoneCode.VESSEL_ARRIVED: ContainerMilestone.VESSEL_ARRIVED,
    MilestoneCode.DISCHARGED: ContainerMilestone.DISCHARGED,
    MilestoneCode.AVAILABLE_FOR_PICKUP: ContainerMilestone.AVAILABLE_FOR_PICKUP,
    MilestoneCode.GATE_OUT_FULL: ContainerMilestone.GATE_OUT_FULL,
    MilestoneCode.DELIVERED: ContainerMilestone.DELIVERED,
    MilestoneCode.GATE_IN_EMPTY_RETURN: ContainerMilestone.GATE_IN_EMPTY_RETURN,
}

DataSource = Literal["carrier", "terminal", "ais", "manual", "unknown"]


@dataclass(frozen=True)
class ProviderEvent:
    """One milestone as the provider told it.

    `provider_event_key` is the adapter's stable de-duplication key: replaying the same snapshot must
    produce the same key, and an estimate that becomes actual must produce a *different* one, so both
    rows survive (the carrier's broken promises are evidence in a demurrage dispute).
    """

    code: MilestoneCode
    occurred_at: datetime
    is_estimate: bool
    provider_event_key: str
    location_unlocode: str | None = None
    location_name: str | None = None
    vessel_name: str | None = None
    voyage: str | None = None
    raw_description: str = ""
    source: DataSource = "unknown"


@dataclass(frozen=True)
class SubscribeRequest:
    identifier: str
    identifier_type: Literal["container", "bill_of_lading", "booking"]
    scac: str | None
    callback_url: str
    external_ref: str  # our container id, echoed back as a tag when the provider supports it


@dataclass(frozen=True)
class Subscription:
    provider: str
    provider_ref: str
    status: Literal["pending", "active", "failed"]
    message: str | None = None


@dataclass(frozen=True)
class WebhookEnvelope:
    """One verified delivery, translated.

    `provider_ref` and `container_number` are the two ways a delivery can be routed to one of our
    containers; a provider that only knows the box number leaves `provider_ref` empty.
    """

    provider: str
    delivery_id: str
    events: list[ProviderEvent] = field(default_factory=list)
    provider_ref: str | None = None
    container_number: str | None = None
    eta: datetime | None = None
    destination_unlocode: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    #: The other containers this one delivery is about, each already routed to its own box.
    #:
    #: A shipment-level notification — "the arrival on this bill of lading moved four days" — carries
    #: every container on the bill. Reading only the first left the other two boxes of the same
    #: vessel showing a date the carrier had already changed, on the same screen, side by side.
    #: Each sibling carries its own events and its own ETA; none of them carries siblings itself.
    others: list[WebhookEnvelope] = field(default_factory=list)


class InvalidSignature(Unauthorized):
    """The delivery is not provably from the provider (bad HMAC, missing header, or too old)."""

    code = "INVALID_SIGNATURE"


class ProviderNotConfigured(DomainError):
    """The operation needs a credential the deployment does not have (typically an API key)."""

    status = 501
    code = "PROVIDER_NOT_CONFIGURED"


class ProviderUnavailable(DomainError):
    status = 502
    code = "PROVIDER_UNAVAILABLE"


@runtime_checkable
class TrackingProvider(Protocol):
    name: str

    def subscribe(self, req: SubscribeRequest) -> Subscription: ...

    def unsubscribe(self, provider_ref: str) -> None: ...

    def fetch(self, provider_ref: str) -> WebhookEnvelope:
        """Fallback polling, for when a webhook was lost."""
        ...

    def verify_and_parse(self, headers: Mapping[str, str], body: bytes) -> WebhookEnvelope:
        """Raise `InvalidSignature` unless the body is provably the provider's, then translate it."""
        ...

    def parse(self, body: bytes) -> WebhookEnvelope:
        """Translate a body whose signature was already checked, for replaying a stored delivery."""
        ...
