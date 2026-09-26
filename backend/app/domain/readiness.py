"""What is switched on, what is not, and what it takes to switch it on.

Every moving part of the product was built before anyone could afford to run it: alert e-mails need
a verified sending domain, automatic tracking needs a provider account, the sign-in runs on a
development instance until there is a domain. None of that is visible from a screen — an alert that
is logged instead of sent looks, from the inside, exactly like an alert that was sent. This module is
the one place that says which is which: to the customer, in terms of what they can rely on; to the
operator, in terms of which variable to set.

Nothing here reads or returns a secret. A key is present or absent, never shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.settings import Settings

State = Literal["ok", "off", "degraded"]
Audience = Literal["customer", "operator"]

WORKER_SILENT_AFTER = timedelta(minutes=15)
BACKUP_STALE_AFTER = timedelta(hours=26)


@dataclass(frozen=True)
class Check:
    key: str
    state: State
    audience: Audience
    #: What a screen translates. The sentence is never written here.
    code: str
    params: dict[str, str] = field(default_factory=dict)
    #: For the operator only: what to do about it, in one line.
    fix: str | None = None


@dataclass(frozen=True)
class JobRun:
    last_run_at: datetime | None
    failures: int


def job_runs(conn: Connection) -> dict[str, JobRun]:
    rows = conn.execute(text("SELECT name, last_run_at, failures FROM job_runs"))
    return {name: JobRun(last_run_at, failures or 0) for name, last_run_at, failures in rows}


def webhook_router_sees_tenants(conn: Connection) -> bool | None:
    """Whether `tracking_resolve_subscription()` can see across organizations.

    It is SECURITY DEFINER, and every table it reads is under FORCED row-level security, which binds
    a table's owner too: the function only sees rows if the role that owns it is a superuser or has
    BYPASSRLS. Owned by an ordinary role it returns nothing, every provider webhook is filed as
    unroutable, and nothing else anywhere says so. None when the function is not there.
    """
    row = conn.execute(
        text(
            "SELECT r.rolsuper OR r.rolbypassrls FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
            "WHERE p.proname = 'tracking_resolve_subscription'"
        )
    ).first()
    return None if row is None else bool(row[0])


def checks(
    settings: Settings,
    runs: dict[str, JobRun],
    now: datetime | None = None,
    webhook_router_ok: bool | None = None,
) -> list[Check]:
    now = now or datetime.now(UTC)
    found: list[Check] = []

    # ------------------------------------------------------------------ what a customer relies on
    if settings.resend_api_key and settings.alerts_from_email:
        found.append(Check("alert_emails", "ok", "customer", "ALERT_EMAILS_ON"))
    else:
        missing = [
            name
            for name, value in (
                ("RESEND_API_KEY", settings.resend_api_key),
                ("ALERTS_FROM_EMAIL", settings.alerts_from_email),
            )
            if not value
        ]
        found.append(
            Check(
                "alert_emails",
                "off",
                "customer",
                "ALERT_EMAILS_OFF",
                fix=f"set {', '.join(missing)} on the API service; the worker reads them from it. "
                "Resend only sends from a verified domain.",
            )
        )

    providers = [
        name
        for name, key in (("shipsgo", settings.shipsgo_api_key), ("terminal49", settings.terminal49_api_key))
        if key
    ]
    if providers:
        unsigned = [
            name
            for name, secret in (
                ("shipsgo", settings.tracking_webhook_secret_shipsgo),
                ("terminal49", settings.tracking_webhook_secret_terminal49),
            )
            if name in providers and not secret
        ]
        found.append(
            Check(
                "automatic_tracking",
                "degraded" if unsigned else "ok",
                "customer",
                "TRACKING_POLLING_ONLY" if unsigned else "TRACKING_ON",
                {"providers": ",".join(providers)},
                fix=(
                    "set "
                    + ", ".join(f"TRACKING_WEBHOOK_SECRET_{n.upper()}" for n in unsigned)
                    + ": without it the provider's webhooks are refused and containers are only polled"
                )
                if unsigned
                else None,
            )
        )
    else:
        found.append(
            Check(
                "automatic_tracking",
                "off",
                "customer",
                "TRACKING_MANUAL_ONLY",
                fix="set SHIPSGO_API_KEY (and TRACKING_WEBHOOK_SECRET_SHIPSGO) on the API service",
            )
        )

    found.append(
        Check(
            "invoice_reading",
            "ok",
            "customer",
            "READING_RULES_AND_MODEL" if settings.extraction_api_key else "READING_RULES_ONLY",
        )
    )

    heartbeat = runs.get("worker.heartbeat")
    seen = heartbeat.last_run_at if heartbeat else None
    if seen is not None and now - seen < WORKER_SILENT_AFTER:
        found.append(
            Check("background_jobs", "ok", "customer", "WORKER_ALIVE", {"seen_at": seen.isoformat()})
        )
    else:
        found.append(
            Check(
                "background_jobs",
                "degraded",
                "customer",
                "WORKER_SILENT",
                {"seen_at": seen.isoformat() if seen else ""},
                fix="the worker service is not running: check its deployment and its logs",
            )
        )

    backup = runs.get("ops.backup_freshness")
    checked = backup.last_run_at if backup else None
    if (
        backup is not None
        and checked is not None
        and backup.failures == 0
        and now - checked < BACKUP_STALE_AFTER
    ):
        found.append(Check("backups", "ok", "customer", "BACKUPS_FRESH", {"checked_at": checked.isoformat()}))
    else:
        found.append(
            Check(
                "backups",
                "degraded",
                "customer",
                "BACKUPS_UNVERIFIED",
                {"checked_at": checked.isoformat() if checked else ""},
                fix="no recent successful backup check: look at the worker's `ops.backup_freshness` "
                "job and at the bucket (SCW_* variables)",
            )
        )

    # ------------------------------------------------------------------ what only the operator sees
    is_prod = settings.app_env == "prod"
    # Said, not judged: the doctor cannot know whether this environment is meant to be production.
    found.append(Check("environment", "ok", "operator", "ENVIRONMENT", {"app_env": settings.app_env}))
    switches = [
        name
        for name, on in (
            ("ALLOW_DEV_PRINCIPAL", settings.allow_dev_principal),
            ("ALLOW_PRIVATE_OUTBOUND", settings.allow_private_outbound),
        )
        if on
    ]
    found.append(
        Check(
            "development_switches",
            "degraded" if switches and is_prod else "ok",
            "operator",
            "DEV_SWITCHES_ON" if switches else "DEV_SWITCHES_OFF",
            {"switches": ",".join(switches)},
            fix=f"unset {', '.join(switches)}: production ignores them, and they should not be there"
            if switches and is_prod
            else None,
        )
    )
    issuer = settings.clerk_issuer or ""
    development_instance = ".accounts.dev" in issuer
    found.append(
        Check(
            "sign_in",
            "off" if not issuer else "degraded" if development_instance else "ok",
            "operator",
            "CLERK_MISSING"
            if not issuer
            else "CLERK_DEVELOPMENT_INSTANCE"
            if development_instance
            else "CLERK_PRODUCTION",
            fix="a Clerk production instance needs a domain you own: buy it, create the instance, then "
            "set CLERK_ISSUER, CLERK_JWKS_URL and the front-end keys"
            if not issuer or development_instance
            else None,
        )
    )
    local = [origin for origin in settings.cors_origins if "localhost" in origin or "127.0.0.1" in origin]
    found.append(
        Check(
            "cors",
            "degraded" if local and is_prod else "ok",
            "operator",
            "CORS_LOCALHOST" if local and is_prod else "CORS_OK",
            {"origins": ",".join(local)},
            fix="remove localhost from CORS_ORIGINS" if local and is_prod else None,
        )
    )
    found.append(
        Check(
            "erp_secrets",
            "ok" if settings.erp_encryption_key else "off",
            "operator",
            "ERP_KEY_SET" if settings.erp_encryption_key else "ERP_KEY_MISSING",
            fix=None
            if settings.erp_encryption_key
            else "set ERP_ENCRYPTION_KEY: no ERP connection can be saved without it",
        )
    )
    if webhook_router_ok is not None:
        found.append(
            Check(
                "webhook_routing",
                "ok" if webhook_router_ok else "degraded",
                "operator",
                "WEBHOOK_ROUTER_OK" if webhook_router_ok else "WEBHOOK_ROUTER_BLIND",
                fix=None
                if webhook_router_ok
                else "tracking_resolve_subscription() is owned by a role that row-level security binds: "
                "it finds no subscription and every tracking webhook is dropped as unroutable. Run the "
                "migrations as a superuser, or ALTER FUNCTION … OWNER TO one",
            )
        )
    found.append(
        Check(
            "error_reporting",
            "ok" if settings.sentry_dsn else "off",
            "operator",
            "SENTRY_ON" if settings.sentry_dsn else "SENTRY_OFF",
            fix=None
            if settings.sentry_dsn
            else "set SENTRY_DSN: an exception in production is otherwise only a log line",
        )
    )
    return found
