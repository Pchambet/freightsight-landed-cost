"""The queue is not an archive: a job that succeeded a month ago is deleted, with its events.

Every tick of every schedule leaves a row. In production they were kept for ever — 21 000 jobs and
65 000 events after a month — in the table the worker reads at every fetch.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.jobs import handlers


def job(db: Session, name: str, *, status: str, finished: str) -> int:
    """A job in `status`, whose every event is `finished` old."""
    job_id: int = db.scalar(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, args) "
            "VALUES ('default', :name, '{}') RETURNING id"
        ),
        {"name": name},
    )
    db.execute(
        text("UPDATE procrastinate_jobs SET status = CAST(:s AS procrastinate_job_status) WHERE id = :id"),
        {"s": status, "id": job_id},
    )
    db.execute(
        text("UPDATE procrastinate_events SET at = now() - CAST(:ago AS interval) WHERE job_id = :id"),
        {"ago": finished, "id": job_id},
    )
    return job_id


def test_only_the_jobs_that_succeeded_over_a_month_ago_are_forgotten(db: Session) -> None:
    old = job(db, "worker.heartbeat", status="succeeded", finished="40 days")
    recent = job(db, "worker.heartbeat", status="succeeded", finished="5 days")
    failed = job(db, "invoices.extract", status="failed", finished="40 days")
    waiting = job(db, "erp.sync", status="todo", finished="40 days")
    assert db.scalar(text("SELECT count(*) FROM procrastinate_events WHERE job_id = :id"), {"id": old}) > 0

    assert handlers.forget_finished_jobs(db) == 1

    kept = set(db.scalars(text("SELECT id FROM procrastinate_jobs")))
    assert old not in kept and {recent, failed, waiting} <= kept
    # Its events go with it: they are two thirds of what the table weighed.
    assert db.scalar(text("SELECT count(*) FROM procrastinate_events WHERE job_id = :id"), {"id": old}) == 0
    assert handlers.forget_finished_jobs(db) == 0


def test_the_cleaning_is_on_the_schedule() -> None:
    from app.jobs.tasks import app

    scheduled = {periodic.task.name for periodic in app.periodic_registry.periodic_tasks.values()}
    assert "ops.forget_finished_jobs" in scheduled
