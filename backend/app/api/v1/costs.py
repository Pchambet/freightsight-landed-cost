from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.errors import Conflict, Unprocessable
from app.core.money import q2
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import (
    COST_CLOSED,
    COST_CREATED,
    COST_DELETED,
    COST_FIELDS,
    COST_SUPERSEDED,
    COST_UPDATED,
    record,
    snapshot,
)
from app.domain.costing import entry
from app.domain.costing.service import recompute_org
from app.domain.invoices.checks import invoice_line_recorded, recorded_note, same_line_on_file
from app.domain.models import (
    AllocationMethod,
    Container,
    Cost,
    CostScope,
    CostStatus,
    CostType,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
)

router = APIRouter(prefix="/costs", tags=["costs"])

_SCOPE_MODEL = {
    CostScope.SHIPMENT: (Shipment, "shipment_id", "Shipment"),
    CostScope.CONTAINER: (Container, "container_id", "Container"),
    CostScope.PO: (PurchaseOrder, "po_id", "Purchase order"),
    CostScope.PO_LINE: (PurchaseOrderLine, "po_line_id", "Purchase order line"),
}


def _check_splits(splits: list[dict[str, Any]]) -> None:
    """Refuse a manual split that names one target twice.

    Two lines on the same container is the ordinary slip — you meant to split between two orders and
    picked the wrong target type — and it used to allocate the cost once per line, so a 1 000 € freight
    invoice landed as 2 000 € of stock value in the customer's own books, silently.
    """
    seen = {(s["target_type"], str(s["target_id"])) for s in splits}
    if len(seen) != len(splits):
        raise Unprocessable(
            "A manual split names the same target twice; give it one line and one percentage",
            code="DUPLICATE_MANUAL_SPLIT",
        )


_UNSET = object()


def _refuse_a_line_already_recorded(t: TenantSession, cost: Cost) -> None:
    """The same invoice line keyed in twice — "TRANSDEMO / F-0412" then "Transdemo SAS / F0412" on one
    box in one currency, or once on the box and once on its bill of lading for the same amount —
    counts twice in the landed cost. Refused by name: before this it was a database error when spelled
    the same, and nothing at all otherwise. Spelled the same to the letter, an estimate carrying the
    number is refused too: the database's own index does not tell the two statuses apart."""
    target = cost.container_id or cost.shipment_id or cost.po_id or cost.po_line_id
    if cost.invoice_number is None or target is None:
        return
    with t.db.no_autoflush:  # the pending row itself must not reach the index before we have looked
        found = same_line_on_file(
            t.db,
            t.org_id,
            vendor=cost.vendor,
            invoice_number=cost.invoice_number,
            cost_type=cost.cost_type,
            currency=cost.currency,
            target_id=target,
            exclude=cost.id,
        )
        if cost.status is CostStatus.ACTUAL:
            found += [
                c
                for c in invoice_line_recorded(
                    t.db,
                    t.org_id,
                    vendor=cost.vendor,
                    invoice_number=cost.invoice_number,
                    cost_type=cost.cost_type,
                    currency=cost.currency,
                    amount=cost.amount,
                    scope=cost.scope,
                    target_id=target,
                    exclude=cost.id,
                )
                if c not in found
            ]
    if found:
        raise Unprocessable(
            "This invoice line is already recorded on this freight",
            code="INVOICE_LINE_ALREADY_RECORDED",
            errors=[
                {
                    "field": "invoice_number",
                    "code": "INVOICE_LINE_ALREADY_RECORDED",
                    "message": recorded_note(c),
                }
                for c in found
            ],
        )


def _supersede(t: TenantSession, cost: Cost, estimate_id: UUID | None, *, status: CostStatus) -> None:
    """Point an actual cost at the estimate it replaces, refusing the pairings that make no sense.

    The estimate leaves the current allocation the moment this is set — that is what makes the
    landed cost exact instead of approximate — and stays in the database as the thing the variance
    is measured against.
    """
    if estimate_id is None:
        cost.supersedes_cost_id = None
        return
    if status is not CostStatus.ACTUAL:
        raise Unprocessable("Only an actual cost can replace an estimate", code="NOT_AN_ACTUAL")
    if estimate_id == cost.id:
        raise Unprocessable("A cost cannot replace itself", code="SELF_SUPERSEDE")
    estimate = t.get_or_404(Cost, estimate_id, "Estimate")
    if estimate.status is not CostStatus.ESTIMATE:
        raise Unprocessable(f"Cost {estimate_id} is not an estimate", code="NOT_AN_ESTIMATE")
    if estimate.closed_at is not None:
        raise Unprocessable("This estimate was closed; reopen it or pick another", code="ESTIMATE_CLOSED")
    taken = t.db.scalar(t.q(Cost).where(Cost.supersedes_cost_id == estimate_id, Cost.id != cost.id))
    if taken is not None:
        raise Unprocessable(
            f"Estimate {estimate_id} is already replaced by cost {taken.id}", code="ALREADY_SUPERSEDED"
        )
    cost.supersedes_cost_id = estimate_id


@router.get("", response_model=list[schemas.CostResponse])
def list_costs(
    t: TenantDep,
    scope: CostScope | None = None,
    target_id: UUID | None = None,
    cost_type: CostType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[Cost]:
    stmt = t.q(Cost)
    if scope:
        stmt = stmt.where(Cost.scope == scope)
    if target_id:
        stmt = stmt.where(
            (Cost.shipment_id == target_id)
            | (Cost.container_id == target_id)
            | (Cost.po_id == target_id)
            | (Cost.po_line_id == target_id)
        )
    if cost_type:
        stmt = stmt.where(Cost.cost_type == cost_type)
    if date_from:
        stmt = stmt.where(Cost.cost_date >= date_from)
    if date_to:
        stmt = stmt.where(Cost.cost_date <= date_to)
    return list(t.db.scalars(stmt.order_by(Cost.cost_date.desc(), Cost.created_at.desc())))


@router.post("", response_model=schemas.CostResponse, status_code=status.HTTP_201_CREATED)
def create_cost(payload: schemas.CostCreate, t: TenantDep, fx: FxDep, p: WriterDep) -> Cost:
    entry.lock_books(t.db, t.org_id)
    model, fk, what = _SCOPE_MODEL[payload.scope]
    t.get_or_404(model, payload.target_id, what)
    splits = [s.model_dump(mode="json") for s in payload.manual_splits] if payload.manual_splits else None
    if splits is not None:
        _check_splits(splits)
    cost = Cost(
        scope=payload.scope,
        status=payload.status,
        cost_type=payload.cost_type,
        amount=q2(payload.amount),
        currency=payload.currency,
        cost_date=payload.cost_date,
        manual_splits=splits,
        vendor=payload.vendor,
        invoice_number=payload.invoice_number,
        notes=payload.notes,
        created_by=p.user_id,
    )
    setattr(cost, fk, payload.target_id)
    # The amount in the organization's currency first: the default duty method weighs it against the
    # duty the lines' own rates give.
    entry.apply_fx(t.db, fx, t.org, cost, payload.fx_rate)
    method = payload.allocation_method or entry.default_method(
        t.db, t.org, payload.scope, payload.target_id, payload.cost_type, cost.amount_base
    )
    entry.check_method(method, payload.cost_type)
    cost.allocation_method = method
    _refuse_a_line_already_recorded(t, cost)
    t.add(cost)
    t.db.flush()
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=COST_CREATED,
        entity_type="cost",
        entity_id=cost.id,
        after=snapshot(cost, COST_FIELDS),
    )
    recompute_org(t.db, t.org)
    t.db.commit()
    t.db.refresh(cost)
    return cost


@router.get("/{cost_id}", response_model=schemas.CostResponse)
def get_cost(cost_id: UUID, t: TenantDep) -> Cost:
    return t.get_or_404(Cost, cost_id, "Cost")


@router.patch("/{cost_id}", response_model=schemas.CostResponse)
def update_cost(cost_id: UUID, payload: schemas.CostUpdate, t: TenantDep, fx: FxDep, p: WriterDep) -> Cost:
    entry.lock_books(t.db, t.org_id)
    cost = t.get_or_404(Cost, cost_id, "Cost")
    before = snapshot(cost, COST_FIELDS)
    before_type = cost.cost_type
    changes = payload.model_dump(exclude_unset=True)
    splits = changes.pop("manual_splits", None)
    manual_rate = changes.pop("fx_rate", None)
    supersedes = changes.pop("supersedes_cost_id", _UNSET)
    if supersedes is not _UNSET:
        _supersede(t, cost, supersedes, status=changes.get("status") or cost.status)
    for k, v in changes.items():
        if k == "amount":
            v = q2(v)
        setattr(cost, k, v)
    if "cost_type" in changes:
        cost.type_inferred = False  # a person said it: what the audit finds on it is a fact again
    # Before anything below may write the edited row — a rate fetched for a new currency or date is
    # saved at once, and any query outside `no_autoflush` sends what is pending — and so reach the
    # database's own refusal as a server error.
    if {"invoice_number", "vendor", "cost_type", "currency", "amount", "status"} & changes.keys():
        _refuse_a_line_already_recorded(t, cost)
    if payload.allocation_method is not None or splits is not None:
        if cost.allocation_method is AllocationMethod.MANUAL:
            if splits is None and cost.manual_splits is None:
                raise Unprocessable("manual_splits is required for MANUAL", code="MANUAL_SPLITS_REQUIRED")
            if splits is not None:
                if sum(Decimal(str(s["pct"])) for s in splits) != Decimal("100.00"):
                    raise Unprocessable("manual_splits must sum to 100.00", code="INVALID_MANUAL_SPLIT")
                _check_splits(splits)
                cost.manual_splits = [
                    {"target_type": s["target_type"], "target_id": str(s["target_id"]), "pct": str(s["pct"])}
                    for s in splits
                ]
        else:
            cost.manual_splits = None
        entry.check_method(cost.allocation_method, cost.cost_type)
    if any(k in changes for k in ("amount", "currency", "cost_date")) or manual_rate is not None:
        entry.apply_fx(t.db, fx, t.org, cost, manual_rate)
    if cost.cost_type is not before_type:
        # A duty re-typed as freight kept its duty method and was spread by duty rates in the second
        # pass, after the customs value it belongs to had been taken. The method follows the type when
        # it was the old type's own default or does not suit the new one; a method somebody chose that
        # suits both stays theirs. Whatever the method, it has to suit the new type.
        duty_like = cost.allocation_method in (
            AllocationMethod.BY_CIF_VALUE,
            AllocationMethod.BY_THEORETICAL_DUTY,
        )
        was_default = (
            duty_like
            if before_type is CostType.CUSTOMS_DUTY
            else cost.allocation_method is t.org.default_allocation_method
        )
        unsuited = duty_like and cost.cost_type is not CostType.CUSTOMS_DUTY
        if (
            CostType.CUSTOMS_DUTY in (before_type, cost.cost_type)
            and payload.allocation_method is None
            and cost.allocation_method is not AllocationMethod.MANUAL
            and (was_default or unsuited)
        ):
            target = cost.container_id or cost.shipment_id or cost.po_id or cost.po_line_id
            assert target is not None
            with t.db.no_autoflush:
                cost.allocation_method = entry.default_method(
                    t.db, t.org, cost.scope, target, cost.cost_type, cost.amount_base, exclude=cost.id
                )
        entry.check_method(cost.allocation_method, cost.cost_type)
    t.db.flush()
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        # Replacing an estimate is not an ordinary edit: it is the moment a forecast becomes a fact.
        action=COST_SUPERSEDED if cost.supersedes_cost_id and supersedes is not _UNSET else COST_UPDATED,
        entity_type="cost",
        entity_id=cost.id,
        before=before,
        after=snapshot(cost, COST_FIELDS),
    )
    recompute_org(t.db, t.org)
    t.db.commit()
    t.db.refresh(cost)
    return cost


@router.post("/{cost_id}/close", response_model=schemas.CostResponse)
def close_cost(cost_id: UUID, payload: schemas.CostClose, t: TenantDep, p: WriterDep) -> Cost:
    """Close an estimate no invoice will ever match — "the forwarder never billed the inspection".

    The estimate stops being allocated but is not deleted: what was expected and never charged is
    part of the story of the shipment, and deleting it would quietly change past landed costs.
    """
    entry.lock_books(t.db, t.org_id)
    cost = t.get_or_404(Cost, cost_id, "Cost")
    if cost.status is not CostStatus.ESTIMATE:
        raise Unprocessable("Only an estimate can be closed", code="NOT_AN_ESTIMATE")
    if cost.closed_at is not None:
        raise Conflict("This estimate is already closed")
    before = snapshot(cost, COST_FIELDS)
    cost.closed_at = datetime.now(UTC)
    cost.close_reason = payload.reason
    t.db.flush()
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=COST_CLOSED,
        entity_type="cost",
        entity_id=cost.id,
        before=before,
        after={"closed_at": cost.closed_at, "close_reason": cost.close_reason},
    )
    recompute_org(t.db, t.org)
    t.db.commit()
    t.db.refresh(cost)
    return cost


@router.delete("/{cost_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cost(cost_id: UUID, t: TenantDep, p: WriterDep) -> None:
    entry.lock_books(t.db, t.org_id)
    cost = t.get_or_404(Cost, cost_id, "Cost")
    # Recorded before the row is gone: this is the entry someone will look for when a landed cost
    # changes and nobody remembers why.
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=COST_DELETED,
        entity_type="cost",
        entity_id=cost.id,
        before=snapshot(cost, COST_FIELDS),
    )
    t.db.delete(cost)
    t.db.flush()
    recompute_org(t.db, t.org)
    t.db.commit()
