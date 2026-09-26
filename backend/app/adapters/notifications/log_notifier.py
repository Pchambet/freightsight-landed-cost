"""The notifier a deployment without an e-mail provider gets: it writes the digest to the logs.

It is not a no-op on purpose — the alerts are still marked as notified afterwards. Otherwise the day
a real API key is added, the first round would mail out the entire backlog.
"""

from __future__ import annotations

import logging

from app.domain.alerts.notifier import Notification

logger = logging.getLogger("freightsight.alerts")


class LogNotifier:
    name = "log"

    def send(self, notification: Notification) -> None:
        logger.info(
            "alert digest for %s (%s alerts, recipients: %s)\n%s",
            notification.org_name,
            len(notification.alerts),
            ", ".join(notification.recipients) or "nobody",
            notification.text(),
        )
