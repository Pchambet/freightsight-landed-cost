"""The `X-Org-Id` development principal, and the two switches that have to agree.

An organisation id is not a secret: it comes out of `/me`, it is in support tickets, in logs and in
crash reports. Accepted on its own it is an OWNER on every cost, invoice, order and ERP connection
of that tenant, with no token at all. Until now the only thing standing between production and that
mode was `APP_ENV`, which lives in a dashboard and comes back to its "dev" default whenever it is
lost — a cloned environment, a restored service, a `production` typed where `prod` was meant.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app.core.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", os.environ.get("DATABASE_URL", "postgresql+psycopg://x@x/x"))
    yield
    get_settings.cache_clear()


def configure(monkeypatch: pytest.MonkeyPatch, *, app_env: str, allow: str) -> None:
    from app.core.settings import get_settings

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("ALLOW_DEV_PRINCIPAL", allow)
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("app_env", "allow", "status"),
    [
        ("test", "true", 200),  # what the suite and the local stack run on
        ("test", "false", 401),  # the opt-in is what grants it, not the environment
        ("prod", "true", 401),  # and prod refuses even when someone turned it on
        ("prod", "false", 401),
    ],
)
def test_the_header_needs_both_switches(
    client: TestClient,
    settings_env: None,
    monkeypatch: pytest.MonkeyPatch,
    app_env: str,
    allow: str,
    status: int,
) -> None:
    configure(monkeypatch, app_env=app_env, allow=allow)
    res = client.get("/api/v1/organization", headers={"X-Org-Id": str(uuid.uuid4())})
    assert res.status_code == status, res.text
    if status == 401:
        assert res.json()["code"] == "UNAUTHORIZED"


def test_a_refused_header_creates_no_organization(
    client: TestClient, settings_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal has to happen before the mirror row, or an unauthenticated request still leaves
    a tenant behind in the database."""
    from app.domain.models import Organization

    org_id = uuid.uuid4()
    configure(monkeypatch, app_env="prod", allow="true")
    assert client.get("/api/v1/organization", headers={"X-Org-Id": str(org_id)}).status_code == 401

    configure(monkeypatch, app_env="test", allow="true")
    from app.core.db import get_db
    from app.main import app

    db = next(app.dependency_overrides[get_db]())
    assert db.get(Organization, org_id) is None
