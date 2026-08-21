from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

DEFAULT_RATE_LIMIT = 100
DEFAULT_RATE_WINDOW = 60.0
MAX_TRACKED_KEYS = 20_000

EXEMPT_PATHS = frozenset({"/", "/docs", "/redoc", "/openapi.json", "/v1/health"})


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter:
    """In-memory sliding window: *limit* requests per *window_seconds* per key."""

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT, window_seconds: float = DEFAULT_RATE_WINDOW) -> None:
        if limit < 0:
            raise ValueError("limit must be >= 0")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._last_gc = time.monotonic()

    def check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            self._gc(now, cutoff)
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, int(hits[0] + self.window_seconds - now) + 1)
                return RateLimitDecision(False, self.limit, 0, retry_after)
            hits.append(now)
            remaining = self.limit - len(hits)
            retry_after = max(1, int(hits[0] + self.window_seconds - now) + 1)
            return RateLimitDecision(True, self.limit, remaining, retry_after)

    def _gc(self, now: float, cutoff: float) -> None:
        if now - self._last_gc < 30 and len(self._hits) < MAX_TRACKED_KEYS:
            return
        self._last_gc = now
        stale = [
            key
            for key, hits in self._hits.items()
            if not _trim(hits, cutoff)
        ]
        for key in stale:
            del self._hits[key]
        if len(self._hits) > MAX_TRACKED_KEYS:
            overflow = len(self._hits) - MAX_TRACKED_KEYS
            for key in list(self._hits.keys())[:overflow]:
                del self._hits[key]


def client_ip(request: object) -> str:
    headers = getattr(request, "headers", {})
    forwarded = headers.get("x-forwarded-for") if headers is not None else None
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    real_ip = headers.get("x-real-ip") if headers is not None else None
    if real_ip:
        return real_ip.strip()
    client = getattr(request, "client", None)
    host = getattr(client, "host", None)
    if host:
        return host
    return "unknown"


def is_exempt(path: str) -> bool:
    return path in EXEMPT_PATHS


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "Retry-After": str(decision.retry_after),
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.retry_after),
    }


def _trim(hits: deque[float], cutoff: float) -> bool:
    while hits and hits[0] <= cutoff:
        hits.popleft()
    return bool(hits)
