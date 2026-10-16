"""Deleting a company: every row it has in the database, in one transaction.

What this has to get right, and how:

  * **Every org-scoped table, found in the catalogue** — the tables with an `org_id` column, the way
    `tests/test_rls_coverage.py` finds them — so a table added next year is deleted without editing
    this file. Children go before their parents, in the order the foreign keys give.
  * **Nothing of another organization.** Each DELETE names the organization, and runs with the tenant
    setting set to it, so row-level security confines it too when the owner is not a superuser. The
    foreign keys' own cascades bypass both; so before committing, the transaction asks Postgres what
    it deleted and updated, table by table, and rolls everything back unless that is exactly this
    organization's rows.
  * **The audit log's lock is lifted for this transaction only.** Its append-only trigger is disabled
    just before its rows go and enabled again straight after. ALTER TABLE is transactional: a failure
    anywhere leaves the trigger as it was. Row-level security is never lifted — the tenant setting is
    what lets an owner without superuser rights see the organization's rows, and only those.

The organization's own row goes too, and so do its users who belong to no other organization.
"""

from __future__ import annotations

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from uuid import UUID

from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

AUDIT_TABLE = "audit_log"
AUDIT_TRIGGER = "audit_log_append_only"
#: Long enough for a quiet moment, short enough not to queue the API behind this for long.
LOCK_TIMEOUT = "10s"


class ErasureError(RuntimeError):
    pass


@dataclass(frozen=True)
class Organization:
    id: UUID
    name: str
    slug: str | None
    external_id: str | None


@dataclass(frozen=True)
class Plan:
    organization: Organization
    #: Rows per org-scoped table, in the order they are deleted.
    rows: dict[str, int]
    #: The users who belong to this organization only, deleted with it.
    users: list[UUID]
    #: Documents whose bytes live in object storage, under `<org id>/`: the database forgets them, the
    #: bucket does not.
    documents_in_object_storage: int


def find(conn: Connection, ref: str) -> Organization:
    """The organization by its id, its Clerk id or its slug."""
    row = conn.execute(
        text(
            "SELECT id, name, slug, external_id FROM organizations "
            "WHERE id::text = :ref OR external_id = :ref OR slug = :ref"
        ),
        {"ref": ref},
    ).all()
    if len(row) != 1:
        raise ErasureError(f"no single organization is {ref!r} (found {len(row)})")
    return Organization(*row[0])


def org_tables(conn: Connection) -> list[str]:
    """Every table with an `org_id`, children before parents."""
    tables = set(
        conn.scalars(
            text(
                """
                SELECT c.relname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE a.attname = 'org_id' AND NOT a.attisdropped
                  AND c.relkind = 'r' AND n.nspname = 'public'
                """
            )
        )
    )
    if not tables:
        raise ErasureError("no org-scoped table found: the query or the schema is wrong")
    references = conn.execute(
        text(
            """
            SELECT conrelid::regclass::text, confrelid::regclass::text
            FROM pg_constraint
            WHERE contype = 'f' AND connamespace = 'public'::regnamespace
            """
        )
    ).all()
    # A parent waits for every table that points at it.
    children: dict[str, set[str]] = {table: set() for table in tables}
    for child, parent in references:
        if child in tables and parent in tables and child != parent:
            children[parent].add(child)
    try:
        order = list(TopologicalSorter(children).static_order())
    except CycleError as exc:
        raise ErasureError(f"org-scoped tables reference each other in a cycle: {exc.args[1]}") from exc
    # The audit log is last: its trigger is lifted for as short a time as possible.
    return [table for table in order if table != AUDIT_TABLE] + [AUDIT_TABLE]


def _as_tenant(conn: Connection, org: Organization) -> None:
    conn.execute(text("SELECT set_config('app.org_id', :org, true)"), {"org": str(org.id)})


def plan(conn: Connection, org: Organization) -> Plan:
    """What deleting it would delete. Reads only."""
    _as_tenant(conn, org)
    rows = {
        table: conn.execute(
            text(f"SELECT count(*) FROM {_quoted(conn, table)} WHERE org_id = :org"), {"org": org.id}
        ).scalar_one()
        for table in org_tables(conn)
    }
    users = list(
        conn.scalars(
            text(
                "SELECT m.user_id FROM memberships m WHERE m.org_id = :org AND NOT EXISTS "
                "(SELECT 1 FROM memberships o WHERE o.user_id = m.user_id AND o.org_id <> :org) "
                "ORDER BY m.user_id"
            ),
            {"org": org.id},
        )
    )
    in_storage = conn.execute(
        text("SELECT count(*) FROM documents WHERE org_id = :org AND storage <> 'postgres'"), {"org": org.id}
    ).scalar_one()
    return Plan(org, rows, users, in_storage)


def erase(conn: Connection, org: Organization, *, confirm: str) -> Plan:
    """Delete the organization, inside the caller's transaction; the caller commits. Refused unless
    `confirm` is its exact name or slug. Raises — and the caller must roll back — when anything other
    than this organization's rows would go."""
    if confirm not in {org.name, org.slug}:
        raise ErasureError("the confirmation is not the organization's exact name or slug")
    conn.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    planned = plan(conn, org)
    before = _changes(conn)
    try:
        for table in planned.rows:
            if table == AUDIT_TABLE:
                conn.execute(text(f"ALTER TABLE {AUDIT_TABLE} DISABLE TRIGGER {AUDIT_TRIGGER}"))
            conn.execute(text(f"DELETE FROM {_quoted(conn, table)} WHERE org_id = :org"), {"org": org.id})
            if table == AUDIT_TABLE:
                conn.execute(text(f"ALTER TABLE {AUDIT_TABLE} ENABLE TRIGGER {AUDIT_TRIGGER}"))
        if planned.users:
            conn.execute(text("DELETE FROM users WHERE id = ANY(:ids)"), {"ids": planned.users})
        conn.execute(text("DELETE FROM organizations WHERE id = :org"), {"org": org.id})
    except IntegrityError as exc:  # a row of someone else points at one of these, without a cascade
        raise ErasureError(f"refused, another row still points at this organization's: {exc.orig}") from exc

    after = _changes(conn)
    done = {
        table: (d - before.get(table, (0, 0))[0], u - before.get(table, (0, 0))[1])
        for table, (d, u) in after.items()
    }
    expected = {table: (count, 0) for table, count in planned.rows.items()}
    expected |= {"organizations": (1, 0), "users": (len(planned.users), 0)}
    wrong = sorted(
        f"{table}: {done.get(table, (0, 0))} deleted/updated, expected {expected.get(table, (0, 0))}"
        for table in done.keys() | expected.keys()
        if done.get(table, (0, 0)) != expected.get(table, (0, 0))
    )
    if wrong:
        raise ErasureError("refused, rows beyond this organization would change: " + "; ".join(wrong))
    return planned


def _changes(conn: Connection) -> dict[str, tuple[int, int]]:
    """Rows this transaction has deleted and updated so far, per table — cascades included, whoever's
    rows they were, which no query under row-level security could see."""
    rows = conn.execute(
        text("SELECT relname, n_tup_del, n_tup_upd FROM pg_stat_xact_user_tables WHERE schemaname = 'public'")
    ).all()
    return {name: (deleted, updated) for name, deleted, updated in rows}


def _quoted(conn: Connection, table: str) -> str:
    return conn.dialect.identifier_preparer.quote(table)
