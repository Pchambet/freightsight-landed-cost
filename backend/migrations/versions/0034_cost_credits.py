"""Credits on a cost: a refund or a discount lowers what a cost amounts to, and each one is kept.

A credit note used to be refused at confirmation and could not be entered by hand either, since a
cost cannot be negative: a refund never lowered a landed cost. The cost now carries its net amount,
and `cost_credits` keeps every reduction with the document it came from and who accepted it.

`invoice_lines.credit_cost_id` is the cost a refund line reduces, chosen by the person reviewing it.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "cost_credits",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "cost_id", sa.UUID(), sa.ForeignKey("costs.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("credit_number", sa.String()),
        sa.Column("vendor", sa.String()),
        sa.Column("credit_date", sa.Date(), nullable=False),
        sa.Column("invoice_id", sa.UUID(), sa.ForeignKey("invoices.id", ondelete="SET NULL"), index=True),
        sa.Column("created_by", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount > 0", name="amount_positive"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON cost_credits TO {APP_ROLE}")
    op.execute("ALTER TABLE cost_credits ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cost_credits FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON cost_credits USING ({PREDICATE}) WITH CHECK ({PREDICATE})")

    op.add_column(
        "invoice_lines",
        sa.Column("credit_cost_id", sa.UUID(), sa.ForeignKey("costs.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_column("invoice_lines", "credit_cost_id")
    op.drop_table("cost_credits")
