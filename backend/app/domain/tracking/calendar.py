"""Which calendar a free day is counted in.

A terminal counts free days in its own working calendar, not in UTC. Counting in UTC is wrong by a
whole day whenever the discharge happens near local midnight — a night shift at Port 2000 discharging
at 01:30 CEST is stored as 23:30 the day before, and the customer is told demurrage started a day
early. One day is a truck sent in a panic, or a day of demurrage nobody saw coming.

The port's zone is derived from the UN/LOCODE, whose first two letters are the ISO country. A country
is not a time zone in general, but it is for every country in the trade lanes this product is sold
into, and the two places where it is not (the Spanish islands) are listed by port. Anything we cannot
place falls back to the organization's own zone and then to Europe/Paris: our customers are French
importers, and their forwarder's invoice is dated in Paris.

Carrying the provider's own offset per event would be better still — Shipsgo publishes the port's
IANA zone on each movement, Terminal49 on the port resource — but the offset is lost the moment the
event is stored in a `timestamptz`, so it would need a column on `tracking_events`.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

#: The zone this product assumes when it has nothing better. The customers are in Paris.
DEFAULT_ZONE = "Europe/Paris"

#: ISO country of the UN/LOCODE -> IANA zone, for the countries whose ports we actually see.
COUNTRY_ZONES: dict[str, str] = {
    "FR": "Europe/Paris",
    "BE": "Europe/Brussels",
    "NL": "Europe/Amsterdam",
    "DE": "Europe/Berlin",
    "ES": "Europe/Madrid",
    "IT": "Europe/Rome",
    "GB": "Europe/London",
    "CH": "Europe/Zurich",
}

#: The ports whose country zone would be wrong. Both Spanish island groups run an hour behind Madrid.
PORT_ZONES: dict[str, str] = {
    "ESLPA": "Atlantic/Canary",  # Las Palmas
    "ESSCT": "Atlantic/Canary",  # Santa Cruz de Tenerife
    "ESACE": "Atlantic/Canary",  # Arrecife
}


def zone_for(unlocode: str | None, fallback: str | None = None) -> ZoneInfo:
    """The port's calendar, from its UN/LOCODE. `fallback` is the organization's own zone, if it set one."""
    code = (unlocode or "").strip().upper()
    name = PORT_ZONES.get(code) or COUNTRY_ZONES.get(code[:2])
    if name is None:
        name = fallback or DEFAULT_ZONE
    try:
        return ZoneInfo(name)
    except Exception:
        # A zone name typed into a setting by hand. Counting days in Paris is wrong by an hour or
        # two somewhere; refusing to count them at all is wrong everywhere.
        return ZoneInfo(DEFAULT_ZONE)


def local_date(moment: datetime, zone: ZoneInfo) -> date:
    """The calendar day this instant falls on at the port."""
    return moment.astimezone(zone).date()
