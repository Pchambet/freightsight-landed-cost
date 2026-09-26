"""Taking a costs file back.

A prospect's ledger is imported to be audited; the wrong export, a month too many, a column mapped
the wrong way round, and the landed costs of a whole quarter are wrong. Taking the file back deletes
exactly what it wrote — the costs carry the import they came from — and the estimates those costs had
replaced stand again. Nothing else is touched.

The line is drawn where the confirmation of an invoice draws it: once a cost has gone into the
customer's own books, through an ERP document, or into a closed month, deleting it here would leave
their valuation or their close carrying a figure nothing on our side remembers. Those are refused, by
name, and corrected the way a posted document is — with another one.

And a later copy of the same ledger that recognised some of these lines, and left them alone because
this file had written them, stands on them: taking this file back would take those lines out of the
books while the later file says they are in. That later file is taken back first, by name.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.errors import Conflict, Unprocessable
from app.core.tenancy import TenantSession
from app.domain.costing.service import recompute_org
from app.domain.invoices.checks import note_field
from app.domain.invoices.service import _live_pushes_carrying
from app.domain.models import ContainerLoad, Cost, CostAllocation, ImportJob, ImportKind, ImportStatus
from app.domain.periods.service import frozen_periods


def undoable(job: ImportJob, *, synced: bool) -> bool:
    """A costs file, committed, dropped by a person, not taken back yet. What happened to its costs
    since is for `undo` to refuse, by name."""
    return (
        job.kind is ImportKind.COSTS
        and job.status is ImportStatus.DONE
        and job.undone_at is None
        and not synced
    )


def undo(t: TenantSession, job: ImportJob, *, synced: bool) -> tuple[list[UUID], int]:
    """Delete every cost the file wrote, in the caller's transaction. Returns the costs deleted and how
    many estimates stand again."""
    if job.undone_at is not None:
        raise Conflict(
            "This import was already taken back",
            code="IMPORT_ALREADY_UNDONE",
            undone_at=job.undone_at.isoformat(),
        )
    if not undoable(job, synced=synced):
        raise Unprocessable(
            "Only a costs file a person committed can be taken back",
            code="IMPORT_NOT_UNDOABLE",
            kind=job.kind.value,
            status=job.status.value,
        )
    costs = list(t.db.scalars(t.q(Cost).where(Cost.import_job_id == job.id)))
    mine = {str(cost.id) for cost in costs}
    relying = [
        later
        for later in t.db.scalars(
            t.q(ImportJob)
            .where(
                ImportJob.id != job.id,
                ImportJob.kind == ImportKind.COSTS,
                ImportJob.status == ImportStatus.DONE,
                ImportJob.undone_at.is_(None),
            )
            .order_by(ImportJob.committed_at)
        )
        if mine & set(later.matched_cost_ids or [])
    ]
    if relying:
        raise Unprocessable(
            "A later costs file recognised some of these lines and left them to this one; take it back first",
            code="IMPORT_RELIED_ON",
            import_ids=[str(later.id) for later in relying],
            files=[later.original_filename or "" for later in relying],
        )
    pushed = _live_pushes_carrying(t.db, t.org, costs)
    if pushed:
        raise Unprocessable(
            "Some of these costs are in the ERP; correct them there with an adjustment document, or "
            "forget the push first",
            code="COSTS_PUSHED_TO_ERP",
            errors=[
                {
                    "field": "erp",
                    "code": "COSTS_PUSHED_TO_ERP",
                    "message": (
                        f"@pushed_cost|{note_field(push.odoo_name or str(push.odoo_id))}|{push.status}"
                    ),
                }
                for push in pushed
            ],
        )
    frozen = frozen_periods(t.db, t.org_id)
    ids = [cost.id for cost in costs]
    boxes = {cost.container_id for cost in costs if cost.container_id is not None}
    if ids:
        boxes |= set(
            t.db.scalars(
                select(ContainerLoad.container_id)
                .join(CostAllocation, CostAllocation.container_load_id == ContainerLoad.id)
                .where(CostAllocation.cost_id.in_(ids))
            )
        )
    months = sorted({frozen[box] for box in boxes if box in frozen})
    if months:
        raise Unprocessable(
            "Some of these costs are in a closed month; reopen it first",
            code="IMPORT_PERIOD_CLOSED",
            months=months,
        )
    reopened = sum(1 for cost in costs if cost.supersedes_cost_id is not None)
    for cost in costs:
        t.db.delete(cost)
    job.undone_at = datetime.now(UTC)
    t.db.flush()
    recompute_org(t.db, t.org)
    return ids, reopened
