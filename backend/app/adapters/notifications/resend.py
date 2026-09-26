"""Resend, for when there is an API key.

Deliberately thin: one POST, no template engine, plain text. The digest is short by construction —
if it is long, something upstream is raising too many alerts, and that is the thing to fix.
"""

from __future__ import annotations

import logging

import httpx

from app.core.http import timeout as http_timeout
from app.domain.alerts.notifier import Notification
from app.domain.tracking.ports import ProviderUnavailable

logger = logging.getLogger(__name__)

#: One small POST. If it has not answered by now the digest waits for the next round rather than
#: holding the sender.
TIMEOUT_SECONDS = 15.0

BASE_URL = "https://api.resend.com"


class ResendNotifier:
    name = "resend"

    def __init__(
        self,
        api_key: str,
        from_email: str,
        *,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key
        self.from_email = from_email
        self.base_url = base_url.rstrip("/")
        self._client = client

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(base_url=self.base_url, timeout=http_timeout(TIMEOUT_SECONDS))

    def send(self, notification: Notification) -> None:
        if not notification.recipients:
            logger.warning("no recipient for org %s: digest not sent", notification.org_id)
            return
        payload = {
            "from": self.from_email,
            "to": notification.recipients,
            "subject": notification.subject,
            "text": notification.text(),
        }
        try:
            # The header goes on the request, not the client: an injected client (a test transport,
            # a shared pool) then needs to know nothing about credentials.
            response = self._http().post(
                "/emails", json=payload, headers={"Authorization": f"Bearer {self.api_key}"}
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Resend is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise ProviderUnavailable(f"Resend answered {response.status_code}: {response.text[:200]}")
        logger.info(
            "digest sent to %s recipient(s) for org %s", len(notification.recipients), notification.org_id
        )
