"""Deleting a company at the end of its contract: every row of it, audit log included, and not one row of
anybody else — whether the owner running it is a superuser or not."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app import erase as cli
from app.core.tenancy import set_current_org
from app.domain.audit.service import record
from app.domain.models import Membership, Organization, Supplier, User
from app.ops.erase import ErasureError, erase, find, org_tables, plan

TARIFF = "﻿Référence;Désignation;Taux de droits\nTYRE;Pneu;4,5\n".encode()


def seed(client: TestClient, org: uuid.UUID) -> None:
    """The demo company, a tariff imported, a container shared: rows in most org-scoped tables."""
    as_org = {"X-Org-Id": str(org)}
    assert client.post("/api/v1/organization/sample-data", headers=as_org).status_code == 201
    job = client.post(
        "/api/v1/imports",
        data={"kind": "PRODUCTS"},
        files={"file": ("t.csv", TARIFF, "text/csv")},
        headers=as_org,
    ).json()
    client.post(f"/api/v1/imports/{job['id']}/validate", json={"mapping": job["mapping"]}, headers=as_org)
    key = client.get(f"/api/v1/imports/{job['id']}", headers=as_org).json()["preview_key"]
    committed = client.post(f"/api/v1/imports/{job['id']}/commit", json={"preview_key": key}, headers=as_org)
    assert committed.status_code == 200, committed.text
    box = client.get("/api/v1/containers", headers=as_org).json()[0]["id"]
    assert client.post(f"/api/v1/containers/{box}/share", json={}, headers=as_org).status_code == 201


@pytest.fixture
def two(client: TestClient, db: Session, org_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Two companies with the same data; the first has a user of its own and one it shares."""
    other = uuid.uuid4()
    seed(client, org_id)
    seed(client, other)
    db.execute(
        text("UPDATE organizations SET name = 'Roulez Bio SAS', slug = 'roulez-bio' WHERE id = :o"),
        {"o": org_id},
    )
    alone = User(external_id=f"user_{uuid.uuid4().hex}", email="daf@roulezbio.example")
    shared = User(external_id=f"user_{uuid.uuid4().hex}", email="consultant@example.com")
    db.add_all([alone, shared])
    db.flush()
    db.add_all(
        [
            Membership(org_id=org_id, user_id=alone.id),
            Membership(org_id=org_id, user_id=shared.id),
            Membership(org_id=other, user_id=shared.id),
        ]
    )
    db.flush()
    return org_id, other


def rows_of(conn: Connection, org: uuid.UUID) -> dict[str, int]:
    """Every org-scoped table's rows of `org`, counted as the superuser the tests connect as."""
    return {
        table: conn.execute(
            text(f'SELECT count(*) FROM "{table}" WHERE org_id = :o'), {"o": org}
        ).scalar_one()
        for table in org_tables(conn)
    }


def locks_in_force(conn: Connection) -> None:
    assert (
        conn.execute(text("SELECT tgenabled FROM pg_trigger WHERE tgname = 'audit_log_append_only'")).scalar()
        == "O"
    )
    unforced = [
        table
        for table in org_tables(conn)
        if table != "memberships"
        and conn.execute(
            text("SELECT NOT (relrowsecurity AND relforcerowsecurity) FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).scalar_one()
    ]
    assert unforced == []
    with pytest.raises(DBAPIError, match="append-only"), conn.begin_nested():
        conn.execute(text("DELETE FROM audit_log"))


def test_a_dry_run_counts_and_deletes_nothing(db: Session, two: tuple[uuid.UUID, uuid.UUID]) -> None:
    mine, _ = two
    conn = db.connection()
    before = rows_of(conn, mine)
    found = plan(conn, find(conn, "roulez-bio"))
    assert found.rows == before
    assert sum(1 for count in before.values() if count) >= 15  # the test seeds what it claims to
    assert len(found.users) == 1 and found.documents_in_object_storage == 0
    assert rows_of(conn, mine) == before


def test_a_wrong_confirmation_is_refused(db: Session, two: tuple[uuid.UUID, uuid.UUID]) -> None:
    mine, _ = two
    conn = db.connection()
    before = rows_of(conn, mine)
    for wrong in ("Roulez Bio", "roulez-bio ", str(mine), ""):
        with pytest.raises(ErasureError, match="exact name or slug"):
            erase(conn, find(conn, str(mine)), confirm=wrong)
    assert rows_of(conn, mine) == before


def deleted_whole(conn: Connection, mine: uuid.UUID, other: uuid.UUID, other_rows: dict[str, int]) -> None:
    assert set(rows_of(conn, mine).values()) == {0}
    assert rows_of(conn, other) == other_rows
    assert conn.execute(text("SELECT count(*) FROM organizations WHERE id = :o"), {"o": mine}).scalar() == 0
    emails = conn.scalars(
        text("SELECT email FROM users WHERE email IN ('daf@roulezbio.example', 'consultant@example.com')")
    )
    assert list(emails) == ["consultant@example.com"]  # the one who also works for the other company stays
    locks_in_force(conn)


def test_deleting_a_company_leaves_nothing_of_it_and_everything_of_the_other(
    db: Session, two: tuple[uuid.UUID, uuid.UUID]
) -> None:
    mine, other = two
    conn = db.connection()
    other_rows = rows_of(conn, other)
    done = erase(conn, find(conn, str(mine)), confirm="Roulez Bio SAS")
    assert done.rows["audit_log"] > 0
    deleted_whole(conn, mine, other, other_rows)


def test_an_owner_who_is_not_a_superuser_deletes_it_too(
    db: Session, two: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Row-level security binds such an owner: the tenant setting is what lets it see the rows."""
    mine, other = two
    conn = db.connection()
    other_rows = rows_of(conn, other)
    owner = f"erase_owner_{uuid.uuid4().hex[:8]}"
    conn.execute(text(f"CREATE ROLE {owner} NOSUPERUSER NOBYPASSRLS"))
    for table in conn.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")):
        conn.execute(text(f'ALTER TABLE "{table}" OWNER TO {owner}'))
    conn.execute(text(f"SET ROLE {owner}"))
    try:
        conn.execute(text("SELECT set_config('app.org_id', '', true)"))
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar() == 0  # bound by RLS
        erase(conn, find(conn, "roulez-bio"), confirm="roulez-bio")
    finally:
        conn.execute(text("RESET ROLE"))
    deleted_whole(conn, mine, other, other_rows)


@pytest.mark.parametrize(
    ("pointing", "refusal"),
    [
        # a cascade bypasses row-level security and any WHERE: it would quietly clear the other's row
        ("UPDATE costs SET import_job_id = (SELECT id FROM import_jobs WHERE org_id = :mine LIMIT 1) "
         "WHERE org_id = :other", r"costs: \(\d+, [1-9]\d*\) deleted/updated"),
        # without one, Postgres refuses the deletion itself
        ("UPDATE containers SET shipment_id = (SELECT id FROM shipments WHERE org_id = :mine LIMIT 1) "
         "WHERE org_id = :other", "another row still points"),
    ],
)  # fmt: skip
def test_a_row_of_another_company_pointing_at_this_one_refuses_it(
    db: Session, two: tuple[uuid.UUID, uuid.UUID], pointing: str, refusal: str
) -> None:
    mine, other = two
    conn = db.connection()
    conn.execute(text(pointing), {"mine": mine, "other": other})
    before = rows_of(conn, mine)
    with pytest.raises(ErasureError, match=refusal), conn.begin_nested():
        erase(conn, find(conn, str(mine)), confirm="roulez-bio")
    assert rows_of(conn, mine) == before


# ---------------------------------------------------------------------------- the command, committed


def test_the_command_runs_as_the_owner_dry_by_default(
    database_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Committed for real, as production would: the dry run deletes nothing, a wrong confirmation
    nothing either, the right one everything — and after the commit the audit log is locked again."""
    monkeypatch.setenv("MIGRATIONS_DATABASE_URL", database_url)
    seeding = create_engine(database_url)
    ids: list[uuid.UUID] = []
    try:
        with Session(seeding) as session:
            for name in ("Erase Me SAS", "Keep Me SAS"):
                org = Organization(name=name, slug=f"{name.split()[0].lower()}-{uuid.uuid4().hex[:6]}")
                session.add(org)
                session.flush()
                set_current_org(session, org.id)
                session.add(Supplier(org_id=org.id, name="Ningbo Tyres"))
                record(session, org.id, actor_user_id=None, action="test.event", entity_type="organization")
                ids.append(org.id)
            session.commit()
        doomed, kept = ids

        assert cli.main([str(doomed)]) == 0
        assert "dry run, nothing deleted" in capsys.readouterr().out
        assert cli.main([str(doomed), "--confirm", "Keep Me SAS"]) == 2
        assert "exact name or slug" in capsys.readouterr().err
        with seeding.connect() as conn:
            assert rows_of(conn, doomed)["audit_log"] == 1

        assert cli.main([str(doomed), "--confirm", "Erase Me SAS"]) == 0
        with seeding.connect() as conn:
            assert set(rows_of(conn, doomed).values()) == {0}
            assert rows_of(conn, kept)["audit_log"] == 1 and rows_of(conn, kept)["suppliers"] == 1
            locks_in_force(conn)
    finally:
        with seeding.connect() as conn, conn.begin():
            for left in ids:
                if conn.execute(text("SELECT 1 FROM organizations WHERE id = :o"), {"o": left}).first():
                    found = find(conn, str(left))
                    erase(conn, found, confirm=found.name)
        seeding.dispose()


def test_the_command_will_not_run_without_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATIONS_DATABASE_URL", raising=False)
    assert cli.main([str(uuid.uuid4())]) == 2
