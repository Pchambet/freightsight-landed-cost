"""Provider webhooks. No JWT: the proof is the provider's signature over the raw body."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1 import schemas
from app.api.v1.deps import ProviderFactoryDep
from app.core.auth import DbDep
from app.domain.tracking.delivery import receive

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/tracking/{provider}", response_model=schemas.WebhookAck)
async def tracking_webhook(
    provider: str, request: Request, db: DbDep, make_provider: ProviderFactoryDep
) -> schemas.WebhookAck:
    """Accept one tracking delivery.

    Answers 401 when the signature does not check out or the delivery is too old, and 200 for
    everything else — including a replay and a delivery we cannot route — because a provider retries
    on anything else and there is nothing to gain from being retried.
    """
    impl = make_provider(provider)
    body = await request.body()
    result = receive(db, impl, dict(request.headers), body)
    return schemas.WebhookAck(
        delivery_id=result.delivery_id,
        status=result.status,
        events_ingested=result.events_ingested,
        detail=result.detail,
    )
