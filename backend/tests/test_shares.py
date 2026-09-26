"""A landed-cost report shared by link: a frozen snapshot, opened by its token and by nothing else."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import SharedReport
from app.domain.shares.service import hash_token, open_by_token

PUBLIC = "/api/v1/public/shared-reports/open"
NOBODY = {"X-Org-Id": ""}  # the public page sends no organization at all


def open_link(client: TestClient, token: str) -> Any:
    return client.post(PUBLIC, json={"token": token}, headers=NOBODY)


@pytest.fixture
def sample(client: TestClient) -> dict[str, Any]:
    res = client.post("/api/v1/organization/sample-data")
    assert res.status_code == 201, res.text
    body: dict[str, Any] = res.json()
    return body


def share(client: TestClient, container_id: str, **body: int) -> dict[str, Any]:
    res = client.post(f"/api/v1/containers/{container_id}/share", json=body)
    assert res.status_code == 201, res.text
    created: dict[str, Any] = res.json()
    return created


def test_the_link_opens_the_report_as_it_stood_and_a_later_cost_does_not_move_it(
    client: TestClient, sample: dict[str, Any]
) -> None:
    box = sample["container_ids"][0]
    live = client.get(f"/api/v1/landed-costs/containers/{box}").json()
    created = share(client, box)
    assert created["path"] == f"/r/{created['token']}" and len(created["token"]) >= 40

    late = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": box, "cost_type": "INSPECTION", "amount": "145.00",
              "currency": "EUR", "cost_date": "2026-09-10"},
    )  # fmt: skip
    assert late.status_code == 201, late.text
    assert client.get(f"/api/v1/landed-costs/containers/{box}").json()["totals"] != live["totals"]

    opened = open_link(client, created["token"])
    assert opened.status_code == 200, opened.text
    assert opened.headers["cache-control"] == "no-store" and "noindex" in opened.headers["x-robots-tag"]
    body = opened.json()
    assert body["report"]["totals"] == live["totals"]  # the figure that was sent, not today's
    assert body["report"]["lines"] == live["lines"]
    assert (body["subject_type"], body["subject_label"]) == ("container", "MSCU4821990")
    assert body["shipment_reference"] == "MEDUSH2604417" and body["locale"] == "fr"
    assert body["sample_data"] is True  # sent from a demo company: the document has to say so
    assert body["issuer"]["name"]


def test_an_order_can_be_shared_too(client: TestClient, sample: dict[str, Any]) -> None:
    order = next(
        po for po in client.get("/api/v1/purchase-orders").json() if po["po_number"] == "PO-2026-014"
    )
    created = client.post(f"/api/v1/purchase-orders/{order['id']}/share", json={"expires_in_days": 7})
    assert created.status_code == 201, created.text
    body = open_link(client, created.json()["token"]).json()
    assert (body["subject_type"], body["subject_label"]) == ("purchase_order", "PO-2026-014")
    assert body["report"]["totals"]["fob"] == "27300.00"


def test_the_sender_sees_that_it_was_read_and_can_take_the_link_back(
    client: TestClient, sample: dict[str, Any]
) -> None:
    box = sample["container_ids"][0]
    created = share(client, box, expires_in_days=90)
    for _ in range(3):
        assert open_link(client, created["token"]).status_code == 200
    (listed,) = client.get(f"/api/v1/containers/{box}/shares").json()
    assert (listed["status"], listed["view_count"]) == ("active", 3)
    assert listed["last_viewed_at"] is not None and "token" not in listed

    assert client.delete(f"/api/v1/shares/{created['id']}").status_code == 204
    gone = open_link(client, created["token"])
    assert gone.status_code == 404 and gone.json()["code"] == "SHARE_NOT_FOUND"
    (listed,) = client.get(f"/api/v1/containers/{box}/shares").json()
    assert (listed["status"], listed["view_count"]) == ("revoked", 3)
    actions = [
        e["action"] for e in client.get("/api/v1/audit-log", params={"entity_id": box}).json()["entries"]
    ]
    assert actions[:2] == ["share.revoked", "container.shared"]


def test_unknown_expired_and_revoked_are_one_and_the_same_answer(
    client: TestClient, db: Session, sample: dict[str, Any]
) -> None:
    created = share(client, sample["container_ids"][0], expires_in_days=1)
    row = db.scalar(select(SharedReport).where(SharedReport.id == uuid.UUID(created["id"])))
    assert row is not None
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    answers = [
        open_link(client, created["token"]),  # expired
        open_link(client, "x" * 43),  # never existed
        open_link(client, "short"),  # not even the shape of one
    ]
    assert {(a.status_code, a.json()["code"]) for a in answers} == {(404, "SHARE_NOT_FOUND")}
    (listed,) = client.get(f"/api/v1/containers/{sample['container_ids'][0]}/shares").json()
    assert (listed["status"], listed["view_count"]) == ("expired", 0)


def test_the_database_holds_no_token_only_its_hash(
    client: TestClient, db: Session, sample: dict[str, Any]
) -> None:
    created = share(client, sample["container_ids"][0])
    row = db.execute(text("SELECT * FROM shared_reports")).mappings().one()
    assert row["token_hash"] == hash_token(created["token"])
    assert created["token"] not in str(dict(row))


def test_another_organization_neither_lists_nor_revokes_our_links(
    client: TestClient, sample: dict[str, Any]
) -> None:
    box = sample["container_ids"][0]
    created = share(client, box)
    theirs = {"X-Org-Id": str(uuid.uuid4())}
    assert client.get(f"/api/v1/containers/{box}/shares", headers=theirs).status_code == 404
    assert client.delete(f"/api/v1/shares/{created['id']}", headers=theirs).status_code == 404
    assert open_link(client, created["token"]).status_code == 200


def test_under_the_application_role_the_token_opens_its_own_row_and_no_other(
    client: TestClient, db: Session, org_id: uuid.UUID, sample: dict[str, Any]
) -> None:
    """The door through row-level security, tried as the role production runs as: with no tenant set,
    the right token reaches its snapshot, a wrong one reaches nothing, and the table stays shut."""
    first = share(client, sample["container_ids"][0])
    share(client, sample["container_ids"][1])
    db.commit()
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, None)
        assert list(db.scalars(select(SharedReport))) == []  # no tenant, no token: nothing
        opened = open_by_token(db, first["token"])
        assert opened is not None and opened[0]["subject_label"] == "MSCU4821990"
        assert list(db.scalars(select(SharedReport))) == []  # and the door closed behind it
        assert open_by_token(db, "y" * 43) is None
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)


def test_a_redacted_link_does_not_carry_the_forwarders_names_at_all(
    client: TestClient, db: Session, sample: dict[str, Any]
) -> None:
    """Hidden on a page is readable in the response. Redacted means it is not in the snapshot."""
    box = sample["container_ids"][0]
    typed = client.post(
        "/api/v1/costs",
        json={"scope": "CONTAINER", "target_id": box, "cost_type": "INSPECTION", "amount": "145.00",
              "currency": "EUR", "cost_date": "2026-09-10", "vendor": "TRANSDEMO SAS",
              "invoice_number": "FA-2026-0912", "notes": "scanner du 9"},
    )  # fmt: skip
    assert typed.status_code == 201, typed.text
    plain = share(client, box)
    redacted = client.post(f"/api/v1/containers/{box}/share", json={"redact_vendors": True}).json()
    assert redacted["landed"] == plain["landed"] and redacted["generated_at"]

    seen = open_link(client, redacted["token"])
    assert seen.json()["redacted"] is True
    assert (
        "TRANSDEMO" not in seen.text and "FA-2026-0912" not in seen.text and "scanner du 9" not in seen.text
    )
    assert seen.json()["report"]["totals"]["landed"] == plain["landed"]  # the figures are all there
    stored = (
        db.execute(text("SELECT snapshot::text FROM shared_reports ORDER BY created_at DESC"))
        .scalars()
        .first()
    )
    assert stored is not None and "TRANSDEMO" not in stored
    assert "TRANSDEMO" in open_link(client, plain["token"]).text


def test_a_token_holder_can_read_one_row_and_change_none(
    client: TestClient, db: Session, org_id: uuid.UUID, sample: dict[str, Any]
) -> None:
    """The write door does not exist. With the token set and no tenant, as the role production runs
    as: the share cannot be un-revoked, extended, rewritten or moved to another organization, and the
    only thing that can be written is "opened now", for that share and no other."""
    mine = share(client, sample["container_ids"][0])
    other = share(client, sample["container_ids"][1])
    db.commit()
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, None)
        db.execute(
            text("SELECT set_config('app.share_token_hash', :h, true)"), {"h": hash_token(mine["token"])}
        )
        # RETURNING rather than a row count: what comes back is what was touched
        rewritten = db.execute(
            text(
                "UPDATE shared_reports SET revoked_at = NULL, expires_at = now() + interval '10 years', "
                "snapshot = '{}'::jsonb, org_id = :other RETURNING id"
            ),
            {"other": uuid.uuid4()},
        ).all()
        assert rewritten == []
        assert db.execute(text("DELETE FROM shared_reports RETURNING id")).all() == []
        with pytest.raises(Exception, match="row-level security"):
            db.execute(
                text("INSERT INTO shared_report_views (id, org_id, share_id) VALUES (:id, :org, :share)"),
                {"id": uuid.uuid4(), "org": org_id, "share": uuid.UUID(other["id"])},
            )
    finally:
        db.rollback()
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)


def test_under_the_application_role_another_organization_sees_no_share_of_ours(
    client: TestClient, db: Session, org_id: uuid.UUID, sample: dict[str, Any]
) -> None:
    share(client, sample["container_ids"][0])
    db.commit()
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert len(list(db.scalars(select(SharedReport)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(SharedReport))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)


def test_the_token_never_reaches_a_log_line_or_a_url() -> None:
    from app.core.observability import loggable_path

    assert loggable_path("/api/v1/public/shared-reports/open") == "/api/v1/public/shared-reports/open"
    leaked = loggable_path("/api/v1/public/shared-reports/hX9kQ2_long-bearer-token")
    assert leaked == "/api/v1/public/shared-reports/[redacted]"
    assert loggable_path("/api/v1/containers/123") == "/api/v1/containers/123"


def test_a_flood_is_answered_without_the_database() -> None:
    from app.core.ratelimit import SlidingWindow

    window = SlidingWindow(limit=3, seconds=60)
    assert [window.allow("1.2.3.4", now=t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert window.allow("5.6.7.8", now=3) is True  # somebody else is not held back
    assert window.allow("1.2.3.4", now=61) is True  # and the window slides


def test_an_organization_creates_at_most_so_many_links_an_hour_and_another_is_not_held_back(
    client: TestClient, sample: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A loop sharing links holds the one API process everybody waits on. The limit is counted per
    organization, before the report is computed, and a subject that does not exist uses none of it."""
    from app.api.v1 import shares as shares_api
    from app.core.ratelimit import SlidingWindow

    monkeypatch.setattr(shares_api, "_created", SlidingWindow(limit=2, seconds=3600))
    box = sample["container_ids"][0]
    assert client.post(f"/api/v1/containers/{uuid.uuid4()}/share", json={}).status_code == 404
    assert [client.post(f"/api/v1/containers/{box}/share", json={}).status_code for _ in range(2)] == [
        201,
        201,
    ]
    third = client.post(f"/api/v1/containers/{box}/share", json={})
    assert (third.status_code, third.json()["code"]) == (429, "RATE_LIMITED")
    period = {"period_from": "2026-01-01", "period_to": "2026-03-31"}
    assert client.post("/api/v1/reports/audit/share", json=period).status_code == 429  # the same hour
    elsewhere = client.post(
        "/api/v1/reports/audit/share", json=period, headers={"X-Org-Id": str(uuid.uuid4())}
    )
    assert elsewhere.status_code == 201  # another organization's hour is its own
