"""What the scheduled ERP read does when a customer's Odoo is not answering.

The failure mode this guards against is not a wrong number, it is a worker that never comes back:
the daily sweep holds a queueing lock, so one customer's silent server would stop every other
customer's sync with it.
"""

from __future__ import annotations

import threading
import time
import uuid
import xmlrpc.client
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_erp_odoo import FakeOdoo, connect
from test_erp_odoo import erp_key as erp_key
from test_erp_odoo import org as org

from app.domain.erp import service as erp_service
from app.domain.models import ErpConnection, ErpSyncRun, ErpSyncStatus, Organization
from app.jobs import handlers

NOW = datetime(2026, 9, 12, 5, 15, tzinfo=UTC)


# ---------------------------------------------------------------------------- the timeout


def test_an_erp_that_never_answers_does_not_hold_the_worker() -> None:
    """198.51.100.0/24 is TEST-NET-2: it routes nowhere and drops packets rather than refusing
    them, which is exactly how a customer's server behaves when its firewall changes."""
    from app.adapters.erp.odoo import OdooConnector

    connector = OdooConnector("http://198.51.100.7:8069", "fstest", "admin", "key", timeout=2)
    started = time.monotonic()
    outcome: list[str] = []

    def probe() -> None:
        try:
            connector.test_connection()
        except Exception as exc:
            outcome.append(f"{type(exc).__name__}: {exc}")

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout=20)

    assert not thread.is_alive(), "the call was still blocked: ServerProxy has no timeout of its own"
    assert time.monotonic() - started < 20
    # A server that drops packets is its own answer — "it did not reply in time", not "nothing is
    # listening" — and it carries its own code, so the screen can say which of the two happened.
    assert outcome and "ErpTimeout" in outcome[0]
    # and the message is one a person can act on, naming the host and what happened
    assert "198.51.100.7" in outcome[0]
    assert "did not answer within" in outcome[0]


def test_the_timeout_comes_from_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import get_settings
    from app.domain.erp import service

    seen: list[float] = []

    def factory(url: str, timeout: float) -> Any:
        seen.append(timeout)
        return FakeOdoo().proxy(url)

    monkeypatch.setenv("ERP_TIMEOUT_SECONDS", "7")
    get_settings.cache_clear()
    try:
        connection = ErpConnection(
            org_id=uuid.uuid4(),
            kind=__import__("app.domain.models", fromlist=["ErpKind"]).ErpKind.ODOO,
            url="https://erp.example.test",
            database="fstest",
            login="admin",
            api_key_sealed=service.seal_key("the-api-key"),
        )
        connector = service.connector_for(connection)
        connector._proxy_factory = factory  # type: ignore[attr-defined]
        connector.test_connection()
    finally:
        get_settings.cache_clear()
    # every proxy this connector builds carries the ceiling, not just the first one
    assert seen and set(seen) == {7.0}


# ---------------------------------------------------------------------------- the backoff


def test_the_wait_doubles_and_then_stops_growing() -> None:
    assert erp_service.backoff_for(0) == timedelta(0)
    assert erp_service.backoff_for(1) == timedelta(minutes=15)
    assert erp_service.backoff_for(2) == timedelta(minutes=30)
    assert erp_service.backoff_for(3) == timedelta(hours=1)
    assert erp_service.backoff_for(50) == timedelta(days=1)  # capped, and no overflow


def test_a_failing_erp_is_left_alone_until_its_wait_is_over(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A customer whose server has been down for a week should not be dialled every morning to
    write the same failure row again."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="unreachable").proxy)

    assert handlers.sync_erp(db, now=NOW) == 0
    db.expire_all()
    connection = db.scalar(select(ErpConnection))
    assert connection is not None
    assert connection.consecutive_failures == 1
    assert connection.retry_after == NOW + timedelta(minutes=15)
    # the reason is the ERP's own words, not "see the application logs"
    assert connection.last_error and "unreachable" in connection.last_error.lower()

    # ten minutes later the schedule does not even try
    before = len(list(db.scalars(select(ErpSyncRun))))
    assert handlers.sync_erp(db, now=NOW + timedelta(minutes=10)) == 0
    db.expire_all()
    assert len(list(db.scalars(select(ErpSyncRun)))) == before
    assert db.scalar(select(ErpConnection)).consecutive_failures == 1  # type: ignore[union-attr]

    # once the wait is over it tries again, and the wait doubles
    assert handlers.sync_erp(db, now=NOW + timedelta(minutes=20)) == 0
    db.expire_all()
    connection = db.scalar(select(ErpConnection))
    assert connection is not None
    assert connection.consecutive_failures == 2
    assert connection.retry_after == NOW + timedelta(minutes=20) + timedelta(minutes=30)


def test_a_read_that_works_again_clears_the_slate(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="unreachable").proxy)
    handlers.sync_erp(db, now=NOW)
    db.expire_all()
    assert db.scalar(select(ErpConnection)).consecutive_failures == 1  # type: ignore[union-attr]

    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo().proxy)
    assert handlers.sync_erp(db, now=NOW + timedelta(hours=1)) == 1
    db.expire_all()
    connection = db.scalar(select(ErpConnection))
    assert connection is not None
    assert connection.consecutive_failures == 0
    assert connection.retry_after is None
    assert connection.last_error is None
    assert connection.last_sync_at is not None


def test_pressing_the_button_is_never_held_back(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backoff protects the schedule from a pointless round trip. A person waiting in front of
    the screen has just asked, and is owed the answer — even if it is the same failure."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="unreachable").proxy)
    handlers.sync_erp(db, now=NOW)
    db.expire_all()

    # the schedule holds off...
    assert handlers.sync_erp(db, now=NOW + timedelta(minutes=1)) == 0
    # ...but asking for this organization by name does not
    before = len(list(db.scalars(select(ErpSyncRun))))
    org_row = db.scalar(select(Organization))
    assert org_row is not None
    handlers.sync_erp(db, org_row.id, now=NOW + timedelta(minutes=1))
    db.expire_all()
    assert len(list(db.scalars(select(ErpSyncRun)))) == before + 1

    # and so does the endpoint
    res = client.post("/api/v1/erp/sync")
    assert res.status_code in (202, 502), res.text


def test_one_unreachable_customer_does_not_stop_the_others(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the per-organization transaction: the sweep is not all-or-nothing."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    other = client.get("/api/v1/organization", headers={"X-Org-Id": str(uuid.uuid4())}).json()
    res = client.post(
        "/api/v1/erp/connection",
        headers={"X-Org-Id": other["id"]},
        json={
            "kind": "ODOO",
            "url": "https://other.example.test",
            "database": "fstest",
            "login": "admin",
            "api_key": "the-api-key",
        },
    )
    assert res.status_code == 201, res.text

    broken = FakeOdoo(fail="unreachable")
    healthy = FakeOdoo()

    def route(url: str, timeout: float = 0) -> Any:
        return broken.proxy(url) if url.startswith("https://erp.example.test") else healthy.proxy(url)

    monkeypatch.setattr(odoo_module, "_default_proxy", route)
    assert handlers.sync_erp(db, now=NOW) == 1  # the healthy one still ran

    db.expire_all()
    states = {c.url: c.consecutive_failures for c in db.scalars(select(ErpConnection))}
    assert states == {"https://erp.example.test": 1, "https://other.example.test": 0}


def test_the_failure_reaches_the_screen(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Everything above is worth nothing if the customer cannot see it."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="unreachable").proxy)
    handlers.sync_erp(db, now=NOW)
    db.commit()

    shown = client.get("/api/v1/erp/connection").json()
    assert shown["last_error"] and "see the application logs" not in shown["last_error"]
    assert "unreachable" in shown["last_error"].lower()
    assert shown["consecutive_failures"] == 1
    assert shown["retry_after"] is not None

    runs = client.get("/api/v1/erp/sync-runs").json()
    assert runs[0]["status"] == ErpSyncStatus.FAILED.value
    assert "unreachable" in runs[0]["error"].lower()


def test_a_fault_from_odoo_is_reported_in_odoo_s_own_words(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)
    monkeypatch.setattr(odoo_module, "_default_proxy", FakeOdoo(fail="protocol").proxy)
    handlers.sync_erp(db, now=NOW)
    db.commit()

    shown = client.get("/api/v1/erp/connection").json()
    assert "Invalid field 'nope' on model" in shown["last_error"]


def test_the_credential_is_never_in_the_message(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever goes wrong, what is written down must not be the key to someone's accounting."""
    from app.adapters.erp import odoo as odoo_module

    connect(client, FakeOdoo(), monkeypatch)

    def leaky(url: str, timeout: float = 0) -> Any:
        raise xmlrpc.client.Fault(1, "Traceback...\nRuntimeError: login failed for admin/the-api-key")

    monkeypatch.setattr(odoo_module, "_default_proxy", leaky)
    handlers.sync_erp(db, now=NOW)
    db.commit()

    shown = client.get("/api/v1/erp/connection").json()
    assert "the-api-key" not in (shown["last_error"] or "")
    runs = client.get("/api/v1/erp/sync-runs").json()
    assert "the-api-key" not in (runs[0]["error"] or "")


def test_a_url_carrying_a_password_is_refused(client: TestClient, db: Session, org: Organization) -> None:
    """The url column is stored in clear and returned by the API — unlike the key, which is sealed."""
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": "https://admin:s3cret@erp.example.test",
            "database": "fstest",
            "login": "admin",
            "api_key": "the-api-key",
        },
    )
    assert res.status_code == 422
    assert "must not carry a login" in res.text
    assert list(db.scalars(select(ErpConnection))) == []


def test_the_url_refusals_carry_a_code_the_screen_can_translate(
    client: TestClient, org: Organization
) -> None:
    """A bare ValueError reaches the client as 'value_error' and an English sentence. These two are
    read in a form, by someone who may not be reading English."""
    for url, code in [
        ("https://admin:s3cret@erp.example.test", "ERP_URL_HAS_CREDENTIALS"),
        ("ftp://erp.example.test", "ERP_URL_SCHEME"),
    ]:
        res = client.post(
            "/api/v1/erp/connection",
            json={
                "kind": "ODOO",
                "url": url,
                "database": "fstest",
                "login": "admin",
                "api_key": "the-api-key",
            },
        )
        assert res.status_code == 422, res.text
        (error,) = res.json()["errors"]
        assert error["code"] == code
        assert error["field"] == "url"
        assert error["message"]  # the English sentence stays, as the fallback
