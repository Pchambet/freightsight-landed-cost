"""What each pushed cost was, amount and label, when it was written.

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Existing pushes get an empty map: nothing was recorded for them, so no amount comparison is
    # made. Only whether their costs still exist is checked, and a gone cost is named by its id.
    op.add_column(
        "erp_pushes",
        sa.Column("carried", postgresql.JSONB(), nullable=False, server_default="{}"),
    )
    op.alter_column("erp_pushes", "carried", server_default=None)


def downgrade() -> None:
    op.drop_column("erp_pushes", "carried")
