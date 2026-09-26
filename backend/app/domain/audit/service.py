"""Recording what changed.

The log is append-only in the database itself (grants plus a trigger, migration 0008), so this module
only has to decide *what* is worth writing and keep the payloads readable: JSON of the fields that
matter, not a pickle of an ORM object, because the point is to be legible in two years by someone
reading a dispute.

Writes join the caller's transaction. An audited action that rolls back leaves no entry — the log
records what happened, not what was attempted.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.domain.models import AuditLog

# The actions worth a line. Named as `entity.verb`, past tense: they are facts, not intentions.
COST_CREATED = "cost.created"
COST_UPDATED = "cost.updated"
COST_DELETED = "cost.deleted"
COST_CLOSED = "cost.closed"
COST_SUPERSEDED = "cost.superseded"
INVOICE_CONFIRMED = "invoice.confirmed"
INVOICE_REJECTED = "invoice.rejected"
INVOICE_REOPENED = "invoice.reopened"
INVOICE_CORRECTED = "invoice.corrected"
IMPORT_COMMITTED = "import.committed"
IMPORT_UNDONE = "import.undone"
LOADS_REPLACED = "container.loads_replaced"
ORGANIZATION_UPDATED = "organization.updated"
SAMPLE_DATA_DELETED = "organization.sample_data_deleted"
CONTAINER_SHARED = "container.shared"
PURCHASE_ORDER_SHARED = "purchase_order.shared"
AUDIT_SHARED = "audit.shared"
SHARE_REVOKED = "share.revoked"
PERIOD_CLOSED = "period.closed"
PERIOD_REOPENED = "period.reopened"
PERIOD_DRIFT_ACKNOWLEDGED = "period.drift_acknowledged"
TRACKING_EVENT_ADDED = "container.tracking_event_added"
ERP_LANDED_COST_PUSHED = "erp.landed_cost_pushed"
ERP_PUSH_FORGOTTEN = "erp.push_forgotten"


def jsonable(value: Any) -> Any:
    """Money stays a string, dates stay ISO: the log must not round anything on its way in."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if hasattr(value, "value") and hasattr(value, "name"):  # an enum
        return value.value
    return value


def snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: jsonable(getattr(obj, field, None)) for field in fields}


COST_FIELDS = (
    "status",
    "supersedes_cost_id",
    "scope",
    "cost_type",
    "amount",
    "currency",
    "amount_base",
    "fx_rate",
    "fx_date",
    "fx_source",
    "cost_date",
    "allocation_method",
    "vendor",
    "invoice_number",
    "shipment_id",
    "container_id",
    "po_id",
    "po_line_id",
)


def record(
    db: Session,
    org_id: UUID,
    *,
    actor_user_id: UUID | None,
    action: str,
    entity_type: str,
    entity_id: UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    entry = AuditLog(
        org_id=org_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before=jsonable(before) if before is not None else None,
        after=jsonable(after) if after is not None else None,
    )
    db.add(entry)
    db.flush()
    return entry
