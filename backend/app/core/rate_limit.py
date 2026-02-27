"""
Rate limiting configuration using slowapi.

Applies per-IP rate limits to authentication endpoints to prevent
brute-force attacks and credential stuffing.

Uses Redis-backed storage so rate limit counters persist across
restarts and are shared across workers.
"""
import os

from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_rate_limit_storage_uri() -> str:
    """Determine rate limit storage backend.

    Uses Redis DB 3 (separate from broker/results/cache) when available,
    falls back to in-memory for test environments.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    if redis_url:
        # Swap DB index to /3 for rate limiting (separate from app data)
        base = redis_url.rsplit("/", 1)[0]
        return f"{base}/3"
    return "memory://"


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],  # SA-06: Global rate limit as safety net
    storage_uri=_get_rate_limit_storage_uri(),
)
