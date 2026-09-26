"""The three things that were unobservable: a dead worker, a database role that bypasses RLS, and a
backup that stopped being taken.

All three share a shape — nothing fails, nothing throws, every screen goes on showing the numbers it
last computed — which is why each of them needs something that actively goes and looks.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters.s3 import S3Object
from app.jobs import handlers

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------- the worker's pulse


def test_the_worker_probe_is_separate_from_the_api_one(client: TestClient, db: Session) -> None:
    """Railway uses `/readyz` as the deploy healthcheck, so a dead worker must not fail it: the day
    the worker dies is the day you most need to be able to deploy the fix. The worker gets its own
    endpoint, for an external uptime probe, and `/readyz` only reports what it knows."""
    ready = client.get("/readyz")
    assert ready.status_code == 200
    assert "worker_seen_at" in ready.json()

    stale = client.get("/healthz/worker")
    assert stale.status_code == 503  # nothing has ever beaten in this database
    assert stale.json()["status"] == "stale"


def test_a_beating_worker_answers_200(client: TestClient, db: Session) -> None:
    handlers.heartbeat(db, now=datetime.now(UTC))
    alive = client.get("/healthz/worker")
    assert alive.status_code == 200
    assert alive.json()["status"] == "ok"


def test_a_worker_that_stopped_a_quarter_of_an_hour_ago_is_reported_dead(
    client: TestClient, db: Session
) -> None:
    handlers.heartbeat(db, now=datetime.now(UTC) - timedelta(minutes=20))
    dead = client.get("/healthz/worker")
    assert dead.status_code == 503
    assert dead.json()["seconds_since"] >= 15 * 60


def test_the_heartbeat_keeps_one_row_however_often_it_beats(db: Session) -> None:
    handlers.heartbeat(db, now=NOW)
    handlers.heartbeat(db, now=NOW + timedelta(minutes=5))
    assert handlers.last_job_run(db, handlers.WORKER_HEARTBEAT) == NOW + timedelta(minutes=5)


# ---------------------------------------------------------------------------- the database role


def test_a_superuser_connection_refuses_to_be_ready(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row Level Security is the whole tenant boundary, and a superuser ignores
    `FORCE ROW LEVEL SECURITY` in complete silence: every query works, every screen renders, and the
    isolation is simply gone. `DATABASE_URL` and `MIGRATIONS_DATABASE_URL` sit side by side on the
    same Railway service, so it is one copy-paste away."""
    import app.main as main

    monkeypatch.setattr(main, "_role_check", lambda conn, settings: "the application is a superuser")
    refused = client.get("/readyz")
    assert refused.status_code == 503
    assert refused.json()["code"] == "DB_ROLE_BYPASSES_RLS"


def test_outside_production_the_role_is_not_questioned(client: TestClient) -> None:
    """Locally and in tests the owner role is the only one there is, and refusing to start would
    mean nobody could run the application at all."""
    assert client.get("/readyz").status_code == 200


def test_in_production_the_catalogue_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.settings import Settings
    from app.main import _role_check

    class FakeRow:
        def __init__(self, *values: bool) -> None:
            self.values = values

        def __getitem__(self, index: int) -> bool:
            return self.values[index]

    class FakeConn:
        def __init__(self, row: FakeRow) -> None:
            self.row = row

        def execute(self, *_: object, **__: object) -> FakeConn:
            return self

        def first(self) -> FakeRow:
            return self.row

    prod = Settings(app_env="prod", database_url="postgresql+psycopg://x/y")
    ask = cast(Any, _role_check)
    assert ask(FakeConn(FakeRow(False, False)), prod) is None
    assert "superuser" in (ask(FakeConn(FakeRow(True, False)), prod) or "")
    assert "BYPASSRLS" in (ask(FakeConn(FakeRow(False, True)), prod) or "")


# ---------------------------------------------------------------------------- the bucket


class FakeBucket:
    def __init__(self, objects: list[S3Object]) -> None:
        self.objects = objects

    def list(self, prefix: str) -> list[S3Object]:
        return [obj for obj in self.objects if obj.key.startswith(prefix)]


def backup(day: str, size: int) -> S3Object:
    return S3Object(key=f"backups/prod/{day}T0300.dump.enc", size=size, last_modified="")


ENV = {
    "SCW_ACCESS_KEY": "k",
    "SCW_SECRET_KEY": "s",
    "SCW_BUCKET": "b",
    "APP_ENV": "prod",
}


def test_no_bucket_configured_is_not_an_incident() -> None:
    """A development machine has no bucket, and a daily error about it would teach people to ignore
    the one that matters."""
    assert handlers.check_backup_freshness(now=NOW, env={}, client=None) == "skipped"


def test_a_fresh_backup_says_nothing() -> None:
    bucket = FakeBucket([backup("2026-09-16", 900_000), backup("2026-09-17", 910_000)])
    assert handlers.check_backup_freshness(now=NOW, env=ENV, client=bucket) == "ok"


def test_a_backup_that_stopped_being_taken_is_an_error(caplog: pytest.LogCaptureFixture) -> None:
    """The failure mode the nightly job cannot report: it does not fail, it stops running. A removed
    credential, a cron that was never re-created after a service restore."""
    bucket = FakeBucket([backup("2026-09-10", 900_000)])
    with caplog.at_level("ERROR"):
        assert handlers.check_backup_freshness(now=NOW, env=ENV, client=bucket) == "stale"
    assert "older than the window" in caplog.text


def test_a_backup_that_suddenly_holds_a_third_less_is_an_error() -> None:
    """A dump taken against the wrong database, or a `pg_dump` that stopped early and exited zero.
    It restores cleanly, which is what makes it the worst kind of backup."""
    bucket = FakeBucket([backup("2026-09-16", 900_000), backup("2026-09-17", 400_000)])
    assert handlers.check_backup_freshness(now=NOW, env=ENV, client=bucket) == "shrunk"


def test_an_empty_bucket_is_an_error() -> None:
    assert handlers.check_backup_freshness(now=NOW, env=ENV, client=FakeBucket([])) == "missing"
