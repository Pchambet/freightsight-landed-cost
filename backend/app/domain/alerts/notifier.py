"""The notification port: how an alert leaves the application.

Alerts are grouped before they are sent — one message per organization per round, never one per
alert — so the port takes a batch. A digest of four containers is one thing a person reads; four
e-mails in a minute is what makes people filter you into a folder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.domain.alerts.text import DEFAULT_LOCALE, alert_text, normalise_locale, severity_label, t
from app.domain.models import Alert


@dataclass(frozen=True)
class Notification:
    org_id: UUID
    org_name: str
    recipients: list[str]
    alerts: list[Alert]
    #: The organization's language. The digest is written in it, exactly as the screen would.
    locale: str = DEFAULT_LOCALE
    #: Container number per container id, so a sentence can name the box without a query per alert.
    container_numbers: dict[UUID, str] = field(default_factory=dict)

    def _written(self, alert: Alert) -> tuple[str, str]:
        number = self.container_numbers.get(alert.container_id) if alert.container_id else None
        return alert_text(alert, self.locale, container_number=number)

    @property
    def subject(self) -> str:
        locale = normalise_locale(self.locale)
        if len(self.alerts) == 1:
            return t(locale, "digest.subjectOne", title=self._written(self.alerts[0])[0])
        return t(locale, "digest.subjectMany", count=len(self.alerts))

    def text(self) -> str:
        locale = normalise_locale(self.locale)
        count = len(self.alerts)
        heading = "digest.headingOne" if count == 1 else "digest.headingMany"
        lines = [t(locale, heading, org=self.org_name, count=count), ""]
        for alert in self.alerts:
            title, body = self._written(alert)
            lines.append(f"[{severity_label(alert.severity, locale)}] {title}")
            if body:
                lines.append(f"    {body}")
        lines += ["", t(locale, "digest.footer")]
        return "\n".join(lines)


@runtime_checkable
class Notifier(Protocol):
    name: str

    def send(self, notification: Notification) -> None:
        """Deliver the digest, or raise. Raising means the alerts stay unnotified and are retried."""
        ...


def make_notifier() -> Notifier:
    """The notifier this deployment can actually use: Resend when it has a key, the log otherwise."""
    from app.adapters.notifications.log_notifier import LogNotifier
    from app.adapters.notifications.resend import ResendNotifier
    from app.core.settings import get_settings

    settings = get_settings()
    if settings.resend_api_key and settings.alerts_from_email:
        return ResendNotifier(
            settings.resend_api_key, settings.alerts_from_email, base_url=settings.resend_base_url
        )
    return LogNotifier()
