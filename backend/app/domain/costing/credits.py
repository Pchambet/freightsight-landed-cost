"""Credits: a refund or a discount that lowers what an invoiced cost amounts to.

A credit corrects a cost that exists — never a cost of its own, never a negative one. The cost
carries the net amount, so that everything that reads a cost (the engine, the reports and exports, a
closed month's drift, the ERP's « changed since it was pushed ») sees the refund without knowing what
a credit is. The reduction itself is kept as its own row, with the document it came from and who
accepted it, and taking it back gives the cost its amount back to the cent.

The rate stays the cost's own: a landed cost does not move with the rate of the day the refund came.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Unprocessable
from app.core.money import q2, to_base
from app.domain.costing.entry import normalize_invoice_number, same_invoice, target_column
from app.domain.models import Cost, CostCredit, CostScope, CostStatus, CostType


def creditable(
    db: Session, org_id: UUID, scope: CostScope, target_id: UUID, cost_type: CostType
) -> list[Cost]:
    """The invoiced costs of this type on this exact target, in any currency: what a refund of that
    type on that target can reduce. An estimate is no money paid, and nothing to refund."""
    return list(
        db.scalars(
            select(Cost)
            .where(
                Cost.org_id == org_id,
                Cost.status == CostStatus.ACTUAL,
                Cost.cost_type == cost_type,
                target_column(scope) == target_id,
            )
            .order_by(Cost.cost_date, Cost.created_at)
        )
    )


def apply(
    cost: Cost,
    amount: Decimal,
    *,
    currency: str,
    credit_number: str | None,
    vendor: str | None,
    credit_date: date,
    invoice_id: UUID | None = None,
    created_by: UUID | None = None,
) -> CostCredit:
    """Take `amount` off `cost`, refusing what would make the cost wrong rather than less."""
    amount = q2(amount)
    if cost.status is not CostStatus.ACTUAL:
        raise Unprocessable("Only an invoiced cost can be credited", code="CREDIT_ON_ESTIMATE")
    if currency != cost.currency:
        # Converting would put a rate nobody chose between the refund and the charge it refunds.
        raise Unprocessable(
            f"This credit is in {currency}; the cost it reduces is in {cost.currency}",
            code="CREDIT_CURRENCY_MISMATCH",
            params={"credit_currency": currency, "cost_currency": cost.currency},
        )
    if amount > cost.amount:
        raise Unprocessable(
            f"A credit of {amount} is more than what this cost still amounts to ({cost.amount})",
            code="CREDIT_EXCEEDS_COST",
            params={"credit": f"{amount:.2f}", "remaining": f"{cost.amount:.2f}", "currency": cost.currency},
        )
    if normalize_invoice_number(credit_number) and any(
        same_invoice(vendor, credit_number, other.vendor, other.credit_number) for other in cost.credits
    ):
        raise Unprocessable(
            "This credit note already reduces this cost",
            code="CREDIT_ALREADY_RECORDED",
            params={"credit_number": credit_number or ""},
        )
    credit = CostCredit(
        org_id=cost.org_id,
        amount=amount,
        credit_number=credit_number,
        vendor=vendor,
        credit_date=credit_date,
        invoice_id=invoice_id,
        created_by=created_by,
    )
    cost.credits.append(credit)
    _set_amount(cost, cost.amount - amount)
    return credit


def remove(cost: Cost, credit: CostCredit) -> None:
    """Take a credit back: the cost amounts to what it did before it."""
    cost.credits.remove(credit)  # the relationship's delete-orphan deletes the row
    _set_amount(cost, cost.amount + credit.amount)


def _set_amount(cost: Cost, amount: Decimal) -> None:
    cost.amount = q2(amount)
    cost.amount_base = to_base(cost.amount, cost.fx_rate)


def audit_entry(cost: Cost, credit: CostCredit, *, applied: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    """The before and after of the cost a credit was just applied to (or just taken back from), with
    the credit's own figures on the side that holds it."""
    other = cost.amount + credit.amount if applied else cost.amount - credit.amount
    # The type on both sides, unchanged: it is what names a cost in the history.
    # `currency` says what `amount` and `credited` are in: a credit on a dollar cost is in dollars.
    now: dict[str, Any] = {
        "cost_type": cost.cost_type,
        "amount": cost.amount,
        "currency": cost.currency,
        "amount_base": cost.amount_base,
    }
    then: dict[str, Any] = {
        "cost_type": cost.cost_type,
        "amount": other,
        "currency": cost.currency,
        "amount_base": to_base(other, cost.fx_rate),
    }
    held: dict[str, Any] = {
        "credited": credit.amount,
        "credit_number": credit.credit_number,
        "credit_vendor": credit.vendor,
        "credit_date": credit.credit_date,
    }
    return (then, now | held) if applied else (then | held, now)
