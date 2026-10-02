"""The chain the product is sold on: a box lands, a clock starts, and somebody is told in time.

Every test here is a defect that would reach a user's inbox — or, worse, would not: an alert that
was never raised because the band reached its final value at ingestion, an alert said once and never
repeated while the money ran, free days counted in the wrong calendar, and a transhipment discharge
starting the clock for a box still at sea.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.alerts.service import raise_dnd_risk_alert
from app.domain.alerts.text import alert_text
from app.domain.models import (
    Alert,
    AlertKind,
    Container,
    CostScope,
    CostType,
    DndRisk,
    MilestoneCode,
    Organization,
    RateBasis,
    RateCard,
    Shipment,
    TrackingState,
)
from app.domain.tracking.calendar import zone_for
from app.domain.tracking.manual import manual_event
from app.domain.tracking.state import derive, derive_dnd
from app.jobs import handlers

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
#: French money is written with a non-breaking space, in the digest as on the screen.
NBSP = "\N{NO-BREAK SPACE}"


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def make_container(client: TestClient, number: str = "TCLU7654321") -> uuid.UUID:
    res = client.post("/api/v1/containers", json={"container_number": number})
    assert res.status_code == 201, res.text
    return uuid.UUID(res.json()["id"])


def rate_card(db: Session, org: Organization, cost_type: CostType, amount: str) -> None:
    db.add(
        RateCard(
            org_id=org.id,
            cost_type=cost_type,
            scope=CostScope.CONTAINER,
            basis=RateBasis.FLAT,
            amount=Decimal(amount),
            currency="EUR",
        )
    )
    db.commit()


def dnd_alerts(db: Session) -> list[Alert]:
    return list(db.scalars(select(Alert).where(Alert.kind == AlertKind.DND_RISK).order_by(Alert.created_at)))


# ------------------------------------------------------- a band that is already final at ingestion


def test_a_container_onboarded_past_its_last_free_day_is_alerted_immediately(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The onboarding scenario, and the one that used to be completely silent.

    A box discharged at Le Havre on the 10th with five free days is already incurring on the 17th.
    `refresh` wrote INCURRING and said nothing; the next morning's pass compared INCURRING with
    INCURRING, found no change, and moved on — for ever. The customer saw a red badge if they
    happened to look, and got no e-mail about the only box costing them money that day.
    """
    container_id = make_container(client)
    res = client.post(
        f"/api/v1/containers/{container_id}/events",
        json={"code": "DISCHARGED", "occurred_at": "2026-09-10T08:00:00Z"},
    )
    assert res.status_code == 201, res.text

    (alert,) = dnd_alerts(db)
    assert alert.payload["risk"] == "INCURRING"
    assert alert.payload["clock"] == "demurrage"
    assert alert.container_id == container_id


def test_saying_it_at_ingestion_and_again_at_dawn_is_still_one_alert(
    client: TestClient, db: Session, org: Organization
) -> None:
    """The dedup key is what makes calling this from everywhere safe, which is the whole argument
    for calling it from the ingestion path at all."""
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.discharged_at = NOW - timedelta(days=3)  # five free days: one left, HIGH
    db.commit()
    from app.domain.tracking.service import refresh

    refresh(db, org, container, now=NOW)
    db.commit()
    assert len(dnd_alerts(db)) == 1

    handlers.recompute_dnd_risk(db, now=NOW)
    assert len(dnd_alerts(db)) == 1


# ------------------------------------------------------------------- the reminder, and the amount


def test_a_running_clock_is_said_again_every_day_with_the_running_total(
    client: TestClient, db: Session, org: Organization
) -> None:
    """`dnd:{id}:INCURRING` was permanent: one alert, ever, on the alert that costs the most.

    A responsable logistique on holiday came back to a 1 440 € line on the forwarder's invoice and
    one e-mail about it, sent eight days earlier and buried."""
    rate_card(db, org, CostType.DEMURRAGE, "180.00")
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.discharged_at = NOW - timedelta(days=8)  # LFD was four days ago
    db.commit()

    handlers.recompute_dnd_risk(db, now=NOW)
    handlers.recompute_dnd_risk(db, now=NOW + timedelta(days=1))
    handlers.recompute_dnd_risk(db, now=NOW + timedelta(days=1, hours=1))  # twice the same day

    alerts = dnd_alerts(db)
    assert len(alerts) == 2, "one a day while it runs, and not one an hour"
    assert [a.payload["days_elapsed"] for a in alerts] == [4, 5]
    assert alerts[0].payload["amount_to_date"] == "720.00"
    assert alerts[0].payload["daily_rate"] == "180.0000"  # the rate card's own precision
    assert alerts[0].payload["currency"] == "EUR"

    title, body = alert_text(alerts[0], "fr", container_number="TCLU7654321", now=NOW)
    assert title == f"TCLU7654321 : surestaries en cours, 720,00{NBSP}€"
    assert body == (
        "Le dernier jour franc était le 13/09/2026. Les surestaries courent depuis 4 jours. "
        f"720,00{NBSP}€ à ce jour, 180,00{NBSP}€ par jour."
    )


def test_the_reminder_slows_to_weekly_then_stops(client: TestClient, db: Session, org: Organization) -> None:
    """A clock only stops on the empty return, which in manual tracking somebody has to type in and
    mostly nobody does: daily for ever would be a critical alert every morning for every box ever
    delivered. Daily for a fortnight, weekly until two months, then silence."""
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.discharged_at = NOW - timedelta(days=4 + 20)  # 20 days past the last free day
    db.commit()

    for day in range(7):  # one whole week of morning passes
        handlers.recompute_dnd_risk(db, now=NOW + timedelta(days=day))
    weekly = len(dnd_alerts(db))
    assert 1 <= weekly <= 2, "seven mornings inside at most two ISO weeks"

    container.discharged_at = NOW - timedelta(days=4 + 90)
    db.commit()
    handlers.recompute_dnd_risk(db, now=NOW + timedelta(days=30))
    assert len(dnd_alerts(db)) == weekly, "past two months it is a data-entry gap, not an alarm"


def test_without_a_rate_card_the_days_are_said_and_the_setting_is_pointed_at(
    client: TestClient, db: Session, org: Organization
) -> None:
    """A made-up daily rate on an alert that says "this is costing you money" is how you lose the
    right to say it. The days are a fact; the amount is not, until they tell us their rate."""
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.discharged_at = NOW - timedelta(days=8)
    db.commit()

    handlers.recompute_dnd_risk(db, now=NOW)
    (alert,) = dnd_alerts(db)
    assert "amount_to_date" not in alert.payload

    title, body = alert_text(alert, "fr", container_number="TCLU7654321", now=NOW)
    assert title == "TCLU7654321 : surestaries en cours"
    assert body == (
        "Le dernier jour franc était le 13/09/2026. Les surestaries courent depuis 4 jours. "
        "Ajoutez un tarif journalier dans vos réglages pour voir le montant."
    )


def test_detention_has_its_own_words_and_its_own_rate(
    client: TestClient, db: Session, org: Organization
) -> None:
    rate_card(db, org, CostType.DETENTION, "120.00")
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.discharged_at = NOW - timedelta(days=20)
    container.gate_out_at = NOW - timedelta(days=9)  # seven free days: three days over
    db.commit()

    handlers.recompute_dnd_risk(db, now=NOW)
    (alert,) = dnd_alerts(db)
    assert alert.payload["clock"] == "detention"
    assert alert.payload["amount_to_date"] == "360.00"

    title, body = alert_text(alert, "fr", container_number="TCLU7654321", now=NOW)
    assert title == f"TCLU7654321 : détention en cours, 360,00{NBSP}€"
    assert body.startswith("Le vide devait être rendu le 14/09/2026. La détention court depuis 3 jours.")


# ------------------------------------------------------------------------ the calendar of the port


_SERIAL = itertools.count(1)


def _landed(db: Session, org: Organization, at: datetime, pod: str | None) -> Container:
    """One container discharged at `at`, on a shipment bound for `pod`. The shipment is the point:
    it is where the port of destination lives, and therefore which calendar the days are counted in."""
    serial = next(_SERIAL)
    shipment = Shipment(org_id=org.id, reference=f"BL-{serial}", destination_unlocode=pod)
    db.add(shipment)
    db.flush()
    container = Container(
        org_id=org.id,
        container_number=f"MSCU{serial:07d}",
        shipment_id=shipment.id,
        discharged_at=at,
    )
    db.add(container)
    db.flush()
    return container


def test_free_days_are_counted_in_the_ports_calendar_not_in_utc(db: Session, org: Organization) -> None:
    """A night shift at Port 2000 is ordinary. Discharge at 01:30 CEST on 1 July is stored as
    23:30 UTC on 30 June, and counting in UTC moved the last free day a day earlier — so the
    customer was told demurrage had started while a free day was still left, and sent a truck."""
    org.free_days_demurrage = 5
    container = _landed(db, org, datetime(2026, 6, 30, 23, 30, tzinfo=UTC), "FRLEH")

    derive_dnd(container, org, datetime(2026, 7, 1, 10, 0, tzinfo=UTC))

    assert container.last_free_day is not None
    assert container.last_free_day.isoformat() == "2026-07-05"  # 1 July local + 5 - 1


def test_the_same_instant_counts_differently_in_two_ports(db: Session, org: Organization) -> None:
    org.free_days_demurrage = 5
    instant = datetime(2026, 6, 30, 23, 30, tzinfo=UTC)
    rotterdam = _landed(db, org, instant, "NLRTM")  # 01:30 on 1 July, CEST
    london = _landed(db, org, instant, "GBLON")  # 00:30 on 1 July, BST

    derive_dnd(rotterdam, org, instant)
    derive_dnd(london, org, instant)
    assert rotterdam.last_free_day == london.last_free_day

    unknown = _landed(db, org, instant, None)  # falls back to Europe/Paris, like the customer
    derive_dnd(unknown, org, instant)
    assert unknown.last_free_day == rotterdam.last_free_day


@pytest.mark.parametrize(
    "instant,expected",
    [
        # The spring change: 02:00 becomes 03:00 in Paris on 29 March 2026.
        (datetime(2026, 3, 28, 23, 30, tzinfo=UTC), "2026-04-02"),  # 00:30 on 29 March, CET
        (datetime(2026, 3, 29, 1, 30, tzinfo=UTC), "2026-04-02"),  # 03:30 on 29 March, CEST
        # The autumn change: 03:00 becomes 02:00 on 25 October 2026.
        (datetime(2026, 10, 24, 23, 30, tzinfo=UTC), "2026-10-29"),  # 01:30 on 25 October, CEST
        (datetime(2026, 10, 25, 1, 30, tzinfo=UTC), "2026-10-29"),  # 02:30 on 25 October, CET
    ],
)
def test_the_two_clock_changes_do_not_move_a_last_free_day(
    db: Session, org: Organization, instant: datetime, expected: str
) -> None:
    """Both sides of both changes land on the same local day, so they must produce the same date.
    This is the test a `timedelta` on a naive datetime fails."""
    org.free_days_demurrage = 5
    container = _landed(db, org, instant, "FRLEH")
    derive_dnd(container, org, instant)
    assert container.last_free_day is not None
    assert container.last_free_day.isoformat() == expected


def test_a_port_we_cannot_place_is_counted_in_paris() -> None:
    assert zone_for("FRLEH").key == "Europe/Paris"
    assert zone_for("NLRTM").key == "Europe/Amsterdam"
    assert zone_for("ESLPA").key == "Atlantic/Canary"  # the country zone would be an hour out
    assert zone_for("CNNGB").key == "Europe/Paris"  # not a destination we count days at
    assert zone_for(None, "Europe/Lisbon").key == "Europe/Lisbon"  # the organization's own setting
    assert zone_for(None, "Mars/Olympus").key == "Europe/Paris"  # typed by hand, and wrong


# ---------------------------------------------------------------------------- transhipment


def at(days: float) -> datetime:
    return NOW + timedelta(days=days)


def test_a_transhipment_discharge_does_not_start_the_clock() -> None:
    """Ningbo to Le Havre via Tanger Med. The provider publishes a discharge at Tanger on the 12th;
    the box is still at sea. Starting the clock there told the customer demurrage was running on a
    container two weeks from land — and, because the first discharge won, the real Le Havre one
    never corrected it."""
    events = [
        manual_event(MilestoneCode.VESSEL_DEPARTED, at(-20), location_unlocode="CNNGB"),
        manual_event(MilestoneCode.DISCHARGED, at(-5), location_unlocode="MAPTM"),
    ]
    snapshot = derive(events, NOW, destination_unlocode="FRLEH")
    assert snapshot.discharged_at is None
    assert snapshot.milestone.value == "VESSEL_DEPARTED"

    arrived = [*events, manual_event(MilestoneCode.DISCHARGED, at(-1), location_unlocode="FRLEH")]
    real = derive(arrived, NOW, destination_unlocode="FRLEH")
    assert real.discharged_at == at(-1)
    assert real.discharge_port_confirmed is True


def test_without_a_known_destination_the_last_discharge_wins_and_says_it_is_unsure() -> None:
    events = [
        manual_event(MilestoneCode.DISCHARGED, at(-5), location_unlocode="MAPTM"),
        manual_event(MilestoneCode.DISCHARGED, at(-1), location_unlocode="FRLEH"),
    ]
    snapshot = derive(events, NOW)
    assert snapshot.discharged_at == at(-1)
    assert snapshot.discharge_port_confirmed is False


def test_a_single_discharge_typed_by_hand_is_not_flagged_as_a_guess() -> None:
    """The ordinary manual case: one discharge, no port, nothing ambiguous about it."""
    snapshot = derive([manual_event(MilestoneCode.DISCHARGED, at(-1))], NOW)
    assert snapshot.discharged_at == at(-1)
    assert snapshot.discharge_port_confirmed is True


def test_the_uncertainty_reaches_the_sentence(db: Session, org: Organization) -> None:
    container = _landed(db, org, at(-8), None)
    derive_dnd(container, org, NOW)
    alert = raise_dnd_risk_alert(db, org, container, NOW, discharge_confirmed=False)
    assert alert is not None
    assert alert.payload["port_of_discharge_confirmed"] is False
    body = alert_text(alert, "fr", container_number=container.container_number, now=NOW)[1]
    assert body.endswith("Port de déchargement non confirmé : vérifiez qu'il s'agit bien du port final.")


# ---------------------------------------------------------------------------- nobody typing


def test_an_organization_on_manual_tracking_is_asked_once_a_week(
    client: TestClient, db: Session, org: Organization
) -> None:
    """No provider account, so no guard rail at all: forget the discharge and the risk stays NONE,
    the daily pass skips the box, and the screen is green until the forwarder's invoice arrives."""
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.eta = NOW - timedelta(days=5)
    container.tracking_state = TrackingState.MANUAL
    db.commit()

    assert handlers.remind_manual_tracking(db, now=NOW) == 1
    assert handlers.remind_manual_tracking(db, now=NOW + timedelta(days=2)) == 0  # same week
    assert handlers.remind_manual_tracking(db, now=NOW + timedelta(days=8)) == 1

    alert = db.scalars(
        select(Alert).where(Alert.kind == AlertKind.TRACKING_DATA_MISSING).order_by(Alert.created_at)
    ).first()
    assert alert is not None and alert.severity.value == "info"
    body = alert_text(alert, "fr", container_number="TCLU7654321", now=NOW)[1]
    assert body.startswith("TCLU7654321 devait arriver le 12/09/2026")
    assert "Saisissez le déchargement" in body


def test_a_box_whose_eta_is_two_months_old_is_history_not_a_reminder(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Like the demurrage reminders, silent after two months: a quarter imported for an audit is
    history, and a weekly note for each of its boxes would bury the ones that matter."""
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.eta = NOW - timedelta(days=61)
    container.tracking_state = TrackingState.MANUAL
    db.commit()
    assert handlers.remind_manual_tracking(db, now=NOW) == 0
    container.eta = NOW - timedelta(days=59)
    db.commit()
    assert handlers.remind_manual_tracking(db, now=NOW) == 1


def test_a_container_whose_discharge_was_entered_is_left_in_peace(
    client: TestClient, db: Session, org: Organization
) -> None:
    container_id = make_container(client)
    container = db.get(Container, container_id)
    assert container is not None
    container.eta = NOW - timedelta(days=5)
    container.discharged_at = NOW - timedelta(days=4)
    container.dnd_risk = DndRisk.LOW
    container.tracking_state = TrackingState.MANUAL
    db.commit()

    assert handlers.remind_manual_tracking(db, now=NOW) == 0
