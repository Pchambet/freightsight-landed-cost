"""Rate cards: what an organization expects to pay, so estimates can be produced from its own prices.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASES = ["FLAT", "PER_100KG", "PER_CBM", "PCT_OF_FOB"]

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    postgresql.ENUM(*BASES, name="rate_basis").create(op.get_bind(), checkfirst=True)
    op.create_table(
        "rate_cards",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("cost_type", postgresql.ENUM(name="cost_type", create_type=False), nullable=False),
        sa.Column("scope", postgresql.ENUM(name="cost_scope", create_type=False), nullable=False),
        sa.Column(
            "basis",
            postgresql.ENUM(name="rate_basis", create_type=False),
            nullable=False,
            server_default="FLAT",
        ),
        sa.Column("amount", sa.Numeric(14, 4), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("hs_code", sa.String()),
        sa.Column("notes", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("amount >= 0", name="amount_nonneg"),
    )
    # NULLS NOT DISTINCT, because a card with no HS code is *the* card for that cost type: with the
    # default NULL handling an organization could store twenty identical "THC per container" cards.
    op.execute(
        "ALTER TABLE rate_cards ADD CONSTRAINT uq_rate_cards_org_id "
        "UNIQUE NULLS NOT DISTINCT (org_id, cost_type, scope, hs_code)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON rate_cards TO {APP_ROLE}")
    op.execute("ALTER TABLE rate_cards ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE rate_cards FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON rate_cards USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_table("rate_cards")
    postgresql.ENUM(name="rate_basis").drop(op.get_bind(), checkfirst=True)
