"""How many SQL round trips the screens people actually open cost, and whether that grows with the data.

An N+1 is invisible on the two containers of the demo dataset and fatal on the three hundred of a
real importer: the page still works, it just takes eight seconds, and nothing in the test suite says
so. So the measurement is the test — every hot endpoint is called against a small organization and
again against one twice the size, and what must not change is the *number of queries*.

The bounds below are deliberately a little above what the code does today. They are there to catch a
loop that starts querying, not to fail because somebody added one legitimate lookup.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.orm import Session

from app.domain.costing.service import recompute_org
from app.domain.models import (
    AlertKind,
    AlertSeverity,
    AllocationMethod,
    Container,
    ContainerLoad,
    ContainerMilestone,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    Incoterm,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
    Supplier,
    TrackingState,
)

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


@contextmanager
def counting(engine: Engine) -> Iterator[list[str]]:
    """Every statement the application sends while the block runs."""
    seen: list[str] = []

    def before(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", before)


def queries(engine: Engine, client: TestClient, url: str) -> int:
    """The number of statements one GET costs, the response having been checked."""
    with counting(engine) as seen:
        res = client.get(url)
    assert res.status_code == 200, f"{url} -> {res.status_code} {res.text[:200]}"
    return len(seen)


def load_org(
    db: Session, org: Organization, *, containers: int, costs_per_container: int, start: int = 0
) -> list[Container]:
    """An organization the size of a real one: shipments of several boxes, split purchase orders,
    costs at both levels, and the allocations materialised as the application would have them."""
    supplier = Supplier(
        org_id=org.id, name=f"Zhejiang Kaiyuan Tyre Co. {start}", country="CN", default_currency="EUR"
    )
    db.add(supplier)
    db.flush()

    made: list[Container] = []
    per_shipment = 3
    for index in range(start, start + containers):
        if (index - start) % per_shipment == 0:
            shipment = Shipment(
                org_id=org.id,
                reference=f"MEDU{index:07d}",
                carrier_scac="MSCU",
                incoterm=Incoterm.FOB,
                origin_unlocode="CNNGB",
                destination_unlocode="FRLEH",
                etd=TODAY - timedelta(days=60),
                eta=TODAY - timedelta(days=10),
            )
            db.add(shipment)
            db.flush()
            po = PurchaseOrder(
                org_id=org.id,
                po_number=f"PO-2026-{index:04d}",
                supplier_id=supplier.id,
                currency="EUR",
                fx_rate=Decimal("1"),
                incoterm=Incoterm.FOB,
                order_date=TODAY - timedelta(days=90),
            )
            for line_no in (1, 2):
                po.lines.append(
                    PurchaseOrderLine(
                        org_id=org.id,
                        line_no=line_no,
                        sku=f"SKU-{index:04d}-{line_no}",
                        description="Passenger tyre 205/55 R16 91V",
                        hs_code="40111000",
                        quantity=Decimal("900"),
                        unit_price=Decimal("10.00"),
                        unit_weight_kg=Decimal("2"),
                        unit_volume_cbm=Decimal("0.055"),
                    )
                )
            db.add(po)
            db.flush()
            db.add(
                Cost(
                    org_id=org.id,
                    scope=CostScope.SHIPMENT,
                    shipment_id=shipment.id,
                    cost_type=CostType.OCEAN_FREIGHT,
                    amount=Decimal("3000.00"),
                    currency="EUR",
                    fx_rate=Decimal("1"),
                    fx_date=TODAY - timedelta(days=40),
                    fx_source="same",
                    amount_base=Decimal("3000.00"),
                    allocation_method=AllocationMethod.BY_WEIGHT,
                    cost_date=TODAY - timedelta(days=40),
                    vendor="Kuehne + Nagel France",
                    invoice_number=f"KN-{index:05d}",
                    status=CostStatus.ACTUAL,
                )
            )

        container = Container(
            org_id=org.id,
            shipment_id=shipment.id,
            container_number=f"MSCU{4000000 + index:07d}",
            iso_type="40HC",
            carrier_scac="MSCU",
            milestone=ContainerMilestone.DISCHARGED,
            tracking_state=TrackingState.MANUAL,
            eta=NOW - timedelta(days=10),
            ata=NOW - timedelta(days=10),
            discharged_at=NOW - timedelta(days=4),
            free_days_demurrage=5,
        )
        db.add(container)
        db.flush()
        for line in po.lines:
            db.add(
                ContainerLoad(
                    org_id=org.id,
                    container_id=container.id,
                    po_line_id=line.id,
                    quantity=Decimal("300"),
                )
            )
        for n in range(costs_per_container):
            db.add(
                Cost(
                    org_id=org.id,
                    scope=CostScope.CONTAINER,
                    container_id=container.id,
                    cost_type=[CostType.DRAYAGE, CostType.THC, CostType.CUSTOMS_BROKERAGE][n % 3],
                    amount=Decimal("300.00"),
                    currency="EUR",
                    fx_rate=Decimal("1"),
                    fx_date=TODAY - timedelta(days=3),
                    fx_source="same",
                    amount_base=Decimal("300.00"),
                    allocation_method=AllocationMethod.BY_VALUE,
                    cost_date=TODAY - timedelta(days=3),
                    vendor="Transports Delcourt",
                    invoice_number=f"TD-{index:04d}-{n}",
                    status=CostStatus.ACTUAL,
                )
            )
        made.append(container)

    db.flush()
    recompute_org(db, org)
    db.commit()
    return made


def raise_alerts(client: TestClient, db: Session, org: Organization, containers: list[Container]) -> None:
    from app.domain.alerts.service import raise_alert

    for index, container in enumerate(containers):
        raise_alert(
            db,
            org.id,
            kind=AlertKind.DND_RISK,
            severity=list(AlertSeverity)[index % 3],
            title=f"{container.container_number}: demurrage risk HIGH",
            body="Last free day 2026-09-20.",
            dedup_key=f"dnd:{container.id}:HIGH",
            container_id=container.id,
            payload={"risk": "HIGH", "last_free_day": "2026-09-20"},
        )
    db.commit()


#: The screens a person opens every day, and the reports behind the two tabs of the report page.
HOT = {
    "board": "/api/v1/containers",
    "costs": "/api/v1/costs",
    "alerts": "/api/v1/alerts",
    "report_variance": f"/api/v1/reports/variance?period={TODAY:%Y-%m}",
    "report_landed_cost": (
        f"/api/v1/reports/landed-cost?group_by=supplier"
        f"&period_from={TODAY - timedelta(days=90)}&period_to={TODAY}"
    ),
    "report_dnd": f"/api/v1/reports/dnd?period_from={TODAY - timedelta(days=90)}&period_to={TODAY}",
}


def add_invoices(
    client: TestClient, db: Session, org: Organization, how_many: int, *, start: int = 0
) -> list[str]:
    """A few read invoices, with their proposed lines: the inbox is a hot screen too."""
    from invoice_fixtures import FRENCH_ONE_CONTAINER
    from pdfs import make_pdf

    from app.jobs import handlers

    ids = []
    for n in range(start, start + how_many):
        text = list(FRENCH_ONE_CONTAINER)
        text[1] = f"FACTURE N FA-2026-{n:04d}"  # a different document each time, or the upload is a duplicate
        res = client.post(
            "/api/v1/invoices", files={"file": (f"facture-{n}.pdf", make_pdf(text), "application/pdf")}
        )
        assert res.status_code == 201, res.text
        invoice_id = res.json()["id"]
        handlers.extract_invoice(db, org.id, uuid.UUID(invoice_id))
        ids.append(invoice_id)
    return ids


def measure(
    engine: Engine, client: TestClient, containers: list[Container], invoice_id: str
) -> dict[str, int]:
    counts = {name: queries(engine, client, url) for name, url in HOT.items()}
    first = containers[0].id
    counts["container"] = queries(engine, client, f"/api/v1/containers/{first}")
    counts["container_loads"] = queries(engine, client, f"/api/v1/containers/{first}/loads")
    counts["container_landed_cost"] = queries(engine, client, f"/api/v1/landed-costs/containers/{first}")
    counts["invoices"] = queries(engine, client, "/api/v1/invoices")
    counts["invoice"] = queries(engine, client, f"/api/v1/invoices/{invoice_id}")
    return counts


#: What each screen costs today, plus two. A budget is not a target: it is there so that a loop which
#: starts querying per row shows up as a failing test rather than as a slow page nobody profiles.
BUDGET = {
    "board": 17,
    "costs": 5,
    "alerts": 5,
    "report_variance": 10,
    "report_landed_cost": 10,
    "report_dnd": 8,
    "container": 8,
    "container_loads": 8,
    "container_landed_cost": 11,
    "invoices": 6,
    "invoice": 7,
}


def test_no_endpoint_queries_once_per_row(
    client: TestClient, db: Session, engine: Engine, org: Organization
) -> None:
    """Trebling the data must not add a single query. The number of *rows* grows; the number of round
    trips is a property of the code, and the landed-cost report failed this: grouping by supplier
    reached through the purchase order one bucket at a time, so it cost one query per supplier."""
    small = load_org(db, org, containers=6, costs_per_container=2)
    raise_alerts(client, db, org, small)
    invoice_id = add_invoices(client, db, org, 3)[0]
    before = measure(engine, client, small, invoice_id)

    # a second supplier, more orders, more boxes, more costs, more alerts, more invoices
    bigger = load_org(db, org, containers=12, costs_per_container=2, start=100)
    raise_alerts(client, db, org, bigger)
    add_invoices(client, db, org, 3, start=50)
    after = measure(engine, client, small, invoice_id)

    grew = {name: f"{before[name]} -> {after[name]}" for name in before if after[name] > before[name]}
    assert not grew, f"these endpoints query once per row: {grew}"


def test_hot_endpoints_stay_within_their_query_budget(
    client: TestClient, db: Session, engine: Engine, org: Organization
) -> None:
    """The other half: flat is not the same as cheap. A page that took four round trips and now takes
    twenty is a regression even if it is a constant twenty."""
    containers = load_org(db, org, containers=6, costs_per_container=2)
    raise_alerts(client, db, org, containers)
    invoice_id = add_invoices(client, db, org, 3)[0]

    counts = measure(engine, client, containers, invoice_id)

    over = {name: f"{n} > {BUDGET[name]}" for name, n in counts.items() if n > BUDGET[name]}
    assert not over, f"more queries than budgeted: {over}"
