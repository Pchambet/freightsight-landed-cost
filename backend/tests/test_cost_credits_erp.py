"""A refund on a cost already in the customer's ERP: their books hold the old figure, and it is said."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from test_erp_landed_cost import (
    DOCUMENT,
    WritableOdoo,
    connect,
    erp_key,  # noqa: F401 - the ERP's encryption key, for every test here
    preview,
    seed_container,
    validate_and_forget,
)

from app.domain.models import ErpPush, Organization


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


def test_a_credit_on_a_cost_pushed_and_validated_shows_as_a_changed_amount(
    client: TestClient, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    odoo = WritableOdoo()
    connect(client, odoo, monkeypatch)
    container_id = seed_container(client)
    push = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost")
    assert push.status_code == 201, push.text
    odoo.documents[push.json()["odoo_id"]]["state"] = "done"

    (cost,) = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()["costs"]
    res = client.post(
        f"/api/v1/costs/{cost['id']}/credits",
        json={"amount": "266.67", "credit_number": "AV-2026-07", "credit_date": "2026-04-02"},
    )
    assert res.status_code == 201, res.text

    (changed,) = [b for b in preview(client, container_id)["blockers"] if b["code"] == "pushed_cost_changed"]
    assert changed["params"]["existing"] == DOCUMENT
    assert "2766.67 → 2500.00" in changed["params"]["changes"]

    # forgotten with a reason, the next push carries the refund alone, as a negative adjustment
    validate_and_forget(client, odoo, container_id, push.json())
    body = preview(client, container_id)
    assert body["total"] == "-266.67"


def test_a_difference_entered_by_hand_is_remembered_so_the_next_refund_asks_only_for_its_own(
    client: TestClient, db: Session, org: Organization, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A push validated before its split per receipt line was kept: each refund since is a difference
    a person enters in the ERP by hand. Once they say it is entered, it counts as what the ERP holds:
    the next refund asks for its own amount, not again for both."""
    odoo = WritableOdoo()
    connect(client, odoo, monkeypatch)
    container_id = seed_container(client)
    push = client.post(f"/api/v1/containers/{container_id}/erp/landed-cost").json()
    row = db.get(ErpPush, uuid.UUID(push["id"]))
    assert row is not None
    row.carried = {
        key: {"amount": value["amount"], "label": value["label"]} for key, value in row.carried.items()
    }
    db.commit()
    validate_and_forget(client, odoo, container_id, push)
    (cost,) = client.get(f"/api/v1/landed-costs/containers/{container_id}").json()["costs"]

    def refund(amount: str, number: str) -> None:
        res = client.post(
            f"/api/v1/costs/{cost['id']}/credits",
            json={"amount": amount, "credit_number": number, "credit_date": "2026-04-02"},
        )
        assert res.status_code == 201, res.text

    def to_enter() -> str | None:
        (planned,) = preview(client, container_id)["costs"]
        return planned["by_hand"]["difference"] if planned["by_hand"] else None

    refund("266.67", "AV-1")
    assert to_enter() == "-266.67"
    url = f"/api/v1/containers/{container_id}/erp/landed-cost/by-hand"
    # a screen left open on an older figure is refused rather than recorded
    stale = client.post(url, json={"cost_id": cost["id"], "difference": "-100.00"})
    assert (stale.status_code, stale.json()["code"]) == (422, "ERP_BY_HAND_CHANGED")
    entered = client.post(url, json={"cost_id": cost["id"], "difference": "-266.67"})
    assert entered.status_code == 200, entered.text
    assert to_enter() is None
    entries = client.get("/api/v1/audit-log", params={"entity_id": container_id}).json()["entries"]
    (said,) = [e for e in entries if e["action"] == "erp.by_hand_entered"]
    assert (said["after"]["difference"], said["after"]["held"]) == ("-266.67", "2500.00")

    refund("100.00", "AV-2")
    assert to_enter() == "-100.00"
    nothing = client.post(url, json={"cost_id": str(uuid.uuid4()), "difference": "-100.00"})
    assert (nothing.status_code, nothing.json()["code"]) == (422, "ERP_BY_HAND_NOTHING_TO_ENTER")
