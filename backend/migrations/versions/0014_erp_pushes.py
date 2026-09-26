"""What we have written into a customer's ERP, so we never write it twice.

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "erp_pushes",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "connection_id",
            sa.UUID(),
            sa.ForeignKey("erp_connections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("cost_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("odoo_model", sa.String(), nullable=False),
        sa.Column("odoo_id", sa.Integer(), nullable=False),
        sa.Column("odoo_name", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("response", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.UUID()),
    )
    op.create_index("ix_erp_pushes_org_container", "erp_pushes", ["org_id", "container_id"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON erp_pushes TO {APP_ROLE}")
    op.execute("ALTER TABLE erp_pushes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE erp_pushes FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON erp_pushes USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_index("ix_erp_pushes_org_container", table_name="erp_pushes")
    op.drop_table("erp_pushes")
