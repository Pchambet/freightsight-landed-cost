"""The alert in the reader's language, written from its kind and its payload.

This is `frontend/src/features/alerts/text.ts` done server-side, for the e-mail digest. The screen
already builds its sentence from the kind and the moving parts; a digest that kept the stored English
would have the same alert reading one way in the application and another way in the inbox. The
sentences here are the ones in `frontend/messages/{fr,en}.json`, and a test compares the two so they
cannot drift apart quietly.

The alert's own `title` and `body` stay English and stay the fallback: a kind this module does not
know, or a payload that lacks what a sentence needs — an alert raised before the payload carried it
— is sent as it was stored rather than as a half-written phrase.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.alerts.codes import EMAIL_CHANNEL, MANUAL_SILENCE
from app.domain.models import Alert, AlertKind, AlertSeverity

#: French first: the product is sold to French importers. Same list as the front's i18n config.
LOCALES = ("fr", "en")
DEFAULT_LOCALE = "fr"

MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "DND_RISK.title": "{container} : risque de surestaries {risk}",
        "DND_RISK.titleRunning": "{container} : surestaries en cours",
        "DND_RISK.titleRunningAmount": "{container} : surestaries en cours, {amount}",
        "DND_RISK.titleDetention": "{container} : risque de détention {risk}",
        "DND_RISK.titleDetentionRunning": "{container} : détention en cours",
        "DND_RISK.titleDetentionRunningAmount": "{container} : détention en cours, {amount}",
        "DND_RISK.body": "Dernier jour franc le {lfd}.",
        "DND_RISK.bodyRunning": "Le dernier jour franc était le {lfd}. Les surestaries courent.",
        "DND_RISK.bodyRunningDays.one": (
            "Le dernier jour franc était le {lfd}. Les surestaries courent depuis {days} jour."
        ),
        "DND_RISK.bodyRunningDays.other": (
            "Le dernier jour franc était le {lfd}. Les surestaries courent depuis {days} jours."
        ),
        "DND_RISK.bodyLeft.one": "Dernier jour franc le {lfd}, {days} jour restant.",
        "DND_RISK.bodyLeft.other": "Dernier jour franc le {lfd}, {days} jours restants.",
        "DND_RISK.bodyNoLfd": "Dernier jour franc inconnu.",
        "DND_RISK.bodyDetention": "Retour du vide attendu le {deadline}.",
        "DND_RISK.bodyDetentionLeft.one": "Retour du vide attendu le {deadline}, {days} jour restant.",
        "DND_RISK.bodyDetentionLeft.other": "Retour du vide attendu le {deadline}, {days} jours restants.",
        "DND_RISK.bodyDetentionRunning": "Le vide devait être rendu le {deadline}. La détention court.",
        "DND_RISK.bodyDetentionRunningDays.one": (
            "Le vide devait être rendu le {deadline}. La détention court depuis {days} jour."
        ),
        "DND_RISK.bodyDetentionRunningDays.other": (
            "Le vide devait être rendu le {deadline}. La détention court depuis {days} jours."
        ),
        "DND_RISK.amount": "{amount} à ce jour, {rate} par jour.",
        "DND_RISK.noRate": "Ajoutez un tarif journalier dans vos réglages pour voir le montant.",
        "DND_RISK.portUnconfirmed": (
            "Port de déchargement non confirmé : vérifiez qu'il s'agit bien du port final."
        ),
        "ETA_CHANGED.title": "{container} : arrivée décalée de {hours} h {direction}",
        "ETA_CHANGED.later": "plus tard",
        "ETA_CHANGED.earlier": "plus tôt",
        "ETA_CHANGED.body": "Nouvelle ETA {eta}, au lieu de {previous}.",
        "ETA_CHANGED.bodyNoPrevious": "Nouvelle ETA {eta}.",
        "TRACKING_DATA_MISSING.title": "Aucune donnée de suivi pour {container}",
        "TRACKING_DATA_MISSING.body": (
            "{provider} n'a rien envoyé depuis au moins {hours} h alors qu'un événement était "
            "attendu. Vérifiez le conteneur sur le site du transporteur."
        ),
        "TRACKING_DATA_MISSING.bodyManual": (
            "{container} devait arriver le {eta} et aucun jalon n'a été saisi depuis. Saisissez le "
            "déchargement pour que nous puissions compter vos jours francs."
        ),
        "NOTIFICATION_FAILED.title": "Vos alertes ne partent plus par e-mail",
        "NOTIFICATION_FAILED.body": (
            "Nous n'avons pas réussi à envoyer votre récapitulatif après {attempts} tentatives "
            "({reason}). Vos alertes restent visibles ici et repartiront dès que l'envoi "
            "refonctionnera."
        ),
        "TRACKING_PROVIDER_DOWN.title": "{provider} ne répond pas",
        "TRACKING_PROVIDER_DOWN.body": (
            "Impossible de joindre {provider} pour plusieurs conteneurs d'affilée : les mises à jour "
            "de suivi sont en pause jusqu'à ce qu'il réponde. Les conteneurs eux-mêmes n'ont rien : "
            "rien à faire ici, le prochain passage reprendra où celui-ci s'est arrêté."
        ),
        "TRACKING_DELIVERY_FAILED.title": "{container} : une mise à jour de suivi n'a pas pu être traitée",
        "TRACKING_DELIVERY_FAILED.body.one": (
            "{provider} a envoyé une mise à jour le {received} que nous n'avons pas réussi à traiter "
            "après {attempts} tentative. Le suivi de ce conteneur peut être en retard. La donnée "
            "brute est conservée : rien n'est perdu, et elle peut être rejouée."
        ),
        "TRACKING_DELIVERY_FAILED.body.other": (
            "{provider} a envoyé une mise à jour le {received} que nous n'avons pas réussi à traiter "
            "après {attempts} tentatives. Le suivi de ce conteneur peut être en retard. La donnée "
            "brute est conservée : rien n'est perdu, et elle peut être rejouée."
        ),
        "risk.NONE": "Aucun risque",
        "risk.LOW": "Faible",
        "risk.MEDIUM": "Moyen",
        "risk.HIGH": "Élevé",
        "risk.INCURRING": "Surestaries en cours",
        # The digest itself has no equivalent on the screen: nobody reads an e-mail there.
        "severity.info": "Info",
        "severity.warning": "Attention",
        "severity.critical": "Critique",
        "digest.subjectOne": "FreightSight : {title}",
        "digest.subjectMany": "FreightSight : {count} alertes",
        "digest.headingOne": "{org} — {count} alerte",
        "digest.headingMany": "{org} — {count} alertes",
        "digest.footer": "Ouvrez FreightSight pour les marquer comme lues.",
    },
    "en": {
        "DND_RISK.title": "{container}: demurrage risk {risk}",
        "DND_RISK.titleRunning": "{container}: demurrage running",
        "DND_RISK.titleRunningAmount": "{container}: demurrage running, {amount}",
        "DND_RISK.titleDetention": "{container}: detention risk {risk}",
        "DND_RISK.titleDetentionRunning": "{container}: detention running",
        "DND_RISK.titleDetentionRunningAmount": "{container}: detention running, {amount}",
        "DND_RISK.body": "Last free day {lfd}.",
        "DND_RISK.bodyRunning": "The last free day was {lfd}. Demurrage is running.",
        "DND_RISK.bodyRunningDays.one": (
            "The last free day was {lfd}. Demurrage has been running for {days} day."
        ),
        "DND_RISK.bodyRunningDays.other": (
            "The last free day was {lfd}. Demurrage has been running for {days} days."
        ),
        "DND_RISK.bodyLeft.one": "Last free day {lfd}, {days} day left.",
        "DND_RISK.bodyLeft.other": "Last free day {lfd}, {days} days left.",
        "DND_RISK.bodyNoLfd": "Last free day unknown.",
        "DND_RISK.bodyDetention": "Empty due back {deadline}.",
        "DND_RISK.bodyDetentionLeft.one": "Empty due back {deadline}, {days} day left.",
        "DND_RISK.bodyDetentionLeft.other": "Empty due back {deadline}, {days} days left.",
        "DND_RISK.bodyDetentionRunning": "The empty was due back {deadline}. Detention is running.",
        "DND_RISK.bodyDetentionRunningDays.one": (
            "The empty was due back {deadline}. Detention has been running for {days} day."
        ),
        "DND_RISK.bodyDetentionRunningDays.other": (
            "The empty was due back {deadline}. Detention has been running for {days} days."
        ),
        "DND_RISK.amount": "{amount} so far, at {rate} per day.",
        "DND_RISK.noRate": "Add a daily rate in your settings to see the amount.",
        "DND_RISK.portUnconfirmed": ("Port of discharge not confirmed: check that this is the final port."),
        "ETA_CHANGED.title": "{container}: arrival moved {hours} h {direction}",
        "ETA_CHANGED.later": "later",
        "ETA_CHANGED.earlier": "earlier",
        "ETA_CHANGED.body": "New ETA {eta}, was {previous}.",
        "ETA_CHANGED.bodyNoPrevious": "New ETA {eta}.",
        "TRACKING_DATA_MISSING.title": "No tracking data for {container}",
        "TRACKING_DATA_MISSING.body": (
            "{provider} has sent nothing for at least {hours} h, and an event was due. "
            "Check the container on the carrier's own site."
        ),
        "TRACKING_DATA_MISSING.bodyManual": (
            "{container} was due on {eta} and no milestone has been entered since. Enter the "
            "discharge so we can count your free days."
        ),
        "NOTIFICATION_FAILED.title": "Your alerts are no longer being e-mailed",
        "NOTIFICATION_FAILED.body": (
            "We could not send your digest after {attempts} attempts ({reason}). Your alerts stay "
            "visible here and will be sent again as soon as e-mail works."
        ),
        "TRACKING_PROVIDER_DOWN.title": "{provider} is not answering",
        "TRACKING_PROVIDER_DOWN.body": (
            "We could not reach {provider} for several containers in a row, so tracking updates are "
            "paused until it answers again. Nothing is wrong with the containers themselves, and "
            "nothing needs doing here: the next round picks up where this one stopped."
        ),
        "TRACKING_DELIVERY_FAILED.title": "{container}: a tracking update could not be processed",
        "TRACKING_DELIVERY_FAILED.body.one": (
            "{provider} sent an update on {received} that we could not process after {attempts} "
            "attempt. This container's tracking may be behind. The raw delivery is kept: nothing is "
            "lost, and it can be replayed."
        ),
        "TRACKING_DELIVERY_FAILED.body.other": (
            "{provider} sent an update on {received} that we could not process after {attempts} "
            "attempts. This container's tracking may be behind. The raw delivery is kept: nothing is "
            "lost, and it can be replayed."
        ),
        "risk.NONE": "No risk",
        "risk.LOW": "Low",
        "risk.MEDIUM": "Medium",
        "risk.HIGH": "High",
        "risk.INCURRING": "Incurring",
        "severity.info": "Info",
        "severity.warning": "Warning",
        "severity.critical": "Critical",
        "digest.subjectOne": "FreightSight: {title}",
        "digest.subjectMany": "FreightSight: {count} alerts",
        "digest.headingOne": "{org} — {count} alert",
        "digest.headingMany": "{org} — {count} alerts",
        "digest.footer": "Open FreightSight to acknowledge these.",
    },
}

#: Short month names for English dates, so the sentence does not depend on the process locale.
_MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def normalise_locale(value: str | None) -> str:
    """The locale we speak that is closest to what was asked. `fr-CA` is French; `de` is not German."""
    if not value:
        return DEFAULT_LOCALE
    base = value.strip().lower().replace("_", "-").split("-")[0]
    return base if base in LOCALES else DEFAULT_LOCALE


def t(locale: str, key: str, **values: Any) -> str:
    template = MESSAGES[normalise_locale(locale)][key]
    return template.format(**values) if values else template


def severity_label(severity: AlertSeverity, locale: str) -> str:
    return t(locale, f"severity.{severity.value}")


def alert_text(
    alert: Alert,
    locale: str,
    *,
    container_number: str | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """The title and body to show a reader of `locale`, from the alert's kind and payload."""
    payload = alert.payload if isinstance(alert.payload, dict) else {}
    fallback = (alert.title, alert.body or "")
    container = container_number or ""

    if alert.kind is AlertKind.DND_RISK:
        risk = _str(payload, "risk")
        if not risk or not container:
            return fallback
        return _dnd_text(payload, locale, container=container, risk=risk, now=now)

    if alert.kind is AlertKind.ETA_CHANGED:
        hours = _number(payload, "delta_hours")
        eta = _datetime(_str(payload, "eta"))
        if hours is None or eta is None or not container:
            return fallback
        direction = t(locale, "ETA_CHANGED.later" if hours > 0 else "ETA_CHANGED.earlier")
        title = t(
            locale,
            "ETA_CHANGED.title",
            container=container,
            hours=abs(round(hours)),
            direction=direction,
        )
        previous = _datetime(_str(payload, "previous_eta"))
        if previous is None:
            return title, t(locale, "ETA_CHANGED.bodyNoPrevious", eta=_format_datetime(eta, locale))
        return title, t(
            locale,
            "ETA_CHANGED.body",
            eta=_format_datetime(eta, locale),
            previous=_format_datetime(previous, locale),
        )

    if alert.kind is AlertKind.TRACKING_DATA_MISSING:
        if not container:
            return fallback
        title = t(locale, "TRACKING_DATA_MISSING.title", container=container)
        if _str(payload, "reason") == MANUAL_SILENCE:
            eta = _datetime(_str(payload, "eta"))
            if eta is None:
                return fallback
            return title, t(
                locale,
                "TRACKING_DATA_MISSING.bodyManual",
                container=container,
                eta=_format_date(eta.astimezone(UTC).date(), locale),
            )
        provider = _str(payload, "provider")
        hours = _number(payload, "hours")
        if not provider or hours is None:
            return fallback
        return title, t(locale, "TRACKING_DATA_MISSING.body", provider=provider, hours=round(hours))

    if alert.kind is AlertKind.TRACKING_DELIVERY_FAILED:
        if _str(payload, "channel") == EMAIL_CHANNEL:
            attempts = _number(payload, "attempts")
            reason = _str(payload, "reason")
            if attempts is None or not reason:
                return fallback
            return (
                t(locale, "NOTIFICATION_FAILED.title"),
                t(locale, "NOTIFICATION_FAILED.body", attempts=round(attempts), reason=reason),
            )
        provider = _str(payload, "provider")
        attempts = _number(payload, "attempts")
        received = _datetime(_str(payload, "received_at"))
        if not provider or attempts is None or received is None or not container:
            return fallback
        tries = round(attempts)
        return (
            t(locale, "TRACKING_DELIVERY_FAILED.title", container=container),
            t(
                locale,
                _plural(locale, "TRACKING_DELIVERY_FAILED.body", tries),
                provider=provider,
                received=_format_datetime(received, locale),
                attempts=tries,
            ),
        )

    if alert.kind is AlertKind.TRACKING_PROVIDER_DOWN:
        provider = _str(payload, "provider")
        if not provider:
            return fallback
        return (
            t(locale, "TRACKING_PROVIDER_DOWN.title", provider=provider),
            t(locale, "TRACKING_PROVIDER_DOWN.body", provider=provider),
        )

    return fallback


def _dnd_text(
    payload: dict[str, Any],
    locale: str,
    *,
    container: str,
    risk: str,
    now: datetime | None,
) -> tuple[str, str]:
    """The demurrage or detention sentence, with the money in it when the organization has a rate.

    Two clocks share this alert kind because they are the same fact to the reader — "this box is
    costing you something" — and only one of them ever runs at a time. What changes is the noun and
    the date it names.
    """
    detention = _str(payload, "clock") == "detention"
    running = risk == "INCURRING"
    amount, rate = _amounts(payload, locale)
    date_key = "deadline" if detention else "lfd"
    when = _date(_str(payload, "deadline")) or _date(_str(payload, "last_free_day"))

    if running:
        suffix = "Amount" if amount else ""
        key = "titleDetentionRunning" if detention else "titleRunning"
        title = t(locale, f"DND_RISK.{key}{suffix}", container=container, amount=amount or "")
    else:
        key = "titleDetention" if detention else "title"
        title = t(locale, f"DND_RISK.{key}", container=container, risk=_risk_label(risk, locale).lower())
    if when is None:
        return title, t(locale, "DND_RISK.bodyNoLfd")
    printed = {date_key: _format_date(when, locale)}

    if running:
        elapsed = _number(payload, "days_elapsed")
        if elapsed is None:
            # Raised before the payload carried the count. Say the plain old sentence rather than a
            # half-written one; there is nothing to put a price on either.
            key = "bodyDetentionRunning" if detention else "bodyRunning"
            return title, _caveat(t(locale, f"DND_RISK.{key}", **printed), payload, locale)
        days = round(elapsed)
        stem = "DND_RISK.bodyDetentionRunningDays" if detention else "DND_RISK.bodyRunningDays"
        body = t(locale, _plural(locale, stem, days), days=days, **printed)
        money = (
            t(locale, "DND_RISK.amount", amount=amount, rate=rate)
            if amount and rate
            else t(locale, "DND_RISK.noRate")
        )
        return title, _caveat(f"{body} {money}", payload, locale)

    left = _number(payload, "days_left")
    days = round(left) if left is not None else (when - (now or datetime.now(UTC)).date()).days
    if days < 0:
        key = "bodyDetention" if detention else "body"
        return title, _caveat(t(locale, f"DND_RISK.{key}", **printed), payload, locale)
    stem = "DND_RISK.bodyDetentionLeft" if detention else "DND_RISK.bodyLeft"
    return title, _caveat(t(locale, _plural(locale, stem, days), days=days, **printed), payload, locale)


def _caveat(body: str, payload: dict[str, Any], locale: str) -> str:
    """Say it out loud when we had to guess which discharge was the real one."""
    if payload.get("port_of_discharge_confirmed") is False:
        return f"{body} {t(locale, 'DND_RISK.portUnconfirmed')}"
    return body


def _amounts(payload: dict[str, Any], locale: str) -> tuple[str | None, str | None]:
    """The running total and the daily rate, written out, or nothing when there is no rate card."""
    currency = _str(payload, "currency")
    total = _decimal(payload, "amount_to_date")
    rate = _decimal(payload, "daily_rate")
    if currency is None or total is None or rate is None:
        return None, None
    return _format_money(total, currency, locale), _format_money(rate, currency, locale)


def _risk_label(code: str, locale: str) -> str:
    key = f"risk.{code}"
    return MESSAGES[normalise_locale(locale)].get(key, code)


def _plural(locale: str, key: str, count: int) -> str:
    """French counts 0 and 1 as one; English counts only 1. Same rule as the ICU messages."""
    one = count == 1 or (normalise_locale(locale) == "fr" and count == 0)
    return f"{key}.one" if one else f"{key}.other"


def _str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if isinstance(value, bool):  # a bool is an int, and never a count of hours
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    """Money is read as a Decimal or not at all: a rate turned into a float is a wrong invoice."""
    value = payload.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


#: The three currencies a French importer's forwarder invoices in. Anything else prints its code.
_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def _format_money(amount: Decimal, currency: str, locale: str) -> str:
    """`1 440,00 €` in French, `€1,440.00` in English. Same convention as the screen."""
    whole, _, cents = f"{amount.quantize(Decimal('0.01')):f}".partition(".")
    sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    symbol = _SYMBOLS.get(currency.upper())
    if normalise_locale(locale) == "fr":
        # Non-breaking spaces, as French typography and the screen's own formatter both use: a line
        # break between "1 440" and "€" in an e-mail reads as two numbers.
        nbsp = "\N{NO-BREAK SPACE}"
        return f"{sign}{_grouped(digits, nbsp)},{cents}{nbsp}{symbol or currency.upper()}"
    grouped = f"{sign}{_grouped(digits, ',')}.{cents}"
    return f"{symbol}{grouped}" if symbol else f"{currency.upper()} {grouped}"


def _grouped(digits: str, separator: str) -> str:
    return (
        separator.join([digits[max(i - 3, 0) : i] for i in range(len(digits) % 3 or 3, len(digits) + 1, 3)])
        or digits
    )


def _date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _format_date(value: date, locale: str) -> str:
    if normalise_locale(locale) == "fr":
        return f"{value.day:02d}/{value.month:02d}/{value.year}"
    return f"{value.day:02d} {_MONTHS_EN[value.month - 1]} {value.year}"


def _format_datetime(value: datetime, locale: str) -> str:
    """Always UTC, like the screen: an ETA read in two time zones is two different facts."""
    utc = value.astimezone(UTC)
    stamp = f"{utc.day:02d}/{utc.month:02d}/{utc.year} {utc.hour:02d}:{utc.minute:02d}"
    return stamp if normalise_locale(locale) == "fr" else stamp.replace(" ", ", ", 1)
