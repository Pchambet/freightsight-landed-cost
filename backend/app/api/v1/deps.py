from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.adapters.fx_ecb import make_fetcher, make_range_fetcher
from app.core.auth import WRITE_ROLES, Principal, require_role
from app.core.settings import get_settings
from app.domain.fx.service import FxService
from app.domain.models import MemberRole
from app.domain.tracking.ports import TrackingProvider
from app.domain.tracking.registry import get_provider

WriterDep = Annotated[Principal, Depends(require_role(*WRITE_ROLES))]
#: Closing a month, and reopening one, is an accounting act: the people who answer for the accounts.
#: Not OWNER alone: a Clerk organization only knows `org:admin` and `org:member`, so nobody in a real
#: organization is an OWNER, and a month closed there could never have been reopened.
AdminDep = Annotated[Principal, Depends(require_role(MemberRole.OWNER, MemberRole.ADMIN))]


@lru_cache(maxsize=1)
def get_fx_service() -> FxService:
    url = get_settings().fx_api_url
    return FxService(make_fetcher(url), range_fetcher=make_range_fetcher(url))


FxDep = Annotated[FxService, Depends(get_fx_service)]


def get_provider_factory() -> Callable[[str], TrackingProvider]:
    """Indirection so tests (and, later, a sandbox mode) can hand the webhook route a fake provider."""
    return get_provider


ProviderFactoryDep = Annotated[Callable[[str], TrackingProvider], Depends(get_provider_factory)]
