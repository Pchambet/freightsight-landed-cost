"""Documents in S3-compatible object storage (Scaleway Paris is the intended home).

Only the mapping from a document key to an object; the signing and the HTTP live in
`app/adapters/s3.py`, shared with the backup job.

The bucket is never public and no URL is ever handed to a browser: the API streams the bytes, so a
document stays behind the same authentication and the same tenancy checks as its metadata.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from app.adapters.s3 import S3Client, S3Error, S3NotFound
from app.domain.documents.ports import DocumentNotFound, DocumentStoreUnavailable


class S3DocumentStore:
    name = "s3"

    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.s3 = S3Client(bucket, region, access_key, secret_key, endpoint, client=client, timeout=30.0)

    def put(self, org_id: UUID, key: str, content: bytes, content_type: str) -> None:
        try:
            self.s3.put(key, content, content_type)
        except S3Error as exc:
            raise DocumentStoreUnavailable(f"Object storage refused the upload: {exc}") from exc

    def get(self, org_id: UUID, key: str) -> bytes:
        try:
            return self.s3.get(key)
        except S3NotFound as exc:
            raise DocumentNotFound(key) from exc
        except S3Error as exc:
            raise DocumentStoreUnavailable(f"Object storage could not be read: {exc}") from exc

    def delete(self, org_id: UUID, key: str) -> None:
        try:
            self.s3.delete(key)
        except S3Error as exc:
            raise DocumentStoreUnavailable(f"Object storage could not be written: {exc}") from exc
