"""The document store of a deployment that has no object storage yet.

Bytes in Postgres is not where this ends up — a few thousand invoices at 2 MB is a backup you do not
want — but it is honest for now: same transaction, same backup, same tenancy rules as everything
else, and no credentials to hold. Swapping in S3 changes one setting.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.documents.ports import DocumentNotFound
from app.domain.models import DocumentBlob


class PostgresDocumentStore:
    name = "postgres"

    def __init__(self, db: Session) -> None:
        self.db = db

    def put(self, org_id: UUID, key: str, content: bytes, content_type: str) -> None:
        # The write joins the caller's transaction: an upload that fails afterwards leaves no orphan.
        self.db.add(DocumentBlob(org_id=org_id, storage_key=key, content=content))
        self.db.flush()

    def get(self, org_id: UUID, key: str) -> bytes:
        content = self.db.scalar(
            select(DocumentBlob.content).where(DocumentBlob.org_id == org_id, DocumentBlob.storage_key == key)
        )
        if content is None:
            raise DocumentNotFound(key)
        return bytes(content)

    def delete(self, org_id: UUID, key: str) -> None:
        blob = self.db.scalar(
            select(DocumentBlob).where(DocumentBlob.org_id == org_id, DocumentBlob.storage_key == key)
        )
        if blob is not None:
            self.db.delete(blob)
            self.db.flush()
