"""Phase 1 schema.

Rules: UUID keys, `org_id` on every business table (denormalised, required for RLS), NUMERIC for money and
quantities, timestamps in UTC. A cost has exactly one scope and is always allocated down to the leaves,
`container_loads` (purchase-order line x container x quantity).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    CHAR,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB().with_variant(JSON(), "sqlite")}


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------- enums


class AllocationMethod(enum.StrEnum):
    BY_VALUE = "BY_VALUE"
    BY_WEIGHT = "BY_WEIGHT"
    BY_VOLUME = "BY_VOLUME"
    BY_QUANTITY = "BY_QUANTITY"
    MANUAL = "MANUAL"
    BY_CIF_VALUE = "BY_CIF_VALUE"
    BY_THEORETICAL_DUTY = "BY_THEORETICAL_DUTY"


class CostScope(enum.StrEnum):
    SHIPMENT = "SHIPMENT"
    CONTAINER = "CONTAINER"
    PO = "PO"
    PO_LINE = "PO_LINE"


class CostType(enum.StrEnum):
    OCEAN_FREIGHT = "OCEAN_FREIGHT"
    AIR_FREIGHT = "AIR_FREIGHT"
    INSURANCE = "INSURANCE"
    ORIGIN_CHARGES = "ORIGIN_CHARGES"
    THC = "THC"
    BL_FEE = "BL_FEE"
    CUSTOMS_DUTY = "CUSTOMS_DUTY"
    CUSTOMS_BROKERAGE = "CUSTOMS_BROKERAGE"
    IMPORT_VAT = "IMPORT_VAT"
    DRAYAGE = "DRAYAGE"
    DEMURRAGE = "DEMURRAGE"
    DETENTION = "DETENTION"
    WAREHOUSING = "WAREHOUSING"
    INSPECTION = "INSPECTION"
    BANK_FEES = "BANK_FEES"
    OTHER = "OTHER"


class CostStatus(enum.StrEnum):
    """A cost is either what someone expects to pay or what was actually invoiced.

    The whole point of the distinction: a landed cost can be right on the day the container lands,
    from estimates, and become exact as invoices arrive — with the gap between the two visible.
    """

    ESTIMATE = "ESTIMATE"
    ACTUAL = "ACTUAL"


class ContainerMilestone(enum.StrEnum):
    BOOKED = "BOOKED"
    GATE_IN_FULL_ORIGIN = "GATE_IN_FULL_ORIGIN"
    LOADED = "LOADED"
    VESSEL_DEPARTED = "VESSEL_DEPARTED"
    VESSEL_ARRIVED = "VESSEL_ARRIVED"
    DISCHARGED = "DISCHARGED"
    AVAILABLE_FOR_PICKUP = "AVAILABLE_FOR_PICKUP"
    GATE_OUT_FULL = "GATE_OUT_FULL"
    DELIVERED = "DELIVERED"
    GATE_IN_EMPTY_RETURN = "GATE_IN_EMPTY_RETURN"


class MilestoneCode(enum.StrEnum):
    """Tracking timeline vocabulary: every milestone a provider can report.

    A superset of `ContainerMilestone` (the coarser state carried on the container itself); the extra
    codes — empty pickup at origin, transshipment and rail legs, `UNKNOWN` — stay in the timeline.
    """

    BOOKED = "BOOKED"
    GATE_OUT_EMPTY_ORIGIN = "GATE_OUT_EMPTY_ORIGIN"
    GATE_IN_FULL_ORIGIN = "GATE_IN_FULL_ORIGIN"
    LOADED = "LOADED"
    VESSEL_DEPARTED = "VESSEL_DEPARTED"
    TRANSSHIPMENT_ARRIVED = "TRANSSHIPMENT_ARRIVED"
    TRANSSHIPMENT_DISCHARGED = "TRANSSHIPMENT_DISCHARGED"
    TRANSSHIPMENT_LOADED = "TRANSSHIPMENT_LOADED"
    TRANSSHIPMENT_DEPARTED = "TRANSSHIPMENT_DEPARTED"
    VESSEL_ARRIVED = "VESSEL_ARRIVED"
    DISCHARGED = "DISCHARGED"
    AVAILABLE_FOR_PICKUP = "AVAILABLE_FOR_PICKUP"
    GATE_OUT_FULL = "GATE_OUT_FULL"
    RAIL_LOADED = "RAIL_LOADED"
    RAIL_DEPARTED = "RAIL_DEPARTED"
    RAIL_ARRIVED = "RAIL_ARRIVED"
    RAIL_UNLOADED = "RAIL_UNLOADED"
    DELIVERED = "DELIVERED"
    GATE_IN_EMPTY_RETURN = "GATE_IN_EMPTY_RETURN"
    UNKNOWN = "UNKNOWN"


class TrackingState(enum.StrEnum):
    UNTRACKED = "UNTRACKED"
    MANUAL = "MANUAL"
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    ENDED = "ENDED"


class DndRisk(enum.StrEnum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    INCURRING = "INCURRING"


class AlertKind(enum.StrEnum):
    DND_RISK = "DND_RISK"  # the demurrage clock crossed a threshold
    ETA_CHANGED = "ETA_CHANGED"
    TRACKING_DATA_MISSING = "TRACKING_DATA_MISSING"  # a subscription has gone quiet
    TRACKING_DELIVERY_FAILED = "TRACKING_DELIVERY_FAILED"  # a webhook we could not process
    #: The provider itself is not answering. Deliberately not TRACKING_DATA_MISSING: that one says
    #: "this container has gone quiet, go and look at the carrier's site", which is an accusation
    #: aimed at the wrong party when the truth is that our supplier is down.
    TRACKING_PROVIDER_DOWN = "TRACKING_PROVIDER_DOWN"


class AlertSeverity(enum.StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ErpKind(enum.StrEnum):
    ODOO = "ODOO"


class ErpSyncStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class RateBasis(enum.StrEnum):
    """How a rate card turns into an amount. What the forwarder's price list actually says."""

    FLAT = "FLAT"  # the amount, once, per target
    PER_100KG = "PER_100KG"
    PER_CBM = "PER_CBM"
    PCT_OF_FOB = "PCT_OF_FOB"  # amount is a percentage: 4.50 means 4.5 %


class MemberRole(enum.StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    VIEWER = "VIEWER"


class Incoterm(enum.StrEnum):
    EXW = "EXW"
    FCA = "FCA"
    FOB = "FOB"
    CFR = "CFR"
    CIF = "CIF"
    DAP = "DAP"
    DDP = "DDP"


class PurchaseOrderStatus(enum.StrEnum):
    """Open, or cancelled upstream. Cancelling never deletes: the order may already carry costs."""

    OPEN = "OPEN"
    CANCELLED = "CANCELLED"


class ImportKind(enum.StrEnum):
    PURCHASE_ORDERS = "PURCHASE_ORDERS"  # one row per PO line
    LEGACY_PO_CONTAINER = "LEGACY_PO_CONTAINER"  # Phase 0 format: one row per PO with a container
    CONTAINERS = "CONTAINERS"  # the containers' tracking: one row per container and order
    COSTS = "COSTS"  # a costs ledger: one row per invoice line, actual costs only
    PRODUCTS = "PRODUCTS"  # the tariff: one row per article


class InvoiceStatus(enum.StrEnum):
    """An invoice's life. Nothing becomes a cost without a human passing through NEEDS_REVIEW."""

    UPLOADED = "UPLOADED"
    EXTRACTING = "EXTRACTING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class ImportStatus(enum.StrEnum):
    PARSED = "PARSED"
    VALIDATED = "VALIDATED"
    DONE = "DONE"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------- mixins


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class OrgScoped:
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)


# ---------------------------------------------------------------------------- identity


class Organization(Timestamped, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str | None] = mapped_column(String, unique=True)
    external_id: Mapped[str | None] = mapped_column(String, unique=True)  # Clerk organization id
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="EUR")
    default_allocation_method: Mapped[AllocationMethod] = mapped_column(
        Enum(AllocationMethod, name="allocation_method"), nullable=False, default=AllocationMethod.BY_VALUE
    )
    default_incoterm: Mapped[Incoterm | None] = mapped_column(Enum(Incoterm, name="incoterm"))
    free_days_demurrage: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    free_days_detention: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    #: The language everything this organization is sent is written in — the alert digests today.
    #: French by default: the product is sold to French importers, and a Clerk account says nothing
    #: about what language the company works in.
    locale: Mapped[str] = mapped_column(String(5), nullable=False, default="fr")
    settings: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class User(Timestamped, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    external_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)  # Clerk user id
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String)


class Membership(Base):
    __tablename__ = "memberships"

    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole, name="member_role"), nullable=False, default=MemberRole.MEMBER
    )


# ---------------------------------------------------------------------------- business


class Supplier(Timestamped, OrgScoped, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str | None] = mapped_column(CHAR(2))
    default_currency: Mapped[str | None] = mapped_column(CHAR(3))


class Shipment(Timestamped, OrgScoped, Base):
    __tablename__ = "shipments"
    __table_args__ = (UniqueConstraint("org_id", "reference"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    reference: Mapped[str] = mapped_column(String, nullable=False)  # BL or booking
    carrier_scac: Mapped[str | None] = mapped_column(CHAR(4))
    incoterm: Mapped[Incoterm | None] = mapped_column(Enum(Incoterm, name="incoterm"))
    origin_unlocode: Mapped[str | None] = mapped_column(CHAR(5))
    destination_unlocode: Mapped[str | None] = mapped_column(CHAR(5))
    etd: Mapped[date | None] = mapped_column(Date)
    eta: Mapped[date | None] = mapped_column(Date)

    containers: Mapped[list[Container]] = relationship(back_populates="shipment")


class Container(Timestamped, OrgScoped, Base):
    __tablename__ = "containers"
    __table_args__ = (
        CheckConstraint("container_number ~ '^[A-Z]{4}[0-9]{7}$'", name="iso6346"),
        Index(
            "uq_containers_active_number",
            "org_id",
            "container_number",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    container_number: Mapped[str] = mapped_column(CHAR(11), nullable=False)
    iso_type: Mapped[str | None] = mapped_column(String)
    carrier_scac: Mapped[str | None] = mapped_column(CHAR(4))
    milestone: Mapped[ContainerMilestone] = mapped_column(
        Enum(ContainerMilestone, name="container_milestone"),
        nullable=False,
        default=ContainerMilestone.BOOKED,
    )
    tracking_state: Mapped[TrackingState] = mapped_column(
        Enum(TrackingState, name="tracking_state"), nullable=False, default=TrackingState.MANUAL
    )
    eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ata: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discharged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gate_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    empty_returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    free_days_demurrage: Mapped[int | None] = mapped_column(Integer)
    free_days_detention: Mapped[int | None] = mapped_column(Integer)
    last_free_day: Mapped[date | None] = mapped_column(Date)
    #: gate-out + free detention days - 1: when the empty has to be back before detention runs.
    detention_deadline: Mapped[date | None] = mapped_column(Date)
    dnd_risk: Mapped[DndRisk] = mapped_column(
        Enum(DndRisk, name="dnd_risk"), nullable=False, default=DndRisk.NONE
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    shipment: Mapped[Shipment | None] = relationship(back_populates="containers")
    loads: Mapped[list[ContainerLoad]] = relationship(
        back_populates="container", cascade="all, delete-orphan"
    )


class PurchaseOrder(Timestamped, OrgScoped, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (UniqueConstraint("org_id", "po_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    po_number: Mapped[str] = mapped_column(String, nullable=False)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), index=True)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=Decimal("1"))
    fx_date: Mapped[date | None] = mapped_column(Date)
    incoterm: Mapped[Incoterm | None] = mapped_column(Enum(Incoterm, name="incoterm"))
    order_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        Enum(PurchaseOrderStatus, name="purchase_order_status"),
        nullable=False,
        default=PurchaseOrderStatus.OPEN,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    supplier: Mapped[Supplier | None] = relationship()
    lines: Mapped[list[PurchaseOrderLine]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="PurchaseOrderLine.line_no"
    )


class PurchaseOrderLine(OrgScoped, Base):
    __tablename__ = "po_lines"
    __table_args__ = (
        UniqueConstraint("po_id", "line_no"),
        CheckConstraint("quantity > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="price_nonneg"),
        Index("ix_po_lines_org_sku", "org_id", "sku"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"), nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    sku: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    hs_code: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)  # in PO currency
    unit_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_volume_cbm: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    duty_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # 0.0450 = 4.5 %

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")
    loads: Mapped[list[ContainerLoad]] = relationship(back_populates="po_line", cascade="all, delete-orphan")


class ContainerLoad(OrgScoped, Base):
    """The leaf: a quantity of one PO line inside one container."""

    __tablename__ = "container_loads"
    __table_args__ = (
        UniqueConstraint("container_id", "po_line_id"),
        CheckConstraint("quantity > 0", name="qty_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("containers.id"), nullable=False, index=True)
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("po_lines.id"), nullable=False, index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)

    container: Mapped[Container] = relationship(back_populates="loads")
    po_line: Mapped[PurchaseOrderLine] = relationship(back_populates="loads")


class Cost(Timestamped, OrgScoped, Base):
    __tablename__ = "costs"
    __table_args__ = (
        CheckConstraint("num_nonnulls(shipment_id, container_id, po_id, po_line_id) = 1", name="one_scope"),
        CheckConstraint("(allocation_method = 'MANUAL') = (manual_splits IS NOT NULL)", name="manual_splits"),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
        CheckConstraint("closed_at IS NULL OR status = 'ESTIMATE'", name="estimate_only_close"),
        CheckConstraint("supersedes_cost_id IS NULL OR status = 'ACTUAL'", name="actual_only_supersede"),
        Index("ix_costs_org_status", "org_id", "status", postgresql_where=text("closed_at IS NULL")),
        # One invoice line is one charge of one type, in one currency, on one target: the freight of
        # each container of a bill of lading, or a freight and its bunker surcharge merged into one
        # cost, never collide; the same line recorded twice still does (migration 0028).
        Index(
            "uq_costs_invoice_line",
            "org_id",
            "vendor",
            "invoice_number",
            "cost_type",
            "currency",
            text("COALESCE(container_id, shipment_id, po_id, po_line_id)"),
            unique=True,
            postgresql_where=text("invoice_number IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scope: Mapped[CostScope] = mapped_column(Enum(CostScope, name="cost_scope"), nullable=False)
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shipments.id"), index=True)
    container_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("containers.id"), index=True)
    po_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("po_lines.id"), index=True)
    cost_type: Mapped[CostType] = mapped_column(Enum(CostType, name="cost_type"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fx_date: Mapped[date] = mapped_column(Date, nullable=False)
    fx_source: Mapped[str] = mapped_column(String, nullable=False)  # ecb | ecb_latest | manual | same
    amount_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    allocation_method: Mapped[AllocationMethod] = mapped_column(
        Enum(AllocationMethod, name="allocation_method"), nullable=False
    )
    manual_splits: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    cost_date: Mapped[date] = mapped_column(Date, nullable=False)
    vendor: Mapped[str | None] = mapped_column(String)
    invoice_number: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[CostStatus] = mapped_column(
        Enum(CostStatus, name="cost_status"), nullable=False, default=CostStatus.ACTUAL
    )
    #: The estimate this actual cost replaces. Unique: an estimate is superseded once, by one invoice.
    supersedes_cost_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("costs.id", ondelete="SET NULL"), unique=True
    )
    #: An estimate closed without an invoice ever arriving — "the forwarder never billed it".
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    close_reason: Mapped[str | None] = mapped_column(String)
    #: The costs file this cost was read from: the whole file can be taken back, and nothing else.
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="SET NULL"), index=True
    )
    #: The type was inferred — from the line's label, or the type a person gave the whole file — not
    #: read from a column: what the audit finds on such a cost is a question, never a fact.
    type_inferred: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: The rows of the costs file this cost was written from, as their source said them: the next copy
    #: of the same ledger recognises its line by them, whatever a person has changed on the cost since.
    import_row_key: Mapped[str | None] = mapped_column(String(64))

    allocations: Mapped[list[CostAllocation]] = relationship(
        back_populates="cost", cascade="all, delete-orphan"
    )
    supersedes: Mapped[Cost | None] = relationship(remote_side="Cost.id", back_populates="superseded_by")
    superseded_by: Mapped[Cost | None] = relationship(back_populates="supersedes", uselist=False)


class CostAllocation(OrgScoped, Base):
    """Materialised output of the engine. Recomputed in the same transaction as any write that affects it."""

    __tablename__ = "cost_allocations"
    __table_args__ = (UniqueConstraint("cost_id", "container_load_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    cost_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("costs.id", ondelete="CASCADE"), nullable=False)
    container_load_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("container_loads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    cost: Mapped[Cost] = relationship(back_populates="allocations")


class FxRate(Base):
    __tablename__ = "fx_rates"

    base: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    quote: Mapped[str] = mapped_column(CHAR(3), primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date, primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)  # 1 quote = rate base
    source: Mapped[str] = mapped_column(String, nullable=False)


# ---------------------------------------------------------------------------- imports


class ImportJob(Timestamped, OrgScoped, Base):
    __tablename__ = "import_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[ImportKind] = mapped_column(Enum(ImportKind, name="import_kind"), nullable=False)
    status: Mapped[ImportStatus] = mapped_column(
        Enum(ImportStatus, name="import_status"), nullable=False, default=ImportStatus.PARSED
    )
    original_filename: Mapped[str | None] = mapped_column(String)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[bytes] = mapped_column(nullable=False)
    encoding: Mapped[str | None] = mapped_column(String)
    delimiter: Mapped[str | None] = mapped_column(String)
    columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    mapping: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    report: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: A costs file taken back: its costs are gone, the job stays as the record that they came and went.
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What the preview ran with besides the mapping — the type of the rows that name none, the types a
    #: person gave the labels it could not read. The commit runs with exactly that, never another.
    options: Mapped[dict[str, Any]] = mapped_column(
        nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    #: A costs file's lines that an earlier import had written already: left alone here, so that file
    #: cannot be taken back while this one stands on them.
    matched_cost_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: A costs file's rows refused whose charge is therefore not in the books — the date, the amount,
    #: the target when it was read — for the audit's preparation to say until a cost holds them.
    refused_rows: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )


class ImportMapping(OrgScoped, Base):
    __tablename__ = "import_mappings"
    __table_args__ = (UniqueConstraint("org_id", "kind", "header_signature"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[ImportKind] = mapped_column(Enum(ImportKind, name="import_kind"), nullable=False)
    header_signature: Mapped[str] = mapped_column(String, nullable=False)
    mapping: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


# ---------------------------------------------------------------------------- tracking


class TrackingSubscription(Timestamped, OrgScoped, Base):
    """One container watched at one provider. `provider_ref` is the provider's own handle for it;
    it is null while a webhook-only deployment routes deliveries by container number."""

    __tablename__ = "tracking_subscriptions"
    __table_args__ = (
        UniqueConstraint("container_id"),
        UniqueConstraint("provider", "provider_ref"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("containers.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_ref: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")  # pending|active|failed
    last_error: Mapped[str | None] = mapped_column(String)
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookDelivery(Base):
    """Raw provider deliveries, kept whole. Not org-scoped and not under RLS: a delivery is written
    before we know which tenant it belongs to, and it holds no tenant data of its own beyond the
    provider's payload. `(provider, delivery_id)` is the idempotency key."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("provider", "delivery_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    delivery_id: Mapped[str] = mapped_column(String, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    headers: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signature_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="received"
    )  # received|processed|failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String)


class JobRun(Base):
    """When each background job last got through its work (migration 0021). Like
    `webhook_deliveries`, nobody's data: no `org_id`, no RLS. The jobs read and write it with raw
    SQL; it is declared here so that `alembic check` knows the table instead of proposing to drop it."""

    __tablename__ = "job_runs"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    detail: Mapped[str | None] = mapped_column(String)


class TrackingEvent(OrgScoped, Base):
    """Append-only milestone history. Nothing here is ever updated: a snapshot replayed inserts
    nothing (ON CONFLICT DO NOTHING), and an estimate that becomes actual is a new row, because the
    carrier's earlier promise is evidence in a demurrage dispute."""

    __tablename__ = "tracking_events"
    __table_args__ = (
        UniqueConstraint("org_id", "container_id", "provider", "provider_event_key"),
        Index("ix_tracking_events_container_time", "container_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("containers.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_event_key: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[MilestoneCode] = mapped_column(Enum(MilestoneCode, name="milestone_code"), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    location_unlocode: Mapped[str | None] = mapped_column(CHAR(5))
    location_name: Mapped[str | None] = mapped_column(String)
    vessel_name: Mapped[str | None] = mapped_column(String)
    voyage: Mapped[str | None] = mapped_column(String)
    raw_description: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    delivery_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("webhook_deliveries.id"))
    inserted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class EtaHistory(OrgScoped, Base):
    """Every ETA move worth telling the user about (more than 12 h). `severity` is `warning` past 48 h."""

    __tablename__ = "eta_history"
    __table_args__ = (Index("ix_eta_history_container", "container_id", "recorded_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("containers.id"), nullable=False)
    eta: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_eta: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delta_hours: Mapped[Decimal] = mapped_column(Numeric(8, 1), nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")  # info|warning
    source: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class Alert(OrgScoped, Base):
    """Something the user should look at. `dedup_key` is what stops the same fact being repeated:
    one alert per organization per key, so a daily recompute cannot pile up ten identical warnings."""

    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("org_id", "dedup_key"),
        Index("ix_alerts_org_unread", "org_id", "created_at", postgresql_where=text("read_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    container_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("containers.id"), index=True)
    kind: Mapped[AlertKind] = mapped_column(Enum(AlertKind, name="alert_kind"), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        # The Postgres enum holds the lowercase values, not the member names: severities are shown
        # as they are stored, and `info` reads better than `INFO` in an API payload.
        Enum(AlertSeverity, name="alert_severity", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=AlertSeverity.INFO,
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False, default="")
    dedup_key: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------- documents and invoices


class Document(OrgScoped, Base):
    """An uploaded file. The bytes live wherever the document store puts them — today the database,
    tomorrow object storage — and this row is the metadata either way."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("org_id", "storage_key", name="uq_documents_org_id"),
        # The same invoice cannot be uploaded twice. Named explicitly because the convention would
        # give both constraints the same name, which is the sort of thing `alembic check` catches.
        UniqueConstraint("org_id", "sha256", name="uq_documents_org_id_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    storage: Mapped[str] = mapped_column(String, nullable=False)  # postgres | s3
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    filename: Mapped[str | None] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class DocumentBlob(OrgScoped, Base):
    """The bytes, when the store is the database. Temporary, and deliberately its own table: object
    storage replaces this one row type and nothing else."""

    __tablename__ = "document_blobs"
    __table_args__ = (UniqueConstraint("org_id", "storage_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    storage_key: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Invoice(Timestamped, OrgScoped, Base):
    """A supplier invoice on its way to becoming costs — never automatically."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id"), nullable=False, index=True)
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(InvoiceStatus, name="invoice_status"), nullable=False, default=InvoiceStatus.UPLOADED
    )
    vendor: Mapped[str | None] = mapped_column(String)
    invoice_number: Mapped[str | None] = mapped_column(String)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    #: The pre-tax total the invoice prints, as it prints it — never total minus VAT.
    subtotal_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    vat_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    extractor: Mapped[str | None] = mapped_column(String)  # regex | llm
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    error: Mapped[str | None] = mapped_column(String)
    raw: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: The container whoever dropped the document said it is about. Used only for the lines on which
    #: the reading found no container or order of its own.
    default_container_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("containers.id", ondelete="SET NULL")
    )

    document: Mapped[Document] = relationship()
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", cascade="all, delete-orphan", order_by="InvoiceLine.line_no"
    )


class InvoiceLine(OrgScoped, Base):
    """One proposed cost. `accepted` starts false and only a person sets it."""

    __tablename__ = "invoice_lines"
    __table_args__ = (UniqueConstraint("invoice_id", "line_no"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False, default="")
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    cost_type: Mapped[CostType | None] = mapped_column(Enum(CostType, name="cost_type"))
    scope: Mapped[CostScope | None] = mapped_column(Enum(CostScope, name="cost_scope"))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False, default=Decimal("0"))
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(String)  # why a human should look twice
    cost_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("costs.id", ondelete="SET NULL"))

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class RateCard(Timestamped, OrgScoped, Base):
    """What this organization expects to pay, so an estimate can be produced without asking anyone.

    One card per (cost type, scope, HS code): a forwarder quotes one price for terminal handling per
    container, not three. The HS code is only used for customs duty, where the rate depends on what
    is in the box rather than on the box.
    """

    __tablename__ = "rate_cards"
    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "cost_type",
            "scope",
            "hs_code",
            name="uq_rate_cards_org_id",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("amount >= 0", name="amount_nonneg"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    cost_type: Mapped[CostType] = mapped_column(Enum(CostType, name="cost_type"), nullable=False)
    scope: Mapped[CostScope] = mapped_column(Enum(CostScope, name="cost_scope"), nullable=False)
    basis: Mapped[RateBasis] = mapped_column(
        Enum(RateBasis, name="rate_basis"), nullable=False, default=RateBasis.FLAT
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    #: Customs duty only: the tariff heading this rate applies to, matched by prefix.
    hs_code: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)


class ErpConnection(Timestamped, OrgScoped, Base):
    """How to reach one organization's ERP. One per organization, for now.

    The API key is stored sealed (AES-GCM, `ERP_ENCRYPTION_KEY`) and never leaves this table in
    clear: it is a credential to a customer's accounting system, which is about the most sensitive
    thing this application will ever hold.
    """

    __tablename__ = "erp_connections"
    __table_args__ = (UniqueConstraint("org_id", name="uq_erp_connections_org_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[ErpKind] = mapped_column(Enum(ErpKind, name="erp_kind"), nullable=False)
    url: Mapped[str] = mapped_column(String, nullable=False)
    database: Mapped[str] = mapped_column(String, nullable=False)
    login: Mapped[str] = mapped_column(String, nullable=False)
    api_key_sealed: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String)
    #: How many scheduled reads have failed in a row, and when the schedule may try again. A
    #: customer whose ERP has been down for a week should not be dialled every morning to produce
    #: the same failure row; someone pressing the button is another matter and is never held back.
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_version: Mapped[str | None] = mapped_column(String)
    company: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ErpSyncRun(OrgScoped, Base):
    """One attempt at reading the ERP, kept whether it worked or not."""

    __tablename__ = "erp_sync_runs"
    __table_args__ = (Index("ix_erp_sync_runs_org_started", "org_id", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("erp_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ErpSyncStatus] = mapped_column(
        Enum(ErpSyncStatus, name="erp_sync_status"), nullable=False, default=ErpSyncStatus.RUNNING
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The import job this run drove, so the same report is readable from either side.
    import_job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("import_jobs.id"))
    purchase_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lines: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String)
    detail: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)


class ErpPush(OrgScoped, Base):
    """One landed cost written into a customer's ERP, and which of our costs it carried.

    Kept so the next push carries only what no document has carried yet: a landed cost adds to the
    stock valuation rather than replacing it, so a cost pushed twice is charged to the goods twice.

    Never deleted. A push can be *forgotten* — when the draft it made was deleted in the ERP and its
    costs have to become pushable again — and forgetting is a row that says so, with a reason and an
    audit entry. Deleting it would erase the only evidence that we once wrote in someone's books.
    """

    __tablename__ = "erp_pushes"
    __table_args__ = (Index("ix_erp_pushes_org_container", "org_id", "container_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("erp_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    container_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("containers.id"), nullable=False)
    #: The costs this push carried, as strings, so the row survives a cost being deleted.
    cost_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: What each of them was when it was written — `cost id -> {"amount": "2766.67", "label": …}` —
    #: so a cost edited or deleted afterwards is noticed and still has a name: the draft in the ERP
    #: carries the old figure, and nothing over there says so.
    carried: Mapped[dict[str, dict[str, str]]] = mapped_column(JSONB, nullable=False, default=dict)
    odoo_model: Mapped[str] = mapped_column(String, nullable=False)
    odoo_id: Mapped[int] = mapped_column(Integer, nullable=False)
    odoo_name: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    response: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: Set when the document this push made is gone from the ERP and its costs may be pushed again.
    #: The row stays; what changes is whether it still holds those costs.
    forgotten_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    forgotten_reason: Mapped[str | None] = mapped_column(String)
    forgotten_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    @property
    def live(self) -> bool:
        """Still holding its costs. A forgotten push holds nothing."""
        return self.forgotten_at is None


class Product(Timestamped, OrgScoped, Base):
    """What an organization knows about an article once and for all: what it is, how it is taxed,
    what it weighs, and what it sells for.

    An order line still carries its own copy of the description, tariff heading, duty rate, weight
    and volume — those are facts about that purchase, and a landed cost already computed must not
    move because somebody edited a catalogue. The catalogue fills the blanks of a *new* line, and
    holds the one thing no order knows: the selling price, which is what turns a landed cost into a
    margin.
    """

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("org_id", "sku", name="uq_products_org_sku"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String)
    hs_code: Mapped[str | None] = mapped_column(String)
    duty_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # 0.0450 = 4.5 %
    unit_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_volume_cbm: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    #: Excluding VAT, per unit. Null currency means the organization's own.
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    sale_currency: Mapped[str | None] = mapped_column(CHAR(3))


class PeriodClose(OrgScoped, Base):
    """A month, closed: the landed cost of the containers that arrived in it, as it stood that day.

    This is the figure that went into the accounts, and it never changes again. What happens to
    those containers afterwards — the forwarder's invoice six weeks late — still happens in the live
    numbers; the difference between the two is reported as such, to be booked in the open month.
    """

    __tablename__ = "period_closes"
    __table_args__ = (UniqueConstraint("org_id", "period", name="uq_period_closes_org_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    period: Mapped[str] = mapped_column(CHAR(7), nullable=False)  # YYYY-MM
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    closed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    containers: Mapped[int] = mapped_column(Integer, nullable=False)
    fob: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    landed: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    by_cost_type: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)
    #: How much of the drift since the close has been booked as an adjustment in a later month. The
    #: month stops asking for it — and asks again if another late cost moves the figure further.
    acknowledged_drift: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False, default=Decimal("0"))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    acknowledged_note: Mapped[str | None] = mapped_column(String)

    lines: Mapped[list[PeriodCloseLine]] = relationship(
        back_populates="close", cascade="all, delete-orphan", order_by="PeriodCloseLine.container_number"
    )
    accruals: Mapped[list[PeriodCloseAccrual]] = relationship(
        back_populates="close", cascade="all, delete-orphan", order_by="PeriodCloseAccrual.container_number"
    )


class PeriodCloseLine(OrgScoped, Base):
    """One order line in one container, as frozen. It names what it froze (container number, order
    number, SKU) as well as pointing at it: a container archived next year is still in the close."""

    __tablename__ = "period_close_lines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    close_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("period_closes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    container_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    container_number: Mapped[str] = mapped_column(String, nullable=False)
    arrived_on: Mapped[date | None] = mapped_column(Date)
    load_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    po_number: Mapped[str] = mapped_column(String, nullable=False)
    sku: Mapped[str | None] = mapped_column(String)
    description: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    fob: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    landed: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    by_cost_type: Mapped[dict[str, Any]] = mapped_column(nullable=False, default=dict)

    close: Mapped[PeriodClose] = relationship(back_populates="lines")


class PeriodCloseAccrual(OrgScoped, Base):
    """What had been received and not yet invoiced when the month was closed: the estimates no invoice
    had replaced, on containers that had landed by the last day of the month. The accountant calls it
    *factures non parvenues*; it is the one cut-off figure FreightSight knows and the books do not."""

    __tablename__ = "period_close_accruals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    close_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("period_closes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    container_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    container_number: Mapped[str] = mapped_column(String, nullable=False)
    arrived_on: Mapped[date | None] = mapped_column(Date)
    cost_type: Mapped[str] = mapped_column(String, nullable=False)
    amount_base: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)

    close: Mapped[PeriodClose] = relationship(back_populates="accruals")


class SharedReport(Timestamped, OrgScoped, Base):
    """A landed-cost report sent to somebody who has no account, frozen at the moment it was sent.

    The link is a bearer token: whoever holds it reads this one snapshot and nothing else. Only its
    SHA-256 is kept, so a copy of the database opens no link. The snapshot is the report as it stood —
    the prospect who opens it three weeks later sees the figure they were sent, not today's.
    """

    __tablename__ = "shared_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    subject_type: Mapped[str] = mapped_column(String, nullable=False)  # container | purchase_order | audit
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    subject_label: Mapped[str] = mapped_column(String, nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SharedReportView(OrgScoped, Base):
    """One opening of a shared report. Its own table because counting must not be an UPDATE of the
    share: the token that opens a link may add a line here, and may change nothing anywhere."""

    __tablename__ = "shared_report_views"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    share_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("shared_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class SampleObject(OrgScoped, Base):
    """One row per object the demo dataset created, so that it can be taken away again.

    A register rather than a `source` column on eleven tables: the business tables stay what they
    are, and "is this the demo's?" is one lookup. It is filled when the dataset is loaded — which only
    happens in an empty organization, so everything that exists at that moment is the demo's.
    """

    __tablename__ = "sample_objects"
    __table_args__ = (UniqueConstraint("org_id", "kind", "object_id", name="uq_sample_objects"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class SkuCostHistory(OrgScoped, Base):
    """The landed unit cost of a SKU, as it stood each day the numbers were recomputed.

    One row per (SKU, day): recomputing twice on the same day corrects the day rather than adding a
    second point, so the curve is a history of facts, not of button presses.
    """

    __tablename__ = "sku_cost_history"
    __table_args__ = (
        UniqueConstraint("org_id", "sku", "recorded_on", name="uq_sku_cost_history_org_id"),
        Index("ix_sku_cost_history_org_sku", "org_id", "sku", "recorded_on"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    recorded_on: Mapped[date] = mapped_column(Date, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    fob_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    landed_base: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    unit_landed_cost: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    load_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ---------------------------------------------------------------------------- audit


class AuditLog(OrgScoped, Base):
    """Who changed what, kept forever and never rewritten.

    Append-only in the strong sense: the application role holds SELECT and INSERT and nothing else,
    and a trigger refuses UPDATE and DELETE even to the owner. A log that can be edited by whoever
    is being investigated is not evidence.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_org_at", "org_id", "at"),
        Index("ix_audit_log_entity", "org_id", "entity_type", "entity_id", "at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String, nullable=False)  # cost.created, invoice.confirmed…
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# Tables that carry org_id and get a Row Level Security policy in migration 0003 (0004 for tracking).
RLS_TABLES = [
    "suppliers",
    "shipments",
    "containers",
    "purchase_orders",
    "po_lines",
    "container_loads",
    "costs",
    "cost_allocations",
    "import_jobs",
    "import_mappings",
    "tracking_subscriptions",
    "tracking_events",
    "eta_history",
    "alerts",
    "documents",
    "document_blobs",
    "invoices",
    "invoice_lines",
    "audit_log",
    "rate_cards",
    "erp_connections",
    "erp_sync_runs",
    "sku_cost_history",
    "erp_pushes",
]
