"""The invoice inbox.

THE RULE: no cost is created without a human confirming it. Upload and extraction only ever propose;
`POST /invoices/{id}/confirm` is the single place in this application where an invoice becomes money
in the ledger, and it writes only the lines somebody accepted.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, selectinload

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import (
    INVOICE_CONFIRMED,
    INVOICE_CORRECTED,
    INVOICE_REJECTED,
    INVOICE_REOPENED,
    record,
)
from app.domain.costing import entry
from app.domain.documents.registry import get_store
from app.domain.invoices.service import confirm as confirm_invoice
from app.domain.invoices.service import create_invoice, reopen_confirmed
from app.domain.models import Container, Cost, Invoice, InvoiceLine, InvoiceStatus
from app.jobs.app import defer

router = APIRouter(prefix="/invoices", tags=["invoices"])


#: Postgres's « lock_not_available »: another transaction holds the row.
_LOCK_NOT_AVAILABLE = "55P03"


def _get(t: TenantSession, invoice_id: UUID, *, lock: bool = False) -> Invoice:
    """The invoice and its lines. `lock` is for every route that writes: two confirmations of one
    invoice at once — two tabs, two people on the quarter's queue — would otherwise both read "being
    reviewed" and both write its costs. The second is not kept waiting: the reading job holds the row
    for as long as a model takes to answer, so a busy invoice is said at once (409 INVOICE_BUSY)."""
    stmt = (
        t.q(Invoice)
        .where(Invoice.id == invoice_id)
        .options(selectinload(Invoice.lines), selectinload(Invoice.document))
    )
    if lock:
        stmt = stmt.with_for_update(of=Invoice, nowait=True).execution_options(populate_existing=True)
    try:
        invoice = t.db.scalar(stmt)
    except OperationalError as e:
        if getattr(e.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        t.db.rollback()
        raise Conflict(
            "This invoice is being read or changed at this moment; try again in a moment",
            code="INVOICE_BUSY",
        ) from e
    if invoice is None:
        raise NotFound("Invoice")
    return invoice


def _containers_from_costs(db: Session, costs: list[Cost]) -> list[schemas.InvoiceContainerLink]:
    ids = {cost.container_id for cost in costs if cost.container_id is not None}
    if not ids:
        return []
    rows = db.scalars(select(Container).where(Container.id.in_(ids)).order_by(Container.container_number))
    return [schemas.InvoiceContainerLink(id=row.id, container_number=row.container_number) for row in rows]


def _response(invoice: Invoice) -> schemas.InvoiceResponse:
    summary = schemas.InvoiceSummary.model_validate(invoice).model_dump()
    summary["filename"] = invoice.document.filename if invoice.document else None
    return schemas.InvoiceResponse(
        **summary,
        lines=[schemas.InvoiceLineResponse.model_validate(ln) for ln in invoice.lines],
        # The route, not a storage URL: the bytes always come back through this API.
        document_url=f"/api/v1/invoices/{invoice.id}/document",
        notes=list(invoice.raw.get("notes", [])) if isinstance(invoice.raw, dict) else [],
    )


@router.post("", response_model=schemas.InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def upload_invoice(
    t: TenantDep,
    p: WriterDep,
    file: UploadFile = File(...),
    default_container_id: UUID | None = Form(default=None),
) -> schemas.InvoiceResponse:
    """Accept a file and open an invoice on it. Reading happens in a job; this answers immediately.

    `default_container_id` is for an invoice dropped from a container's page: the lines on which
    the reading finds no container or order of its own are proposed on that container, and say so.

    The extraction job is deferred inside this transaction: if the upload rolls back, the job never
    existed either.
    """
    if default_container_id is not None:
        t.get_or_404(Container, default_container_id, "Container")
    content = await file.read()
    invoice = create_invoice(
        t.db,
        get_store(t.db),
        t.org,
        content,
        filename=file.filename,
        created_by=p.user_id,
        default_container_id=default_container_id,
    )
    from app.jobs.tasks import extract_invoice

    defer(
        t.db,
        extract_invoice,
        lock=f"invoice:{invoice.id}",
        org_id=str(t.org_id),
        invoice_id=str(invoice.id),
    )
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.get("", response_model=list[schemas.InvoiceSummary])
def list_invoices(t: TenantDep, invoice_status: InvoiceStatus | None = None) -> list[schemas.InvoiceSummary]:
    stmt = t.q(Invoice).options(selectinload(Invoice.document)).order_by(Invoice.created_at.desc())
    if invoice_status:
        stmt = stmt.where(Invoice.status == invoice_status)
    out = []
    for invoice in t.db.scalars(stmt):
        summary = schemas.InvoiceSummary.model_validate(invoice)
        summary.filename = invoice.document.filename if invoice.document else None
        out.append(summary)
    return out


@router.get("/{invoice_id}", response_model=schemas.InvoiceResponse)
def get_invoice(invoice_id: UUID, t: TenantDep) -> schemas.InvoiceResponse:
    return _response(_get(t, invoice_id))


@router.patch("/{invoice_id}", response_model=schemas.InvoiceResponse)
def correct_invoice(
    invoice_id: UUID, payload: schemas.InvoiceUpdate, t: TenantDep, p: WriterDep
) -> schemas.InvoiceResponse:
    """Correct the header the reader took — the number, the forwarder, the date — before confirming.

    Every guard against counting one invoice twice compares the number and the forwarder, and the
    confirmation writes them on each cost: a date read as the number is a false « already recorded »
    today and a missed double entry tomorrow. Only while the invoice is being reviewed; what a
    confirmed invoice says is corrected by reopening it.
    """
    invoice = _get(t, invoice_id, lock=True)
    if invoice.status not in (InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.FAILED):
        raise Unprocessable(
            f"Only an invoice being reviewed can be corrected (this one is {invoice.status.value})",
            code="INVALID_STATUS",
        )
    changes = payload.model_dump(exclude_unset=True)
    before = {field: getattr(invoice, field) for field in changes}
    for field, value in changes.items():
        setattr(invoice, field, value)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=INVOICE_CORRECTED,
        entity_type="invoice",
        entity_id=invoice.id,
        before=before,
        after=changes,
    )
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.get("/{invoice_id}/document")
def get_invoice_document(invoice_id: UUID, t: TenantDep) -> Response:
    """The file itself, streamed by the API.

    Never a storage URL: a document stays behind the same authentication and the same tenancy checks
    as its metadata, wherever its bytes happen to live.
    """
    invoice = _get(t, invoice_id)
    content = get_store(t.db).get(t.org_id, invoice.document.storage_key)
    filename = invoice.document.filename or f"invoice-{invoice.id}"
    return Response(
        content=content,
        media_type=invoice.document.content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.patch("/{invoice_id}/lines/{line_id}", response_model=schemas.InvoiceResponse)
def update_line(
    invoice_id: UUID,
    line_id: UUID,
    payload: schemas.InvoiceLineUpdate,
    t: TenantDep,
    _: WriterDep,
) -> schemas.InvoiceResponse:
    """Correct what was read, and accept the line or not. This is the human half of the loop."""
    invoice = _get(t, invoice_id, lock=True)
    if invoice.status is InvoiceStatus.CONFIRMED:
        raise NotFound("Invoice line")  # a confirmed invoice is history, not a draft
    line = next((ln for ln in invoice.lines if ln.id == line_id), None)
    if line is None:
        raise NotFound("Invoice line")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(line, field, value)
    t.db.flush()
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.post(
    "/{invoice_id}/lines",
    response_model=schemas.InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_line(
    invoice_id: UUID,
    payload: schemas.InvoiceLineCreate,
    t: TenantDep,
    _: WriterDep,
) -> schemas.InvoiceResponse:
    """Add the charge the reading missed — the other half of « it does not add up ».

    The arithmetic check is only worth printing if the screen it prints on can answer it: a line the
    patterns dropped because its wording carried « TVA 20 % », or a charge on a second page nobody
    read, otherwise sends the reviewer off to key a cost somewhere else, unlinked to the invoice it
    came from and flagged as a duplicate on the next one.
    """
    invoice = _get(t, invoice_id, lock=True)
    if invoice.status is InvoiceStatus.CONFIRMED:
        raise NotFound("Invoice line")  # a confirmed invoice is history, not a draft
    line = InvoiceLine(
        org_id=t.org_id,
        invoice_id=invoice.id,
        line_no=max((ln.line_no for ln in invoice.lines), default=0) + 1,
        description=payload.description,
        amount=payload.amount,
        currency=payload.currency or invoice.currency or t.org.base_currency,
        cost_type=payload.cost_type,
        scope=payload.scope,
        target_id=payload.target_id,
        confidence=Decimal("0"),  # nothing read it; someone typed it
        accepted=False,  # and someone still has to tick it
        notes="@added_by_human",
    )
    t.db.add(line)
    t.db.flush()
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.post("/{invoice_id}/confirm", response_model=schemas.InvoiceConfirmResponse)
def confirm(
    invoice_id: UUID,
    t: TenantDep,
    fx: FxDep,
    p: WriterDep,
    payload: schemas.InvoiceConfirmRequest | None = None,
) -> schemas.InvoiceConfirmResponse:
    """Create the accepted lines as costs — all of them, or none of them.

    One transaction: the costs, the invoice's new status and the recomputed allocations land
    together. A line pointing at something that is not ours stops the whole confirmation.
    """
    invoice = _get(t, invoice_id, lock=True)
    entry.lock_books(t.db, t.org_id)
    try:
        result = confirm_invoice(
            t.db,
            fx,
            t.org,
            invoice,
            created_by=p.user_id,
            force=bool(payload and payload.force),
            force_recorded=bool(payload and payload.force_recorded),
        )
    except Exception:
        # All or nothing, said out loud: a refusal halfway through leaves no half-written costs
        # behind, not even for the rest of this session.
        t.db.rollback()
        raise
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=INVOICE_CONFIRMED,
        entity_type="invoice",
        entity_id=invoice.id,
        after={
            "invoice_number": invoice.invoice_number,
            "vendor": invoice.vendor,
            "cost_ids": [c.id for c in result.costs],
            "total_amount": invoice.total_amount,
            # what a person overruled to confirm it, if anything: a duplicate, an invoice on record
            "notes": [
                note for note in result.notes if note.startswith(("@forced_duplicate", "@forced_recorded"))
            ],
        },
    )
    t.db.commit()
    t.db.refresh(invoice)
    return schemas.InvoiceConfirmResponse(
        invoice=_response(invoice),
        costs_created=len(result.costs),
        cost_ids=[c.id for c in result.costs],
        containers=_containers_from_costs(t.db, result.costs),
        superseded_estimate_ids=result.superseded,
        notes=result.notes,
    )


@router.post("/{invoice_id}/reopen", response_model=schemas.InvoiceResponse)
def reopen(invoice_id: UUID, t: TenantDep, p: WriterDep) -> schemas.InvoiceResponse:
    """Put an invoice back in review without re-uploading the file.

    A rejected one only changes status. A confirmed one gives back what it wrote: the costs it
    created are deleted in this same transaction and the audit entry names them, so the correction
    the forwarder sent the next morning is made on the invoice it belongs to. Once those costs have
    reached the customer's ERP the confirmation stops being ours to undo, and the service refuses.
    """
    invoice = _get(t, invoice_id, lock=True)
    entry.lock_books(t.db, t.org_id)
    if invoice.status not in (InvoiceStatus.REJECTED, InvoiceStatus.CONFIRMED):
        raise Unprocessable(
            f"Only a rejected or confirmed invoice can be reopened (this one is {invoice.status.value})",
            code="INVALID_STATUS",
        )
    if not invoice.lines:
        raise Unprocessable(
            "This invoice has no extracted lines — relaunch extraction instead",
            code="NO_LINES",
        )
    before = {"status": invoice.status}
    removed: list[UUID] = []
    if invoice.status is InvoiceStatus.CONFIRMED:
        try:
            removed = reopen_confirmed(t.db, t.org, invoice)
        except Exception:
            # Same all-or-nothing as the confirmation it undoes: a refusal leaves every cost where
            # it was, not a half-emptied invoice.
            t.db.rollback()
            raise
    else:
        invoice.status = InvoiceStatus.NEEDS_REVIEW
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=INVOICE_REOPENED,
        entity_type="invoice",
        entity_id=invoice.id,
        before=before,
        after={"status": invoice.status, "costs_deleted": removed},
    )
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.post("/{invoice_id}/reject", response_model=schemas.InvoiceResponse)
def reject(invoice_id: UUID, t: TenantDep, p: WriterDep) -> schemas.InvoiceResponse:
    """Not our invoice, a duplicate, an error: it stays on file, it creates nothing."""
    invoice = _get(t, invoice_id, lock=True)
    if invoice.status is InvoiceStatus.CONFIRMED:
        raise NotFound("Invoice")  # confirmed costs are undone by deleting the costs, not the invoice
    before = {"status": invoice.status}
    invoice.status = InvoiceStatus.REJECTED
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=INVOICE_REJECTED,
        entity_type="invoice",
        entity_id=invoice.id,
        before=before,
        after={"status": invoice.status},
    )
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)


@router.post("/{invoice_id}/retry", response_model=schemas.InvoiceResponse)
def retry(invoice_id: UUID, t: TenantDep, _: WriterDep) -> schemas.InvoiceResponse:
    """Read the document again — after a fix on our side, or once a model key exists."""
    invoice = _get(t, invoice_id, lock=True)
    if invoice.status is InvoiceStatus.CONFIRMED:
        raise NotFound("Invoice")
    invoice.status = InvoiceStatus.UPLOADED
    invoice.error = None
    t.db.flush()
    from app.jobs.tasks import extract_invoice

    defer(
        t.db,
        extract_invoice,
        lock=f"invoice:{invoice.id}",
        org_id=str(t.org_id),
        invoice_id=str(invoice.id),
    )
    t.db.commit()
    t.db.refresh(invoice)
    return _response(invoice)
