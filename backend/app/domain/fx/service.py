"""Fixed FX rates. A rate is looked up in `fx_rates`, fetched once from the ECB (via Frankfurter) if missing,
and never converted on read: the caller stores (rate, date, source) on the row that used it."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.domain.models import FxRate

# 1 unit of `quote` = `rate` units of `base`. Injectable so tests never touch the network.
Fetcher = Callable[[str, str, date], tuple[Decimal, date] | None]
#: Every rate published between two dates, in one call: `{published_on: rate}`, or None when down.
RangeFetcher = Callable[[str, str, date, date], dict[date, Decimal] | None]


@dataclass(frozen=True)
class ResolvedRate:
    rate: Decimal
    rate_date: date
    source: str


#: The ECB publishes its euro reference rates around 16:00 CET; the provider follows within minutes.
ECB_ZONE = ZoneInfo("Europe/Brussels")
PUBLISHED_BY = time(17, 0)
#: With the provider down, how old a published rate may be and still stand in for a date the ECB has
#: not reached yet.
STAND_IN_MAX_AGE = timedelta(days=7)


class FxService:
    def __init__(
        self,
        fetcher: Fetcher,
        now: Callable[[], datetime] | None = None,
        range_fetcher: RangeFetcher | None = None,
    ):
        self._fetch = fetcher
        self._now = now or (lambda: datetime.now(UTC))
        self._fetch_range = range_fetcher

    def preload(self, db: Session, base: str, quote: str, start: date, end: date) -> int:
        """Hold every rate published between two dates, asked for in one call instead of one per
        day. Returns how many were stored. It is a courtesy to whoever is about to resolve many
        dates — a file of orders, six months of sample data — and never a condition: without a range
        fetcher, or with the provider down, it stores nothing and `resolve` does what it always did.
        """
        if base == quote or self._fetch_range is None or start > end:
            return 0
        published = self._fetch_range(base, quote, start, end)
        if not published:
            return 0
        for published_on, rate in published.items():
            db.merge(FxRate(base=base, quote=quote, rate_date=published_on, rate=rate, source="ecb"))
        db.flush()
        return len(published)

    def resolve(
        self,
        db: Session,
        base: str,
        quote: str,
        on_date: date,
        manual_rate: Decimal | None = None,
    ) -> ResolvedRate:
        if manual_rate is not None:
            if manual_rate <= 0:
                raise Unprocessable("fx_rate must be > 0", code="FX_RATE_INVALID")
            return ResolvedRate(manual_rate, on_date, "manual")
        if base == quote:
            return ResolvedRate(Decimal("1"), on_date, "same")
        row = db.get(FxRate, (base, quote, on_date))
        if row is not None:
            return ResolvedRate(row.rate, row.rate_date, row.source)
        # No quote for that very day. Before asking the provider, see whether the answer can only be
        # a rate we already hold: a Saturday can only get Friday's, an ETA five weeks out can only
        # get the last one published. Without this, every weekend-dated cost and every estimate
        # (dated at the ETA) is an HTTP call, and a provider outage turns them all into 422s.
        latest = self._latest_published(db, base, quote, on_date)
        anchor = min(on_date, last_publication_day(self._now()))
        if latest is not None and not _weekday_between(latest.rate_date, anchor):
            return ResolvedRate(latest.rate, latest.rate_date, "ecb_latest")
        fetched = self._fetch(base, quote, on_date)
        if fetched is None:
            # The provider is down. A date the ECB has not reached yet gets a stand-in whatever
            # happens, so a recent one we hold is as good as what we would have been told; a past
            # working day has a real quote somewhere, and guessing it would be written on the cost
            # for good — that one asks for a manual rate.
            if latest is not None and on_date > anchor and (anchor - latest.rate_date) <= STAND_IN_MAX_AGE:
                return ResolvedRate(latest.rate, latest.rate_date, "ecb_latest")
            raise Unprocessable(
                f"No {quote}→{base} rate available for {on_date.isoformat()}. Provide fx_rate manually.",
                code="FX_RATE_UNAVAILABLE",
                base=base,
                quote=quote,
                date=on_date.isoformat(),
            )
        rate, actual_date = fetched
        # Cached under the day the ECB actually published, never under the day we asked for. Asked
        # about a Saturday, or about an ETA five weeks out, the provider answers with the last
        # published rate and says so in its own `date`. Filing that answer under the requested day
        # would freeze it there for ever: every invoice really dated that day would then convert at a
        # five-week-old rate, labelled "ecb", indistinguishable from a real quote. On EUR/USD five
        # weeks is routinely 1 to 3 %, which is 300 to 900 € on a 30 000 $ container.
        db.merge(FxRate(base=base, quote=quote, rate_date=actual_date, rate=rate, source="ecb"))
        db.flush()
        # A cost dated in the future is legitimate — an estimate is dated at the ETA — so it gets the
        # latest published rate rather than a refusal, flagged as such on the row that used it.
        return ResolvedRate(rate, actual_date, "ecb" if actual_date == on_date else "ecb_latest")

    @staticmethod
    def _latest_published(db: Session, base: str, quote: str, on_date: date) -> FxRate | None:
        return db.scalars(
            select(FxRate)
            .where(
                FxRate.base == base,
                FxRate.quote == quote,
                FxRate.source == "ecb",
                FxRate.rate_date <= on_date,
            )
            .order_by(FxRate.rate_date.desc())
            .limit(1)
        ).first()


def last_publication_day(now: datetime) -> date:
    """The most recent day the ECB can have published a reference rate by `now`: working days only,
    and today only once the afternoon fixing is out. TARGET holidays are not modelled — on those few
    days the provider is simply asked, and answers with the previous day's rate."""
    local = now.astimezone(ECB_ZONE)
    day = local.date() if local.time() >= PUBLISHED_BY else local.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _weekday_between(after: date, until: date) -> bool:
    """Is there a working day in (after, until] — a day the ECB may have published on since `after`?"""
    day = after + timedelta(days=1)
    while day <= until:
        if day.weekday() < 5:
            return True
        day += timedelta(days=1)
    return False
