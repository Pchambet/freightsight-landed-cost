"""Odoo over XML-RPC, verified against a real Odoo 17.

Two endpoints and nothing else: `/xmlrpc/2/common` to authenticate, `/xmlrpc/2/object` to read. No
`odoorpc` dependency for what is two proxies and a method call, and no HTTP session to keep alive.

Three things a real Odoo taught me, which the fixtures now encode:

  * **A line's quantity is in the line's unit of measure, not the product's.** The demo data orders
    "20 Dozens" of a product whose weight is per unit. Importing 20 where the customer means 240
    would understate every weight-based allocation, so quantities are converted through the UoM
    ratio and the original unit is kept in the description.
  * **`hs_code` does not exist on `product.product` in Odoo 17 Community.** It arrives with
    localisation or Enterprise modules. The adapter asks `fields_get` what exists and reads the
    field only if it is there, rather than failing on a customer who does not have it.
  * **A wrong password is not an exception**: `authenticate` returns `False`. A wrong *field* is a
    `Fault` carrying a full Python traceback, whose last line is the only part worth showing.
"""

from __future__ import annotations

import logging
import re
import xmlrpc.client
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit
from xml.parsers.expat import ExpatError

from app.core.net import assert_public_host
from app.domain.erp.ports import (
    ErpAuthError,
    ErpBadResponse,
    ErpCostLine,
    ErpDocument,
    ErpIdentity,
    ErpLine,
    ErpProtocolError,
    ErpPurchaseOrder,
    ErpPushResult,
    ErpReceipt,
    ErpReceiptLine,
    ErpTimeout,
    ErpUnavailable,
)

logger = logging.getLogger(__name__)

NAME = "odoo"
#: Orders in these states are commitments; a draft is not something to plan a landed cost around.
CONFIRMED_STATES = ("purchase", "done")
#: Fields we ask for on the product, when the instance has them.
OPTIONAL_PRODUCT_FIELDS = ("hs_code", "l10n_in_hsn_code")
#: Orders read per round trip. Odoo's own default page in the web client is 80; a customer with ten
#: thousand orders should not be one `search_read` away from an XML document nobody can hold.
PAGE_SIZE = 200
#: States that mean "this order is no longer a commitment".
CANCELLED_STATES = ("cancel",)

#: Where the marker is written, and how it reads once it is there. `description` ("Item Description"
#: in the form) is stored text with no accounting meaning, so a string of ours in it changes nothing
#: about the document — unlike the name, which is the reference the accountant reads.
MARKER_PREFIX = "FreightSight-ref:"

#: How long any single call to a customer's ERP may take. Generous, because a real Odoo reading
#: two hundred orders is not fast; finite, because the alternative is a worker that never comes back.
DEFAULT_TIMEOUT_SECONDS = 60.0

ProxyFactory = Callable[[str, float], Any]

#: The precision of `po_lines.unit_price`, so a converted price is stored as it will be read.
PRICE_PRECISION = Decimal("0.0001")


class _TimeoutTransport(xmlrpc.client.Transport):
    """`ServerProxy` takes no timeout, and its default transport waits forever.

    Measured, not assumed: a call to an address that drops packets was still blocked after 12
    seconds with no sign of stopping. One customer whose Odoo has gone dark would hold the daily
    sweep open for as long as the socket stayed up — and the sweep holds a queueing lock, so every
    *other* customer's sync would stop with it.
    """

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: Any) -> Any:
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    """The same, for https — which is what a hosted Odoo actually is."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.timeout = timeout

    def make_connection(self, host: Any) -> Any:
        connection = super().make_connection(host)
        connection.timeout = self.timeout
        return connection


def _default_proxy(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
    transport = _TimeoutSafeTransport(timeout) if url.startswith("https:") else _TimeoutTransport(timeout)
    return xmlrpc.client.ServerProxy(url, allow_none=True, transport=transport)


def _description(marker: str, reference: str | None) -> str:
    """What a human reads in the document, with the key we search on at the end of it.

    The sentence is there for whoever opens the draft and wonders what the string is: it is how a
    retried push finds this document again instead of writing a second one.
    """
    head = f"{reference}\n" if reference else ""
    return (
        f"{head}Allocated by FreightSight. Please leave the reference below in place: it is how a "
        f"retried push finds this draft again instead of creating a second one.\n"
        f"{MARKER_PREFIX} {marker}"
    )


def _decimal(value: Any) -> str | None:
    """Odoo speaks float over XML-RPC; it becomes a string here and stays one."""
    if value in (None, False, ""):
        return None
    try:
        return f"{Decimal(str(value)):f}"
    except (InvalidOperation, ValueError):
        return None


#: `https://user:secret@host` — a URL a customer can perfectly well have typed into the form, and
#: which then travels inside every connection error the socket layer produces.
_URL_USERINFO = re.compile(r"(://[^/\s:@]+):[^/\s@]*@")


def _fault_reason(exc: xmlrpc.client.Fault) -> str:
    """The last meaningful line of an Odoo traceback, which is the part a person can act on."""
    lines = [line.strip() for line in str(exc.faultString or "").splitlines() if line.strip()]
    return lines[-1] if lines else "Odoo returned an error"


#: Everything that can come back from the wire instead of an answer. `ProtocolError` is what
#: `xmlrpc` raises on any non-200 — a proxy's error page, a login wall, **and a redirect**, which is
#: why a 302 to somewhere else is never followed. `ExpatError` is an answer that is not XML at all.
TRANSPORT_ERRORS = (OSError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError, ExpatError)


class OdooConnector:
    name = NAME

    def __init__(
        self,
        url: str,
        database: str,
        login: str,
        api_key: str,
        *,
        proxy_factory: ProxyFactory | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.url = url.rstrip("/")
        self.database = database
        self.login = login
        self.api_key = api_key
        # Resolved here rather than as a default argument, so the module-level factory can be
        # replaced (a test transport, one day a proxy) without rebuilding this class.
        self._timeout = timeout
        self._proxy_factory = proxy_factory or _default_proxy
        self._uid: int | None = None

    # ------------------------------------------------------------------ plumbing

    def _proxy(self, path: str) -> Any:
        """A proxy on one of Odoo's two endpoints, for a host we are allowed to reach.

        Vetted here rather than once when the connection was saved: the name a customer stored last
        week can resolve to 127.0.0.1 today, and this is the last of our code that runs before a
        socket opens. Outside production the check stands down (`core.net`), so the demo against
        `host.docker.internal` is untouched.
        """
        assert_public_host(self.url)
        return self._proxy_factory(f"{self.url}{path}", self._timeout)

    def _common(self) -> Any:
        return self._proxy("/xmlrpc/2/common")

    def _models(self) -> Any:
        return self._proxy("/xmlrpc/2/object")

    def _host(self) -> str:
        """The address as the customer knows it: no credentials, no path, no scheme."""
        return urlsplit(self.url).netloc.rsplit("@", 1)[-1] or self.url

    def _down(self, exc: Exception, *, during: str) -> ErpUnavailable:
        """One coded error for anything the wire raises, with the technical detail left in the logs.

        What the socket says — an errno, our own container's hostname, a proxy's HTML — belongs to
        whoever operates this service, not to the customer looking at their ERP card. They get the
        host, the code, and a sentence their screen can translate.
        """
        host = self._host()
        logger.warning(
            "odoo call failed",
            extra={"host": host, "during": during, "detail": self._safe(f"{type(exc).__name__}: {exc}")},
        )
        params = {"host": host}
        if isinstance(exc, TimeoutError):
            return ErpTimeout(
                f"Odoo at {host} did not answer within {self._timeout:.0f} seconds.",
                params={**params, "seconds": f"{self._timeout:.0f}"},
            )
        if isinstance(exc, OSError):
            return ErpUnavailable(f"Odoo is unreachable at {host}.", params=params)
        return ErpBadResponse(
            f"{host} answered something that is not an Odoo XML-RPC endpoint.", params=params
        )

    def _authenticate(self) -> int:
        if self._uid is not None:
            return self._uid
        try:
            uid = self._common().authenticate(self.database, self.login, self.api_key, {})
        except xmlrpc.client.Fault as exc:
            # A database that does not exist comes back as a Fault, not as False.
            raise ErpAuthError(
                self._safe(f"Odoo refused the connection: {_fault_reason(exc)}"),
                params={"host": self._host(), "database": self.database, "login": self.login},
            ) from exc
        except TRANSPORT_ERRORS as exc:
            raise self._down(exc, during="authenticate") from exc
        if not uid:
            raise ErpAuthError(
                f"Odoo refused the credentials for {self.login!r} on database {self.database!r}. "
                "Check the login and the API key (Preferences → Account Security → API keys).",
                params={"host": self._host(), "database": self.database, "login": self.login},
            )
        self._uid = int(uid)
        return self._uid

    def _safe(self, text: str) -> str:
        """Nothing we store or show may contain the key to a customer's accounting.

        Error text from an ERP is not ours to trust: a traceback rendered with locals, a proxy's
        error page, or a URL with credentials in it all carry the secret out into a database column
        and onto a screen. Cheap to redact here, impossible to take back afterwards.
        """
        cleaned = text.replace(self.api_key, "***") if self.api_key else text
        return _URL_USERINFO.sub(r"\1:***@", cleaned)

    def _kw(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        uid = self._authenticate()
        try:
            return self._models().execute_kw(
                self.database, uid, self.api_key, model, method, list(args), kwargs
            )
        except xmlrpc.client.Fault as exc:
            raise ErpProtocolError(
                self._safe(f"Odoo could not answer {model}.{method}: {_fault_reason(exc)}"),
                params={"host": self._host(), "model": model, "method": method},
            ) from exc
        except TRANSPORT_ERRORS as exc:
            raise self._down(exc, during=f"{model}.{method}") from exc

    # ------------------------------------------------------------------ the port

    def test_connection(self) -> ErpIdentity:
        try:
            version = self._common().version().get("server_version", "unknown")
        except TRANSPORT_ERRORS as exc:
            raise self._down(exc, during="version") from exc
        uid = self._authenticate()
        user = self._kw("res.users", "read", [uid], fields=["name", "company_id"])
        company = None
        if user and user[0].get("company_id"):
            company = user[0]["company_id"][1]
        return ErpIdentity(
            server_version=str(version),
            database=self.database,
            login=self.login,
            user_id=uid,
            company=company,
        )

    def fetch_purchase_orders(
        self, since: datetime | None = None, limit: int | None = None
    ) -> list[ErpPurchaseOrder]:
        orders = self._paged_orders(since, limit)
        if not orders:
            return []

        line_ids = [line_id for order in orders for line_id in order.get("order_line") or []]
        lines = (
            self._kw(
                "purchase.order.line",
                "search_read",
                [["id", "in", line_ids]],
                fields=[
                    "order_id",
                    "name",
                    "product_id",
                    "product_qty",
                    "price_unit",
                    "product_uom",
                    "display_type",
                ],
            )
            if line_ids
            else []
        )
        product_fields = [
            "default_code",
            "name",
            "weight",
            "volume",
            "uom_id",
            *self._optional_fields(),
        ]
        product_ids = sorted({ln["product_id"][0] for ln in lines if ln.get("product_id")})
        products = (
            {p["id"]: p for p in self._kw("product.product", "read", product_ids, fields=product_fields)}
            if product_ids
            else {}
        )
        uom_ids = sorted(
            {ln["product_uom"][0] for ln in lines if ln.get("product_uom")}
            | {p["uom_id"][0] for p in products.values() if p.get("uom_id")}
        )
        uoms = (
            {u["id"]: u for u in self._kw("uom.uom", "read", uom_ids, fields=["name", "factor_inv"])}
            if uom_ids
            else {}
        )

        by_order: dict[int, list[ErpLine]] = {}
        for raw in lines:
            if raw.get("display_type"):
                continue  # a section header or a note, not something bought
            by_order.setdefault(raw["order_id"][0], []).append(self._line(raw, products, uoms))

        return [
            ErpPurchaseOrder(
                external_id=str(order["id"]),
                number=str(order["name"]),
                currency=str(order["currency_id"][1]) if order.get("currency_id") else "",
                supplier=str(order["partner_id"][1]) if order.get("partner_id") else None,
                order_date=_as_date(order.get("date_order")),
                cancelled=str(order.get("state")) in CANCELLED_STATES,
                lines=by_order.get(order["id"], []),
            )
            for order in orders
        ]

    def _paged_orders(self, since: datetime | None, limit: int | None) -> list[dict[str, Any]]:
        """Read the orders in pages, walking forward on the id.

        A cursor on the id rather than an offset: the customer's staff keep confirming orders while
        this runs, and an offset would skip whatever was inserted between two pages.
        """
        base: list[Any] = [["state", "in", [*CONFIRMED_STATES, *CANCELLED_STATES]]]
        if since is not None:
            base.append(["write_date", ">=", since.strftime("%Y-%m-%d %H:%M:%S")])

        collected: list[dict[str, Any]] = []
        after = 0
        while True:
            page_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(collected))
            if page_size <= 0:
                break
            page = self._kw(
                "purchase.order",
                "search_read",
                [*base, ["id", ">", after]],
                fields=["name", "partner_id", "currency_id", "date_order", "state", "order_line"],
                order="id",
                limit=page_size,
            )
            if not page:
                break
            collected.extend(page)
            after = int(page[-1]["id"])
            if len(page) < page_size:
                break
        return collected

    # ------------------------------------------------------------------ writing back

    def company_currency(self) -> str | None:
        """The currency the company behind this login keeps its books in, as its ISO code.

        Two reads rather than one: `res.users` gives the company, `res.company` its currency, and
        `res.currency.name` *is* the ISO code in Odoo. A connector that cannot see either answers
        None, and the caller then says "we could not check" instead of inventing a match.
        """
        uid = self._authenticate()
        user = self._kw("res.users", "read", [uid], fields=["company_id"])
        company = user[0].get("company_id") if user else None
        if not company:
            return None
        rows = self._kw("res.company", "read", [int(company[0])], fields=["currency_id"])
        currency = rows[0].get("currency_id") if rows else None
        return str(currency[1]) if currency else None

    def find_receipts(self, po_numbers: list[str]) -> list[ErpReceipt]:
        """The done receipts of these orders, with enough about each line to know if Odoo can value it."""
        if not po_numbers:
            return []
        orders = self._kw(
            "purchase.order",
            "search_read",
            [["name", "in", po_numbers]],
            fields=["name", "picking_ids"],
        )
        picking_ids = [pid for order in orders for pid in order.get("picking_ids") or []]
        if not picking_ids:
            return []
        pickings = self._kw(
            "stock.picking",
            "search_read",
            [["id", "in", picking_ids], ["state", "=", "done"]],
            fields=["name", "state", "date_done", "purchase_id"],
        )
        if not pickings:
            return []
        moves = self._kw(
            "stock.move",
            "search_read",
            [["picking_id", "in", [p["id"] for p in pickings]], ["state", "=", "done"]],
            fields=["picking_id", "product_id", "purchase_line_id", "quantity"],
        )
        product_ids = sorted({m["product_id"][0] for m in moves if m.get("product_id")})
        products = (
            {
                p["id"]: p
                for p in self._kw(
                    "product.product",
                    "read",
                    product_ids,
                    # `valuation` and `cost_method` come from the product's category and are exactly
                    # what decides whether a landed cost is possible at all.
                    fields=["default_code", "name", "valuation", "cost_method"],
                )
            }
            if product_ids
            else {}
        )
        po_line_numbers = self._po_numbers_by_line(moves)

        by_picking: dict[int, list[ErpReceiptLine]] = {}
        for move in moves:
            product = products.get(move["product_id"][0]) if move.get("product_id") else None
            line_id = move["purchase_line_id"][0] if move.get("purchase_line_id") else None
            valuation = (product or {}).get("valuation")
            cost_method = (product or {}).get("cost_method")
            by_picking.setdefault(move["picking_id"][0], []).append(
                ErpReceiptLine(
                    move_id=int(move["id"]),
                    product_id=int(move["product_id"][0]) if move.get("product_id") else 0,
                    product_name=str((product or {}).get("name") or ""),
                    sku=(product or {}).get("default_code") or None,
                    quantity=f"{Decimal(str(move.get('quantity') or 0)):f}",
                    po_number=po_line_numbers.get(line_id) if line_id is not None else None,
                    po_line_id=line_id,
                    valued=valuation == "real_time" and cost_method in ("fifo", "average"),
                    valuation=valuation,
                    cost_method=cost_method,
                )
            )
        return [
            ErpReceipt(
                picking_id=int(picking["id"]),
                name=str(picking["name"]),
                po_number=str(picking["purchase_id"][1]) if picking.get("purchase_id") else None,
                done_at=picking.get("date_done") or None,
                lines=by_picking.get(picking["id"], []),
            )
            for picking in pickings
        ]

    def _po_numbers_by_line(self, moves: list[dict[str, Any]]) -> dict[int, str]:
        line_ids = sorted({m["purchase_line_id"][0] for m in moves if m.get("purchase_line_id")})
        if not line_ids:
            return {}
        lines = self._kw("purchase.order.line", "read", line_ids, fields=["order_id"])
        return {int(line["id"]): str(line["order_id"][1]) for line in lines if line.get("order_id")}

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
        """Create the landed cost in draft and write our own split into it.

        Verified against a real Odoo 17, because the documentation does not say it: the valuation
        adjustment lines cannot be given at creation — Odoo computes them — but
        `additional_landed_cost` on each of them **is** writable afterwards. So the sequence is
        create, `compute_landed_cost`, then overwrite each line with the amount our engine
        allocated. The record stays in `draft`: validating it posts accounting entries, and that is
        a decision for the person whose books they are.

        With a `marker`, the document is looked for before it is created. Creating in someone's ERP
        and recording it here are two writes to two systems, and the gap between them can end in a
        crash; without this, the draft would exist at the customer with no trace here and the next
        click would make a second one. The marker goes into `description`, which Odoo stores and
        which carries no accounting meaning, and comes back out through a search.
        """
        product_id = landed_cost_product_id or self._default_landed_cost_product()
        journal = journal_id or self._default_journal()
        values: dict[str, Any] = {
            "date": (on_date or date.today()).isoformat(),  # noqa: DTZ011 - an accounting date
            "target_model": "picking",
            "picking_ids": [(6, 0, [picking_id])],
            "account_journal_id": journal,
            "cost_lines": [
                (
                    0,
                    0,
                    {
                        "product_id": product_id,
                        "name": line.label[:200],
                        # Our own split is written line by line below; the method only decides the
                        # first guess Odoo makes, which we then replace.
                        "split_method": "equal",
                        "price_unit": float(Decimal(line.amount)),
                    },
                )
                for line in cost_lines
            ],
        }
        if reference:
            values["name"] = reference
        if marker:
            values["description"] = _description(marker, reference)

        adopted = self._find_draft(picking_id, marker, len(cost_lines)) if marker else None
        if adopted is not None:
            record_id = adopted
        else:
            created = self._kw("stock.landed.cost", "create", values)
            record_id = int(created[0] if isinstance(created, list) else created)
        # Recomputing an adopted draft is the same call as computing a new one, and it puts the
        # document in a known state whatever the interrupted attempt managed to write.
        self._kw("stock.landed.cost", "compute_landed_cost", [record_id])
        record = self._kw(
            "stock.landed.cost",
            "read",
            [record_id],
            fields=["name", "state", "amount_total", "cost_lines", "valuation_adjustment_lines"],
        )[0]
        self._apply_our_split(record_id, cost_lines, record.get("cost_lines") or [])
        return ErpPushResult(
            model="stock.landed.cost",
            record_id=record_id,
            name=str(record.get("name") or ""),
            state=str(record.get("state") or ""),
            amount_total=f"{Decimal(str(record.get('amount_total') or 0)):.2f}",
            adopted=adopted is not None,
            raw=record,
        )

    def read_landed_cost(self, record_id: int) -> ErpDocument | None:
        """Look the document up by id. `search_read` rather than `read`, because `read` on an id
        that no longer exists raises where an empty list is the answer we want."""
        found = self._kw(
            "stock.landed.cost",
            "search_read",
            [["id", "=", record_id]],
            fields=["name", "state"],
            limit=1,
        )
        if not found:
            return None
        return ErpDocument(
            record_id=record_id,
            name=str(found[0].get("name") or ""),
            state=str(found[0].get("state") or ""),
        )

    def _find_draft(self, picking_id: int, marker: str, expected_lines: int) -> int | None:
        """The draft a previous attempt left behind for exactly these costs, if there is one.

        Scoped to `draft` and to this receipt as well as to the marker: a validated landed cost is
        posted in the accounts and is not ours to touch again, and a draft hanging off another
        receipt is not the document we were making.
        """
        found = self._kw(
            "stock.landed.cost",
            "search_read",
            [
                ["state", "=", "draft"],
                ["picking_ids", "in", [picking_id]],
                ["description", "like", marker],
            ],
            fields=["name", "cost_lines"],
            order="id",
            limit=2,
        )
        if not found:
            return None
        if len(found) > 1:
            names = ", ".join(str(f.get("name") or f["id"]) for f in found)
            raise ErpProtocolError(
                f"Several drafts in this ERP carry the same FreightSight reference ({names}). "
                "Keep one and delete the others, then push again — picking one for you would "
                "leave the other behind in your accounts.",
                code="ERP_DUPLICATE_DRAFT",
            )
        draft = found[0]
        existing_lines = list(draft.get("cost_lines") or [])
        if len(existing_lines) != expected_lines:
            # Written over by hand since. Overwriting it back would be us deciding what someone
            # meant to change in their own books.
            raise ErpProtocolError(
                f"The draft {draft.get('name') or draft['id']} carries this FreightSight reference "
                f"but has {len(existing_lines)} cost line(s) where we expect {expected_lines}. It "
                "has been edited since; reconcile it in Odoo, or delete it and push again.",
                code="ERP_DRAFT_EDITED",
            )
        return int(draft["id"])

    def _our_line_ids(self, cost_lines: list[ErpCostLine], created_line_ids: list[int]) -> list[int]:
        """Which id in the document belongs to which of our charges, by identity and not by rank.

        Zipping our list with the ids Odoo returned assumed the x2many comes back in creation order.
        On a draft *adopted* from an interrupted attempt the order is whatever that attempt left, and
        a mismatch swaps the charges between products: the document still validates, the totals are
        still right, and the cost per SKU is quietly wrong — the one error nobody ever notices.

        So the lines are read back and matched on what we wrote into them, the label and the amount.
        Two charges that carry the same label *and* the same amount are indistinguishable in Odoo
        and interchangeable here; they are taken in id order, which is the old behaviour for the only
        case where it cannot do harm.
        """
        if not created_line_ids:
            return []
        rows = self._kw(
            "stock.landed.cost.lines", "read", sorted(created_line_ids), fields=["name", "price_unit"]
        )
        available: dict[tuple[str, str], list[int]] = {}
        for row in sorted(rows, key=lambda r: int(r["id"])):
            key = (str(row.get("name") or ""), f"{Decimal(str(row.get('price_unit') or 0)):.2f}")
            available.setdefault(key, []).append(int(row["id"]))
        matched: list[int] = []
        for line in cost_lines:
            key = (line.label[:200], f"{Decimal(line.amount):.2f}")
            candidates = available.get(key) or []
            if not candidates:
                raise ErpProtocolError(
                    f"The document does not carry the charge {line.label!r} we just wrote into it, "
                    "so we cannot tell which line our split belongs to. Delete this draft in Odoo "
                    "and push again.",
                    code="ERP_SPLIT_UNMATCHED",
                    params={"label": line.label},
                )
            matched.append(candidates.pop(0))
        return matched

    def _apply_our_split(
        self, record_id: int, cost_lines: list[ErpCostLine], created_line_ids: list[int]
    ) -> None:
        """Replace Odoo's split with ours, line by line.

        Matched on the adjustment's `cost_line_id` rather than on its label: Odoo truncates the name
        it stores, and a user can rename it, so matching on text would silently write nothing.
        """
        line_ids = self._our_line_ids(cost_lines, [int(line_id) for line_id in created_line_ids])
        adjustments = self._kw(
            "stock.valuation.adjustment.lines",
            "search_read",
            [["cost_id", "=", record_id]],
            fields=["move_id", "cost_line_id", "additional_landed_cost"],
        )
        wanted: dict[tuple[int, int], Decimal] = {}
        for line, line_id in zip(cost_lines, line_ids, strict=True):
            for move_id, amount in line.per_move.items():
                wanted[(int(line_id), int(move_id))] = Decimal(amount)
        ours = set(line_ids)
        for adjustment in adjustments:
            move = adjustment.get("move_id")
            cost_line = adjustment.get("cost_line_id")
            if not move or not cost_line or int(cost_line[0]) not in ours:
                continue
            # A receipt line we allocated nothing to still got a share from Odoo's own split, and
            # it has to be taken back: Odoo refuses to validate a document whose adjustments do
            # not add up to its cost lines, with a message that names no line. Zero, not skip.
            allocated = wanted.get((int(cost_line[0]), int(move[0])), Decimal("0"))
            self._kw(
                "stock.valuation.adjustment.lines",
                "write",
                [int(adjustment["id"])],
                {"additional_landed_cost": float(allocated)},
            )

    def _default_landed_cost_product(self) -> int:
        found = self._kw("product.product", "search", [["landed_cost_ok", "=", True]], limit=1, order="id")
        if not found:
            raise ErpProtocolError(
                "This Odoo has no product marked as a landed cost. Create a service product with "
                "'Is a Landed Cost' ticked, then push again.",
                code="NO_LANDED_COST_PRODUCT",
            )
        return int(found[0])

    def _default_journal(self) -> int:
        found = self._kw("account.journal", "search", [["type", "=", "general"]], limit=1, order="id")
        if not found:
            raise ErpProtocolError("This Odoo has no miscellaneous journal", code="NO_JOURNAL")
        return int(found[0])

    # ------------------------------------------------------------------ details

    def _optional_fields(self) -> list[str]:
        """Ask what this instance actually has: HS codes come from modules many customers lack."""
        try:
            available = self._kw("product.product", "fields_get", [], attributes=["type"])
        except ErpProtocolError:  # pragma: no cover - an Odoo that refuses fields_get is broken
            return []
        return [name for name in OPTIONAL_PRODUCT_FIELDS if name in available]

    def _line(self, raw: dict[str, Any], products: dict[int, Any], uoms: dict[int, Any]) -> ErpLine:
        product = products.get(raw["product_id"][0]) if raw.get("product_id") else None
        line_uom = uoms.get(raw["product_uom"][0]) if raw.get("product_uom") else None
        product_uom = uoms.get(product["uom_id"][0]) if product and product.get("uom_id") else None

        quantity = Decimal(str(raw.get("product_qty") or 0))
        unit_price = Decimal(str(raw.get("price_unit") or 0))
        uom_name = line_uom["name"] if line_uom else None
        if line_uom and product_uom and line_uom["id"] != product_uom["id"]:
            # "20 Dozens" is 240 of the thing the product's weight is expressed in. Convert, and
            # divide the price by the same ratio so the line total is untouched.
            ratio = Decimal(str(line_uom.get("factor_inv") or 1)) / Decimal(
                str(product_uom.get("factor_inv") or 1)
            )
            if ratio > 0:
                quantity *= ratio
                # Four decimals, the precision of the column this ends up in. On "20 Dozens at 500"
                # that is 240 at 41.6667, whose total is off by eight thousandths of a currency
                # unit — visible in a test, invisible in a landed cost, and honest about where it
                # comes from.
                unit_price = (unit_price / ratio).quantize(PRICE_PRECISION)

        hs_code = None
        if product:
            for candidate in OPTIONAL_PRODUCT_FIELDS:
                value = product.get(candidate)
                if value:
                    hs_code = str(value)
                    break

        return ErpLine(
            description=str(raw.get("name") or (product or {}).get("name") or ""),
            quantity=f"{quantity:f}",
            unit_price=f"{unit_price:f}",
            sku=(product or {}).get("default_code") or None,
            uom=uom_name,
            unit_weight_kg=_decimal((product or {}).get("weight")),
            unit_volume_cbm=_decimal((product or {}).get("volume")),
            hs_code=hs_code,
        )


def _as_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()  # noqa: DTZ007 - a date, no zone
    except ValueError:
        return None
