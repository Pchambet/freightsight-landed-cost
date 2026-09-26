"""Clerk JWT verification through JWKS. The only module that knows Clerk's claim layout."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.errors import Unauthorized

_ROLE_MAP = {"org:admin": "ADMIN", "admin": "ADMIN", "org:member": "MEMBER", "member": "MEMBER"}


@dataclass(frozen=True)
class VerifiedClaims:
    user_external_id: str
    email: str | None
    name: str | None
    org_external_id: str | None
    org_slug: str | None
    org_name: str | None
    role: str  # OWNER | ADMIN | MEMBER | VIEWER


#: How long to wait for Clerk's public keys. PyJWT's own default is 30 seconds, which is a long
#: time to hold a request that cannot be authenticated without them: this fetch is on the path of
#: every signed-in call, and a cache miss (a key rotation, a cold worker) puts it in front of a
#: person waiting. The document is a few kilobytes from a CDN — five seconds or something is wrong.
JWKS_TIMEOUT_SECONDS = 5.0


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, timeout=JWKS_TIMEOUT_SECONDS)


class ClerkVerifier:
    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        audience: str | None = None,
        authorized_parties: list[str] | None = None,
    ):
        self.jwks_url, self.issuer, self.audience = jwks_url, issuer, audience
        self.authorized_parties = [p.rstrip("/") for p in (authorized_parties or [])]

    def verify(self, token: str) -> VerifiedClaims:
        try:
            key = _jwks_client(self.jwks_url).get_signing_key_from_jwt(token)
            claims: dict[str, Any] = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                issuer=self.issuer,
                audience=self.audience,
                options={"verify_aud": self.audience is not None},
                leeway=10,  # tokens live 60 s; Clerk's own SDK allows 5 s of skew
            )
        except jwt.PyJWTError as e:
            raise Unauthorized(f"Invalid token: {e}") from e

        if claims.get("sts") == "pending":
            raise Unauthorized("Session pending: choose an organization first")
        azp = str(claims.get("azp", "")).rstrip("/")
        if self.authorized_parties and azp not in self.authorized_parties:
            raise Unauthorized("Token was issued to an unknown origin")

        org = claims.get("o") or {}
        org_id = org.get("id") or claims.get("org_id")
        role_raw = org.get("rol") or claims.get("org_role") or "member"
        return VerifiedClaims(
            user_external_id=str(claims["sub"]),
            email=claims.get("email"),
            name=claims.get("name"),
            org_external_id=org_id,
            org_slug=org.get("slg") or claims.get("org_slug"),
            org_name=claims.get("org_name"),  # custom session claim {{org.name}}, see ops doc
            role=_ROLE_MAP.get(str(role_raw).lower(), "MEMBER"),
        )
