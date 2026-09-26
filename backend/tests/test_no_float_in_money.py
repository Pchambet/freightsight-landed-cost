"""Guard rail: no float on any money-looking column or schema field."""

from __future__ import annotations

import re

from sqlalchemy import Float

from app.api.v1 import schemas
from app.domain.models import Base

MONEY_NAME = re.compile(r"(amount|price|value|rate|total|cost)", re.IGNORECASE)


def test_no_float_columns_in_models() -> None:
    offenders = [
        f"{table.name}.{col.name}"
        for table in Base.metadata.tables.values()
        for col in table.columns
        if isinstance(col.type, Float)
    ]
    assert offenders == []


def test_no_float_fields_in_money_schemas() -> None:
    offenders = []
    for name in dir(schemas):
        obj = getattr(schemas, name)
        fields = getattr(obj, "model_fields", None)
        if not isinstance(fields, dict):
            continue
        for fname, finfo in fields.items():
            if MONEY_NAME.search(fname) and finfo.annotation is float:
                offenders.append(f"{name}.{fname}")
    assert offenders == []
