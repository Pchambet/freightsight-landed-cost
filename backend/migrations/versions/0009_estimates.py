"""Estimated versus actual costs, and the detention deadline.

Nothing changes for existing data: every cost that exists today was an invoice, so it becomes ACTUAL,
which is also the default for anything written by a version of the code that does not know about this
column yet.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ["ESTIMATE", "ACTUAL"]


def upgrade() -> None:
    postgresql.ENUM(*STATUSES, name="cost_status").create(op.get_bind(), checkfirst=True)
    op.add_column(
        "costs",
        sa.Column(
            "status",
            postgresql.ENUM(name="cost_status", create_type=False),
            nullable=False,
            server_default="ACTUAL",
        ),
    )
    op.add_column(
        "costs",
        sa.Column("supersedes_cost_id", sa.UUID(), sa.ForeignKey("costs.id", ondelete="SET NULL")),
    )
    op.create_unique_constraint("uq_costs_supersedes_cost_id", "costs", ["supersedes_cost_id"])
    op.add_column("costs", sa.Column("closed_at", sa.DateTime(timezone=True)))
    op.add_column("costs", sa.Column("close_reason", sa.String()))
    # Only an estimate can be closed, and only an actual can supersede: the check is in the database
    # because these two columns are the whole grammar of the feature.
    op.create_check_constraint("estimate_only_close", "costs", "closed_at IS NULL OR status = 'ESTIMATE'")
    op.create_check_constraint(
        "actual_only_supersede", "costs", "supersedes_cost_id IS NULL OR status = 'ACTUAL'"
    )
    op.create_index(
        "ix_costs_org_status", "costs", ["org_id", "status"], postgresql_where=sa.text("closed_at IS NULL")
    )

    op.add_column("containers", sa.Column("detention_deadline", sa.Date()))


def downgrade() -> None:
    op.drop_column("containers", "detention_deadline")
    op.drop_index("ix_costs_org_status", table_name="costs")
    # The bare names: the metadata naming convention adds the ck_costs_ prefix on the way out, and
    # passing the rendered name would ask Postgres for ck_costs_ck_costs_...
    op.drop_constraint("actual_only_supersede", "costs", type_="check")
    op.drop_constraint("estimate_only_close", "costs", type_="check")
    op.drop_column("costs", "close_reason")
    op.drop_column("costs", "closed_at")
    op.drop_constraint("uq_costs_supersedes_cost_id", "costs", type_="unique")
    op.drop_column("costs", "supersedes_cost_id")
    op.drop_column("costs", "status")
    postgresql.ENUM(name="cost_status").drop(op.get_bind(), checkfirst=True)
