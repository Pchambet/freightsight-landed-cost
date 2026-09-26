"""The full differential-push scenario, against a real Odoo instead of the fake.

Skipped unless `ODOO_TEST_URL` points at a throwaway instance, so it costs CI nothing. It exists
because the fake can only prove that our code does what we think Odoo does; this proves Odoo agrees.
Bring the instance up with docs/odoo-questions.md section 5 and `scripts/odoo_seed.py`, then:

    ODOO_TEST_URL=http://localhost:8169 uv run pytest tests/test_erp_landed_cost_live.py -q

It creates landed costs and deletes the ones it created, whatever happens.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# The autouse encryption-key fixture and the org fixture, reused as they are.
from test_erp_landed_cost import erp_key as erp_key
from test_erp_landed_cost import org as org
from test_erp_landed_cost import preview, seed_container

from app.domain.models import Organization

ODOO_URL = os.environ.get("ODOO_TEST_URL")
ODOO_DB = os.environ.get("ODOO_TEST_DB", "fstest")
ODOO_LOGIN = os.environ.get("ODOO_TEST_LOGIN", "admin")
ODOO_PASSWORD = os.environ.get("ODOO_TEST_PASSWORD", "admin")

pytestmark = pytest.mark.skipif(not ODOO_URL, reason="set ODOO_TEST_URL to run against a real Odoo")


@pytest.fixture
def odoo() -> Iterator[Any]:
    """The real connector, and a bin for everything this test puts in the instance."""
    from app.adapters.erp.odoo import OdooConnector

    connector = OdooConnector(ODOO_URL or "", ODOO_DB, ODOO_LOGIN, ODOO_PASSWORD)
    created: list[int] = []
    connector.created_here = created  # type: ignore[attr-defined]
    try:
        yield connector
    finally:
        for record_id in created:
            try:
                connector._kw("stock.landed.cost", "unlink", [record_id])
            except Exception as exc:  # cleanup must never mask the real failure
                print(f"could not clean up landed cost {record_id}: {exc}")


def _connect(client: TestClient) -> None:
    res = client.post(
        "/api/v1/erp/connection",
        json={
            "kind": "ODOO",
            "url": ODOO_URL,
            "database": ODOO_DB,
            "login": ODOO_LOGIN,
            "api_key": ODOO_PASSWORD,
        },
    )
    assert res.status_code == 201, res.text


def _add_cost(client: TestClient, container_id: str, **fields: str) -> None:
    res = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": container_id, "currency": "EUR", **fields},
    )
    assert res.status_code == 201, res.text


def test_the_whole_differential_flow_against_a_real_odoo(
    client: TestClient, db: Session, org: Organization, odoo: Any
) -> None:
    _connect(client)
    # The whole scenario is written in euros, and a landed cost is read in the ERP company's own
    # currency — which the push now refuses to guess at. An instance whose company is in USD (the
    # generic Odoo demo) would be blocked, correctly, by a check this test is not about.
    company_currency = odoo.company_currency()
    if company_currency not in (None, "EUR"):
        pytest.skip(f"this instance's company keeps its books in {company_currency}, not EUR")
    container_id = seed_container(client)  # freight FA-2026-0912, 2766.67

    first = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost")
    assert first.status_code == 201, first.text
    odoo.created_here.append(first.json()["odoo_id"])
    assert first.json()["status"] == "draft"

    # a customs invoice lands after the freight has gone
    _add_cost(
        client,
        container_id,
        cost_type="CUSTOMS_DUTY",
        amount="275.00",
        cost_date="2026-03-20",
        invoice_number="FA-2026-1104",
    )
    body = preview(client, container_id)
    assert body["pushable"] is True
    assert body["total"] == "275.00"  # the duty alone

    second = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost")
    assert second.status_code == 201, second.text
    odoo.created_here.append(second.json()["odoo_id"])
    assert second.json()["odoo_id"] != first.json()["odoo_id"]

    # Odoo's own copy: a separate document, carrying the duty and nothing else
    document = odoo._kw(
        "stock.landed.cost",
        "read",
        [second.json()["odoo_id"]],
        fields=["name", "state", "amount_total", "valuation_adjustment_lines"],
    )[0]
    assert document["state"] == "draft"
    assert float(document["amount_total"]) == 275.0
    adjustments = odoo._kw(
        "stock.valuation.adjustment.lines",
        "search_read",
        [["cost_id", "=", second.json()["odoo_id"]]],
        fields=["additional_landed_cost"],
    )
    assert [a["additional_landed_cost"] for a in adjustments] == [275.0]

    # nothing left to push
    after = preview(client, container_id)
    assert after["pushable"] is False
    assert [b["code"] for b in after["blockers"]] == ["already_pushed"]

    # forgetting the second push while its draft is still in Odoo is refused
    refused = client.delete(f"/api/v1/containers/{container_id}/erp/pushes/{second.json()['id']}")
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "ERP_DOCUMENT_STILL_THERE"
    assert refused.json()["document"]["state"] == "draft"

    # deleted in Odoo, by hand, by whoever owns those books
    odoo._kw("stock.landed.cost", "unlink", [second.json()["odoo_id"]])
    odoo.created_here.remove(second.json()["odoo_id"])

    forgotten = client.delete(
        f"/api/v1/containers/{container_id}/erp/pushes/{second.json()['id']}",
        params={"reason": "draft deleted in Odoo"},
    )
    assert forgotten.status_code == 200, forgotten.text
    assert forgotten.json()["forgotten_at"] is not None

    # and the duty is pushable again, on its own
    back = preview(client, container_id)
    assert back["pushable"] is True
    assert back["total"] == "275.00"
    third = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost")
    assert third.status_code == 201, third.text
    odoo.created_here.append(third.json()["odoo_id"])
    assert third.json()["odoo_id"] not in {first.json()["odoo_id"], second.json()["odoo_id"]}

    assert uuid.UUID(third.json()["id"]) != uuid.UUID(second.json()["id"])
