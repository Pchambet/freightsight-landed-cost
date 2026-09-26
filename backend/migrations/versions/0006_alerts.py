"""Alerts, and the attempt counter a failed webhook delivery needs to be retried a bounded number of times.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ["DND_RISK", "ETA_CHANGED", "TRACKING_DATA_MISSING", "TRACKING_DELIVERY_FAILED"]
SEVERITIES = ["info", "warning", "critical"]

APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(*KINDS, name="alert_kind").create(bind, checkfirst=True)
    postgresql.ENUM(*SEVERITIES, name="alert_severity").create(bind, checkfirst=True)

    op.create_table(
        "alerts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id")),
        sa.Column("kind", postgresql.ENUM(name="alert_kind", create_type=False), nullable=False),
        sa.Column(
            "severity",
            postgresql.ENUM(name="alert_severity", create_type=False),
            nullable=False,
            server_default="info",
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False, server_default=""),
        sa.Column("dedup_key", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("org_id", "dedup_key", name="uq_alerts_org_id"),
    )
    op.create_index("ix_alerts_container_id", "alerts", ["container_id"])
    op.create_index(
        "ix_alerts_org_unread",
        "alerts",
        ["org_id", "created_at"],
        postgresql_where=sa.text("read_at IS NULL"),
    )

    op.add_column(
        "webhook_deliveries", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0")
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON alerts TO {APP_ROLE}")
    op.execute("ALTER TABLE alerts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE alerts FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON alerts USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_column("webhook_deliveries", "attempts")
    op.drop_index("ix_alerts_org_unread", table_name="alerts")
    op.drop_index("ix_alerts_container_id", table_name="alerts")
    op.drop_table("alerts")
    bind = op.get_bind()
    postgresql.ENUM(name="alert_severity").drop(bind, checkfirst=True)
    postgresql.ENUM(name="alert_kind").drop(bind, checkfirst=True)
