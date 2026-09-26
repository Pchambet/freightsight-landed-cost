"""A purchase order can be cancelled upstream. It is marked, never deleted.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ["OPEN", "CANCELLED"]


def upgrade() -> None:
    postgresql.ENUM(*STATUSES, name="purchase_order_status").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "purchase_orders",
        sa.Column(
            "status",
            postgresql.ENUM(name="purchase_order_status", create_type=False),
            nullable=False,
            server_default="OPEN",
        ),
    )
    op.add_column("purchase_orders", sa.Column("cancelled_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("purchase_orders", "cancelled_at")
    op.drop_column("purchase_orders", "status")
    postgresql.ENUM(name="purchase_order_status").drop(op.get_bind(), checkfirst=True)
