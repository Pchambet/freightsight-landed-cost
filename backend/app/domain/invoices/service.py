"""The inbox: from an uploaded file to costs a person accepted.

THE RULE: no cost is ever created without a human confirming it. Everything here proposes; only
`confirm()` writes to the ledger, and it writes only what someone ticked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.core.money import q2, to_base
from app.domain.costing import entry
from app.domain.costing.service import Computation, compute, recompute_org
from app.domain.documents.ports import DocumentStore
from app.domain.documents.service import store_document
from app.domain.fx.service import FxService
from app.domain.invoices.checks import (
    CREDIT_NOTE_NOTE,
    check,
    duplicate_cost_note,
    invoice_line_recorded,
    invoice_recorded,
    note_field,
    recorded_note,
    same_line_on_file,
)
from app.domain.invoices.ports import (
    DocumentExtractor,
    ExtractionContext,
    ExtractionFailed,
    ExtractionInput,
    ExtractorNotConfigured,
)
from app.domain.models import (
    Container,
    Cost,
    CostScope,
    CostType,
    ErpPush,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Organization,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
)

logger = logging.getLogger(__name__)

SCOPE_MODEL: dict[CostScope, tuple[type, str, str]] = {
    CostScope.SHIPMENT: (Shipment, "shipment_id", "Shipment"),
    CostScope.CONTAINER: (Container, "container_id", "Container"),
    CostScope.PO: (PurchaseOrder, "po_id", "Purchase order"),
    CostScope.PO_LINE: (PurchaseOrderLine, "po_line_id", "Purchase order line"),
}


@dataclass(frozen=True)
class Confirmation:
    invoice: Invoice
    costs: list[Cost]
    #: Estimates this confirmation replaced, and the lines where it deliberately replaced nothing.
    superseded: list[UUID] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def create_invoice(
    db: Session,
    store: DocumentStore,
    org: Organization,
    content: bytes,
    *,
    filename: str | None,
    created_by: UUID | None = None,
    default_container_id: UUID | None = None,
) -> Invoice:
    """Store the file and open an invoice on it. Extraction happens later, in a job."""
    document = store_document(db, store, org.id, content, filename=filename, created_by=created_by)
    invoice = Invoice(
        org_id=org.id,
        document_id=document.id,
        status=InvoiceStatus.UPLOADED,
        created_by=created_by,
        default_container_id=default_container_id,
    )
    db.add(invoice)
    db.flush()
    return invoice


def context_for(db: Session, org: Organization) -> ExtractionContext:
    """What the extractor may know: this organization's own references, and nothing else's."""
    containers = {
        number: id_
        for number, id_ in db.execute(
            select(Container.container_number, Container.id).where(
                Container.org_id == org.id, Container.archived_at.is_(None)
            )
        ).all()
    }
    purchase_orders = {
        number: id_
        for number, id_ in db.execute(
            select(PurchaseOrder.po_number, PurchaseOrder.id).where(PurchaseOrder.org_id == org.id)
        ).all()
    }
    shipments = {
        reference: id_
        for reference, id_ in db.execute(
            select(Shipment.reference, Shipment.id).where(Shipment.org_id == org.id)
        ).all()
    }
    return ExtractionContext(
        base_currency=org.base_currency,
        containers=containers,
        purchase_orders=purchase_orders,
        shipment_references=shipments,
    )


def extract(
    db: Session,
    store: DocumentStore,
    fx: FxService,
    org: Organization,
    invoice: Invoice,
    extractors: list[DocumentExtractor],
) -> Invoice:
    """Read the document and write the proposed lines. Never raises: a failure is a status."""
    invoice.status = InvoiceStatus.EXTRACTING
    invoice.error = None
    db.flush()

    document = invoice.document
    content = store.get(org.id, document.storage_key)
    payload = ExtractionInput(content, document.content_type, document.filename)
    context = context_for(db, org)

    reading = None
    failures: list[str] = []
    for extractor in extractors:
        try:
            reading = extractor.extract(payload, context)
            invoice.extractor = extractor.name
            break
        except (ExtractionFailed, ExtractorNotConfigured) as exc:
            # The next extractor gets its turn; the reasons are kept for the one that has to fix it.
            failures.append(f"{extractor.name}: {exc.message}")
            logger.info("extractor %s could not read invoice %s: %s", extractor.name, invoice.id, exc)

    if reading is None:
        invoice.status = InvoiceStatus.FAILED
        invoice.error = " / ".join(failures) or "No extractor could read this document"
        db.flush()
        return invoice

    checked = check(
        db,
        fx,
        reading,
        context,
        org_id=org.id,
        base_currency=org.base_currency,
        invoice_date=reading.invoice_date,
        default_container_id=invoice.default_container_id,
    )
    # A reader that found nothing may say so with "": the guards against counting an invoice twice
    # compare these two, and an empty number is no number.
    invoice.vendor = entry.none_if_blank(reading.vendor)
    invoice.invoice_number = entry.number_or_none(reading.invoice_number)
    invoice.invoice_date = reading.invoice_date
    invoice.currency = (reading.currency or org.base_currency)[:3].upper()
    invoice.total_amount = q2(Decimal(reading.total)) if reading.total else None
    invoice.subtotal_amount = q2(Decimal(reading.subtotal)) if reading.subtotal else None
    invoice.vat_amount = q2(Decimal(reading.vat)) if reading.vat else None
    invoice.confidence = Decimal(str(checked.confidence))
    invoice.raw = {"reading": reading.model_dump(mode="json"), "notes": checked.notes}
    invoice.status = InvoiceStatus.NEEDS_REVIEW

    for line in list(invoice.lines):  # a retry replaces the proposal, never appends to it
        db.delete(line)
    db.flush()
    for number, proposal in enumerate(checked.lines, start=1):
        db.add(
            InvoiceLine(
                org_id=org.id,
                invoice_id=invoice.id,
                line_no=number,
                description=proposal.description,
                amount=q2(proposal.amount),
                currency=proposal.currency,
                cost_type=proposal.cost_type,
                scope=proposal.scope,
                target_id=proposal.target_id,
                confidence=Decimal(str(proposal.confidence)),
                accepted=False,  # only a person sets this
                notes="; ".join(proposal.notes) or None,
            )
        )
    db.flush()
    return invoice


def confirm(
    db: Session,
    fx: FxService,
    org: Organization,
    invoice: Invoice,
    *,
    created_by: UUID | None = None,
    force: bool = False,
    force_recorded: bool = False,
) -> Confirmation:
    """Turn the accepted lines into costs, all of them or none.

    Everything happens in the caller's transaction: if the tenth line is wrong, the first nine costs
    do not exist either. The rate is the one of the invoice date, fixed on the cost like any other.

    `force` is the answer to the duplicate check below, and only to that: it says a person looked at
    the cost we found and decided this invoice is not the same charge. `force_recorded` answers the
    other one — "this invoice is already in the books" — when a person has checked that the number
    only happens to match: both are written into the confirmation's notes.
    """
    if invoice.status is InvoiceStatus.CONFIRMED:
        raise Unprocessable("This invoice has already been confirmed", code="ALREADY_CONFIRMED")
    if invoice.status not in (InvoiceStatus.NEEDS_REVIEW, InvoiceStatus.FAILED):
        raise Unprocessable(
            f"An invoice can only be confirmed while it is being reviewed (this one is "
            f"{invoice.status.value})",
            code="INVALID_STATUS",
        )
    accepted = [line for line in invoice.lines if line.accepted]
    if not accepted:
        raise Unprocessable(
            "Accept at least one line before confirming: nothing is created on its own",
            code="NO_ACCEPTED_LINES",
        )

    incomplete = [
        {
            "field": f"line {line.line_no}",
            "code": "LINE_INCOMPLETE",
            "message": "an accepted line needs a cost type and a target",
        }
        for line in accepted
        if line.cost_type is None or line.scope is None or line.target_id is None
    ]
    if incomplete:
        raise Unprocessable(
            "Some accepted lines are missing a cost type or a target",
            code="LINE_INCOMPLETE",
            errors=incomplete,
        )

    # A credit note refunds money; a cost adds it. Until a cost can carry a negative amount — the
    # database itself refuses one — the two cannot meet, and guessing which way round the figures go
    # is how a 2 450 € refund becomes a 2 450 € charge. It is refused, by name, with the document
    # still on file so it can be entered by hand against the same container.
    if any(note.startswith(CREDIT_NOTE_NOTE) for note in _reading_notes(invoice)):
        raise Unprocessable(
            "This document was read as a credit note: enter the refund by hand, costs cannot yet "
            "carry a negative amount",
            code="CREDIT_NOTE_MANUAL",
        )
    negative = [line for line in accepted if line.amount < 0]
    if negative:
        raise Unprocessable(
            "A negative charge cannot become a cost yet; leave the line unaccepted and enter it by hand",
            code="NEGATIVE_COST_UNSUPPORTED",
            errors=[
                {
                    "field": f"line {line.line_no}",
                    "code": "NEGATIVE_COST_UNSUPPORTED",
                    "message": f"@negative_line|{line.amount}",
                }
                for line in negative
            ],
        )

    # Lines of one type, on one target, in one currency are one cost: the freight and its bunker
    # surcharge, the origin and destination handlings. One cost per line would be two costs with the
    # same invoice, forwarder, type and target — which the database refuses, rightly — and the
    # allocation is the same either way. Every line still points at the cost it went into.
    groups: dict[tuple[CostScope, UUID, CostType, str], list[InvoiceLine]] = {}
    for line in accepted:
        assert line.scope is not None and line.target_id is not None and line.cost_type is not None
        groups.setdefault((line.scope, line.target_id, line.cost_type, line.currency), []).append(line)

    # The same document already in the books — its statement imported, or its PDF confirmed under a
    # neighbouring spelling — is refused: no person can mean to count one invoice twice. A person may
    # still say it is another document under the same number (`force_recorded`), except for a line
    # already on file: the very line to the letter, which the database itself refuses, or the same
    # line — type, currency, freight — with the forwarder named on both copies, which by that
    # forwarder's own numbering is the same line. An override offered there could only end in an
    # error, or in the line counted twice.
    freight = [(line.scope, line.target_id) for line in accepted if line.scope and line.target_id]
    recorded = invoice_recorded(db, org.id, invoice.vendor, invoice.invoice_number, freight)
    blocking: list[Cost] = []
    for (scope, target_id, cost_type, currency), lines in groups.items():
        found = same_line_on_file(
            db,
            org.id,
            vendor=invoice.vendor,
            invoice_number=invoice.invoice_number,
            cost_type=cost_type,
            currency=currency,
            target_id=target_id,
        )
        if entry.normalize_vendor(invoice.vendor):
            found += [
                cost
                for cost in invoice_line_recorded(
                    db,
                    org.id,
                    vendor=invoice.vendor,
                    invoice_number=invoice.invoice_number,
                    cost_type=cost_type,
                    currency=currency,
                    amount=q2(sum((line.amount for line in lines), Decimal(0))),
                    scope=scope,
                    target_id=target_id,
                )
                if entry.normalize_vendor(cost.vendor)
            ]
        blocking += [cost for cost in found if cost not in blocking]
    if blocking or (recorded and not force_recorded):
        on_file = recorded + [cost for cost in blocking if cost not in recorded]
        raise Unprocessable(
            "This invoice is already recorded; reopen or delete what carries it before confirming it again",
            code="INVOICE_ALREADY_RECORDED",
            errors=[
                {"field": "invoice", "code": "INVOICE_ALREADY_RECORDED", "message": recorded_note(cost)}
                for cost in on_file
            ],
            forceable=not blocking,
        )

    notes: list[str] = _duplicate_notes(db, org, accepted, force=force)
    if recorded:
        notes.append(f"@forced_recorded|{note_field(invoice.invoice_number)}|{note_field(invoice.vendor)}")

    cost_date = invoice.invoice_date or datetime.now(UTC).date()
    comp: Computation | None = None  # computed only when a duty needs its method
    duty_loads: set[UUID] = set()  # the lines this confirmation has already written duty on
    costs: list[Cost] = []
    superseded: list[UUID] = []
    # Duty last: the customs value it is weighed against includes this invoice's own freight.
    ordered = sorted(groups.items(), key=lambda group: group[0][2] is CostType.CUSTOMS_DUTY)
    for (scope, target_id, cost_type, currency), lines in ordered:
        model, foreign_key, what = SCOPE_MODEL[scope]
        target = db.scalar(
            select(model).where(model.id == target_id, model.org_id == org.id)  # type: ignore[attr-defined]
        )
        if target is None:
            raise Unprocessable(
                f"{what} {target_id} does not belong to this organization", code="UNKNOWN_TARGET"
            )
        amount = q2(sum((line.amount for line in lines), Decimal(0)))
        resolved = fx.resolve(db, org.base_currency, currency, cost_date)
        amount_base = to_base(amount, resolved.rate)
        if cost_type is CostType.CUSTOMS_DUTY:
            comp = comp or compute(db, org)
            loads = {load.id for load in entry.loads_under(comp, scope, target_id)}
            if loads & duty_loads:
                # A second duty on goods this invoice already charged duty on — in another currency,
                # on the bill of lading over its box — is weighed with the first among the duty paid.
                # Only then is the organization computed again: a duty per box of a bill is not.
                comp = compute(db, org)
            duty_loads |= loads
        numbers = ", ".join(str(line.line_no) for line in lines)
        cost = Cost(
            org_id=org.id,
            scope=scope,
            cost_type=cost_type,
            amount=amount,
            currency=currency,
            fx_rate=resolved.rate,
            fx_date=resolved.rate_date,
            fx_source=resolved.source,
            amount_base=amount_base,
            allocation_method=entry.default_method(db, org, scope, target_id, cost_type, amount_base, comp),
            cost_date=cost_date,
            vendor=invoice.vendor,
            invoice_number=invoice.invoice_number,
            notes=(
                f"From invoice {invoice.invoice_number or invoice.id} "
                f"{'lines' if len(lines) > 1 else 'line'} {numbers}"
            ),
            created_by=created_by,
        )
        setattr(cost, foreign_key, target_id)
        candidates = entry.open_estimates(db, org.id, scope, target_id, cost_type)
        if len(candidates) == 1:
            # One estimate of this type on this target: this invoice is plainly the one it was
            # waiting for, and the pair becomes the variance.
            cost.supersedes_cost_id = candidates[0].id
            superseded.append(candidates[0].id)
        elif len(candidates) > 1:
            # Several: which one this invoice settles is a judgement, and guessing would silently
            # drop a real estimate out of the landed cost. Say so and leave them alone.
            notes.append(f"@several_estimates|{numbers}|{len(candidates)}|{cost_type.value}")
        db.add(cost)
        db.flush()
        for line in lines:
            line.cost_id = cost.id
        costs.append(cost)

    invoice.status = InvoiceStatus.CONFIRMED
    invoice.confirmed_at = datetime.now(UTC)
    db.flush()
    recompute_org(db, org)
    return Confirmation(invoice=invoice, costs=costs, superseded=superseded, notes=notes)


def reopen_confirmed(db: Session, org: Organization, invoice: Invoice) -> list[UUID]:
    """Undo a confirmation, costs and all, while nothing has left for the ERP yet.

    A confirmed invoice is history — except when the forwarder sends the correction the next day and
    the costs have gone nowhere. Then the honest move is to take back exactly what this confirmation
    wrote, rather than leave the reviewer to key a correction in somewhere else and be told, on the
    next invoice, that it looks like a duplicate.

    The line is drawn at the customer's own books: once a cost has been written into a landed cost
    over there, deleting it here would leave their stock valuation carrying a figure nothing on our
    side remembers. That is refused, with the push named, and corrected the way an accountant
    corrects a posted document — with another one.
    """
    costs = list(
        db.scalars(
            select(Cost).where(
                Cost.org_id == org.id,
                Cost.id.in_([line.cost_id for line in invoice.lines if line.cost_id is not None]),
            )
        )
    )
    pushed = _live_pushes_carrying(db, org, costs)
    if pushed:
        raise Unprocessable(
            "This invoice's costs are already in the ERP; correct them there with an adjustment "
            "document, or forget the push first",
            code="COSTS_PUSHED_TO_ERP",
            errors=[
                {
                    "field": "erp",
                    "code": "COSTS_PUSHED_TO_ERP",
                    "message": f"@pushed_cost|{push.odoo_name or push.odoo_id}|{push.status}",
                }
                for push in pushed
            ],
        )
    removed = [cost.id for cost in costs]
    for line in invoice.lines:
        line.cost_id = None
    for cost in costs:
        db.delete(cost)
    invoice.status = InvoiceStatus.NEEDS_REVIEW
    invoice.confirmed_at = None
    db.flush()
    recompute_org(db, org)
    return removed


def _live_pushes_carrying(db: Session, org: Organization, costs: list[Cost]) -> list[ErpPush]:
    """The ERP documents still holding any of these costs. A forgotten push holds nothing."""
    if not costs:
        return []
    return list(
        db.scalars(
            select(ErpPush)
            .where(
                ErpPush.org_id == org.id,
                ErpPush.forgotten_at.is_(None),
                or_(*[ErpPush.cost_ids.contains([str(cost.id)]) for cost in costs]),
            )
            .order_by(ErpPush.created_at)
        )
    )


def _reading_notes(invoice: Invoice) -> list[str]:
    """The notes the reading left on the document, as stored."""
    raw = invoice.raw if isinstance(invoice.raw, dict) else {}
    return [str(note) for note in raw.get("notes", [])]


def _duplicate_notes(
    db: Session, org: Organization, accepted: list[InvoiceLine], *, force: bool
) -> list[str]:
    """Look for the cost this invoice would double, now, and not only when it was read.

    A week can pass between the reading and the confirmation. Monday the logistics manager keys in
    the 2 450 € of freight to close the month; Tuesday the finance director confirms the invoice
    that was read on Friday, when nothing existed to warn about. The container ends up with 4 900 €
    of freight in it, and in the customer's stock valuation at the next push.

    Saying it is enough: a person who has looked at the cost we found can confirm anyway, and what
    they overrode is written into the confirmation's notes.
    """
    collisions = [
        (line, note)
        for line in accepted
        if (note := duplicate_cost_note(db, org.id, line.cost_type, line.scope, line.target_id))
    ]
    if not collisions:
        return []
    if not force:
        raise Unprocessable(
            "A cost of the same type is already recorded on this freight; confirm again with "
            "force=true if this invoice is a different charge",
            code="DUPLICATE_COST",
            errors=[
                {"field": f"line {line.line_no}", "code": "DUPLICATE_COST", "message": note}
                for line, note in collisions
            ],
        )
    return [f"@forced_duplicate|{line.line_no}|{note.split('|', 1)[1]}" for line, note in collisions]
