"""Two requests on one container at the same moment, each on its own connection, as two tabs or two
people would send them. Each one plans from what the database says before the other has committed,
so without a turn of their own both write — and in the ERP that is the same charge on the goods twice.

These tests commit for real: a lock that only the outer test transaction could see proves nothing.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
from conftest import fake_fetcher
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from test_erp_landed_cost import (
    WritableOdoo,
    connect,
    erp_key,  # noqa: F401 - the ERP's encryption key, for every test here
    seed_container,
)

from app.domain.models import AuditLog, ErpPush
from app.ops.erase import erase, find

#: How long the first request waits for the second before it goes on alone. Only reached when the
#: second request neither arrives nor waits for a lock, which is a failure of the test, not a result.
PATIENCE = 10.0


class HoldingOdoo(WritableOdoo):
    """An Odoo that holds the first request at one call until the second request has either reached
    the same call or is waiting for a lock in the database. Either way both requests are then in
    flight together, which is the moment the race needs and a sequential test never produces."""

    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        #: The call to hold at, set once the scene is in place.
        self.call: tuple[str, str] | None = None
        self.arrivals = 0
        self.second = threading.Event()
        self.guard = threading.Lock()

    def proxy(self, url: str, timeout: float = 0) -> Any:
        inner = super().proxy(url, timeout)
        return inner if url.endswith("/common") else _Holding(self, inner)

    def hold(self) -> None:
        with self.guard:
            self.arrivals += 1
            first = self.arrivals == 1
        if not first:
            self.second.set()
            return
        deadline = time.monotonic() + PATIENCE
        while not self.second.wait(0.05) and time.monotonic() < deadline:
            if self.someone_waits_for_a_lock():
                return

    def someone_waits_for_a_lock(self) -> bool:
        with self.engine.connect() as conn:
            waiting = conn.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            ).scalar_one()
        return bool(waiting)


class _Holding:
    def __init__(self, odoo: HoldingOdoo, inner: Any) -> None:
        self.odoo = odoo
        self.inner = inner

    def execute_kw(
        self, db: str, uid: int, password: str, model: str, method: str, args: list[Any], kwargs: Any
    ) -> Any:
        if (model, method) == self.odoo.call:
            self.odoo.hold()
        return self.inner.execute_kw(db, uid, password, model, method, args, kwargs)


@pytest.fixture
def committed(engine: Engine) -> Iterator[tuple[TestClient, uuid.UUID]]:
    """A client whose every request gets a session of its own, which commits, as in production."""
    from app.api.v1.deps import get_fx_service
    from app.core.db import get_db
    from app.domain.fx.service import FxService
    from app.main import app

    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def own_session() -> Iterator[Session]:
        with sessions() as session:
            yield session

    org_id = uuid.uuid4()
    app.dependency_overrides[get_db] = own_session
    app.dependency_overrides[get_fx_service] = lambda: FxService(fake_fetcher)
    try:
        with TestClient(app, headers={"X-Org-Id": str(org_id)}) as client:
            assert client.get("/api/v1/organization").status_code == 200
            yield client, org_id
    finally:
        app.dependency_overrides.clear()
        with engine.connect() as conn, conn.begin():
            if conn.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": org_id}).first():
                found = find(conn, str(org_id))
                erase(conn, found, confirm=found.name)


def together(*requests: Callable[[], httpx.Response]) -> list[httpx.Response]:
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        return [future.result() for future in [pool.submit(request) for request in requests]]


def test_two_pushes_at_once_write_one_document(
    committed: tuple[TestClient, uuid.UUID], engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A double click, or two people on one container: the second push waits for the first, then
    finds its costs carried and answers with that push. One document in the ERP, one row here."""
    client, org_id = committed
    odoo = HoldingOdoo(engine)
    connect(client, odoo, monkeypatch)
    container_id = seed_container(client)
    odoo.call = ("stock.landed.cost", "create")

    url = f"/api/v1/containers/{container_id}/erp/landed-cost"
    first, second = together(lambda: client.post(url), lambda: client.post(url))

    assert (first.status_code, second.status_code) == (201, 201), (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert len(odoo.created) == 1
    with Session(engine) as session:
        assert len(list(session.scalars(select(ErpPush).where(ErpPush.org_id == org_id)))) == 1
        pushed = session.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "erp.landed_cost_pushed")
        )
        assert len(list(pushed)) == 1  # the second wrote nothing, and the history does not say it did


def test_two_forgets_at_once_let_go_of_the_push_once(
    committed: tuple[TestClient, uuid.UUID], engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The validated document is let go of in two tabs at once: the second finds it already let go
    of, rather than writing a second release over the first with its own reason."""
    client, org_id = committed
    odoo = HoldingOdoo(engine)
    connect(client, odoo, monkeypatch)
    container_id = seed_container(client)
    pushed = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost")
    assert pushed.status_code == 201, pushed.text
    odoo.documents[pushed.json()["odoo_id"]]["state"] = "done"
    odoo.call = ("stock.landed.cost", "read")

    url = f"/api/v1/containers/{container_id}/erp/pushes/{pushed.json()['id']}"
    first, second = together(
        lambda: client.delete(url, params={"reason": "facture corrigée"}),
        lambda: client.delete(url, params={"reason": "erreur de saisie"}),
    )

    statuses = sorted((res.status_code, res.json().get("code")) for res in (first, second))
    assert statuses == [(200, None), (409, "ERP_PUSH_ALREADY_FORGOTTEN")], (first.text, second.text)
    with Session(engine) as session:
        released = session.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == "erp.push_forgotten")
        )
        assert len(list(released)) == 1
