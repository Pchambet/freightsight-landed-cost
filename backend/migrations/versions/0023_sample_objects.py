"""A register of what the demo dataset created, so that it can be deleted again.

The button that loads six months of sample data is the first thing a new account sees. Loaded into
the real organization, it could not be undone: nothing told a demo container from a real one.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "sample_objects",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("object_id", sa.UUID(), nullable=False),
        sa.UniqueConstraint("org_id", "kind", "object_id", name="uq_sample_objects"),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sample_objects TO {APP_ROLE}")
    op.execute("ALTER TABLE sample_objects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sample_objects FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON sample_objects USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )


def downgrade() -> None:
    op.drop_table("sample_objects")
