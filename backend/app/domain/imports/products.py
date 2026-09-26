"""Reading a tariff: the catalogue of articles, and what the audit needs to know of each.

The selling price is what turns a landed cost into a margin; the tariff heading and the duty rate are
what spread a duty by what each line owes; the unit weight and volume, what spreads a freight by them.
One row per article, written on its reference.

A cell left empty says nothing: it never blanks what the catalogue already knows. And the order lines
already written keep what they said — the catalogue lends only to a line being written
(`products.fill_blanks`), so a landed cost already shown does not move because the tariff did. That is
why the tariff comes first when a quarter is loaded.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.tenancy import TenantSession
from app.domain.imports.parsing import ColumnNumbers, Table
from app.domain.imports.service import ImportReport, RowIssue, _Row
from app.domain.models import Product

#: What a row may say of an article, besides its reference.
FIELDS = (
    "description",
    "hs_code",
    "duty_rate",
    "unit_weight_kg",
    "unit_volume_cbm",
    "sale_price",
    "sale_currency",
)


def apply_products(
    t: TenantSession,
    table: Table,
    mapping: dict[str, str],
    report: ImportReport,
    conventions: dict[str, ColumnNumbers],
) -> None:
    catalogue = {product.sku: product for product in t.db.scalars(t.q(Product))}
    counters = {"products_created": 0, "products_updated": 0}
    seen: set[str] = set()
    priced_without_currency = False
    for idx, raw in enumerate(table.rows, start=table.header_row + 1):
        r = _Row(idx, raw, mapping, report, conventions)
        sku = r.required("sku")
        duty = r.rate("duty_rate")
        if duty is not None and duty < 0:
            r.error("duty_rate", "NEGATIVE", "a duty rate cannot be negative", value=str(duty))
        numbers = {name: r.decimal(name) for name in ("unit_weight_kg", "unit_volume_cbm", "sale_price")}
        for name, value in numbers.items():
            if value is not None and value < 0:
                r.error(name, "NEGATIVE", f"{name} cannot be negative", value=str(value))
        currency = (r.text("sale_currency") or "").upper() or None
        if currency is not None and (len(currency) != 3 or not currency.isalpha()):
            r.error(
                "sale_currency",
                "INVALID_CURRENCY",
                f"{currency!r} is not a 3-letter ISO code",
                value=currency,
            )
        if sku is not None and sku in seen:
            r.error("sku", "DUPLICATE_IN_FILE", f"{sku} appears twice", sku=sku)
        if r.failed or sku is None:
            continue
        seen.add(sku)
        if numbers["sale_price"] is not None and currency is None:
            # Read in the organization's currency, as the warning says — and written so: a price
            # without a currency does not take the one an older price of the article had.
            priced_without_currency = True
            currency = t.org.base_currency
        values: dict[str, object] = {
            "description": r.text("description"),
            "hs_code": r.text("hs_code"),
            "duty_rate": duty,
            **numbers,
            "sale_currency": currency,
        }
        known = {name: said for name, said in values.items() if said is not None}
        product = catalogue.get(sku)
        if product is None:
            catalogue[sku] = t.add(Product(sku=sku, **known))
            counters["products_created"] += 1
        elif any(getattr(product, name) != _stored(name, said) for name, said in known.items()):
            for name, said in known.items():
                setattr(product, name, said)
            counters["products_updated"] += 1
        report.valid_rows += 1
    t.db.flush()
    if priced_without_currency:
        report.warnings.append(
            RowIssue(
                table.header_row,
                "sale_currency",
                "SALE_CURRENCY_ASSUMED",
                f"prices without a currency are read in {t.org.base_currency}",
                {"currency": t.org.base_currency},
            )
        )
    report.extra |= counters


def _stored(name: str, value: object) -> object:
    """The value as the database gives it back, so that a file saying the same thing again is no
    update: 4.5 read as 0.045 is 0.0450 once stored."""
    if isinstance(value, Decimal):
        places = {"duty_rate": 4, "unit_weight_kg": 4, "unit_volume_cbm": 6, "sale_price": 4}[name]
        return value.quantize(Decimal(1).scaleb(-places))
    return value
