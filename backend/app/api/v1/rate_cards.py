"""The organization's price list, and applying it to a container.

A rate card is what the customer already knows and we do not: their forwarder's prices. Nothing here
invents a number — a cost type with no card produces no estimate, and the report says so.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy.exc import IntegrityError

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.errors import Conflict, NotFound
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import jsonable, record, snapshot
from app.domain.costing.entry import lock_books
from app.domain.costing.service import recompute_org
from app.domain.estimates.service import estimate_container, record_estimates
from app.domain.models import Container, RateCard

router = APIRouter(tags=["rate-cards"])

#: A card prices every estimate made from it, so who set which price, and when, is kept like a cost.
RATE_CARD_CREATED = "rate_card.created"
RATE_CARD_UPDATED = "rate_card.updated"
RATE_CARD_DELETED = "rate_card.deleted"


def _card(card: RateCard) -> dict[str, Any]:
    """What the history keeps of a card. Its amount is kept as `price`: depending on the basis it is
    a percentage of FOB or a duty rate as often as a sum, and must not be read as money."""
    kept = snapshot(card, ("cost_type", "scope", "basis", "currency", "hs_code", "notes"))
    return {**kept, "price": jsonable(card.amount)}


def _record(t: TenantSession, user_id: UUID | None, action: str, card: RateCard, **change: Any) -> None:
    record(
        t.db,
        t.org_id,
        actor_user_id=user_id,
        action=action,
        entity_type="rate_card",
        entity_id=card.id,
        **change,
    )


@router.get("/organization/rate-cards", response_model=list[schemas.RateCardResponse])
def list_rate_cards(t: TenantDep) -> list[RateCard]:
    return list(t.db.scalars(t.q(RateCard).order_by(RateCard.cost_type, RateCard.scope)))


@router.post(
    "/organization/rate-cards",
    response_model=schemas.RateCardResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_rate_card(payload: schemas.RateCardIn, t: TenantDep, p: WriterDep) -> RateCard:
    card = t.add(RateCard(**payload.model_dump()))
    try:
        t.db.flush()
        _record(t, p.user_id, RATE_CARD_CREATED, card, after=_card(card))
        t.db.commit()
    except IntegrityError as exc:
        t.db.rollback()
        raise Conflict(
            f"A {payload.cost_type.value} card already exists for this scope", code="RATE_CARD_EXISTS"
        ) from exc
    t.db.refresh(card)
    return card


@router.patch("/organization/rate-cards/{card_id}", response_model=schemas.RateCardResponse)
def update_rate_card(card_id: UUID, payload: schemas.RateCardUpdate, t: TenantDep, p: WriterDep) -> RateCard:
    card = t.get_or_404(RateCard, card_id, "Rate card")
    before = _card(card)
    # Compared as values, not as the history's strings: « 1.5 » sent back for a stored « 1.5000 » is
    # the same price, and an entry saying it moved would be false.
    changes = {f: v for f, v in payload.model_dump(exclude_unset=True).items() if getattr(card, f) != v}
    for field, value in changes.items():
        setattr(card, field, value)
    if changes:
        _record(t, p.user_id, RATE_CARD_UPDATED, card, before=before, after=_card(card))
    t.db.commit()
    t.db.refresh(card)
    return card


@router.delete("/organization/rate-cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_card(card_id: UUID, t: TenantDep, p: WriterDep) -> None:
    """Deleting a card leaves the estimates it produced alone: they are costs now, not derivations."""
    card = t.get_or_404(RateCard, card_id, "Rate card")
    _record(t, p.user_id, RATE_CARD_DELETED, card, before=_card(card))
    t.db.delete(card)
    t.db.commit()


@router.post(
    "/containers/{container_id}/estimates",
    response_model=schemas.EstimateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_estimates(container_id: UUID, t: TenantDep, fx: FxDep, p: WriterDep) -> schemas.EstimateResponse:
    """Fill in what this container is expected to cost, from the rate cards and the tariff rates.

    One transaction, and never a second estimate for a cost type that already has one: running this
    twice is a no-op, which is what makes it safe to offer as a button.
    """
    container = t.db.scalar(
        t.q(Container).where(Container.id == container_id, Container.archived_at.is_(None))
    )
    if container is None:
        raise NotFound("Container")
    lock_books(t.db, t.org_id)
    result = estimate_container(t.db, fx, t.org, container, created_by=p.user_id)
    record_estimates(t.db, t.org_id, result, actor_user_id=p.user_id)
    if result.created:
        recompute_org(t.db, t.org)
    t.db.commit()
    return schemas.EstimateResponse(
        created=[schemas.CostResponse.model_validate(c) for c in result.created],
        skipped={
            k: schemas.EstimateSkip(code=v.code, scope=v.scope, basis=v.basis)
            for k, v in result.skipped.items()
        },
    )
