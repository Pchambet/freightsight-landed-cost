"""Backing off from an ERP that keeps failing.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "erp_connections",
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("erp_connections", sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True))
    # The default was for the rows that already exist; new rows get theirs from the model.
    op.alter_column("erp_connections", "consecutive_failures", server_default=None)


def downgrade() -> None:
    op.drop_column("erp_connections", "retry_after")
    op.drop_column("erp_connections", "consecutive_failures")
