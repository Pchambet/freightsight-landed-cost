"""Connecting an organization's ERP, and reading its purchase orders.

Read-only: this brick imports orders, it writes nothing back into the customer's system.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.crypto import CryptoError
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.net import assert_public_host
from app.core.tenancy import TenantDep
from app.domain.audit.service import ERP_LANDED_COST_PUSHED, ERP_PUSH_FORGOTTEN
from app.domain.audit.service import record as record_audit
from app.domain.erp import landed_cost, service
from app.domain.erp.urls import odoo_record_url
from app.domain.models import Container, ErpConnection, ErpPush, ErpSyncRun

router = APIRouter(prefix="/erp", tags=["erp"])


@router.get("/connection", response_model=schemas.ErpConnectionResponse)
def get_connection(t: TenantDep) -> ErpConnection:
    connection = service.connection_for(t.db, t.org_id)
    if connection is None:
        raise NotFound("ERP connection")
    return connection


@router.post("/connection", response_model=schemas.ErpConnectionResponse, status_code=status.HTTP_201_CREATED)
def create_connection(payload: schemas.ErpConnectionCreate, t: TenantDep, p: WriterDep) -> ErpConnection:
    """Store the connection — after proving it works.

    The test happens before anything is written: a saved connection that has never authenticated is
    a promise the next sync will break, and the error the ERP gives is far more useful now, while
    the person who typed the credentials is still looking at them.
    """
    if service.connection_for(t.db, t.org_id) is not None:
        raise Conflict("This organization already has an ERP connection", code="ERP_ALREADY_CONNECTED")
    # Before the very first connection, because `test_connection()` below runs on demand, as often
    # as someone asks, and an unvetted address makes this form a probe of our own private network.
    # The adapter checks again before each call: this one is here so the refusal names the field
    # the person is looking at.
    assert_public_host(payload.url)
    try:
        sealed = service.seal_key(payload.api_key)
    except CryptoError as exc:
        raise Unprocessable(
            f"This deployment cannot store ERP credentials: {exc}", code="ERP_KEY_MISSING"
        ) from exc

    connection = ErpConnection(
        org_id=t.org_id,
        kind=payload.kind,
        url=payload.url,
        database=payload.database,
        login=payload.login,
        api_key_sealed=sealed,
        created_by=p.user_id,
    )
    identity = service.connector_for(connection).test_connection()
    connection.server_version = identity.server_version
    connection.company = identity.company
    t.add(connection)
    t.db.commit()
    t.db.refresh(connection)
    return connection


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(t: TenantDep, _: WriterDep) -> None:
    """Forget the ERP. The orders already imported stay: they are ours now."""
    connection = service.connection_for(t.db, t.org_id)
    if connection is None:
        raise NotFound("ERP connection")
    t.db.delete(connection)
    t.db.commit()


@router.post("/sync", response_model=schemas.ErpSyncRunResponse, status_code=status.HTTP_202_ACCEPTED)
def sync_now(t: TenantDep, fx: FxDep, _: WriterDep, full: bool = False) -> ErpSyncRun:
    """Read the ERP now, in this request.

    Deliberately synchronous: someone has just connected their Odoo and is watching this button.
    The daily refresh is the job; this is the reassurance. `full=true` re-reads the whole history
    instead of what has changed — the button for "this looks wrong, start again".
    """
    connection = service.connection_for(t.db, t.org_id)
    if connection is None:
        raise NotFound("ERP connection")
    try:
        result = service.sync(t, connection, full=full, fx=fx)
    except Exception:
        # The run row was written before the failure and says why; keep it and let the error surface.
        t.db.commit()
        raise
    t.db.commit()
    t.db.refresh(result.run)
    return result.run


@router.get("/sync-runs", response_model=list[schemas.ErpSyncRunResponse])
def list_sync_runs(t: TenantDep, limit: int = 20) -> list[ErpSyncRun]:
    return list(
        t.db.scalars(
            select(ErpSyncRun)
            .where(ErpSyncRun.org_id == t.org_id)
            .order_by(ErpSyncRun.started_at.desc())
            .limit(min(limit, 100))
        )
    )


# ---------------------------------------------------------------------------- landed cost push


def _preview_response(plan: landed_cost.Plan) -> schemas.ErpLandedCostPreview:
    return schemas.ErpLandedCostPreview(
        container_id=plan.container_id,
        container_number=plan.container_number,
        receipt_id=plan.receipt.picking_id if plan.receipt else None,
        receipt_name=plan.receipt.name if plan.receipt else None,
        costs=[
            schemas.ErpPlannedCost(
                label=cost.label,
                cost_ids=cost.cost_ids,
                amount=cost.amount,
                lines=[
                    schemas.ErpPlannedLine(
                        move_id=line.move_id,
                        product_name=line.product_name,
                        sku=line.sku,
                        quantity=Decimal(line.quantity),
                        amount=line.amount,
                    )
                    for line in cost.lines
                ],
                pushed_as=cost.pushed_as,
                pushed_push_id=cost.pushed_push_id,
            )
            for cost in plan.costs
        ],
        total=plan.total,
        blockers=[schemas.ErpBlocker(code=b.code, message=b.message, params=b.params) for b in plan.blockers],
        pushable=plan.pushable,
        already_pushed_as=", ".join(sorted({c.pushed_as for c in plan.costs if c.pushed_as})) or None,
    )


containers_router = APIRouter(prefix="/containers", tags=["erp"])


def _container(t: TenantDep, container_id: UUID) -> Container:
    container = t.db.scalar(
        t.q(Container).where(Container.id == container_id, Container.archived_at.is_(None))
    )
    if container is None:
        raise NotFound("Container")
    return container


@containers_router.get("/{container_id}/erp/landed-cost/preview", response_model=schemas.ErpLandedCostPreview)
def preview_landed_cost(container_id: UUID, t: TenantDep) -> schemas.ErpLandedCostPreview:
    """What we would write into the ERP for this container, and what stands in the way.

    Writes nothing, on purpose: the person clicking should see the object before it exists.
    """
    container = _container(t, container_id)
    connection = service.connection_for(t.db, t.org_id)
    writer = service.writer_for(connection) if connection is not None else None
    plan = landed_cost.plan(t.db, t.org, container, writer, connection)
    return _preview_response(plan)


@containers_router.post(
    "/{container_id}/erp/landed-cost",
    response_model=schemas.ErpPushResponse,
    status_code=status.HTTP_201_CREATED,
)
def push_landed_cost(container_id: UUID, t: TenantDep, p: WriterDep) -> schemas.ErpPushResponse:
    """Create the landed cost in the ERP, in draft.

    Never validated by us: validating posts accounting entries in someone else's books, and that
    decision belongs to whoever owns them. Pushing the same costs twice returns the first push
    rather than writing a second document.
    """
    container = _container(t, container_id)
    connection = service.connection_for(t.db, t.org_id)
    if connection is None:
        raise NotFound("ERP connection")
    writer = service.writer_for(connection)
    record, _ = landed_cost.push(t.db, t.org, container, writer, connection, created_by=p.user_id)
    record_audit(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=ERP_LANDED_COST_PUSHED,
        entity_type="container",
        entity_id=container.id,
        after={
            "odoo_model": record.odoo_model,
            "odoo_id": record.odoo_id,
            "odoo_name": record.odoo_name,
            "status": record.status,
            "cost_ids": record.cost_ids,
            # False on a normal push; True when an interrupted attempt had already created the
            # draft and this one filled it in rather than making a second.
            "adopted": bool(record.response.get("freightsight_adopted")),
        },
    )
    t.db.commit()
    t.db.refresh(record)
    return _push_response(record, connection)


def _push_response(record: ErpPush, connection: ErpConnection | None) -> schemas.ErpPushResponse:
    record_url = (
        odoo_record_url(connection.url, connection.database, record.odoo_model, record.odoo_id)
        if connection is not None
        else None
    )
    return schemas.ErpPushResponse(
        id=record.id,
        odoo_model=record.odoo_model,
        odoo_id=record.odoo_id,
        odoo_name=record.odoo_name,
        record_url=record_url,
        status=record.status,
        cost_ids=[UUID(value) for value in record.cost_ids],
        created_at=record.created_at,
        adopted=bool(record.response.get("freightsight_adopted")),
        forgotten_at=record.forgotten_at,
        forgotten_reason=record.forgotten_reason,
    )


@containers_router.get("/{container_id}/erp/pushes", response_model=list[schemas.ErpPushResponse])
def list_pushes(container_id: UUID, t: TenantDep) -> list[schemas.ErpPushResponse]:
    """Every landed cost we have written for this container, newest first.

    Forgotten ones included, and marked: they are the record of something that was in the customer's
    books, and hiding them would make the audit trail a shorter story than what happened.
    """
    container = _container(t, container_id)
    records = t.db.scalars(
        select(ErpPush)
        .where(ErpPush.org_id == t.org_id, ErpPush.container_id == container.id)
        .order_by(ErpPush.created_at.desc())
    )
    connection = service.connection_for(t.db, t.org_id)
    return [_push_response(record, connection) for record in records]


@containers_router.delete("/{container_id}/erp/pushes/{push_id}", response_model=schemas.ErpPushResponse)
def forget_push(
    container_id: UUID,
    push_id: UUID,
    t: TenantDep,
    p: WriterDep,
    reason: str | None = None,
) -> schemas.ErpPushResponse:
    """Forget a push whose document is gone from the ERP, so its costs can be pushed again.

    Not a deletion: the row stays and says it was forgotten, by whom and why. It is the only record
    that we once wrote into someone's accounts, and the audit log points at it.
    """
    container = _container(t, container_id)
    record = t.db.scalar(t.q(ErpPush).where(ErpPush.id == push_id, ErpPush.container_id == container.id))
    if record is None:
        raise NotFound("ERP push")
    connection = service.connection_for(t.db, t.org_id)
    writer = service.writer_for(connection) if connection is not None else None
    before = {"forgotten_at": None, "cost_ids": record.cost_ids}
    _, document = landed_cost.forget(t.db, t.org, record, writer, actor_user_id=p.user_id, reason=reason)
    record_audit(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=ERP_PUSH_FORGOTTEN,
        entity_type="container",
        entity_id=container.id,
        before=before,
        after={
            "push_id": str(record.id),
            "odoo_id": record.odoo_id,
            "odoo_name": record.odoo_name,
            "forgotten_at": record.forgotten_at.isoformat() if record.forgotten_at else None,
            "forgotten_reason": record.forgotten_reason,
            # Null when the document was already gone; "done" or "cancel" when it is still in their
            # books and we let go of it anyway. That is the line this trail exists for.
            "document_state": document.state if document is not None else None,
            "cost_ids": record.cost_ids,
        },
    )
    t.db.commit()
    t.db.refresh(record)
    return _push_response(record, connection)
