"""Forgetting a push instead of deleting it.

A push whose draft was removed in the ERP has to stop holding its costs, or those costs can never
be pushed again. The row stays: it is the only evidence that we once wrote into someone's books,
and the audit log points at it.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("erp_pushes", sa.Column("forgotten_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("erp_pushes", sa.Column("forgotten_reason", sa.String(), nullable=True))
    op.add_column("erp_pushes", sa.Column("forgotten_by", sa.UUID(as_uuid=True), nullable=True))


def downgrade() -> None:
    op.drop_column("erp_pushes", "forgotten_by")
    op.drop_column("erp_pushes", "forgotten_reason")
    op.drop_column("erp_pushes", "forgotten_at")
