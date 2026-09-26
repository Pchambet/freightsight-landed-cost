"""Logs you can search, and errors you hear about.

Two rules run through this module:

  * **Nothing here needs a DSN.** Without SENTRY_DSN the application logs exactly as it does with
    one, and the tests never talk to anyone. Observability that only works in production is
    observability nobody has tested.
  * **The request id is the thread.** It is accepted from the caller when there is one (a proxy or
    the front-end has usually already started the story), generated otherwise, attached to every log
    line and to every problem+json body, and returned in the response header. That is what turns "a
    customer says it failed at 14:03" into one grep.
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.settings import Settings

REQUEST_ID_HEADER = "X-Request-ID"

#: Header values that must never reach a log line or an error report.
SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"})


def request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


def configure_logging(settings: Settings | None = None) -> None:
    """JSON to stdout, which is what Railway collects and what a human can still read with `jq`.

    Takes the environment rather than the whole configuration when asked to: the backup command runs
    with MIGRATIONS_DATABASE_URL and no DATABASE_URL, and refusing to log because the application's
    settings will not build would be a poor trade.
    """
    app_env = settings.app_env if settings is not None else os.environ.get("APP_ENV", "dev")
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            structlog.processors.StackInfoRenderer(),
            # The rendering happens once, in the handler below: structlog hands the event dictionary
            # over to the formatter instead of serialising it itself, so a structlog line and a line
            # from any other library come out the same shape rather than one nested inside the other.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            timestamper,
            structlog.processors.format_exc_info,
        ],
    )
    # Success goes to stdout and trouble to stderr. Railway colours anything on stderr as an error,
    # so a nightly backup reporting success in red is a false alarm arriving at 03:00 for ever.
    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(formatter)
    out.addFilter(lambda record: record.levelno < logging.WARNING)
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(formatter)
    err.setLevel(logging.WARNING)

    root = logging.getLogger()
    root.handlers = [out, err]
    root.setLevel(logging.INFO if app_env != "test" else logging.WARNING)
    # uvicorn keeps its own handlers unless told otherwise, which would double every line.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True


def scrub(event: Any, _hint: dict[str, Any]) -> Any:
    """Take the credentials out of an error report before it leaves the process.

    `send_default_pii` is off, which already stops Sentry collecting bodies and cookies; this is the
    belt to that pair of braces, because a header we add ourselves would not be covered by it.
    """
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: ("[redacted]" if key.lower() in SENSITIVE_HEADERS else value)
                for key, value in headers.items()
            }
        request.pop("cookies", None)
        request.pop("data", None)  # an invoice or an import file has no business in a crash report
    return event


def init_sentry(settings: Settings) -> bool:
    """Start Sentry when there is a DSN. Returns whether it was started."""
    dsn = settings.sentry_dsn
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        before_send=scrub,
    )
    return True


#: Public routes are reached by people with no account, holding a bearer token. The token travels in
#: the body; should a path under this prefix ever carry anything after the route's own name, it is a
#: credential until proven otherwise, and the log line does not get it.
PUBLIC_PREFIX = "/api/v1/public/"


def loggable_path(path: str) -> str:
    if not path.startswith(PUBLIC_PREFIX):
        return path
    head, _, rest = path[len(PUBLIC_PREFIX) :].partition("/")
    known = {"shared-reports": ("open",)}
    if not rest or rest in known.get(head, ()):
        return path
    return f"{PUBLIC_PREFIX}{head}/[redacted]"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """One log line per request, carrying the request id, the tenant and how long it took."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        import time

        incoming = request.headers.get(REQUEST_ID_HEADER)
        correlation = incoming or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=correlation, method=request.method, path=loggable_path(request.url.path)
        )
        logger = structlog.get_logger("freightsight.request")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request failed", duration_ms=round((time.perf_counter() - started) * 1000, 1))
            structlog.contextvars.clear_contextvars()
            raise
        duration = round((time.perf_counter() - started) * 1000, 1)
        response.headers[REQUEST_ID_HEADER] = correlation
        bind_tenant(
            org_id=getattr(request.state, "org_id", None),
            user_id=getattr(request.state, "user_id", None),
        )
        # Health checks every fifteen seconds would drown everything else.
        if request.url.path not in ("/healthz", "/readyz"):
            logger.info("request", status=response.status_code, duration_ms=duration)
        structlog.contextvars.clear_contextvars()
        return response


def bind_tenant(org_id: object | None = None, user_id: object | None = None) -> None:
    """Say who this request is for, once the authentication has worked it out."""
    values: dict[str, str] = {}
    if org_id is not None:
        values["org_id"] = str(org_id)
    if user_id is not None:
        values["user_id"] = str(user_id)
    if values:
        structlog.contextvars.bind_contextvars(**values)
