"""A small in-process rate limit, for the one route that answers strangers.

Per process and in memory on purpose: there is one API process today, and a limit that needs Redis to
exist would not exist. It bounds what a flood can cost; it is not an access control — the access
control of a shared report is a 256-bit token.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request


class SlidingWindow:
    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[key]
            while hits and moment - hits[0] >= self.seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(moment)
            if len(self._hits) > 10_000:  # a flood of distinct addresses must not become a memory leak
                for stale in [k for k, v in self._hits.items() if not v]:
                    del self._hits[stale]
            return True


def client_address(request: Request) -> str:
    """The caller as the platform's proxy reports it, else the socket's peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
