"""Phase 1 schema: identity, suppliers, shipments, PO lines, container loads, scoped costs, allocations,
FX rates, import jobs. Migrates Phase 0 data (one line per PO, one load per PO/container link).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENUMS = {
    "allocation_method": [
        "BY_VALUE",
        "BY_WEIGHT",
        "BY_VOLUME",
        "BY_QUANTITY",
        "MANUAL",
        "BY_CIF_VALUE",
        "BY_THEORETICAL_DUTY",
    ],
    "incoterm": ["EXW", "FCA", "FOB", "CFR", "CIF", "DAP", "DDP"],
    "container_milestone": [
        "BOOKED",
        "GATE_IN_FULL_ORIGIN",
        "LOADED",
        "VESSEL_DEPARTED",
        "VESSEL_ARRIVED",
        "DISCHARGED",
        "AVAILABLE_FOR_PICKUP",
        "GATE_OUT_FULL",
        "DELIVERED",
        "GATE_IN_EMPTY_RETURN",
    ],
    "tracking_state": ["UNTRACKED", "MANUAL", "PENDING", "ACTIVE", "FAILED", "ENDED"],
    "dnd_risk": ["NONE", "LOW", "MEDIUM", "HIGH", "INCURRING"],
    "member_role": ["OWNER", "ADMIN", "MEMBER", "VIEWER"],
    "cost_scope": ["SHIPMENT", "CONTAINER", "PO", "PO_LINE"],
    "cost_type": [
        "OCEAN_FREIGHT",
        "AIR_FREIGHT",
        "INSURANCE",
        "ORIGIN_CHARGES",
        "THC",
        "BL_FEE",
        "CUSTOMS_DUTY",
        "CUSTOMS_BROKERAGE",
        "IMPORT_VAT",
        "DRAYAGE",
        "DEMURRAGE",
        "DETENTION",
        "WAREHOUSING",
        "INSPECTION",
        "BANK_FEES",
        "OTHER",
    ],
    "import_kind": ["PURCHASE_ORDERS", "LEGACY_PO_CONTAINER"],
    "import_status": ["PARSED", "VALIDATED", "DONE", "FAILED"],
}


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def _ts() -> list[sa.Column[Any]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).create(bind, checkfirst=True)

    # ---- organizations ---------------------------------------------------------------------------------
    op.add_column("organizations", sa.Column("slug", sa.String(), unique=True))
    op.add_column("organizations", sa.Column("external_id", sa.String(), unique=True))
    op.add_column(
        "organizations",
        sa.Column(
            "default_allocation_method", _enum("allocation_method"), nullable=False, server_default="BY_VALUE"
        ),
    )
    op.add_column("organizations", sa.Column("default_incoterm", _enum("incoterm")))
    op.add_column(
        "organizations", sa.Column("free_days_demurrage", sa.Integer(), nullable=False, server_default="5")
    )
    op.add_column(
        "organizations", sa.Column("free_days_detention", sa.Integer(), nullable=False, server_default="7")
    )
    op.add_column(
        "organizations", sa.Column("settings", postgresql.JSONB(), nullable=False, server_default="{}")
    )
    for col in _ts():
        op.add_column("organizations", col)

    # ---- identity ----------------------------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("external_id", sa.String(), nullable=False, unique=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("name", sa.String()),
        *_ts(),
    )
    op.create_table(
        "memberships",
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("role", _enum("member_role"), nullable=False, server_default="MEMBER"),
    )

    # ---- suppliers, shipments ----------------------------------------------------------------------------
    op.create_table(
        "suppliers",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("country", sa.CHAR(2)),
        sa.Column("default_currency", sa.CHAR(3)),
        *_ts(),
        sa.UniqueConstraint("org_id", "name", name="uq_suppliers_org_id"),
    )
    op.create_table(
        "shipments",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("reference", sa.String(), nullable=False),
        sa.Column("carrier_scac", sa.CHAR(4)),
        sa.Column("incoterm", _enum("incoterm")),
        sa.Column("origin_unlocode", sa.CHAR(5)),
        sa.Column("destination_unlocode", sa.CHAR(5)),
        sa.Column("etd", sa.Date()),
        sa.Column("eta", sa.Date()),
        *_ts(),
        sa.UniqueConstraint("org_id", "reference", name="uq_shipments_org_id"),
    )

    # ---- purchase orders + lines -------------------------------------------------------------------------
    op.add_column("purchase_orders", sa.Column("supplier_id", sa.UUID(), sa.ForeignKey("suppliers.id")))
    op.create_index("ix_purchase_orders_supplier_id", "purchase_orders", ["supplier_id"])
    op.add_column("purchase_orders", sa.Column("currency", sa.CHAR(3)))
    op.add_column(
        "purchase_orders", sa.Column("fx_rate", sa.Numeric(18, 8), nullable=False, server_default="1")
    )
    op.add_column("purchase_orders", sa.Column("fx_date", sa.Date()))
    op.add_column("purchase_orders", sa.Column("incoterm", _enum("incoterm")))
    op.add_column("purchase_orders", sa.Column("order_date", sa.Date()))
    for col in _ts():
        op.add_column("purchase_orders", col)

    op.create_table(
        "po_lines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("po_id", sa.UUID(), sa.ForeignKey("purchase_orders.id"), nullable=False, index=True),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String()),
        sa.Column("description", sa.String()),
        sa.Column("hs_code", sa.String()),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("unit_weight_kg", sa.Numeric(12, 4)),
        sa.Column("unit_volume_cbm", sa.Numeric(12, 6)),
        sa.Column("duty_rate", sa.Numeric(7, 4)),
        sa.UniqueConstraint("po_id", "line_no", name="uq_po_lines_po_id"),
        sa.CheckConstraint("quantity > 0", name="qty_positive"),
        sa.CheckConstraint("unit_price >= 0", name="price_nonneg"),
    )
    op.create_index("ix_po_lines_org_sku", "po_lines", ["org_id", "sku"])

    # data: suppliers from supplier_name, currency = org base, one line per PO carrying total_value
    op.execute(
        """
        INSERT INTO suppliers (id, org_id, name)
        SELECT gen_random_uuid(), org_id, supplier_name FROM purchase_orders
        WHERE supplier_name IS NOT NULL AND supplier_name <> ''
        GROUP BY org_id, supplier_name
        """
    )
    op.execute(
        """
        UPDATE purchase_orders p SET supplier_id = s.id
        FROM suppliers s WHERE s.org_id = p.org_id AND s.name = p.supplier_name
        """
    )
    op.execute(
        "UPDATE purchase_orders p SET currency = o.base_currency FROM organizations o WHERE o.id = p.org_id"
    )
    op.alter_column("purchase_orders", "currency", nullable=False)
    op.execute(
        """
        INSERT INTO po_lines (id, org_id, po_id, line_no, quantity, unit_price)
        SELECT gen_random_uuid(), org_id, id, 1, 1, COALESCE(total_value, 0) FROM purchase_orders
        """
    )
    op.drop_column("purchase_orders", "supplier_name")
    op.drop_column("purchase_orders", "total_value")

    # ---- containers --------------------------------------------------------------------------------------
    op.add_column("containers", sa.Column("shipment_id", sa.UUID(), sa.ForeignKey("shipments.id")))
    op.create_index("ix_containers_shipment_id", "containers", ["shipment_id"])
    op.add_column("containers", sa.Column("iso_type", sa.String()))
    op.add_column("containers", sa.Column("carrier_scac", sa.CHAR(4)))
    op.add_column(
        "containers",
        sa.Column("milestone", _enum("container_milestone"), nullable=False, server_default="BOOKED"),
    )
    op.add_column(
        "containers",
        sa.Column("tracking_state", _enum("tracking_state"), nullable=False, server_default="MANUAL"),
    )
    for name in ("eta", "ata", "discharged_at", "gate_out_at", "empty_returned_at", "archived_at"):
        op.add_column("containers", sa.Column(name, sa.DateTime(timezone=True)))
    op.add_column("containers", sa.Column("free_days_demurrage", sa.Integer()))
    op.add_column("containers", sa.Column("free_days_detention", sa.Integer()))
    op.add_column("containers", sa.Column("last_free_day", sa.Date()))
    op.add_column(
        "containers", sa.Column("dnd_risk", _enum("dnd_risk"), nullable=False, server_default="NONE")
    )
    for col in _ts():
        op.add_column("containers", col)
    op.execute(
        """
        UPDATE containers SET milestone = CASE status::text
            WHEN 'IN_TRANSIT' THEN 'VESSEL_DEPARTED'::container_milestone
            WHEN 'DISCHARGED' THEN 'DISCHARGED'::container_milestone
            WHEN 'DELIVERED' THEN 'DELIVERED'::container_milestone
            ELSE 'BOOKED'::container_milestone END
        """
    )
    op.drop_column("containers", "status")
    op.drop_column("containers", "carrier")
    op.drop_constraint("uq_containers_org_id", "containers", type_="unique")
    op.alter_column("containers", "container_number", type_=sa.CHAR(11))
    op.create_index(
        "uq_containers_active_number",
        "containers",
        ["org_id", "container_number"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_check_constraint("iso6346", "containers", "container_number ~ '^[A-Z]{4}[0-9]{7}$'")

    # ---- container loads (from the Phase 0 many-to-many) -------------------------------------------------
    op.create_table(
        "container_loads",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), nullable=False, index=True),
        sa.Column("po_line_id", sa.UUID(), sa.ForeignKey("po_lines.id"), nullable=False, index=True),
        sa.Column("quantity", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("container_id", "po_line_id", name="uq_container_loads_container_id"),
        sa.CheckConstraint("quantity > 0", name="qty_positive"),
    )
    op.execute(
        """
        INSERT INTO container_loads (id, org_id, container_id, po_line_id, quantity)
        SELECT gen_random_uuid(), l.org_id, cpo.container_id, l.id, 1
        FROM container_purchase_order cpo JOIN po_lines l ON l.po_id = cpo.po_id AND l.line_no = 1
        """
    )
    op.drop_table("container_purchase_order")

    # ---- costs (rebuild with scope, fx, method) ----------------------------------------------------------
    op.rename_table("costs", "costs_old")
    op.execute("ALTER INDEX ix_costs_container_id RENAME TO ix_costs_old_container_id")
    op.execute("ALTER TABLE costs_old RENAME CONSTRAINT pk_costs TO pk_costs_old")
    op.execute(
        "ALTER TABLE costs_old RENAME CONSTRAINT fk_costs_container_id_containers "
        "TO fk_costs_old_container_id"
    )
    op.create_table(
        "costs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("scope", _enum("cost_scope"), nullable=False),
        sa.Column("shipment_id", sa.UUID(), sa.ForeignKey("shipments.id"), index=True),
        sa.Column("container_id", sa.UUID(), sa.ForeignKey("containers.id"), index=True),
        sa.Column("po_id", sa.UUID(), sa.ForeignKey("purchase_orders.id"), index=True),
        sa.Column("po_line_id", sa.UUID(), sa.ForeignKey("po_lines.id"), index=True),
        sa.Column("cost_type", _enum("cost_type"), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("fx_rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("fx_date", sa.Date(), nullable=False),
        sa.Column("fx_source", sa.String(), nullable=False),
        sa.Column("amount_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("allocation_method", _enum("allocation_method"), nullable=False),
        sa.Column("manual_splits", postgresql.JSONB()),
        sa.Column("cost_date", sa.Date(), nullable=False),
        sa.Column("vendor", sa.String()),
        sa.Column("invoice_number", sa.String()),
        sa.Column("notes", sa.String()),
        sa.Column("created_by", sa.UUID()),
        *_ts(),
        sa.CheckConstraint(
            "num_nonnulls(shipment_id, container_id, po_id, po_line_id) = 1", name="one_scope"
        ),
        sa.CheckConstraint(
            "(allocation_method = 'MANUAL') = (manual_splits IS NOT NULL)", name="manual_splits"
        ),
        sa.CheckConstraint("amount >= 0", name="amount_nonneg"),
    )
    op.create_index(
        "uq_costs_invoice_line",
        "costs",
        ["org_id", "vendor", "invoice_number", "cost_type"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )
    op.execute(
        """
        INSERT INTO costs (id, org_id, scope, container_id, cost_type, amount, currency, fx_rate, fx_date,
                           fx_source, amount_base, allocation_method, cost_date)
        SELECT o.id, c.org_id, 'CONTAINER', o.container_id,
               CASE o.cost_type::text WHEN 'CUSTOMS' THEN 'CUSTOMS_DUTY'
                    ELSE o.cost_type::text END::cost_type,
               o.amount, o.currency, 1, CURRENT_DATE, 'same', o.amount, 'BY_VALUE', CURRENT_DATE
        FROM costs_old o JOIN containers c ON c.id = o.container_id
        """
    )
    op.drop_table("costs_old")

    op.create_table(
        "cost_allocations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("cost_id", sa.UUID(), sa.ForeignKey("costs.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "container_load_id",
            sa.UUID(),
            sa.ForeignKey("container_loads.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount_base", sa.Numeric(14, 2), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("cost_id", "container_load_id", name="uq_cost_allocations_cost_id"),
    )
    op.create_table(
        "fx_rates",
        sa.Column("base", sa.CHAR(3), primary_key=True),
        sa.Column("quote", sa.CHAR(3), primary_key=True),
        sa.Column("rate_date", sa.Date(), primary_key=True),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
    )

    # ---- imports -----------------------------------------------------------------------------------------
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("kind", _enum("import_kind"), nullable=False),
        sa.Column("status", _enum("import_status"), nullable=False, server_default="PARSED"),
        sa.Column("original_filename", sa.String()),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("encoding", sa.String()),
        sa.Column("delimiter", sa.String()),
        sa.Column("columns", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("mapping", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.UUID()),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        *_ts(),
    )
    op.create_table(
        "import_mappings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("kind", _enum("import_kind"), nullable=False),
        sa.Column("header_signature", sa.String(), nullable=False),
        sa.Column("mapping", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "kind", "header_signature", name="uq_import_mappings_org_id"),
    )

    # Phase 0 enums are no longer referenced.
    op.execute("DROP TYPE IF EXISTS containerstatus")
    op.execute("DROP TYPE IF EXISTS costtype")


def downgrade() -> None:
    """Drops the Phase 1 schema and recreates the empty Phase 0 tables (no data is carried back)."""
    for table in (
        "import_mappings",
        "import_jobs",
        "fx_rates",
        "cost_allocations",
        "costs",
        "container_loads",
        "po_lines",
        "containers",
        "purchase_orders",
        "shipments",
        "suppliers",
        "memberships",
        "users",
        "organizations",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind = op.get_bind()
    for name in ENUMS:
        _enum(name).drop(bind, checkfirst=True)

    container_status = postgresql.ENUM(
        "PENDING", "IN_TRANSIT", "DISCHARGED", "DELIVERED", name="containerstatus", create_type=False
    )
    cost_type = postgresql.ENUM(
        "OCEAN_FREIGHT", "CUSTOMS", "DRAYAGE", "DEMURRAGE", name="costtype", create_type=False
    )
    container_status.create(bind, checkfirst=True)
    cost_type.create(bind, checkfirst=True)
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
