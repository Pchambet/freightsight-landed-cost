"""What we verify ourselves, whatever read the invoice.

An extractor — regex or model — is a witness, not an authority. These checks are the arithmetic and
the tenancy facts that no witness gets to overrule:

  * the lines plus VAT have to add up to the stated total, to the cent (0.02 of slack for the
    supplier's own rounding). They usually do not, because a charge was missed — so the confidence
    is capped and a person is told which way it is out;
  * a document that refunds rather than charges — a credit note — is named as one, whatever it is
    headed and whichever way its signs are written;
  * a container reference only survives if it is a container this organization actually has. A model
    handed a list can still return something that is not on it;
  * a currency with no ECB rate for the invoice date cannot become a cost silently;
  * an invoiced cost of the same type on the same freight is flagged so a person does not
    double-count — across scopes, because the freight billed per container is often already recorded
    on the shipment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.domain.costing.entry import normalize_invoice_number, normalize_vendor, same_invoice
from app.domain.fx.service import FxService
from app.domain.invoices.ports import ExtractedLine, ExtractionContext, FreightInvoice
from app.domain.models import Container, Cost, CostScope, CostStatus, CostType, PurchaseOrderLine

#: How far the sum of the lines may be from the stated total before we stop believing the reading.
TOLERANCE = Decimal("0.02")
#: The ceiling applied to a reading whose arithmetic does not hold.
CAPPED_CONFIDENCE = 0.5
#: Every note that says « this document refunds money, it does not ask for any ».
CREDIT_NOTE_NOTE = "@credit_note"
#: Neither a total nor a pre-tax total could be read: nothing says the lines are all there.
UNVERIFIED_TOTAL_NOTE = "@unverified_total"


@dataclass
class CheckedLine:
    description: str
    amount: Decimal
    currency: str
    cost_type: CostType | None
    scope: CostScope | None
    target_id: UUID | None
    confidence: float
    notes: list[str]


@dataclass
class CheckedInvoice:
    invoice: FreightInvoice
    lines: list[CheckedLine]
    confidence: float
    notes: list[str]


def arithmetic_holds(invoice: FreightInvoice) -> tuple[bool, Decimal | None]:
    """Whether the charges read add up. Returns the difference when they do not.

    The invoice's own pre-tax total is the better yardstick when it prints one: the charges are
    supposed to reach exactly that figure, and the comparison holds even when the VAT could not be
    read — which is when a missed line would otherwise pass unnoticed, the total being short by
    precisely the amount nobody is looking at. Without a stated subtotal, Σ lines + VAT against the
    grand total is the same check with one more unknown in it.
    """
    line_sum = sum((ln.decimal() or Decimal(0) for ln in invoice.lines), Decimal(0))
    if invoice.subtotal is not None:
        try:
            subtotal = Decimal(invoice.subtotal)
        except ArithmeticError:  # pragma: no cover - the model validated it
            return False, None
        difference = (line_sum - subtotal).quantize(Decimal("0.01"))
        return abs(difference) <= TOLERANCE, difference
    if invoice.total is None:
        return True, None
    try:
        total = Decimal(invoice.total)
    except ArithmeticError:  # pragma: no cover - the model validated it
        return False, None
    vat = Decimal(invoice.vat) if invoice.vat else Decimal(0)
    difference = (line_sum + vat - total).quantize(Decimal("0.01"))
    return abs(difference) <= TOLERANCE, difference


def _same_freight(db: Session, org_id: UUID, scope: CostScope, target_id: UUID) -> ColumnElement[bool] | None:
    """Where a cost covering this target may already sit, whatever scope it was recorded at.

    A forwarder bills the ocean freight per container; the same freight is very often already on
    file at shipment level, because that is how it was quoted. Comparing scope for scope catches
    the cheapest duplicate — drayage, container against container — and lets the most expensive one
    through in silence: on the sample data that is 3 000 € of freight counted twice, with a green
    tick next to it. A charge landing on a box therefore also looks at its shipment, and the reverse;
    a PO line and its order stand in the same relation.
    """
    if scope is CostScope.CONTAINER:
        shipment_id = db.scalar(
            select(Container.shipment_id).where(Container.id == target_id, Container.org_id == org_id)
        )
        if shipment_id is None:
            return Cost.container_id == target_id
        return or_(Cost.container_id == target_id, Cost.shipment_id == shipment_id)
    if scope is CostScope.SHIPMENT:
        boxes = select(Container.id).where(Container.shipment_id == target_id, Container.org_id == org_id)
        return or_(Cost.shipment_id == target_id, Cost.container_id.in_(boxes))
    if scope is CostScope.PO:
        po_lines = select(PurchaseOrderLine.id).where(
            PurchaseOrderLine.po_id == target_id, PurchaseOrderLine.org_id == org_id
        )
        return or_(Cost.po_id == target_id, Cost.po_line_id.in_(po_lines))
    if scope is CostScope.PO_LINE:
        po_id = db.scalar(
            select(PurchaseOrderLine.po_id).where(
                PurchaseOrderLine.id == target_id, PurchaseOrderLine.org_id == org_id
            )
        )
        if po_id is None:
            return Cost.po_line_id == target_id
        return or_(Cost.po_line_id == target_id, Cost.po_id == po_id)
    return None


def duplicate_cost_note(
    db: Session,
    org_id: UUID,
    cost_type: CostType | None,
    scope: CostScope | None,
    target_id: UUID | None,
) -> str | None:
    """Machine-readable note when an invoiced cost of this type already covers the same freight.

    Run at extraction and again at confirmation: a week can pass between the two, and the cost the
    logistics manager keyed in by hand on Monday did not exist when the invoice was read on Friday.
    """
    if cost_type is None or scope is None or target_id is None:
        return None
    where = _same_freight(db, org_id, scope, target_id)
    if where is None:
        return None
    existing = db.scalars(
        select(Cost)
        .where(
            Cost.org_id == org_id,
            Cost.status == CostStatus.ACTUAL,
            Cost.closed_at.is_(None),
            Cost.cost_type == cost_type,
            where,
        )
        .order_by(Cost.cost_date.desc())
        .limit(1)
    ).first()
    if existing is None:
        return None
    return (
        f"@duplicate_cost|{cost_type.value}|{note_field(existing.vendor)}|"
        f"{note_field(existing.invoice_number)}|{existing.amount_base}|{existing.currency}|"
        f"{existing.scope.value}"
    )


def invoice_recorded(
    db: Session,
    org_id: UUID,
    vendor: str | None,
    invoice_number: str | None,
    freight: list[tuple[CostScope, UUID]],
) -> list[Cost]:
    """The costs that already carry this invoice, whatever door it came in by: the same number, as it
    compares (`normalize_invoice_number`), from the same forwarder — or, when either copy names no
    forwarder, on the same freight. A short number like "1234" is in every forwarder's range; alone,
    it proves nothing.

    Not a charge that resembles another (that is `duplicate_cost_note`, which a person may overrule),
    but the same document entered twice: a statement imported on Monday and its PDF confirmed on
    Tuesday count every line of it twice, including the lines of other types the statement lumped
    together.
    """
    number = normalize_invoice_number(invoice_number)
    if not number:
        return []
    same_number = select(Cost).where(
        Cost.org_id == org_id,
        Cost.status == CostStatus.ACTUAL,
        Cost.closed_at.is_(None),
        Cost.invoice_number.is_not(None),
        func.regexp_replace(func.upper(Cost.invoice_number), "[^A-Z0-9]", "", "g") == number,
    )
    wanted = normalize_vendor(vendor)
    places = [
        where for scope, target in freight if (where := _same_freight(db, org_id, scope, target)) is not None
    ]
    found: list[Cost] = []
    for cost in db.scalars(same_number.order_by(Cost.created_at)):
        theirs = normalize_vendor(cost.vendor)
        if wanted and theirs:
            if wanted == theirs:
                found.append(cost)
        elif places and db.scalar(select(Cost.id).where(Cost.id == cost.id, or_(*places))) is not None:
            found.append(cost)
    return found


def invoice_line_recorded(
    db: Session,
    org_id: UUID,
    *,
    vendor: str | None,
    invoice_number: str | None,
    cost_type: CostType,
    currency: str,
    amount: Decimal,
    scope: CostScope,
    target_id: UUID,
    exclude: UUID | None = None,
) -> list[Cost]:
    """The live invoiced costs that are this very line already: the same invoice (`same_invoice`), the
    same type and currency — on the same target, or for the same amount at the other end of the same
    freight (the box and the bill of lading it travelled under, an order and its lines). What a person
    keys in twice by hand, under two spellings, counts twice in the landed cost; this is where it is
    stopped. A second line of one invoice is not: the same type in another currency, a surcharge on
    the bill of lading next to the box's freight — which is how a confirmed invoice writes them."""
    number = normalize_invoice_number(invoice_number)
    where = _same_freight(db, org_id, scope, target_id)
    if not number or where is None:
        return []
    target = func.coalesce(Cost.container_id, Cost.shipment_id, Cost.po_id, Cost.po_line_id)
    candidates = db.scalars(
        select(Cost).where(
            Cost.org_id == org_id,
            Cost.status == CostStatus.ACTUAL,
            Cost.closed_at.is_(None),
            Cost.cost_type == cost_type,
            Cost.currency == currency,
            Cost.invoice_number.is_not(None),
            func.regexp_replace(func.upper(Cost.invoice_number), "[^A-Z0-9]", "", "g") == number,
            where,
            or_(target == target_id, Cost.amount == amount),
            *([Cost.id != exclude] if exclude is not None else []),
        )
    )
    return [c for c in candidates if same_invoice(vendor, invoice_number, c.vendor, c.invoice_number)]


def same_line_on_file(
    db: Session,
    org_id: UUID,
    *,
    vendor: str | None,
    invoice_number: str | None,
    cost_type: CostType,
    currency: str,
    target_id: UUID,
    exclude: UUID | None = None,
) -> list[Cost]:
    """The costs `uq_costs_invoice_line` would refuse this one next to: the same forwarder and number
    to the letter, the same type, currency and target, whatever their status. Asked before writing,
    so that what the database refuses is a refusal with the line on file named — never a server
    error, and never an override a person is offered and cannot use."""
    if vendor is None or invoice_number is None:
        # The index leaves unnumbered costs alone, and a missing vendor never equals another. An empty
        # string is not missing to it: "" is a value, and two of them are the same value.
        return []
    target = func.coalesce(Cost.container_id, Cost.shipment_id, Cost.po_id, Cost.po_line_id)
    return list(
        db.scalars(
            select(Cost).where(
                Cost.org_id == org_id,
                Cost.vendor == vendor,
                Cost.invoice_number == invoice_number,
                Cost.cost_type == cost_type,
                Cost.currency == currency,
                target == target_id,
                *([Cost.id != exclude] if exclude is not None else []),
            )
        )
    )


def note_field(text: str | None) -> str:
    """A free text as one field of an `@code|…` note: a "|" in a forwarder's name would shift every
    field after it."""
    return (text or "").replace("|", "/")


def recorded_note(cost: Cost) -> str:
    """Machine-readable description of a cost that already carries an invoice, for the screen to
    write its sentence from: type, forwarder, number, amount in base currency, scope, day entered, and
    its status — an estimate carrying the number is not an invoice already recorded."""
    return (
        f"@recorded|{cost.cost_type.value}|{note_field(cost.vendor)}|{note_field(cost.invoice_number)}|"
        f"{cost.amount_base}|{cost.scope.value}|{cost.created_at.date().isoformat()}|{cost.status.value}"
    )


def check(
    db: Session,
    fx: FxService,
    invoice: FreightInvoice,
    context: ExtractionContext,
    *,
    org_id: UUID,
    base_currency: str,
    invoice_date: date | None,
    default_container_id: UUID | None = None,
) -> CheckedInvoice:
    """Turn a reading into reviewable lines, saying on each one what a person should look at."""
    holds, difference = arithmetic_holds(invoice)
    confidence = invoice.confidence if holds else min(invoice.confidence, CAPPED_CONFIDENCE)
    notes: list[str] = list(invoice.notes)
    if not holds and difference is not None:
        direction = "more_than" if difference > 0 else "less_than"
        notes.append(f"@arithmetic_mismatch|{abs(difference)}|{direction}")

    # No total and no pre-tax total could be read: the check above had nothing to check against, and
    # "it adds up" would be said of a reading nobody verified. A missed line is then invisible —
    # which is the one failure this pipeline must not have — so the reading says it is unverified.
    if invoice.lines and invoice.total is None and invoice.subtotal is None:
        notes.append(UNVERIFIED_TOTAL_NOTE)
        confidence = min(confidence, CAPPED_CONFIDENCE)

    # A line the reading could not turn into money is a line nobody will ever see again, and the
    # arithmetic above counted it. Both facts belong on the screen rather than in a log.
    priced: list[tuple[ExtractedLine, Decimal]] = []
    for line in invoice.lines:
        amount = line.decimal()
        if amount is None or amount == 0:
            notes.append(f"@dropped_line|{note_field(line.description[:60])}|{line.amount}")
            confidence = min(confidence, CAPPED_CONFIDENCE)
        else:
            priced.append((line, amount))

    # A refund read as a charge is the most expensive mistake this pipeline can make: the amount
    # lands on the container twice over, once as money never received and once as money never spent,
    # and the arithmetic holds perfectly throughout because every figure is consistently wrong. The
    # totals decide first — a document whose bottom line is negative is a credit note whatever it is
    # headed — then an invoice on which nothing is charged at all.
    if _negative(invoice.total) or _negative(invoice.subtotal):
        notes.append(f"{CREDIT_NOTE_NOTE}|negative_total")
    elif priced and all(amount < 0 for _, amount in priced):
        notes.append(f"{CREDIT_NOTE_NOTE}|negative_lines")
    if any(note.startswith(CREDIT_NOTE_NOTE) for note in notes):
        confidence = min(confidence, CAPPED_CONFIDENCE)

    # When the invoice names exactly one container we know, every charge on it is about that box.
    # This is the ordinary case — a forwarder invoices per container — and it is deterministic, so
    # it is decided here rather than left to whatever read the document.
    known = [n for n in {c.upper() for c in invoice.container_numbers} if n in context.containers]
    single_container = known[0] if len(known) == 1 else None
    default_number = next(
        (number for number, id_ in context.containers.items() if id_ == default_container_id), None
    )

    checked: list[CheckedLine] = []
    for line, amount in priced:
        currency = (line.currency or invoice.currency or base_currency).upper()
        line_notes: list[str] = []
        line_confidence = min(line.confidence, confidence)
        if amount < 0:
            # A commercial discount is a legitimate negative charge, and dropping it — which is what
            # used to happen — overstates the landed cost by exactly its amount while the arithmetic
            # still ticks. It stays visible and signed; what it cannot do is become a cost.
            line_notes.append(f"@negative_line|{amount}")
            line_confidence = min(line_confidence, CAPPED_CONFIDENCE)

        scope: CostScope | None = None
        target_id = None
        if line.container_number:
            container_id = context.containers.get(line.container_number.upper())
            if container_id is not None:
                scope, target_id = CostScope.CONTAINER, container_id
            else:
                # The model was given the list of this organization's containers and answered with
                # something else. It is dropped, and said out loud rather than silently.
                line_notes.append(f"@unknown_container|{line.container_number.upper()}")
                line_confidence = min(line_confidence, CAPPED_CONFIDENCE)
        elif line.po_number:
            po_id = context.purchase_orders.get(line.po_number.upper())
            if po_id is not None:
                scope, target_id = CostScope.PO, po_id
        elif single_container is not None:
            scope, target_id = CostScope.CONTAINER, context.containers[single_container]
            line_notes.append(f"@single_container|{single_container}")
        elif default_number is not None and default_container_id is not None:
            # The document names no container we know, and whoever dropped it said which one it is
            # about. Said on the line: it is the uploader's word, not something read on the invoice.
            scope, target_id = CostScope.CONTAINER, default_container_id
            line_notes.append(f"@default_container|{default_number}")

        if not _rate_available(db, fx, base_currency, currency, invoice_date):
            line_notes.append(f"@no_fx_rate|{currency}|{invoice_date.isoformat() if invoice_date else ''}")
            line_confidence = min(line_confidence, CAPPED_CONFIDENCE)

        duplicate = duplicate_cost_note(db, org_id, line.cost_type, scope, target_id)
        if duplicate:
            line_notes.append(duplicate)
            line_confidence = min(line_confidence, CAPPED_CONFIDENCE)

        checked.append(
            CheckedLine(
                description=line.description[:500],
                amount=amount,
                currency=currency,
                cost_type=line.cost_type,
                scope=scope,
                target_id=target_id,
                confidence=round(line_confidence, 2),
                notes=line_notes,
            )
        )

    if len(invoice.container_numbers) > 1:
        notes.append("@multi_container")
    return CheckedInvoice(invoice=invoice, lines=checked, confidence=round(confidence, 2), notes=notes)


def _negative(stated: str | None) -> bool:
    """Whether a total the document states is a figure below zero. An unreadable one is not."""
    if stated is None:
        return False
    try:
        return Decimal(stated) < 0
    except ArithmeticError:  # pragma: no cover - the model validated it
        return False


def _rate_available(db: Session, fx: FxService, base: str, quote: str, on_date: date | None) -> bool:
    if quote == base or on_date is None:
        return True
    try:
        fx.resolve(db, base, quote, on_date)
    except Exception:
        return False
    return True
