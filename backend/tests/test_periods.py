"""Closing a month: the frozen figure never moves again, and what moved since is a number of its own."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.auth import Principal, get_principal
from app.domain.models import MemberRole

MARCH, APRIL = "2026-03", "2026-04"
ORDER_LINE = {"line_no": 1, "sku": "A", "description": "Article A", "quantity": "100", "unit_price": "10"}


def cost(client: TestClient, container_id: str, cost_type: str, amount: str, day: str) -> dict[str, Any]:
    res = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": container_id, "cost_type": cost_type, "amount": amount,
              "currency": "EUR", "cost_date": day},
    )  # fmt: skip
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


@pytest.fixture
def two_months(client: TestClient) -> dict[str, str]:
    """One order of 100 units at 10 €, half in a container landed in March, half in one landed in April."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": "PO-1", "currency": "EUR", "lines": [ORDER_LINE]},
    )
    assert po.status_code == 201, po.text
    line = po.json()["lines"][0]["id"]
    boxes = {}
    for number, landed in (("MSCU4821990", "2026-03-10T08:00:00Z"), ("TGHU7245081", "2026-04-12T08:00:00Z")):
        box = client.post("/api/v1/containers", json={"container_number": number}).json()
        patched = client.patch(
            f"/api/v1/containers/{box['id']}", json={"discharged_at": landed, "milestone": "DISCHARGED"}
        )
        assert patched.status_code == 200, patched.text
        loads = [{"po_line_id": line, "quantity": "50"}]
        assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
        boxes[number] = box["id"]
    cost(client, boxes["MSCU4821990"], "OCEAN_FREIGHT", "200.00", "2026-03-12")
    cost(client, boxes["TGHU7245081"], "OCEAN_FREIGHT", "300.00", "2026-04-14")
    return boxes


def periods(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get("/api/v1/periods").json()
    return {p["period"]: p for p in body["periods"]}


def test_every_month_a_container_arrived_in_is_listed_newest_first_with_todays_figures(
    client: TestClient, two_months: dict[str, str]
) -> None:
    body = client.get("/api/v1/periods").json()
    assert [p["period"] for p in body["periods"]] == [APRIL, MARCH]
    march = body["periods"][1]
    assert (march["status"], march["containers"], march["fob"], march["landed"]) == (
        "open",
        1,
        "500.00",
        "700.00",
    )
    assert march["coefficient"] == "1.4000" and march["by_cost_type"] == {"OCEAN_FREIGHT": "200.00"}
    assert march["drift"] is None and march["closed_at"] is None
    report = client.get(
        "/api/v1/reports/landed-cost", params={"period_from": "2026-03-01", "period_to": "2026-03-31"}
    ).json()
    assert report["totals"]["landed"] == march["landed"]  # the same month as every report


def test_an_open_month_says_what_is_better_settled_first(
    client: TestClient, two_months: dict[str, str]
) -> None:
    estimate = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": two_months["MSCU4821990"], "cost_type": "THC",
              "amount": "250.00", "currency": "EUR", "cost_date": "2026-03-12", "status": "ESTIMATE"},
    )  # fmt: skip
    assert estimate.status_code == 201, estimate.text
    detail = client.get(f"/api/v1/periods/{MARCH}").json()
    assert detail["drift_lines"] == []
    ready = detail["readiness"]
    assert ready["estimated_total"] == "250.00"
    assert [(b["container_number"], b["estimated"]) for b in ready["containers_with_estimates"]] == [
        ("MSCU4821990", "250.00")
    ]
    assert ready["containers_without_cost"] == 0


def test_a_closed_month_is_frozen_and_a_late_invoice_is_a_figure_of_its_own(
    client: TestClient, two_months: dict[str, str]
) -> None:
    closed = client.post(f"/api/v1/periods/{MARCH}/close")
    assert closed.status_code == 201, closed.text
    assert (closed.json()["status"], closed.json()["drift"], closed.json()["drift_containers"]) == (
        "closed",
        "0.00",
        0,
    )
    assert client.post(f"/api/v1/periods/{MARCH}/close").json()["code"] == "PERIOD_ALREADY_CLOSED"

    late = cost(client, two_months["MSCU4821990"], "DRAYAGE", "120.00", "2026-05-02")
    march = periods(client)[MARCH]
    assert (march["landed"], march["drift"], march["drift_containers"]) == ("700.00", "120.00", 1)
    assert periods(client)[APRIL]["status"] == "open"

    detail = client.get(f"/api/v1/periods/{MARCH}").json()
    assert detail["readiness"] is None
    (line,) = detail["drift_lines"]
    assert (line["container_number"], line["reason"]) == ("MSCU4821990", "costs")
    assert (line["frozen_landed"], line["live_landed"], line["difference"]) == ("700.00", "820.00", "120.00")
    assert [(c["cost_id"], c["cost_type"], c["amount_base"]) for c in line["costs_changed"]] == [
        (late["id"], "DRAYAGE", "120.00")
    ]

    box = client.get(f"/api/v1/containers/{two_months['MSCU4821990']}").json()
    assert (box["closed_period"], box["frozen_landed"], box["drift"]) == (MARCH, "700.00", "120.00")
    other = client.get(f"/api/v1/containers/{two_months['TGHU7245081']}").json()
    assert (other["closed_period"], other["frozen_landed"], other["drift"]) == (None, None, None)
    listed = {c["container_number"]: c["closed_period"] for c in client.get("/api/v1/containers").json()}
    assert listed == {"MSCU4821990": MARCH, "TGHU7245081": None}


def test_a_cost_deleted_after_the_close_shows_as_a_negative_drift(
    client: TestClient, two_months: dict[str, str]
) -> None:
    freight = next(c for c in client.get("/api/v1/costs").json() if c["amount"] == "200.00")
    client.post(f"/api/v1/periods/{MARCH}/close")
    assert client.delete(f"/api/v1/costs/{freight['id']}").status_code == 204
    (line,) = client.get(f"/api/v1/periods/{MARCH}").json()["drift_lines"]
    assert (line["difference"], line["costs_deleted"], line["costs_changed"]) == ("-200.00", 1, [])


def test_a_container_whose_arrival_date_changes_leaves_one_closed_month_and_joins_another(
    client: TestClient, two_months: dict[str, str]
) -> None:
    client.post(f"/api/v1/periods/{MARCH}/close")
    client.post(f"/api/v1/periods/{APRIL}/close")
    moved = client.patch(
        f"/api/v1/containers/{two_months['MSCU4821990']}", json={"discharged_at": "2026-04-02T08:00:00Z"}
    )
    assert moved.status_code == 200, moved.text
    (left,) = client.get(f"/api/v1/periods/{MARCH}").json()["drift_lines"]
    assert (left["reason"], left["frozen_landed"], left["live_landed"]) == ("left_period", "700.00", "0.00")
    (joined,) = client.get(f"/api/v1/periods/{APRIL}").json()["drift_lines"]
    assert (joined["reason"], joined["frozen_landed"], joined["difference"]) == (
        "joined_period",
        "0.00",
        "700.00",
    )
    # frozen where it was frozen, whatever month it lands in today
    assert client.get(f"/api/v1/containers/{two_months['MSCU4821990']}").json()["closed_period"] == MARCH


def test_a_month_that_has_not_started_or_saw_nothing_arrive_cannot_be_closed(
    client: TestClient, two_months: dict[str, str]
) -> None:
    assert client.post("/api/v1/periods/2099-01/close").json()["code"] == "PERIOD_IN_FUTURE"
    assert client.post("/api/v1/periods/2026-01/close").json()["code"] == "PERIOD_EMPTY"
    assert client.get("/api/v1/periods/2026-01").json()["code"] == "PERIOD_EMPTY"
    assert client.post("/api/v1/periods/2026-13/close").status_code == 422


def test_reopening_gives_the_month_back_to_todays_figures(
    client: TestClient, two_months: dict[str, str]
) -> None:
    client.post(f"/api/v1/periods/{MARCH}/close")
    cost(client, two_months["MSCU4821990"], "DRAYAGE", "120.00", "2026-05-02")
    assert client.delete(f"/api/v1/periods/{MARCH}/close").status_code == 204
    march = periods(client)[MARCH]
    assert (march["status"], march["landed"], march["drift"]) == ("open", "820.00", None)
    assert client.delete(f"/api/v1/periods/{MARCH}/close").json()["code"] == "PERIOD_NOT_CLOSED"
    actions = [
        e["action"]
        for e in client.get("/api/v1/audit-log", params={"entity_type": "period"}).json()["entries"]
    ]
    assert actions == ["period.reopened", "period.closed"]


@pytest.fixture
def as_role(org_id: uuid.UUID) -> Iterator[Any]:
    from app.main import app  # after the fixtures have pointed the settings at the test database

    def become(role: MemberRole) -> None:
        who = Principal(org_id=org_id, user_id=None, role=role, via="dev")
        app.dependency_overrides[get_principal] = lambda: who

    yield become
    app.dependency_overrides.pop(get_principal, None)


def test_closing_is_for_those_who_answer_for_the_accounts(
    client: TestClient, two_months: dict[str, str], as_role: Any
) -> None:
    as_role(MemberRole.MEMBER)
    refused = client.post(f"/api/v1/periods/{MARCH}/close")
    assert refused.status_code == 403 and refused.json()["code"] == "FORBIDDEN_ROLE"
    assert client.get("/api/v1/periods").status_code == 200  # reading is for everyone
    # ADMIN is the highest role a Clerk organization has: it closes, and it reopens
    as_role(MemberRole.ADMIN)
    assert client.post(f"/api/v1/periods/{MARCH}/close").status_code == 201
    as_role(MemberRole.MEMBER)
    assert client.delete(f"/api/v1/periods/{MARCH}/close").json()["code"] == "FORBIDDEN_ROLE"
    as_role(MemberRole.ADMIN)
    assert client.delete(f"/api/v1/periods/{MARCH}/close").status_code == 204


def read_csv(res: Any) -> list[dict[str, str]]:
    assert res.status_code == 200, res.text
    return list(csv.DictReader(io.StringIO(res.text.lstrip("﻿")), delimiter=";"))


def test_the_closed_valuation_is_a_file_that_never_changes(
    client: TestClient, two_months: dict[str, str]
) -> None:
    assert (
        client.get("/api/v1/exports/period-close.csv", params={"period": MARCH}).json()["code"]
        == "PERIOD_NOT_CLOSED"
    )
    client.post(f"/api/v1/periods/{MARCH}/close")
    before = client.get("/api/v1/exports/period-close.csv", params={"period": MARCH}).text
    cost(client, two_months["MSCU4821990"], "DRAYAGE", "120.00", "2026-05-02")
    assert client.get("/api/v1/exports/period-close.csv", params={"period": MARCH}).text == before
    (row,) = read_csv(client.get("/api/v1/exports/period-close.csv", params={"period": MARCH}))
    assert row["Référence"] == "A" and row["Quantité"] == "50,0000"
    assert (row["FOB"], row["Frais ventilés"], row["Coût de revient rendu"]) == ("500,00", "200,00", "700,00")
    assert row["Coût de revient unitaire"] == "14,0000" and row["Conteneurs"] == "MSCU4821990"

    (drift,) = read_csv(client.get("/api/v1/exports/period-drift.csv", params={"period": MARCH}))
    assert drift["Écart depuis la clôture"] == "120,00"
    assert drift["Origine de l'écart"] == "Coûts ou chargements modifiés"
    assert drift["Type de coût"] == "Camionnage / livraison"


def test_another_organization_sees_none_of_our_months(client: TestClient, two_months: dict[str, str]) -> None:
    client.post(f"/api/v1/periods/{MARCH}/close")
    theirs = client.get("/api/v1/periods", headers={"X-Org-Id": str(uuid.uuid4())}).json()
    assert theirs["periods"] == []


def estimate(client: TestClient, container_id: str, amount: str) -> dict[str, Any]:
    res = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": container_id, "cost_type": "THC", "amount": amount,
              "currency": "EUR", "cost_date": "2026-03-12", "status": "ESTIMATE"},
    )  # fmt: skip
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def test_a_booked_drift_stops_asking_until_another_late_cost_arrives(
    client: TestClient, two_months: dict[str, str], as_role: Any
) -> None:
    box = two_months["MSCU4821990"]
    not_closed = client.post(f"/api/v1/periods/{MARCH}/acknowledge-drift", json={})
    assert not_closed.json()["code"] == "PERIOD_NOT_CLOSED"
    client.post(f"/api/v1/periods/{MARCH}/close")
    cost(client, box, "DRAYAGE", "120.00", "2026-05-02")
    march = periods(client)[MARCH]
    assert (march["landed"], march["live_landed"], march["drift"]) == ("700.00", "820.00", "120.00")
    assert (march["drift_acknowledged"], march["drift_outstanding"]) == ("0.00", "120.00")

    booked = client.post(f"/api/v1/periods/{MARCH}/acknowledge-drift", json={"note": "OD du 05/05"})
    assert booked.status_code == 200, booked.text
    assert (booked.json()["drift_acknowledged"], booked.json()["drift_outstanding"]) == ("120.00", "0.00")
    assert booked.json()["acknowledged_note"] == "OD du 05/05" and booked.json()["acknowledged_at"]

    cost(client, box, "INSPECTION", "30.00", "2026-05-20")
    march = periods(client)[MARCH]
    assert (march["drift"], march["drift_acknowledged"], march["drift_outstanding"]) == (
        "150.00",
        "120.00",
        "30.00",
    )
    assert march["landed"] == "700.00"  # the closed figure itself never moved

    as_role(MemberRole.MEMBER)
    refused = client.post(f"/api/v1/periods/{MARCH}/acknowledge-drift", json={})
    assert refused.json()["code"] == "FORBIDDEN_ROLE"


def test_the_invoices_not_yet_received_are_frozen_at_the_close_and_span_earlier_months(
    client: TestClient, two_months: dict[str, str]
) -> None:
    pending = estimate(client, two_months["MSCU4821990"], "250.00")
    march = client.get(f"/api/v1/periods/{MARCH}").json()
    assert [(a["container_number"], a["cost_type"], a["amount_base"]) for a in march["accruals"]] == [
        ("MSCU4821990", "THC", "250.00")
    ]
    assert march["accruals_total"] == "250.00"
    # still not invoiced at the end of April: the March box is in April's cut-off too
    april = client.get(f"/api/v1/periods/{APRIL}").json()
    assert [(a["container_number"], a["arrived_on"]) for a in april["accruals"]] == [
        ("MSCU4821990", "2026-03-10")
    ]

    assert client.post(f"/api/v1/periods/{MARCH}/close").status_code == 201
    invoiced = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": two_months["MSCU4821990"], "cost_type": "THC",
              "amount": "268.00", "currency": "EUR", "cost_date": "2026-04-20"},
    )  # fmt: skip
    assert invoiced.status_code == 201, invoiced.text
    replaced = client.patch(
        f"/api/v1/costs/{invoiced.json()['id']}", json={"supersedes_cost_id": pending["id"]}
    )
    assert replaced.status_code == 200, replaced.text
    assert client.get(f"/api/v1/periods/{APRIL}").json()["accruals"] == []  # the invoice has come
    frozen = client.get(f"/api/v1/periods/{MARCH}").json()
    assert frozen["accruals_total"] == "250.00"  # and March says what was true the day it was closed

    (row,) = read_csv(client.get("/api/v1/exports/period-accruals.csv", params={"period": MARCH}))
    assert (row["N° conteneur"], row["Montant en devise de base"]) == ("MSCU4821990", "250,00")
    assert row["Type de coût"] == "Manutention portuaire (THC)"
    assert row["État de la période"] == "Clôturée"


def test_a_box_still_at_sea_owes_nothing_yet(client: TestClient, two_months: dict[str, str]) -> None:
    """An ETA in April is a plan: nothing has been received, so nothing is a *facture non parvenue*.
    The same box, once discharged, is."""
    po = client.post(
        "/api/v1/purchase-orders",
        json={"po_number": "PO-2", "currency": "EUR", "lines": [ORDER_LINE]},
    ).json()
    voyage = client.post("/api/v1/shipments", json={"reference": "BL-APRIL", "eta": "2026-04-20"}).json()
    sailing = client.post("/api/v1/containers", json={"container_number": "TCLU1234565"}).json()
    client.patch(f"/api/v1/containers/{sailing['id']}", json={"shipment_id": voyage["id"]})
    loads = [{"po_line_id": po["lines"][0]["id"], "quantity": "10"}]
    assert client.put(f"/api/v1/containers/{sailing['id']}/loads", json=loads).status_code == 200
    estimate(client, sailing["id"], "99.00")

    assert periods(client)[APRIL]["containers"] == 2  # it is planned for April…
    assert client.get(f"/api/v1/periods/{APRIL}").json()["accruals"] == []  # …and owes nothing yet
    landed = {"discharged_at": "2026-04-21T08:00:00Z", "milestone": "DISCHARGED"}
    assert client.patch(f"/api/v1/containers/{sailing['id']}", json=landed).status_code == 200
    (owed,) = client.get(f"/api/v1/periods/{APRIL}").json()["accruals"]
    assert (owed["container_number"], owed["amount_base"]) == ("TCLU1234565", "99.00")


def test_the_cost_price_file_gives_the_average_or_the_last_arrival(
    client: TestClient, two_months: dict[str, str]
) -> None:
    def unit(**params: str) -> tuple[str, str, str]:
        (row,) = read_csv(client.get("/api/v1/exports/sku-costs.csv", params=params))
        return row["Coût de revient unitaire"], row["Base du coût"], row["Conteneurs"]

    assert unit(basis="average") == ("15,0000", "Moyenne pondérée", "MSCU4821990 TGHU7245081")
    assert unit(basis="last") == ("16,0000", "Dernier arrivage", "TGHU7245081")
    assert unit(period=MARCH) == ("14,0000", "Moyenne pondérée", "MSCU4821990")

    client.post(f"/api/v1/periods/{MARCH}/close")
    cost(client, two_months["MSCU4821990"], "DRAYAGE", "120.00", "2026-05-02")
    assert unit(period=MARCH)[0] == "14,0000"  # a closed month gives its frozen cost price
    (row,) = read_csv(client.get("/api/v1/exports/sku-costs.csv", params={"period": MARCH}))
    assert (row["État de la période"], row["Référence"], row["Quantité"]) == ("Clôturée", "A", "50,0000")
