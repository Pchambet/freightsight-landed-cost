"""API v1 schemas. Money and quantities are Decimal in, strings out. Never float."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.domain.alerts.text import LOCALES
from app.domain.boxes import iso_size_type
from app.domain.costing.entry import none_if_blank, number_or_none
from app.domain.invoices.ports import ConfidenceBand, ExtractorKind, confidence_band, extractor_kind
from app.domain.models import (
    AlertKind,
    AlertSeverity,
    AllocationMethod,
    ContainerMilestone,
    CostScope,
    CostStatus,
    CostType,
    DndRisk,
    ErpKind,
    ErpSyncStatus,
    ImportKind,
    ImportStatus,
    Incoterm,
    InvoiceStatus,
    MemberRole,
    MilestoneCode,
    PurchaseOrderStatus,
    RateBasis,
    TrackingState,
)
from app.domain.reporting.preparation import Code as PreparationCode

Money = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str, when_used="json")]
Qty = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.4f}", return_type=str, when_used="json")]
Rate = Annotated[
    Decimal, PlainSerializer(lambda d: f"{d:.8f}".rstrip("0").rstrip("."), return_type=str, when_used="json")
]
Pct = Annotated[Decimal, PlainSerializer(lambda d: f"{d:.2f}", return_type=str, when_used="json")]


def currency_code(v: str) -> str:
    v = v.strip().upper()
    if len(v) != 3 or not v.isalpha():
        raise ValueError("currency must be a 3-letter ISO code")
    return v


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    code: str | None = None
    detail: str | None = None
    errors: list[dict[str, str]] | None = None
    #: The id of the request that produced this error, also in the X-Request-ID response header.
    request_id: str | None = None


# ---------------------------------------------------------------------------- identity


class MeResponse(BaseModel):
    org_id: UUID
    user_id: UUID | None
    role: MemberRole
    via: str


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str | None
    base_currency: str
    default_allocation_method: AllocationMethod
    default_incoterm: Incoterm | None
    free_days_demurrage: int
    free_days_detention: int
    #: The language this organization is written to in: "fr" or "en". What the digest e-mails use.
    locale: str
    settings: dict[str, Any]
    unread_alerts: int = 0  # what the bell shows
    #: The demo dataset is loaded here, and `DELETE /organization/sample-data` can take it away.
    has_sample_data: bool = False


class SampleDataDeleted(BaseModel):
    """What DELETE /organization/sample-data took away, by kind."""

    deleted: dict[str, int]


class SampleDataResponse(BaseModel):
    """What POST /organization/sample-data created."""

    suppliers: int
    purchase_orders: int
    containers: int
    costs: int
    shipment_reference: str
    container_ids: list[UUID]


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = None
    default_allocation_method: AllocationMethod | None = None
    default_incoterm: Incoterm | None = None
    free_days_demurrage: int | None = Field(default=None, ge=0, le=60)
    free_days_detention: int | None = Field(default=None, ge=0, le=60)
    locale: str | None = None
    settings: dict[str, Any] | None = None

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: str | None) -> str | None:
        if v is None:
            return None
        # Not silently coerced to French: a language we cannot write is a setting someone would
        # believe was saved. The code is for the screen, which shows this in a form.
        if v not in LOCALES:
            raise PydanticCustomError(
                "ORG_LOCALE_UNKNOWN", "locale must be one of: {allowed}", {"allowed": ", ".join(LOCALES)}
            )
        return v

    @field_validator("base_currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)

    @field_validator("default_allocation_method")
    @classmethod
    def _method(cls, v: AllocationMethod | None) -> AllocationMethod | None:
        if v in (
            AllocationMethod.MANUAL,
            AllocationMethod.BY_CIF_VALUE,
            AllocationMethod.BY_THEORETICAL_DUTY,
        ):
            raise ValueError("default method must be BY_VALUE, BY_WEIGHT, BY_VOLUME or BY_QUANTITY")
        return v


# ---------------------------------------------------------------------------- suppliers


class SupplierCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    default_currency: str | None = None

    @field_validator("default_currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)


class SupplierUpdate(SupplierCreate):
    name: str | None = Field(default=None, min_length=1, max_length=200)  # type: ignore[assignment]


class SupplierResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    country: str | None
    default_currency: str | None


# ---------------------------------------------------------------------------- purchase orders


class PoLineIn(BaseModel):
    line_no: int = Field(ge=1)
    sku: str | None = None
    description: str | None = None
    hs_code: str | None = None
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    unit_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    unit_volume_cbm: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    duty_rate: Decimal | None = Field(default=None, ge=0, le=5, max_digits=7, decimal_places=4)


class PoLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    line_no: int
    sku: str | None
    description: str | None
    hs_code: str | None
    quantity: Qty
    unit_price: Qty
    unit_weight_kg: Qty | None
    unit_volume_cbm: Qty | None
    duty_rate: Rate | None


class PurchaseOrderCreate(BaseModel):
    po_number: str = Field(min_length=1, max_length=64)
    supplier_id: UUID | None = None
    #: Instead of an id: the supplier of that name (case ignored), created if there is none.
    supplier_name: str | None = Field(default=None, max_length=200)
    currency: str | None = None
    fx_rate: Decimal | None = Field(default=None, gt=0)
    fx_date: date | None = None
    incoterm: Incoterm | None = None
    order_date: date | None = None
    lines: list[PoLineIn] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)

    @model_validator(mode="after")
    def _unique_lines(self) -> PurchaseOrderCreate:
        nos = [ln.line_no for ln in self.lines]
        if len(nos) != len(set(nos)):
            raise ValueError("line_no must be unique within a purchase order")
        return self


class PurchaseOrderUpdate(BaseModel):
    supplier_id: UUID | None = None
    fx_rate: Decimal | None = Field(default=None, gt=0)
    fx_date: date | None = None
    incoterm: Incoterm | None = None
    order_date: date | None = None
    lines: list[PoLineIn] | None = None  # upsert by line_no; lines absent from the list are kept


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    po_number: str
    supplier_id: UUID | None
    supplier_name: str | None = None
    currency: str
    fx_rate: Rate
    fx_date: date | None
    incoterm: Incoterm | None
    order_date: date | None
    status: PurchaseOrderStatus
    cancelled_at: datetime | None
    lines: list[PoLineResponse]


class PurchaseOrderSummary(BaseModel):
    id: UUID
    po_number: str
    supplier_name: str | None
    currency: str
    line_count: int
    fob_base: Money
    container_numbers: list[str]


# ---------------------------------------------------------------------------- shipments & containers


class ShipmentCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=64)
    carrier_scac: str | None = Field(default=None, min_length=4, max_length=4)
    incoterm: Incoterm | None = None
    origin_unlocode: str | None = Field(default=None, min_length=5, max_length=5)
    destination_unlocode: str | None = Field(default=None, min_length=5, max_length=5)
    etd: date | None = None
    eta: date | None = None


class ShipmentUpdate(ShipmentCreate):
    reference: str | None = Field(default=None, min_length=1, max_length=64)  # type: ignore[assignment]


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    reference: str
    carrier_scac: str | None
    incoterm: Incoterm | None
    origin_unlocode: str | None
    destination_unlocode: str | None
    etd: date | None
    eta: date | None


def container_type(v: str | None) -> str | None:
    """The ISO 6346 size-type, whatever notation came in: "40HC", "40' HC" and "45G1" are one box, and
    the audit compares boxes by the length that code gives. A code nobody listed is kept as typed
    rather than lost; an empty one is no type."""
    if v is None or not v.strip():
        return None
    return iso_size_type(v) or v.strip()


class ContainerCreate(BaseModel):
    container_number: str = Field(min_length=11, max_length=11, pattern=r"^[A-Za-z]{4}[0-9]{7}$")
    shipment_id: UUID | None = None
    carrier_scac: str | None = Field(default=None, min_length=4, max_length=4)
    iso_type: str | None = None

    @field_validator("container_number")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("iso_type")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        return container_type(v)


class ContainerUpdate(BaseModel):
    shipment_id: UUID | None = None
    carrier_scac: str | None = Field(default=None, min_length=4, max_length=4)
    iso_type: str | None = None
    milestone: ContainerMilestone | None = None
    eta: datetime | None = None
    ata: datetime | None = None
    discharged_at: datetime | None = None
    gate_out_at: datetime | None = None
    empty_returned_at: datetime | None = None
    free_days_demurrage: int | None = Field(default=None, ge=0, le=60)
    free_days_detention: int | None = Field(default=None, ge=0, le=60)

    @field_validator("iso_type")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        return container_type(v)


class ContainerResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    container_number: str
    shipment_id: UUID | None
    carrier_scac: str | None
    iso_type: str | None
    milestone: ContainerMilestone
    tracking_state: TrackingState
    eta: datetime | None
    ata: datetime | None
    discharged_at: datetime | None
    gate_out_at: datetime | None
    empty_returned_at: datetime | None
    free_days_demurrage: int | None
    free_days_detention: int | None
    last_free_day: date | None
    detention_deadline: date | None
    dnd_risk: DndRisk


class ContainerDetail(ContainerResponse):
    """GET /containers/{id}: the container, and what the accounts hold of it."""

    #: The month it was frozen in, at how much, and what has moved since. Null while no close holds it.
    closed_period: str | None = None
    frozen_landed: Money | None = None
    drift: Money | None = None


class ContainerSummary(ContainerResponse):
    #: The month this container was frozen in, if any: a padlock in a list.
    closed_period: str | None = None
    shipment_reference: str | None = None
    po_numbers: list[str] = Field(default_factory=list)
    load_count: int = 0
    fob_base: Money = Decimal("0.00")
    allocated_base: Money = Decimal("0.00")
    cost_types_present: list[CostType] = Field(default_factory=list)


class LoadIn(BaseModel):
    po_line_id: UUID
    quantity: Decimal = Field(gt=0, max_digits=14, decimal_places=4)


class LoadResponse(BaseModel):
    id: UUID
    po_line_id: UUID
    po_id: UUID
    po_number: str
    line_no: int
    sku: str | None
    quantity: Qty
    line_quantity: Qty


# ---------------------------------------------------------------------------- costs


class ManualSplitIn(BaseModel):
    target_type: Literal["container", "po", "po_line"]
    target_id: UUID
    pct: Decimal = Field(gt=0, le=100, max_digits=5, decimal_places=2)


class CostCreate(BaseModel):
    scope: CostScope
    status: CostStatus = CostStatus.ACTUAL
    target_id: UUID
    cost_type: CostType
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    currency: str
    cost_date: date
    fx_rate: Decimal | None = Field(
        default=None, gt=0, description="Manual override; otherwise ECB rate at cost_date"
    )
    allocation_method: AllocationMethod | None = None
    manual_splits: list[ManualSplitIn] | None = None
    vendor: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return currency_code(v)

    @field_validator("vendor")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return none_if_blank(v)

    @field_validator("invoice_number")
    @classmethod
    def _number(cls, v: str | None) -> str | None:
        return number_or_none(v)

    @model_validator(mode="after")
    def _manual(self) -> CostCreate:
        if (self.allocation_method == AllocationMethod.MANUAL) != (self.manual_splits is not None):
            raise ValueError("manual_splits is required if and only if allocation_method is MANUAL")
        if self.manual_splits is not None and sum(s.pct for s in self.manual_splits) != Decimal("100.00"):
            raise ValueError("manual_splits must sum to 100.00")
        return self


class CostUpdate(BaseModel):
    cost_type: CostType | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = None
    cost_date: date | None = None
    fx_rate: Decimal | None = Field(default=None, gt=0)
    allocation_method: AllocationMethod | None = None
    manual_splits: list[ManualSplitIn] | None = None
    vendor: str | None = None
    invoice_number: str | None = None
    notes: str | None = None
    status: CostStatus | None = None
    #: The estimate this cost replaces. Setting it takes that estimate out of the allocation and
    #: makes the pair a variance; only an ACTUAL cost may carry one.
    supersedes_cost_id: UUID | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)

    @field_validator("vendor")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return none_if_blank(v)

    @field_validator("invoice_number")
    @classmethod
    def _number(cls, v: str | None) -> str | None:
        return number_or_none(v)


class CostResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scope: CostScope
    shipment_id: UUID | None
    container_id: UUID | None
    po_id: UUID | None
    po_line_id: UUID | None
    cost_type: CostType
    amount: Money
    currency: str
    fx_rate: Rate
    fx_date: date
    fx_source: str
    amount_base: Money
    allocation_method: AllocationMethod
    manual_splits: list[dict[str, Any]] | None
    cost_date: date
    vendor: str | None
    invoice_number: str | None
    notes: str | None
    status: CostStatus
    supersedes_cost_id: UUID | None
    closed_at: datetime | None
    close_reason: str | None
    created_at: datetime


class CostClose(BaseModel):
    """Closing an estimate no invoice will ever match."""

    reason: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------- landed costs


class ReportWarning(BaseModel):
    cost_id: UUID
    code: str
    message: str
    amount_base: Money
    load_ids: list[UUID]
    # "PO-2026-101 #1 SKU" per affected load, so the UI can compose a translated sentence
    # instead of parsing `message`.
    load_labels: list[str] = []


class ReportNote(BaseModel):
    """A rule applied to a cost that *was* allocated — never an amount missing from the total.

    Same shape as a warning minus the amount, precisely so it cannot be rendered as "x € unallocated".
    """

    cost_id: UUID
    code: str
    message: str
    load_ids: list[UUID]
    load_labels: list[str] = []


class CostTypeSplit(BaseModel):
    estimated: Money = Decimal("0.00")
    actual: Money = Decimal("0.00")


class ReportLine(BaseModel):
    load_id: UUID
    container_id: UUID
    container_number: str
    po_id: UUID
    po_number: str
    line_no: int
    sku: str | None
    description: str | None
    quantity: Qty
    fob: Money
    allocated: Money
    vat: Money
    landed: Money
    unit_landed_cost: Qty
    estimated: Money
    actual: Money
    variance: Money
    by_cost_type: dict[str, Money]


class LandedCostReport(BaseModel):
    """Landed-cost figures only: recoverable import VAT is in `totals.vat` and in no other total.

    The keys of `totals` are meant to be added up by the person reading them:
    `allocated == estimated + actual`, `actual == matched_actual + unforecast_actual`, and
    `variance == matched_actual - matched_estimated` on the `matched_pairs` estimates an invoice has
    actually replaced. Nothing else compares an estimate with an invoice that was never its own.
    """

    base_currency: str
    #: fob, allocated, vat, landed, unallocated, estimated, actual, variance, matched_estimated,
    #: matched_actual, unforecast_actual
    totals: dict[str, Money]
    #: How many estimate/invoice pairs `variance` rests on. Zero means there is no variance to show.
    matched_pairs: int = 0
    #: Share of the cost types on this scope that have a real invoice, 0 to 1. Null when none is
    #: expected yet.
    completeness: Decimal | None = None
    by_cost_type: dict[str, Money]
    by_cost_type_detail: dict[str, CostTypeSplit] = Field(default_factory=dict)
    lines: list[ReportLine]
    costs: list[CostResponse]
    warnings: list[ReportWarning]
    notes: list[ReportNote] = Field(default_factory=list)


class PreviewRequest(BaseModel):
    container_id: UUID | None = None
    shipment_id: UUID | None = None
    po_id: UUID | None = None
    method_overrides: dict[CostType, AllocationMethod] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one(self) -> PreviewRequest:
        if sum(x is not None for x in (self.container_id, self.shipment_id, self.po_id)) != 1:
            raise ValueError("exactly one of container_id, shipment_id, po_id is required")
        return self


class IntegrityReport(BaseModel):
    ok: bool
    costs_checked: int
    mismatches: list[dict[str, str]]


# ---------------------------------------------------------------------------- imports


class ImportFieldInfo(BaseModel):
    """A column an import reads, and the header the templates and the application's own exports give
    it in each language: a file written with either maps itself."""

    field: str
    required: bool
    header_fr: str
    header_en: str


class ImportKindInfo(BaseModel):
    kind: ImportKind
    fields: list[ImportFieldInfo]
    #: Groups of fields of which at least one must be mapped: a cost lands on a box, a bill of lading
    #: or an order, whichever column says it — or its label.
    required_one_of: list[list[str]] = Field(default_factory=list)


class ImportRowIssue(BaseModel):
    """One thing to say about one row: `code` and `params` are what the screen words; `message` is
    the English fallback."""

    row: int
    field: str
    code: str
    message: str
    params: dict[str, Any] = Field(default_factory=dict)


class ImportMoneyByType(BaseModel):
    cost_type: CostType
    rows: int
    total_base: Money


class ImportMoneyByCurrency(BaseModel):
    currency: str
    rows: int
    total: Money
    total_base: Money


class ImportMoney(BaseModel):
    """What a costs file puts in the books, in the organization's currency: the money first."""

    #: What the file adds to the landed costs: import VAT, written but never a landed cost, is left out
    #: of it and shown by type alone.
    total_base: Money
    by_type: list[ImportMoneyByType] = Field(default_factory=list)
    by_currency: list[ImportMoneyByCurrency] = Field(default_factory=list)


class ImportUnknownLabel(BaseModel):
    """A label no rule can type, with what hangs on it: a person gives it a type (`label_types`)."""

    label: str
    rows: int
    total_base: Money


class ImportCreditNote(BaseModel):
    """A negative line left out of the books — costs cannot be negative — and counted, never dropped in
    silence: it may refund a charge the audit would otherwise claim. Amounts as the file wrote them:
    negative."""

    row: int
    cost_date: date | None = None
    vendor: str | None = None
    invoice_number: str | None = None
    amount: Money
    currency: str
    amount_base: Money | None = None
    #: The cost it exactly reverses, when the file holds that line too and the books have it already:
    #: until a person takes that cost out, the audit's preparation blocks (CREDIT_NOTE_REVERSES_COST).
    reverses_cost_id: UUID | None = None


class ImportReport(BaseModel):
    """What a preview found or a commit did. The counters of the other kinds are absent, not zero;
    a preview or commit refused as a whole says so in `code` and `error`."""

    model_config = ConfigDict(extra="allow")
    row_count: int = 0
    valid_rows: int = 0
    header_row: int = 1
    totals_row_ignored: bool = False
    forward_filled_rows: int = 0
    errors: list[ImportRowIssue] = Field(default_factory=list)
    warnings: list[ImportRowIssue] = Field(default_factory=list)
    code: str | None = None
    error: str | None = None
    # PURCHASE_ORDERS, LEGACY_PO_CONTAINER
    purchase_orders_created: int | None = None
    purchase_orders_updated: int | None = None
    lines_created: int | None = None
    lines_updated: int | None = None
    suppliers_created: int | None = None
    loads_updated: int | None = None
    # PURCHASE_ORDERS, CONTAINERS
    containers_created: int | None = None
    loads_created: int | None = None
    # CONTAINERS
    containers_updated: int | None = None
    #: Boxes that had no arrival date and have one now: what makes them visible to a period.
    containers_dated: int | None = None
    shipments_created: int | None = None
    #: Orders the file puts on several boxes without saying how much on each: left to a person.
    po_split: int | None = None
    #: Invoices waiting for a box this file created, now pointed at it.
    invoices_matched: int | None = None
    # COSTS
    costs_created: int | None = None
    #: Rows already in the books from an earlier import of the same ledger: left as they are.
    costs_skipped: int | None = None
    estimates_replaced: int | None = None
    estimates_replaced_base: Money | None = None
    #: Rows refused because they are already in the books: DUPLICATE_COST, INVOICE_ALREADY_RECORDED,
    #: INVOICE_LINE_ALREADY_RECORDED, INVOICE_IN_INBOX.
    duplicates: int | None = None
    duplicates_base: Money | None = None
    money: ImportMoney | None = None
    unknown_labels: list[ImportUnknownLabel] | None = None
    credit_notes_skipped: list[ImportCreditNote] | None = None
    #: Their total, negative as the file wrote them.
    credit_notes_skipped_base: Money | None = None
    #: Pairs of rows that cancel each other out in the file, both left out.
    reversed_in_file: int | None = None
    # PRODUCTS
    products_created: int | None = None
    products_updated: int | None = None


class ImportJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: ImportKind
    status: ImportStatus
    original_filename: str | None
    encoding: str | None
    delimiter: str | None
    columns: list[str]
    mapping: dict[str, str]
    #: What the preview ran with besides the mapping (COSTS: `default_cost_type`, `label_types`).
    options: dict[str, Any] = Field(default_factory=dict)
    row_count: int
    error_count: int
    report: ImportReport
    created_at: datetime
    committed_at: datetime | None
    #: A costs file taken back (DELETE /imports/{id}).
    undone_at: datetime | None = None
    #: A costs file, committed, dropped by a person, not taken back yet. What happened since — a cost
    #: pushed to the ERP, a month closed — is the DELETE's to refuse, by name.
    can_undo: bool = False
    #: How the file got here: a person dropped it, or an ERP sync produced it. Not stored on the
    #: job — the sync run that drove it says so (see api/v1/imports.py).
    source: Literal["upload", "erp_sync"] = "upload"
    #: CONTAINERS, COSTS, PRODUCTS, once previewed: the preview this report is — its mapping, its
    #: options and its figures. The commit names it, and writes only what that preview showed.
    preview_key: str | None = None


class ImportValidateRequest(BaseModel):
    mapping: dict[str, str]
    #: COSTS: the type of the rows whose type no column and no label gives. Rows typed this way only
    #: ever give findings to check.
    default_cost_type: CostType | None = None
    #: COSTS: the type a person gave each label the preview could not read, keyed by the label as
    #: `report.unknown_labels[].label` gives it.
    label_types: dict[str, CostType] = Field(default_factory=dict)
    #: COSTS: a person has looked at the charges of the same type already on the same freight
    #: (DUPLICATE_COST) and says these are others. Written in the history with the import.
    force_duplicates: bool = False


class ImportCommitRequest(BaseModel):
    #: PURCHASE_ORDERS and LEGACY_PO_CONTAINER only. The other kinds commit exactly what their preview
    #: ran with, which the job keeps: 409 PREVIEW_REQUIRED without one, 409 PREVIEW_CHANGED — with the
    #: new `report` and its `preview_key` — when the books moved since, or when another preview of the
    #: same file (another tab, other options) replaced the one this person saw.
    mapping: dict[str, str] | None = None
    on_error: Literal["skip_rows", "abort"] = "skip_rows"
    #: CONTAINERS, COSTS, PRODUCTS: the `preview_key` of the preview the person approved. Required:
    #: without it, 409 PREVIEW_REQUIRED.
    preview_key: str | None = None


class ImportUndoResponse(BaseModel):
    costs_deleted: int
    #: Estimates the deleted costs had replaced, standing again.
    estimates_reopened: int


# ---------------------------------------------------------------------------- fx


class FxRateResponse(BaseModel):
    base: str
    quote: str
    rate_date: date
    rate: Rate
    source: str


# ---------------------------------------------------------------------------- tracking


class TrackingEventCreate(BaseModel):
    """A milestone typed in by hand. `occurred_at` is when it happened, not when it was entered."""

    code: MilestoneCode
    occurred_at: datetime
    is_estimate: bool = False
    location_unlocode: str | None = Field(default=None, min_length=5, max_length=5)
    location_name: str | None = None
    vessel_name: str | None = None
    voyage: str | None = None
    note: str | None = None


class TrackingEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    container_id: UUID
    provider: str
    code: MilestoneCode
    occurred_at: datetime
    is_estimate: bool
    location_unlocode: str | None
    location_name: str | None
    vessel_name: str | None
    voyage: str | None
    raw_description: str | None
    source: str
    inserted_at: datetime


class TrackingSubscriptionCreate(BaseModel):
    """`provider_ref` is the provider's own handle for this box. Leave it out when the provider is
    subscribed through its API; give it when deliveries arrive webhook-only (no API key)."""

    #: Omitted, the organization's `tracking_provider_default` setting decides, and failing that the
    #: person doing it by hand.
    provider: str | None = None
    provider_ref: str | None = None
    identifier_type: Literal["container", "bill_of_lading", "booking"] = "container"
    identifier: str | None = None  # defaults to the container number


class TrackingSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    container_id: UUID
    provider: str
    provider_ref: str | None
    status: str
    last_error: str | None
    subscribed_at: datetime | None
    ended_at: datetime | None


class EtaHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    container_id: UUID
    eta: datetime
    previous_eta: datetime | None
    delta_hours: Decimal
    severity: str
    source: str
    recorded_at: datetime


class WebhookAck(BaseModel):
    """What a provider gets back. Always 200 once the delivery is stored; `status` says what we did
    with it, and a provider must not retry on `duplicate` or `failed`."""

    delivery_id: UUID
    status: Literal["processed", "duplicate", "failed", "unroutable"]
    events_ingested: int = 0
    detail: str | None = None


# ---------------------------------------------------------------------------- alerts


class AlertResponse(BaseModel):
    """`title` and `body` are English sentences kept for API clients and logs. A screen builds its
    own sentence from `kind`, `payload` and `container_number` in the reader's language."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    container_id: UUID | None
    container_number: str | None = None
    kind: AlertKind
    severity: AlertSeverity
    title: str
    body: str
    payload: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class AlertsRead(BaseModel):
    marked: int


# ---------------------------------------------------------------------------- invoices


class InvoiceLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    line_no: int
    description: str
    amount: Money
    currency: str
    cost_type: CostType | None
    scope: CostScope | None
    target_id: UUID | None
    confidence: Decimal
    #: The same figure as a band the screen can put a word on. The number stays, for a ticket.
    confidence_band: ConfidenceBand | None = None
    accepted: bool
    notes: str | None
    cost_id: UUID | None

    @model_validator(mode="after")
    def _band(self) -> InvoiceLineResponse:
        self.confidence_band = confidence_band(self.confidence)
        return self


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: InvoiceStatus
    vendor: str | None
    invoice_number: str | None
    invoice_date: date | None
    currency: str | None
    total_amount: Money | None
    #: The invoice's own pre-tax total when it prints one, so the screen can show the three figures
    #: the document shows. Null is not zero: it means the document did not state it.
    subtotal_amount: Money | None
    vat_amount: Money | None
    confidence: Decimal | None
    confidence_band: ConfidenceBand | None = None
    extractor: str | None
    #: Who read the document, as a value to translate: « regex » and 0.60 are ours, not a CFO's.
    extractor_kind: ExtractorKind = ExtractorKind.UNKNOWN
    error: str | None
    document_id: UUID
    filename: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def _named_for_a_person(self) -> InvoiceSummary:
        self.confidence_band = confidence_band(self.confidence)
        self.extractor_kind = extractor_kind(self.extractor)
        return self


class InvoiceResponse(InvoiceSummary):
    lines: list[InvoiceLineResponse] = Field(default_factory=list)
    document_url: str  # this API's own route; the bytes never come from a storage URL
    notes: list[str] = Field(default_factory=list)


class InvoiceLineUpdate(BaseModel):
    """A correction made by a person. Everything is optional; nothing is inferred."""

    amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    currency: str | None = None
    cost_type: CostType | None = None
    scope: CostScope | None = None
    target_id: UUID | None = None
    accepted: bool | None = None
    description: str | None = None

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)


class InvoiceUpdate(BaseModel):
    """What the reader took from the invoice's header, corrected by the person looking at the PDF: a
    date or a file reference read as the invoice's number, a forwarder not recognised. The number and
    the forwarder are what every guard against counting an invoice twice compares, so a wrong one is
    either a false refusal or a missed double entry. Only while the invoice is being reviewed; a field
    left out is left as it is, and an empty one is cleared."""

    vendor: str | None = Field(default=None, max_length=200)
    invoice_number: str | None = Field(default=None, max_length=64)
    invoice_date: date | None = None

    @field_validator("vendor")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return none_if_blank(v)

    @field_validator("invoice_number")
    @classmethod
    def _number(cls, v: str | None) -> str | None:
        return number_or_none(v)


class InvoiceLineCreate(BaseModel):
    """The charge the arithmetic says is missing, added by the person who is looking at the PDF.

    It arrives like every other proposal — unaccepted, at zero confidence — so the rule holds: what
    becomes a cost is what someone ticked, whoever typed it.
    """

    description: str = Field(default="", max_length=500)
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: str | None = None  # the invoice's own, when not stated
    cost_type: CostType | None = None
    scope: CostScope | None = None
    target_id: UUID | None = None

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)


class InvoiceConfirmRequest(BaseModel):
    """Confirm again after looking at what a check found. Each flag answers one check only, and both
    may be sent together; what was overruled is written into the confirmation's notes."""

    #: DUPLICATE_COST: another cost of this type already covers this freight, and it is not this charge.
    force: bool = False
    #: INVOICE_ALREADY_RECORDED: a cost already carries this invoice's number, and it is not this invoice.
    force_recorded: bool = False


class InvoiceContainerLink(BaseModel):
    id: UUID
    container_number: str


class InvoiceConfirmResponse(BaseModel):
    invoice: InvoiceResponse
    costs_created: int
    cost_ids: list[UUID]
    #: Containers whose landed cost changed — where to look next.
    containers: list[InvoiceContainerLink] = Field(default_factory=list)
    #: Estimates these costs replaced, when exactly one was waiting for them.
    superseded_estimate_ids: list[UUID] = Field(default_factory=list)
    #: What the confirmation deliberately did not decide, in plain words.
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------- audit


class AuditFinding(BaseModel):
    """One measured fact about the period's invoices, never an accusation: the rule that produced it,
    how sure it is, how much money it is about, and the objects behind it."""

    model_config = ConfigDict(from_attributes=True)
    #: DUPLICATE_CHARGE | DOUBLE_ENTRY | ABOVE_QUOTE | OUTLIER_CHARGE | ESTIMATE_NEVER_INVOICED |
    #: UNALLOCATED_COST | DUTY_RATE_MISSING
    code: str
    #: sure: a fact — one provider billing a charge twice or two identical amounts, one invoice line
    #: recorded twice on one target in one currency with its forwarder named on both copies, an invoice
    #: above the estimate it replaced in the same currency, an estimate nobody invoiced. to_check: a
    #: question — two providers billing neighbouring amounts, the same invoice number on a box and on
    #: its bill of lading or in two currencies or with a forwarder missing, an estimate and an invoice
    #: in two currencies, a charge far above the median of its route.
    confidence: Literal["sure", "to_check"]
    #: The money the finding is about: what was billed twice, the excess over the estimate (at the
    #: invoice's own rate, exchange effect apart) or over the median, the estimate still standing.
    #: Never a promised saving.
    amount: Money
    container_id: UUID | None
    container_number: str | None
    cost_type: CostType | None
    vendor: str | None
    invoice_number: str | None
    cost_ids: list[UUID]
    #: Statistical rules only: how many other containers the charge was compared with.
    basis: int | None
    #: The moving parts of the sentence, as raw strings. DUPLICATE_CHARGE: first_invoice,
    #: first_vendor, first_amount (base currency), same_vendor ("true" | "false"). DOUBLE_ENTRY:
    #: first_invoice, first_vendor, first_amount (base currency) — the copy recorded first; two costs
    #: written by one confirmed invoice are two lines of it, never a double entry. ABOVE_QUOTE: quoted,
    #: invoiced and currency (the currency both were written in, or the base currency when they
    #: differ), share_pct, and fx_effect when the exchange rate moved: the part of the difference in
    #: base currency that is the rate's, not the price's. OUTLIER_CHARGE: amount, median, ratio,
    #: route, size ("20" | "40" | "45" when known). ESTIMATE_NEVER_INVOICED: days (on `as_of`),
    #: landed_on. UNALLOCATED_COST: reason (an engine code) and, when lines lack a basis, skus and
    #: po_numbers. DUTY_RATE_MISSING: lines, skus, po_numbers. Lists are joined with ", ". A copy sent
    #: with names left out keeps only the params that name nobody (reporting.audit.PUBLIC_PARAMS).
    params: dict[str, str]
    #: DUPLICATE_CHARGE, ABOVE_QUOTE and OUTLIER_CHARGE: a claim can be made, and the amount counts in
    #: the first page's recoverable sums. The others are about the quality of the figures — a
    #: DOUBLE_ENTRY is corrected in the company's own books, never claimed from anybody.
    recoverable: bool


class AuditMargin(BaseModel):
    """An article over the period."""

    model_config = ConfigDict(from_attributes=True)
    sku: str
    description: str | None
    quantity: Qty
    #: Purchase and landed value of the period's volume, and per unit.
    fob: Money
    landed: Money
    unit_fob: Qty
    unit_landed: Qty
    #: landed - fob: what a price computed on the purchase price leaves out. Always present.
    approach_costs: Money
    #: landed - fob x `AuditReport.assumed_coefficient`: what the company's own calculation leaves
    #: out. Null without a coefficient; negative when the coefficient is more prudent than the facts.
    #: Needs no selling price.
    gap_vs_assumed: Money | None
    #: The four below only with a selling price in the base currency; null otherwise.
    sale_price: Qty | None
    margin_on_fob_pct: Decimal | None
    margin_real_pct: Decimal | None
    #: margin_on_fob_pct - margin_real_pct, from the two rounded figures.
    points_lost: Decimal | None


class AuditDemurrageLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    paid: Money
    #: Only with a DEMURRAGE rate card and a first risk alert on record: `rule_code` says which case,
    #: as on the demurrage report (DndReportLine).
    avoided: Money | None
    rule_code: Literal["DND_AVOIDED_ESTIMATED", "DND_AVOIDED_NO_RATE", "DND_AVOIDED_NO_ALERT"]
    days: int | None
    daily_rate: Money | None
    days_over: int | None
    #: Units that travelled in the box, and what the demurrage added to each.
    quantity: Qty
    per_unit: Qty | None


class AuditCoefficient(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str
    fob: Money
    landed: Money
    coefficient: Qty | None


class AuditCompleteness(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    containers: int
    fob: Money
    landed: Money
    estimated: Money
    estimated_share_pct: Decimal | None
    #: Invoices waiting for a person on `as_of`, in the whole organization: until read, any of them
    #: may still change the period's figures.
    invoices_to_review: int
    containers_without_cost: int
    #: How many of the period's costs took their exchange rate from each source: ecb, ecb_latest,
    #: manual, same (no conversion).
    fx_sources: dict[str, int]
    #: The period's boxes "far above the route" could not compare (no route or no length), and the
    #: organization's boxes that fall in no period (no arrival date). Null in links sent before.
    containers_not_compared: int | None = None
    containers_without_date: int | None = None


class AuditHeadline(BaseModel):
    """The first page. Each figure is the sum of its section: the screen adds nothing."""

    model_config = ConfigDict(from_attributes=True)
    #: Sum of `margins[].approach_costs` over the articles that have a selling price (`priced_skus`):
    #: the margin a calculation on the purchase price announces and the goods do not make.
    margin_overstated: Money
    priced_skus: int
    unpriced_skus: int
    #: Sum of `margins[].gap_vs_assumed` over every article of the period, priced or not. Null without
    #: `assumed_coefficient`; negative when the coefficient is more prudent than the facts.
    gap_vs_assumed: Money | None
    #: Sum of the findings with `recoverable`, the sure ones and the doubtful ones apart.
    recoverable_sure: Money
    recoverable_to_check: Money
    #: Sum of `demurrage[].paid`.
    demurrage_paid: Money


class AuditRules(BaseModel):
    """The thresholds the audit was produced with, for the page that states its method. Amounts are in
    the base currency. A copy sent by link keeps the rules it was computed under."""

    model_config = ConfigDict(from_attributes=True)
    #: DUPLICATE_CHARGE: two invoices within this per cent of each other, in their own currency when
    #: they share one; never for these types.
    duplicate_tolerance_pct: Decimal
    never_duplicate: list[CostType]
    #: ABOVE_QUOTE: from this per cent (in the currency both were written in) and this amount (the
    #: excess at the invoice's rate) above the estimate.
    above_quote_min_pct: Decimal
    above_quote_min_amount: Money
    #: OUTLIER_CHARGE: above ratio x the median of the same charge on the same route for the same size
    #: of box, and min_excess above it, measured on at least min_basis other containers of the last
    #: lookback_days — of the freight_window_days around it for freight; never for `not_compared`.
    outlier_ratio: Decimal
    outlier_min_excess: Money
    outlier_min_basis: int
    outlier_lookback_days: int
    freight_window_days: int
    not_compared: list[CostType]
    #: ESTIMATE_NEVER_INVOICED: still an estimate this many days after landing, on `as_of`.
    estimate_stale_after_days: int
    #: How the figures were made — present, and so stated, only in documents computed that way; a link
    #: sent before they existed has them null. Duty spread by each line's rate when the rates explain
    #: the duty paid within this per cent (by customs value otherwise); demurrage and detention counted
    #: with the box that arrived in the period, whatever the invoice date; boxes of unknown route or
    #: length compared with nothing; one invoice entered twice never called a charge billed twice.
    duty_explained_pct: Decimal | None = None
    demurrage_by_arrival: bool | None = None
    outlier_needs_route_and_size: bool | None = None
    same_invoice_never_duplicate: bool | None = None


class AuditReport(BaseModel):
    """Frozen into the snapshot of every audit sent by link, and validated again at each opening: once
    a link exists, this changes only by adding fields that may be absent."""

    base_currency: str
    period_from: date
    period_to: date
    #: The day it was computed: estimates are judged still open on that day, invoices to review too.
    as_of: date
    headline: AuditHeadline
    #: Largest `overstated` first.
    margins: list[AuditMargin]
    #: Sure first, then by amount.
    findings: list[AuditFinding]
    demurrage: list[AuditDemurrageLine]
    coefficient_by_month: list[AuditCoefficient]
    coefficient_by_supplier: list[AuditCoefficient]
    #: landed / fob over the period, and what the organization applies from memory
    #: (`settings.assumed_coefficient`), when it said so.
    coefficient: Qty | None
    assumed_coefficient: Qty | None
    completeness: AuditCompleteness
    rules: AuditRules


class PreparationExample(BaseModel):
    """Something to click on: `kind` says which page, `id` which one — a uuid, or for a `sku` the
    article code itself."""

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["container", "invoice", "purchase_order", "sku", "import"]
    id: str
    label: str


class PreparationItem(BaseModel):
    """One thing that would make the audit wrong (`blocking`) or that it will not be able to say
    (`limits`), counted, with the section it touches and a few examples. Codes and raw values: the
    sentence and the remedy are the screen's. `params`: COSTS_UNALLOCATED `reason` (an engine code),
    DUTY_GAP `pct` (the guard, "20"); none elsewhere."""

    model_config = ConfigDict(from_attributes=True)
    code: PreparationCode
    severity: Literal["blocking", "limits"]
    section: Literal["period", "findings", "margins", "demurrage"]
    count: int
    #: The money concerned, in the organization's currency — never negative, whatever the sign of what
    #: it counts (CREDIT_NOTES_SKIPPED counts refunds by their size).
    amount_base: Money | None = None
    params: dict[str, str] = Field(default_factory=dict)
    #: At most five; `count` says how many there are in all.
    examples: list[PreparationExample] = Field(default_factory=list)


class AuditPreparation(BaseModel):
    """What to put right before auditing a period: only what was found, blocking first. Not part of the
    audit, and never sent with it: a working list, not a result."""

    model_config = ConfigDict(from_attributes=True)
    base_currency: str
    period_from: date
    period_to: date
    as_of: date
    items: list[PreparationItem]


# ---------------------------------------------------------------------------- shared reports


class ShareCreate(BaseModel):
    expires_in_days: int = Field(default=30, ge=1, le=365)
    #: Leave the forwarders' names, their invoice numbers and the notes out of the snapshot itself.
    #: Hiding them on a screen would leave them readable in the response by anyone holding the link.
    redact_vendors: bool = False


class AuditShareCreate(ShareCreate):
    period_from: date
    period_to: date


class ShareOpen(BaseModel):
    token: str = Field(min_length=1, max_length=256)


class ShareCreated(BaseModel):
    id: UUID
    #: Returned here and never again: only its hash is stored. Show it, copy it, send it.
    token: str
    #: Where the front end serves it: "/r/<token>".
    path: str
    expires_at: datetime
    #: The landed cost that was frozen, so the sender sees the figure that really left.
    landed: Money
    generated_at: datetime


class ShareSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    subject_type: Literal["container", "purchase_order", "audit"]
    subject_label: str
    created_at: datetime
    created_by_name: str | None = None
    expires_at: datetime
    revoked_at: datetime | None
    #: How many times the public page was opened, and when last: "the prospect has read it".
    view_count: int = 0
    last_viewed_at: datetime | None = None
    status: Literal["active", "expired", "revoked"] = "active"


class ShareIssuer(BaseModel):
    name: str


class SharedReportPublic(BaseModel):
    """What a link opens: the report as it stood when the link was made, and nothing else."""

    issuer: ShareIssuer
    subject_type: Literal["container", "purchase_order", "audit"]
    subject_label: str
    shipment_reference: str | None
    #: The language of the company that sent it, not of whoever opens it.
    locale: str
    generated_at: datetime
    expires_at: datetime
    #: The sending company held the demo dataset: the document must say "sample data".
    sample_data: bool = False
    #: Vendors, invoice numbers and notes were left out of this snapshot (they are null in `report`).
    redacted: bool = False
    #: One of the two is filled: a container's or an order's report, or a period's audit.
    report: LandedCostReport | None = None
    audit: AuditReport | None = None


# ---------------------------------------------------------------------------- period close


class PeriodSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    period: str  # YYYY-MM: the month the containers arrived in
    status: Literal["open", "closed"]
    #: Closed: the frozen figures, which never move again. Open: today's.
    containers: int
    fob: Money
    landed: Money
    coefficient: Qty | None
    by_cost_type: dict[str, Money]
    closed_at: datetime | None = None
    closed_by_name: str | None = None
    #: Today's landed cost of the month (equal to `landed` while it is open): never add `landed` and
    #: `drift` on a screen.
    live_landed: Money = Decimal("0.00")
    #: Closed months only: today's landed cost minus the frozen one ("0.00" when nothing moved), and
    #: how many containers it comes from. It is the sum of `drift_lines` of the detail.
    drift: Money | None = None
    drift_containers: int | None = None
    #: What of the drift has been booked as an adjustment in a later month, and what still asks to be.
    #: A cost that arrives after the acknowledgement makes `drift_outstanding` non-zero again.
    drift_acknowledged: Money | None = None
    drift_outstanding: Money | None = None
    acknowledged_at: datetime | None = None
    acknowledged_note: str | None = None


class PeriodEstimatedBox(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    estimated: Money


class PeriodReadiness(BaseModel):
    """What is better settled before closing. Never a refusal."""

    model_config = ConfigDict(from_attributes=True)
    containers_with_estimates: list[PeriodEstimatedBox]
    estimated_total: Money
    invoices_to_review: int
    unallocated_costs: int
    containers_without_cost: int


class PeriodChangedCost(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    cost_id: UUID
    cost_type: CostType
    vendor: str | None
    invoice_number: str | None
    amount_base: Money
    changed_at: datetime


class PeriodDrift(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    frozen_landed: Money
    live_landed: Money
    difference: Money
    #: costs: its costs or loads changed since the close. left_period: its arrival date changed and it
    #: lands in another month now (live is 0). joined_period: the reverse (frozen is 0).
    reason: Literal["costs", "left_period", "joined_period"]
    #: Costs created or changed since the close whose allocation lands on this container.
    costs_changed: list[PeriodChangedCost]
    costs_deleted: int


class PeriodAccrual(BaseModel):
    """Received and not yet invoiced at the end of the month: an estimate no invoice has replaced, on a
    container that had landed by the last day of the month. *Factures non parvenues*, per cost type."""

    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    arrived_on: date | None
    cost_type: CostType
    amount_base: Money


class PeriodAcknowledge(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class PeriodDetail(BaseModel):
    base_currency: str
    summary: PeriodSummary
    #: Open months only.
    readiness: PeriodReadiness | None
    #: Closed months only: one entry per container whose landed cost is no longer the frozen one.
    drift_lines: list[PeriodDrift]
    #: Frozen at the close for a closed month; today's view of the month's last day for an open one.
    #: Containers that landed in EARLIER months and are still not invoiced are in it too.
    accruals: list[PeriodAccrual] = Field(default_factory=list)
    accruals_total: Money = Decimal("0.00")


class PeriodList(BaseModel):
    base_currency: str
    periods: list[PeriodSummary]


# ---------------------------------------------------------------------------- service status


class ServiceCheck(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    #: alert_emails | automatic_tracking | invoice_reading | background_jobs | backups
    key: str
    #: ok: works as described. off: not switched on for this deployment. degraded: on, and failing.
    state: Literal["ok", "off", "degraded"]
    #: What the screen translates: ALERT_EMAILS_ON/OFF, TRACKING_ON/POLLING_ONLY/MANUAL_ONLY,
    #: READING_RULES_ONLY/READING_RULES_AND_MODEL, WORKER_ALIVE/SILENT, BACKUPS_FRESH/UNVERIFIED.
    code: str
    #: Raw facts for the sentence: `providers` (comma-separated), `seen_at`, `checked_at` (ISO).
    params: dict[str, str] = Field(default_factory=dict)


class ServiceStatus(BaseModel):
    checked_at: datetime
    checks: list[ServiceCheck]


# ---------------------------------------------------------------------------- overview


class OverviewFigures(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    period_from: date
    period_to: date
    #: Containers that landed in the period (discharge, else arrival, else the shipment's ETA).
    containers: int
    fob: Money
    landed: Money
    #: landed / fob, four decimals: 1.1400 means the goods cost 14 % more than their purchase price
    #: once they are in the warehouse. Null when nothing landed.
    coefficient: Qty | None
    #: Estimates replaced by an invoice dated in the period, and what the invoices came to.
    variance_estimated: Money
    variance_actual: Money
    variance: Money
    variance_pairs: int
    demurrage_paid: Money
    demurrage_avoided: Money


class OverviewTodo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    invoices_to_review: int
    invoices_failed: int
    #: Active containers carrying goods and not one cost, and active containers carrying nothing.
    containers_without_cost: int
    containers_without_loads: int
    #: Estimates on containers that have landed and that no invoice has replaced yet.
    estimates_awaiting_invoice: int
    #: Today's demurrage exposure, whatever the period: boxes past their last free day, boxes close
    #: to it, and what the days already run come to (null without a DEMURRAGE rate card).
    containers_overdue: int
    containers_at_risk: int
    amount_at_risk: Money | None


class OverviewResponse(BaseModel):
    base_currency: str
    current: OverviewFigures
    #: The same number of days, immediately before.
    previous: OverviewFigures
    todo: OverviewTodo
    #: The current period by month and by supplier, exactly as `/reports/landed-cost` gives them for
    #: the same dates, its cost types, its six heaviest articles, and today's demurrage exposure as
    #: `/reports/dnd` lists it: one computation for the whole first screen.
    by_month: list[ReportBucket] = Field(default_factory=list)
    by_supplier: list[ReportBucket] = Field(default_factory=list)
    by_cost_type: dict[str, Money] = Field(default_factory=dict)
    skus: list[ReportSkuLine] = Field(default_factory=list)
    at_risk: list[DndAtRiskLine] = Field(default_factory=list)


# ---------------------------------------------------------------------------- products


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=120)
    description: str | None = None
    hs_code: str | None = None
    duty_rate: Decimal | None = Field(default=None, ge=0, le=5, max_digits=7, decimal_places=4)
    unit_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    unit_volume_cbm: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    #: Excluding VAT, per unit.
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    #: Null means the organization's own currency, the only case a margin is derived in.
    sale_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("sku")
    @classmethod
    def _trim(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("sku must not be blank")
        return value


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    hs_code: str | None = None
    duty_rate: Decimal | None = Field(default=None, ge=0, le=5, max_digits=7, decimal_places=4)
    unit_weight_kg: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=4)
    unit_volume_cbm: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=6)
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    sale_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sku: str
    description: str | None
    hs_code: str | None
    duty_rate: Rate | None
    unit_weight_kg: Qty | None
    unit_volume_cbm: Qty | None
    sale_price: Qty | None
    sale_currency: str | None
    created_at: datetime
    updated_at: datetime


class ProductPage(BaseModel):
    products: list[ProductResponse]
    next_cursor: str | None = None


class ProductsFromOrders(BaseModel):
    created: int
    #: Articles that already had a catalogue entry: left exactly as they were.
    skipped: int


# ---------------------------------------------------------------------------- articles (SKU reports)


class SkuSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    sku: str
    description: str | None
    quantity: Qty
    fob: Money
    landed: Money
    unit_fob: Qty
    #: Weighted by quantity over every arrival of the period.
    unit_landed: Qty
    #: The two most recent containers that have actually landed. A box still at sea is not compared.
    last_unit_landed: Qty | None
    previous_unit_landed: Qty | None
    #: last against previous, in per cent, one decimal, signed: "8.6", "-2.1".
    change_pct: Decimal | None
    #: How many containers carried it.
    arrivals: int
    last_arrival_on: date | None
    #: Part of the landed cost still rests on estimates.
    has_estimates: bool
    product_id: UUID | None
    sale_price: Qty | None
    #: sale_price - unit_landed, and that as a share of the sale price. Null unless the catalogue
    #: holds a price in the organization's currency: no conversion is invented.
    margin_unit: Qty | None
    margin_pct: Decimal | None
    #: The same two against `last_unit_landed`: what the stock about to be sold leaves.
    last_margin_unit: Qty | None = None
    last_margin_pct: Decimal | None = None


class SkuArrival(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    load_id: UUID
    container_id: UUID
    container_number: str
    po_id: UUID
    po_number: str
    supplier_name: str | None
    arrived_on: date | None
    #: The date is the shipment's ETA, not an arrival on record.
    arrival_is_estimate: bool
    quantity: Qty
    fob: Money
    landed: Money
    unit_fob: Qty
    unit_landed: Qty
    #: Import VAT excluded, as everywhere.
    by_cost_type: dict[str, Money]
    unit_by_cost_type: dict[str, Qty]
    estimated: Money


class SkuBox(BaseModel):
    """The article in one container, whatever number of order lines carried it there. This is what
    `last_unit_landed`, `previous_unit_landed` and `change_pct` compare, and what a curve plots."""

    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    arrived_on: date | None
    arrival_is_estimate: bool
    quantity: Qty
    fob: Money
    landed: Money
    unit_fob: Qty
    unit_landed: Qty
    by_cost_type: dict[str, Money]
    unit_by_cost_type: dict[str, Qty]
    estimated: Money


class SkuListResponse(BaseModel):
    base_currency: str
    skus: list[SkuSummary]


class SkuDetailResponse(BaseModel):
    base_currency: str
    summary: SkuSummary
    #: One per container, oldest first, undated ones last. The last two that have landed
    #: (`arrival_is_estimate` false) are the ones `change_pct` compares.
    containers: list[SkuBox]
    #: One per order line in a container (same order): the detail under a container's point.
    arrivals: list[SkuArrival]


# ---------------------------------------------------------------------------- search


class SearchHit(BaseModel):
    kind: Literal["container", "purchase_order", "sku", "invoice", "supplier", "shipment"]
    #: Null for an article only: a SKU is not a row, its key is `sku`.
    id: UUID | None
    #: The number or the name itself.
    label: str
    #: A raw fact that helps tell two results apart, never a sentence: the shipment of a container,
    #: the supplier of an order, the description of an article, the vendor of an invoice, the country
    #: of a supplier, the carrier of a shipment.
    sublabel: str | None = None
    sku: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]


# ---------------------------------------------------------------------------- audit log


class AuditEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    actor_user_id: UUID | None
    #: The member's name, or their e-mail when Clerk gave no name. Null for the system.
    actor_name: str | None = None
    action: str
    entity_type: str
    entity_id: UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    at: datetime


class AuditPage(BaseModel):
    """A page of history, newest first. `next_cursor` is null when there is nothing older."""

    entries: list[AuditEntryResponse]
    next_cursor: str | None = None
    #: What each id met on this page is called by the people who read it: a container number, an
    #: order number, "order · SKU" for a line, an invoice number. An id that names nothing any more
    #: (the object was deleted) is simply absent.
    labels: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------- rate cards


class RateCardIn(BaseModel):
    cost_type: CostType
    scope: Literal[CostScope.CONTAINER, CostScope.SHIPMENT] = CostScope.CONTAINER
    basis: RateBasis = RateBasis.FLAT
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=4)
    currency: str
    hs_code: str | None = Field(default=None, max_length=12)
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str) -> str:
        return currency_code(v)


class RateCardUpdate(BaseModel):
    basis: RateBasis | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=4)
    currency: str | None = None
    hs_code: str | None = None
    notes: str | None = None

    @field_validator("currency")
    @classmethod
    def _cur(cls, v: str | None) -> str | None:
        return None if v is None else currency_code(v)


class RateCardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    cost_type: CostType
    scope: CostScope
    basis: RateBasis
    amount: Qty
    currency: str
    hs_code: str | None
    notes: str | None


class EstimateResponse(BaseModel):
    """What applying the rate cards to a container produced, and what it did not."""

    created: list[CostResponse]
    skipped: dict[str, str]  # cost type -> the reason, in plain words


# ---------------------------------------------------------------------------- variance report


class VarianceRow(BaseModel):
    key: str  # a cost type, or a purchase order number
    label: str
    estimated: Money
    actual: Money
    variance: Money


class VarianceReport(BaseModel):
    """What the month's invoices cost against what was expected of them.

    Only costs that replaced an estimate appear: an invoice nobody forecast has nothing to differ
    from, and counting it as a 100 % overrun would be a lie by arithmetic.
    """

    period: str  # YYYY-MM
    base_currency: str
    estimated: Money
    actual: Money
    variance: Money
    pairs: int
    by_cost_type: list[VarianceRow]
    by_purchase_order: list[VarianceRow]


# ---------------------------------------------------------------------------- ERP


class ErpConnectionCreate(BaseModel):
    """Everything needed to reach an Odoo. The API key is sealed before it is stored."""

    kind: ErpKind = ErpKind.ODOO
    url: str = Field(min_length=8, max_length=500)
    database: str = Field(min_length=1, max_length=200)
    login: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=1, max_length=500)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        # A plain ValueError reaches the client as code "value_error" and an English sentence
        # prefixed with "Value error, ". These two are shown in a form, so they carry a code of
        # their own for the screen to translate — the message stays as the fallback.
        value = v.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise PydanticCustomError("ERP_URL_SCHEME", "url must start with http:// or https://")
        # `https://admin:secret@erp.example.com` is a URL someone can reasonably paste. This column
        # is stored in clear and returned by the API, unlike the key, which is sealed — so the
        # password would end up on a screen. Refused rather than quietly stripped: dropping half of
        # what they typed would fail to authenticate later, for a reason they could not see.
        host = value.split("://", 1)[1].split("/", 1)[0]
        if "@" in host:
            raise PydanticCustomError(
                "ERP_URL_HAS_CREDENTIALS",
                "url must not carry a login: put the user in 'login' and the password in 'api_key'",
            )
        return value


class ErpConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: ErpKind
    url: str
    database: str
    login: str  # the API key is never returned, in any form
    active: bool
    server_version: str | None
    company: str | None
    last_sync_at: datetime | None
    #: Why the last read failed, in the ERP's own words. Null once a read works again.
    last_error: str | None
    #: How many scheduled reads have failed in a row.
    consecutive_failures: int = 0
    #: When the schedule will try again. Pressing the button ignores it: a person is waiting.
    retry_after: datetime | None = None
    created_at: datetime


class ErpSyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    status: ErpSyncStatus
    started_at: datetime
    finished_at: datetime | None
    purchase_orders: int
    lines: int
    import_job_id: UUID | None
    error: str | None
    detail: dict[str, Any]


# ---------------------------------------------------------------------------- finance reports


class ReportBucket(BaseModel):
    key: str
    label: str
    fob: Money
    allocated: Money
    vat: Money
    landed: Money
    quantity: Qty
    load_count: int
    #: Freight as a percentage of the goods. Null when there are no goods to compare it to.
    freight_share_pct: Pct | None = None
    by_cost_type: dict[str, Money] = Field(default_factory=dict)


class ReportSkuLine(BaseModel):
    sku: str
    quantity: Qty
    fob: Money
    landed: Money
    unit_landed_cost: Qty
    load_count: int


class LandedCostAnalysis(BaseModel):
    base_currency: str
    group_by: Literal["supplier", "route", "month", "cost_type"]
    period_from: date | None
    period_to: date | None
    buckets: list[ReportBucket]
    skus: list[ReportSkuLine]
    totals: ReportBucket


class DndReportLine(BaseModel):
    container_id: UUID
    container_number: str
    paid: Money
    #: Null when nothing can be estimated honestly; `rule_code` says why.
    avoided: Money | None
    #: Deprecated: `rule_code` and its values in an English sentence, kept until the screens read the
    #: code. Never show it.
    rule: str
    #: DND_AVOIDED_ESTIMATED (`days` between the first risk alert and the pickup x `daily_rate`),
    #: DND_AVOIDED_NO_RATE (no DEMURRAGE rate card), DND_AVOIDED_NO_ALERT (no alert or no pickup).
    rule_code: Literal["DND_AVOIDED_ESTIMATED", "DND_AVOIDED_NO_RATE", "DND_AVOIDED_NO_ALERT"]
    days: int | None
    daily_rate: Money | None
    #: Days past the last free day at pickup — the demurrage days paid for — when both are known.
    days_over: int | None


class DndAtRiskLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    container_id: UUID
    container_number: str
    dnd_risk: DndRisk
    last_free_day: date | None
    rule: str
    #: Days already past the last free day (0 while it is ahead), and days left (negative once past).
    days_over: int = 0
    days_left: int | None = None
    #: The organization's DEMURRAGE rate card read as a price per day, and `days_over` at that price.
    #: Both null without a rate card: no figure is made up.
    daily_rate: Money | None = None
    amount_at_risk: Money | None = None


class DndAnalysis(BaseModel):
    base_currency: str
    period_from: date | None
    period_to: date | None
    paid: Money
    avoided: Money
    estimable: int
    not_estimable: int
    lines: list[DndReportLine]
    #: Always sent, empty when nothing is at risk. A default here would make it optional in the
    #: generated client for a field the API never omits.
    at_risk: list[DndAtRiskLine]


class SkuHistoryPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    recorded_on: date
    quantity: Qty
    fob_base: Money
    landed_base: Money
    unit_landed_cost: Qty
    load_count: int


class SkuHistoryResponse(BaseModel):
    sku: str
    base_currency: str
    points: list[SkuHistoryPoint]


# ---------------------------------------------------------------------------- ERP write-back


class ErpBlocker(BaseModel):
    code: str
    message: str
    #: The moving parts of `message`, so a screen can write the sentence in its own language
    #: instead of showing ours. Empty for the blockers that have none.
    params: dict[str, str] = Field(default_factory=dict)


class ErpPlannedLine(BaseModel):
    move_id: int
    product_name: str
    sku: str | None
    quantity: Qty
    amount: Money


class ErpPlannedCost(BaseModel):
    label: str
    cost_ids: list[UUID]
    amount: Money
    lines: list[ErpPlannedLine]
    #: The document that already carries this cost, or null when it is still to be pushed. A landed
    #: cost adds to the stock valuation rather than replacing it, so what has gone never goes again.
    pushed_as: str | None = None
    #: Which push that was. The document's name does not identify it — several documents on one
    #: container can share a name — and this is what "forget this push" needs.
    pushed_push_id: UUID | None = None


class ErpLandedCostPreview(BaseModel):
    """Exactly what would be created in the ERP, and everything standing in the way of it.

    Nothing is written to produce this.
    """

    container_id: UUID
    container_number: str
    receipt_id: int | None
    receipt_name: str | None
    #: Every invoiced cost on the container, pushed or not, so the screen shows the whole picture.
    costs: list[ErpPlannedCost]
    #: Only what this push would carry. The costs already written are not counted twice.
    total: Money
    blockers: list[ErpBlocker]
    pushable: bool
    #: The documents already holding costs of this container, joined by ", ".
    already_pushed_as: str | None = None


class ErpPushResponse(BaseModel):
    id: UUID
    odoo_model: str
    odoo_id: int
    odoo_name: str | None
    #: Deep link into the customer's Odoo web client for this document.
    record_url: str | None = None
    #: Always `draft`: validating a landed cost posts entries in someone else's books.
    status: str
    #: The costs this document carries — the subset that was still pending when it was written.
    cost_ids: list[UUID]
    created_at: datetime
    #: True when a push interrupted before it could be recorded here had already created this
    #: document, and this call filled it in rather than creating a second one.
    adopted: bool = False
    #: Set once the document is gone from the ERP and this push was forgotten, so its costs became
    #: pushable again. The row is never deleted.
    forgotten_at: datetime | None = None
    forgotten_reason: str | None = None
