"""Test harness: a real Postgres (TEST_DATABASE_URL, else a throwaway container), migrations applied once,
each test wrapped in an outer transaction that is rolled back. FX lookups use a fake fetcher."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("CORS_ORIGINS", '["http://localhost:3000"]')
# The suite authenticates with the `X-Org-Id` dev header, which is now an explicit opt-in rather
# than something a non-prod `APP_ENV` grants on its own. Set here, so a test that wants to prove the
# header is refused only has to turn it off.
os.environ.setdefault("ALLOW_DEV_PRINCIPAL", "true")
# Same rule for outbound calls: the tests use made-up ERP hosts that must not be resolved.
os.environ.setdefault("ALLOW_PRIVATE_OUTBOUND", "true")

FAKE_RATES: dict[tuple[str, str], Decimal] = {
    ("EUR", "USD"): Decimal("0.92"),  # 1 USD = 0.92 EUR
    ("USD", "EUR"): Decimal("1.0850"),  # 1 EUR = 1.085 USD
    ("EUR", "CNY"): Decimal("0.128"),
}


def fake_fetcher(base: str, quote: str, on_date: date) -> tuple[Decimal, date] | None:
    rate = FAKE_RATES.get((base, quote))
    return None if rate is None else (rate, on_date)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        yield url
        return
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # older testcontainers
        from testcontainers.postgres import PostgresContainer

    # The same major as production (Amsterdam runs 18.6): a test suite on another major is
    # a test suite that can bless a migration production will refuse.
    with PostgresContainer("postgres:18", driver="psycopg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    os.environ["DATABASE_URL"] = database_url
    here = os.path.dirname(__file__)
    cfg = AlembicConfig(os.path.join(here, "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(here, "..", "migrations"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(database_url)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    outer = connection.begin()
    session = sessionmaker(
        bind=connection, autoflush=False, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess: Session, trans):  # type: ignore[no-untyped-def]
        if trans.nested and not trans._parent.nested and not sess.in_transaction():
            sess.begin_nested()

    session.begin_nested()
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def client(db: Session, engine: Engine, org_id: uuid.UUID) -> Iterator[TestClient]:
    from app.api.v1.deps import get_fx_service
    from app.core.db import get_db
    from app.domain.fx.service import FxService
    from app.main import app

    def fresh_db():  # type: ignore[no-untyped-def]
        db.expire_all()  # each request starts from the database, like a per-request session
        yield db

    app.dependency_overrides[get_db] = fresh_db
    app.dependency_overrides[get_fx_service] = lambda: FxService(fake_fetcher)
    with TestClient(app, headers={"X-Org-Id": str(org_id)}) as c:
        yield c
    app.dependency_overrides.clear()
