"""Frankfurter adapter: response parsing, redirect following, failure handling (no network)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.adapters.fx_ecb import make_fetcher


def _mock(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[..., httpx.Response]:
    transport = httpx.MockTransport(handler)

    def get(url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=transport, follow_redirects=kwargs.get("follow_redirects", False)) as c:
            return c.get(url, params=kwargs.get("params"))

    return get


def test_parses_rate_and_actual_date(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/2026-09-06" and request.url.params["from"] == "USD"
        return httpx.Response(
            200, json={"amount": 1.0, "base": "USD", "date": "2026-09-05", "rates": {"EUR": 0.9187}}
        )

    monkeypatch.setattr(httpx, "get", _mock(handler))
    fetch = make_fetcher("https://api.frankfurter.dev/v1")
    assert fetch("EUR", "USD", date(2026, 9, 6)) == (Decimal("0.9187"), date(2026, 9, 5))


def test_follows_the_legacy_host_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.frankfurter.app":
            return httpx.Response(
                301,
                headers={
                    "location": f"https://api.frankfurter.dev/v1{request.url.path}?{request.url.query.decode()}"
                },
            )
        return httpx.Response(200, json={"date": "2026-09-02", "rates": {"EUR": 0.92}})

    monkeypatch.setattr(httpx, "get", _mock(handler))
    fetch = make_fetcher("https://api.frankfurter.app")
    assert fetch("EUR", "USD", date(2026, 9, 2)) == (Decimal("0.92"), date(2026, 9, 2))


def test_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", _mock(lambda r: httpx.Response(404, json={"message": "not found"})))
    assert make_fetcher("https://api.frankfurter.dev/v1")("EUR", "XXX", date(2026, 9, 2)) is None
