"""Reading a supplier invoice.

THE RULE, and it holds everywhere below this line: **no cost is ever created without a human
confirming it.** Extraction proposes; a person accepts, line by line, and only then does
`POST /invoices/{id}/confirm` write anything into the ledger. A confident extractor and a careless
one differ in how much typing they save, never in what reaches the accounts.

Amounts cross this boundary as strings. A float would already have lost the cents by the time anyone
looked, and the extractor is exactly where a wrong 0.01 is easiest to introduce and hardest to see.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.errors import DomainError
from app.domain.models import CostType


class ExtractorKind(enum.StrEnum):
    """Who read the document, in words a finance director can be shown.

    The extractor's own name — « regex », « llm » — is our plumbing and reaches a CFO's screen
    through the API. The name stays, because that is what a support ticket needs; this is what the
    front translates.
    """

    RULES = "RULES"
    MODEL = "MODEL"
    UNKNOWN = "UNKNOWN"


class ConfidenceBand(enum.StrEnum):
    """How much of a second look a reading needs. `confidence` keeps the number as it is."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


#: A reading nothing was found wrong with. Below it, something was capped or missed.
HIGH_CONFIDENCE = Decimal("0.8")
#: Under this, no line of the reading can be trusted enough to be read rather than retyped.
LOW_CONFIDENCE = Decimal("0.5")

EXTRACTOR_KINDS = {"regex": ExtractorKind.RULES, "llm": ExtractorKind.MODEL}


def extractor_kind(name: str | None) -> ExtractorKind:
    return EXTRACTOR_KINDS.get(name or "", ExtractorKind.UNKNOWN)


def confidence_band(confidence: Decimal | None) -> ConfidenceBand | None:
    if confidence is None:
        return None
    if confidence >= HIGH_CONFIDENCE:
        return ConfidenceBand.HIGH
    if confidence >= LOW_CONFIDENCE:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


class ExtractionFailed(DomainError):
    status = 422
    code = "EXTRACTION_FAILED"


class ExtractorNotConfigured(DomainError):
    status = 501
    code = "EXTRACTOR_NOT_CONFIGURED"


class ExtractedLine(BaseModel):
    """One charge on the invoice, as read. `amount` is a decimal string, never a float."""

    description: str = ""
    amount: str
    currency: str | None = None
    cost_type: CostType | None = None
    container_number: str | None = None
    po_number: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    def decimal(self) -> Decimal | None:
        try:
            return Decimal(self.amount)
        except (InvalidOperation, ValueError):
            return None


class FreightInvoice(BaseModel):
    """What an extractor claims to have read. Nothing here is trusted enough to become a cost."""

    vendor: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    currency: str | None = None
    total: str | None = None
    #: What the invoice itself states as the pre-tax total — its own "Total HT", not total minus
    #: VAT. Kept apart from the arithmetic precisely so the two can be compared: a charge the
    #: extractor missed shows up as lines that do not reach the figure the invoice prints.
    subtotal: str | None = None
    vat: str | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)
    container_numbers: list[str] = Field(default_factory=list)
    bl_numbers: list[str] = Field(default_factory=list)
    po_numbers: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ExtractionContext:
    """What the extractor is allowed to know about this organization.

    The container and PO lists are given to the model so that it *recognises* references instead of
    inventing them: anything it returns that is not in these lists is dropped downstream.
    """

    base_currency: str
    containers: dict[str, UUID] = field(default_factory=dict)  # container number -> id
    purchase_orders: dict[str, UUID] = field(default_factory=dict)  # PO number -> id
    shipment_references: dict[str, UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionInput:
    content: bytes
    content_type: str
    filename: str | None = None


@runtime_checkable
class DocumentExtractor(Protocol):
    name: str

    def extract(self, document: ExtractionInput, context: ExtractionContext) -> FreightInvoice:
        """Read the document, or raise `ExtractionFailed` with a reason a human can act on."""
        ...
