"""An order's exchange rate may be missing.

When no rate could be had for a foreign-currency order — the provider down, a currency the ECB does
not quote — the import wrote 1, and dollars were read as euros in every landed cost after it. The
rate now stays empty until a person sets it, and the engine says the value is missing.

Orders already written at that 1 are not touched here: the table is under FORCE row-level security,
so a migration sees no organization's rows, and telling a defaulted 1 from a rate typed by hand is a
person's call (`fx_rate = 1 AND fx_date IS NULL` on a foreign currency is the import's mark).

No new table, so no new row-level security policy.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("purchase_orders", "fx_rate", nullable=True)


def downgrade() -> None:
    # Writing 1 back would bring the silent rate back: the column is required again only once every
    # order has a rate, and the downgrade fails loudly while one has none.
    op.alter_column("purchase_orders", "fx_rate", nullable=False)
