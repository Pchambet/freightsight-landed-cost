"""How long the numbers take at the size of a real importer — measured, with a budget.

"A full-organization recompute is a few milliseconds" was written in `costing/service.py` when the
largest dataset was two containers, and never measured. This seeds five years of a sixty-containers-a-
year importer — 300 containers, 3 000 order lines loaded, 1 500 costs — and times what every screen
calls. The budgets are loose on purpose (CI machines are slow and shared): they catch an accidental
quadratic, not a 10 % drift. The printed figures are the measurement.

Measured on 18 September 2026 (a laptop, Postgres in a container): 0.4 to 0.65 s for every one of
them, of which about 0.4 s is the engine recomputing the whole organization. Good enough that paging
the lists on the server would buy nothing — the lists are not what costs — and the place to look,
when it stops being good enough, is one computation per request shared by the endpoints of a page.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domain.models import (
    AllocationMethod,
    Container,
    ContainerLoad,
    ContainerMilestone,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    PurchaseOrder,
    PurchaseOrderLine,
)

CONTAINERS, LINES_PER_BOX, COSTS_PER_BOX = 300, 10, 5
TYPES = [
    CostType.OCEAN_FREIGHT,
    CostType.THC,
    CostType.CUSTOMS_BROKERAGE,
    CostType.DRAYAGE,
    CostType.CUSTOMS_DUTY,
]


def seed(db: Session, org_id: uuid.UUID) -> None:
    now = datetime.now(UTC)
    for index in range(CONTAINERS):
        landed = now - timedelta(days=index * 6)
        order = PurchaseOrder(org_id=org_id, po_number=f"PO-{index:04d}", currency="EUR", fx_rate=Decimal(1))
        box = Container(
            org_id=org_id,
            container_number=f"SCAL{index:07d}",
            milestone=ContainerMilestone.DELIVERED,
            ata=landed,
            discharged_at=landed,
            gate_out_at=landed + timedelta(days=2),
            empty_returned_at=landed + timedelta(days=4),
        )
        db.add_all([order, box])
        db.flush()
        for line_no in range(1, LINES_PER_BOX + 1):
            line = PurchaseOrderLine(
                org_id=org_id,
                po_id=order.id,
                line_no=line_no,
                sku=f"SKU-{(index * 7 + line_no) % 400:04d}",
                quantity=Decimal(100),
                unit_price=Decimal("12.50"),
                unit_weight_kg=Decimal("2.5"),
                unit_volume_cbm=Decimal("0.02"),
                duty_rate=Decimal("0.045"),
            )
            db.add(line)
            db.flush()
            db.add(
                ContainerLoad(org_id=org_id, container_id=box.id, po_line_id=line.id, quantity=Decimal(100))
            )
        for cost_type in TYPES[:COSTS_PER_BOX]:
            db.add(
                Cost(
                    org_id=org_id,
                    scope=CostScope.CONTAINER,
                    container_id=box.id,
                    cost_type=cost_type,
                    amount=Decimal("850.00"),
                    currency="EUR",
                    fx_rate=Decimal(1),
                    fx_date=date(2026, 1, 1),
                    fx_source="same",
                    amount_base=Decimal("850.00"),
                    allocation_method=AllocationMethod.BY_VALUE,
                    cost_date=landed.date(),
                    status=CostStatus.ACTUAL,
                )
            )
    db.commit()


def timed(client: TestClient, path: str) -> float:
    started = time.perf_counter()
    res = client.get(path)
    elapsed = time.perf_counter() - started
    assert res.status_code == 200, res.text[:300]
    return elapsed


def test_the_screens_of_a_five_year_importer_answer_within_budget(
    client: TestClient, db: Session, org_id: uuid.UUID
) -> None:
    assert client.get("/api/v1/organization").status_code == 200  # creates the organization
    seed(db, org_id)
    box = client.get("/api/v1/containers").json()[0]["id"]
    today = datetime.now(UTC).date()
    quarter = f"period_from={today - timedelta(days=91)}&period_to={today}"  # ~15 boxes, a year of peers
    budgets = {
        "/api/v1/containers": 6.0,
        f"/api/v1/landed-costs/containers/{box}": 4.0,
        "/api/v1/reports/landed-cost?group_by=month": 4.0,
        "/api/v1/reports/overview": 8.0,
        "/api/v1/reports/skus": 5.0,
        f"/api/v1/reports/audit?{quarter}": 8.0,
        f"/api/v1/reports/audit/preparation?{quarter}": 8.0,
        "/api/v1/periods": 5.0,
        "/api/v1/search?q=scal0000": 2.0,
    }
    measured = {path: timed(client, path) for path in budgets}
    print("\n" + "\n".join(f"{seconds:6.2f} s  {path}" for path, seconds in measured.items()))
    late = {p: round(s, 2) for p, s in measured.items() if s > budgets[p]}
    assert not late, f"over budget: {late}"
