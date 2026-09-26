"""Every table that carries an org_id is under Row Level Security, forced, with the tenant policy.

The per-table tests prove isolation for the tables someone thought of. This one is for the table
nobody thought of: a migration that adds an org-scoped table and forgets the three statements
would leak across tenants silently, and only a test that asks the catalogue can notice.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Tables whose org_id is deliberately not the tenant boundary. Each one needs a reason.
EXEMPT: dict[str, str] = {
    # Read by primary key (org, user) while resolving the caller, before any tenant is known — a
    # policy keyed on the tenant setting would refuse the very lookup that establishes it. No API
    # endpoint reads or lists it; verified when this test was written (app/core/auth.py only).
    "memberships": "resolved before the tenant setting exists; only ever read by primary key",
}


#: Policies other than `tenant_isolation` on an org-scoped table. A permissive policy WIDENS access
#: (it is OR-ed with the tenant one), so each is named here with its reason, and nothing else passes.
EXTRA_POLICIES: dict[tuple[str, str], str] = {
    ("shared_reports", "share_token_read"): (
        "SELECT only: the public page has no tenant; it reads the one row whose token hash it set"
    ),
    ("shared_report_views", "share_token_view"): (
        "INSERT only: recording that a share was opened, for the share whose token is set"
    ),
}


def test_no_org_scoped_table_carries_a_policy_nobody_vouched_for(db: Session) -> None:
    """The test above asks whether the tenant policy exists. This one asks what else does: a second
    permissive policy on `costs` or `invoices` would open them wider and pass every other test."""
    rows = db.execute(
        text(
            """
            SELECT p.tablename, p.policyname, p.cmd
            FROM pg_policies p
            WHERE p.schemaname = 'public' AND p.policyname <> 'tenant_isolation'
              AND EXISTS (
                  SELECT 1 FROM information_schema.columns c
                  WHERE c.table_schema = 'public' AND c.table_name = p.tablename AND c.column_name = 'org_id'
              )
            ORDER BY p.tablename, p.policyname
            """
        )
    ).all()
    unknown = [(t, p) for t, p, _ in rows if (t, p) not in EXTRA_POLICIES]
    assert not unknown, f"policies nobody vouched for: {unknown}"
    # and the two doors are exactly as narrow as their reason says
    commands = {(t, p): cmd for t, p, cmd in rows}
    assert commands[("shared_reports", "share_token_read")] == "SELECT"
    assert commands[("shared_report_views", "share_token_view")] == "INSERT"


def test_every_org_scoped_table_is_under_forced_rls_with_the_tenant_policy(db: Session) -> None:
    tables = [
        row[0]
        for row in db.execute(
            text(
                """
                SELECT c.relname
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE a.attname = 'org_id' AND NOT a.attisdropped
                  AND c.relkind = 'r' AND n.nspname = 'public'
                ORDER BY c.relname
                """
            )
        )
    ]
    assert tables, "no org-scoped table found: the query or the schema is wrong"

    missing: list[str] = []
    for table in tables:
        if table in EXEMPT:
            continue
        enabled, forced = db.execute(
            text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = :t"),
            {"t": table},
        ).one()
        policies = db.execute(
            text("SELECT count(*) FROM pg_policies WHERE tablename = :t AND policyname = 'tenant_isolation'"),
            {"t": table},
        ).scalar_one()
        if not (enabled and forced and policies == 1):
            missing.append(f"{table} (enabled={enabled}, forced={forced}, policy={policies})")
    assert not missing, "org-scoped tables without full RLS: " + ", ".join(missing)


def test_the_application_role_cannot_bypass_rls(db: Session) -> None:
    row = db.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'freightsight_app'")
    ).one_or_none()
    assert row is not None, "the application role is missing"
    assert row == (False, False)
