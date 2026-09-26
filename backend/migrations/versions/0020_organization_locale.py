"""The language an organization is written to in.

French for every row that already exists, because every customer we have is French and an English
digest landing in their inbox tomorrow morning would be a regression, not a new feature.

Revision ID: 0020
Revises: 0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("locale", sa.String(length=5), nullable=False, server_default="fr"),
    )
    # The default was for the rows that already exist; new rows get theirs from the model.
    op.alter_column("organizations", "locale", server_default=None)


def downgrade() -> None:
    op.drop_column("organizations", "locale")
