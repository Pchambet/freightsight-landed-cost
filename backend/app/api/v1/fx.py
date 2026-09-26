from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.api.v1 import schemas
from app.api.v1.deps import FxDep
from app.api.v1.schemas import currency_code
from app.core.tenancy import TenantDep

router = APIRouter(prefix="/fx-rates", tags=["fx"])


@router.get("", response_model=schemas.FxRateResponse)
def get_rate(
    t: TenantDep, fx: FxDep, quote: str, on_date: date, base: str | None = None
) -> schemas.FxRateResponse:
    """1 `quote` = rate `base` on `on_date` (ECB reference, cached)."""
    base_cur = currency_code(base) if base else t.org.base_currency
    r = fx.resolve(t.db, base_cur, currency_code(quote), on_date)
    t.db.commit()
    return schemas.FxRateResponse(
        base=base_cur, quote=currency_code(quote), rate_date=r.rate_date, rate=r.rate, source=r.source
    )
