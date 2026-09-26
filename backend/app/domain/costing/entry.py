"""How a cost comes in, whatever the door: typed by hand, confirmed from an invoice, estimated from the
rate cards, or read from a file.

One rule each, in one place, so that the same charge lands the same way whichever way it came. A duty
spread by customs value when it was confirmed from a PDF, and by theoretical duty when it was typed
in, is two different unit costs for one invoice — and the audit's margins per article would depend on
which button somebody pressed.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.core.money import to_base
from app.domain.costing import engine
from app.domain.costing.engine import DUTIABLE
from app.domain.costing.service import Computation, compute
from app.domain.fx.service import FxService
from app.domain.models import AllocationMethod, Cost, CostScope, CostStatus, CostType, Organization

#: How far the duty paid may sit from the duty the lines' own rates give before those rates are no
#: longer trusted to split it. A 0 % that an ERP writes on every product by default satisfies "every
#: line has a rate", and would load a container's whole duty onto its one rated line; this is where
#: that shows — the rates explain a fraction of what customs actually took.
DUTY_GUARD = Decimal("0.20")

#: What a cost's scope points at, on a load: the engine's own reading of a target.
_SCOPE_KEY = {
    CostScope.SHIPMENT: "shipment_id",
    CostScope.CONTAINER: "container_id",
    CostScope.PO: "po_id",
    CostScope.PO_LINE: "po_line_id",
}


def loads_under(comp: Computation, scope: CostScope, target_id: UUID) -> list[engine.Load]:
    """The loads a cost on this target is spread over. Only loaded lines: an order-level cost is not
    spread over the part of the order that has not travelled."""
    key = _SCOPE_KEY[scope]
    return [ld for ld in comp.loads if getattr(ld, key) == target_id]


def default_method(
    db: Session,
    org: Organization,
    scope: CostScope,
    target_id: UUID,
    cost_type: CostType,
    amount_base: Decimal | None = None,
    comp: Computation | None = None,
    exclude: UUID | None = None,
    pending: Decimal = Decimal(0),
) -> AllocationMethod:
    """The method written on a new cost when nobody chose one. Written on the cost, never implicit at
    compute time, and the same for every door a cost comes in by.

    Customs duty is spread by theoretical duty — each line's rate times its customs value — when the
    rates can be trusted to do it: every loaded line has one, at least one is above zero (all zeros
    leave nothing to spread on), and they explain the duty paid within `DUTY_GUARD`. Otherwise by
    customs value, which is never unallocated and never puts a whole duty on one line.

    `exclude` is the cost the method is for when it is already on file — re-typed as duty, it would
    otherwise count among the duty already paid, and be weighed twice. `pending` is duty already paid
    on this very target that the computation does not hold yet: written by the same file, a moment ago.
    """
    if cost_type is not CostType.CUSTOMS_DUTY:
        return org.default_allocation_method
    comp = comp if comp is not None else compute(db, org)
    loads = loads_under(comp, scope, target_id)
    rates = [ld.duty_rate for ld in loads]
    if not loads or any(rate is None for rate in rates) or not any(rate and rate > 0 for rate in rates):
        return AllocationMethod.BY_CIF_VALUE
    theoretical = sum(
        ((ld.duty_rate or Decimal(0)) * comp.result.cif.get(ld.id, ld.fob) for ld in loads), Decimal(0)
    )
    if amount_base is not None:
        # Customs may bill a box's duty in two goes; the rates explain what was paid in all, so the
        # invoiced duty already on these lines counts with this one. An estimate does not: this very
        # cost is usually what replaces it.
        on_these = {ld.id for ld in loads}
        already = sum(
            (
                a.amount_base
                for a in comp.result.allocations
                if a.load_id in on_these
                and a.cost_id != exclude
                and comp.cost_rows[a.cost_id].cost_type is CostType.CUSTOMS_DUTY
                and comp.cost_rows[a.cost_id].status is CostStatus.ACTUAL
            ),
            Decimal(0),
        )
        if abs(amount_base + already + pending - theoretical) > theoretical * DUTY_GUARD:
            return AllocationMethod.BY_CIF_VALUE
    return AllocationMethod.BY_THEORETICAL_DUTY


def check_method(method: AllocationMethod, cost_type: CostType) -> None:
    duty_like = method in (AllocationMethod.BY_CIF_VALUE, AllocationMethod.BY_THEORETICAL_DUTY)
    if duty_like and cost_type is not CostType.CUSTOMS_DUTY:
        raise Unprocessable(f"{method.value} is reserved for CUSTOMS_DUTY costs", code="METHOD_NOT_ALLOWED")
    if cost_type.value in DUTIABLE and duty_like:
        raise Unprocessable("A dutiable cost cannot be allocated on a CIF basis", code="METHOD_NOT_ALLOWED")


def apply_fx(db: Session, fx: FxService, org: Organization, cost: Cost, manual_rate: Decimal | None) -> None:
    """Fix the rate on the cost — the ECB's of its date unless a person gave one — and its amount in the
    organization's currency. Fixed once: a landed cost does not move with tomorrow's rate."""
    resolved = fx.resolve(db, org.base_currency, cost.currency, cost.cost_date, manual_rate)
    cost.fx_rate, cost.fx_date, cost.fx_source = resolved.rate, resolved.rate_date, resolved.source
    cost.amount_base = to_base(cost.amount, cost.fx_rate)


def open_estimates(
    db: Session, org_id: UUID, scope: CostScope, target_id: UUID, cost_type: CostType
) -> list[Cost]:
    """Estimates of this type, on this exact target, still standing and not yet replaced."""
    column = getattr(Cost, _SCOPE_KEY[scope])
    taken = select(Cost.supersedes_cost_id).where(Cost.org_id == org_id, Cost.supersedes_cost_id.is_not(None))
    return list(
        db.scalars(
            select(Cost).where(
                Cost.org_id == org_id,
                Cost.status == CostStatus.ESTIMATE,
                Cost.cost_type == cost_type,
                Cost.closed_at.is_(None),
                Cost.id.not_in(taken),
                column == target_id,
            )
        )
    )


# ---------------------------------------------------------------------------- the same invoice

#: Words that say what kind of company it is, not which one: "Transdemo SAS" and "TRANSDEMO" are one
#: forwarder. Kept to legal forms, so that two real names are never merged by it.
_LEGAL_FORMS = frozenset(
    {"sa", "sas", "sasu", "sarl", "eurl", "snc", "sca", "gmbh", "ag", "kg", "ltd", "limited", "plc",
     "bv", "nv", "srl", "spa", "sl", "inc", "llc", "corp", "co"}
)  # fmt: skip


def none_if_blank(text: str | None) -> str | None:
    """A field left empty is no value. Stored as "", an invoice number would be a number — the
    database's own uniqueness counts it as one — and every empty one the same number."""
    if text is None:
        return None
    return text.strip() or None


_NO_NUMBER = frozenset({"n/a", "na", "nc", "n.c.", "s/o", "none", "neant", "néant"})


def number_or_none(text: str | None) -> str | None:
    """An invoice number, or none: « - », « / », « n/a » written where a ledger has no number are no
    number. Kept, the database's uniqueness would hold every « - » of a forwarder on a box to be one
    invoice line, while every rule comparing invoices reads them as no number at all."""
    number = none_if_blank(text)
    if number is None or not normalize_invoice_number(number) or number.casefold() in _NO_NUMBER:
        return None
    return number


# ---------------------------------------------------------------------------- one writer at a time

#: The first key of the advisory lock that makes the doors writing an organization's costs take turns.
#: Two ints: the second is the organization, so that two organizations never wait for each other
#: (unless their ids share 32 bits, and then they only wait).
_BOOKS_LOCK = 0x46534243  # "FSBC"


def lock_books(db: Session, org_id: UUID) -> None:
    """Take the organization's books for this transaction: every door that writes or deletes costs —
    a person's entry, an invoice confirmed or reopened, a ledger imported or taken back, estimates —
    reads the books to refuse a line already there, then writes. Two doors at once each read the books
    without the other's lines, and one charge is written twice. Released at commit or rollback."""
    space, org = books_lock_key(org_id)
    db.execute(text("SELECT pg_advisory_xact_lock(:space, :org)"), {"space": space, "org": org})


def books_lock_key(org_id: UUID) -> tuple[int, int]:
    """The two ints of an organization's books lock."""
    return _BOOKS_LOCK, int.from_bytes(org_id.bytes[:4], "big", signed=True)


def normalize_vendor(name: str | None) -> str:
    """A forwarder's name as it can be compared: no accents, case, punctuation or legal form."""
    if not name:
        return ""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return "".join(word for word in re.findall(r"[a-z0-9]+", plain) if word not in _LEGAL_FORMS)


def normalize_invoice_number(number: str | None) -> str:
    """An invoice number as it can be compared: "F-0412", "F 0412" and "f0412" are one invoice."""
    return re.sub(r"[^A-Z0-9]", "", (number or "").upper())


def same_invoice(
    vendor_a: str | None, number_a: str | None, vendor_b: str | None, number_b: str | None
) -> bool:
    """One invoice, whatever door each copy came in by: the same number, from the same forwarder — or
    from a forwarder one of the two copies does not name."""
    number = normalize_invoice_number(number_a)
    if not number or number != normalize_invoice_number(number_b):
        return False
    first, second = normalize_vendor(vendor_a), normalize_vendor(vendor_b)
    return not first or not second or first == second
