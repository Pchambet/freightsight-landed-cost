"""Reading purchase orders out of someone else's ERP.

Read-only, on purpose and for now: this connector is the commercial door — "it already knows your
orders" — and writing back into a customer's accounting system is a promise with a much higher price
of failure. The landed-cost write-back (Odoo's stock.landed.cost) is its own brick, later.

Amounts and quantities cross this boundary as strings, like everywhere else money travels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from app.core.errors import DomainError


class ErpAuthError(DomainError):
    """The credentials were refused. The message carries what the ERP said, not our guess."""

    status = 422
    code = "ERP_AUTH_FAILED"


class ErpUnavailable(DomainError):
    """Nothing answered at that address.

    Every one of these carries `params` with the host, and nothing else: the screen that shows it is
    the customer's, and `[Errno 111] Connection refused at http://host.docker.internal:8169` told a
    prospect our container's hostname and an errno instead of "their Odoo is down". The socket's own
    words stay in the logs, where the person who can act on them is.
    """

    status = 502
    code = "ERP_UNREACHABLE"


class ErpTimeout(ErpUnavailable):
    """The ERP took longer than we give it. Its own state, not a network that is down."""

    code = "ERP_TIMEOUT"


class ErpBadResponse(ErpUnavailable):
    """Something answered, and it was not an ERP: a proxy error page, a redirect, broken XML."""

    code = "ERP_BAD_RESPONSE"


class ErpProtocolError(DomainError):
    """The ERP answered something we cannot use — a missing model, a renamed field."""

    status = 502
    code = "ERP_PROTOCOL_ERROR"


@dataclass(frozen=True)
class ErpIdentity:
    """What a successful connection test learned, shown back to the user as proof."""

    server_version: str
    database: str
    login: str
    user_id: int
    company: str | None = None


@dataclass(frozen=True)
class ErpLine:
    description: str
    quantity: str
    unit_price: str
    sku: str | None = None
    uom: str | None = None
    unit_weight_kg: str | None = None
    unit_volume_cbm: str | None = None
    hs_code: str | None = None


@dataclass(frozen=True)
class ErpPurchaseOrder:
    external_id: str
    number: str
    currency: str
    supplier: str | None = None
    order_date: date | None = None
    #: Cancelled in the ERP. Provisional decision: we mark, we never delete — the order may already
    #: carry costs and containers here, and deleting it would rewrite a landed cost silently.
    cancelled: bool = False
    lines: list[ErpLine] = field(default_factory=list)


@dataclass(frozen=True)
class ErpReceiptLine:
    """One line of a receipt in the ERP, and whether Odoo can value it.

    `valued` is the question that decides everything: Odoo refuses a landed cost on a product whose
    category is not in real-time valuation with a real cost method, and it refuses it at validation —
    long after we would have written it.
    """

    move_id: int
    product_id: int
    product_name: str
    sku: str | None
    quantity: str
    po_number: str | None
    po_line_id: int | None
    valued: bool
    valuation: str | None = None
    cost_method: str | None = None


@dataclass(frozen=True)
class ErpReceipt:
    picking_id: int
    name: str
    po_number: str | None
    done_at: str | None
    lines: list[ErpReceiptLine] = field(default_factory=list)


@dataclass(frozen=True)
class ErpCostLine:
    """One charge to push, already split across the receipt's lines by our own engine."""

    label: str
    amount: str
    #: `move_id -> amount`, summing to `amount`. This is the whole point of the integration: Odoo is
    #: told what each line costs rather than being asked to guess a split.
    per_move: dict[int, str] = field(default_factory=dict)
    cost_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ErpPushResult:
    model: str
    record_id: int
    name: str
    state: str
    amount_total: str
    #: True when this is a draft an interrupted push had already created, found again by its marker
    #: and filled in, rather than a new document.
    adopted: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErpDocument:
    """A document that is still in the ERP, and what state it is in."""

    record_id: int
    name: str
    state: str


@runtime_checkable
class ErpWriter(Protocol):
    """Writing back into the ERP. Separate from reading because the risk is not the same."""

    def find_receipts(self, po_numbers: list[str]) -> list[ErpReceipt]:
        """The done receipts of these purchase orders, with what Odoo knows about each line."""
        ...

    def company_currency(self) -> str | None:
        """The currency the ERP's company keeps its books in, or None if it will not say.

        We push a bare number into `price_unit`, and the ERP reads it in its company's currency. A
        group whose Odoo is in CHF would get our euros valued as francs, silently and in their
        stock valuation — so the two currencies are compared before anything is written.
        """
        ...

    def create_landed_cost(
        self,
        picking_id: int,
        cost_lines: list[ErpCostLine],
        *,
        landed_cost_product_id: int | None = None,
        journal_id: int | None = None,
        on_date: date | None = None,
        reference: str | None = None,
        marker: str | None = None,
    ) -> ErpPushResult:
        """Create a landed cost in draft, carrying our amounts. Never validated by us.

        `marker` identifies this exact set of costs. The implementation writes it into the document
        and looks for it before creating, so a push interrupted between the ERP write and our own
        commit is resumed rather than duplicated in the customer's books.
        """
        ...

    def read_landed_cost(self, record_id: int) -> ErpDocument | None:
        """The document, or None if it is no longer in the ERP.

        Asked before a push is forgotten: a push stops holding its costs only once the document it
        made is really gone, or forgetting it would let the same charge be written twice.
        """
        ...


@runtime_checkable
class ErpConnector(Protocol):
    name: str

    def test_connection(self) -> ErpIdentity:
        """Authenticate, or raise `ErpAuthError` with the reason the ERP gave."""
        ...

    def fetch_purchase_orders(
        self, since: datetime | None = None, limit: int | None = None
    ) -> list[ErpPurchaseOrder]:
        """Confirmed orders, newest changes first. `since` filters on the ERP's own write date."""
        ...
