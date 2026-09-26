"""Accepting an upload, and the two places its bytes can live."""

from __future__ import annotations

import hashlib
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.documents.postgres import PostgresDocumentStore
from app.adapters.documents.s3 import S3DocumentStore
from app.core.errors import Conflict
from app.core.tenancy import set_current_org
from app.domain.documents.ports import (
    DocumentNotFound,
    DocumentStoreUnavailable,
    DocumentTooLarge,
    UnsupportedDocumentType,
)
from app.domain.documents.registry import get_store
from app.domain.documents.service import MAX_DOCUMENT_BYTES, accept, sniff, store_document
from app.domain.models import Document, DocumentBlob, Organization

PDF = b"%PDF-1.4\nnot much of an invoice\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 40


@pytest.fixture
def org(client: TestClient, db: Session, org_id: uuid.UUID) -> Organization:
    assert client.get("/api/v1/organization").status_code == 200
    organization = db.get(Organization, org_id)
    assert organization is not None
    return organization


# ---------------------------------------------------------------------------- what is accepted


def test_the_type_comes_from_the_bytes_not_the_name() -> None:
    assert sniff(PDF) == "application/pdf"
    assert sniff(PNG) == "image/png"
    assert sniff(JPEG) == "image/jpeg"


def test_a_renamed_executable_is_refused() -> None:
    """The filename and the browser's Content-Type are attacker-controlled; the first bytes are not."""
    with pytest.raises(UnsupportedDocumentType):
        sniff(b"MZ\x90\x00 this is a windows binary called invoice.pdf")
    with pytest.raises(UnsupportedDocumentType):
        accept(b"")


def test_too_large_is_refused_with_the_limit_in_the_message() -> None:
    with pytest.raises(DocumentTooLarge) as raised:
        accept(b"%PDF-" + b"0" * MAX_DOCUMENT_BYTES)
    assert "20 MB" in raised.value.message


def test_accept_reports_the_hash_and_the_size() -> None:
    accepted = accept(PDF)
    assert accepted.sha256 == hashlib.sha256(PDF).hexdigest()
    assert accepted.size == len(PDF)


# ---------------------------------------------------------------------------- storing


def test_a_stored_document_can_be_read_back(db: Session, org: Organization) -> None:
    store = PostgresDocumentStore(db)
    document = store_document(db, store, org.id, PDF, filename="facture.pdf")
    db.commit()

    assert document.storage == "postgres"
    assert document.content_type == "application/pdf"
    assert store.get(org.id, document.storage_key) == PDF


def test_the_same_file_twice_is_a_conflict_naming_the_first(db: Session, org: Organization) -> None:
    store = PostgresDocumentStore(db)
    first = store_document(db, store, org.id, PDF, filename="facture.pdf")
    db.commit()

    with pytest.raises(Conflict) as raised:
        store_document(db, store, org.id, PDF, filename="facture-copie.pdf")
    assert raised.value.extra["document_id"] == str(first.id)


def test_another_organization_may_upload_the_same_file(db: Session, org: Organization) -> None:
    """De-duplication is per tenant: two companies can receive the same forwarder's invoice."""
    other = Organization(name="Other", base_currency="EUR")
    db.add(other)
    db.flush()
    store = PostgresDocumentStore(db)
    store_document(db, store, org.id, PDF, filename="facture.pdf")
    store_document(db, store, other.id, PDF, filename="facture.pdf")
    db.commit()
    assert len(list(db.scalars(select(Document)))) == 2


def test_deleting_what_is_not_there_is_not_an_error(db: Session, org: Organization) -> None:
    store = PostgresDocumentStore(db)
    store.delete(org.id, "nothing/here")
    with pytest.raises(DocumentNotFound):
        store.get(org.id, "nothing/here")


def test_documents_are_invisible_to_another_organization(db: Session, org: Organization) -> None:
    store = PostgresDocumentStore(db)
    store_document(db, store, org.id, PDF, filename="facture.pdf")
    db.commit()
    db.execute(text("SET ROLE freightsight_app"))
    try:
        set_current_org(db, org.id)
        assert len(list(db.scalars(select(Document)))) == 1
        assert len(list(db.scalars(select(DocumentBlob)))) == 1
        set_current_org(db, uuid.uuid4())
        assert list(db.scalars(select(Document))) == []
        assert list(db.scalars(select(DocumentBlob))) == []
    finally:
        db.execute(text("RESET ROLE"))
        set_current_org(db, org.id)


# ---------------------------------------------------------------------------- which store


def test_the_store_is_postgres_until_s3_is_fully_configured(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.settings import get_settings

    get_settings.cache_clear()
    assert get_store(db).name == "postgres"

    monkeypatch.setenv("DOCUMENTS_S3_BUCKET", "freightsight-documents")
    get_settings.cache_clear()
    assert get_store(db).name == "postgres"  # a bucket without credentials is not storage

    monkeypatch.setenv("DOCUMENTS_S3_ACCESS_KEY", "SCW00000000000000000")
    monkeypatch.setenv("DOCUMENTS_S3_SECRET_KEY", "secret")
    get_settings.cache_clear()
    assert get_store(db).name == "s3"
    get_settings.cache_clear()


# ---------------------------------------------------------------------------- the S3 adapter


def s3_store(handler) -> S3DocumentStore:  # type: ignore[no-untyped-def]
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://s3.fr-par.test")
    return S3DocumentStore(
        "freightsight-documents",
        "fr-par",
        "SCW00000000000000000",
        "secret",
        "https://s3.fr-par.test",
        client=client,
    )


def test_s3_signs_the_request_it_sends() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["sha"] = request.headers["x-amz-content-sha256"]
        return httpx.Response(200)

    s3_store(handler).put(uuid.uuid4(), "org/key", PDF, "application/pdf")

    assert seen["url"] == "https://s3.fr-par.test/freightsight-documents/org/key"
    assert seen["authorization"].startswith("AWS4-HMAC-SHA256 Credential=SCW00000000000000000/")
    assert "fr-par/s3/aws4_request" in seen["authorization"]
    assert seen["sha"] == hashlib.sha256(PDF).hexdigest()


def test_s3_reads_back_and_reports_a_missing_key() -> None:
    def found(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PDF)

    assert s3_store(found).get(uuid.uuid4(), "org/key") == PDF

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(DocumentNotFound):
        s3_store(missing).get(uuid.uuid4(), "org/key")


def test_s3_failures_are_not_swallowed() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(DocumentStoreUnavailable):
        s3_store(broken).put(uuid.uuid4(), "org/key", PDF, "application/pdf")


def test_s3_delete_tolerates_a_missing_object() -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    s3_store(missing).delete(uuid.uuid4(), "org/key")
