"""An alert for "the provider is down", distinct from "this container has gone quiet".

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE alert_kind ADD VALUE IF NOT EXISTS 'TRACKING_PROVIDER_DOWN'")


def downgrade() -> None:
    # Postgres cannot remove a value from an enum. Dropping and recreating the type would mean
    # rewriting every row that uses it, to undo something harmless: the value simply stops being
    # written. Left in place on purpose.
    pass
