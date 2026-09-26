"""A prospect's quarter comes in as files: the containers' tracking, the costs ledger, the tariff.

Three new kinds of import. A cost read from a file remembers the import it came from, so that the
whole file can be taken back (`import_jobs.undone_at`), and whether its type was read from a column or
inferred — from its label or from the type the person gave the whole file — because an inferred type
only ever gives findings to check. What the preview ran with (the default type, the types a person
gave the labels it could not read) is kept on the job: the commit runs with exactly that.

No new table, so no new row-level security policy.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Postgres 12+ runs ADD VALUE inside a transaction, as long as the new value is not used in it.
    for kind in ("CONTAINERS", "COSTS", "PRODUCTS"):
        op.execute(f"ALTER TYPE import_kind ADD VALUE IF NOT EXISTS '{kind}'")
    op.add_column(
        "costs",
        sa.Column("import_job_id", sa.UUID(), sa.ForeignKey("import_jobs.id", ondelete="SET NULL")),
    )
    op.create_index("ix_costs_import_job_id", "costs", ["import_job_id"])
    op.add_column(
        "costs", sa.Column("type_inferred", sa.Boolean(), nullable=False, server_default=sa.text("false"))
    )
    op.add_column("import_jobs", sa.Column("undone_at", sa.DateTime(timezone=True)))
    op.add_column(
        "import_jobs",
        sa.Column("options", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("import_jobs", "options")
    op.drop_column("import_jobs", "undone_at")
    op.drop_column("costs", "type_inferred")
    op.drop_index("ix_costs_import_job_id", table_name="costs")
    op.drop_column("costs", "import_job_id")
    # Enum values cannot be dropped in Postgres; the three kinds stay, unused.
