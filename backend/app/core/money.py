from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
UNIT4 = Decimal("0.0001")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(UNIT4, rounding=ROUND_HALF_UP)


def to_base(amount: Decimal, fx_rate: Decimal) -> Decimal:
    """Convert an amount into the organization's base currency at a fixed rate, rounded to the cent."""
    return q2(amount * fx_rate)
