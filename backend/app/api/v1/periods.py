"""Closing a month: freeze what went into the accounts, and say what has moved since."""

from __future__ import annotations

from fastapi import APIRouter, Path, status

from app.api.v1 import schemas
from app.api.v1.deps import AdminDep
from app.core.tenancy import TenantDep
from app.domain.audit.service import (
    PERIOD_CLOSED,
    PERIOD_DRIFT_ACKNOWLEDGED,
    PERIOD_REOPENED,
    record,
)
from app.domain.costing.service import compute
from app.domain.periods import service as periods

router = APIRouter(prefix="/periods", tags=["periods"])

Period = Path(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="YYYY-MM")


@router.get("", response_model=schemas.PeriodList)
def list_periods(t: TenantDep) -> schemas.PeriodList:
    """Every month a container arrived in, newest first: open months with today's figures, closed
    months with the frozen ones and how far today's have moved from them."""
    found = periods.summaries(t.db, compute(t.db, t.org), t.org)
    return schemas.PeriodList(
        base_currency=t.org.base_currency,
        periods=[schemas.PeriodSummary.model_validate(s) for s in found],
    )


@router.get("/{period}", response_model=schemas.PeriodDetail)
def get_period(t: TenantDep, period: str = Period) -> schemas.PeriodDetail:
    found = periods.detail(t.db, compute(t.db, t.org), t.org, period)
    return schemas.PeriodDetail(
        base_currency=t.org.base_currency,
        summary=schemas.PeriodSummary.model_validate(found.summary),
        readiness=schemas.PeriodReadiness.model_validate(found.readiness) if found.readiness else None,
        drift_lines=[schemas.PeriodDrift.model_validate(line) for line in found.drift_lines],
        accruals=[schemas.PeriodAccrual.model_validate(a) for a in found.accruals],
        accruals_total=found.accruals_total,
    )


@router.post("/{period}/close", response_model=schemas.PeriodSummary, status_code=status.HTTP_201_CREATED)
def close(t: TenantDep, p: AdminDep, period: str = Period) -> schemas.PeriodSummary:
    """Freeze the landed cost of the containers that arrived this month, line by line. Nothing is
    locked afterwards; the figure frozen here simply never moves again."""
    comp = compute(t.db, t.org)
    closed = periods.close_period(t.db, comp, t.org, period, p.user_id)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=PERIOD_CLOSED,
        entity_type="period",
        entity_id=closed.id,
        after={"period": period, "containers": closed.containers, "fob": closed.fob, "landed": closed.landed},
    )
    t.db.commit()
    found = periods.detail(t.db, comp, t.org, period)
    return schemas.PeriodSummary.model_validate(found.summary)


@router.delete("/{period}/close", status_code=status.HTTP_204_NO_CONTENT)
def reopen(t: TenantDep, p: AdminDep, period: str = Period) -> None:
    """Undo a close. The frozen figures are deleted; the audit log keeps what they were."""
    closed = periods.reopen_period(t.db, t.org, period)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=PERIOD_REOPENED,
        entity_type="period",
        entity_id=closed.id,
        before={
            "period": period,
            "containers": closed.containers,
            "fob": closed.fob,
            "landed": closed.landed,
        },
    )
    t.db.commit()


@router.post("/{period}/acknowledge-drift", response_model=schemas.PeriodSummary)
def acknowledge(
    payload: schemas.PeriodAcknowledge, t: TenantDep, p: AdminDep, period: str = Period
) -> schemas.PeriodSummary:
    """The drift as it stands today has been booked as an adjustment in the open month: the closed
    month stops asking for it. A cost that arrives later makes it ask again, for the difference."""
    comp = compute(t.db, t.org)
    closed = periods.acknowledge_drift(t.db, comp, t.org, period, p.user_id, payload.note)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=PERIOD_DRIFT_ACKNOWLEDGED,
        entity_type="period",
        entity_id=closed.id,
        after={
            "period": period,
            "acknowledged_drift": closed.acknowledged_drift,
            "note": closed.acknowledged_note,
        },
    )
    t.db.commit()
    return schemas.PeriodSummary.model_validate(periods.detail(t.db, comp, t.org, period).summary)
