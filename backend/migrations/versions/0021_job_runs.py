"""When each background job last got through its work.

One row per job name, and the rows are not anybody's data: they say that the worker's heartbeat beat
at 06:03, that the morning demurrage pass has run today, that an organization's digest has failed
four times in a row. So, like `webhook_deliveries`, this table carries no `org_id`, is not under Row
Level Security, and is granted to the application role directly.

It exists because three things were unobservable without it. A dead worker looked exactly like a
quiet week — no exception, no process, every screen still showing the last numbers it computed. The
06:00 pass was an equality on the local hour, so a restart inside that hour skipped a whole day of
demurrage warnings with nothing to show for it. And a mail provider refusing every digest was
retried every five minutes for ever, with no counter anywhere.

Revision ID: 0021
Revises: 0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("name", sa.String(), primary_key=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=False),
        #: Consecutive failures, for the jobs that have to stop trying at some point.
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail", sa.String()),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON job_runs TO {APP_ROLE}")


def downgrade() -> None:
    op.drop_table("job_runs")
