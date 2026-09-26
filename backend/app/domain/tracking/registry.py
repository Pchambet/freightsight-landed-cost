"""Which providers this deployment can talk to.

A provider is built from settings, so a missing credential is a configuration fact rather than a
crash: the Terminal49 adapter, for instance, verifies and parses webhooks with only its signing
secret, and refuses `subscribe` with a clear error until an API key exists.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from app.core.errors import NotFound
from app.core.settings import Settings, get_settings
from app.domain.tracking.manual import ManualProvider
from app.domain.tracking.ports import TrackingProvider


def _terminal49(settings: Settings) -> TrackingProvider:
    from app.adapters.tracking.terminal49 import Terminal49Provider

    return Terminal49Provider(
        settings.tracking_webhook_secret_terminal49,
        settings.terminal49_api_key,
        base_url=settings.terminal49_base_url,
        max_age=timedelta(seconds=settings.tracking_webhook_max_age_seconds),
    )


def _shipsgo(settings: Settings) -> TrackingProvider:
    from app.adapters.tracking.shipsgo import ShipsgoConnector

    return ShipsgoConnector(settings.shipsgo_api_key, settings.tracking_webhook_secret_shipsgo)


BUILDERS: dict[str, Callable[[Settings], TrackingProvider]] = {
    "manual": lambda _: ManualProvider(),
    "terminal49": _terminal49,
    "shipsgo": _shipsgo,
}


def get_provider(name: str, settings: Settings | None = None) -> TrackingProvider:
    builder = BUILDERS.get(name.lower())
    if builder is None:
        raise NotFound(f"Tracking provider {name!r}")
    return builder(settings or get_settings())


def provider_names() -> list[str]:
    return sorted(BUILDERS)
