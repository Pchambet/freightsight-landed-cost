"""Tenant-scoped data access: belt (application filter) and braces (Postgres Row Level Security)."""

from __future__ import annotations

from typing import Annotated, Any, TypeVar
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import Select, event, select, text
from sqlalchemy.orm import Session

from app.core.auth import DbDep, Principal, PrincipalDep
from app.core.errors import NotFound, TenantViolation
from app.core.observability import bind_tenant
from app.domain.models import Organization

T = TypeVar("T")

# Postgres session variable read by every RLS policy.
#
# A request may span several pooled connections (every commit returns the connection to the pool and
# the checkin hook resets the variable), so the value is remembered on the Session and re-applied at
# the start of every transaction, transaction-scoped (`is_local = true`), by the listener below.
ORG_GUC = "app.org_id"
#: The other transaction-local setting: the hash of a share token, which the read-only token policy
#: of `shared_reports` compares with (migration 0025). Reset at checkin like the first.
SHARE_TOKEN_GUC = "app.share_token_hash"
_SESSION_KEY = "org_id"


def set_current_org(db: Session, org_id: UUID | None) -> None:
    db.info[_SESSION_KEY] = str(org_id) if org_id else ""
    db.execute(text(f"SELECT set_config('{ORG_GUC}', :v, true)"), {"v": db.info[_SESSION_KEY]})


@event.listens_for(Session, "after_begin")
def _apply_org_on_begin(session: Session, transaction: Any, connection: Any) -> None:
    value = session.info.get(_SESSION_KEY)
    if value:
        connection.execute(text(f"SELECT set_config('{ORG_GUC}', :v, true)"), {"v": value})


class TenantSession:
    def __init__(self, db: Session, principal: Principal, org: Organization):
        self.db = db
        self.principal = principal
        self.org = org
        self.org_id = org.id

    def q(self, model: type[T]) -> Select[tuple[T]]:
        return select(model).where(model.org_id == self.org_id)  # type: ignore[attr-defined]

    def get_or_404(self, model: type[T], id: UUID, what: str | None = None) -> T:
        obj = self.db.scalar(self.q(model).where(model.id == id))  # type: ignore[attr-defined]
        if obj is None:
            raise NotFound(what or model.__name__)
        return obj

    def add(self, obj: T) -> T:
        current = getattr(obj, "org_id", None)
        if current not in (None, self.org_id):
            raise TenantViolation("Object belongs to another organization")
        obj.org_id = self.org_id  # type: ignore[attr-defined]
        self.db.add(obj)
        return obj


def get_tenant(request: Request, db: DbDep, principal: PrincipalDep) -> TenantSession:
    # Both, deliberately: the context variables serve any log line written from this dependency's
    # own thread, and request.state is what survives back to the middleware — a sync dependency runs
    # in a worker thread, whose context variables are a copy the caller never sees again.
    bind_tenant(org_id=principal.org_id, user_id=principal.user_id)
    request.state.org_id = principal.org_id
    request.state.user_id = principal.user_id
    set_current_org(db, principal.org_id)
    org = db.get(Organization, principal.org_id)
    if org is None:
        raise NotFound("Organization")
    return TenantSession(db, principal, org)


TenantDep = Annotated[TenantSession, Depends(get_tenant)]
