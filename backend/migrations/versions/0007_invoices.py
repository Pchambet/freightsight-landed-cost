"""The invoice inbox: documents, their bytes, invoices and the cost lines proposed from them.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ["UPLOADED", "EXTRACTING", "NEEDS_REVIEW", "CONFIRMED", "FAILED", "REJECTED"]

RLS_TABLES = ["documents", "document_blobs", "invoices", "invoice_lines"]
APP_ROLE = "freightsight_app"
PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"


def upgrade() -> None:
    postgresql.ENUM(*STATUSES, name="invoice_status").create(op.get_bind(), checkfirst=True)

    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("storage", sa.String(), nullable=False),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("filename", sa.String()),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.UUID()),
        sa.UniqueConstraint("org_id", "storage_key", name="uq_documents_org_id"),
        sa.UniqueConstraint("org_id", "sha256", name="uq_documents_org_id_sha256"),
    )
    op.create_table(
        "document_blobs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("storage_key", sa.String(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint("org_id", "storage_key", name="uq_document_blobs_org_id"),
    )
    op.create_table(
        "invoices",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="invoice_status", create_type=False),
            nullable=False,
            server_default="UPLOADED",
        ),
        sa.Column("vendor", sa.String()),
        sa.Column("invoice_number", sa.String()),
        sa.Column("invoice_date", sa.Date()),
        sa.Column("currency", sa.CHAR(3)),
        sa.Column("total_amount", sa.Numeric(14, 2)),
        sa.Column("vat_amount", sa.Numeric(14, 2)),
        sa.Column("extractor", sa.String()),
        sa.Column("confidence", sa.Numeric(3, 2)),
        sa.Column("error", sa.String()),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("created_by", sa.UUID()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "invoice_lines",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column(
            "invoice_id",
            sa.UUID(),
            sa.ForeignKey("invoices.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("cost_type", postgresql.ENUM(name="cost_type", create_type=False)),
        sa.Column("scope", postgresql.ENUM(name="cost_scope", create_type=False)),
        sa.Column("target_id", sa.UUID()),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default="0"),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.String()),
        sa.Column("cost_id", sa.UUID(), sa.ForeignKey("costs.id", ondelete="SET NULL")),
        sa.UniqueConstraint("invoice_id", "line_no", name="uq_invoice_lines_invoice_id"),
    )

    for table in RLS_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING ({PREDICATE}) WITH CHECK ({PREDICATE})")


def downgrade() -> None:
    op.drop_table("invoice_lines")
    op.drop_table("invoices")
    op.drop_table("document_blobs")
    op.drop_table("documents")
    postgresql.ENUM(name="invoice_status").drop(op.get_bind(), checkfirst=True)
