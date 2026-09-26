"""A small S3 client: sign, put, get, list, delete.

Signature Version 4 written out rather than pulled in — the whole need is four requests, and the
algorithm has not changed since 2012. It talks to Scaleway Paris by default and to anything else that
speaks S3.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.http import timeout as http_timeout

ALGORITHM = "AWS4-HMAC-SHA256"
SERVICE = "s3"
NAMESPACE = "{http://s3.amazonaws.com/doc/2006-03-01/}"


class S3Error(RuntimeError):
    pass


class S3NotFound(S3Error):
    pass


@dataclass(frozen=True)
class S3Object:
    key: str
    size: int
    last_modified: str


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


#: Sending a backup is a write as long as the dump is big. Reaching the bucket is not, and no
#: longer shares the number.
TIMEOUT_SECONDS = 300.0


class S3Client:
    def __init__(
        self,
        bucket: str,
        region: str,
        access_key: str,
        secret_key: str,
        endpoint: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self._client = client

    def _http(self) -> httpx.Client:
        return self._client or httpx.Client(base_url=self.endpoint, timeout=http_timeout(self.timeout))

    def _path(self, key: str) -> str:
        return f"/{self.bucket}/{quote(key, safe='/')}"

    def headers(
        self,
        method: str,
        path: str,
        payload: bytes,
        content_type: str | None = None,
        query: str = "",
    ) -> dict[str, str]:
        now = dt.datetime.now(dt.UTC)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(payload).hexdigest()
        headers = {
            "host": self.endpoint.split("://", 1)[-1],
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": stamp,
        }
        if content_type:
            headers["content-type"] = content_type
        signed = ";".join(sorted(headers))
        canonical = "\n".join(
            [
                method,
                path,
                query,
                "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                signed,
                payload_hash,
            ]
        )
        scope = f"{day}/{self.region}/{SERVICE}/aws4_request"
        to_sign = "\n".join([ALGORITHM, stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
        signing_key = _sign(
            _sign(_sign(_sign(f"AWS4{self.secret_key}".encode(), day), self.region), SERVICE),
            "aws4_request",
        )
        signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"{ALGORITHM} Credential={self.access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
        )
        return headers

    def put(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> None:
        path = self._path(key)
        response = self._request("PUT", path, content=content, content_type=content_type)
        if response.status_code >= 400:
            raise S3Error(f"PUT {key} answered {response.status_code}: {response.text[:200]}")

    def get(self, key: str) -> bytes:
        response = self._request("GET", self._path(key))
        if response.status_code == 404:
            raise S3NotFound(key)
        if response.status_code >= 400:
            raise S3Error(f"GET {key} answered {response.status_code}")
        return response.content

    def delete(self, key: str) -> None:
        response = self._request("DELETE", self._path(key))
        if response.status_code >= 400 and response.status_code != 404:
            raise S3Error(f"DELETE {key} answered {response.status_code}")

    def list(self, prefix: str) -> list[S3Object]:
        """Every object under a prefix, following continuation tokens."""
        objects: list[S3Object] = []
        token: str | None = None
        while True:
            query = f"list-type=2&prefix={quote(prefix, safe='')}"
            if token:
                query += f"&continuation-token={quote(token, safe='')}"
            response = self._request("GET", f"/{self.bucket}", query=query)
            if response.status_code >= 400:
                raise S3Error(f"LIST {prefix} answered {response.status_code}")
            root = ET.fromstring(response.text)
            for item in root.findall(f"{NAMESPACE}Contents"):
                key = item.findtext(f"{NAMESPACE}Key") or ""
                objects.append(
                    S3Object(
                        key=key,
                        size=int(item.findtext(f"{NAMESPACE}Size") or 0),
                        last_modified=item.findtext(f"{NAMESPACE}LastModified") or "",
                    )
                )
            truncated = (root.findtext(f"{NAMESPACE}IsTruncated") or "false").lower() == "true"
            token = root.findtext(f"{NAMESPACE}NextContinuationToken")
            if not truncated or not token:
                return objects

    def _request(
        self,
        method: str,
        path: str,
        *,
        content: bytes = b"",
        content_type: str | None = None,
        query: str = "",
    ) -> httpx.Response:
        headers = self.headers(method, path, content, content_type, query)
        url = f"{path}?{query}" if query else path
        try:
            return self._http().request(method, url, content=content or None, headers=headers)
        except httpx.HTTPError as exc:
            raise S3Error(f"{method} {path}: {exc}") from exc
