"""
Shared WebSocket security utilities.

GAP-2: WebSocket connection guard + message validation.
GAP-10: WebSocket query-param auth removed (SA2-10).
SGA-M1/M2/L3: Centralized WebSocket hardening — rate limiting, message
size validation, and per-user connection tracking.
SGA3-L4: Redis-backed connection tracking for multi-worker deployments.

Extracted from agents.py to be shared across all WebSocket endpoints
(agents, campaign progress, spend advisor).
"""

import json
import logging
import os
import time
from collections import defaultdict
from typing import Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------
# WebSocket Hardening Constants
# -------------------------------------------------------------------------

WS_MAX_CONNECTIONS_PER_USER = 5
WS_MAX_MESSAGE_BYTES = 64 * 1024  # 64 KiB
WS_MIN_MESSAGE_INTERVAL = 0.5  # seconds between messages

# Per-user active connection counts (in-memory fallback)
_ws_connections: dict[str, int] = defaultdict(int)

# SGA3-L4: Redis-backed connection tracking for multi-worker accuracy
_ws_redis = None
_ws_redis_checked = False
_WS_REDIS_KEY_PREFIX = "ws:conn:"
_WS_CONN_TTL = 300  # seconds — auto-expire stale entries


def _get_ws_redis():
    """Get Redis client for WS connection tracking (lazy init)."""
    global _ws_redis, _ws_redis_checked
    if _ws_redis_checked:
        return _ws_redis
    _ws_redis_checked = True
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return None
    try:
        import redis as _redis_mod
        base = redis_url.rsplit("/", 1)[0]
        _ws_redis = _redis_mod.Redis.from_url(
            f"{base}/5", decode_responses=True, socket_connect_timeout=2,
        )
        _ws_redis.ping()
        logger.info("WebSocket connection tracking using Redis (DB 5)")
        return _ws_redis
    except Exception as exc:
        logger.warning("Redis unavailable for WS tracking, using in-memory: %s", exc)
        return None


class WSConnectionGuard:
    """Async context manager to track per-user WebSocket connections.

    Usage::

        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                # Too many connections
                await websocket.close(...)
                return
            # ... main WebSocket loop ...

    The guard increments on enter and decrements on exit (including on
    exception), preventing counter drift from manual tracking.
    """

    def __init__(self, user_id: str, *, max_connections: int = WS_MAX_CONNECTIONS_PER_USER):
        self.user_id = user_id
        self.max_connections = max_connections
        self.rejected = False
        self._entered = False

    async def __aenter__(self):
        r = _get_ws_redis()
        if r is not None:
            # SGA3-L4: Redis-backed tracking — accurate across workers
            try:
                key = f"{_WS_REDIS_KEY_PREFIX}{self.user_id}"
                current = int(r.get(key) or 0)
                if current >= self.max_connections:
                    self.rejected = True
                    return self
                r.incr(key)
                r.expire(key, _WS_CONN_TTL)
                self._entered = True
                return self
            except Exception:
                pass  # Fall through to in-memory
        # In-memory fallback
        if _ws_connections.get(self.user_id, 0) >= self.max_connections:
            self.rejected = True
            return self
        _ws_connections[self.user_id] += 1
        self._entered = True
        return self

    async def __aexit__(self, *exc):
        if self._entered:
            r = _get_ws_redis()
            if r is not None:
                try:
                    key = f"{_WS_REDIS_KEY_PREFIX}{self.user_id}"
                    r.decr(key)
                    # Ensure we don't go negative
                    if int(r.get(key) or 0) <= 0:
                        r.delete(key)
                    return
                except Exception:
                    pass  # Fall through to in-memory
            _ws_connections[self.user_id] -= 1
            if _ws_connections[self.user_id] <= 0:
                _ws_connections.pop(self.user_id, None)


async def ws_receive_validated(
    websocket: WebSocket,
    rate_state: Optional[dict] = None,
) -> dict:
    """Receive and validate a WebSocket message (size + rate limiting).

    Args:
        websocket: The WebSocket connection.
        rate_state: Mutable dict for per-connection rate tracking.
            Pass ``{}`` on first call; the function updates it internally.
            Pass ``None`` to skip rate limiting (e.g. for auth messages).

    Returns:
        Parsed JSON dict.  Special ``type`` values:

        - ``"_oversized"`` — message exceeded ``WS_MAX_MESSAGE_BYTES``
        - ``"_rate_limited"`` — message arrived within ``WS_MIN_MESSAGE_INTERVAL``
        - ``"invalid"`` — message was not valid JSON
    """
    raw = await websocket.receive_text()

    # Size check
    if len(raw.encode("utf-8")) > WS_MAX_MESSAGE_BYTES:
        return {"type": "_oversized"}

    # SGA-M1: Actual rate-limit enforcement using monotonic clock
    if rate_state is not None:
        now = time.monotonic()
        last = rate_state.get("last_msg_time", 0.0)
        if last > 0.0 and (now - last) < WS_MIN_MESSAGE_INTERVAL:
            return {"type": "_rate_limited"}
        rate_state["last_msg_time"] = now

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"type": "invalid"}
    return data


def get_connection_count(user_id: str) -> int:
    """Return the current WebSocket connection count for a user."""
    return _ws_connections.get(user_id, 0)


# -------------------------------------------------------------------------
# WebSocket Authentication  (moved from agents.py)
# -------------------------------------------------------------------------

async def authenticate_websocket(websocket: WebSocket) -> Optional["User"]:
    """Authenticate a WebSocket connection using first-message auth (SA2-10).

    Protocol:
        1. First-message auth: ``{"type": "auth", "token": "<jwt>"}``

    Query-param auth (``?token=``) was removed to prevent token leakage
    via server logs, proxy logs, and browser history.

    Returns the :class:`User` if authenticated, ``None`` otherwise.
    A 10-second timeout is applied to prevent hanging connections.

    .. note::

        Connection-limit enforcement has been moved to
        :class:`WSConnectionGuard` so that callers get a distinct
        "too many connections" error instead of a generic auth failure.
    """
    import asyncio

    from app.core.security import decode_access_token

    try:
        token = await asyncio.wait_for(
            _extract_ws_token(websocket),
            timeout=10,
        )
    except asyncio.TimeoutError:
        return None

    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    from sqlalchemy import select
    from app.api.deps import get_db_context
    from app.models import User

    async with get_db_context() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user and user.is_active:
            return user

    return None


async def _extract_ws_token(websocket: WebSocket) -> Optional[str]:
    """Extract auth token from the first WebSocket message.

    SA2-10 / GAP-10: Query-param auth removed — tokens in URLs leak
    via logs/history.  Only first-message auth is supported.
    """
    try:
        raw = await websocket.receive_text()
        msg = json.loads(raw)
        if isinstance(msg, dict) and msg.get("type") == "auth":
            return msg.get("token")
    except Exception:
        pass
    return None
