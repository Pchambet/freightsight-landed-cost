"""Sharing a landed-cost report with somebody who has no account.

Two things make it safe to hand out. The link is a random 256-bit token of which only the SHA-256 is
stored: the database cannot be read back into links. And what the link opens is a **snapshot**, taken
when it was created: it cannot be used to watch a company's figures move, and the person it was sent
to sees the number they were sent.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.tenancy import SHARE_TOKEN_GUC
from app.domain.models import SharedReport, SharedReportView, User

SubjectType = Literal["container", "purchase_order", "audit"]
#: The setting the token policy of `shared_reports` reads (migration 0025).
TOKEN_GUC = SHARE_TOKEN_GUC


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Created:
    share: SharedReport
    #: Shown once, to whoever created the link, and never stored.
    token: str


def create(
    db: Session,
    org_id: UUID,
    *,
    subject_type: SubjectType,
    subject_id: UUID,
    subject_label: str,
    snapshot: dict[str, Any],
    expires_in_days: int,
    created_by: UUID | None,
) -> Created:
    token = secrets.token_urlsafe(32)
    share = SharedReport(
        org_id=org_id,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label,
        token_hash=hash_token(token),
        snapshot=snapshot,
        created_by=created_by,
        expires_at=datetime.now(UTC) + timedelta(days=expires_in_days),
    )
    db.add(share)
    db.flush()
    return Created(share, token)


def status_of(share: SharedReport, now: datetime | None = None) -> str:
    if share.revoked_at is not None:
        return "revoked"
    return "expired" if share.expires_at <= (now or datetime.now(UTC)) else "active"


def shares_of(db: Session, org_id: UUID, subject_type: SubjectType, subject_id: UUID) -> list[SharedReport]:
    return list(
        db.scalars(
            select(SharedReport)
            .where(
                SharedReport.org_id == org_id,
                SharedReport.subject_type == subject_type,
                SharedReport.subject_id == subject_id,
            )
            .order_by(SharedReport.created_at.desc())
        )
    )


def creator_names(db: Session, shares: list[SharedReport]) -> dict[UUID, str]:
    ids = {s.created_by for s in shares if s.created_by}
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.name, User.email).where(User.id.in_(ids)))
    return {user_id: name or email for user_id, name, email in rows}


#: What `secrets.token_urlsafe(32)` produces: 43 URL-safe characters. Anything else is not one of ours,
#: and is answered without a database round trip.
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def open_by_token(db: Session, token: str) -> tuple[dict[str, Any], datetime] | None:
    """The snapshot behind a token, counted as opened — or None for a token that is unknown, expired
    or revoked, which are one and the same answer to whoever is asking.

    There is no tenant here. The row is reached through the token policy of the table, which is for
    reading only: the hash is set for this transaction, and the policy lets through the row that
    carries it and no other. The opening is recorded as a new row of `shared_report_views`, the one
    thing a token is allowed to write.
    """
    if not TOKEN_SHAPE.match(token):
        return None
    hashed = hash_token(token)
    db.execute(text(f"SELECT set_config('{TOKEN_GUC}', :h, true)"), {"h": hashed})
    try:
        row = db.execute(
            text(
                "SELECT id, org_id, snapshot, expires_at FROM shared_reports "
                "WHERE token_hash = :h AND revoked_at IS NULL AND expires_at > now()"
            ),
            {"h": hashed},
        ).one_or_none()
        if row is None:
            return None
        db.execute(
            text("INSERT INTO shared_report_views (id, org_id, share_id) VALUES (:id, :org, :share)"),
            {"id": uuid4(), "org": row.org_id, "share": row.id},
        )
        return row.snapshot, row.expires_at
    finally:
        db.execute(text(f"SELECT set_config('{TOKEN_GUC}', '', true)"))


def views_of(db: Session, org_id: UUID, share_ids: list[UUID]) -> dict[UUID, tuple[int, datetime | None]]:
    """How many times each share was opened, and when last."""
    if not share_ids:
        return {}
    rows = db.execute(
        select(SharedReportView.share_id, func.count(), func.max(SharedReportView.viewed_at))
        .where(SharedReportView.org_id == org_id, SharedReportView.share_id.in_(share_ids))
        .group_by(SharedReportView.share_id)
    )
    return {share_id: (count, last) for share_id, count, last in rows}
