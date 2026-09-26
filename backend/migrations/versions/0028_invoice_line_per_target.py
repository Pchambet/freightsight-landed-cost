"""An invoice line is one charge of one type, in one currency, on one target.

A forwarder's invoice routinely bills the freight of each container of a bill of lading on its own
line, and the freight and its bunker surcharge on the same container as two lines of the same type.
The uniqueness of an invoice line said "one per invoice and type", so both invoices failed at
confirmation with a database error. It now includes the target and the currency: the same line still
cannot be recorded twice, and an ordinary invoice goes through. The new key contains the old one, so no
row that satisfied the old index can break the new one.

Downgrading puts the stricter index back and fails if an invoice now spans several containers; those
costs would have to be merged or deleted first.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None

_TARGET = "COALESCE(container_id, shipment_id, po_id, po_line_id)"


def upgrade() -> None:
    op.drop_index("uq_costs_invoice_line", table_name="costs")
    op.create_index(
        "uq_costs_invoice_line",
        "costs",
        ["org_id", "vendor", "invoice_number", "cost_type", "currency", sa.text(_TARGET)],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_costs_invoice_line", table_name="costs")
    op.create_index(
        "uq_costs_invoice_line",
        "costs",
        ["org_id", "vendor", "invoice_number", "cost_type"],
        unique=True,
        postgresql_where=sa.text("invoice_number IS NOT NULL"),
    )
