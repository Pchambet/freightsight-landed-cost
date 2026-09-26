"""What the "first file in ten minutes" walkthrough asks of the API: an order with its supplier by
name, conflicts that say what they collided with, and an invoice that knows which container it is about."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from pdfs import make_pdf
from sqlalchemy.orm import Session

from app.jobs import handlers

LINE = {"line_no": 1, "sku": "A", "quantity": "100", "unit_price": "10"}


def order(client: TestClient, number: str, **extra: Any) -> Any:
    return client.post(
        "/api/v1/purchase-orders", json={"po_number": number, "currency": "EUR", "lines": [LINE], **extra}
    )


def test_an_order_names_its_supplier_and_the_supplier_is_found_or_created(client: TestClient) -> None:
    first = order(client, "PO-1", supplier_name="  Zhejiang Kaiyuan Tyre Co. ")
    assert first.status_code == 201, first.text
    assert first.json()["supplier_name"] == "Zhejiang Kaiyuan Tyre Co."
    again = order(client, "PO-2", supplier_name="zhejiang kaiyuan tyre co.")
    assert again.json()["supplier_id"] == first.json()["supplier_id"]  # found, not created twice
    assert len(client.get("/api/v1/suppliers").json()) == 1


def test_a_second_try_is_told_which_record_is_already_there(client: TestClient) -> None:
    made = order(client, "PO-1").json()
    clash = order(client, "PO-1")
    assert clash.status_code == 409
    assert (clash.json()["code"], clash.json()["existing_id"]) == ("PURCHASE_ORDER_EXISTS", made["id"])

    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    again = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"})
    assert again.status_code == 409
    assert (again.json()["code"], again.json()["existing_id"]) == ("CONTAINER_EXISTS", box["id"])


def test_an_invoice_that_names_no_container_lands_on_the_one_it_was_dropped_for(
    client: TestClient, db: Session, org_id: uuid.UUID
) -> None:
    box = client.post("/api/v1/containers", json={"container_number": "MSCU4821990"}).json()
    pdf = make_pdf(
        ["TRANSDEMO SAS", "FACTURE N° FA-2026-0099", "Date : 10/09/2026", "",
         "Designation                         Montant",
         "Fret maritime                       2 450,00", "Frais de surete                        18,50",
         "Total HT                            2 468,50"]
    )  # fmt: skip
    uploaded = client.post(
        "/api/v1/invoices",
        files={"file": ("fa.pdf", pdf, "application/pdf")},
        data={"default_container_id": box["id"]},
    )
    assert uploaded.status_code == 201, uploaded.text
    handlers.extract_invoice(db, org_id, uuid.UUID(uploaded.json()["id"]))
    lines = client.get(f"/api/v1/invoices/{uploaded.json()['id']}").json()["lines"]
    assert [(ln["scope"], ln["target_id"]) for ln in lines] == [("CONTAINER", box["id"])] * 2
    assert all("@default_container|MSCU4821990" in (ln["notes"] or "") for ln in lines)
    assert lines[1]["cost_type"] is None  # a wording nobody knows: read, and left for a person to name

    unknown = client.post(
        "/api/v1/invoices",
        files={"file": ("fa.pdf", pdf, "application/pdf")},
        data={"default_container_id": str(uuid.uuid4())},
    )
    assert unknown.status_code == 404
