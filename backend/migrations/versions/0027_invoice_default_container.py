"""An invoice dropped from a container's page, or in the first-file walkthrough, says which container
it is about: the lines on which no container can be read are attached to that one.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("default_container_id", sa.UUID(), sa.ForeignKey("containers.id", ondelete="SET NULL")),
    )


def downgrade() -> None:
    op.drop_column("invoices", "default_container_id")
