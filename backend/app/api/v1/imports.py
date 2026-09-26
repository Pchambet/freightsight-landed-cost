from __future__ import annotations

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.v1 import schemas
from app.api.v1.deps import FxDep, WriterDep
from app.core.errors import Conflict, NotFound, Unprocessable
from app.core.tenancy import TenantDep, TenantSession
from app.domain.audit.service import IMPORT_COMMITTED, IMPORT_UNDONE, record
from app.domain.costing.entry import lock_books
from app.domain.imports import service, undo
from app.domain.imports.fields import FIELDS, KIND_ORDER, REQUIRED_ONE_OF
from app.domain.models import Cost, ErpSyncRun, ImportJob, ImportKind, ImportStatus

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _synced(t: TenantSession, jobs: list[ImportJob]) -> set[UUID]:
    """The jobs an ERP sync drove. An ERP sync writes a file too, and without this the imports list
    shows the nightly Odoo sync as "odoo-sync.csv", as if someone had dropped it by hand. The sync run
    that drove the job is the record of it, so nothing has to be stored on the job."""
    if not jobs:
        return set()
    return set(
        t.db.scalars(
            select(ErpSyncRun.import_job_id)
            .where(ErpSyncRun.org_id == t.org_id)
            .where(ErpSyncRun.import_job_id.in_([j.id for j in jobs]))
        )
    )


def _responses(t: TenantSession, jobs: list[ImportJob]) -> list[schemas.ImportJobResponse]:
    synced = _synced(t, jobs)
    return [
        schemas.ImportJobResponse.model_validate(job).model_copy(
            update={
                "source": "erp_sync" if job.id in synced else "upload",
                "can_undo": undo.undoable(job, synced=job.id in synced),
                "preview_key": service.preview_key(job) if job.kind in BOUND else None,
            }
        )
        for job in jobs
    ]


@router.get("", response_model=list[schemas.ImportJobResponse])
def list_imports(t: TenantDep) -> list[schemas.ImportJobResponse]:
    jobs = list(t.db.scalars(t.q(ImportJob).order_by(ImportJob.created_at.desc()).limit(100)))
    return _responses(t, jobs)


@router.get("/kinds", response_model=list[schemas.ImportKindInfo])
def import_kinds(_: TenantDep) -> list[schemas.ImportKindInfo]:
    """What each kind of file reads, in the order a quarter is loaded — the catalogue first, since it
    lends its rates only to the order lines written after it — with the header each column carries
    in either language. A template written with these headers maps itself."""
    return [
        schemas.ImportKindInfo(
            kind=kind,
            fields=[
                schemas.ImportFieldInfo(
                    field=field.name,
                    required=field.required,
                    header_fr=field.header_fr,
                    header_en=field.header_en,
                )
                for field in FIELDS[kind]
            ],
            required_one_of=[list(group) for group in REQUIRED_ONE_OF.get(kind, ())],
        )
        for kind in KIND_ORDER
    ]


@router.post("", response_model=schemas.ImportJobResponse, status_code=status.HTTP_201_CREATED)
async def create_import(
    t: TenantDep,
    _: WriterDep,
    kind: Annotated[ImportKind, Form()] = ImportKind.PURCHASE_ORDERS,
    file: UploadFile = File(...),
) -> ImportJob:
    """Upload a CSV/XLSX. Returns detected columns and a suggested (or remembered) mapping.

    Nothing is written yet.
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise Unprocessable("File larger than 10 MB", code="FILE_TOO_LARGE")
    if not content.strip():
        raise Unprocessable("File is empty", code="EMPTY_FILE")
    job = service.create_job(t, kind, content, file.filename)
    t.db.commit()
    t.db.refresh(job)
    return job


@router.get("/{import_id}", response_model=schemas.ImportJobResponse)
def get_import(import_id: UUID, t: TenantDep) -> schemas.ImportJobResponse:
    return _responses(t, [t.get_or_404(ImportJob, import_id, "Import")])[0]


#: Kinds whose commit is bound to their preview: what a person approved is what is written.
BOUND = (ImportKind.CONTAINERS, ImportKind.COSTS, ImportKind.PRODUCTS)


def _locked(t: TenantSession, import_id: UUID) -> ImportJob:
    """The job, locked: one preview or commit of a file at a time."""
    job = t.db.scalar(t.q(ImportJob).where(ImportJob.id == import_id).with_for_update())
    if job is None:
        raise NotFound("Import")
    return job


def _run(t: TenantSession, job: ImportJob, run: Callable[[], object]) -> None:
    """Run the file; a line the database refuses as one already on file is said, not a 500. The
    reading refuses those lines by name first — this is the case it could not see."""
    try:
        run()
    except IntegrityError as e:
        t.db.rollback()
        if "uq_costs_invoice_line" not in str(e.orig):
            raise
        raise Conflict(
            "A line of this file is already on file; preview it again", code="INVOICE_LINE_ALREADY_RECORDED"
        ) from e


@router.post("/{import_id}/validate", response_model=schemas.ImportJobResponse)
def validate_import(
    import_id: UUID, payload: schemas.ImportValidateRequest, t: TenantDep, fx: FxDep, _: WriterDep
) -> schemas.ImportJobResponse:
    """Dry run: the commit code path under a SAVEPOINT that is rolled back.

    The report is exactly what commit would do. For containers, costs and products the job keeps what
    the preview ran with — the mapping, and for costs the type of the rows no label types and the
    types a person gave the labels it could not read — and the commit runs with exactly that.
    """
    job = _locked(t, import_id)
    service.ensure_not_done(job)
    job.options = (
        {
            "default_cost_type": payload.default_cost_type.value if payload.default_cost_type else None,
            "label_types": {label: kind.value for label, kind in payload.label_types.items()},
            "force_duplicates": payload.force_duplicates,
        }
        if job.kind is ImportKind.COSTS
        else {}
    )
    try:
        _run(t, job, lambda: service.run_job(t, job, payload.mapping, dry_run=True, fx=fx))
    except Unprocessable as e:
        job.mapping = payload.mapping
        job.status = ImportStatus.PARSED  # a preview that failed is no preview to commit
        job.report = {"error": e.message, "code": e.code, **e.extra}
        t.db.commit()
        raise
    t.db.commit()
    t.db.refresh(job)
    return _responses(t, [job])[0]


@router.delete("/{import_id}", response_model=schemas.ImportUndoResponse)
def undo_import(import_id: UUID, t: TenantDep, p: WriterDep) -> schemas.ImportUndoResponse:
    """Take a costs file back: every cost it wrote is deleted, and the estimates they had replaced
    stand again. Refused by name once a cost is in the ERP (COSTS_PUSHED_TO_ERP) or in a closed month
    (IMPORT_PERIOD_CLOSED, with its `months`); a file taken back once answers IMPORT_ALREADY_UNDONE."""
    job = _locked(t, import_id)
    lock_books(t.db, t.org_id)
    deleted, reopened = undo.undo(t, job, synced=job.id in _synced(t, [job]))
    record(
        t.db,
        t.org_id,
        actor_user_id=p.user_id,
        action=IMPORT_UNDONE,
        entity_type="import",
        entity_id=job.id,
        after={"cost_ids": deleted, "estimates_reopened": reopened},
    )
    t.db.commit()
    return schemas.ImportUndoResponse(costs_deleted=len(deleted), estimates_reopened=reopened)


@router.post("/{import_id}/commit", response_model=schemas.ImportJobResponse)
def commit_import(
    import_id: UUID, payload: schemas.ImportCommitRequest, t: TenantDep, fx: FxDep, p: WriterDep
) -> schemas.ImportJobResponse:
    """Write the file. Containers, costs and products write exactly what the preview a person approved
    showed, named by its `preview_key`: without one, 409 PREVIEW_REQUIRED; when another preview of the
    file replaced it, or the books have moved since and the same file would now do something else,
    nothing is written and 409 PREVIEW_CHANGED carries the new `report` and its `preview_key`."""
    job = _locked(t, import_id)
    service.ensure_not_done(job)
    expected = None
    if job.kind in BOUND:
        if job.status is not ImportStatus.VALIDATED or payload.preview_key is None:
            raise Conflict("Preview the file before importing it", code="PREVIEW_REQUIRED")
        if payload.preview_key != service.preview_key(job):
            # Another preview of the same file — another tab, other options — replaced the one this
            # person saw: they approve the figures it shows, not the file.
            raise Conflict(
                "The file was previewed again since: look at the figures again before importing",
                code="PREVIEW_CHANGED",
                report=dict(job.report),
                preview_key=service.preview_key(job),
            )
        mapping, expected = dict(job.mapping), dict(job.report)
    else:
        mapping = payload.mapping or dict(job.mapping)
    if job.kind is ImportKind.COSTS:
        lock_books(t.db, t.org_id)
    try:
        _run(
            t,
            job,
            lambda: service.run_job(
                t, job, mapping, dry_run=False, on_error=payload.on_error, fx=fx, expected=expected
            ),
        )
    except Conflict as e:
        if e.code != "PREVIEW_CHANGED" or "report" not in e.extra:
            raise
        job.report = dict(e.extra["report"])  # type: ignore[call-overload]
        t.db.commit()
        raise Conflict(
            e.message, code="PREVIEW_CHANGED", report=dict(job.report), preview_key=service.preview_key(job)
        ) from e
    if job.kind is ImportKind.COSTS:
        record(
            t.db,
            t.org_id,
            actor_user_id=p.user_id,
            action=IMPORT_COMMITTED,
            entity_type="import",
            entity_id=job.id,
            after={
                "kind": job.kind.value,
                "cost_ids": list(t.db.scalars(select(Cost.id).where(Cost.import_job_id == job.id))),
                # what a person overruled to import it, if anything
                "force_duplicates": bool((job.options or {}).get("force_duplicates")),
            },
        )
    t.db.commit()
    t.db.refresh(job)
    return _responses(t, [job])[0]
