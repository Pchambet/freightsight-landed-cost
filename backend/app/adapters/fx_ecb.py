"""Frankfurter adapter: ECB reference rates, no API key. GET {api_url}/{date}?from=QUOTE&to=BASE,
and {api_url}/{start}..{end} for every rate published between two dates.

The service moved from api.frankfurter.app to api.frankfurter.dev/v1 (301); redirects are followed and the
default URL points at the new host."""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import httpx

from app.core.http import timeout as http_timeout

logger = logging.getLogger(__name__)


def make_fetcher(api_url: str, timeout: float = 5.0):  # type: ignore[no-untyped-def]
    def fetch(base: str, quote: str, on_date: date) -> tuple[Decimal, date] | None:
        try:
            res = httpx.get(
                f"{api_url.rstrip('/')}/{on_date.isoformat()}",
                params={"from": quote, "to": base},
                timeout=http_timeout(timeout),
                follow_redirects=True,
            )
            res.raise_for_status()
            payload = res.json()
            rate = payload["rates"][base]
            return Decimal(str(rate)), date.fromisoformat(payload["date"])
        except (httpx.HTTPError, KeyError, ValueError) as e:
            logger.warning("fx fetch failed for %s->%s on %s: %s", quote, base, on_date, e)
            return None

    return fetch


def make_range_fetcher(api_url: str, timeout: float = 10.0):  # type: ignore[no-untyped-def]
    def fetch_range(base: str, quote: str, start: date, end: date) -> dict[date, Decimal] | None:
        try:
            res = httpx.get(
                f"{api_url.rstrip('/')}/{start.isoformat()}..{end.isoformat()}",
                params={"from": quote, "to": base},
                timeout=http_timeout(timeout),
                follow_redirects=True,
            )
            res.raise_for_status()
            rates = res.json()["rates"]
            return {date.fromisoformat(day): Decimal(str(quoted[base])) for day, quoted in rates.items()}
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            logger.warning("fx range fetch failed for %s->%s %s..%s: %s", quote, base, start, end, e)
            return None

    return fetch_range
