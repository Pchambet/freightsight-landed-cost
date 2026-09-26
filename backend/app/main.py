from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine, event, text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import schemas
from app.api.v1.router import router as v1_router
from app.core.auth import DbDep
from app.core.db import assert_migrated, get_engine
from app.core.errors import DomainError
from app.core.observability import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    configure_logging,
    init_sentry,
    request_id,
)
from app.core.settings import Settings, get_settings
from app.core.tenancy import ORG_GUC, SHARE_TOKEN_GUC
from app.jobs.handlers import WORKER_HEARTBEAT  # one name for the row both sides read

logger = logging.getLogger("freightsight")

#: Three missed beats. The worker writes one every five minutes, so this does not fire on a slow
#: deploy or a job that held the loop for a few minutes.
WORKER_SILENT_AFTER = timedelta(minutes=15)


def install_guc_reset(engine: Engine) -> None:
    """Clear the tenant variable when a pooled connection is returned, so it never leaks across requests."""

    @event.listens_for(engine, "checkin")
    def _reset(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        try:
            cur = dbapi_connection.cursor()
            cur.execute(f"RESET {ORG_GUC}")
            cur.execute(f"RESET {SHARE_TOKEN_GUC}")
            cur.close()
            # RESET opens a transaction on a non-autocommit DBAPI connection; close it, or the
            # next checkout fails with "can't change autocommit now: connection in transaction".
            dbapi_connection.rollback()
        except Exception:  # pragma: no cover - connection already broken
            pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.is_prod and not settings.clerk_enabled:
        raise RuntimeError(
            "APP_ENV=prod requires CLERK_JWKS_URL and CLERK_ISSUER: "
            "without them every request would be refused"
        )
    engine = get_engine()
    install_guc_reset(engine)
    if settings.app_env != "test":
        assert_migrated(engine)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    if init_sentry(settings):
        logger.info("sentry enabled for environment %s", settings.app_env)
    app = FastAPI(
        title="FreightSight API",
        version="0.2.0",
        description="Landed cost allocation and container tracking for SMB importers. Pre-alpha.",
        lifespan=lifespan,
        docs_url=None if settings.is_prod else "/docs",
        redoc_url=None,
    )
    # Outermost, so every request is logged with its id — including the ones CORS rejects.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.exception_handler(DomainError)
    async def domain_problem(_: Request, exc: DomainError) -> JSONResponse:
        body = schemas.Problem(
            title=_title(exc.status),
            status=exc.status,
            code=exc.code,
            detail=exc.message,
            errors=exc.errors,
            # The same id as the log line and the response header: one string to quote in a ticket.
            request_id=request_id(),
        ).model_dump()
        body.update(exc.extra)
        return JSONResponse(body, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else None
        body = schemas.Problem(
            title=_title(exc.status_code),
            status=exc.status_code,
            detail=detail,
            request_id=request_id(),
        ).model_dump()
        if isinstance(exc.detail, dict):
            body.update(exc.detail)
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_problem(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in e["loc"] if p != "body"),
                "code": e["type"],
                "message": e["msg"],
            }
            for e in exc.errors()
        ]
        body = schemas.Problem(
            title="Validation error",
            status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="VALIDATION",
            errors=errors,
            request_id=request_id(),
        ).model_dump()
        return JSONResponse(body, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT)

    @app.get("/", tags=["system"])
    def root() -> dict[str, str]:
        return {"service": "FreightSight API", "status": "pre-alpha", "api": "/api/v1"}

    @app.get("/healthz", tags=["system"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["system"])
    def readyz() -> JSONResponse:
        """Is this API able to serve? Railway uses it as the deploy healthcheck.

        Which is why a dead worker must not fail it: the day the worker crashes is the day you most
        need to be able to deploy a fix. `worker_seen_at` is reported here for whoever is reading,
        and `GET /healthz/worker` is the probe that actually fails on it.
        """
        settings = get_settings()
        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
                role = _role_check(conn, settings)
                seen = _worker_seen_at(conn)
        except Exception as e:
            logger.error("readiness failed: %s", e)
            return JSONResponse({"status": "unavailable"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        if role is not None:
            logger.error("readiness refused: %s", role)
            return JSONResponse(
                {"status": "unavailable", "code": "DB_ROLE_BYPASSES_RLS", "detail": role},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return JSONResponse({"status": "ready", "worker_seen_at": seen.isoformat() if seen else None})

    @app.get("/healthz/worker", tags=["system"])
    def healthz_worker(db: DbDep) -> JSONResponse:
        """The probe an external uptime service points at, separately from the API's own.

        Nobody would otherwise know the worker had died: there is no exception to report, there is
        no process, and every screen goes on showing the numbers it last computed while the alerts
        that the product is sold on quietly stop being raised.
        """
        try:
            seen = _worker_seen_at(db.connection())
        except Exception as e:  # pragma: no cover - the database is the API's own problem
            logger.error("worker health check could not read the database: %s", e)
            return JSONResponse({"status": "unknown"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        age = (datetime.now(UTC) - seen).total_seconds() if seen is not None else None
        alive = age is not None and age < WORKER_SILENT_AFTER.total_seconds()
        body = {
            "status": "ok" if alive else "stale",
            "worker_seen_at": seen.isoformat() if seen else None,
            "seconds_since": int(age) if age is not None else None,
        }
        if alive:
            return JSONResponse(body)
        logger.error("the worker has not been seen", extra={"worker_seen_at": body["worker_seen_at"]})
        return JSONResponse(body, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    app.include_router(v1_router)
    return app


def _worker_seen_at(conn: Connection) -> datetime | None:
    """The worker's last heartbeat, or None when it has never beaten (a fresh database, or a worker
    that has not started since this table existed)."""
    try:
        return conn.execute(
            text("SELECT last_run_at FROM job_runs WHERE name = :name"), {"name": WORKER_HEARTBEAT}
        ).scalar()
    except Exception:  # pragma: no cover - a database that answered SELECT 1 a line ago
        return None


def _role_check(conn: Connection, settings: Settings) -> str | None:
    """Why this connection must not serve production, or None because it is fine.

    Row Level Security is the whole tenant boundary here, and a superuser or a `BYPASSRLS` role
    ignores `FORCE ROW LEVEL SECURITY` **silently**: every query keeps working, every screen keeps
    rendering, and the isolation is simply gone. `DATABASE_URL` and `MIGRATIONS_DATABASE_URL` live
    side by side on the same Railway service, so one copy-paste is all it takes. Refusing to be
    ready is much better than serving everyone's data to everyone.
    """
    if not settings.is_prod:
        return None
    row = conn.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).first()
    if row is None:  # pragma: no cover - current_user is always in pg_roles
        return None
    if row[0] or row[1]:
        which = "a superuser" if row[0] else "a BYPASSRLS role"
        return f"the application is connected as {which}, so tenant isolation is not enforced"
    return None


def _title(code: int) -> str:
    return {
        400: "Bad request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not found",
        409: "Conflict",
        413: "Content too large",
        422: "Unprocessable content",
        501: "Not implemented",
    }.get(code, "Error")


app = create_app()
