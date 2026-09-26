"""A landed-cost report shared by link: a frozen snapshot, opened by a token and by nothing else.

The table is under the same tenant policy as every other. The public page has no tenant, so it gets
a door of its own, and a narrow one: a second policy, **for SELECT only**, that lets through the
single row whose `token_hash` equals the transaction-local setting `app.share_token_hash`. The API
sets it to the SHA-256 of the token it was handed and reads that row. No privileged function, no table
left outside row-level security: holding the token is the whole of the right — and that right is to
read. There is deliberately no UPDATE door: a policy that let a token holder update "their" row would
let them un-revoke it, extend it, or rewrite what it shows.

Counting an opening is therefore an INSERT into `shared_report_views`, allowed only for the share
whose token is set. A token holder can add a line saying "opened now", and nothing else.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"
BY_TOKEN = "token_hash = NULLIF(current_setting('app.share_token_hash', true), '')"


def upgrade() -> None:
    op.create_table(
        "shared_reports",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False, index=True),
        sa.Column("subject_label", sa.String(), nullable=False),
        sa.Column("token_hash", sa.CHAR(64), nullable=False, unique=True),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.UUID()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON shared_reports TO {APP_ROLE}")
    op.execute("ALTER TABLE shared_reports ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shared_reports FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON shared_reports USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )
    op.execute(f"CREATE POLICY share_token_read ON shared_reports FOR SELECT USING ({BY_TOKEN})")

    op.create_table(
        "shared_report_views",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "share_id",
            sa.UUID(),
            sa.ForeignKey("shared_reports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("viewed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON shared_report_views TO {APP_ROLE}")
    op.execute("ALTER TABLE shared_report_views ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE shared_report_views FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON shared_report_views USING ({PREDICATE}) WITH CHECK ({PREDICATE})"
    )
    # The one thing a token may write: "this share was opened", for the share it opens and in that
    # share's organization. The sub-select is itself under `share_token_read`.
    op.execute(
        "CREATE POLICY share_token_view ON shared_report_views FOR INSERT WITH CHECK ("
        "EXISTS (SELECT 1 FROM shared_reports s WHERE s.id = share_id "
        f"AND s.org_id = shared_report_views.org_id AND s.{BY_TOKEN}))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shared_report_views")
    op.drop_table("shared_reports")
