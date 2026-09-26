"""Row Level Security: a raw query without the tenant variable, or with another org's id, sees nothing.
Superusers bypass RLS, so the check runs as the application role created by migration 0003."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.tenancy import set_current_org
from app.domain.models import Container, Cost


def test_rls_blocks_cross_org_reads_and_writes(client: TestClient, db: Session, org_id: uuid.UUID) -> None:
    res = client.post("/api/v1/containers", json={"container_number": "MSCU1234567"})
    assert res.status_code == 201
    container_id = res.json()["id"]

    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org_id)
        assert [str(c.id) for c in db.scalars(select(Container))] == [container_id]

        set_current_org(db, None)
        assert list(db.scalars(select(Container))) == []

        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(Container))) == []
        assert list(db.scalars(select(Cost))) == []

        # an insert for a foreign org is rejected by the policy's WITH CHECK
        nested = db.begin_nested()
        try:
            db.execute(
                text(
                    "INSERT INTO containers (id, org_id, container_number) "
                    "VALUES (gen_random_uuid(), :o, 'CMAU9876543')"
                ),
                {"o": str(org_id)},
            )
            raised = False
        except Exception:
            raised = True
        finally:
            nested.rollback()
        assert raised
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org_id)
