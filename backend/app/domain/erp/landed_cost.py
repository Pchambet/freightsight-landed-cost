"""Writing our landed cost back into the customer's ERP, under human control.

Two rules, and they are the ones that make a write-back safe to offer at all:

  * **The preview writes nothing.** It builds exactly what would be created and lists what stands in
    the way, so the person clicking sees the object before it exists.
  * **What we create is a draft.** Validating a landed cost posts accounting entries in someone
    else's books; that decision belongs to whoever owns them, in their own screen.

The amounts are ours, per receipt line, because that is the entire point of the integration: Odoo is
told what each line costs rather than asked to guess a split it has no basis for.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Unprocessable
from app.core.money import q2
from app.domain.costing.engine import EXCLUDED_FROM_LANDED
from app.domain.costing.service import Computation, compute
from app.domain.erp.ports import ErpCostLine, ErpDocument, ErpReceipt, ErpReceiptLine, ErpWriter
from app.domain.labels import COST_TYPE_FR
from app.domain.models import (
    Container,
    ContainerLoad,
    Cost,
    CostStatus,
    ErpConnection,
    ErpPush,
    Organization,
    PurchaseOrderLine,
)

logger = logging.getLogger(__name__)

#: Why a container cannot be pushed. Each one is a sentence a person can act on.
NO_CONNECTION = "no_erp_connection"
NO_COSTS = "no_actual_costs"
NO_RECEIPT = "no_receipt"
MULTIPLE_RECEIPTS = "multiple_receipts"
UNMATCHED_LINE = "unmatched_line"
AMBIGUOUS_LINE = "ambiguous_line"
NOT_VALUED = "not_valued"
ALREADY_PUSHED = "already_pushed"
PUSHED_COST_CHANGED = "pushed_cost_changed"
PUSHED_COST_GONE = "pushed_cost_gone"
CURRENCY_MISMATCH = "erp_currency_mismatch"
ADJUSTMENT_UNPLACED = "adjustment_unplaced"

#: In a forgotten push's snapshot of a cost: what a person said they entered by hand in the ERP on
#: top of what the document holds, cumulative and signed.
ENTERED_BY_HAND = "entered_by_hand"
TAKE_BACK_NO_GOODS = "take_back_no_goods"

#: Where a document that is no longer a draft has got to. Both mean the same thing for us: it is
#: posted or voided in someone's accounts and it will not be deleted to suit us.
FINAL_DOCUMENT_STATES = ("done", "cancel")
#: What `forget` records for a push whose document was already gone from the ERP.
GONE_STATE = "gone"
#: Where a document we let go of holds nothing in the customer's books: a cancelled one posted
#: nothing, and a deleted draft is gone. Every other state holds what it carried — a validated one
#: has posted it into the stock valuation.
HOLDS_NOTHING = ("cancel", GONE_STATE)

#: Appended to what a correction document says, in the organisation's language like the rest.
_ADJUSTMENT = {"fr": "ajustement"}

#: The first key of the advisory lock that makes the writes into the ERP for one container take turns.
#: The second is the container, so that two containers never wait for each other.
_ERP_LOCK = 0x46534550  # "FSEP"


def lock_container(db: Session, container_id: UUID) -> None:
    """Take this container's turn at the ERP for this transaction. A push plans from the pushes on
    file, then writes into the ERP: two at once each plan without the other's document, and the same
    charge — or the same take-back — lands twice in someone else's books. A forget reads the push it
    lets go of, which another one may be letting go of too. Released at commit or rollback."""
    key = int.from_bytes(container_id.bytes[:4], "big", signed=True)
    db.execute(text("SELECT pg_advisory_xact_lock(:space, :key)"), {"space": _ERP_LOCK, "key": key})


def marker_for(cost_ids: Iterable[UUID | str]) -> str:
    """A stable key for *this set of costs*, to recognise our own draft in the ERP.

    Not the container, and not the document's name: the same container is legitimately pushed again
    once another invoice lands, and that second push is a different document. What has to match is
    the set of costs, so the key is their sorted ids, hashed — short enough to sit in a note a human
    might read, and carrying no identifier out of our database into someone else's.
    """
    joined = "|".join(sorted(str(cost_id) for cost_id in cost_ids))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Blocker:
    """A reason, in English, plus its moving parts on the side.

    `message` is the sentence an API client gets and is not going to change shape. `params` carries
    the same values separately so a screen can build its own sentence in its own language instead of
    showing ours underneath a translated title. Every value is a string, already formatted for
    reading: a list is joined with ", " and a missing SKU is "", so the front decides what "no SKU"
    says rather than parsing our English out of it.
    """

    code: str
    message: str
    params: dict[str, str] = field(default_factory=dict)
    #: Whether this stops the push. False is a remark about what has already gone to the ERP — a
    #: document written from figures that have changed since — which is worth showing next to the
    #: container but has no business refusing tomorrow's demurrage invoice.
    blocking: bool = True


@dataclass
class PlannedLine:
    move_id: int
    product_name: str
    sku: str | None
    quantity: str
    amount: Decimal


@dataclass
class PlannedCost:
    label: str
    cost_ids: list[UUID]
    amount: Decimal
    lines: list[PlannedLine] = field(default_factory=list)
    #: Carried through so a second document on the same container gets a reference that tells it
    #: apart from the first. Not in the API response: it is already inside `label`.
    invoice_number: str | None = None
    #: The document that already carries this cost, if one does. Shown so the preview is the whole
    #: picture — what has gone and what has not — rather than only the remainder.
    pushed_as: str | None = None
    #: Its push, so a screen offering "forget this push" has something to name. The document's own
    #: name does not identify it: three drafts on one test instance are called the same thing.
    pushed_push_id: UUID | None = None
    #: The validated document this one corrects, when what would go is a difference rather than a
    #: whole cost. Not in the API response: the label says it.
    adjusts: str | None = None
    #: A difference from a validated document that cannot be written for want of its split per
    #: receipt line, so a person enters it in the ERP. The cost then goes nowhere from here.
    by_hand: ByHand | None = None

    @property
    def pending(self) -> bool:
        return self.pushed_push_id is None and self.by_hand is None


@dataclass(frozen=True)
class ByHand:
    """What a validated document holds of a cost, against which our figure is now different."""

    existing: str
    held: Decimal
    difference: Decimal


@dataclass
class Plan:
    container_id: UUID
    container_number: str
    receipt: ErpReceipt | None
    costs: list[PlannedCost]
    blockers: list[Blocker]
    total: Decimal
    existing_push: ErpPush | None = None

    @property
    def pending(self) -> list[PlannedCost]:
        """What this push would carry: a landed cost adds to the stock valuation rather than
        replacing it, so a cost a document already holds is never sent a second time."""
        return [cost for cost in self.costs if cost.pending]

    @property
    def hard_blockers(self) -> list[Blocker]:
        """The ones that really stand in the way of writing this document."""
        return [blocker for blocker in self.blockers if blocker.blocking]

    @property
    def pushable(self) -> bool:
        return not self.hard_blockers and bool(self.pending) and self.receipt is not None


def _allocations(comp: Computation, container_id: UUID) -> dict[UUID, dict[UUID, Decimal]]:
    """`cost id -> {load id -> amount}` for the actual costs that reached this container."""
    out: dict[UUID, dict[UUID, Decimal]] = {}
    for allocation in comp.result.allocations:
        load = comp.load_rows.get(allocation.load_id)
        cost = comp.cost_rows.get(allocation.cost_id)
        if load is None or cost is None or load.container_id != container_id:
            continue
        if cost.status is not CostStatus.ACTUAL:
            continue  # an estimate is ours to hold, not theirs to book
        if cost.cost_type.value in EXCLUDED_FROM_LANDED:
            # Recoverable VAT is allocated for the cash view and excluded from the landed cost —
            # the engine's own list, so the two can never drift apart. Pushing it would inflate
            # the customer's stock valuation by a tax they get back.
            continue
        out.setdefault(cost.id, {})[allocation.load_id] = allocation.amount_base
    return out


def plan(
    db: Session,
    org: Organization,
    container: Container,
    writer: ErpWriter | None,
    connection: ErpConnection | None,
) -> Plan:
    """Build what would be written, and everything that stands in the way. Writes nothing."""
    blockers: list[Blocker] = []
    comp = compute(db, org)
    by_cost = _allocations(comp, container.id)

    if connection is None or writer is None:
        blockers.append(
            Blocker(NO_CONNECTION, "This organization has no ERP connection to push anything to.")
        )
    booked = _booked_costs(db, org, container, writer)
    carried = _carried_costs(db, org, container)
    waiting = _unallocatable_here(comp, container)
    # Held by a validated document we let go of, and no longer counted here at all (deleted, closed,
    # moved, re-entered under a new id when its invoice was corrected): their books still carry it,
    # so it is taken back. Not a cost the engine cannot allocate for now: that one is still ours.
    taken_back = [
        key
        for key, held in booked.items()
        if key not in carried and held.amount and UUID(key) not in by_cost and key not in waiting
    ]
    allocated = {load_id for allocations in by_cost.values() for load_id in allocations}
    load_ids = set(allocated)
    if taken_back and not load_ids:
        # What is taken back may be all that is left to write: the receipt is then found from the
        # container's goods. Only then — asking for every order in the box finds the receipts of
        # orders no cost is on, and several receipts refuse the whole push.
        load_ids |= {load_id for load_id, row in comp.load_rows.items() if row.container_id == container.id}
    po_numbers = sorted({comp.load_rows[load_id].po_line.purchase_order.po_number for load_id in load_ids})
    costs: list[PlannedCost] = []
    if taken_back and not po_numbers:
        costs = _no_goods_left(taken_back, booked, blockers)
    elif not by_cost and not po_numbers:
        blockers.append(
            Blocker(
                NO_COSTS,
                "No invoiced cost is allocated to this container yet. Estimates are not pushed: "
                "they are ours to hold, not the accounts' to carry.",
            )
        )

    receipts = writer.find_receipts(po_numbers) if writer and po_numbers else []
    receipt = receipts[0] if receipts else None
    if writer and po_numbers and receipt is None:
        blockers.append(
            Blocker(
                NO_RECEIPT,
                f"No done receipt found in the ERP for {', '.join(po_numbers)}. A landed cost "
                "attaches to a receipt, so the goods have to have been received there first.",
                {"po_numbers": ", ".join(po_numbers)},
            )
        )
    if len(receipts) > 1:
        # Several receipts for one container is a real case (a partial delivery), and splitting our
        # costs across them is a decision nobody has made yet. Said out loud rather than guessed.
        blockers.append(
            Blocker(
                MULTIPLE_RECEIPTS,
                f"{len(receipts)} receipts match these orders ("
                + ", ".join(r.name for r in receipts)
                + "). Pushing to one of several receipts is not decided yet; do it by hand for now.",
                {"count": str(len(receipts)), "receipts": ", ".join(r.name for r in receipts)},
            )
        )
        receipt = None

    existing = _latest_live_push(db, org, container)
    blockers.extend(
        _drift_blockers(live_pushes(db, org, container), comp, by_cost, booked, waiting, org.locale)
    )

    if receipt is not None:
        blockers.extend(_currency_blockers(org, writer))
        moves = _moves_by_sku(receipt)
        paired = _pair_by_quantity(moves, [comp.load_rows[load_id] for load_id in allocated])
        unvalued = [line for line in receipt.lines if not line.valued]
        if unvalued:
            names = ", ".join(sorted({line.product_name for line in unvalued}))
            blockers.append(
                Blocker(
                    NOT_VALUED,
                    f"Odoo cannot value a landed cost for {names}: their product category is not in "
                    "automated valuation with a real cost method (FIFO or average). Odoo refuses "
                    "this at validation, so it is refused here instead.",
                    {"products": names},
                )
            )
        for cost_id, allocations in sorted(by_cost.items(), key=lambda item: str(item[0])):
            cost = comp.cost_rows[cost_id]
            total = q2(sum(allocations.values(), Decimal("0.00")))
            lines, unplaceable = _placed(allocations, comp, moves, paired, receipt)
            already = carried.get(str(cost_id))
            # Held by a validated document we let go of: their books keep it whatever we forget, so
            # only a difference from it is still to go.
            held = booked.get(str(cost_id)) if already is None else None
            settled = held is not None and _settled(held, total, lines, complete=not unplaceable)
            holder = already or (held.push if held is not None and settled else None)
            planned = PlannedCost(
                label=_label(cost, org.locale),
                cost_ids=[cost_id],
                amount=total,
                lines=lines,
                invoice_number=cost.invoice_number,
                pushed_as=_name(holder) if holder else None,
                pushed_push_id=holder.id if holder else None,
            )
            if held is not None and not settled:
                blockers.extend(_unplaced(planned, held, receipt))
            if planned.pending:
                # Only what this push would carry can block it. A cost that already went is
                # history; whether its line still matches today changes nothing.
                blockers.extend(unplaceable)
                if held is not None:
                    _adjust(planned, held, receipt, org.locale)
            costs.append(planned)
        for key in sorted(waiting & booked.keys() - carried.keys()):
            # Still this container's, with no figure here for now: their books keep it as it is.
            held = booked[key]
            if held.amount is not None:
                costs.append(
                    PlannedCost(
                        label=held.label,
                        cost_ids=[UUID(key)],
                        amount=held.amount,
                        pushed_as=_name(held.push),
                        pushed_push_id=held.push.id,
                    )
                )
        for key in sorted(taken_back):
            held = booked[key]
            planned = PlannedCost(label=held.label, cost_ids=[UUID(key)], amount=Decimal("0.00"))
            blockers.extend(_unplaced(planned, held, receipt))
            if planned.pending:
                _adjust(planned, held, receipt, org.locale)
            costs.append(planned)

    prepared = Plan(
        container_id=container.id,
        container_number=container.container_number,
        receipt=receipt,
        costs=costs,
        blockers=blockers,
        # Only what would go: a total counting what has already been written would be the amount of
        # a document nobody is about to create.
        total=q2(sum((cost.amount for cost in costs if cost.pending), Decimal("0.00"))),
        existing_push=existing,
    )
    if costs and not prepared.pending and not any(cost.by_hand for cost in costs):
        joined = ", ".join(sorted({cost.pushed_as for cost in costs if cost.pushed_as}))
        blockers.append(
            Blocker(
                ALREADY_PUSHED,
                f"Every invoiced cost on this container has already been pushed, as {joined}. "
                "There is nothing left to write; a new invoice on this container becomes a new "
                "document of its own.",
                {"existing": joined},
            )
        )
    return prepared


def _reference(container: Container, costs: list[PlannedCost], locale: str = "fr") -> str:
    """The document's name in the ERP.

    The container alone is no longer enough now that one container legitimately has several
    documents — a later invoice is a document of its own — and three drafts called the same thing
    tell an accountant nothing and make our own "already pushed as …" ambiguous. The invoices this
    one carries are what distinguish it.
    """
    parts = [f"FreightSight {container.container_number}"]
    invoices = sorted({cost.invoice_number for cost in costs if cost.invoice_number})
    if invoices:
        parts.append(", ".join(invoices))
    if any(cost.adjusts for cost in costs):
        parts.append(_ADJUSTMENT.get(locale, "adjustment"))
    return " — ".join(parts)[:120]


def forget(
    db: Session,
    org: Organization,
    push_row: ErpPush,
    writer: ErpWriter | None,
    *,
    actor_user_id: UUID | None = None,
    reason: str | None = None,
) -> tuple[ErpPush, ErpDocument | None]:
    """Stop a push from holding its costs, so they can be pushed again. Never deletes the row.

    While the document is a **draft**, only once it is really gone from the ERP: forgetting a push
    whose draft is still there would let the same charge be written into the customer's books a
    second time, which is the accident this whole mechanism exists to prevent.

    Once it is validated or cancelled, that rule became a dead end. A validated landed cost has
    posted accounting entries and Odoo will not delete it, so "delete it there, forget it here, push
    again" is an instruction nobody can follow — and the container stayed blocked for every later
    invoice. So it is allowed, against a written reason: the costs are in their books, and the
    correction is a new adjustment document, not a rewrite of that one. The state it was found in is
    kept on the row for that reason: a validated document goes on holding what it carried, so the
    next push carries only what differs from it (see `_booked_costs`), never the whole amount again.

    Returns the document as it was found, so the caller can put its state in the audit trail: what
    was forgotten, and what it was.
    """
    lock_container(db, push_row.container_id)
    db.refresh(push_row)  # as the push that held the turn before this one left it
    if push_row.forgotten_at is not None:
        raise Conflict("This push has already been forgotten.", code="ERP_PUSH_ALREADY_FORGOTTEN")
    if writer is None:
        raise Unprocessable(
            "The ERP connection is gone, so we cannot check whether the document is still there. "
            "Reconnect the ERP and try again: forgetting a push whose document still exists would "
            "let the same costs be charged to the goods twice.",
            code="ERP_NO_CONNECTION",
        )
    document = writer.read_landed_cost(push_row.odoo_id)
    if document is not None:
        name = document.name or str(document.record_id)
        details = {"id": document.record_id, "name": document.name, "state": document.state}
        if document.state not in FINAL_DOCUMENT_STATES:
            raise Conflict(
                f"{name} is still in the ERP, in {document.state}. "
                "Delete it there first: while it exists it carries these costs, and forgetting the "
                "push would let them be written a second time.",
                code="ERP_DOCUMENT_STILL_THERE",
                document=details,
            )
        if not (reason or "").strip():
            raise Unprocessable(
                f"{name} is {document.state} in the ERP: it cannot be deleted there, so its costs "
                "stay in those books and a correction goes through a new adjustment document. "
                "Forgetting it here is a decision that has to carry a reason.",
                code="ERP_FORGET_REASON_REQUIRED",
                document=details,
            )
        push_row.status = document.state
    else:
        push_row.status = GONE_STATE
    push_row.forgotten_at = datetime.now(UTC)
    push_row.forgotten_reason = reason
    push_row.forgotten_by = actor_user_id
    db.flush()
    logger.info(
        "forgot an erp push",
        extra={
            "push_id": str(push_row.id),
            "odoo_id": push_row.odoo_id,
            "org_id": str(org.id),
            "document_state": document.state if document else None,
        },
    )
    return push_row, document


def _drift_blockers(
    pushes: list[ErpPush],
    comp: Computation,
    by_cost: dict[UUID, dict[UUID, Decimal]],
    booked: dict[str, Booked],
    waiting: set[str],
    locale: str = "fr",
) -> list[Blocker]:
    """What has changed on our side since a document was written, and now differs from it.

    A draft in the ERP is a copy of a moment. A cost edited afterwards (amount, rate, method, the
    loads it spreads over) or deleted leaves that copy carrying a figure nobody can see from here.
    So it is said, and it says what to do: a draft is deleted over there, the push forgotten here and
    pushed again; a validated document is forgotten with a reason, and the next push carries only the
    difference — steps a person can check.

    Said, and not refused. These are remarks about costs a document already carries, and a carried
    cost is never sent again anyway; blocking on them stopped *unrelated* work — the demurrage
    invoice that arrives the week after — for a drift its own push has nothing to do with.

    A push that corrects a validated document carries a difference, so what it is compared against
    is the two together: what their books hold of the cost now.
    """
    out: list[Blocker] = []
    for push_row in pushes:
        name = push_row.odoo_name or str(push_row.odoo_id)
        gone: list[str] = []
        changed: list[str] = []
        for cost_id in push_row.cost_ids:
            try:
                key = UUID(cost_id)
            except ValueError:  # pragma: no cover - our own rows
                continue
            snapshot = (push_row.carried or {}).get(cost_id) or {}
            recorded = snapshot.get("amount")
            held = booked.get(cost_id)
            base = held.amount if held is not None else Decimal("0.00")
            # None when written before amounts were kept: nothing to compare against.
            expected = Decimal(recorded) + base if recorded is not None and base is not None else None
            allocations = by_cost.get(key)
            if allocations is None:
                if expected == 0 or cost_id in waiting:
                    # Taken back, which is what it was written for; or still here with no figure
                    # for now, which is neither gone nor changed.
                    continue
                cost = comp.cost_rows.get(key)
                gone.append(
                    _label(cost, locale) if cost is not None else snapshot.get("label") or cost_id[:8]
                )
                continue
            if expected is None:
                continue
            current = q2(sum(allocations.values(), Decimal("0.00")))
            if current != expected:
                changed.append(f"{_label(comp.cost_rows[key], locale)}: {expected:.2f} → {current:.2f}")
        if gone:
            out.append(
                Blocker(
                    PUSHED_COST_GONE,
                    f"{name} carries {', '.join(gone)}, which no longer counts on this container "
                    "(deleted, closed or moved). The document still holds it. Still a draft: delete "
                    "it in the ERP, forget the push here, then push again. Already validated: forget "
                    "the push here with a reason, then push again; it is taken back in an adjustment.",
                    {"existing": name, "costs": ", ".join(gone), "count": str(len(gone))},
                    blocking=False,
                )
            )
        if changed:
            out.append(
                Blocker(
                    PUSHED_COST_CHANGED,
                    f"{name} was written with amounts that have changed since ({'; '.join(changed)}). "
                    "It still carries the old figures. Still a draft: delete it in the ERP, forget the "
                    "push here, then push again. Already validated: forget the push here with a "
                    "reason, then push again; only the difference goes, as an adjustment.",
                    {"existing": name, "changes": "; ".join(changed), "count": str(len(changed))},
                    blocking=False,
                )
            )
    return out


def _label(cost: Cost, locale: str) -> str:
    """What the accountant reads on the landed-cost line, in the organisation's language: these
    words land in the customer's own Odoo, and a French importer's books are in French."""
    if locale == "fr":
        parts = [COST_TYPE_FR.get(cost.cost_type.value, cost.cost_type.value)]
        if cost.invoice_number:
            parts.append(f"facture {cost.invoice_number}")
    else:
        parts = [cost.cost_type.value.replace("_", " ").title()]
        if cost.invoice_number:
            parts.append(f"invoice {cost.invoice_number}")
    if cost.vendor:
        parts.append(cost.vendor)
    return " — ".join(parts)


def _moves_by_sku(receipt: ErpReceipt) -> dict[str, list[ErpReceiptLine]]:
    """Receipt lines grouped by SKU — all of them, never the first one.

    The SKU is the only identifier both systems agree on: our order lines come in through the CSV
    import, which carries no Odoo line id, so `ErpReceiptLine.po_line_id` can tell two moves apart
    but cannot say which of ours each belongs to. Keeping the whole group is what lets the case
    where it does not settle anything be seen, and refused, instead of resolved to whichever move
    happened to come back first.
    """
    out: dict[str, list[ErpReceiptLine]] = {}
    for line in receipt.lines:
        if line.sku:
            out.setdefault(line.sku, []).append(line)
    return out


def _candidate_moves(moves: dict[str, list[ErpReceiptLine]], line: PurchaseOrderLine) -> list[ErpReceiptLine]:
    """The receipt lines this order line could be. One is a match; anything else is a question."""
    candidates = moves.get(line.sku or "", [])
    # The same SKU on two purchase orders is two different receipt lines, and each move says which
    # order it came from. That settles the common half of the ambiguity without guessing.
    same_order = [move for move in candidates if move.po_number == line.purchase_order.po_number]
    if same_order:
        return same_order
    # A move that names another order is that order's goods, even when it is the only one of the SKU:
    # taken as ours, our charge would land on them. Only a move that names no order can be ours, and
    # beside another order's lot it may be that order's second lot as much as ours: still a question.
    if all(move.po_number is not None for move in candidates):
        return []
    return candidates


def _pair_by_quantity(
    moves: dict[str, list[ErpReceiptLine]], loads: list[ContainerLoad]
) -> dict[UUID, ErpReceiptLine]:
    """`load id -> its move`, for the loads whose SKU sits on several moves that quantities settle.

    The same product received in two lots is two moves of one SKU, and nothing we hold names the
    Odoo line. What each lot does carry is its quantity: a load of 400 pieces is the move of 400 when
    that is the only move of 400 and no other load of the group is 400 as well. Anything short of
    that one-to-one fit is left out, so it is refused as ambiguous rather than placed by rank.
    """
    groups: dict[tuple[str, str], list[ContainerLoad]] = {}
    for load in loads:
        line = load.po_line
        groups.setdefault((line.sku or "", line.purchase_order.po_number), []).append(load)
    out: dict[UUID, ErpReceiptLine] = {}
    for group in groups.values():
        candidates = _candidate_moves(moves, group[0].po_line)
        if len(candidates) < 2:
            continue
        order = group[0].po_line.purchase_order.po_number
        for load in group:
            same_quantity = [
                move
                for move in candidates
                if move.po_number in (None, order) and Decimal(move.quantity) == load.quantity
            ]
            rivals = [other for other in group if other.quantity == load.quantity]
            if len(same_quantity) == 1 and len(rivals) == 1:
                out[load.id] = same_quantity[0]
    # Across groups too: a move with no order of its own is a candidate for every order of the SKU,
    # and two of them finding "their" move of 100 there would put both charges on the same goods. A
    # load placed on its order's only move claims that move as well.
    claims = Counter(move.move_id for move in out.values())
    for load in loads:
        if load.id not in out and len(single := _candidate_moves(moves, load.po_line)) == 1:
            claims[single[0].move_id] += 1
    return {load_id: move for load_id, move in out.items() if claims[move.move_id] == 1}


def _placed(
    allocations: dict[UUID, Decimal],
    comp: Computation,
    moves: dict[str, list[ErpReceiptLine]],
    paired: dict[UUID, ErpReceiptLine],
    receipt: ErpReceipt,
) -> tuple[list[PlannedLine], list[Blocker]]:
    """A cost's amounts by receipt line, and why the order lines that fit no single line do not.

    By receipt line, not by order line: two of our order lines legitimately land on one move (the
    same SKU ordered twice, received once), and what Odoo takes is one amount per move. Keyed here so
    the second one adds to the first instead of replacing it.
    """
    by_move: dict[int, PlannedLine] = {}
    refused: list[Blocker] = []
    for load_id, amount in allocations.items():
        line = comp.load_rows[load_id].po_line
        candidates = [paired[load_id]] if load_id in paired else _candidate_moves(moves, line)
        if len(candidates) != 1:
            refused.append(_no_single_move(line, candidates, receipt))
            continue
        move = candidates[0]
        planned_line = by_move.get(move.move_id)
        if planned_line is None:
            by_move[move.move_id] = PlannedLine(
                move_id=move.move_id,
                product_name=move.product_name,
                sku=move.sku,
                quantity=move.quantity,
                amount=q2(amount),
            )
        else:
            planned_line.amount = q2(planned_line.amount + amount)
    return list(by_move.values()), refused


def _settled(held: Booked, total: Decimal, lines: list[PlannedLine], *, complete: bool) -> bool:
    """Whether their books already hold this cost as we count it now.

    The total, and the amount on each receipt line when that split was recorded and ours can be
    placed: a change that moves money from one lot to another — a corrected weight, another method —
    leaves the total where it was and each lot's stock valuation wrong.
    """
    if held.amount is None:
        return True  # written before amounts were kept: nothing to compare against
    if held.amount != total:
        return False
    if held.lines is None or not complete:
        return True
    ours = {line.move_id: line.amount for line in lines if line.amount}
    return ours == {move_id: amount for move_id, amount in held.lines.items() if amount}


def _unallocatable_here(comp: Computation, container: Container) -> set[str]:
    """The invoiced costs the engine cannot allocate at all for now whose scope still takes in this
    container — a split by value stops at one order without an exchange rate.

    Such a cost has not left the container. What a validated document holds of it is not taken back:
    it stays held, and is compared with our figure again once there is one.
    """
    here = [row for row in comp.load_rows.values() if row.container_id == container.id]
    out: set[str] = set()
    for unallocated in comp.result.unallocated:
        cost = comp.cost_rows.get(unallocated.cost_id)
        if cost is None or cost.status is not CostStatus.ACTUAL:
            continue
        if cost.container_id is not None:
            meant = cost.container_id == container.id
        elif cost.shipment_id is not None:
            meant = cost.shipment_id == container.shipment_id
        elif cost.po_id is not None:
            meant = any(row.po_line.po_id == cost.po_id for row in here)
        else:
            meant = any(row.po_line_id == cost.po_line_id for row in here)
        if meant:
            out.add(str(cost.id))
    return out


def _no_single_move(
    line: PurchaseOrderLine, candidates: list[ErpReceiptLine], receipt: ErpReceipt
) -> Blocker:
    """Why this order line cannot be placed on the receipt: nothing matches, or too much does."""
    params = {
        "po_number": line.purchase_order.po_number,
        "line_no": str(line.line_no),
        "sku": line.sku or "",
        "receipt": receipt.name,
    }
    if not candidates:
        return Blocker(
            UNMATCHED_LINE,
            f"{line.purchase_order.po_number} line {line.line_no} ({line.sku or 'no SKU'}) has no "
            f"matching line on receipt {receipt.name}. Costs are pushed per line, so every line has "
            "to match.",
            params,
        )
    moves = ", ".join(str(move.move_id) for move in candidates)
    return Blocker(
        AMBIGUOUS_LINE,
        f"{line.purchase_order.po_number} line {line.line_no} ({line.sku or 'no SKU'}) matches "
        f"{len(candidates)} lines of receipt {receipt.name} (moves {moves}). The same product "
        "received in several lots has several lines there, and their quantities do not say which one "
        "this order line is: splitting it by guesswork would put the cost on the wrong goods, so it "
        "is refused. Push this container by hand, or receive the order on a single line.",
        {**params, "count": str(len(candidates)), "moves": moves},
    )


def _currency_blockers(org: Organization, writer: ErpWriter | None) -> list[Blocker]:
    """Refuse to write euros into a company that keeps its books in something else.

    We push a bare number into `price_unit`; the ERP reads it in its company's currency. Nothing in
    the document records ours, so a mismatch is not an error anywhere — it is a stock valuation that
    is wrong by the exchange rate, in the customer's accounts, for good.
    """
    if writer is None:
        return []
    currency = writer.company_currency()
    if not currency or currency.upper() == org.base_currency.upper():
        return []
    return [
        Blocker(
            CURRENCY_MISMATCH,
            f"The ERP company keeps its books in {currency} and this organisation works in "
            f"{org.base_currency}. Our amounts would be read as {currency} and the stock valuation "
            "would be wrong by the exchange rate, so nothing is written.",
            {"erp_currency": currency, "base_currency": org.base_currency},
        )
    ]


def live_pushes(db: Session, org: Organization, container: Container) -> list[ErpPush]:
    """The pushes on this container that still hold their costs, newest first."""
    return list(
        db.scalars(
            select(ErpPush)
            .where(
                ErpPush.org_id == org.id,
                ErpPush.container_id == container.id,
                ErpPush.forgotten_at.is_(None),
            )
            .order_by(ErpPush.created_at.desc())
        )
    )


def _carried_costs(db: Session, org: Organization, container: Container) -> dict[str, ErpPush]:
    """`cost id -> the document that already carries it`, oldest push winning a tie."""
    out: dict[str, ErpPush] = {}
    for push_row in reversed(live_pushes(db, org, container)):
        for cost_id in push_row.cost_ids:
            out[cost_id] = push_row
    return out


@dataclass
class Booked:
    """What validated documents we have let go of still hold of one cost, in the customer's books."""

    #: The latest of them, named when the cost is still wholly theirs.
    push: ErpPush
    #: What the cost was called when it was first written, for taking it back once it is gone here.
    label: str
    #: None when a push predates the amounts being kept: the cost then stays held as it is.
    amount: Decimal | None
    #: `move id -> amount`, None when a push predates the split being kept.
    lines: dict[int, Decimal] | None


def _booked_costs(
    db: Session, org: Organization, container: Container, writer: ErpWriter | None
) -> dict[str, Booked]:
    """`cost id -> what forgotten, validated documents hold of it`.

    Forgetting a validated landed cost frees the container, not the money: Odoo will not delete a
    posted document, so the amounts it carried stay in the stock valuation. Counting them as gone
    let the next push write the whole cost again on top of them — the goods charged twice. A
    cancelled document posted nothing and holds nothing, and a deleted draft is gone; neither is here.

    A push forgotten before its document's state was recorded still reads as it was written, which
    is how a deleted draft reads too. Taken as holding nothing, a validated one had its costs pushed
    again in full; so the ERP is asked, and until it can be, the push goes on holding what it carried.
    """
    out: dict[str, Booked] = {}
    rows = db.scalars(
        select(ErpPush)
        .where(
            ErpPush.org_id == org.id,
            ErpPush.container_id == container.id,
            ErpPush.forgotten_at.is_not(None),
            ErpPush.status.not_in(HOLDS_NOTHING),
        )
        .order_by(ErpPush.created_at)
    )
    for push_row in rows:
        if push_row.status not in FINAL_DOCUMENT_STATES and writer is not None:
            document = writer.read_landed_cost(push_row.odoo_id)
            push_row.status = document.state if document is not None else GONE_STATE
            if push_row.status in HOLDS_NOTHING:
                continue
        for cost_id in push_row.cost_ids:
            snapshot = (push_row.carried or {}).get(cost_id) or {}
            amount = Decimal(snapshot["amount"]) if snapshot.get("amount") is not None else None
            # What a person said they entered by hand on top of it is in their books too; where it
            # landed per receipt line nobody told us, so the split is no longer known either.
            entered = snapshot.get(ENTERED_BY_HAND)
            if entered is not None and amount is not None:
                amount += Decimal(entered)
            raw = snapshot.get("lines") if entered is None else None
            lines = (
                {int(move_id): Decimal(value) for move_id, value in raw.items()} if raw is not None else None
            )
            held = out.get(cost_id)
            if held is None:
                out[cost_id] = Booked(push_row, snapshot.get("label") or cost_id[:8], amount, lines)
                continue
            held.push = push_row
            held.amount = held.amount + amount if held.amount is not None and amount is not None else None
            if held.lines is None or lines is None:
                held.lines = None
            else:
                for move_id, value in lines.items():
                    held.lines[move_id] = held.lines.get(move_id, Decimal("0.00")) + value
    return out


def _unplaced(planned: PlannedCost, held: Booked, receipt: ErpReceipt) -> list[Blocker]:
    """Whether a difference from a validated document can be placed on the receipt's lines.

    It needs what each line already holds. When that split was never recorded, or its lines are not
    on this receipt any more, placing the difference would be a guess — so nothing is written for the
    cost, and the gap is said on it for a person to enter by hand rather than written onto the wrong
    goods. Not as carried: their books hold the old figure, not ours.
    """
    assert held.amount is not None
    if held.lines is not None and set(held.lines) <= {line.move_id for line in receipt.lines}:
        return []
    name = _name(held.push)
    change = f"{planned.label}: {held.amount:.2f} → {planned.amount:.2f}"
    planned.by_hand = ByHand(name, held.amount, q2(planned.amount - held.amount))
    return [
        Blocker(
            ADJUSTMENT_UNPLACED,
            f"{name} was validated with amounts that have changed since ({change}), and the split it "
            "was written with per receipt line is not known here. The difference cannot be placed on "
            "the right goods without it, so nothing is written for it: enter the adjustment by hand "
            "in the ERP.",
            {"existing": name, "changes": change},
            blocking=False,
        )
    ]


def _no_goods_left(
    taken_back: list[str], booked: dict[str, Booked], blockers: list[Blocker]
) -> list[PlannedCost]:
    """What is to be taken back when no goods are left in the container to find the receipt from.

    Nothing can be written: a landed cost attaches to a receipt. Left at that, the preview said only
    that no invoiced cost is here, while their books still hold the amount. So each one is named, on
    the cost and in a remark, for a person to take back by hand in the ERP.
    """
    out: list[PlannedCost] = []
    for key in sorted(taken_back):
        held = booked[key]
        assert held.amount is not None
        name = _name(held.push)
        planned = PlannedCost(label=held.label, cost_ids=[UUID(key)], amount=Decimal("0.00"))
        planned.by_hand = ByHand(name, held.amount, -held.amount)
        blockers.append(
            Blocker(
                TAKE_BACK_NO_GOODS,
                f"{name} still holds {held.amount:.2f} for {held.label}, which no longer counts on this "
                "container. No goods are left in it to find the receipt from, so nothing is written: "
                "take this amount back by hand in the ERP.",
                {"existing": name, "cost": held.label, "held": f"{held.amount:.2f}"},
                blocking=False,
            )
        )
        out.append(planned)
    return out


def _adjust(planned: PlannedCost, held: Booked, receipt: ErpReceipt, locale: str) -> None:
    """Turn a cost into its difference from what a validated document already holds of it.

    Per receipt line, not only in total: their books hold an amount on each move, and the correction
    has to leave each one at our figure. `_unplaced` has made sure that split is known.
    """
    assert held.amount is not None and held.lines is not None
    on_receipt = {line.move_id: line for line in receipt.lines}
    deltas = {line.move_id: line.amount for line in planned.lines}
    for move_id, amount in held.lines.items():
        deltas[move_id] = deltas.get(move_id, Decimal("0.00")) - amount
    planned.lines = [
        PlannedLine(
            move_id=move_id,
            product_name=on_receipt[move_id].product_name,
            sku=on_receipt[move_id].sku,
            quantity=on_receipt[move_id].quantity,
            amount=q2(delta),
        )
        for move_id, delta in sorted(deltas.items())
        if delta
    ]
    planned.amount = q2(planned.amount - held.amount)
    planned.label = f"{planned.label} — {_ADJUSTMENT.get(locale, 'adjustment')}"
    planned.adjusts = _name(held.push)


def _name(push_row: ErpPush) -> str:
    return push_row.odoo_name or str(push_row.odoo_id)


def _latest_live_push(db: Session, org: Organization, container: Container) -> ErpPush | None:
    """What a push with nothing new to say returns, so a double click is not an error."""
    pushes = live_pushes(db, org, container)
    return pushes[0] if pushes else None


def _cost_line(cost: PlannedCost) -> ErpCostLine:
    """One charge as the ERP takes it, with the split checked against its own total first.

    "The per-move amounts add up to the charge" is the contract with Odoo: it refuses to validate a
    document whose adjustments do not sum to their cost line, with a message that names no line. The
    accountant meets that refusal days later, in their own screen, with nothing to go on — so the
    invariant is checked here, before the call, where it can still be a sentence about our data.
    """
    per_move = {line.move_id: f"{line.amount:.2f}" for line in cost.lines}
    split = sum((Decimal(amount) for amount in per_move.values()), Decimal("0.00"))
    if split != cost.amount:
        raise Unprocessable(
            f"The split of {cost.label} adds up to {split:.2f} where the charge is "
            f"{cost.amount:.2f}. Nothing was written: a document Odoo cannot validate is worse than "
            "no document at all.",
            code="ERP_SPLIT_MISMATCH",
            params={"label": cost.label, "amount": f"{cost.amount:.2f}", "split": f"{split:.2f}"},
        )
    return ErpCostLine(
        label=cost.label,
        amount=f"{cost.amount:.2f}",
        per_move=per_move,
        cost_ids=[str(cost_id) for cost_id in cost.cost_ids],
    )


def enter_by_hand(
    db: Session,
    org: Organization,
    container: Container,
    writer: ErpWriter | None,
    connection: ErpConnection | None,
    *,
    cost_id: UUID,
    difference: Decimal,
) -> tuple[Plan, Booked]:
    """A person says they entered a cost's difference by hand in the ERP, as the screen asked.

    The difference could not be placed on the receipt's lines, so nothing we write will ever hold it;
    without this the next refund on the cost asks again for this one too. It must be the difference
    the screen shows now: a figure from a screen left open on an older one is refused, not recorded.
    """
    lock_container(db, container.id)
    current = plan(db, org, container, writer, connection)
    planned = next((c for c in current.costs if cost_id in c.cost_ids and c.by_hand is not None), None)
    if planned is None or planned.by_hand is None:
        raise Unprocessable(
            "Nothing is waiting to be entered by hand for this cost", code="ERP_BY_HAND_NOTHING_TO_ENTER"
        )
    if planned.by_hand.difference != q2(difference):
        raise Unprocessable(
            f"The difference to enter is now {planned.by_hand.difference:.2f}",
            code="ERP_BY_HAND_CHANGED",
            params={"difference": f"{planned.by_hand.difference:.2f}"},
        )
    held = _booked_costs(db, org, container, writer)[str(cost_id)]
    snapshot = dict((held.push.carried or {}).get(str(cost_id)) or {})
    snapshot[ENTERED_BY_HAND] = f"{q2(Decimal(snapshot.get(ENTERED_BY_HAND) or 0) + difference):.2f}"
    # A new dict, so the JSON column is seen as changed.
    held.push.carried = {**(held.push.carried or {}), str(cost_id): snapshot}
    db.flush()
    return current, held


def push(
    db: Session,
    org: Organization,
    container: Container,
    writer: ErpWriter,
    connection: ErpConnection,
    *,
    created_by: UUID | None = None,
    on_date: date | None = None,
) -> tuple[ErpPush, Plan]:
    """Write what no document carries yet into the ERP, as a draft.

    Only the costs that are still pending: a landed cost adds to the stock valuation rather than
    replacing it, so re-sending a cost an earlier document already holds charges the goods twice —
    in someone else's books, where we would not be the ones to notice. When nothing is pending the
    most recent push comes back instead of an error, so clicking twice is not a mistake — unless a
    difference is left to enter by hand: answered with that push, it would read as all in the ERP.
    """
    lock_container(db, container.id)
    prepared = plan(db, org, container, writer, connection)
    by_hand = any(cost.by_hand for cost in prepared.costs)
    if not prepared.pending and prepared.existing_push is not None and not by_hand:
        return prepared.existing_push, prepared
    if not prepared.pushable:
        # With nothing refused outright, what stands in the way is a difference to enter by hand.
        hard = prepared.hard_blockers or prepared.blockers
        raise Unprocessable(
            "This container cannot be pushed yet: " + "; ".join(b.message for b in hard),
            code="ERP_PUSH_BLOCKED",
            blockers=[{"code": b.code, "message": b.message, "params": b.params} for b in hard],
        )

    assert prepared.receipt is not None
    pending = prepared.pending
    cost_ids = sorted(str(cost_id) for cost in pending for cost_id in cost.cost_ids)
    result = writer.create_landed_cost(
        prepared.receipt.picking_id,
        [_cost_line(cost) for cost in pending],
        on_date=on_date or datetime.now(UTC).date(),
        reference=_reference(container, pending, org.locale),
        # A correction carries the same costs as the document it corrects; the amounts tell them apart.
        marker=marker_for(
            f"{cost_id}@{cost.amount:.2f}" if cost.adjusts else str(cost_id)
            for cost in pending
            for cost_id in cost.cost_ids
        ),
    )
    if result.adopted:
        # The ERP write of an earlier attempt survived; ours did not. Worth a line: it is the only
        # trace that anything went wrong, and the customer's books are unharmed either way.
        logger.info(
            "adopted an existing landed cost draft",
            extra={"odoo_id": result.record_id, "container_id": str(container.id)},
        )
    record = ErpPush(
        org_id=org.id,
        connection_id=connection.id,
        container_id=container.id,
        cost_ids=cost_ids,
        # The split per move too: once the document is validated, a correction has to know what
        # each receipt line already holds.
        carried={
            str(cost_id): {
                "amount": f"{cost.amount:.2f}",
                "label": cost.label,
                "lines": {str(line.move_id): f"{line.amount:.2f}" for line in cost.lines},
            }
            for cost in pending
            for cost_id in cost.cost_ids
        },
        odoo_model=result.model,
        odoo_id=result.record_id,
        odoo_name=result.name,
        status=result.state,
        # Prefixed, so it cannot be mistaken for a field Odoo sent us.
        response={**result.raw, "freightsight_adopted": result.adopted},
        created_by=created_by,
    )
    db.add(record)
    db.flush()
    return record, prepared
