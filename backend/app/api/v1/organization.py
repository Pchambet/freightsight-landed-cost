from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, status

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.auth import PrincipalDep
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.settings import get_settings
from app.core.tenancy import TenantDep
from app.domain.alerts.service import raise_dnd_risk_alert, unread_count
from app.domain.audit.service import ORGANIZATION_UPDATED, SAMPLE_DATA_DELETED, record
from app.domain.costing.service import recompute_org
from app.domain.documents.registry import get_store
from app.domain.readiness import checks, job_runs
from app.domain.sample_data import has_data, seed_sample_data
from app.domain.sample_history import seed_history
from app.domain.sample_registry import (
    blockers,
    delete_sample_data,
    has_sample_data,
    register_everything,
)
from app.domain.tracking.registry import provider_names
from app.domain.tracking.state import derive_dnd

router = APIRouter(tags=["organization"])


@router.get("/me", response_model=schemas.MeResponse)
def me(p: PrincipalDep) -> schemas.MeResponse:
    return schemas.MeResponse(org_id=p.org_id, user_id=p.user_id, role=p.role, via=p.via)


def _check_settings(settings: dict[str, object] | None) -> None:
    """A default tracking provider we do not have is a setting that fails at the worst moment: when
    someone finally clicks "track this container"."""
    if not settings:
        return
    chosen = settings.get("tracking_provider_default")
    if chosen is not None and (not isinstance(chosen, str) or chosen not in provider_names()):
        raise Unprocessable(
            f"tracking_provider_default must be one of: {', '.join(provider_names())}",
            code="UNKNOWN_PROVIDER",
        )
    # The coefficient the company applies from memory ("FOB x 1,15"), which the audit compares the
    # measured one with. A ratio between 1 and 3: 15 would be a percentage typed by mistake.
    assumed = settings.get("assumed_coefficient")
    if assumed is not None:
        try:
            ratio = Decimal(str(assumed))
        except ArithmeticError:
            ratio = Decimal(0)
        # NaN and infinities are Decimals too, and comparing a NaN raises instead of saying no.
        if isinstance(assumed, bool) or not ratio.is_finite() or not Decimal(1) <= ratio <= Decimal(3):
            raise Unprocessable(
                "assumed_coefficient is a ratio between 1 and 3, such as 1.15",
                code="ASSUMED_COEFFICIENT_INVALID",
            )


def _organization(t: TenantDep) -> schemas.OrganizationResponse:
    response = schemas.OrganizationResponse.model_validate(t.org)
    response.unread_alerts = unread_count(t.db, t.org_id)
    response.has_sample_data = has_sample_data(t.db, t.org_id)
    return response


@router.get("/organization", response_model=schemas.OrganizationResponse)
def get_organization(t: TenantDep) -> schemas.OrganizationResponse:
    return _organization(t)


@router.patch("/organization", response_model=schemas.OrganizationResponse)
def update_organization(
    payload: schemas.OrganizationUpdate, t: TenantDep, p: PrincipalDep, _: WriterDep
) -> schemas.OrganizationResponse:
    changes = payload.model_dump(exclude_unset=True)
    sent = changes.pop("settings", None)
    if sent:
        # Settings are merged, not replaced: a key sent is written, a key sent as null is removed, a
        # key not sent is kept. Two screens that each save their own setting must not erase each
        # other's — the coefficient of the audit page wiping the tracking provider of the settings.
        # (`settings: null` changes nothing: the column cannot be empty, and "forget every setting"
        # is not something a form should be able to say by accident.)
        _check_settings({k: v for k, v in sent.items() if v is not None})
        merged = {**(t.org.settings or {}), **sent}
        changes["settings"] = {k: v for k, v in merged.items() if v is not None}
    before = {k: getattr(t.org, k) for k in changes}
    for k, v in changes.items():
        setattr(t.org, k, v)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=ORGANIZATION_UPDATED,
        entity_type="organization",
        entity_id=t.org_id,
        before=before,
        after=changes,
    )
    if "default_allocation_method" in changes:
        recompute_org(t.db, t.org)
    t.db.commit()
    t.db.refresh(t.org)
    return _organization(t)


@router.post(
    "/organization/sample-data",
    response_model=schemas.SampleDataResponse,
    status_code=status.HTTP_201_CREATED,
)
def load_sample_data(
    t: TenantDep,
    fx: FxDep,
    _: WriterDep,
    profile: Literal["reference", "history"] = "reference",
) -> schemas.SampleDataResponse:
    """Fill an empty organization with a demo dataset. Refuses once it holds any data.

    `reference` is one shipment, two containers and the invoice of the sales demo; `history` is
    twenty containers over the last six months, for the screens that draw a trend.
    """
    if has_data(t.db, t.org_id):
        raise Conflict("This organization already has purchase orders or containers")
    if profile == "history":
        sample = seed_history(t.db, fx, get_store(t.db), t.org)
    else:
        sample = seed_sample_data(t.db, t.org)
    now = datetime.now(UTC)
    for c in sample.containers:
        derive_dnd(c, t.org, now)
        raise_dnd_risk_alert(t.db, t.org, c, now)
    t.db.flush()
    # The organization was empty a moment ago: everything in it now is the demo's, alerts included.
    register_everything(t.db, t.org_id)
    t.db.commit()
    return schemas.SampleDataResponse(
        suppliers=sample.supplier_count,
        purchase_orders=sample.purchase_order_count,
        containers=len(sample.containers),
        costs=sample.cost_count,
        shipment_reference=sample.shipment_reference,
        container_ids=[c.id for c in sample.containers],
    )


@router.delete("/organization/sample-data", response_model=schemas.SampleDataDeleted)
def delete_sample(t: TenantDep, p: PrincipalDep, _: WriterDep) -> schemas.SampleDataDeleted:
    """Take the demo dataset away: what it created, and what only existed because of it (a cost typed
    on a demo container goes with the container). Real data tied to it is refused by name, never
    deleted along: a line of the user's own order in a demo container, a cost from an invoice they
    uploaded, a draft pushed to their ERP."""
    if not has_sample_data(t.db, t.org_id):
        raise NotFound("Sample data", code="NO_SAMPLE_DATA")
    in_the_way = blockers(t.db, t.org_id)
    if in_the_way:
        raise Conflict(
            "Some of your own data is attached to the sample data",
            code="SAMPLE_DATA_IN_USE",
            errors=[{"field": b.field, "code": b.code, "message": b.code} for b in in_the_way],
        )
    gone = delete_sample_data(t.db, get_store(t.db), t.org)
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=SAMPLE_DATA_DELETED,
        entity_type="organization",
        entity_id=t.org_id,
        after={"deleted": dict(gone)},
    )
    t.db.commit()
    return schemas.SampleDataDeleted(deleted=dict(gone))


@router.get("/service-status", response_model=schemas.ServiceStatus)
def service_status(t: TenantDep) -> schemas.ServiceStatus:
    """What this deployment has switched on, in terms of what a customer can rely on: are alert
    e-mails really sent, are containers tracked without typing, is the worker alive, were the backups
    checked. An alert that is logged instead of sent looks, from every other screen, exactly like one
    that was sent. States and codes only: never a key, never a variable name."""
    found = checks(get_settings(), job_runs(t.db.connection()))
    return schemas.ServiceStatus(
        checked_at=datetime.now(UTC),
        checks=[schemas.ServiceCheck.model_validate(c) for c in found if c.audience == "customer"],
    )
