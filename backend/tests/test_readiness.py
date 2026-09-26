"""What is switched on, said in one place: to the customer as what to rely on, to the operator as
which variable to set — and never as a value."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.doctor import render
from app.domain.readiness import JobRun, checks

NOW = datetime(2026, 9, 17, 21, 0, tzinfo=UTC)
DB = "postgresql+psycopg://x:x@localhost:1/x"


def by_key(settings: Settings, runs: dict[str, JobRun]) -> dict[str, tuple[str, str]]:
    return {c.key: (c.state, c.code) for c in checks(settings, runs, NOW)}


def test_production_as_it_stands_tonight_says_what_is_not_on() -> None:
    """The variables production really has on 17 September 2026: no mail key, no tracking key, a
    Clerk development instance. Every one of them looked fine from every screen."""
    settings = Settings(
        database_url=DB,
        app_env="prod",
        cors_origins=["https://freight-sight-two.vercel.app"],
        clerk_issuer="https://grand-marmot-12.clerk.accounts.dev",
        erp_encryption_key="k",
        sentry_dsn="https://key@o1.ingest.de.sentry.io/1",
    )
    runs = {
        "worker.heartbeat": JobRun(NOW - timedelta(minutes=3), 0),
        "ops.backup_freshness": JobRun(NOW - timedelta(hours=5), 0),
    }
    found = by_key(settings, runs)
    assert found["alert_emails"] == ("off", "ALERT_EMAILS_OFF")
    assert found["automatic_tracking"] == ("off", "TRACKING_MANUAL_ONLY")
    assert found["invoice_reading"] == ("ok", "READING_RULES_ONLY")
    assert found["background_jobs"] == ("ok", "WORKER_ALIVE")
    assert found["backups"] == ("ok", "BACKUPS_FRESH")
    assert found["sign_in"] == ("degraded", "CLERK_DEVELOPMENT_INSTANCE")
    assert found["environment"] == ("ok", "ENVIRONMENT")


def test_everything_on_is_everything_ok() -> None:
    settings = Settings(
        database_url=DB,
        app_env="prod",
        cors_origins=["https://app.freightsight.fr"],
        clerk_issuer="https://clerk.freightsight.fr",
        resend_api_key="re_x",
        alerts_from_email="alertes@freightsight.fr",
        shipsgo_api_key="sg_x",
        tracking_webhook_secret_shipsgo="whsec",
        erp_encryption_key="k",
        sentry_dsn="https://key@o1.ingest.de.sentry.io/1",
        # the test environment turns both on for the rest of the suite
        allow_dev_principal=False,
        allow_private_outbound=False,
    )
    runs = {
        "worker.heartbeat": JobRun(NOW - timedelta(minutes=1), 0),
        "ops.backup_freshness": JobRun(NOW - timedelta(hours=2), 0),
    }
    assert {state for state, _ in by_key(settings, runs).values()} == {"ok"}


def test_a_tracking_key_without_its_webhook_secret_only_polls() -> None:
    settings = Settings(database_url=DB, shipsgo_api_key="sg_x")
    assert by_key(settings, {})["automatic_tracking"] == ("degraded", "TRACKING_POLLING_ONLY")


def test_a_silent_worker_and_a_failing_backup_check_are_degraded_not_off() -> None:
    runs = {
        "worker.heartbeat": JobRun(NOW - timedelta(hours=2), 0),
        "ops.backup_freshness": JobRun(NOW - timedelta(hours=3), 4),
    }
    found = by_key(Settings(database_url=DB), runs)
    assert found["background_jobs"] == ("degraded", "WORKER_SILENT")
    assert found["backups"] == ("degraded", "BACKUPS_UNVERIFIED")


def test_the_doctor_names_variables_and_never_prints_a_value() -> None:
    settings = Settings(
        database_url=DB, shipsgo_api_key="sg_secret_value", sentry_dsn="https://secret@sentry"
    )
    report = render(checks(settings, {}, NOW))
    assert "RESEND_API_KEY" in report and "TRACKING_WEBHOOK_SECRET_SHIPSGO" in report
    assert "sg_secret_value" not in report and "secret@sentry" not in report


def test_a_customer_sees_what_they_can_rely_on_and_nothing_about_the_plumbing(client: TestClient) -> None:
    body = client.get("/api/v1/service-status").json()
    keys = [c["key"] for c in body["checks"]]
    assert keys == ["alert_emails", "automatic_tracking", "invoice_reading", "background_jobs", "backups"]
    assert "fix" not in body["checks"][0]
    assert {c["state"] for c in body["checks"]} <= {"ok", "off", "degraded"}


def test_a_webhook_router_that_cannot_see_tenants_is_said_and_only_when_known() -> None:
    blind = {
        c.key: (c.state, c.code) for c in checks(Settings(database_url=DB), {}, NOW, webhook_router_ok=False)
    }
    assert blind["webhook_routing"] == ("degraded", "WEBHOOK_ROUTER_BLIND")
    fine = {c.key: c.state for c in checks(Settings(database_url=DB), {}, NOW, webhook_router_ok=True)}
    assert fine["webhook_routing"] == "ok"
    assert "webhook_routing" not in {c.key for c in checks(Settings(database_url=DB), {}, NOW)}


def test_the_function_owner_is_read_from_the_catalogue(db: Any) -> None:
    from app.domain.readiness import webhook_router_sees_tenants

    # the test database is migrated by a superuser, as production is meant to be
    assert webhook_router_sees_tenants(db.connection()) is True
