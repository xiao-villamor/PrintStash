"""In-process per-IP sliding-window rate limiting for FastAPI routes.

ponytail: process-local dict, correct for a single worker only. A
multi-worker or multi-process deployment needs a shared store (Redis/Upstash)
since each worker would otherwise track its own independent window.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, limit: int, window_s: float, *, max_keys: int = 10_000) -> None:
        if limit < 1 or window_s <= 0 or max_keys < 1:
            raise ValueError("limit, window_s, and max_keys must be positive")
        self._limit = limit
        self._window = window_s
        self._max_keys = max_keys
        self._hits: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            # Remove fully expired buckets first. This both bounds idle-state
            # retention and avoids evicting an active key unnecessarily.
            for stale_key, stale_hits in list(self._hits.items()):
                if not stale_hits or stale_hits[-1] <= cutoff:
                    self._hits.pop(stale_key, None)

            hits = [t for t in self._hits.get(key, ()) if t > cutoff]
            if len(hits) >= self._limit:
                self._hits[key] = hits
                self._hits.move_to_end(key)
                return False
            if key not in self._hits and len(self._hits) >= self._max_keys:
                self._hits.popitem(last=False)
            hits.append(now)
            self._hits[key] = hits
            self._hits.move_to_end(key)
            return True

    @property
    def key_count(self) -> int:
        with self._lock:
            return len(self._hits)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def rate_limit(limit: int, window_s: float) -> Callable[[Request], None]:
    """Build a FastAPI dependency enforcing *limit* requests per *window_s* per IP."""
    limiter = RateLimiter(limit=limit, window_s=window_s)

    def _dependency(request: Request) -> None:
        client = request.client.host if request.client else "unknown"
        if not limiter.check(client):
            raise HTTPException(status_code=429, detail="rate_limited")

    _dependency.limiter = limiter  # type: ignore[attr-defined]
    return _dependency
