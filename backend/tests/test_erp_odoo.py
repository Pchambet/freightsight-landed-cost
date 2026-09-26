"""The Odoo connector, against fixtures taken from a real Odoo 17, and the sync that uses it."""

from __future__ import annotations

import base64
import json
import uuid
import xmlrpc.client
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.erp.odoo import OdooConnector
from app.core.crypto import CryptoError
from app.core.tenancy import set_current_org
from app.domain.erp.ports import ErpAuthError, ErpProtocolError, ErpUnavailable
from app.domain.erp.service import open_key, seal_key, to_csv
from app.domain.models import ErpConnection, ErpSyncRun, Organization

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "odoo" / "purchase_orders.json").read_text())
ERP_KEY = base64.b64encode(b"e" * 32).decode()


@pytest.fixture(autouse=True)
def erp_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import os

    from app.core.settings import get_settings

    monkeypatch.setenv("ERP_ENCRYPTION_KEY", ERP_KEY)
    # Settings are read from the environment, and a test that never touches the database has not had
    # one set for it by the harness.
    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://x@x/x"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


class FakeOdoo:
    """An Odoo that answers from the captured fixture, and only to the calls we actually make."""

    def __init__(self, *, password: str = "the-api-key", fail: str | None = None) -> None:
        self.password = password
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def proxy(self, url: str, timeout: float = 0) -> Any:
        return _Common(self) if url.endswith("/common") else _Models(self)

    def maybe_fail(self) -> None:
        """The three ways the wire itself answers instead of Odoo, as the transport raises them."""
        if self.fail == "unreachable":
            raise OSError(111, "Connection refused")
        if self.fail == "timeout":
            raise TimeoutError("timed out")
        if self.fail == "redirected":
            # `xmlrpc` raises this on any non-200 — a proxy's error page, a login wall, and a
            # redirect, which is how a 302 to somewhere else never becomes a second request.
            raise xmlrpc.client.ProtocolError(
                "erp.example.test/xmlrpc/2/common", 302, "Found", {"Location": "http://169.254.169.254/"}
            )


class _Common:
    def __init__(self, odoo: FakeOdoo) -> None:
        self.odoo = odoo

    def version(self) -> dict[str, str]:
        self.odoo.maybe_fail()
        return {"server_version": FIXTURE["version"]}

    def authenticate(self, db: str, login: str, password: str, _context: dict[str, Any]) -> Any:
        self.odoo.maybe_fail()
        if self.odoo.fail == "no-database":
            raise xmlrpc.client.Fault(3, "Traceback...\npsycopg2.OperationalError: database does not exist")
        return 2 if password == self.odoo.password else False


class _Models:
    def __init__(self, odoo: FakeOdoo) -> None:
        self.odoo = odoo

    def execute_kw(
        self, db: str, uid: int, password: str, model: str, method: str, args: list[Any], kwargs: Any
    ) -> Any:
        self.odoo.calls.append((model, method))
        if self.odoo.fail == "protocol":
            raise xmlrpc.client.Fault(1, "Traceback...\nValueError: Invalid field 'nope' on model")
        match (model, method):
            case ("res.users", "read"):
                return [{"id": 2, "name": "Mitchell Admin", "company_id": [1, "YourCompany"]}]
            case ("purchase.order", "search_read"):
                orders = FIXTURE["orders"]
                return orders[: kwargs["limit"]] if kwargs.get("limit") else orders
            case ("purchase.order.line", "search_read"):
                return FIXTURE["lines"]
            case ("product.product", "fields_get"):
                return {"default_code": {"type": "char"}, "weight": {"type": "float"}}
            case ("product.product", "read"):
                return FIXTURE["products"]
            case ("uom.uom", "read"):
                return FIXTURE["uoms"]
        raise AssertionError(f"unexpected call {model}.{method}")  # pragma: no cover


def connector(odoo: FakeOdoo, *, api_key: str = "the-api-key") -> OdooConnector:
    return OdooConnector("https://erp.example.test", "fstest", "admin", api_key, proxy_factory=odoo.proxy)


# ---------------------------------------------------------------------------- connecting


def test_a_successful_connection_reports_what_it_found() -> None:
    identity = connector(FakeOdoo()).test_connection()
    assert identity.server_version.startswith("17.0")
    assert identity.database == "fstest"
    assert identity.user_id == 2
    assert identity.company == "YourCompany"


def test_a_wrong_key_is_not_an_exception_in_odoo_but_is_one_here() -> None:
    """`authenticate` answers False; a caller that does not check gets None back much later."""
    with pytest.raises(ErpAuthError) as raised:
        connector(FakeOdoo(), api_key="wrong").test_connection()
    assert "refused the credentials" in raised.value.message
    assert "API keys" in raised.value.message  # where to find one, since that is the usual mistake


def test_a_missing_database_says_what_odoo_said() -> None:
    with pytest.raises(ErpAuthError) as raised:
        connector(FakeOdoo(fail="no-database")).test_connection()
    assert "database does not exist" in raised.value.message  # the last line, not the traceback


def test_an_unreachable_server_is_not_an_authentication_problem() -> None:
    with pytest.raises(ErpUnavailable):
        connector(FakeOdoo(fail="unreachable")).test_connection()


def test_an_unreachable_server_reaches_the_screen_as_a_code_and_a_host() -> None:
    """What the socket says is ours to read, not theirs.

    `Odoo is unreachable at http://host.docker.internal:8169: [Errno 111] Connection refused` is
    what a prospect saw during a demo: our container's hostname, an errno, and English. The screen
    gets a code, the host, and a sentence it can say in its own language.
    """
    with pytest.raises(ErpUnavailable) as raised:
        connector(FakeOdoo(fail="unreachable")).test_connection()
    assert raised.value.code == "ERP_UNREACHABLE"
    assert raised.value.extra["params"] == {"host": "erp.example.test"}
    assert raised.value.message == "Odoo is unreachable at erp.example.test."
    assert "Errno" not in raised.value.message
    assert "https://" not in raised.value.message


def test_a_server_that_is_too_slow_says_so_rather_than_unreachable() -> None:
    """Not answering in time and nothing listening are two different days for a customer."""
    with pytest.raises(ErpUnavailable) as raised:
        connector(FakeOdoo(fail="timeout")).test_connection()
    assert raised.value.code == "ERP_TIMEOUT"
    assert raised.value.extra["params"] == {"host": "erp.example.test", "seconds": "60"}


def test_an_answer_that_is_not_odoo_is_its_own_code_and_carries_nothing_back() -> None:
    """A proxy's error page, a login wall or a redirect. The body — and where it pointed — stays
    out of the response: that is what turns an ERP form into a scanner with an oracle."""
    with pytest.raises(ErpUnavailable) as raised:
        connector(FakeOdoo(fail="redirected")).test_connection()
    assert raised.value.code == "ERP_BAD_RESPONSE"
    assert raised.value.extra["params"] == {"host": "erp.example.test"}
    assert "169.254" not in raised.value.message


def test_in_production_a_private_erp_url_never_reaches_a_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The URL is the customer's, and the network behind this process is not theirs to explore.

    Refused where the connection is opened rather than only where it is saved: a name saved last
    week can point at 127.0.0.1 today, and nothing between the two would have noticed.
    """
    from app.core.net import BlockedHost
    from app.core.settings import get_settings

    monkeypatch.setenv("APP_ENV", "prod")
    get_settings.cache_clear()
    odoo = FakeOdoo()
    probe = OdooConnector(
        "http://postgreseu.railway.internal:5432", "fstest", "admin", "k", proxy_factory=odoo.proxy
    )
    with pytest.raises(BlockedHost) as raised:
        probe.test_connection()
    assert raised.value.code == "URL_NOT_PUBLIC"
    assert odoo.calls == []  # nothing was dialled


def test_every_call_vets_the_address_again_not_only_the_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding is the whole reason: `xmlrpc` resolves on its own and hands us no address to
    pin, so the only defence is asking again each time a proxy is built."""
    from app.adapters.erp import odoo as odoo_module

    seen: list[str] = []

    def record(url: str) -> list[str]:
        seen.append(url)
        return []

    monkeypatch.setattr(odoo_module, "assert_public_host", record)
    connector(FakeOdoo()).test_connection()
    assert len(seen) >= 3  # version, authenticate, and the user read
    assert set(seen) == {"https://erp.example.test"}


def test_the_host_in_an_error_never_carries_the_credentials_typed_into_the_url() -> None:
    url = "https://admin:secret@erp.example.test"
    odoo = FakeOdoo(fail="unreachable")
    with pytest.raises(ErpUnavailable) as raised:
        OdooConnector(url, "fstest", "admin", "k", proxy_factory=odoo.proxy).test_connection()
    assert "secret" not in raised.value.message
    assert raised.value.extra["params"] == {"host": "erp.example.test"}


def test_a_renamed_field_surfaces_as_a_protocol_error() -> None:
    odoo = FakeOdoo()
    odoo.fail = "protocol"
    with pytest.raises(ErpProtocolError) as raised:
        connector(odoo).fetch_purchase_orders()
    assert "Invalid field" in raised.value.message


# ---------------------------------------------------------------------------- reading orders


def test_orders_and_lines_come_back_as_ours() -> None:
    orders = connector(FakeOdoo()).fetch_purchase_orders()
    assert [o.number for o in orders] == ["P00008", "P00009", "P00010"]
    order = next(o for o in orders if o.number == "P00009")
    assert order.supplier == "Gemini Furniture"
    assert order.currency == "USD"
    assert order.order_date is not None
    assert [line.sku for line in order.lines] == ["E-COM09", "E-COM06"]


def test_a_quantity_in_dozens_becomes_a_quantity_in_units() -> None:
    """The lesson from the real Odoo: `product_qty` is in the line's unit, the weight is per unit.

    Importing 20 where the customer means 240 would understate every weight-based allocation.
    """
    orders = connector(FakeOdoo()).fetch_purchase_orders()
    line = next(ln for o in orders for ln in o.lines if ln.sku == "E-COM09")
    assert line.uom == "Dozens"
    assert Decimal(line.quantity) == Decimal("240")
    assert Decimal(line.unit_price) == Decimal("41.6667")  # 500 per dozen
    # and the line total survives the conversion, to the cent it will be stored at
    assert abs(Decimal(line.quantity) * Decimal(line.unit_price) - Decimal("10000")) < Decimal("0.01")
    assert line.unit_weight_kg == "9.54"  # per unit, as the product carries it


def test_a_field_this_odoo_does_not_have_is_not_asked_for() -> None:
    """hs_code does not exist on product.product in Odoo 17 Community; asking would fail the sync."""
    odoo = FakeOdoo()
    orders = connector(odoo).fetch_purchase_orders()
    assert all(line.hs_code is None for order in orders for line in order.lines)
    assert ("product.product", "fields_get") in odoo.calls


# ---------------------------------------------------------------------------- the sealed credential


def test_the_api_key_is_sealed_and_comes_back() -> None:
    sealed = seal_key("the-api-key")
    assert b"the-api-key" not in sealed
    assert open_key(sealed) == "the-api-key"


def test_another_key_cannot_open_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import get_settings

    sealed = seal_key("the-api-key")
    monkeypatch.setenv("ERP_ENCRYPTION_KEY", base64.b64encode(b"z" * 32).decode())
    get_settings.cache_clear()
    with pytest.raises(CryptoError):
        open_key(sealed)


def test_without_a_key_the_deployment_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import get_settings

    monkeypatch.delenv("ERP_ENCRYPTION_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(CryptoError) as raised:
        seal_key("the-api-key")
    assert "ERP_ENCRYPTION_KEY" in str(raised.value)


# ---------------------------------------------------------------------------- the CSV bridge


def test_the_orders_are_written_in_our_own_import_columns() -> None:
    """The ERP goes through the same pipeline as a spreadsheet: one place for the rules."""
    orders = connector(FakeOdoo()).fetch_purchase_orders()
    content, lines = to_csv(orders)
    text_content = content.decode()
    assert lines == 5
    header = text_content.splitlines()[0]
    assert header.startswith("po_number,supplier_name,currency,order_date,line_no,sku")
    assert "P00009,Gemini Furniture,USD," in text_content
    assert "Large Desk (Dozens)" in text_content  # the unit stays legible to a human


# ---------------------------------------------------------------------------- through the API


def connect(client: TestClient, odoo: FakeOdoo, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from app.adapters.erp import odoo as odoo_module

    monkeypatch.setattr(odoo_module, "_default_proxy", odoo.proxy)
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": "https://erp.example.test/",
            "database": "fstest",
            "login": "admin",
            "api_key": "the-api-key",
        },
    )
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def test_connecting_tests_the_credentials_first(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = connect(client, FakeOdoo(), monkeypatch)
    assert body["server_version"].startswith("17.0")
    assert body["company"] == "YourCompany"
    assert "api_key" not in body  # never returned, in any form
    assert body["url"] == "https://erp.example.test"  # normalised

    stored = db.scalar(select(ErpConnection))
    assert stored is not None
    assert b"the-api-key" not in bytes(stored.api_key_sealed)


def test_the_address_is_vetted_before_the_form_dials_anything(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`POST /erp/connection` connects before it stores, on demand, as often as anyone asks — so
    the address is checked first, in the route, where the refusal names the field being filled in."""
    from app.api.v1 import erp as erp_router
    from app.core.net import BlockedHost

    def refuse(url: str) -> list[str]:
        raise BlockedHost("nope", params={"host": url, "reason": "private"})

    monkeypatch.setattr(erp_router, "assert_public_host", refuse)
    odoo = FakeOdoo()
    monkeypatch.setattr("app.adapters.erp.odoo._default_proxy", odoo.proxy)
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": "http://10.0.0.5:8069",
            "database": "fstest",
            "login": "admin",
            "api_key": "the-api-key",
        },
    )
    assert res.status_code == 422
    assert res.json()["code"] == "URL_NOT_PUBLIC"
    assert odoo.calls == []
    assert list(db.scalars(select(ErpConnection))) == []


def test_bad_credentials_are_refused_before_anything_is_stored(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved connection that has never authenticated is a promise the next sync will break."""
    from app.adapters.erp import odoo as odoo_module

    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(password="something-else").proxy)
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": "https://erp.example.test",
            "database": "fstest",
            "login": "admin",
            "api_key": "the-api-key",
        },
    )
    assert res.status_code == 422
    assert res.json()["code"] == "ERP_AUTH_FAILED"
    assert list(db.scalars(select(ErpConnection))) == []


def test_one_connection_per_organization(
    client: TestClient, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(client, FakeOdoo(), monkeypatch)
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": "https://erp.example.test",
            "database": "other",
            "login": "admin",
            "api_key": "k",
        },
    )
    assert res.status_code == 409
    assert res.json()["code"] == "ERP_ALREADY_CONNECTED"


def test_a_sync_imports_the_orders_and_is_idempotent(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(client, FakeOdoo(), monkeypatch)

    first = client.post("/api/v1/erp/sync")
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "SUCCEEDED"
    assert first.json()["purchase_orders"] == 3
    assert first.json()["lines"] == 5

    orders = client.get("/api/v1/purchase-orders").json()
    assert sorted(o["po_number"] for o in orders) == ["P00008", "P00009", "P00010"]

    # the same orders again: updated in place, never duplicated
    second = client.post("/api/v1/erp/sync")
    assert second.status_code == 202
    assert len(client.get("/api/v1/purchase-orders").json()) == 3

    runs = client.get("/api/v1/erp/sync-runs").json()
    assert len(runs) == 2
    assert all(run["status"] == "SUCCEEDED" for run in runs)


def test_a_failed_sync_leaves_a_run_that_says_why(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync that fails silently is a customer who believes their orders are up to date."""
    connect(client, FakeOdoo(), monkeypatch)
    from app.adapters.erp import odoo as odoo_module

    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="protocol").proxy)
    res = client.post("/api/v1/erp/sync")
    assert res.status_code == 502

    runs = client.get("/api/v1/erp/sync-runs").json()
    assert runs[0]["status"] == "FAILED"
    assert "Invalid field" in runs[0]["error"]
    assert client.get("/api/v1/erp/connection").json()["last_error"]


def test_forgetting_the_erp_keeps_the_orders(
    client: TestClient, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(client, FakeOdoo(), monkeypatch)
    client.post("/api/v1/erp/sync")
    assert client.delete("/api/v1/erp/connection").status_code == 204
    assert client.get("/api/v1/erp/connection").status_code == 404
    assert len(client.get("/api/v1/purchase-orders").json()) == 3  # they are ours now


def test_erp_rows_are_invisible_to_another_organization(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect(client, FakeOdoo(), monkeypatch)
    client.post("/api/v1/erp/sync")
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert len(list(db.scalars(select(ErpConnection)))) == 1
        assert len(list(db.scalars(select(ErpSyncRun)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(ErpConnection))) == []
        assert list(db.scalars(select(ErpSyncRun))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)


# ---------------------------------------------------------------------------- pagination


class PagedOdoo(FakeOdoo):
    """An Odoo with more orders than one page, answering the id cursor honestly."""

    def __init__(self, count: int = 450) -> None:
        super().__init__()
        self.orders = [
            {
                "id": index,
                "name": f"P{index:05d}",
                "partner_id": [9, "Wood Corner"],
                "currency_id": [1, "USD"],
                "date_order": "2026-09-04 10:47:02",
                "state": "purchase",
                "order_line": [],
            }
            for index in range(1, count + 1)
        ]
        self.pages: list[tuple[int, int]] = []

    def proxy(self, url: str, timeout: float = 0) -> Any:
        return _Common(self) if url.endswith("/common") else _PagedModels(self)


class _PagedModels(_Models):
    def execute_kw(
        self, db: str, uid: int, password: str, model: str, method: str, args: list[Any], kwargs: Any
    ) -> Any:
        if (model, method) == ("purchase.order", "search_read"):
            after = next((int(clause[2]) for clause in args[0] if clause[0] == "id" and clause[1] == ">"), 0)
            limit = kwargs["limit"]
            self.odoo.pages.append((after, limit))  # type: ignore[attr-defined]
            page = [o for o in self.odoo.orders if o["id"] > after][:limit]  # type: ignore[attr-defined]
            return page
        return super().execute_kw(db, uid, password, model, method, args, kwargs)


def test_orders_are_read_in_pages_with_a_cursor_on_the_id() -> None:
    """An offset would skip whatever the customer's staff confirm while the sync is running."""
    odoo = PagedOdoo(count=450)
    orders = connector(odoo).fetch_purchase_orders()

    assert len(orders) == 450
    assert [after for after, _ in odoo.pages] == [0, 200, 400]  # a cursor, never an offset
    assert {limit for _, limit in odoo.pages} == {200}


def test_a_limit_is_respected_across_pages() -> None:
    odoo = PagedOdoo(count=450)
    assert len(connector(odoo).fetch_purchase_orders(limit=250)) == 250
    assert [limit for _, limit in odoo.pages] == [200, 50]


# ---------------------------------------------------------------------------- cancellations


class CancellingOdoo(FakeOdoo):
    """The same orders, with P00009 cancelled upstream."""

    def proxy(self, url: str, timeout: float = 0) -> Any:
        return _Common(self) if url.endswith("/common") else _CancellingModels(self)


class _CancellingModels(_Models):
    def execute_kw(
        self, db: str, uid: int, password: str, model: str, method: str, args: list[Any], kwargs: Any
    ) -> Any:
        if (model, method) == ("purchase.order", "search_read"):
            orders = [dict(order) for order in FIXTURE["orders"]]
            for order in orders:
                if order["name"] == "P00009":
                    order["state"] = "cancel"
            return orders
        return super().execute_kw(db, uid, password, model, method, args, kwargs)


def test_an_order_cancelled_upstream_is_marked_never_deleted(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It may already carry costs and containers here; deleting it would rewrite a landed cost."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    assert client.post("/api/v1/erp/sync").status_code == 202
    before = {po["po_number"]: po for po in client.get("/api/v1/purchase-orders").json()}
    assert sorted(before) == ["P00008", "P00009", "P00010"]

    monkeypatch.setattr(odoo_module, "_default_proxy", CancellingOdoo().proxy)
    second = client.post("/api/v1/erp/sync")
    assert second.status_code == 202, second.text
    assert second.json()["detail"]["cancelled"] == 1

    after = {po["po_number"]: po for po in client.get("/api/v1/purchase-orders").json()}
    assert sorted(after) == ["P00008", "P00009", "P00010"]  # still there
    cancelled = client.get(f"/api/v1/purchase-orders/{before['P00009']['id']}").json()
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancelled_at"] is not None
    assert client.get(f"/api/v1/purchase-orders/{before['P00008']['id']}").json()["status"] == "OPEN"


def test_an_order_cancelled_before_we_ever_saw_it_is_not_created(
    client: TestClient, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An order we have never seen, already cancelled, has nothing to say to us."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", CancellingOdoo().proxy)
    assert client.post("/api/v1/erp/sync").status_code == 202
    assert sorted(po["po_number"] for po in client.get("/api/v1/purchase-orders").json()) == [
        "P00008",
        "P00010",
    ]


# ---------------------------------------------------------------------------- incremental sync


class RecordingOdoo(FakeOdoo):
    """Remembers the domains it was asked for, so a test can see what was re-read."""

    def __init__(self) -> None:
        super().__init__()
        self.domains: list[list[Any]] = []

    def proxy(self, url: str, timeout: float = 0) -> Any:
        return _Common(self) if url.endswith("/common") else _RecordingModels(self)


class _RecordingModels(_Models):
    def execute_kw(
        self, db: str, uid: int, password: str, model: str, method: str, args: list[Any], kwargs: Any
    ) -> Any:
        if (model, method) == ("purchase.order", "search_read"):
            self.odoo.domains.append(args[0])  # type: ignore[attr-defined]
        return super().execute_kw(db, uid, password, model, method, args, kwargs)


def write_date_filter(domain: list[Any]) -> str | None:
    return next((clause[2] for clause in domain if clause[0] == "write_date"), None)


def test_the_second_sync_only_asks_for_what_changed(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-reading a customer's whole history every night is how an integration becomes the thing
    their IT department switches off."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    odoo = RecordingOdoo()
    monkeypatch.setattr(odoo_module, "_default_proxy", odoo.proxy)

    assert client.post("/api/v1/erp/sync").status_code == 202
    assert write_date_filter(odoo.domains[0]) is None  # the first sync reads everything

    second = client.post("/api/v1/erp/sync")
    assert second.status_code == 202
    watermark = write_date_filter(odoo.domains[-1])
    assert watermark is not None, odoo.domains
    assert second.json()["detail"]["since"] is not None


def test_the_watermark_looks_back_a_little_further_than_the_last_sync(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ERP's clock is not ours and a write can land a second before the read that missed it.
    Five minutes of overlap costs one request; a missed order costs a wrong landed cost."""
    from app.domain.erp.service import SYNC_OVERLAP, watermark
    from app.domain.models import ErpConnection

    connect(client, FakeOdoo(), monkeypatch)
    connection = db.scalar(select(ErpConnection))
    assert connection is not None

    assert watermark(connection) is None  # never synced: everything
    synced_at = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    connection.last_sync_at = synced_at
    assert watermark(connection) == synced_at - SYNC_OVERLAP
    assert watermark(connection, full=True) is None  # the "start again" button


def test_a_full_sync_re_reads_everything(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    odoo = RecordingOdoo()
    monkeypatch.setattr(odoo_module, "_default_proxy", odoo.proxy)
    client.post("/api/v1/erp/sync")

    forced = client.post("/api/v1/erp/sync", params={"full": True})
    assert forced.status_code == 202
    assert write_date_filter(odoo.domains[-1]) is None
    assert forced.json()["detail"]["since"] is None
