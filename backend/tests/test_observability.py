"""Logs and error reporting: the same behaviour with a DSN and without one."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import structlog
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.core.observability import (
    REQUEST_ID_HEADER,
    bind_tenant,
    configure_logging,
    init_sentry,
    request_id,
    scrub,
)
from app.core.settings import Settings, get_settings


def settings(**overrides: object) -> Settings:
    return Settings(database_url="postgresql+psycopg://x:x@localhost/x", **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------- the request id


def test_a_request_id_is_returned_and_can_be_supplied(client: TestClient) -> None:
    """A proxy or the front-end has usually already started the story; we keep their id."""
    generated = client.get("/api/v1/me")
    assert generated.headers[REQUEST_ID_HEADER]

    mine = uuid.uuid4().hex
    kept = client.get("/api/v1/me", headers={REQUEST_ID_HEADER: mine})
    assert kept.headers[REQUEST_ID_HEADER] == mine


def test_an_error_carries_the_same_id_as_the_header(client: TestClient) -> None:
    """One string to quote in a ticket: it is in the body, the header and the log line."""
    mine = uuid.uuid4().hex
    res = client.get(f"/api/v1/containers/{uuid.uuid4()}", headers={REQUEST_ID_HEADER: mine})
    assert res.status_code == 404
    assert res.json()["request_id"] == mine
    assert res.headers[REQUEST_ID_HEADER] == mine


def test_a_validation_error_carries_it_too(client: TestClient) -> None:
    mine = uuid.uuid4().hex
    res = client.post(
        "/api/v1/containers", json={"container_number": "nope"}, headers={REQUEST_ID_HEADER: mine}
    )
    assert res.status_code == 422
    assert res.json()["request_id"] == mine


def test_outside_a_request_there_is_no_id() -> None:
    structlog.contextvars.clear_contextvars()
    assert request_id() is None


# ---------------------------------------------------------------------------- what a log line says


def json_lines(captured: str) -> list[dict[str, Any]]:
    out = []
    for line in captured.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue  # something that is not ours
    return out


def test_a_request_logs_one_line_of_json_with_the_tenant(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """The real output, not a captured structlog event: what Railway will actually collect."""
    configure_logging(settings(app_env="dev"))
    try:
        client.get("/api/v1/organization", headers={REQUEST_ID_HEADER: "abc123"})
        # stdout, not stderr: Railway paints anything on stderr red, and a successful request is not
        # an error. Warnings and above still go to stderr.
        written = capsys.readouterr().out
    finally:
        configure_logging(get_settings())

    events = [line for line in json_lines(written) if line.get("event") == "request"]
    assert len(events) == 1, written
    line = events[0]
    assert line["request_id"] == "abc123"
    assert line["path"] == "/api/v1/organization"
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert line["level"] == "info"
    assert isinstance(line["duration_ms"], float)
    assert line["org_id"]  # bound by the tenant dependency
    assert "timestamp" in line


def test_health_checks_are_not_logged(client: TestClient) -> None:
    """Every fifteen seconds, for ever, would drown everything worth reading."""
    with capture_logs() as captured:
        client.get("/healthz")
    assert [line for line in captured if line.get("event") == "request"] == []


def test_the_tenant_binding_ignores_what_it_does_not_know() -> None:
    structlog.contextvars.clear_contextvars()
    bind_tenant(org_id=None, user_id=None)
    assert "org_id" not in structlog.contextvars.get_contextvars()
    bind_tenant(org_id="org-1")
    assert structlog.contextvars.get_contextvars()["org_id"] == "org-1"
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------- Sentry


def test_without_a_dsn_nothing_is_started() -> None:
    """The tests never talk to anyone, and neither does a deployment without a DSN."""
    assert init_sentry(settings()) is False


def test_credentials_never_leave_the_process() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer secret-token",
                "Cookie": "__session=secret",
                "X-Request-ID": "abc123",
                "User-Agent": "curl/8",
            },
            "cookies": {"__session": "secret"},
            "data": {"file": "an invoice"},
        }
    }
    scrubbed = scrub(event, {})

    headers = scrubbed["request"]["headers"]
    assert headers["Authorization"] == "[redacted]"
    assert headers["Cookie"] == "[redacted]"
    assert headers["X-Request-ID"] == "abc123"  # the useful ones survive
    assert headers["User-Agent"] == "curl/8"
    assert "cookies" not in scrubbed["request"]
    assert "data" not in scrubbed["request"]  # an invoice has no business in a crash report


def test_scrubbing_copes_with_a_shapeless_event() -> None:
    assert scrub({}, {}) == {}
    assert scrub({"request": "not a dict"}, {}) == {"request": "not a dict"}
