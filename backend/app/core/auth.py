"""Who is calling, and for which organization.

Order of resolution:
1. `Authorization: Bearer <Clerk JWT>` when Clerk is configured: verified via JWKS, user/org/membership
   mirrored locally (the local membership is what authorises).
2. `X-Org-Id` (+ optional `X-User-Email`) as a development principal with role OWNER — only where
   `ALLOW_DEV_PRINCIPAL` is on *and* the environment is not prod.
3. Otherwise 401.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.auth_clerk import VerifiedClaims
from app.core.db import get_db
from app.core.errors import Conflict, Forbidden, Unauthorized
from app.core.settings import Settings, get_settings
from app.domain.models import MemberRole, Membership, Organization, User

SettingsDep = Annotated[Settings, Depends(get_settings)]
DbDep = Annotated[Session, Depends(get_db)]


@dataclass(frozen=True)
class Principal:
    org_id: UUID
    user_id: UUID | None
    role: MemberRole
    via: Literal["jwt", "dev"]


def _verifier(settings: Settings):  # type: ignore[no-untyped-def]
    from app.adapters.auth_clerk import ClerkVerifier

    assert settings.clerk_jwks_url and settings.clerk_issuer
    return ClerkVerifier(
        settings.clerk_jwks_url,
        settings.clerk_issuer,
        settings.clerk_audience,
        settings.clerk_authorized_parties,
    )


def _principal_from_jwt(db: Session, settings: Settings, token: str) -> Principal:
    claims = _verifier(settings).verify(token)
    if not claims.org_external_id:
        raise Forbidden("Select an organization first")
    # The first requests of a new user arrive in parallel (layout + page), so two of them may race
    # to create the same mirror rows. On a unique violation, roll back and read what the other wrote.
    for attempt in range(3):
        try:
            return _mirror_and_resolve(db, settings, claims, allow_slug=attempt == 0)
        except IntegrityError:
            db.rollback()
    raise Conflict("Could not resolve the organization membership, retry")


def _mirror_and_resolve(
    db: Session, settings: Settings, claims: VerifiedClaims, allow_slug: bool
) -> Principal:
    user = db.scalar(select(User).where(User.external_id == claims.user_external_id))
    if user is None:
        user = User(external_id=claims.user_external_id, email=claims.email or "", name=claims.name)
        db.add(user)
    else:
        user.email = claims.email or user.email
        user.name = claims.name or user.name

    org = db.scalar(select(Organization).where(Organization.external_id == claims.org_external_id))
    if org is None:
        slug = claims.org_slug if allow_slug else None
        if slug and db.scalar(select(Organization.id).where(Organization.slug == slug)):
            slug = None  # slug already taken by another mirrored org; the external id is the identity
        org = Organization(
            name=claims.org_name or claims.org_slug or "New organization",
            slug=slug,
            external_id=claims.org_external_id,
            base_currency=settings.default_base_currency,
        )
        db.add(org)
    elif claims.org_name and org.name in (claims.org_slug, "New organization"):
        org.name = claims.org_name  # upgrade a slug-named mirror once the claim is available
    db.flush()

    membership = db.get(Membership, (org.id, user.id))
    role = MemberRole(claims.role)
    if membership is None:
        membership = Membership(org_id=org.id, user_id=user.id, role=role)
        db.add(membership)
    elif membership.role != role and membership.role != MemberRole.OWNER:
        membership.role = role
    db.commit()
    return Principal(org_id=org.id, user_id=user.id, role=membership.role, via="jwt")


def _principal_from_dev_header(db: Session, settings: Settings, org_id: UUID) -> Principal:
    org = db.get(Organization, org_id)
    if org is None:
        org = Organization(id=org_id, name="Dev organization", base_currency=settings.default_base_currency)
        db.add(org)
        db.commit()
    return Principal(org_id=org.id, user_id=None, role=MemberRole.OWNER, via="dev")


def get_principal(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    x_org_id: Annotated[UUID | None, Header(alias="X-Org-Id")] = None,
) -> Principal:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and settings.clerk_enabled:
        return _principal_from_jwt(db, settings, auth[7:].strip())
    # Two switches, and the header needs both. `APP_ENV` on its own was the whole barrier between
    # production and a mode where an organisation id — which is not a secret; it is in /me, in
    # tickets, in logs — reads and writes everything a tenant has. It lives in a dashboard, where a
    # clone, a restore or a typo ("production", "Prod") silently demotes it to its "dev" default.
    # `allow_dev_principal` is off by default, so losing `APP_ENV` now costs nothing.
    if x_org_id is not None and settings.allow_dev_principal and not settings.is_prod:
        return _principal_from_dev_header(db, settings, x_org_id)
    if auth.lower().startswith("bearer "):
        raise Unauthorized("Authentication is not configured on this server")
    raise Unauthorized("Missing credentials")


PrincipalDep = Annotated[Principal, Depends(get_principal)]


def require_role(*roles: MemberRole):  # type: ignore[no-untyped-def]
    def dep(p: PrincipalDep) -> Principal:
        if p.role not in roles:
            raise Forbidden(f"Requires one of: {', '.join(r.value for r in roles)}", code="FORBIDDEN_ROLE")
        return p

    return dep


WRITE_ROLES = (MemberRole.OWNER, MemberRole.ADMIN, MemberRole.MEMBER)
