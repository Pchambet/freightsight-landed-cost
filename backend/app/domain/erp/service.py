"""Turning what the ERP says into purchase orders, through the import pipeline we already have.

The ERP rows are written as a CSV in our own canonical columns and handed to the same code a person
uploading a spreadsheet goes through: the same validation, the same natural keys, the same
idempotence, the same row-level report. A second, parallel writing path would be a second place for
the rules to drift.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decode_key, seal, unseal
from app.core.errors import NotFound, Unprocessable
from app.core.settings import Settings, get_settings
from app.core.tenancy import TenantSession
from app.domain.erp.ports import ErpConnector, ErpPurchaseOrder, ErpWriter
from app.domain.fx.service import FxService
from app.domain.imports import service as imports
from app.domain.models import (
    ErpConnection,
    ErpKind,
    ErpSyncRun,
    ErpSyncStatus,
    ImportKind,
    PurchaseOrder,
    PurchaseOrderStatus,
)

logger = logging.getLogger(__name__)

#: How long the schedule waits after a failure, doubling each time: 15 minutes, then 30, then an
#: hour… capped at a day. The cap matters more than the curve — the daily sweep is already slow —
#: because the protection has to survive somebody making the schedule more frequent.
FIRST_BACKOFF = timedelta(minutes=15)
MAX_BACKOFF = timedelta(days=1)


def backoff_for(consecutive_failures: int) -> timedelta:
    """The wait after `consecutive_failures` failures in a row, the first being 1."""
    if consecutive_failures < 1:
        return timedelta(0)
    doubled: timedelta = FIRST_BACKOFF * (2 ** min(consecutive_failures - 1, 20))
    return doubled if doubled < MAX_BACKOFF else MAX_BACKOFF


def note_success(connection: ErpConnection, at: datetime) -> None:
    """A read that worked clears the slate: the error, the counter and the wait all go."""
    connection.last_sync_at = at
    connection.last_error = None
    connection.consecutive_failures = 0
    connection.retry_after = None


def note_failure(connection: ErpConnection, reason: str, *, at: datetime | None = None) -> None:
    """Record why the ERP could not be read, and how long to leave it alone.

    The reason is what the screen shows, so it is the ERP's own words rather than ours: "see the
    logs" is not something a customer can act on.
    """
    moment = at or datetime.now(UTC)
    connection.last_error = reason[:1000]
    connection.consecutive_failures += 1
    connection.retry_after = moment + backoff_for(connection.consecutive_failures)


def due_for_sync(connection: ErpConnection, now: datetime | None = None) -> bool:
    """Whether the *schedule* may read this ERP. A person pressing the button is never held back."""
    if connection.retry_after is None:
        return True
    return (now or datetime.now(UTC)) >= connection.retry_after


#: How far back before the last successful sync to look again. The ERP's clock is not ours, a write
#: can land a second before the read that missed it, and re-reading five minutes of orders costs one
#: request. Missing an order costs a landed cost that is quietly wrong.
SYNC_OVERLAP = timedelta(minutes=5)

#: The container magic for a sealed ERP credential.
ERP_MAGIC = b"FSERP1"
KEY_NAME = "ERP_ENCRYPTION_KEY"

#: The canonical columns of our own purchase-order import, in the order they are written.
COLUMNS = (
    "po_number",
    "supplier_name",
    "currency",
    "order_date",
    "line_no",
    "sku",
    "description",
    "quantity",
    "unit_price",
    "unit_weight_kg",
    "unit_volume_cbm",
    "hs_code",
)


@dataclass(frozen=True)
class SyncResult:
    run: ErpSyncRun
    purchase_orders: int
    lines: int


def seal_key(api_key: str, settings: Settings | None = None) -> bytes:
    key = decode_key((settings or get_settings()).erp_encryption_key, KEY_NAME)
    return seal(api_key.encode(), key, magic=ERP_MAGIC)


def open_key(sealed: bytes, settings: Settings | None = None) -> str:
    key = decode_key((settings or get_settings()).erp_encryption_key, KEY_NAME)
    return unseal(bytes(sealed), key, magic=ERP_MAGIC).decode()


def connector_for(connection: ErpConnection, settings: Settings | None = None) -> ErpConnector:
    if connection.kind is ErpKind.ODOO:
        from app.adapters.erp.odoo import OdooConnector

        return OdooConnector(
            connection.url,
            connection.database,
            connection.login,
            open_key(connection.api_key_sealed, settings),
            timeout=(settings or get_settings()).erp_timeout_seconds,
        )
    raise NotFound(f"ERP connector {connection.kind.value}")  # pragma: no cover - one kind so far


def mark_cancelled(db: Session, org_id: UUID, orders: list[ErpPurchaseOrder]) -> int:
    """Mark the orders the ERP has cancelled, and only the ones we already know about.

    Never a delete, and never a creation: an order cancelled upstream may already carry costs and
    containers here, and one we have never seen has nothing to say to us.
    """
    numbers = [order.number for order in orders if order.cancelled]
    if not numbers:
        return 0
    known = list(
        db.scalars(
            select(PurchaseOrder).where(PurchaseOrder.org_id == org_id, PurchaseOrder.po_number.in_(numbers))
        )
    )
    now = datetime.now(UTC)
    for po in known:
        if po.status is not PurchaseOrderStatus.CANCELLED:
            po.status = PurchaseOrderStatus.CANCELLED
            po.cancelled_at = now
    db.flush()
    return len(known)


def writer_for(connection: ErpConnection, settings: Settings | None = None) -> ErpWriter:
    """The same adapter, seen through the writing half of the port.

    A connector that cannot write back is a configuration a customer can be in — an ERP we only read
    — so this says so rather than failing at the moment someone presses the button.
    """
    connector = connector_for(connection, settings)
    if not isinstance(connector, ErpWriter):
        raise Unprocessable(
            f"The {connection.kind.value} connector cannot write back yet", code="ERP_READ_ONLY"
        )
    return connector


def to_csv(orders: list[ErpPurchaseOrder]) -> tuple[bytes, int]:
    """One row per order line, in our canonical columns. Returns the file and the line count."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(COLUMNS))
    writer.writeheader()
    written = 0
    for order in orders:
        for number, line in enumerate(order.lines, start=1):
            writer.writerow(
                {
                    "po_number": order.number,
                    "supplier_name": order.supplier or "",
                    "currency": order.currency,
                    "order_date": order.order_date.isoformat() if order.order_date else "",
                    "line_no": number,
                    "sku": line.sku or "",
                    # The unit is kept in words: an importer reading "20 Dozens" in Odoo should find
                    # the same words here, even though the quantity has been converted to units.
                    "description": f"{line.description} ({line.uom})" if line.uom else line.description,
                    "quantity": line.quantity,
                    "unit_price": line.unit_price,
                    "unit_weight_kg": line.unit_weight_kg or "",
                    "unit_volume_cbm": line.unit_volume_cbm or "",
                    "hs_code": line.hs_code or "",
                }
            )
            written += 1
    return buffer.getvalue().encode(), written


def watermark(connection: ErpConnection, *, full: bool = False) -> datetime | None:
    """Where to start reading. None means everything, which is what a first or forced sync wants."""
    if full or connection.last_sync_at is None:
        return None
    return connection.last_sync_at - SYNC_OVERLAP


def sync(
    t: TenantSession,
    connection: ErpConnection,
    connector: ErpConnector | None = None,
    *,
    since: datetime | None = None,
    limit: int | None = None,
    full: bool = False,
    fx: FxService | None = None,
) -> SyncResult:
    """Read the ERP and import what it says. One run row, whatever happens.

    Incremental by default: only what the ERP has touched since the last successful sync, minus an
    overlap. `full` re-reads everything, which is the button for "it looks wrong, start again".
    """
    since = since if since is not None else watermark(connection, full=full)
    run = ErpSyncRun(org_id=t.org_id, connection_id=connection.id, status=ErpSyncStatus.RUNNING, detail={})
    t.db.add(run)
    t.db.flush()

    impl = connector or connector_for(connection)
    try:
        fetched = impl.fetch_purchase_orders(since=since, limit=limit)
        cancelled = mark_cancelled(t.db, t.org_id, fetched)
        orders = [order for order in fetched if not order.cancelled]
        content, line_count = to_csv(orders)
        if not orders or not line_count:
            run.status = ErpSyncStatus.SUCCEEDED
            run.finished_at = datetime.now(UTC)
            run.detail = {
                "message": "nothing new in the ERP",
                "cancelled": cancelled,
                "since": since.isoformat() if since else None,
            }
            note_success(connection, run.finished_at)
            t.db.flush()
            return SyncResult(run=run, purchase_orders=0, lines=0)

        job = imports.create_job(t, ImportKind.PURCHASE_ORDERS, content, "odoo-sync.csv")
        # Our own columns, so the mapping is the identity: no guessing, no remembered mapping.
        mapping = {column: column for column in COLUMNS}
        report = imports.run_job(t, job, mapping, dry_run=False, on_error="skip_rows", fx=fx)

        run.status = ErpSyncStatus.SUCCEEDED
        run.import_job_id = job.id
        run.purchase_orders = len(orders)
        run.lines = line_count
        run.detail = {
            **report.to_dict(),
            "cancelled": cancelled,
            "since": since.isoformat() if since else None,
        }
        run.finished_at = datetime.now(UTC)
        note_success(connection, run.finished_at)
        t.db.flush()
        return SyncResult(run=run, purchase_orders=len(orders), lines=line_count)
    except Exception as exc:
        # The run row is the point: a sync that failed silently is a customer who thinks their orders
        # are up to date. It is written even though the import itself rolled back.
        run.status = ErpSyncStatus.FAILED
        run.error = str(exc)[:1000]
        run.finished_at = datetime.now(UTC)
        note_failure(connection, run.error, at=run.finished_at)
        logger.exception("ERP sync failed for org %s", t.org_id)
        raise


def connection_for(db: Session, org_id: UUID) -> ErpConnection | None:
    return db.scalar(select(ErpConnection).where(ErpConnection.org_id == org_id))
