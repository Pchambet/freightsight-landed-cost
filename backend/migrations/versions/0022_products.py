"""A product catalogue: what an article is, how it is taxed, what it weighs, what it sells for.

Order lines keep their own copy of everything the landed cost depends on; the catalogue fills the
blanks of a new line and holds the selling price, the one figure no purchase order knows and the one
that turns a landed cost into a margin.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("description", sa.String()),
        sa.Column("hs_code", sa.String()),
        sa.Column("duty_rate", sa.Numeric(7, 4)),
        sa.Column("unit_weight_kg", sa.Numeric(12, 4)),
        sa.Column("unit_volume_cbm", sa.Numeric(12, 6)),
        sa.Column("sale_price", sa.Numeric(14, 4)),
        sa.Column("sale_currency", sa.CHAR(3)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "sku", name="uq_products_org_sku"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON products TO {APP_ROLE}")
    op.execute("ALTER TABLE products ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE products FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON products USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_table("products")
