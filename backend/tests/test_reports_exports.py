"""The finance reports and the CSV exports, on the plan's reference case."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain import labels as export_labels
from app.domain.models import Organization, SkuCostHistory


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def reference_case(client: TestClient) -> dict[str, Any]:
    """The plan's T5: two orders in one container, ocean freight allocated by weight.

    PO-A 1000 x 10.00 at 2 kg each, PO-B 500 x 20.00 at 5 kg each, both fully in CNT1.
    FOB 10 000 + 10 000; weights 2 000 kg and 2 500 kg; freight 3 000 by weight.
    """
    shipment = client.post(
        "/api/v1/shipments",
        json={
            "reference": "MEDUSH2604417",
            "carrier_scac": "MSCU",
            "origin_unlocode": "CNNGB",
            "destination_unlocode": "FRLEH",
        },
    )
    assert shipment.status_code == 201, shipment.text
    shipment_id = shipment.json()["id"]

    orders = {}
    for name, quantity, price, weight, supplier_name in (
        ("PO-A", "1000", "10.00", "2.0000", "Zhejiang Kaiyuan Tyre Co."),
        ("PO-B", "500", "20.00", "5.0000", "Ningbo Hanwei"),
    ):
        supplier = client.post("/api/v1/suppliers", json={"name": supplier_name})
        assert supplier.status_code == 201, supplier.text
        res = client.post(
            "/api/v1/purchase-orders",
            json={
                "po_number": name,
                "supplier_id": supplier.json()["id"],
                "currency": "EUR",
                "order_date": "2026-01-15",
                "lines": [
                    {
                        "line_no": 1,
                        "sku": f"SKU-{name}",
                        "quantity": quantity,
                        "unit_price": price,
                        "unit_weight_kg": weight,
                    }
                ],
            },
        )
        assert res.status_code == 201, res.text
        orders[name] = res.json()

    container = client.post(
        "/api/v1/containers",
        json={"container_number": "MSCU4821990", "shipment_id": shipment_id, "carrier_scac": "MSCU"},
    )
    container_id = container.json()["id"]
    loads = client.put(
        f"/api/v1/containers/{container_id}/loads",
        json=[
            {"po_line_id": orders["PO-A"]["lines"][0]["id"], "quantity": "1000"},
            {"po_line_id": orders["PO-B"]["lines"][0]["id"], "quantity": "500"},
        ],
    )
    assert loads.status_code == 200, loads.text

    freight = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": "OCEAN_FREIGHT",
            "amount": "3000.00",
            "currency": "EUR",
            "cost_date": "2026-02-10",
            "allocation_method": "BY_WEIGHT",
        },
    )
    assert freight.status_code == 201, freight.text

    # the container lands, which is what the reports' period axis is about
    arrived = client.patch(
        f"/api/v1/containers/{container_id}",
        json={"milestone": "DISCHARGED", "discharged_at": "2026-02-20T08:00:00Z"},
    )
    assert arrived.status_code == 200, arrived.text
    return {"container_id": container_id, "shipment_id": shipment_id, **orders}


def report(client: TestClient, **params: Any) -> dict[str, Any]:
    res = client.get("/api/v1/reports/landed-cost", params=params)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


# ---------------------------------------------------------------------------- landed-cost analysis


def test_the_totals_are_the_reference_case_by_hand(client: TestClient, org: Organization) -> None:
    """20 000 of goods, 3 000 of freight: nothing here should be a surprise."""
    reference_case(client)
    body = report(client)
    assert body["totals"]["fob"] == "20000.00"
    assert body["totals"]["allocated"] == "3000.00"
    assert body["totals"]["landed"] == "23000.00"
    assert body["totals"]["freight_share_pct"] == "15.00"  # 3 000 / 20 000
    assert body["totals"]["by_cost_type"] == {"OCEAN_FREIGHT": "3000.00"}


def test_by_supplier_splits_the_freight_by_weight(client: TestClient, org: Organization) -> None:
    """2 000 kg against 2 500 kg: 1 333.33 and 1 666.67, and the two add back to 3 000."""
    reference_case(client)
    buckets = {b["label"]: b for b in report(client, group_by="supplier")["buckets"]}
    assert sorted(buckets) == ["Ningbo Hanwei", "Zhejiang Kaiyuan Tyre Co."]

    tyres = buckets["Zhejiang Kaiyuan Tyre Co."]
    housewares = buckets["Ningbo Hanwei"]
    assert tyres["allocated"] == "1333.33"
    assert housewares["allocated"] == "1666.67"
    assert Decimal(tyres["allocated"]) + Decimal(housewares["allocated"]) == Decimal("3000.00")
    assert tyres["landed"] == "11333.33"
    assert tyres["freight_share_pct"] == "13.33"


def test_by_route_uses_the_ports_and_says_so_when_it_cannot(client: TestClient, org: Organization) -> None:
    reference_case(client)
    (bucket,) = report(client, group_by="route")["buckets"]
    assert bucket["key"] == "CNNGB-FRLEH"
    assert bucket["label"] == "CNNGB → FRLEH"

    # a container with no shipment has no route, and the report says that rather than inventing one
    orphan = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"})
    assert orphan.status_code == 201
    labels = {b["label"] for b in report(client, group_by="route")["buckets"]}
    assert labels == {"CNNGB → FRLEH"}  # the orphan has no loads, so nothing to report on it


def test_by_month_files_goods_under_the_month_they_landed(client: TestClient, org: Organization) -> None:
    """Not the month they were invoiced: a forwarder billing late must not move a closed quarter."""
    reference_case(client)
    (bucket,) = report(client, group_by="month")["buckets"]
    assert bucket["key"] == "2026-02"  # discharged in February, invoiced in February too
    assert bucket["landed"] == "23000.00"


def test_the_period_filters_on_arrival(client: TestClient, org: Organization) -> None:
    reference_case(client)
    inside = report(client, period_from="2026-02-01", period_to="2026-02-28")
    assert inside["totals"]["landed"] == "23000.00"
    outside = report(client, period_from="2026-03-01")
    assert outside["totals"]["landed"] == "0.00"
    assert outside["buckets"] == []


def test_by_cost_type_shows_each_charge_against_the_goods(client: TestClient, org: Organization) -> None:
    reference_case(client)
    (bucket,) = report(client, group_by="cost_type")["buckets"]
    assert bucket["key"] == "OCEAN_FREIGHT"
    assert bucket["landed"] == "3000.00"
    assert bucket["freight_share_pct"] == "15.00"


def test_recoverable_vat_weighs_nothing_in_the_table_of_charges(
    client: TestClient, org: Organization
) -> None:
    """One table, one question: which charge weighs what in the landed cost. A recoverable tax does
    not weigh in it, and the row used to claim it did."""
    case = reference_case(client)
    add_import_vat(client, case["container_id"])
    buckets = {b["key"]: b for b in report(client, group_by="cost_type")["buckets"]}
    assert buckets["IMPORT_VAT"]["vat"] == "4600.00"
    assert buckets["IMPORT_VAT"]["landed"] == "0.00"
    assert buckets["OCEAN_FREIGHT"]["landed"] == "3000.00"
    assert report(client)["totals"]["landed"] == "23000.00"  # the total never counted it either


def test_the_unit_cost_per_sku_is_weighted_by_quantity(client: TestClient, org: Organization) -> None:
    reference_case(client)
    skus = {line["sku"]: line for line in report(client)["skus"]}
    assert skus["SKU-PO-A"]["unit_landed_cost"] == "11.3333"  # (10 000 + 1 333.33) / 1 000
    assert skus["SKU-PO-B"]["unit_landed_cost"] == "23.3333"  # (10 000 + 1 666.67) / 500


# ---------------------------------------------------------------------------- SKU history


def test_every_recompute_records_the_unit_cost_of_the_day(
    client: TestClient, db: Session, org: Organization
) -> None:
    reference_case(client)
    body = client.get("/api/v1/reports/sku/SKU-PO-A/history").json()
    assert len(body["points"]) == 1
    assert body["points"][0]["unit_landed_cost"] == "11.3333"
    assert body["points"][0]["quantity"] == "1000.0000"

    # a second recompute the same day corrects the day rather than adding a point
    client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": reference_case_container(client),
            "cost_type": "THC",
            "amount": "275.00",
            "currency": "EUR",
            "cost_date": "2026-02-11",
        },
    )
    body = client.get("/api/v1/reports/sku/SKU-PO-A/history").json()
    assert len(body["points"]) == 1
    assert body["points"][0]["unit_landed_cost"] != "11.3333"
    assert len(list(db.scalars(select(SkuCostHistory)))) == 2  # one row per SKU, one day


def reference_case_container(client: TestClient) -> str:
    return str(client.get("/api/v1/containers").json()[0]["id"])


# ---------------------------------------------------------------------------- demurrage report


def test_demurrage_paid_is_a_fact_and_avoided_needs_a_rate(
    client: TestClient, db: Session, org: Organization
) -> None:
    """Without a rate card there is no saving reported at all — only a sentence saying why."""
    case = reference_case(client)
    client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": case["container_id"],
            "cost_type": "DEMURRAGE",
            "amount": "450.00",
            "currency": "EUR",
            "cost_date": "2026-02-25",
        },
    )

    body = client.get("/api/v1/reports/dnd").json()
    assert body["paid"] == "450.00"
    assert body["avoided"] == "0.00"
    assert body["not_estimable"] == 1
    assert body["lines"][0]["avoided"] is None
    assert body["lines"][0]["rule"] == "no risk alert and no pickup recorded: nothing to estimate"
    assert isinstance(body["at_risk"], list)


def test_an_avoided_amount_always_cites_the_rule_that_produced_it(
    client: TestClient, db: Session, org: Organization
) -> None:
    from app.domain.alerts.service import raise_alert
    from app.domain.models import AlertKind, AlertSeverity, Container

    case = reference_case(client)
    client.post(
        "/api/v1/organization/rate-cards",
        json={"cost_type": "DEMURRAGE", "amount": "150.0000", "currency": "EUR"},
    )
    container = db.get(Container, uuid.UUID(case["container_id"]))
    assert container is not None
    alerted_on = datetime.now(UTC) - timedelta(days=5)
    raise_alert(
        db,
        org.id,
        kind=AlertKind.DND_RISK,
        severity=AlertSeverity.WARNING,
        title="MSCU4821990: demurrage risk MEDIUM",
        dedup_key="dnd:test:MEDIUM",
        container_id=container.id,
    )
    row = db.scalars(select(__import__("app.domain.models", fromlist=["Alert"]).Alert)).one()
    row.created_at = alerted_on
    container.gate_out_at = alerted_on + timedelta(days=3)
    db.commit()

    body = client.get("/api/v1/reports/dnd").json()
    line = body["lines"][0]
    assert line["avoided"] == "450.00"  # three days at 150
    assert line["rule"] == "3 day(s) between the first risk alert and pickup x 150.0000 per day"
    assert body["avoided"] == "450.00"
    assert body["estimable"] == 1


# ---------------------------------------------------------------------------- exports


def parse_csv(content: bytes, locale: str) -> tuple[list[str], list[list[str]]]:
    text_content = content.decode("utf-8")
    assert text_content.startswith("﻿"), "Excel needs the byte-order mark to read UTF-8"
    reader = csv.reader(io.StringIO(text_content.lstrip("﻿")), delimiter=";" if locale == "fr" else ",")
    rows = list(reader)
    if locale != "fr":
        return rows[0], rows[1:]
    # The French file is titled and labelled in French (see test_the_french_file_reads_in_french);
    # the assertions below address columns and values by their code, so both are read back here.
    titles = {v: k for k, v in {**export_labels.COST_TYPE_FR, **export_labels.HEADERS_FR}.items()}
    header = [titles.get(title, title) for title in rows[0]]
    codes = [{v: k for k, v in export_labels.VALUES_FR.get(name, {}).items()} for name in header]
    return header, [[codes[i].get(value, value) for i, value in enumerate(row)] for row in rows[1:]]


def test_the_french_file_reads_in_french(client: TestClient, org: Organization) -> None:
    """A CSV opened in Excel by a CFO is a screen like any other: no snake_case, no enum code."""
    reference_case(client)
    raw = client.get("/api/v1/exports/landed-costs.csv", params={"locale": "fr"}).content.decode("utf-8")
    header, first = raw.lstrip("\ufeff").split("\r\n")[:2]
    assert header.startswith("Type de ligne;N° conteneur;Date d'arrivée;")
    assert "Coût de revient unitaire" in header and "Fret maritime" in header and "_" not in header
    assert first.startswith("Ligne;")

    english = client.get("/api/v1/exports/landed-costs.csv", params={"locale": "en"}).content.decode("utf-8")
    assert english.lstrip("\ufeff").startswith("row_type,container_number,arrival_date,")


def cells(header: list[str], row: list[str]) -> dict[str, str]:
    return dict(zip(header, row, strict=True))


def landed_cost_rows(client: TestClient, locale: str = "fr", **params: Any) -> list[dict[str, str]]:
    res = client.get("/api/v1/exports/landed-costs.csv", params={"locale": locale, **params})
    assert res.status_code == 200, res.text
    header, rows = parse_csv(res.content, locale)
    return [cells(header, row) for row in rows]


def test_the_french_export_is_what_french_excel_expects(client: TestClient, org: Organization) -> None:
    """Semicolons and commas: get this wrong and every amount arrives as text."""
    reference_case(client)
    res = client.get("/api/v1/exports/landed-costs.csv", params={"locale": "fr"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "landed-costs.csv" in res.headers["content-disposition"]

    header, rows = parse_csv(res.content, "fr")
    assert header[:7] == [
        "row_type",
        "container_number",
        "arrival_date",
        "shipment_reference",
        "supplier",
        "po_number",
        "line_no",
    ]
    assert len(rows) == 2
    by_po = {cells(header, row)["po_number"]: cells(header, row) for row in rows}
    assert by_po["PO-A"]["fob"] == "10000,00"  # with a comma
    assert by_po["PO-A"]["allocated"] == "1333,33"
    assert by_po["PO-A"]["unit_landed_cost"] == "11,3333"
    assert by_po["PO-A"]["arrival_date"] == "2026-02-20"  # dates stay ISO, which every locale reads
    assert by_po["PO-A"]["period_basis"] == "arrival_date"  # which of the three dates this file uses


def test_the_english_export_uses_commas_and_dots(client: TestClient, org: Organization) -> None:
    reference_case(client)
    by_po = {row["po_number"]: row for row in landed_cost_rows(client, "en")}
    assert by_po["PO-A"]["fob"] == "10000.00"
    assert by_po["PO-B"]["allocated"] == "1666.67"


def add_import_vat(client: TestClient, container_id: str) -> None:
    res = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": container_id,
            "cost_type": "IMPORT_VAT",
            "amount": "4600.00",
            "currency": "EUR",
            "cost_date": "2026-02-21",
        },
    )
    assert res.status_code == 201, res.text


def test_the_export_and_the_container_report_agree_line_by_line(
    client: TestClient, org: Organization
) -> None:
    """The file the finance director reconciles against his balance, against the screen he saw.

    Import VAT is the reason this test exists: it is recoverable, so it is not a landed cost, and the
    export used to add it in anyway — a file inflated by exactly the VAT he claims back.
    """
    case = reference_case(client)
    add_import_vat(client, case["container_id"])

    report = client.get(f"/api/v1/landed-costs/containers/{case['container_id']}").json()
    exported = {row["po_number"]: row for row in landed_cost_rows(client, "en")}
    assert len(exported) == len(report["lines"])
    for line in report["lines"]:
        row = exported[line["po_number"]]
        assert row["fob"] == line["fob"]
        assert row["allocated"] == line["allocated"]
        assert row["import_vat"] == line["vat"]
        assert row["landed"] == line["landed"]
        assert row["unit_landed_cost"] == line["unit_landed_cost"]
        assert row["OCEAN_FREIGHT"] == line["by_cost_type"]["OCEAN_FREIGHT"]

    # the VAT is beside the landed cost, never inside it, and never in the charge columns either
    assert "IMPORT_VAT" not in exported["PO-A"]
    assert sum(Decimal(row["import_vat"]) for row in exported.values()) == Decimal("4600.00")
    for row in exported.values():
        assert Decimal(row["landed"]) == Decimal(row["fob"]) + Decimal(row["allocated"])


def test_the_export_says_what_it_could_not_allocate(client: TestClient, org: Organization) -> None:
    """An export whose total is quietly short of the invoices is an export nobody can reconcile."""
    reference_case(client)
    orphan = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"})
    assert orphan.status_code == 201
    stray = client.post(
        "/api/v1/costs",
        json={
            "scope": "CONTAINER",
            "target_id": orphan.json()["id"],
            "cost_type": "DRAYAGE",
            "amount": "480.00",
            "currency": "EUR",
            "cost_date": "2026-02-18",
            "vendor": "Transports Bernard",
        },
    )
    assert stray.status_code == 201, stray.text

    rows = landed_cost_rows(client)
    (unallocated,) = [row for row in rows if row["row_type"] == "UNALLOCATED"]
    assert unallocated["unallocated"] == "480,00"
    assert unallocated["unallocated_reason"].startswith("no container loads under")
    assert unallocated["supplier"] == "Transports Bernard"
    assert unallocated["period_basis"] == "cost_date"  # it never landed anywhere, so it has no arrival
    assert unallocated["allocated"] == ""  # and it is not counted as if it had been spread

    # the cost list carries the same reason on the piece itself
    header, cost_rows = parse_csv(client.get("/api/v1/exports/costs.csv").content, "fr")
    reasons = {cells(header, row)["amount"]: cells(header, row)["unallocated_reason"] for row in cost_rows}
    assert reasons["480,00"].startswith("no container loads under")
    assert reasons["3000,00"] == ""
    assert cells(header, cost_rows[0])["period_basis"] == "cost_date"


def test_a_supplier_name_cannot_carry_a_formula_into_excel(client: TestClient, org: Organization) -> None:
    """A forwarder chooses the text of his own invoice lines; he must not choose what Excel runs."""
    supplier = client.post("/api/v1/suppliers", json={"name": '=HYPERLINK("https://x.tld";"Voir")'})
    assert supplier.status_code == 201, supplier.text
    order = client.post(
        "/api/v1/purchase-orders",
        json={
            "po_number": "PO-C",
            "supplier_id": supplier.json()["id"],
            "currency": "EUR",
            "order_date": "2026-01-20",
            "lines": [{"line_no": 1, "sku": "-1+2", "quantity": "1", "unit_price": "10.00"}],
        },
    )
    assert order.status_code == 201, order.text
    container = client.post("/api/v1/containers", json={"container_number": "TGHU7245081"})
    client.put(
        f"/api/v1/containers/{container.json()['id']}/loads",
        json=[{"po_line_id": order.json()["lines"][0]["id"], "quantity": "1"}],
    )

    header, rows = parse_csv(client.get("/api/v1/exports/purchase-orders.csv").content, "fr")
    (row,) = [cells(header, r) for r in rows]
    assert row["supplier"] == '\'=HYPERLINK("https://x.tld";"Voir")'
    assert row["sku"] == "'-1+2"
    # and a negative amount is still a number, not a quoted string Excel reads as text
    assert row["unit_price"] == "10,0000"


def test_a_negative_amount_stays_a_number(client: TestClient, org: Organization) -> None:
    from app.api.v1.exports import cell, number

    assert cell(number(Decimal("-1333.33"), ",")) == "-1333,33"
    assert cell("-1333,33") == "'-1333,33"
    assert cell("=1+1") == "'=1+1"
    assert cell("\tSKU") == "'\tSKU"
    assert cell(42) == 42


def test_the_export_is_streamed_row_by_row() -> None:
    """The property lives in the generator, not in what the test client happens to buffer."""
    from app.api.v1.exports import stream

    def rows() -> Iterator[list[Any]]:
        yield ["a", "1"]
        yield ["b", "2"]

    chunks = list(stream(rows(), ["letter", "number"], "fr"))
    assert chunks[0] == "﻿"  # the byte-order mark, on its own, first
    assert chunks[1] == "letter;number\r\n"
    assert chunks[2] == "a;1\r\n"
    assert chunks[3] == "b;2\r\n"
    assert len(chunks) == 4  # one chunk per row, nothing accumulated


def test_the_other_three_exports_carry_what_they_promise(client: TestClient, org: Organization) -> None:
    reference_case(client)

    header, rows = parse_csv(client.get("/api/v1/exports/costs.csv").content, "fr")
    assert header[:4] == ["cost_date", "status", "cost_type", "scope"]
    assert rows[0][1] == "ACTUAL" and rows[0][2] == "OCEAN_FREIGHT"
    target_col = header.index("target")
    for row in rows:
        target = row[target_col]
        assert target
        assert not (len(target) == 36 and target.count("-") == 4)  # human label, not a bare UUID
    assert any(r[target_col] == "MSCU4821990" for r in rows)

    header, rows = parse_csv(client.get("/api/v1/exports/purchase-orders.csv").content, "fr")
    assert header[:3] == ["po_number", "supplier", "order_date"]
    assert len(rows) == 2

    header, rows = parse_csv(client.get("/api/v1/exports/containers.csv").content, "fr")
    assert "detention_deadline" in header
    assert rows[0][0] == "MSCU4821990"
    assert rows[0][3] == "DISCHARGED"


def test_exports_only_ever_see_one_organization(client: TestClient, db: Session, org: Organization) -> None:
    reference_case(client)
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(SkuCostHistory))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)

    other = client.get("/api/v1/exports/landed-costs.csv", headers={"X-Org-Id": str(uuid.uuid4())})
    _, rows = parse_csv(other.content, "fr")
    assert rows == []
