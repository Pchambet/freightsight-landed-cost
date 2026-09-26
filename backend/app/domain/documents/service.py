"""Accepting an upload: what it is, how big, and whether we already have it.

The type is decided by the file's first bytes, not by the name or by the `Content-Type` the browser
claims — those are what an attacker controls.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict
from app.domain.documents.ports import DocumentStore, DocumentTooLarge, UnsupportedDocumentType
from app.domain.models import Document

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

#: Magic bytes we accept, and the type we record for them.
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True)
class Accepted:
    content_type: str
    sha256: str
    size: int


def sniff(content: bytes) -> str:
    for signature, content_type in SIGNATURES:
        if content.startswith(signature):
            return content_type
    raise UnsupportedDocumentType(
        "Only PDF, PNG and JPEG files are accepted, decided by the file's own first bytes",
        code="UNSUPPORTED_DOCUMENT_TYPE",
    )


def accept(content: bytes) -> Accepted:
    if not content:
        raise UnsupportedDocumentType("The file is empty", code="EMPTY_DOCUMENT")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge(
            f"The file is {len(content) // 1024 // 1024} MB; the limit is "
            f"{MAX_DOCUMENT_BYTES // 1024 // 1024} MB"
        )
    return Accepted(sniff(content), hashlib.sha256(content).hexdigest(), len(content))


def store_document(
    db: Session,
    store: DocumentStore,
    org_id: UUID,
    content: bytes,
    *,
    filename: str | None,
    created_by: UUID | None = None,
) -> Document:
    """Validate, refuse a duplicate, write the bytes, record the metadata.

    The duplicate check is per organization and on the content itself: the same invoice re-uploaded
    under another name is the same invoice, and the conflict names the document we already hold so
    the caller can go straight to it.
    """
    accepted = accept(content)
    existing = db.scalar(
        select(Document).where(Document.org_id == org_id, Document.sha256 == accepted.sha256)
    )
    if existing is not None:
        raise Conflict(
            f"This file was already uploaded as {existing.filename or existing.id}",
            code="DOCUMENT_ALREADY_EXISTS",
            document_id=str(existing.id),
        )
    key = f"{org_id}/{uuid.uuid4()}"
    store.put(org_id, key, content, accepted.content_type)
    document = Document(
        org_id=org_id,
        storage=store.name,
        storage_key=key,
        filename=filename,
        content_type=accepted.content_type,
        size_bytes=accepted.size,
        sha256=accepted.sha256,
        created_by=created_by,
    )
    db.add(document)
    db.flush()
    return document
