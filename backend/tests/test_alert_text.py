"""Writing an alert in the reader's language, and keeping the e-mail's words identical to the screen's.

The drift this file exists to prevent is quiet: someone rewords the demurrage alert in
`frontend/messages/fr.json`, the screen changes, and the 6am e-mail keeps saying the old thing for
months because nothing fails.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.domain.alerts.text import LOCALES, MESSAGES, alert_text, normalise_locale
from app.domain.models import Alert, AlertKind, AlertSeverity

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
MESSAGES_DIR = Path(__file__).resolve().parents[2] / "frontend" / "messages"


def alert(
    kind: AlertKind,
    payload: dict[str, Any],
    *,
    title: str = "stored title",
    body: str = "stored body",
) -> Alert:
    return Alert(
        org_id=uuid.uuid4(),
        kind=kind,
        severity=AlertSeverity.WARNING,
        title=title,
        body=body,
        dedup_key="k",
        payload=payload,
        created_at=NOW,
    )


# ---------------------------------------------------------------------------- the sentences


def test_the_demurrage_alert_counts_the_days_left_in_both_languages() -> None:
    a = alert(AlertKind.DND_RISK, {"risk": "HIGH", "last_free_day": "2026-09-13"})
    title, body = alert_text(a, "fr", container_number="MSCU4821990", now=NOW)
    assert title == "MSCU4821990 : risque de surestaries élevé"
    assert body == "Dernier jour franc le 13/09/2026, 1 jour restant."

    title, body = alert_text(a, "en", container_number="MSCU4821990", now=NOW)
    assert title == "MSCU4821990: demurrage risk high"
    assert body == "Last free day 13 Sep 2026, 1 day left."


def test_the_last_free_day_today_is_zero_days_left_not_a_missing_sentence() -> None:
    """French counts zero as singular, English does not — the ICU rule the screen uses."""
    a = alert(AlertKind.DND_RISK, {"risk": "HIGH", "last_free_day": "2026-09-12"})
    fr = alert_text(a, "fr", container_number="C", now=NOW)[1]
    assert fr == "Dernier jour franc le 12/09/2026, 0 jour restant."
    assert alert_text(a, "en", container_number="C", now=NOW)[1] == "Last free day 12 Sep 2026, 0 days left."


def test_demurrage_already_running_says_so_rather_than_counting_backwards() -> None:
    a = alert(AlertKind.DND_RISK, {"risk": "INCURRING", "last_free_day": "2026-09-01"})
    assert alert_text(a, "fr", container_number="C", now=NOW) == (
        "C : surestaries en cours",
        "Le dernier jour franc était le 01/09/2026. Les surestaries courent.",
    )


def test_a_day_already_past_without_demurrage_running_only_states_it() -> None:
    a = alert(AlertKind.DND_RISK, {"risk": "HIGH", "last_free_day": "2026-09-01"})
    assert alert_text(a, "fr", container_number="C", now=NOW)[1] == "Dernier jour franc le 01/09/2026."


def test_an_eta_that_moved_is_read_in_utc_whoever_reads_it() -> None:
    a = alert(
        AlertKind.ETA_CHANGED,
        {
            "delta_hours": "18",
            "eta": "2026-09-20T06:30:00+00:00",
            "previous_eta": "2026-09-19T12:30:00+00:00",
        },
    )
    assert alert_text(a, "fr", container_number="MSCU4821990", now=NOW) == (
        "MSCU4821990 : arrivée décalée de 18 h plus tard",
        "Nouvelle ETA 20/09/2026 06:30, au lieu de 19/09/2026 12:30.",
    )
    title, body = alert_text(a, "en", container_number="MSCU4821990", now=NOW)
    assert title == "MSCU4821990: arrival moved 18 h later"
    assert body == "New ETA 20/09/2026, 06:30, was 19/09/2026, 12:30."


def test_an_eta_given_in_another_time_zone_is_shown_in_utc() -> None:
    a = alert(AlertKind.ETA_CHANGED, {"delta_hours": -6, "eta": "2026-09-20T08:30:00+02:00"})
    assert alert_text(a, "fr", container_number="C", now=NOW) == (
        "C : arrivée décalée de 6 h plus tôt",
        "Nouvelle ETA 20/09/2026 06:30.",
    )


def test_the_provider_outage_never_mentions_a_container() -> None:
    a = alert(AlertKind.TRACKING_PROVIDER_DOWN, {"provider": "shipsgo"})
    title, body = alert_text(a, "fr", now=NOW)
    assert title == "shipsgo ne répond pas"
    assert "Les conteneurs eux-mêmes n'ont rien" in body


def test_a_quiet_container_names_its_provider_and_the_hours() -> None:
    a = alert(AlertKind.TRACKING_DATA_MISSING, {"provider": "shipsgo", "hours": 48})
    assert alert_text(a, "fr", container_number="MSCU4821990", now=NOW) == (
        "Aucune donnée de suivi pour MSCU4821990",
        "shipsgo n'a rien envoyé depuis au moins 48 h alors qu'un événement était attendu. "
        "Vérifiez le conteneur sur le site du transporteur.",
    )


def test_a_delivery_we_gave_up_on_says_when_it_arrived_and_how_often_we_tried() -> None:
    a = alert(
        AlertKind.TRACKING_DELIVERY_FAILED,
        {"provider": "shipsgo", "attempts": 5, "received_at": "2026-09-12T08:05:00+00:00"},
    )
    title, body = alert_text(a, "fr", container_number="MSCU4821990", now=NOW)
    assert title == "MSCU4821990 : une mise à jour de suivi n'a pas pu être traitée"
    assert body.startswith("shipsgo a envoyé une mise à jour le 12/09/2026 08:05")
    assert "après 5 tentatives" in body
    assert "rien n'est perdu" in body

    title, body = alert_text(a, "en", container_number="MSCU4821990", now=NOW)
    assert title == "MSCU4821990: a tracking update could not be processed"
    assert "on 12/09/2026, 08:05" in body and "after 5 attempts" in body


def test_a_single_attempt_is_not_written_as_a_plural() -> None:
    a = alert(
        AlertKind.TRACKING_DELIVERY_FAILED,
        {"provider": "shipsgo", "attempts": 1, "received_at": "2026-09-12T08:05:00+00:00"},
    )
    assert "après 1 tentative." in alert_text(a, "fr", container_number="C", now=NOW)[1]
    assert "after 1 attempt." in alert_text(a, "en", container_number="C", now=NOW)[1]


# ---------------------------------------------------------------------------- when it must not try


@pytest.mark.parametrize(
    "kind,payload",
    [
        (AlertKind.DND_RISK, {"last_free_day": "2026-09-20"}),  # no risk code
        (AlertKind.ETA_CHANGED, {"eta": "2026-09-20T06:30:00+00:00"}),  # no delta
        (AlertKind.ETA_CHANGED, {"delta_hours": "18", "eta": "not a date"}),
        (AlertKind.TRACKING_DATA_MISSING, {"provider": "shipsgo"}),  # no hours
        (AlertKind.TRACKING_PROVIDER_DOWN, {}),  # no provider
        (AlertKind.TRACKING_DELIVERY_FAILED, {"provider": "shipsgo"}),  # no attempts, no date
    ],
)
def test_a_payload_missing_what_the_sentence_needs_falls_back_to_the_stored_words(
    kind: AlertKind, payload: dict[str, Any]
) -> None:
    written = alert_text(alert(kind, payload), "fr", container_number="C", now=NOW)
    assert written == ("stored title", "stored body")


def test_an_alert_about_a_container_we_cannot_name_is_not_half_written() -> None:
    a = alert(AlertKind.DND_RISK, {"risk": "HIGH", "last_free_day": "2026-09-20"})
    assert alert_text(a, "fr", container_number=None, now=NOW) == ("stored title", "stored body")


def test_a_language_we_do_not_speak_is_read_as_french() -> None:
    assert normalise_locale("de") == "fr"
    assert normalise_locale(None) == "fr"
    assert normalise_locale("fr-CA") == "fr"
    assert normalise_locale("EN-GB") == "en"


# ---------------------------------------------------------------------------- the screen's own words

PLURAL = re.compile(r"\{(\w+), plural, one \{([^}]*)\} other \{([^}]*)\}\}")


def icu(message: str, form: str) -> str:
    """The one/other branch of an ICU plural, written the way this module stores it."""

    def pick(match: re.Match[str]) -> str:
        name, one, other = match.groups()
        return (one if form == "one" else other).replace("#", "{" + name + "}")

    return PLURAL.sub(pick, message)


@pytest.mark.skipif(not MESSAGES_DIR.exists(), reason="the front's messages are not in this checkout")
@pytest.mark.parametrize("locale", LOCALES)
def test_the_email_says_exactly_what_the_screen_says(locale: str) -> None:
    front = json.loads((MESSAGES_DIR / f"{locale}.json").read_text())
    ours = MESSAGES[locale]
    for kind, block in front["alerts"]["text"].items():
        for key, value in block.items():
            if PLURAL.search(value):
                assert ours[f"{kind}.{key}.one"] == icu(value, "one"), f"{locale} {kind}.{key} one"
                assert ours[f"{kind}.{key}.other"] == icu(value, "other"), f"{locale} {kind}.{key} other"
            else:
                assert ours[f"{kind}.{key}"] == value, f"{locale} {kind}.{key}"
    for code, label in front["domain"]["risk"].items():
        assert ours[f"risk.{code}"] == label, f"{locale} risk.{code}"
