"""GET /search: what the command palette asks while the user is still typing."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.v1 import schemas
from app.core.tenancy import TenantDep
from app.domain.search.service import search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=schemas.SearchResponse)
def search_everything(
    t: TenantDep,
    q: str = Query(default="", max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
) -> schemas.SearchResponse:
    """Containers, orders, articles, invoices, suppliers and shipments whose name or number matches.

    Case, accents, spaces and punctuation are ignored ("mscu 482" finds MSCU4821990). The whole
    reference comes before its beginning, which comes before a match in the middle. Fewer than two
    characters is not a question yet: the answer is an empty list, not an error.
    """
    hits = search(t.db, t.org_id, q, limit)
    return schemas.SearchResponse(
        query=q,
        results=[
            schemas.SearchHit(kind=h.kind, id=h.id, label=h.label, sublabel=h.sublabel, sku=h.sku)
            for h in hits
        ],
    )
