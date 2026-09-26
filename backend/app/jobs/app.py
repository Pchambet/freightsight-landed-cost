"""The job queue: Procrastinate on the same Postgres as everything else.

Decision 6 of the plan, and the reason for it: `defer()` can run inside the transaction that produced
the work, so a job cannot exist for a row that was rolled back. There is no Redis to host, monitor or
back up, and the queue is a table you can read with SQL.

The connector is opened lazily and differently on each side: the API defers through the SQLAlchemy
session's own connection (see `defer`), the worker opens an async pool of its own.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from procrastinate import App, PsycopgConnector
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.settings import get_settings

#: Where tasks are declared. The worker imports these; the API only needs them to build a job name.
IMPORT_PATHS = ["app.jobs.tasks"]


def psycopg_dsn(database_url: str) -> str:
    """SQLAlchemy spells the driver in the URL; psycopg wants it out."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache(maxsize=1)
def get_app() -> App:
    settings = get_settings()
    return App(
        connector=PsycopgConnector(conninfo=psycopg_dsn(settings.database_url)),
        import_paths=IMPORT_PATHS,
    )


def defer(db: Session, task: Any, /, *, lock: str | None = None, **kwargs: Any) -> int | None:
    """Enqueue `task` inside the caller's transaction.

    The job row is written through the SQLAlchemy session's own database connection, so it lands with
    the rest of the transaction or not at all. `lock` also acts as the queueing lock: at most one job
    for a given key is waiting at a time, which is the whole idempotence story for jobs like
    "recompute this organization's risk".

    Returns the job id, or None when the same job is already queued.
    """
    from procrastinate import exceptions

    connection = db.connection().connection.driver_connection
    deferrer = task.configure(connection=connection, lock=lock, queueing_lock=lock)
    # A queueing-lock clash is a unique violation, and a unique violation poisons the whole
    # transaction. The savepoint is issued in SQL rather than through Session.begin_nested because
    # the failing statement is executed on the raw connection, out of the session's sight: the ORM
    # then believes its savepoint is still healthy and never rolls it back.
    name = f"fs_defer_{uuid.uuid4().hex[:12]}"
    db.execute(text(f"SAVEPOINT {name}"))
    try:
        job_id: int = deferrer.defer(**kwargs)
    except exceptions.AlreadyEnqueued:
        db.execute(text(f"ROLLBACK TO SAVEPOINT {name}"))
        return None
    db.execute(text(f"RELEASE SAVEPOINT {name}"))
    return job_id
