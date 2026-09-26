"""ERP connections and their sync runs.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ["ODOO"]
STATUSES = ["RUNNING", "SUCCEEDED", "FAILED"]

RLS_TABLES = ["erp_connections", "erp_sync_runs"]
APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*KINDS, name="erp_kind").create(bind, checkfirst=True)
    postgresql.ENUM(*STATUSES, name="erp_sync_status").create(bind, checkfirst=True)

    op.create_table(
        "erp_connections",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("kind", postgresql.ENUM(name="erp_kind", create_type=False), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("database", sa.String(), nullable=False),
        sa.Column("login", sa.String(), nullable=False),
        # Sealed with AES-GCM: a customer's ERP credential never sits in this table in clear.
        sa.Column("api_key_sealed", sa.LargeBinary(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String()),
        sa.Column("server_version", sa.String()),
        sa.Column("company", sa.String()),
        sa.Column("created_by", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", name="uq_erp_connections_org_id"),
    )
    op.create_table(
        "erp_sync_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "connection_id",
            sa.UUID(),
            sa.ForeignKey("erp_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="erp_sync_status", create_type=False),
            nullable=False,
            server_default="RUNNING",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("import_job_id", sa.UUID(), sa.ForeignKey("import_jobs.id")),
        sa.Column("purchase_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lines", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String()),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_erp_sync_runs_org_started", "erp_sync_runs", ["org_id", "started_at"])

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_index("ix_erp_sync_runs_org_started", table_name="erp_sync_runs")
    op.drop_table("erp_sync_runs")
    op.drop_table("erp_connections")
    bind = op.get_bind()
    postgresql.ENUM(name="erp_sync_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="erp_kind").drop(bind, checkfirst=True)
