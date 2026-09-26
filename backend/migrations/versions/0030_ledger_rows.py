"""What a costs file did with each of its rows, kept past the commit.

A cost read from a ledger keeps the rows it came from (`costs.import_row_key`): the next copy of the
same ledger recognises its line by them, whatever a person has changed on the cost since — a type
corrected by hand no longer makes the line new again, and counted twice.

The job keeps the costs an earlier import had written and it left alone (`matched_cost_ids`) — so the
earlier file cannot be taken back while this one stands on them — and the rows it refused whose charge
is therefore not in the books (`refused_rows`), for the audit's preparation to say.

No new table, so no new row-level security policy.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("costs", sa.Column("import_row_key", sa.String(64)))
    for column in ("matched_cost_ids", "refused_rows"):
        op.add_column(
            "import_jobs",
            sa.Column(column, postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        )


def downgrade() -> None:
    op.drop_column("import_jobs", "refused_rows")
    op.drop_column("import_jobs", "matched_cost_ids")
    op.drop_column("costs", "import_row_key")
