"""A worker killed mid-job — out of memory, a redeploy, the host going away — leaves its job `doing`
for ever. Procrastinate fetches no job whose lock a `doing` job holds, so the invoice it was reading
stayed "being read" and its Retry did nothing."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pdfs import make_pdf
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.models import Organization
from app.jobs import handlers

SCHEDULED = {"dnd.recompute_risk"}


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def worker(db: Session, *, silent_for: str) -> int:
    worker_id: int = db.scalar(
        text(
            "INSERT INTO procrastinate_workers (last_heartbeat) "
            "VALUES (now() - CAST(:ago AS interval)) RETURNING id"
        ),
        {"ago": silent_for},
    )
    return worker_id


def reading_started(client: TestClient, db: Session, *, by: int, attempts: int = 0) -> str:
    """Upload an invoice, and let `by` pick its extraction job up."""
    res = client.post(
        "/api/v1/invoices", files={"file": ("facture.pdf", make_pdf(["FACTURE"]), "application/pdf")}
    )
    assert res.status_code == 201, res.text
    invoice_id: str = res.json()["id"]
    db.execute(
        text(
            "UPDATE procrastinate_jobs SET status = 'doing', worker_id = :w, attempts = :a WHERE lock = :lock"
        ),
        {"w": by, "a": attempts, "lock": f"invoice:{invoice_id}"},
    )
    return invoice_id


def jobs(db: Session, lock: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT status, attempts FROM procrastinate_jobs WHERE lock = :lock ORDER BY id"), {"lock": lock}
    ).mappings()
    return [dict(row) for row in rows]


def test_a_job_a_dead_worker_left_behind_is_queued_again(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id = reading_started(client, db, by=worker(db, silent_for="1 hour"))

    assert handlers.recover_stalled_jobs(db, scheduled=SCHEDULED) == (1, 0)
    assert jobs(db, f"invoice:{invoice_id}") == [{"status": "todo", "attempts": 1}]


def test_a_live_workers_job_is_left_alone(client: TestClient, db: Session, org: Organization) -> None:
    """Reading a long PDF is not stalling: the worker is still beating."""
    invoice_id = reading_started(client, db, by=worker(db, silent_for="5 seconds"))

    assert handlers.recover_stalled_jobs(db, scheduled=SCHEDULED) == (0, 0)
    assert jobs(db, f"invoice:{invoice_id}") == [{"status": "doing", "attempts": 0}]


def test_a_retry_asked_meanwhile_takes_the_stalled_jobs_place(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Retry queued a second job behind the lock the dead one held. Re-queueing the dead one as well
    would clash with it; it is closed instead, and the person's retry runs."""
    invoice_id = reading_started(client, db, by=worker(db, silent_for="1 hour"))
    assert client.post(f"/api/v1/invoices/{invoice_id}/retry").status_code == 200

    assert handlers.recover_stalled_jobs(db, scheduled=SCHEDULED) == (0, 1)
    assert jobs(db, f"invoice:{invoice_id}") == [
        {"status": "failed", "attempts": 1},
        {"status": "todo", "attempts": 0},
    ]


def test_a_document_that_keeps_killing_the_worker_is_given_up_and_the_invoice_says_so(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Retried for ever, a PDF that runs the worker out of memory would take it down every ten minutes.
    After the last try the invoice reads FAILED, which is where the screen offers Retry."""
    invoice_id = reading_started(client, db, by=worker(db, silent_for="1 hour"), attempts=2)

    assert handlers.recover_stalled_jobs(db, scheduled=SCHEDULED) == (0, 1)
    assert jobs(db, f"invoice:{invoice_id}") == [{"status": "failed", "attempts": 3}]
    invoice = client.get(f"/api/v1/invoices/{invoice_id}").json()
    assert invoice["status"] == "FAILED"
    assert client.post(f"/api/v1/invoices/{invoice_id}/retry").status_code == 200
    assert jobs(db, f"invoice:{invoice_id}")[-1] == {"status": "todo", "attempts": 0}


def test_a_scheduled_job_is_left_to_its_next_tick(db: Session) -> None:
    """Replaying an old tick would write an old moment over a newer one (the heartbeat, say); the
    schedule runs the same work again within the hour anyway."""
    dead = worker(db, silent_for="1 hour")
    db.execute(
        text(
            "INSERT INTO procrastinate_jobs (queue_name, task_name, queueing_lock, args, status, worker_id) "
            "VALUES ('default', 'dnd.recompute_risk', 'dnd:recompute_risk', '{}', 'doing', :w)"
        ),
        {"w": dead},
    )

    assert handlers.recover_stalled_jobs(db, scheduled=SCHEDULED) == (0, 1)
    status = db.scalar(text("SELECT status FROM procrastinate_jobs WHERE task_name = 'dnd.recompute_risk'"))
    assert status == "failed"
