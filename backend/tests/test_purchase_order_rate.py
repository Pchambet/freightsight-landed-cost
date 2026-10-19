"""An order's exchange rate, set by hand on the order: kept through later edits, and logged."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.models import AuditLog, PurchaseOrder


def test_a_rate_set_on_an_order_without_one_is_logged_with_its_source(
    client: TestClient, db: Session
) -> None:
    body = {"po_number": "PO-USD", "currency": "USD", "order_date": "2026-09-01"}
    order = client.post("/api/v1/purchase-orders", json=body).json()
    missing = {"fx_rate": None, "fx_date": None, "fx_source": None}
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == UUID(order["id"])).values(**missing))
    db.flush()
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"fx_rate": "0.92"})
    assert res.status_code == 200, res.text
    assert res.json()["fx_source"] == "manual"
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "purchase_order.updated"))
    assert (
        entry is not None and entry.entity_type == "purchase_order" and entry.entity_id == UUID(order["id"])
    )
    assert entry.before == missing
    assert entry.after == {"fx_rate": "0.92", "fx_date": "2026-09-01", "fx_source": "manual"}


def test_a_rate_typed_by_hand_survives_an_edit_of_the_order(client: TestClient, db: Session) -> None:
    """The ECB's rate of the same day used to replace it on any edit of the order date, unsaid."""
    body = {"po_number": "PO-HAND", "currency": "USD", "order_date": "2026-09-01", "fx_rate": "0.95"}
    po = client.post("/api/v1/purchase-orders", json=body).json()
    assert Decimal(po["fx_rate"]) == Decimal("0.95")
    res = client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"order_date": "2026-08-20"})
    assert res.status_code == 200, res.text
    assert Decimal(res.json()["fx_rate"]) == Decimal("0.95") and res.json()["fx_date"] == "2026-09-01"
    # The day of the rate moved: the ECB's of the new day is taken, and the log says so.
    res = client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"fx_date": "2026-08-20"})
    assert Decimal(res.json()["fx_rate"]) == Decimal("0.92")
    entries = list(db.scalars(select(AuditLog).where(AuditLog.action == "purchase_order.updated")))
    assert [e.after for e in entries] == [
        {"order_date": "2026-08-20"},
        {"fx_rate": "0.92", "fx_date": "2026-08-20", "fx_source": "ecb"},
    ]


def test_an_order_with_neither_date_nor_rate_is_asked_for_one(client: TestClient, db: Session) -> None:
    po = client.post(
        "/api/v1/purchase-orders", json={"po_number": "PO-1", "currency": "USD", "fx_rate": "0.9"}
    )
    assert po.status_code == 201, po.text
    order = po.json()
    assert (order["fx_rate"], order["fx_date"]) == ("0.9", None)
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == UUID(order["id"])).values(fx_rate=None))
    db.flush()
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"incoterm": "FOB"})
    assert res.status_code == 200 and res.json()["fx_rate"] is None  # nothing asked of the rate
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"fx_rate": "0.91"})
    assert res.status_code == 200 and Decimal(res.json()["fx_rate"]) == Decimal("0.91")


def test_the_date_of_an_order_the_ecb_does_not_quote_can_be_corrected(
    client: TestClient, db: Session
) -> None:
    body = {"po_number": "PO-XTS", "currency": "USD", "order_date": "2026-09-01"}
    order = client.post("/api/v1/purchase-orders", json=body).json()
    missing = {"currency": "XTS", "fx_rate": None, "fx_date": None}
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == UUID(order["id"])).values(**missing))
    db.flush()
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"order_date": "2026-08-20"})
    assert res.status_code == 200, res.text
    assert (res.json()["order_date"], res.json()["fx_rate"]) == ("2026-08-20", None)
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"fx_rate": "0.0000365"})
    assert res.status_code == 200, res.text
    assert Decimal(res.json()["fx_rate"]) == Decimal("0.0000365") and res.json()["fx_date"] == "2026-08-20"


def test_every_rate_says_where_it_came_from(client: TestClient) -> None:
    def created(body: dict[str, str]) -> dict[str, Any]:
        res = client.post("/api/v1/purchase-orders", json=body)
        assert res.status_code == 201, res.text
        return dict(res.json())

    ecb = created({"po_number": "PO-ECB", "currency": "USD", "order_date": "2026-09-01"})
    typed = created({"po_number": "PO-TYPED", "currency": "USD", "fx_rate": "0.95"})
    same = created({"po_number": "PO-EUR", "currency": "EUR"})
    assert [(o["fx_rate"], o["fx_source"]) for o in (ecb, typed, same)] == [
        ("0.92", "ecb"),
        ("0.95", "manual"),
        ("1", "same"),
    ]
    assert not any(o["rate_unconfirmed"] for o in (ecb, typed, same))


def test_an_order_date_added_keeps_a_rate_typed_with_no_date(client: TestClient) -> None:
    """Adding the order date used to look the rate up again: the ECB's replaced the one typed, and a
    currency the ECB does not quote failed with FX_RATE_UNAVAILABLE."""
    for number, currency, rate in (("PO-USD", "USD", "0.95"), ("PO-VND", "VND", "0.0000365")):
        body = {"po_number": number, "currency": currency, "fx_rate": rate}
        po = client.post("/api/v1/purchase-orders", json=body).json()
        res = client.patch(f"/api/v1/purchase-orders/{po['id']}", json={"order_date": "2026-09-01"})
        assert res.status_code == 200, res.text
        kept = res.json()
        assert (Decimal(kept["fx_rate"]), kept["fx_source"], kept["fx_date"]) == (
            Decimal(rate),
            "manual",
            None,
        )


def test_a_defaulted_rate_of_one_is_to_be_checked_until_a_person_confirms_it(
    client: TestClient, db: Session
) -> None:
    order = client.post(
        "/api/v1/purchase-orders", json={"po_number": "PO-OLD", "currency": "USD", "fx_rate": "0.9"}
    ).json()
    defaulted = {"fx_rate": Decimal(1), "fx_date": None, "fx_source": None}
    db.execute(update(PurchaseOrder).where(PurchaseOrder.id == UUID(order["id"])).values(**defaulted))
    db.flush()
    found = client.get(f"/api/v1/purchase-orders/{order['id']}").json()
    assert found["rate_unconfirmed"] and found["fx_source"] is None
    # Confirmed as it stands: the rate does not move, its source does, and the history says so.
    res = client.patch(f"/api/v1/purchase-orders/{order['id']}", json={"fx_rate": "1"})
    assert res.status_code == 200, res.text
    assert (res.json()["fx_source"], res.json()["rate_unconfirmed"]) == ("manual", False)
    entry = db.scalar(select(AuditLog).where(AuditLog.action == "purchase_order.updated"))
    assert entry is not None and (entry.before, entry.after) == ({"fx_source": None}, {"fx_source": "manual"})
