"""A landed-cost report, sent by link to somebody who has no account."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response, status

from app.api.v1 import schemas
from app.api.v1.deps import WriterDep
from app.api.v1.landed_costs import _serialize
from app.api.v1.reports import _audit_schema
from app.core.auth import DbDep
from app.core.errors import NotFound, TooManyRequests
from app.core.ratelimit import SlidingWindow, client_address
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import (
    AUDIT_SHARED,
    CONTAINER_SHARED,
    PURCHASE_ORDER_SHARED,
    SHARE_REVOKED,
    record,
)
from app.domain.costing import report as rpt
from app.domain.costing.service import compute
from app.domain.models import Container, PurchaseOrder, SharedReport
from app.domain.reporting.audit import PUBLIC_PARAMS, Audit, audit_report, check_period
from app.domain.sample_registry import has_sample_data
from app.domain.shares import service as shares

router = APIRouter(tags=["shares"])
ZERO = Decimal("0.00")

#: Links one organization may create in an hour. A person shares a handful a day; a loop sharing a
#: thousand would hold the one API process every other customer is waiting on, and fill a table.
LINKS_PER_HOUR = 60
_created = SlidingWindow(limit=LINKS_PER_HOUR, seconds=3600)


def _room_for_a_link(t: TenantSession) -> None:
    """Checked once the subject is known to exist and before its report is computed, which is the
    expensive part: a refused link costs nothing, and a 404 does not use up the hour."""
    if not _created.allow(str(t.org_id)):
        raise TooManyRequests("Too many links created in the last hour", code="RATE_LIMITED")


def _summaries(t: TenantSession, found: list[SharedReport]) -> list[schemas.ShareSummary]:
    names = shares.creator_names(t.db, found)
    opened = shares.views_of(t.db, t.org_id, [share.id for share in found])
    now = datetime.now(UTC)
    out = []
    for share in found:
        summary = schemas.ShareSummary.model_validate(share)
        summary.created_by_name = names.get(share.created_by) if share.created_by else None
        summary.status = shares.status_of(share, now)  # type: ignore[assignment]
        summary.view_count, summary.last_viewed_at = opened.get(share.id, (0, None))
        out.append(summary)
    return out


def _frozen_report(report: rpt.Report, redact: bool) -> dict[str, Any]:
    frozen = _serialize(report).model_dump(mode="json")
    for cost in frozen["costs"]:
        # Free text written for colleagues never leaves, whatever the sender chose.
        cost["notes"] = cost["close_reason"] = None
        if redact:
            # Taken out of the snapshot, not hidden by the page: what is not stored cannot be read.
            cost["vendor"] = cost["invoice_number"] = None
    return frozen


def _frozen_audit(found: Audit, redact: bool) -> dict[str, Any]:
    frozen = _audit_schema(found).model_dump(mode="json")
    if redact:
        # Out of the snapshot itself: the forwarders and their invoice numbers on every finding, any
        # param not known to name nobody, and the suppliers the goods come from — whom an importer
        # buys from is as much its own business as whom it ships with. Prices and margins stay: they
        # are what the document is about, and the sender is told so before sending.
        for finding in frozen["findings"]:
            finding["vendor"] = finding["invoice_number"] = None
            finding["params"] = {k: v for k, v in finding["params"].items() if k in PUBLIC_PARAMS}
        frozen["coefficient_by_supplier"] = []
    return frozen


def _share(
    t: TenantSession,
    actor: UUID | None,
    payload: schemas.ShareCreate,
    *,
    subject_type: shares.SubjectType,
    subject_id: UUID,
    label: str,
    shipment_reference: str | None,
    report: rpt.Report | None = None,
    audit: Audit | None = None,
    action: str,
) -> schemas.ShareCreated:
    now = datetime.now(UTC)
    snapshot = {
        "issuer": {"name": t.org.name},
        "subject_type": subject_type,
        "subject_label": label,
        "shipment_reference": shipment_reference,
        "locale": t.org.locale,
        "generated_at": now.isoformat(),
        # Said on the document itself: a report sent from a demo company must not pass for a real file.
        "sample_data": has_sample_data(t.db, t.org_id),
        "redacted": payload.redact_vendors,
        "report": _frozen_report(report, payload.redact_vendors) if report is not None else None,
        "audit": _frozen_audit(audit, payload.redact_vendors) if audit is not None else None,
    }
    landed = report.landed if report is not None else (audit.completeness.landed if audit else ZERO)
    created = shares.create(
        t.db,
        t.org_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=label,
        snapshot=snapshot,
        expires_in_days=payload.expires_in_days,
        created_by=actor,
    )
    record(
        t.db,
        t.org_id,
        actor_user_id=actor,
        action=action,
        entity_type=subject_type,
        entity_id=subject_id,
        after={
            "share_id": created.share.id,
            "expires_at": created.share.expires_at,
            "label": label,
            "redacted": payload.redact_vendors,
        },
    )
    t.db.commit()
    return schemas.ShareCreated(
        id=created.share.id,
        token=created.token,
        path=f"/r/{created.token}",
        expires_at=created.share.expires_at,
        landed=landed,
        generated_at=now,
    )


@router.post(
    "/containers/{container_id}/share",
    response_model=schemas.ShareCreated,
    status_code=status.HTTP_201_CREATED,
)
def share_container(
    container_id: UUID, payload: schemas.ShareCreate, t: TenantDep, p: WriterDep
) -> schemas.ShareCreated:
    """Freeze this container's landed-cost report as it stands and return a link to it. The token is
    in this answer and nowhere else: only its hash is kept."""
    container = t.get_or_404(Container, container_id, "Container")
    _room_for_a_link(t)
    report = rpt.container_report(compute(t.db, t.org), t.org.base_currency, container_id)
    return _share(
        t,
        p.user_id,
        payload,
        subject_type="container",
        subject_id=container_id,
        label=container.container_number,
        shipment_reference=container.shipment.reference if container.shipment else None,
        report=report,
        action=CONTAINER_SHARED,
    )


@router.post(
    "/purchase-orders/{po_id}/share", response_model=schemas.ShareCreated, status_code=status.HTTP_201_CREATED
)
def share_purchase_order(
    po_id: UUID, payload: schemas.ShareCreate, t: TenantDep, p: WriterDep
) -> schemas.ShareCreated:
    order = t.get_or_404(PurchaseOrder, po_id, "Purchase order")
    _room_for_a_link(t)
    report = rpt.purchase_order_report(compute(t.db, t.org), t.org.base_currency, po_id)
    return _share(
        t,
        p.user_id,
        payload,
        subject_type="purchase_order",
        subject_id=po_id,
        label=order.po_number,
        shipment_reference=None,
        report=report,
        action=PURCHASE_ORDER_SHARED,
    )


@router.post("/reports/audit/share", response_model=schemas.ShareCreated, status_code=status.HTTP_201_CREATED)
def share_audit(payload: schemas.AuditShareCreate, t: TenantDep, p: WriterDep) -> schemas.ShareCreated:
    """Freeze the audit of a period as it stands and return a link to it: the document of the paid
    offer, sent to somebody who has no account. The subject of the share is the organization itself.
    With `redact_vendors`, forwarders, invoice numbers and suppliers are left out of the snapshot."""
    check_period(payload.period_from, payload.period_to)
    _room_for_a_link(t)
    found = audit_report(t.db, compute(t.db, t.org), t.org, payload.period_from, payload.period_to)
    return _share(
        t,
        p.user_id,
        payload,
        subject_type="audit",
        subject_id=t.org_id,
        label=f"{payload.period_from.isoformat()}..{payload.period_to.isoformat()}",
        shipment_reference=None,
        audit=found,
        action=AUDIT_SHARED,
    )


@router.get("/reports/audit/shares", response_model=list[schemas.ShareSummary])
def audit_shares(t: TenantDep) -> list[schemas.ShareSummary]:
    return _summaries(t, shares.shares_of(t.db, t.org_id, "audit", t.org_id))


@router.get("/containers/{container_id}/shares", response_model=list[schemas.ShareSummary])
def container_shares(container_id: UUID, t: TenantDep) -> list[schemas.ShareSummary]:
    t.get_or_404(Container, container_id, "Container")
    return _summaries(t, shares.shares_of(t.db, t.org_id, "container", container_id))


@router.get("/purchase-orders/{po_id}/shares", response_model=list[schemas.ShareSummary])
def purchase_order_shares(po_id: UUID, t: TenantDep) -> list[schemas.ShareSummary]:
    t.get_or_404(PurchaseOrder, po_id, "Purchase order")
    return _summaries(t, shares.shares_of(t.db, t.org_id, "purchase_order", po_id))


@router.delete("/shares/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_share(share_id: UUID, t: TenantDep, p: WriterDep) -> None:
    """The link stops working at once. The row stays: the history of what was sent, and when."""
    share = t.get_or_404(SharedReport, share_id, "Share")
    if share.revoked_at is None:
        share.revoked_at = datetime.now(UTC)
        record(
            t.db,
            t.org_id,
            actor_user_id=p.user_id,
            action=SHARE_REVOKED,
            entity_type=share.subject_type,
            entity_id=share.subject_id,
            before={"share_id": share.id, "label": share.subject_label},
        )
    t.db.commit()


#: The only unauthenticated route that touches the tenants' database. Nobody can guess a token, so
#: this is not about secrecy: it keeps a flood from turning into a stream of transactions.
_public_limit = SlidingWindow(limit=120, seconds=60)


@router.post("/public/shared-reports/open", response_model=schemas.SharedReportPublic)
def open_shared_report(
    payload: schemas.ShareOpen, request: Request, db: DbDep, response: Response
) -> schemas.SharedReportPublic:
    """No account, no organization: the token is the whole of the right, and what it opens is the
    snapshot taken when the link was made. Unknown, expired and revoked are the same 404 — an answer
    that told them apart would tell a stranger which tokens once existed.

    The token travels in the body, never in the URL: a URL is what access logs, error reports and
    proxies keep, and this one is a bearer credential.
    """
    if not _public_limit.allow(client_address(request)):
        raise TooManyRequests("Too many requests", code="RATE_LIMITED")
    found = shares.open_by_token(db, payload.token)
    if found is None:
        db.rollback()
        raise NotFound("Shared report", code="SHARE_NOT_FOUND")
    snapshot, expires_at = found
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Referrer-Policy"] = "no-referrer"
    return schemas.SharedReportPublic(**snapshot, expires_at=expires_at)
