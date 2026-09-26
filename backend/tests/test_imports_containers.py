"""Reading a containers' tracking sheet: every rule in both directions — the dates that happened move a
box along and the expected ones never do, a file that contradicts itself leaves the box out, an order
alone in a box is loaded and one spread over several is left to a person — and the audit then sees
the quarter it could not see before."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from invoice_fixtures import FRENCH_ONE_CONTAINER
from pdfs import make_pdf
from previews import commit_previewed
from sqlalchemy.orm import Session

from app.domain.models import Container, Organization, TrackingState
from app.jobs import handlers

HEADER = (
    "N° conteneur;Type de conteneur;N° commandes;N° B/L;Compagnie maritime;Port de chargement;"
    "Port de déchargement;ETD;ETA;Arrivée réelle;Déchargé le;Sorti du terminal le;Vide restitué le"
)
MSCU = (
    "MSCU4821994;40HC;PO-1;MEDU 2604417;MSC;Ningbo;Le Havre;"
    "01/02/2026;08/03/2026;08/03/2026;10/03/2026;12/03/2026;15/03/2026"
)
TGHU = "TGHU7245086;40' HC;PO-2 / PO-3;MEDU 2604417;MSC;Ningbo;Le Havre;01/02/2026;08/03/2026;;;;"


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def order(client: TestClient, number: str, lines: int = 1) -> dict[str, Any]:
    body = {"po_number": number, "currency": "EUR",
            "lines": [{"line_no": n, "sku": f"{number}-{n}", "quantity": "100", "unit_price": "10"}
                      for n in range(1, lines + 1)]}  # fmt: skip
    res = client.post("/api/v1/purchase-orders", json=body)
    assert res.status_code == 201, res.text
    made: dict[str, Any] = res.json()
    return made


def sheet(*rows: str) -> bytes:
    return ("﻿" + "\n".join([HEADER, *rows]) + "\n").encode()


def preview(client: TestClient, content: bytes) -> tuple[dict[str, Any], dict[str, Any]]:
    job = client.post(
        "/api/v1/imports", data={"kind": "CONTAINERS"}, files={"file": ("suivi.csv", content, "text/csv")}
    )
    assert job.status_code == 201, job.text
    res = client.post(f"/api/v1/imports/{job.json()['id']}/validate", json={"mapping": job.json()["mapping"]})
    if res.status_code == 422 and res.json()["code"] == "NO_VALID_ROWS":
        return job.json(), res.json()["report"]  # every row refused: the report comes with the refusal
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body, body["report"]


def commit(client: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    res = commit_previewed(client, job["id"])
    assert res.status_code == 200, res.text
    report: dict[str, Any] = res.json()["report"]
    return report


def codes(issues: list[dict[str, Any]]) -> list[str]:
    return [issue["code"] for issue in issues]


def boxes(client: TestClient) -> dict[str, dict[str, Any]]:
    return {c["container_number"]: c for c in client.get("/api/v1/containers").json()}


# ---------------------------------------------------------------------------- the quarter comes in


def test_a_tracking_sheet_dates_the_boxes_and_loads_the_orders_that_travelled_alone(
    client: TestClient,
) -> None:
    order(client, "PO-1", lines=2)
    order(client, "PO-2")
    order(client, "PO-3")
    job, report = preview(client, sheet(MSCU, TGHU, TGHU.replace("PO-2 / PO-3", "PO-3")))
    assert (report["valid_rows"], report["errors"], report["warnings"]) == (3, [], [])
    counted = {
        k: report[k]
        for k in ("containers_created", "containers_updated", "containers_dated", "shipments_created",
                  "loads_created", "po_split", "invoices_matched")
    }  # fmt: skip
    assert counted == {
        "containers_created": 2, "containers_updated": 0, "containers_dated": 2, "shipments_created": 1,
        "loads_created": 4, "po_split": 0, "invoices_matched": 0,
    }  # fmt: skip
    assert report["purchase_orders_created"] is None  # another kind's counter is not a zero
    assert client.get("/api/v1/containers").json() == []  # a preview writes nothing

    commit(client, job)
    found = boxes(client)
    mscu, tghu = found["MSCU4821994"], found["TGHU7245086"]
    assert (mscu["iso_type"], mscu["carrier_scac"], mscu["milestone"]) == (
        "45G1",
        "MSCU",
        "GATE_IN_EMPTY_RETURN",
    )
    assert mscu["discharged_at"].startswith("2026-03-10T12:00:00")  # the day it says, at noon UTC
    assert (tghu["iso_type"], tghu["discharged_at"]) == ("45G1", None)
    (voyage,) = client.get("/api/v1/shipments").json()
    assert (voyage["reference"], voyage["origin_unlocode"], voyage["destination_unlocode"]) == (
        "MEDU2604417",
        "CNNGB",
        "FRLEH",
    )
    assert (voyage["etd"], voyage["eta"]) == ("2026-02-01", "2026-03-08")
    # the quarter the audit could not see is there now: both boxes dated in March, none without a date
    preparation = client.get(
        "/api/v1/reports/audit/preparation", params={"period_from": "2026-03-01", "period_to": "2026-03-31"}
    ).json()
    assert "CONTAINERS_WITHOUT_DATE" not in {item["code"] for item in preparation["items"]}
    audit = client.get(
        "/api/v1/reports/audit", params={"period_from": "2026-03-01", "period_to": "2026-03-31"}
    )
    assert audit.json()["completeness"]["containers"] == 2


def test_an_invoice_waiting_for_a_box_nobody_had_points_at_it_once_the_box_comes_in(
    client: TestClient, db: Session, org: Organization
) -> None:
    invoice_id = client.post(
        "/api/v1/invoices",
        files={
            "file": (
                "f.pdf",
                make_pdf([line.replace("MSCU4821990", "MSCU4821994") for line in FRENCH_ONE_CONTAINER]),
                "application/pdf",
            )
        },
    ).json()["id"]
    handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id))
    waiting = client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]
    assert {line["target_id"] for line in waiting} == {None}  # the box it names is nobody's yet

    job, report = preview(client, sheet(MSCU.replace("PO-1", "")))
    assert report["invoices_matched"] == 1
    commit(client, job)
    box = boxes(client)["MSCU4821994"]
    matched = client.get(f"/api/v1/invoices/{invoice_id}").json()["lines"]
    assert {line["target_id"] for line in matched} == {box["id"]}
    assert {line["notes"] for line in matched} == {"@single_container|MSCU4821994"}  # as a reading says it


# ---------------------------------------------------------------------------- what is refused


def test_a_box_or_a_bill_the_file_contradicts_is_left_out_by_name(client: TestClient) -> None:
    other_arrival = MSCU.replace(";08/03/2026;10/03/2026", ";09/03/2026;10/03/2026")  # the same box, twice
    on_bill = "TGHU7245086;40HC;;MEDU 1;MSC;Ningbo;Le Havre;01/02/2026;08/03/2026;;;;"
    other_eta = "CSQU3054383;20GP;;MEDU 1;MSC;Ningbo;Le Havre;01/02/2026;09/03/2026;;;;"  # the same bill
    _, report = preview(client, sheet(MSCU, other_arrival, on_bill, other_eta))
    assert codes(report["errors"]) == ["CONFLICT_IN_FILE", "CONFLICT_IN_FILE"]
    assert report["valid_rows"] == 0  # the box contradicted, and both boxes of the contradicted bill


def test_dates_that_have_not_happened_or_come_out_of_order_are_refused(client: TestClient) -> None:
    rows = [
        MSCU.replace(";08/03/2026;10/03/2026;12/03/2026;15/03/2026", ";08/03/2026;10/03/2099;;"),
        "TGHU7245086;40HC;;;;;;01/02/2026;08/03/2026;08/03/2026;12/03/2026;10/03/2026;",
        "CSQU3054383;40HC;;;;;;09/03/2026;08/03/2026;;;;",
        "MSKU1234566;40HC;;;;;;;;08/03/2026;;;",
        "MSC-12;40HC;;;;;;;;08/03/2026;;;",
    ]
    _, report = preview(client, sheet(*rows))
    by_row = {issue["row"]: issue["code"] for issue in report["errors"]}
    assert by_row == {
        2: "DATE_IN_FUTURE",
        3: "DATES_OUT_OF_ORDER",  # out of the terminal before it was unloaded
        4: "DATES_OUT_OF_ORDER",  # left after it was due
        5: "CONTAINER_CHECK_DIGIT",
        6: "INVALID_CONTAINER_NUMBER",
    }


# ---------------------------------------------------------------------------- the orders


def test_an_order_in_several_boxes_is_left_to_a_person_and_an_unknown_one_is_said(client: TestClient) -> None:
    order(client, "PO-1")
    job, report = preview(client, sheet(MSCU, TGHU.replace("PO-2 / PO-3", "PO-1 / PO-9")))
    assert (report["po_split"], report["loads_created"]) == (1, 0)
    assert sorted(codes(report["warnings"])) == ["PO_SPLIT", "PO_UNKNOWN"]
    commit(client, job)
    assert {n: b["po_numbers"] for n, b in boxes(client).items()} == {  # nobody guessed the quantities
        "MSCU4821994": [],
        "TGHU7245086": [],
    }


def test_an_order_also_named_on_a_row_left_out_is_not_loaded_whole_in_the_other_box(
    client: TestClient,
) -> None:
    """The order travelled in two boxes; one of the two rows is refused. Loading the whole order into
    the box that remains would put the other box's goods in it."""
    order(client, "PO-1")
    order(client, "PO-2")
    in_the_future = TGHU.replace("PO-2 / PO-3", "PO-1").replace(
        ";08/03/2026;;;;", ";08/03/2026;10/03/2099;;;"
    )
    _, report = preview(client, sheet(MSCU, in_the_future))
    assert codes(report["errors"]) == ["DATE_IN_FUTURE"]
    assert (report["po_split"], report["loads_created"]) == (1, 0)
    contradicted = MSCU.replace("MSCU4821994", "TGHU7245086").replace("PO-1", "PO-2")
    _, report = preview(client, sheet(MSCU.replace("PO-1", "PO-2"), contradicted,
                                      contradicted.replace("10/03/2026", "11/03/2026")))  # fmt: skip
    assert "CONFLICT_IN_FILE" in codes(report["errors"])
    assert (report["po_split"], report["loads_created"]) == (1, 0)


def test_an_order_already_loaded_in_its_box_is_neither_loaded_again_nor_questioned(
    client: TestClient,
) -> None:
    made = order(client, "PO-1")
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821994"}).json()
    loads = [{"po_line_id": made["lines"][0]["id"], "quantity": "60"}]
    assert client.put(f"/api/v1/containers/{box['id']}/loads", json=loads).status_code == 200
    _, report = preview(client, sheet(MSCU))
    assert (report["loads_created"], report["po_split"], report["warnings"]) == (0, 0, [])


# ---------------------------------------------------------------------------- what is kept


def test_a_box_a_provider_follows_keeps_the_provider_s_dates(client: TestClient, db: Session) -> None:
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821994"}).json()
    row = db.get(Container, uuid.UUID(box["id"]))
    assert row is not None
    row.tracking_state = TrackingState.ACTIVE
    db.flush()
    job, report = preview(client, sheet(MSCU.replace("PO-1", "")))
    assert codes(report["warnings"]) == ["TRACKED_CONTAINER"]
    commit(client, job)
    assert boxes(client)["MSCU4821994"]["discharged_at"] is None


def test_a_box_moves_along_only_on_what_happened_and_never_back(client: TestClient) -> None:
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821994"}).json()
    assert client.patch(f"/api/v1/containers/{box['id']}", json={"milestone": "GATE_OUT_FULL"}).is_success
    only_discharged = "MSCU4821994;40HC;;;;;;01/02/2026;15/03/2026;15/03/2026;16/03/2026;;"
    job, _ = preview(client, sheet(only_discharged))
    commit(client, job)
    found = boxes(client)["MSCU4821994"]
    assert (found["milestone"], found["discharged_at"][:10]) == ("GATE_OUT_FULL", "2026-03-16")


def test_what_the_file_leaves_open_is_said_before_anyone_counts_days_on_it(client: TestClient) -> None:
    rows = [
        "MSCU4821994;40HC;;;;;;;;15/03/2026;16/03/2026;;",  # unloaded, and no date says it left
        "TGHU7245086;40HC;;;;;;;;15/03/2026;;;",  # arrived, but when was it unloaded?
    ]
    _, report = preview(client, sheet(*rows))
    assert sorted(codes(report["warnings"])) == ["CLOCK_LEFT_RUNNING", "NO_DISCHARGE_DATE"]


def test_sizes_ports_and_lines_are_read_into_codes_and_the_unreadable_ones_are_said(
    client: TestClient,
) -> None:
    rows = [
        "MSCU4821994;1x40HQ;;BL-7;CMA CGM;Anvers;Fos-sur-Mer;01/02/2026;15/03/2026;;;;",
        "TGHU7245086;banana;;BL-8;Some Line;Xyzville;Le Havre;01/02/2026;15/03/2026;;;;",
    ]
    job, report = preview(client, sheet(*rows))
    assert sorted(codes(report["warnings"])) == ["CARRIER_UNKNOWN", "ISO_TYPE_UNKNOWN", "PORT_UNKNOWN"]
    commit(client, job)
    shipments = {s["reference"]: s for s in client.get("/api/v1/shipments").json()}
    assert (shipments["BL-7"]["origin_unlocode"], shipments["BL-7"]["destination_unlocode"]) == (
        "BEANR",
        "FRFOS",
    )
    assert (shipments["BL-7"]["carrier_scac"], boxes(client)["MSCU4821994"]["iso_type"]) == ("CMDU", "45G1")
    assert shipments["BL-8"]["origin_unlocode"] is None


def test_the_file_decides_whether_the_day_or_the_month_comes_first(client: TestClient) -> None:
    american = "MSCU4821994;40HC;;BL-7;;;;02/01/2026;03/15/2026;;;;"
    job, report = preview(client, sheet(american))
    assert "DATE_ORDER_ASSUMED" not in codes(report["warnings"])  # « 03/15 » settles every column
    commit(client, job)
    (voyage,) = client.get("/api/v1/shipments").json()
    assert (voyage["etd"], voyage["eta"]) == ("2026-02-01", "2026-03-15")

    undecided = "TGHU7245086;40HC;;BL-8;;;;01/02/2026;08/03/2026;;;;"
    _, report = preview(client, sheet(undecided))
    assert codes(report["warnings"]).count("DATE_ORDER_ASSUMED") == 2  # ETD and ETA, day first


def test_a_box_with_no_date_at_all_is_said_to_be_in_no_period(client: TestClient) -> None:
    _, report = preview(client, sheet("MSCU4821994;40HC;;;;;;;;;;;"))
    assert codes(report["warnings"]) == ["ARRIVAL_UNKNOWN"]
    assert report["valid_rows"] == 1
