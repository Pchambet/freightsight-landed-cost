"""What an accountant takes away from a closed month: the invoices not yet received, and the drift
once it has been booked.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.add_column(
        "period_closes",
        sa.Column("acknowledged_drift", sa.Numeric(16, 2), nullable=False, server_default="0"),
    )
    op.add_column("period_closes", sa.Column("acknowledged_at", sa.DateTime(timezone=True)))
    op.add_column("period_closes", sa.Column("acknowledged_by", sa.UUID()))
    op.add_column("period_closes", sa.Column("acknowledged_note", sa.String()))
    op.add_column("period_close_lines", sa.Column("arrived_on", sa.Date()))

    op.create_table(
        "period_close_accruals",
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
        sa.Column("arrived_on", sa.Date()),
        sa.Column("cost_type", sa.String(), nullable=False),
        sa.Column("amount_base", sa.Numeric(16, 2), nullable=False),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON period_close_accruals TO {APP_ROLE}")
    op.execute("ALTER TABLE period_close_accruals ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE period_close_accruals FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON period_close_accruals "
        f"USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.drop_table("period_close_accruals")
    op.drop_column("period_close_lines", "arrived_on")
    for column in ("acknowledged_note", "acknowledged_by", "acknowledged_at", "acknowledged_drift"):
        op.drop_column("period_closes", column)
