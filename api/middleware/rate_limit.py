"""Simple in-memory rate limiting middleware for auth endpoints.

Limits requests per IP address using a sliding window counter.
For production with multiple server instances, replace with Redis-backed limiter.
"""

import time
from collections import defaultdict
from typing import Dict, Tuple

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# Rate limits: (max_requests, window_seconds)
_RATE_LIMITS: Dict[str, Tuple[int, int]] = {
    "/api/auth/login": (10, 60),          # 10 attempts per minute
    "/api/auth/register": (5, 300),       # 5 signups per 5 minutes
    "/api/auth/resend-verification": (3, 300),  # 3 resends per 5 minutes
}

# In-memory sliding window counters: key -> [(timestamp, count)]
_counters: Dict[str, list] = defaultdict(list)
_MAX_ENTRIES = 10_000  # Prevent unbounded memory growth


def _get_client_ip(request: Request) -> str:
    """Extract client IP, respecting X-Forwarded-For behind proxies."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _cleanup_old_entries(key: str, window: int, now: float) -> None:
    """Remove entries older than the window."""
    cutoff = now - window
    _counters[key] = [(ts, c) for ts, c in _counters[key] if ts > cutoff]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit POST requests to auth endpoints by client IP."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate-limit POST requests on configured paths
        if request.method != "POST":
            return await call_next(request)

        path = request.url.path.rstrip("/")
        limit_config = _RATE_LIMITS.get(path)
        if not limit_config:
            return await call_next(request)

        max_requests, window_seconds = limit_config
        client_ip = _get_client_ip(request)
        key = f"{path}:{client_ip}"
        now = time.monotonic()

        # Cleanup stale entries
        _cleanup_old_entries(key, window_seconds, now)

        # Count requests in current window
        request_count = sum(c for _, c in _counters[key])

        if request_count >= max_requests:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests. Please try again later."},
                headers={"Retry-After": str(window_seconds)},
            )

        # Record this request
        _counters[key].append((now, 1))

        # Periodic cleanup of entire dict to prevent memory leak
        if len(_counters) > _MAX_ENTRIES:
            oldest_keys = sorted(_counters.keys(), key=lambda k: _counters[k][0][0] if _counters[k] else 0)
            for k in oldest_keys[:len(oldest_keys) // 2]:
                del _counters[k]

        return await call_next(request)
