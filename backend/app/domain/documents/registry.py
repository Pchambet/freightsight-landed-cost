"""Which document store this deployment has."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.settings import Settings, get_settings
from app.domain.documents.ports import DocumentStore


def get_store(db: Session, settings: Settings | None = None) -> DocumentStore:
    """S3 when it is fully configured, the database otherwise. A half-configured S3 is not S3."""
    settings = settings or get_settings()
    if settings.s3_documents_enabled:
        from app.adapters.documents.s3 import S3DocumentStore

        assert settings.documents_s3_bucket and settings.documents_s3_access_key
        assert settings.documents_s3_secret_key
        return S3DocumentStore(
            settings.documents_s3_bucket,
            settings.documents_s3_region,
            settings.documents_s3_access_key,
            settings.documents_s3_secret_key,
            settings.documents_s3_endpoint,
        )
    from app.adapters.documents.postgres import PostgresDocumentStore

    return PostgresDocumentStore(db)
