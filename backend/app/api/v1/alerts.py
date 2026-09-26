"""Alerts: what happened while nobody was looking."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.v1 import schemas
from app.core.errors import NotFound
from app.core.tenancy import TenantDep
from app.domain.alerts.service import mark_all_read, mark_read, unread_count
from app.domain.models import Alert, Container

router = APIRouter(prefix="/alerts", tags=["alerts"])


def _response(alert: Alert, container_number: str | None) -> schemas.AlertResponse:
    """The box number rides along so a screen can write its own sentence without a second call."""
    out = schemas.AlertResponse.model_validate(alert)
    out.container_number = container_number
    return out


def _number_of(t: TenantDep, alert: Alert) -> str | None:
    if alert.container_id is None:
        return None
    return t.db.scalar(
        select(Container.container_number).where(
            Container.id == alert.container_id, Container.org_id == t.org_id
        )
    )


@router.get("", response_model=list[schemas.AlertResponse])
def list_alerts(
    t: TenantDep,
    unread: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[schemas.AlertResponse]:
    stmt = (
        select(Alert, Container.container_number)
        .outerjoin(Container, Container.id == Alert.container_id)
        .where(Alert.org_id == t.org_id)
        .order_by(Alert.created_at.desc())
        .limit(limit)
    )
    if unread:
        stmt = stmt.where(Alert.read_at.is_(None))
    return [_response(alert, number) for alert, number in t.db.execute(stmt)]


@router.post("/{alert_id}/read", response_model=schemas.AlertResponse)
def read_alert(alert_id: UUID, t: TenantDep) -> schemas.AlertResponse:
    alert = mark_read(t.db, t.org_id, alert_id)
    if alert is None:
        raise NotFound("Alert")
    t.db.commit()
    return _response(alert, _number_of(t, alert))


@router.post("/read-all", response_model=schemas.AlertsRead)
def read_all_alerts(t: TenantDep) -> schemas.AlertsRead:
    marked = mark_all_read(t.db, t.org_id)
    t.db.commit()
    return schemas.AlertsRead(marked=marked)


@router.get("/count", response_model=schemas.AlertsRead)
def count_unread(t: TenantDep) -> schemas.AlertsRead:
    """The number behind the bell, for clients that want it without the list."""
    return schemas.AlertsRead(marked=unread_count(t.db, t.org_id))
