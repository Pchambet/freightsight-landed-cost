"""The pre-tax total the invoice itself prints.

Not total minus VAT: the figure the supplier wrote, kept so the three totals of a document can be
shown as the document shows them, and so the arithmetic check keeps its yardstick after the reading.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("subtotal_amount", sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("invoices", "subtotal_amount")
