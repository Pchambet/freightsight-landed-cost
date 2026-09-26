"""The landed unit cost of each SKU, as it stood on each day it was recomputed.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "sku_cost_history",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("recorded_on", sa.Date(), nullable=False),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("fob_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("landed_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_landed_cost", sa.Numeric(14, 4), nullable=False),
        sa.Column("load_count", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("org_id", "sku", "recorded_on", name="uq_sku_cost_history_org_id"),
    )
    op.create_index("ix_sku_cost_history_org_sku", "sku_cost_history", ["org_id", "sku", "recorded_on"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sku_cost_history TO {APP_ROLE}")
    op.execute("ALTER TABLE sku_cost_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sku_cost_history FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON sku_cost_history USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.drop_index("ix_sku_cost_history_org_sku", table_name="sku_cost_history")
    op.drop_table("sku_cost_history")
