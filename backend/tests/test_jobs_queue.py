"""The queue itself: enqueueing is part of the caller's transaction, and a lock means one job at a time."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs.app import defer, get_app, psycopg_dsn


@pytest.fixture(scope="session")
def noop_task():  # type: ignore[no-untyped-def]
    """A task that exists only so that something can be deferred."""

    @get_app().task(name="tests.noop")
    def noop(**kwargs: object) -> None:  # pragma: no cover - never executed in tests
        return None

    return noop


def queued(db: Session, task_name: str = "tests.noop") -> list[dict[str, object]]:
    rows = db.execute(
        text("SELECT id, task_name, lock, queueing_lock, args FROM procrastinate_jobs WHERE task_name = :t"),
        {"t": task_name},
    ).mappings()
    return [dict(r) for r in rows]


def test_the_driver_name_is_stripped_for_psycopg() -> None:
    assert psycopg_dsn("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"


def test_a_deferred_job_is_visible_in_the_same_transaction(db: Session, noop_task) -> None:  # type: ignore[no-untyped-def]
    job_id = defer(db, noop_task, lock="container:1", container_id="c-1")
    assert job_id is not None
    (job,) = queued(db)
    assert job["lock"] == "container:1"
    assert job["args"] == {"container_id": "c-1"}


def test_a_rolled_back_transaction_leaves_no_job(db: Session, noop_task) -> None:  # type: ignore[no-untyped-def]
    """The reason for a Postgres queue: no job can survive the write that justified it."""
    savepoint = db.begin_nested()
    defer(db, noop_task, lock="container:2", container_id="c-2")
    assert len(queued(db)) == 1
    savepoint.rollback()
    assert queued(db) == []


def test_the_same_lock_is_not_queued_twice(db: Session, noop_task) -> None:  # type: ignore[no-untyped-def]
    """`lock` doubles as the queueing lock: one pending job per key, which is idempotence for free."""
    first = defer(db, noop_task, lock="container:3", container_id="c-3")
    second = defer(db, noop_task, lock="container:3", container_id="c-3")
    assert first is not None
    assert second is None
    assert len(queued(db)) == 1
