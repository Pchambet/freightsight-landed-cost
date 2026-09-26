"""Reading the audit log. Nothing here writes: the log is append-only in the database itself."""

from __future__ import annotations

import base64
import binascii
import contextlib
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import literal, select, tuple_

from app.api.v1 import schemas
from app.core.errors import Unprocessable
from app.core.tenancy import TenantDep, TenantSession
from app.domain.models import (
    AuditLog,
    Container,
    ImportJob,
    Invoice,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
    User,
)

router = APIRouter(prefix="/audit-log", tags=["audit"])


def encode_cursor(entry: AuditLog) -> str:
    """`(at, id)` — the timestamp alone is not unique, and two entries can share a millisecond."""
    raw = f"{entry.at.isoformat()}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        at, entry_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(at), UUID(entry_id)
    except (ValueError, binascii.Error) as exc:
        raise Unprocessable("This cursor is not one of ours", code="INVALID_CURSOR") from exc


def _ids_in(value: object) -> set[UUID]:
    """Every uuid a payload mentions, as a value or as a key (`container.loads_replaced` is keyed by
    order line)."""
    found: set[UUID] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found |= _ids_in(key) | _ids_in(inner)
    elif isinstance(value, list):
        for inner in value:
            found |= _ids_in(inner)
    elif isinstance(value, str) and len(value) == 36:
        with contextlib.suppress(ValueError):
            found.add(UUID(value))
    return found


def _labels(t: TenantSession, ids: set[UUID]) -> dict[str, str]:
    """Names for the ids of one page, in one query per kind, all inside the caller's organisation."""
    if not ids:
        return {}
    labels: dict[str, str] = {}
    for c in t.db.scalars(t.q(Container).where(Container.id.in_(ids))):
        labels[str(c.id)] = c.container_number
    for s in t.db.scalars(t.q(Shipment).where(Shipment.id.in_(ids))):
        labels[str(s.id)] = s.reference
    for po in t.db.scalars(t.q(PurchaseOrder).where(PurchaseOrder.id.in_(ids))):
        labels[str(po.id)] = po.po_number
    lines = t.db.execute(
        t.q(PurchaseOrderLine)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrderLine.id.in_(ids))
        .with_only_columns(PurchaseOrderLine.id, PurchaseOrder.po_number, PurchaseOrderLine.sku)
    )
    for line_id, po_number, sku in lines:
        labels[str(line_id)] = f"{po_number} · {sku}"
    for inv in t.db.scalars(t.q(Invoice).where(Invoice.id.in_(ids))):
        if inv.invoice_number:
            labels[str(inv.id)] = inv.invoice_number
    # The file's name, and only that column: the job row also holds the file itself.
    imports = t.db.execute(
        t.q(ImportJob)
        .where(ImportJob.id.in_(ids), ImportJob.original_filename.is_not(None))
        .with_only_columns(ImportJob.id, ImportJob.original_filename)
    )
    for job_id, filename in imports:
        labels[str(job_id)] = filename
    return labels


def _actor_names(t: TenantSession, ids: set[UUID]) -> dict[UUID, str]:
    if not ids:
        return {}
    rows = t.db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
    return {user_id: name or email for user_id, name, email in rows}


@router.get("", response_model=schemas.AuditPage)
def list_audit_log(
    t: TenantDep,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    action: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> schemas.AuditPage:
    """History, newest first, paginated by cursor.

    A cursor rather than an offset because the log grows while it is being read: with an offset, an
    entry written between two pages shifts everything and something is silently skipped.
    """
    stmt = t.q(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit + 1)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if cursor:
        at, entry_id = decode_cursor(cursor)
        # Row-value comparison: "older than this instant, or the same instant and a smaller id".
        stmt = stmt.where(tuple_(AuditLog.at, AuditLog.id) < tuple_(literal(at), literal(entry_id)))

    rows = list(t.db.scalars(stmt))
    has_more = len(rows) > limit
    page = rows[:limit]
    actors = _actor_names(t, {row.actor_user_id for row in page if row.actor_user_id})
    mentioned: set[UUID] = set()
    entries = []
    for row in page:
        entry = schemas.AuditEntryResponse.model_validate(row)
        if row.actor_user_id:
            entry.actor_name = actors.get(row.actor_user_id)
        entries.append(entry)
        mentioned |= _ids_in(row.before) | _ids_in(row.after)
        if row.entity_id:
            mentioned.add(row.entity_id)
    return schemas.AuditPage(
        entries=entries,
        next_cursor=encode_cursor(page[-1]) if has_more and page else None,
        labels=_labels(t, mentioned),
    )
