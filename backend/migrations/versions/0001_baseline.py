"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

container_status = postgresql.ENUM(
    "PENDING", "IN_TRANSIT", "DISCHARGED", "DELIVERED", name="containerstatus", create_type=False
)
cost_type = postgresql.ENUM(
    "OCEAN_FREIGHT", "CUSTOMS", "DRAYAGE", "DEMURRAGE", name="costtype", create_type=False
)


def upgrade() -> None:
    container_status.create(op.get_bind(), checkfirst=True)
    cost_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "organizations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("base_currency", sa.CHAR(3), nullable=False, server_default="EUR"),
    )
    op.create_table(
        "purchase_orders",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("po_number", sa.String(), nullable=False),
        sa.Column("supplier_name", sa.String()),
        sa.Column("total_value", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.UniqueConstraint("org_id", "po_number", name="uq_purchase_orders_org_id"),
    )
    op.create_index("ix_purchase_orders_org_id", "purchase_orders", ["org_id"])
    op.create_table(
        "containers",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("container_number", sa.String(), nullable=False),
        sa.Column("carrier", sa.String()),
        sa.Column("status", container_status, nullable=False, server_default="PENDING"),
        sa.UniqueConstraint("org_id", "container_number", name="uq_containers_org_id"),
    )
    op.create_index("ix_containers_org_id", "containers", ["org_id"])
    op.create_table(
        "container_purchase_order",
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), primary_key=True),
        sa.Column("po_id", sa.UUID(), sa.ForeignKey("purchase_orders.id"), primary_key=True),
        sa.Column("allocation_percentage", sa.Numeric(5, 2)),
    )
    op.create_index("ix_container_purchase_order_po_id", "container_purchase_order", ["po_id"])
    op.create_table(
        "costs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False),
        sa.Column("cost_type", cost_type, nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
    )
    op.create_index("ix_costs_container_id", "costs", ["container_id"])


def downgrade() -> None:
    op.drop_table("costs")
    op.drop_table("container_purchase_order")
    op.drop_table("containers")
    op.drop_table("purchase_orders")
    op.drop_table("organizations")
    cost_type.drop(op.get_bind(), checkfirst=True)
    container_status.drop(op.get_bind(), checkfirst=True)
