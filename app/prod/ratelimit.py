import logging
import math
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.prod.errors import APIError

log = logging.getLogger("triage.ratelimit")


class SlidingWindowLimiter:
    """Allows `limit` requests per `window_s` per client.

    In memory and per process: behind several replicas, use a shared store (Redis) or the gateway.
    """

    def __init__(self, max_clients: int = 10_000) -> None:
        self._hits: defaultdict[str, deque[float]] = defaultdict(deque)
        self._max_clients = max_clients

    def _forget_idle(self, now: float, window_s: float) -> None:
        # One entry per client ever seen would grow until the process runs out of memory.
        idle = [c for c, hits in self._hits.items() if not hits or hits[-1] <= now - window_s]
        for client in idle:
            del self._hits[client]

    def retry_after(self, client: str, limit: int, window_s: float) -> float | None:
        now = time.monotonic()
        if len(self._hits) >= self._max_clients:
            self._forget_idle(now, window_s)
        hits = self._hits[client]
        while hits and hits[0] <= now - window_s:
            hits.popleft()
        if len(hits) >= limit:
            return hits[0] + window_s - now
        hits.append(now)
        return None


limiter = SlidingWindowLimiter()


async def rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_client_id: Annotated[str | None, Header()] = None,
) -> None:
    client = x_client_id or (request.client.host if request.client else "unknown")
    wait = limiter.retry_after(client, settings.rate_limit_requests, settings.rate_limit_window_s)
    if wait is not None:
        seconds = max(1, math.ceil(wait))
        log.warning("rate_limited", extra={"client": client, "retry_after_s": seconds})
        raise APIError(
            429, "rate_limited", f"Too many requests. Retry in {seconds}s.",
            headers={"Retry-After": str(seconds)},
        )
