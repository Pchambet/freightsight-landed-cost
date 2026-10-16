"""How a figure built on the goods' value says that part of that value is unknown.

A line whose order has no exchange rate has no value in the books' currency. Every figure that would
include it — FOB, landed cost, unit cost, coefficient, margin, freight share — is None, never computed
as if the line were worth 0 nor over the other lines as if it were not there. Each aggregate says why
beside it, in the same two fields everywhere: `fob_missing`, the code (FX_RATE_MISSING), and
`fob_missing_lines`, how many of its lines have no value. What does not depend on the value — the
costs placed on those lines, their VAT, their quantities — stays whole.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from app.core.money import q4

FX_RATE_MISSING = "FX_RATE_MISSING"


def total(values: Iterable[Decimal | None]) -> Decimal | None:
    """The sum, or None as soon as one of the parts is unknown."""
    out = Decimal(0)
    for value in values:
        if value is None:
            return None
        out += value
    return out


def missing_code(lines: int) -> str | None:
    return FX_RATE_MISSING if lines else None


def coefficient(landed: Decimal | None, fob: Decimal | None) -> Decimal | None:
    """landed / fob to four decimals; None when either is unknown or there are no goods."""
    if landed is None or not fob:
        return None
    return q4(landed / fob)
