"""Fixed FX rates: what gets cached, under which date, and what a future cost date is worth."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.domain.fx.service import FxService
from app.domain.models import FxRate

FRIDAY = date(2026, 9, 11)
SATURDAY = date(2026, 9, 12)
#: Thursday 17 September 2026, 18:00 in Brussels: that day's fixing is out.
NOW = datetime(2026, 9, 17, 16, 0, tzinfo=UTC)


def fixed(rate: str, published_on: date, now: datetime | None = None) -> tuple[list[date], FxService]:
    """A provider that always answers with one published rate, and remembers what it was asked."""
    asked: list[date] = []

    def fetch(base: str, quote: str, on_date: date) -> tuple[Decimal, date] | None:
        asked.append(on_date)
        return Decimal(rate), published_on

    return asked, FxService(fetch, now=lambda: now or NOW)


def test_the_same_days_rate_is_cached_and_fetched_once(db: Session) -> None:
    asked, fx = fixed("0.9187", FRIDAY)
    first = fx.resolve(db, "EUR", "USD", FRIDAY)
    second = fx.resolve(db, "EUR", "USD", FRIDAY)
    assert (first.rate, first.rate_date, first.source) == (Decimal("0.9187"), FRIDAY, "ecb")
    assert second.source == "ecb"
    assert asked == [FRIDAY]  # the second call read the cache


def test_a_weekend_uses_fridays_rate_without_writing_it_under_saturday(db: Session) -> None:
    """The ECB does not publish on a Saturday; nothing may make it look as though it did."""
    asked, fx = fixed("0.9187", FRIDAY)
    resolved = fx.resolve(db, "EUR", "USD", SATURDAY)
    assert (resolved.rate, resolved.rate_date, resolved.source) == (Decimal("0.9187"), FRIDAY, "ecb_latest")
    assert db.get(FxRate, ("EUR", "USD", SATURDAY)) is None
    friday_row = db.get(FxRate, ("EUR", "USD", FRIDAY))
    assert friday_row is not None and friday_row.rate == Decimal("0.9187")
    assert asked == [SATURDAY]


def test_a_cost_dated_in_the_future_does_not_poison_that_day(db: Session) -> None:
    """An estimate is dated at the ETA: it gets the latest published rate, flagged, and cached nowhere."""
    eta = date(2026, 10, 20)
    _, fx = fixed("0.9187", date(2026, 9, 17))
    forecast = fx.resolve(db, "EUR", "USD", eta)
    assert (forecast.rate_date, forecast.source) == (date(2026, 9, 17), "ecb_latest")
    assert db.get(FxRate, ("EUR", "USD", eta)) is None

    # the day arrives and the ECB publishes its own rate: the invoices really dated that day get it
    _, fx_that_day = fixed("0.8800", eta, now=datetime(2026, 10, 20, 16, 0, tzinfo=UTC))
    real = fx_that_day.resolve(db, "EUR", "USD", eta)
    assert (real.rate, real.rate_date, real.source) == (Decimal("0.8800"), eta, "ecb")


def test_a_provider_that_is_down_asks_for_a_manual_rate(db: Session) -> None:
    fx = FxService(lambda base, quote, on_date: None)
    with pytest.raises(Unprocessable) as raised:
        fx.resolve(db, "EUR", "USD", FRIDAY)
    assert raised.value.code == "FX_RATE_UNAVAILABLE"
    assert db.get(FxRate, ("EUR", "USD", FRIDAY)) is None

    # and a rate typed by hand is never confused with a published one
    manual = fx.resolve(db, "EUR", "USD", FRIDAY, Decimal("0.9200"))
    assert (manual.rate, manual.source) == (Decimal("0.9200"), "manual")
    assert db.get(FxRate, ("EUR", "USD", FRIDAY)) is None


def test_a_weekend_asked_twice_costs_one_call(db: Session) -> None:
    """Friday's rate is the only one a Saturday can get: once it is held, nobody needs asking."""
    asked, fx = fixed("0.9187", FRIDAY)
    fx.resolve(db, "EUR", "USD", SATURDAY)
    again = fx.resolve(db, "EUR", "USD", SATURDAY)
    assert (again.rate_date, again.source) == (FRIDAY, "ecb_latest")
    assert asked == [SATURDAY]


def test_an_eta_asked_twice_costs_one_call_until_the_next_fixing(db: Session) -> None:
    eta = date(2026, 10, 20)
    asked, fx = fixed("0.9187", NOW.date())
    fx.resolve(db, "EUR", "USD", eta)
    fx.resolve(db, "EUR", "USD", eta)
    assert asked == [eta]

    # Friday 18:00: a newer fixing can exist, so the provider is asked again
    friday_evening = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
    asked_later, later = fixed("0.9000", date(2026, 9, 18), now=friday_evening)
    fresh = later.resolve(db, "EUR", "USD", eta)
    assert (fresh.rate, fresh.rate_date, fresh.source) == (Decimal("0.9000"), date(2026, 9, 18), "ecb_latest")
    assert asked_later == [eta]


def test_a_provider_outage_does_not_block_an_estimate_dated_at_the_eta(db: Session) -> None:
    eta = date(2026, 10, 20)
    _, fx = fixed("0.9187", NOW.date())
    fx.resolve(db, "EUR", "USD", NOW.date())
    friday_evening = datetime(2026, 9, 18, 16, 0, tzinfo=UTC)
    down = FxService(lambda b, q, d: None, now=lambda: friday_evening)
    stand_in = down.resolve(db, "EUR", "USD", eta)
    assert (stand_in.rate_date, stand_in.source) == (NOW.date(), "ecb_latest")

    # ...but a past working day has a real quote somewhere: that one is never guessed
    with pytest.raises(Unprocessable):
        down.resolve(db, "EUR", "USD", date(2026, 9, 16))


def test_a_preloaded_period_is_answered_without_asking_again(db: Session) -> None:
    """Six months of sample data, or a file of two hundred orders: one call, then the cache."""
    asked: list[date] = []

    def fetch(base: str, quote: str, on_date: date) -> tuple[Decimal, date] | None:
        asked.append(on_date)
        return None

    def fetch_range(base: str, quote: str, start: date, end: date) -> dict[date, Decimal] | None:
        return {date(2026, 9, 10): Decimal("0.9100"), FRIDAY: Decimal("0.9187")}

    fx = FxService(fetch, now=lambda: NOW, range_fetcher=fetch_range)
    assert fx.preload(db, "EUR", "USD", date(2026, 9, 1), FRIDAY) == 2

    thursday = fx.resolve(db, "EUR", "USD", date(2026, 9, 10))
    weekend = fx.resolve(db, "EUR", "USD", SATURDAY)
    assert (thursday.rate, thursday.source) == (Decimal("0.9100"), "ecb")
    assert (weekend.rate, weekend.rate_date, weekend.source) == (Decimal("0.9187"), FRIDAY, "ecb_latest")
    assert asked == []


def test_preloading_is_a_courtesy_never_a_condition(db: Session) -> None:
    _, plain = fixed("0.9187", FRIDAY)
    assert plain.preload(db, "EUR", "USD", date(2026, 9, 1), FRIDAY) == 0  # no range fetcher at all
    down = FxService(lambda b, q, d: None, range_fetcher=lambda b, q, s, e: None)
    assert down.preload(db, "EUR", "USD", date(2026, 9, 1), FRIDAY) == 0  # provider unreachable
    assert down.preload(db, "EUR", "EUR", date(2026, 9, 1), FRIDAY) == 0  # nothing to convert
