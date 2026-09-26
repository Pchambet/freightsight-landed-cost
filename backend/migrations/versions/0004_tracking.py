"""Phase 2 tracking: subscriptions, raw webhook deliveries, append-only events, ETA history.

`tracking_events`, `tracking_subscriptions` and `eta_history` are org-scoped and get the same RLS
policy as the rest. `webhook_deliveries` is not: it is written before we know which tenant a delivery
belongs to. The one door through RLS is `tracking_resolve_subscription()`, a SECURITY DEFINER function
that maps a provider delivery to `(org_id, container_id)` and returns nothing else.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MILESTONE_CODES = [
    "BOOKED",
    "GATE_OUT_EMPTY_ORIGIN",
    "GATE_IN_FULL_ORIGIN",
    "LOADED",
    "VESSEL_DEPARTED",
    "TRANSSHIPMENT_ARRIVED",
    "TRANSSHIPMENT_DISCHARGED",
    "TRANSSHIPMENT_LOADED",
    "TRANSSHIPMENT_DEPARTED",
    "VESSEL_ARRIVED",
    "DISCHARGED",
    "AVAILABLE_FOR_PICKUP",
    "GATE_OUT_FULL",
    "RAIL_LOADED",
    "RAIL_DEPARTED",
    "RAIL_ARRIVED",
    "RAIL_UNLOADED",
    "DELIVERED",
    "GATE_IN_EMPTY_RETURN",
    "UNKNOWN",
]

RLS_TABLES = ["tracking_subscriptions", "tracking_events", "eta_history"]
APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"

RESOLVE_FN = """
CREATE OR REPLACE FUNCTION tracking_resolve_subscription(
    p_provider TEXT, p_provider_ref TEXT, p_container_number TEXT
) RETURNS TABLE (org_id UUID, container_id UUID)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    -- A webhook arrives with no tenant context, so this lookup has to see across organizations.
    -- It returns two ids and nothing else, and only ever one row: an ambiguous match (the same box
    -- number tracked by two organizations, with no provider reference to tell them apart) returns
    -- none, and the caller records the delivery as unroutable rather than guessing a tenant.
    SELECT s.org_id, s.container_id
    FROM tracking_subscriptions s
    JOIN containers c ON c.id = s.container_id
    WHERE s.provider = p_provider
      AND s.ended_at IS NULL
      AND (
          (p_provider_ref IS NOT NULL AND s.provider_ref = p_provider_ref)
          OR (s.provider_ref IS NULL AND p_container_number IS NOT NULL
              AND c.container_number = p_container_number)
      )
    LIMIT 2
$$;
"""


def upgrade() -> None:
    bind = op.get_bind()
    milestone_code = postgresql.ENUM(*MILESTONE_CODES, name="milestone_code")
    milestone_code.create(bind, checkfirst=True)

    op.create_table(
        "tracking_subscriptions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_ref", sa.String()),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("last_error", sa.String()),
        sa.Column("subscribed_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("container_id", name="uq_tracking_subscriptions_container_id"),
        sa.UniqueConstraint("provider", "provider_ref", name="uq_tracking_subscriptions_provider"),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("delivery_id", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("headers", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("body", sa.LargeBinary(), nullable=False),
        sa.Column("signature_ok", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(), nullable=False, server_default="received"),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.String()),
        sa.UniqueConstraint("provider", "delivery_id", name="uq_webhook_deliveries_provider"),
    )

    op.create_table(
        "tracking_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_event_key", sa.String(), nullable=False),
        sa.Column("code", postgresql.ENUM(name="milestone_code", create_type=False), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_estimate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("location_unlocode", sa.CHAR(5)),
        sa.Column("location_name", sa.String()),
        sa.Column("vessel_name", sa.String()),
        sa.Column("voyage", sa.String()),
        sa.Column("raw_description", sa.String()),
        sa.Column("source", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("delivery_id", sa.UUID(), sa.ForeignKey("webhook_deliveries.id")),
        sa.Column("inserted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "org_id", "container_id", "provider", "provider_event_key", name="uq_tracking_events_org_id"
        ),
    )
    op.create_index("ix_tracking_events_container_time", "tracking_events", ["container_id", "occurred_at"])

    op.create_table(
        "eta_history",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("eta", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_eta", sa.DateTime(timezone=True)),
        sa.Column("delta_hours", sa.Numeric(8, 1), nullable=False),
        sa.Column("severity", sa.String(), nullable=False, server_default="info"),
        sa.Column("source", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_eta_history_container", "eta_history", ["container_id", "recorded_at"])

    for table in [*RLS_TABLES, "webhook_deliveries"]:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})")

    op.execute(RESOLVE_FN)
    op.execute("REVOKE ALL ON FUNCTION tracking_resolve_subscription(TEXT, TEXT, TEXT) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION tracking_resolve_subscription(TEXT, TEXT, TEXT) TO {APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS tracking_resolve_subscription(TEXT, TEXT, TEXT)")
    op.drop_index("ix_eta_history_container", table_name="eta_history")
    op.drop_table("eta_history")
    op.drop_index("ix_tracking_events_container_time", table_name="tracking_events")
    op.drop_table("tracking_events")
    op.drop_table("webhook_deliveries")
    op.drop_table("tracking_subscriptions")
    postgresql.ENUM(name="milestone_code").drop(op.get_bind(), checkfirst=True)
