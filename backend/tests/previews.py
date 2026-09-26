"""Committing an import the way the screen does: naming the preview the person looked at."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from httpx import Response


def approved(client: TestClient, job_id: str) -> str | None:
    """The preview the job holds now — the one a person looking at the screen has just seen."""
    key: str | None = client.get(f"/api/v1/imports/{job_id}").json()["preview_key"]
    return key


def commit_previewed(client: TestClient, job_id: str, **body: Any) -> Response:
    res: Response = client.post(
        f"/api/v1/imports/{job_id}/commit", json={"preview_key": approved(client, job_id), **body}
    )
    return res
