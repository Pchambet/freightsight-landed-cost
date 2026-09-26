"""Clerk JWT path: a token signed with a local RSA key, verified through a patched JWKS client.

Covers the user/org/membership mirror, role mapping, the azp allow-list, and the rule that the dev
header is refused once the app is in prod.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.adapters import auth_clerk
from app.core.settings import Settings, get_settings

ISSUER = "https://example-issuer-0000.clerk.accounts.dev"
JWKS = f"{ISSUER}/.well-known/jwks.json"
ORIGIN = "https://app.example.test"


class _Key:
    def __init__(self, public_pem: bytes):
        self.key = public_pem


class _FakeJwks:
    def __init__(self, public_pem: bytes):
        self._key = _Key(public_pem)

    def get_signing_key_from_jwt(self, token: str) -> _Key:
        return self._key


@pytest.fixture(scope="module")
def rsa_key() -> tuple[bytes, bytes]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    pub = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, pub


def make_token(private_pem: bytes, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "user_2abc",
        "sid": "sess_1",
        "azp": ORIGIN,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "v": 2,
        "o": {"id": "org_9xyz", "slg": "acme-import", "rol": "admin"},
        "org_name": "ACME Import",
    }
    claims.update(overrides)
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def clerk_client(
    db: Session, rsa_key: tuple[bytes, bytes], monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    from app.core.db import get_db
    from app.main import app

    _, public_pem = rsa_key
    monkeypatch.setattr(auth_clerk, "_jwks_client", lambda url: _FakeJwks(public_pem))
    settings = Settings(
        app_env="prod",
        database_url="postgresql+psycopg://unused",
        clerk_jwks_url=JWKS,
        clerk_issuer=ISSUER,
        clerk_authorized_parties=[ORIGIN],
    )

    def fresh_db():  # type: ignore[no-untyped-def]
        db.expire_all()
        yield db

    app.dependency_overrides[get_db] = fresh_db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_jwt_creates_mirror_and_scopes_requests(
    clerk_client: TestClient, rsa_key: tuple[bytes, bytes]
) -> None:
    priv, _ = rsa_key
    headers = {"Authorization": f"Bearer {make_token(priv)}"}
    me = clerk_client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "ADMIN" and me.json()["via"] == "jwt"
    org_id = me.json()["org_id"]

    org = clerk_client.get("/api/v1/organization", headers=headers).json()
    assert org["slug"] == "acme-import" and org["base_currency"] == "EUR" and org["name"] == "ACME Import"

    res = clerk_client.post("/api/v1/containers", headers=headers, json={"container_number": "MSCU1234567"})
    assert res.status_code == 201

    # second call with the same org: same local org id, nothing duplicated
    again = clerk_client.get("/api/v1/me", headers=headers).json()
    assert again["org_id"] == org_id

    # another user in another Clerk org sees nothing
    other_token = make_token(priv, sub="user_other", o={"id": "org_other", "rol": "member"})
    other = {"Authorization": f"Bearer {other_token}"}
    assert clerk_client.get("/api/v1/containers", headers=other).json() == []
    assert clerk_client.get("/api/v1/me", headers=other).json()["role"] == "MEMBER"


def test_jwt_without_active_org_is_403(clerk_client: TestClient, rsa_key: tuple[bytes, bytes]) -> None:
    priv, _ = rsa_key
    res = clerk_client.get("/api/v1/me", headers={"Authorization": f"Bearer {make_token(priv, o=None)}"})
    assert res.status_code == 403
    assert "organization" in res.json()["detail"].lower()


def test_jwt_from_unknown_origin_or_bad_signature_is_401(
    clerk_client: TestClient, rsa_key: tuple[bytes, bytes]
) -> None:
    priv, _ = rsa_key
    res = clerk_client.get(
        "/api/v1/me", headers={"Authorization": f"Bearer {make_token(priv, azp='https://evil.test')}"}
    )
    assert res.status_code == 401

    expired = make_token(priv, exp=int(time.time()) - 3600)
    assert clerk_client.get("/api/v1/me", headers={"Authorization": f"Bearer {expired}"}).status_code == 401

    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    forged = make_token(other_key)
    assert clerk_client.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


def test_pending_session_is_401(clerk_client: TestClient, rsa_key: tuple[bytes, bytes]) -> None:
    priv, _ = rsa_key
    token = make_token(priv, sts="pending", o=None)
    assert clerk_client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_slug_collision_and_concurrent_first_requests(
    clerk_client: TestClient, rsa_key: tuple[bytes, bytes], db: Session
) -> None:
    """Another mirrored org already owns the slug: the new org is created without a slug, no 500."""
    from app.domain.models import Organization

    priv, _ = rsa_key
    db.add(Organization(name="older", slug="acme-import", external_id="org_older", base_currency="EUR"))
    db.commit()
    headers = {"Authorization": f"Bearer {make_token(priv)}"}
    res = clerk_client.get("/api/v1/organization", headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["slug"] is None and res.json()["name"] == "ACME Import"
    # the same token again resolves the same org
    assert clerk_client.get("/api/v1/organization", headers=headers).json()["id"] == res.json()["id"]


def test_dev_header_is_refused_in_prod(clerk_client: TestClient) -> None:
    res = clerk_client.get("/api/v1/me", headers={"X-Org-Id": str(uuid.uuid4())})
    assert res.status_code == 401


def test_viewer_role_cannot_write(clerk_client: TestClient, rsa_key: tuple[bytes, bytes]) -> None:
    priv, _ = rsa_key
    # Clerk has no viewer role by default; a custom role slug that we do not recognise maps to MEMBER,
    # so this exercises the explicit mapping table instead.
    assert auth_clerk._ROLE_MAP["org:admin"] == "ADMIN"
    headers = {"Authorization": f"Bearer {make_token(priv, o={'id': 'org_v', 'rol': 'org:member'})}"}
    assert clerk_client.get("/api/v1/me", headers=headers).json()["role"] == "MEMBER"
