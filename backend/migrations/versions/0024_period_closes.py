"""Closing a month: the landed cost of what arrived in it, frozen as it went into the accounts.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "period_closes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("period", sa.CHAR(7), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("closed_by", sa.UUID()),
        sa.Column("base_currency", sa.CHAR(3), nullable=False),
        sa.Column("containers", sa.Integer(), nullable=False),
        sa.Column("fob", sa.Numeric(16, 2), nullable=False),
        sa.Column("landed", sa.Numeric(16, 2), nullable=False),
        sa.Column("by_cost_type", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint("org_id", "period", name="uq_period_closes_org_period"),
    )
    op.create_table(
        "period_close_lines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "close_id",
            sa.UUID(),
            sa.ForeignKey("period_closes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("container_id", sa.UUID(), nullable=False),
        sa.Column("container_number", sa.String(), nullable=False),
        sa.Column("load_id", sa.UUID(), nullable=False),
        sa.Column("po_number", sa.String(), nullable=False),
        sa.Column("sku", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("fob", sa.Numeric(16, 2), nullable=False),
        sa.Column("landed", sa.Numeric(16, 2), nullable=False),
        sa.Column("by_cost_type", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    for table in ("period_closes", "period_close_lines"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_table("period_close_lines")
    op.drop_table("period_closes")
