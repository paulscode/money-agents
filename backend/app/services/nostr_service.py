"""
Nostr Service — Core business logic for the Nostr Agent Tool.

Handles identity management, relay communication, event building/signing,
content publishing, discovery, and zap integration.
"""
import asyncio
import hashlib
import ipaddress
import json
import logging
import time
from datetime import datetime, timezone
from app.core.datetime_utils import utc_now
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from uuid import UUID, uuid4

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.nostr_identity import NostrIdentity
from app.services.nostr_key_manager import encrypt_nsec, decrypt_nsec
from app.services.prompt_injection_guard import sanitize_external_content

logger = logging.getLogger(__name__)

# Maximum results returned to agent (context window protection)
MAX_RESULTS = 20
DEFAULT_LIMIT = 10

# Event kinds
KIND_METADATA = 0
KIND_TEXT_NOTE = 1
KIND_RECOMMEND_RELAY = 2
KIND_CONTACTS = 3
KIND_DELETE = 5
KIND_REPOST = 6
KIND_REACTION = 7
KIND_ZAP_REQUEST = 9734
KIND_ZAP_RECEIPT = 9735
KIND_LONG_FORM = 30023


# ---------------------------------------------------------------------------
# Rate-limit tracker (in-memory, per-identity)
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Sliding-window rate limiter keyed by identity_id.

    Uses Redis when available for persistence across restarts and
    multi-process deployments. Falls back to in-memory if Redis
    is unreachable.
    """

    def __init__(self):
        self._buckets: Dict[str, List[float]] = {}  # in-memory fallback
        self._redis = None
        self._redis_checked = False

    def _get_redis(self):
        """Lazy-init Redis connection."""
        if not self._redis_checked:
            self._redis_checked = True
            try:
                import redis as redis_lib
                self._redis = redis_lib.Redis.from_url(
                    settings.redis_url, decode_responses=True, socket_connect_timeout=2,
                )
                self._redis.ping()
                logger.info("Nostr rate limiter using Redis backend")
            except Exception:
                self._redis = None
                logger.info("Nostr rate limiter using in-memory backend (Redis unavailable)")
        return self._redis

    def check(self, identity_id: str, window_seconds: int, max_count: int) -> bool:
        """Return True if under limit, False if exceeded."""
        r = self._get_redis()
        if r:
            return self._check_redis(r, identity_id, window_seconds, max_count)
        return self._check_memory(identity_id, window_seconds, max_count)

    def record(self, identity_id: str):
        """Record a post event for an identity."""
        r = self._get_redis()
        if r:
            self._record_redis(r, identity_id)
        else:
            self._record_memory(identity_id)

    # --- Redis implementation ---
    def _check_redis(self, r, identity_id: str, window_seconds: int, max_count: int) -> bool:
        key = f"nostr:rl:{identity_id}:{window_seconds}"
        now = time.time()
        try:
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zcard(key)
            results = pipe.execute()
            return results[1] < max_count
        except Exception:
            return self._check_memory(identity_id, window_seconds, max_count)

    def _record_redis(self, r, identity_id: str):
        now = time.time()
        try:
            pipe = r.pipeline()
            for window in [3600, 86400]:
                key = f"nostr:rl:{identity_id}:{window}"
                pipe.zadd(key, {f"{now}": now})
                pipe.expire(key, window + 60)
            pipe.execute()
        except Exception:
            self._record_memory(identity_id)

    # --- In-memory fallback ---
    def _check_memory(self, identity_id: str, window_seconds: int, max_count: int) -> bool:
        now = time.time()
        key = f"{identity_id}:{window_seconds}"
        bucket = self._buckets.setdefault(key, [])
        self._buckets[key] = [t for t in bucket if now - t < window_seconds]
        return len(self._buckets[key]) < max_count

    def _record_memory(self, identity_id: str):
        now = time.time()
        for suffix in [str(3600), str(86400)]:
            key = f"{identity_id}:{suffix}"
            self._buckets.setdefault(key, []).append(now)


_rate_limiter = _RateLimiter()


# ---------------------------------------------------------------------------
# Profile name cache (avoid repeated kind-0 lookups)
# ---------------------------------------------------------------------------

class _NameCache:
    """In-memory cache of hex_pubkey → display_name with TTL."""

    MAX_ENTRIES = 1000
    TTL_SECONDS = 3600  # 1 hour

    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # hex_pubkey -> (name, timestamp)

    def get(self, pubkey_hex: str) -> Optional[str]:
        entry = self._cache.get(pubkey_hex)
        if entry and (time.time() - entry[1]) < self.TTL_SECONDS:
            return entry[0]
        return None

    def set(self, pubkey_hex: str, name: str):
        if len(self._cache) >= self.MAX_ENTRIES:
            # Evict oldest quarter
            sorted_keys = sorted(self._cache, key=lambda k: self._cache[k][1])
            for k in sorted_keys[: self.MAX_ENTRIES // 4]:
                del self._cache[k]
        self._cache[pubkey_hex] = (name, time.time())


_name_cache = _NameCache()


# ---------------------------------------------------------------------------
# Relay URL validation
# ---------------------------------------------------------------------------

# Private/internal IP ranges that relay URLs must not resolve to
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


import socket


def _resolve_and_check_private(hostname: str, label: str = "URL") -> list[str]:
    """Resolve a hostname via DNS and reject if any result is a private/internal IP.

    This prevents DNS-rebinding SSRF where an attacker controls a domain
    that maps to 127.0.0.1 or another internal address.
    
    SA2-07: Called both at validation time AND immediately before connection
    to mitigate DNS rebinding TOCTOU. The short TTL between re-check and
    connect makes rebinding impractical.
    
    Returns the list of resolved IP strings (for DNS pinning by callers).
    """
    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        # DNS lookup failed — allow through (will fail at connect time)
        return []

    resolved_ips: list[str] = []
    for family, _type, _proto, _canonname, sockaddr in addr_info:
        ip_str = sockaddr[0]
        resolved_ips.append(ip_str)
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                raise ValueError(
                    f"{label} hostname '{hostname}' resolves to private IP {ip_str}"
                )
    return resolved_ips


def _validate_relay_url(url: str) -> str:
    """Validate a Nostr relay URL.

    Ensures the URL uses wss:// or ws:// scheme, has a valid hostname,
    and does not point to private/internal network addresses.
    Also resolves DNS names to guard against DNS-rebinding attacks.

    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("wss", "ws"):
        raise ValueError(f"Invalid relay URL scheme '{parsed.scheme}' — must be wss:// or ws://")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid relay URL — missing hostname: {url}")
    # Reject Docker-internal hostnames
    if hostname in ("localhost", "host.docker.internal", "gateway.docker.internal"):
        raise ValueError(f"Relay URL must not point to internal host: {hostname}")
    # Reject raw IP addresses in private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                raise ValueError(f"Relay URL must not point to private IP: {hostname}")
    except ValueError as exc:
        # If ip_address() fails, hostname is a DNS name — resolve and check
        if "private IP" in str(exc) or "internal host" in str(exc):
            raise
        _resolve_and_check_private(hostname, label="Relay URL")
    return url


def _validate_http_url(url: str) -> str:
    """Validate an HTTP(S) URL is not targeting internal/private networks.

    Used for SSRF protection on LNURL resolution and callback URLs
    where the target is derived from external Nostr profile data.
    Also resolves DNS names to guard against DNS-rebinding attacks.

    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http"):
        raise ValueError(f"Invalid URL scheme '{parsed.scheme}' — must be https:// or http://")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL — missing hostname: {url}")
    # Reject Docker-internal hostnames
    if hostname in ("localhost", "host.docker.internal", "gateway.docker.internal"):
        raise ValueError(f"URL must not point to internal host: {hostname}")
    # Reject raw IP addresses in private ranges
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                raise ValueError(f"URL must not point to private IP: {hostname}")
    except ValueError as exc:
        if "private IP" in str(exc) or "internal host" in str(exc):
            raise
        _resolve_and_check_private(hostname, label="URL")
    return url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _relative_time(unix_ts: int) -> str:
    """Convert a unix timestamp to a human-readable relative string."""
    diff = int(time.time()) - unix_ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{diff // 60}m ago"
    elif diff < 86400:
        return f"{diff // 3600}h ago"
    elif diff < 604800:
        return f"{diff // 86400}d ago"
    else:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate a string, adding ellipsis if needed."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _resolve_pubkey(pubkey_or_npub: str) -> str:
    """Convert npub to hex pubkey, or validate hex pubkey."""
    if len(pubkey_or_npub) == 64:
        return pubkey_or_npub
    if pubkey_or_npub.startswith("npub1"):
        from pynostr.key import PublicKey
        pk = PublicKey.from_npub(pubkey_or_npub)
        return pk.hex()
    raise ValueError(f"Invalid pubkey or npub: {pubkey_or_npub}")


def _compact_event(raw: dict) -> dict:
    """Convert a raw Nostr event into a compact summary for the agent.

    Content is sanitized via sanitize_external_content() to strip
    prompt injection patterns before entering agent tool results (HIGH-2).
    """
    pubkey = raw.get("pubkey", "")
    # Try to resolve name from cache
    author_name = _name_cache.get(pubkey)

    tags = raw.get("tags", [])
    hashtags = [t[1] for t in tags if len(t) >= 2 and t[0] == "t"]

    # Sanitize content BEFORE truncation so injection patterns are stripped
    raw_content = raw.get("content", "")
    sanitized_content, _detections = sanitize_external_content(
        raw_content, source="nostr_post", ml_scan=False,
    )

    result = {
        "id": raw.get("id", "")[:12],
        "author": pubkey[:12] + "...",
        "time": _relative_time(raw.get("created_at", 0)),
        "text": _truncate(sanitized_content),
    }
    if author_name:
        result["author_name"] = author_name
    if hashtags:
        result["hashtags"] = hashtags
    return result


# ---------------------------------------------------------------------------
# Relay Pool — async WebSocket communication
# ---------------------------------------------------------------------------

class NostrRelayPool:
    """Short-lived async WebSocket connections to Nostr relays."""

    def __init__(self, default_relays: Optional[List[str]] = None, 
                 timeout: float = 5.0, connect_timeout: float = 3.0):
        self.default_relays = default_relays or self._parse_default_relays()
        self.timeout = timeout
        self.connect_timeout = connect_timeout

    @staticmethod
    def _parse_default_relays() -> List[str]:
        return [r.strip() for r in settings.nostr_default_relays.split(",") if r.strip()]

    async def publish_event(self, event_json: dict, relays: Optional[List[str]] = None) -> dict:
        """Publish a signed event to multiple relays.

        Returns dict: {relay_url: "ok" | error_message}
        """
        import websockets

        target_relays = relays or self.default_relays
        results = {}

        async def _publish_one(relay_url: str):
            try:
                _validate_relay_url(relay_url)
                # SA2-07: Re-check DNS immediately before connecting to mitigate rebinding
                parsed_url = urlparse(relay_url)
                if parsed_url.hostname:
                    _resolve_and_check_private(parsed_url.hostname, label="Relay (pre-connect)")
                msg = json.dumps(["EVENT", event_json])
                async with websockets.connect(
                    relay_url, open_timeout=self.connect_timeout, close_timeout=2
                ) as ws:
                    await ws.send(msg)
                    # Wait for OK response
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                        data = json.loads(resp)
                        if isinstance(data, list) and len(data) >= 3 and data[0] == "OK":
                            if data[2]:  # accepted
                                results[relay_url] = "ok"
                            else:
                                results[relay_url] = data[3] if len(data) > 3 else "rejected"
                        else:
                            results[relay_url] = "ok"  # Assume success if no explicit rejection
                    except asyncio.TimeoutError:
                        results[relay_url] = "ok"  # Sent but no explicit confirmation
            except Exception as e:
                results[relay_url] = f"error: {str(e)[:80]}"

        await asyncio.gather(*[_publish_one(r) for r in target_relays], return_exceptions=True)
        return results

    async def query_events(
        self, filters: dict, relays: Optional[List[str]] = None,
        limit: int = DEFAULT_LIMIT
    ) -> List[dict]:
        """Query relays with NIP-01 filters, deduplicate by event ID.

        Args:
            filters: NIP-01 filter dict (kinds, authors, #t, since, until, limit, etc.)
            relays: Override relay list.
            limit: Max results to return.

        Returns:
            List of raw event dicts, deduplicated and sorted by created_at desc.
        """
        import websockets

        filters["limit"] = min(limit, MAX_RESULTS)
        target_relays = relays or self.default_relays
        seen_ids: set = set()
        events: List[dict] = []
        sub_id = f"q_{uuid4().hex[:8]}"

        async def _query_one(relay_url: str):
            try:
                _validate_relay_url(relay_url)
                # SA2-07: Re-check DNS immediately before connecting to mitigate rebinding
                parsed_url = urlparse(relay_url)
                if parsed_url.hostname:
                    _resolve_and_check_private(parsed_url.hostname, label="Relay (pre-connect)")
                msg = json.dumps(["REQ", sub_id, filters])
                async with websockets.connect(
                    relay_url, open_timeout=self.connect_timeout, close_timeout=2
                ) as ws:
                    await ws.send(msg)
                    deadline = time.time() + self.timeout
                    while time.time() < deadline:
                        try:
                            resp = await asyncio.wait_for(
                                ws.recv(), timeout=max(0.1, deadline - time.time())
                            )
                            data = json.loads(resp)
                            if isinstance(data, list):
                                if data[0] == "EVENT" and len(data) >= 3:
                                    evt = data[2]
                                    eid = evt.get("id")
                                    if eid and eid not in seen_ids:
                                        seen_ids.add(eid)
                                        events.append(evt)
                                elif data[0] == "EOSE":
                                    break  # End of stored events
                        except asyncio.TimeoutError:
                            break
                    # Close subscription
                    try:
                        await ws.send(json.dumps(["CLOSE", sub_id]))
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Relay query failed for {relay_url}: {e}")

        await asyncio.gather(*[_query_one(r) for r in target_relays], return_exceptions=True)

        # Sort by created_at descending, limit results
        events.sort(key=lambda e: e.get("created_at", 0), reverse=True)
        return events[:limit]

    async def search_events(
        self, query: str, kinds: Optional[List[int]] = None,
        limit: int = DEFAULT_LIMIT
    ) -> List[dict]:
        """NIP-50 full-text search on supporting relays.

        Only searches relays known to support NIP-50 (relay.nostr.band).
        Falls back to basic filter if no search relays available.
        """
        search_relays = [
            r for r in self.default_relays
            if "nostr.band" in r  # Known NIP-50 relay
        ]
        if not search_relays:
            search_relays = self.default_relays[:1]  # Fallback to first relay

        filters: Dict[str, Any] = {"search": query, "limit": min(limit, MAX_RESULTS)}
        if kinds:
            filters["kinds"] = kinds

        return await self.query_events(filters, relays=search_relays, limit=limit)


# ---------------------------------------------------------------------------
# Nostr Service — main business logic
# ---------------------------------------------------------------------------

class NostrService:
    """All Nostr operations — identity mgmt, publishing, discovery, zaps."""

    def __init__(self):
        self.relay_pool = NostrRelayPool(
            timeout=settings.nostr_relay_timeout,
            connect_timeout=settings.nostr_relay_connect_timeout,
        )

    # -------------------------------------------------------------------
    # Identity management
    # -------------------------------------------------------------------

    async def create_identity(
        self,
        user_id: UUID,
        name: str,
        about: str = "",
        picture: str = "",
        nip05: str = "",
        lud16: str = "",
        relays: Optional[List[str]] = None,
        campaign_id: Optional[UUID] = None,
    ) -> dict:
        """Generate a new Nostr keypair, set profile, and store encrypted."""
        # Default lud16 to configured Lightning Address if not provided
        if not lud16 and settings.nostr_lightning_address:
            lud16 = settings.nostr_lightning_address

        from pynostr.key import PrivateKey

        private_key = PrivateKey()
        public_key = private_key.public_key
        npub = public_key.bech32()
        nsec = private_key.bech32()
        pubkey_hex = public_key.hex()

        # Encrypt nsec for storage
        encrypted = encrypt_nsec(nsec)

        identity_id = uuid4()
        relay_urls = relays or self.relay_pool.default_relays

        async with async_session_factory() as session:
            identity = NostrIdentity(
                id=identity_id,
                user_id=user_id,
                campaign_id=campaign_id,
                pubkey_hex=pubkey_hex,
                npub=npub,
                encrypted_nsec=encrypted,
                display_name=name,
                about=about,
                picture_url=picture,
                nip05=nip05,
                lud16=lud16,
                relay_urls=relay_urls,
            )
            session.add(identity)
            await session.commit()

        # Publish kind-0 profile metadata to relays
        try:
            metadata_event = self._build_metadata_event(
                private_key, name, about, picture, nip05, lud16
            )
            await self.relay_pool.publish_event(metadata_event, relays=relay_urls)
        except Exception as e:
            logger.warning(f"Failed to publish profile metadata for {npub[:20]}: {e}")

        _name_cache.set(pubkey_hex, name)
        logger.info(f"Created Nostr identity: {npub[:20]}... name={name}")

        return {
            "identity_id": str(identity_id),
            "npub": npub,
            "pubkey_hex": pubkey_hex[:12] + "...",
            "name": name,
            "relays": relay_urls,
        }

    async def list_identities(self, user_id: UUID) -> List[dict]:
        """List all active identities for a user."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(NostrIdentity)
                .where(NostrIdentity.user_id == user_id, NostrIdentity.is_active == True)
                .order_by(NostrIdentity.created_at.desc())
            )
            identities = result.scalars().all()

        return [
            {
                "id": str(i.id),
                "npub": i.npub,
                "name": i.display_name or "",
                "post_count": i.post_count,
                "followers": i.follower_count,
                "created": _relative_time(int(i.created_at.timestamp())),
            }
            for i in identities
        ]

    async def get_identity(self, identity_id: str, user_id: UUID) -> dict:
        """Get full identity details (never returns keys)."""
        identity = await self._load_identity(identity_id, user_id)
        return {
            "id": str(identity.id),
            "npub": identity.npub,
            "pubkey_hex": identity.pubkey_hex[:12] + "...",
            "name": identity.display_name or "",
            "about": identity.about or "",
            "picture": identity.picture_url or "",
            "nip05": identity.nip05 or "",
            "lud16": identity.lud16 or "",
            "relays": identity.relay_urls or [],
            "stats": {
                "posts": identity.post_count,
                "followers": identity.follower_count,
                "following": identity.following_count,
                "zaps_received_sats": identity.total_zaps_received_sats,
            },
            "created": identity.created_at.isoformat(),
            "last_posted": identity.last_posted_at.isoformat() if identity.last_posted_at else None,
        }

    async def update_profile(
        self,
        identity_id: str,
        user_id: UUID,
        name: Optional[str] = None,
        about: Optional[str] = None,
        picture: Optional[str] = None,
        nip05: Optional[str] = None,
        lud16: Optional[str] = None,
    ) -> dict:
        """Update identity profile and republish kind-0 metadata."""
        identity = await self._load_identity(identity_id, user_id)

        # Build update dict
        updates: Dict[str, Any] = {}
        if name is not None:
            updates["display_name"] = name
        if about is not None:
            updates["about"] = about
        if picture is not None:
            updates["picture_url"] = picture
        if nip05 is not None:
            updates["nip05"] = nip05
        if lud16 is not None:
            updates["lud16"] = lud16

        if not updates:
            return {"status": "no changes"}

        # Update DB
        async with async_session_factory() as session:
            await session.execute(
                update(NostrIdentity)
                .where(NostrIdentity.id == identity.id)
                .values(**updates, updated_at=utc_now())
            )
            await session.commit()

        # Republish kind-0
        try:
            nsec = decrypt_nsec(identity.encrypted_nsec)
            from pynostr.key import PrivateKey
            private_key = PrivateKey.from_nsec(nsec)
            del nsec  # Clear from memory

            final_name = name if name is not None else identity.display_name
            final_about = about if about is not None else identity.about
            final_picture = picture if picture is not None else identity.picture_url
            final_nip05 = nip05 if nip05 is not None else identity.nip05
            final_lud16 = lud16 if lud16 is not None else identity.lud16
            # Fall back to configured Lightning Address if still empty
            if not final_lud16 and settings.nostr_lightning_address:
                final_lud16 = settings.nostr_lightning_address

            metadata_event = self._build_metadata_event(
                private_key, final_name or "", final_about or "",
                final_picture or "", final_nip05 or "", final_lud16 or "",
            )
            await self.relay_pool.publish_event(
                metadata_event, relays=identity.relay_urls
            )
        except Exception as e:
            logger.warning(f"Failed to republish profile: {e}")

        if name:
            _name_cache.set(identity.pubkey_hex, name)

        return {"status": "updated", "fields": list(updates.keys())}

    # -------------------------------------------------------------------
    # Publishing actions
    # -------------------------------------------------------------------

    async def post_note(
        self,
        identity_id: str,
        user_id: UUID,
        content: str,
        hashtags: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Publish a kind-1 text note."""
        identity = await self._load_identity(identity_id, user_id)
        self._check_rate_limit(identity_id)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        event = self._build_text_note(private_key, content, hashtags, reply_to)
        results = await self.relay_pool.publish_event(event, relays=identity.relay_urls)

        # Update stats
        await self._increment_post_count(identity.id)
        _rate_limiter.record(identity_id)

        return {
            "event_id": event.get("id", "")[:12],
            "relays": results,
        }

    async def post_article(
        self,
        identity_id: str,
        user_id: UUID,
        title: str,
        content: str,
        summary: str = "",
        hashtags: Optional[List[str]] = None,
        image: str = "",
    ) -> dict:
        """Publish a kind-30023 long-form article."""
        identity = await self._load_identity(identity_id, user_id)
        self._check_rate_limit(identity_id)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        event = self._build_article(private_key, title, content, summary, hashtags, image)
        results = await self.relay_pool.publish_event(event, relays=identity.relay_urls)

        await self._increment_post_count(identity.id)
        _rate_limiter.record(identity_id)

        d_tag = ""
        for tag in event.get("tags", []):
            if tag[0] == "d":
                d_tag = tag[1]
                break

        return {
            "event_id": event.get("id", "")[:12],
            "d_tag": d_tag,
            "relays": results,
        }

    async def react(
        self,
        identity_id: str,
        user_id: UUID,
        event_id: str,
        reaction: str = "+",
    ) -> dict:
        """React to an event (kind 7)."""
        self._check_rate_limit(identity_id)
        identity = await self._load_identity(identity_id, user_id)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        event = self._build_and_sign(
            private_key,
            kind=KIND_REACTION,
            content=reaction,
            tags=[["e", event_id]],
        )
        results = await self.relay_pool.publish_event(event, relays=identity.relay_urls)
        _rate_limiter.record(identity_id)

        return {"status": "reacted", "reaction": reaction, "relays": results}

    async def repost(
        self,
        identity_id: str,
        user_id: UUID,
        event_id: str,
    ) -> dict:
        """Repost an event (kind 6)."""
        self._check_rate_limit(identity_id)
        identity = await self._load_identity(identity_id, user_id)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        event = self._build_and_sign(
            private_key,
            kind=KIND_REPOST,
            content="",
            tags=[["e", event_id]],
        )
        results = await self.relay_pool.publish_event(event, relays=identity.relay_urls)
        _rate_limiter.record(identity_id)

        return {"status": "reposted", "event_id": event_id[:12], "relays": results}

    async def reply(
        self,
        identity_id: str,
        user_id: UUID,
        event_id: str,
        content: str,
    ) -> dict:
        """Reply to an event (kind 1 with e-tag)."""
        return await self.post_note(
            identity_id, user_id, content,
            reply_to=event_id,
        )

    async def follow(
        self,
        identity_id: str,
        user_id: UUID,
        pubkeys: List[str],
    ) -> dict:
        """Follow users by updating kind-3 contact list."""
        self._check_rate_limit(identity_id)
        identity = await self._load_identity(identity_id, user_id)

        # Get current follow list
        current_follows = await self._get_follow_list(identity.pubkey_hex)

        # Merge new follows (deduplicate)
        follow_set = set(current_follows)
        added = []
        for pk in pubkeys:
            pk_hex = _resolve_pubkey(pk)
            if pk_hex not in follow_set:
                follow_set.add(pk_hex)
                added.append(pk_hex)

        # Publish updated kind-3
        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        tags = [["p", pk] for pk in follow_set]
        event = self._build_and_sign(private_key, kind=KIND_CONTACTS, content="", tags=tags)
        await self.relay_pool.publish_event(event, relays=identity.relay_urls)

        # Update stats
        async with async_session_factory() as session:
            await session.execute(
                update(NostrIdentity)
                .where(NostrIdentity.id == identity.id)
                .values(following_count=len(follow_set))
            )
            await session.commit()

        _rate_limiter.record(identity_id)
        return {"following": len(follow_set), "added": len(added)}

    async def unfollow(
        self,
        identity_id: str,
        user_id: UUID,
        pubkeys: List[str],
    ) -> dict:
        """Unfollow users by updating kind-3 contact list."""
        self._check_rate_limit(identity_id)
        identity = await self._load_identity(identity_id, user_id)

        current_follows = await self._get_follow_list(identity.pubkey_hex)
        follow_set = set(current_follows)

        removed = []
        for pk in pubkeys:
            pk_hex = _resolve_pubkey(pk)
            if pk_hex in follow_set:
                follow_set.discard(pk_hex)
                removed.append(pk_hex)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        tags = [["p", pk] for pk in follow_set]
        event = self._build_and_sign(private_key, kind=KIND_CONTACTS, content="", tags=tags)
        await self.relay_pool.publish_event(event, relays=identity.relay_urls)

        async with async_session_factory() as session:
            await session.execute(
                update(NostrIdentity)
                .where(NostrIdentity.id == identity.id)
                .values(following_count=len(follow_set))
            )
            await session.commit()

        _rate_limiter.record(identity_id)
        return {"following": len(follow_set), "removed": len(removed)}

    async def delete_event(
        self,
        identity_id: str,
        user_id: UUID,
        event_ids: List[str],
    ) -> dict:
        """Request deletion of events (kind 5)."""
        self._check_rate_limit(identity_id)
        identity = await self._load_identity(identity_id, user_id)

        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        tags = [["e", eid] for eid in event_ids]
        event = self._build_and_sign(
            private_key, kind=KIND_DELETE, content="deleted", tags=tags
        )
        results = await self.relay_pool.publish_event(event, relays=identity.relay_urls)
        _rate_limiter.record(identity_id)

        return {"status": "deletion_requested", "event_ids": [e[:12] for e in event_ids], "relays": results}

    # -------------------------------------------------------------------
    # Search & discovery
    # -------------------------------------------------------------------

    async def search(
        self,
        query: str,
        kinds: Optional[List[int]] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """NIP-50 full-text search."""
        limit = min(limit, MAX_RESULTS)
        events = await self.relay_pool.search_events(query, kinds=kinds, limit=limit)

        # Collect profile names for authors
        await self._cache_author_names(events)

        return {
            "query": query,
            "count": len(events),
            "results": [_compact_event(e) for e in events],
        }

    async def get_feed(
        self,
        identity_id: str,
        user_id: UUID,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """Get recent posts from followed users."""
        identity = await self._load_identity(identity_id, user_id)
        limit = min(limit, MAX_RESULTS)

        # Get follow list
        follows = await self._get_follow_list(identity.pubkey_hex)
        if not follows:
            return {"count": 0, "results": [], "note": "Not following anyone yet"}

        # Query for recent posts from followed accounts
        filters = {
            "kinds": [KIND_TEXT_NOTE],
            "authors": follows[:50],  # Limit authors to avoid huge filter
        }
        events = await self.relay_pool.query_events(filters, relays=identity.relay_urls, limit=limit)

        await self._cache_author_names(events)

        return {
            "count": len(events),
            "results": [_compact_event(e) for e in events],
        }

    async def get_thread(
        self,
        event_id: str,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """Get a note and its replies."""
        limit = min(limit, MAX_RESULTS)

        # Get the root event
        root_events = await self.relay_pool.query_events(
            {"ids": [event_id]}, limit=1
        )

        # Get replies (events referencing this event)
        reply_events = await self.relay_pool.query_events(
            {"kinds": [KIND_TEXT_NOTE], "#e": [event_id]}, limit=limit
        )

        all_events = root_events + reply_events
        await self._cache_author_names(all_events)

        root = _compact_event(root_events[0]) if root_events else None
        replies = [_compact_event(e) for e in reply_events]

        result: Dict[str, Any] = {"event_id": event_id[:12], "reply_count": len(replies)}
        if root:
            result["root"] = root
        result["replies"] = replies
        return result

    async def get_profile(
        self,
        pubkey_or_npub: str,
        include_posts: bool = False,
    ) -> dict:
        """Get a user's profile and optionally recent posts."""
        pubkey_hex = _resolve_pubkey(pubkey_or_npub)

        # Get kind-0 metadata
        metadata_events = await self.relay_pool.query_events(
            {"kinds": [KIND_METADATA], "authors": [pubkey_hex]}, limit=1
        )

        profile: Dict[str, Any] = {"pubkey": pubkey_hex[:12] + "..."}
        if metadata_events:
            try:
                meta = json.loads(metadata_events[0].get("content", "{}"))
                # Sanitize profile fields — attacker-controlled Nostr metadata (HIGH-2)
                raw_name = meta.get("name", "")
                raw_about = meta.get("about", "")
                sanitized_name, _ = sanitize_external_content(
                    raw_name, source="nostr_profile", ml_scan=False,
                )
                sanitized_about, _ = sanitize_external_content(
                    raw_about, source="nostr_profile", ml_scan=False,
                )
                profile["name"] = sanitized_name
                profile["about"] = _truncate(sanitized_about, 300)
                profile["picture"] = meta.get("picture", "")
                profile["nip05"] = meta.get("nip05", "")
                profile["lud16"] = meta.get("lud16", "")
                _name_cache.set(pubkey_hex, meta.get("name", ""))
            except json.JSONDecodeError:
                pass

        # Get follower/following counts (approximate)
        contacts_events = await self.relay_pool.query_events(
            {"kinds": [KIND_CONTACTS], "authors": [pubkey_hex]}, limit=1
        )
        if contacts_events:
            p_tags = [t for t in contacts_events[0].get("tags", []) if t[0] == "p"]
            profile["following"] = len(p_tags)

        if include_posts:
            posts = await self.relay_pool.query_events(
                {"kinds": [KIND_TEXT_NOTE], "authors": [pubkey_hex]}, limit=5
            )
            profile["recent_posts"] = [_compact_event(e) for e in posts]

        return profile

    async def get_engagement(
        self,
        identity_id: str,
        user_id: UUID,
        since: Optional[int] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """Get engagement (reactions, replies, reposts) for an identity."""
        identity = await self._load_identity(identity_id, user_id)
        limit = min(limit, MAX_RESULTS)

        filters: Dict[str, Any] = {
            "kinds": [KIND_REACTION, KIND_REPOST, KIND_TEXT_NOTE],
            "#p": [identity.pubkey_hex],
        }
        if since:
            filters["since"] = since

        events = await self.relay_pool.query_events(
            filters, relays=identity.relay_urls, limit=limit
        )

        # Categorize
        reactions = [e for e in events if e.get("kind") == KIND_REACTION]
        reposts = [e for e in events if e.get("kind") == KIND_REPOST]
        replies = [e for e in events if e.get("kind") == KIND_TEXT_NOTE]

        await self._cache_author_names(events)

        return {
            "summary": {
                "reactions": len(reactions),
                "reposts": len(reposts),
                "replies": len(replies),
                "total": len(events),
            },
            "recent": [_compact_event(e) for e in events[:5]],
        }

    # -------------------------------------------------------------------
    # Zap actions (conditional on USE_LND)
    # -------------------------------------------------------------------

    async def send_zap(
        self,
        identity_id: str,
        user_id: UUID,
        target: str,
        amount_sats: int,
        comment: str = "",
    ) -> dict:
        """Send a zap to a user or event (requires USE_LND=true)."""
        if not settings.use_lnd:
            return {"error": "Zaps require USE_LND=true and a connected LND node"}

        identity = await self._load_identity(identity_id, user_id)

        # Resolve target pubkey to Lightning address
        target_hex = _resolve_pubkey(target)
        profile = await self.get_profile(target)

        lud16 = profile.get("lud16", "")
        if not lud16:
            return {"error": "Target does not have a Lightning address (lud16) for zaps"}

        # Resolve LNURL
        import httpx
        lnurl_data = await self._resolve_lnurl_pay(lud16)
        if not lnurl_data:
            return {"error": "Failed to resolve Lightning address"}

        if not lnurl_data.get("allowsNostr"):
            return {"error": "Recipient's Lightning address does not support Nostr zaps"}

        # Build kind 9734 zap request
        nsec = decrypt_nsec(identity.encrypted_nsec)
        from pynostr.key import PrivateKey
        private_key = PrivateKey.from_nsec(nsec)
        del nsec

        zap_request = self._build_zap_request(
            private_key, identity.pubkey_hex, target_hex,
            amount_sats, comment, identity.relay_urls or [],
        )

        # Get invoice from LNURL callback
        callback = lnurl_data.get("callback", "")
        amount_msats = amount_sats * 1000

        # SSRF protection: validate callback URL from external LNURL response
        try:
            _validate_http_url(callback)
        except ValueError as exc:
            logger.warning(f"SSRF blocked LNURL callback: {exc}")
            return {"error": "Invalid LNURL callback URL"}

        # SA2-07: Re-check DNS immediately before connecting to mitigate rebinding
        cb_parsed = urlparse(callback)
        if cb_parsed.hostname:
            try:
                _resolve_and_check_private(cb_parsed.hostname, label="LNURL callback (pre-connect)")
            except ValueError as exc:
                logger.warning(f"SSRF blocked LNURL callback (rebind): {exc}")
                return {"error": "Invalid LNURL callback URL"}

        async with httpx.AsyncClient(timeout=10) as client:
            params = {
                "amount": amount_msats,
                "nostr": json.dumps(zap_request),
            }
            if comment:
                params["comment"] = comment
            resp = await client.get(callback, params=params)
            if resp.status_code != 200:
                return {"error": f"LNURL callback failed: HTTP {resp.status_code}"}
            invoice_data = resp.json()

        bolt11 = invoice_data.get("pr", "")
        if not bolt11:
            return {"error": "No invoice returned from LNURL callback"}

        # Pay via LND (budget-enforced)
        # GAP-1: Fixed — correct BitcoinBudgetService constructor & method signatures
        try:
            from app.services.lnd_service import LNDService
            from app.services.bitcoin_budget_service import BitcoinBudgetService
            from app.models.bitcoin_budget import TransactionType, TransactionStatus

            lnd = LNDService()

            # Budget check requires a DB session
            async with async_session_factory() as budget_db:
                budget = BitcoinBudgetService(db=budget_db)

                # Check budget using correct signature
                budget_result = await budget.check_spend(
                    amount_sats=amount_sats,
                    user_id=user_id,
                    fee_sats=0,
                )
                if not budget_result.allowed:
                    return {"error": f"Zap blocked by budget: {budget_result.reason}"}

                # Pay invoice
                payment, pay_error = await lnd.send_payment_sync(bolt11)
                if pay_error:
                    return {"error": f"Zap payment failed: {pay_error}"}

                # Record transaction using correct signature
                await budget.record_transaction(
                    user_id=user_id,
                    tx_type=TransactionType.LIGHTNING_SEND,
                    amount_sats=amount_sats,
                    description=f"Nostr zap to {target[:20]}",
                    status=TransactionStatus.CONFIRMED,
                )
                await budget_db.commit()

            return {
                "status": "zap_sent",
                "amount_sats": amount_sats,
                "target": target[:12] + "...",
                "comment": comment[:50] if comment else "",
            }
        except Exception as e:
            logger.error(f"Zap payment failed: {e}")
            return {"error": f"Zap payment failed: {str(e)[:100]}"}

    async def get_zap_receipts(
        self,
        identity_id: str,
        user_id: UUID,
        since: Optional[int] = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict:
        """Get zap receipts for an identity."""
        if not settings.use_lnd:
            return {"error": "Zap receipts require USE_LND=true"}

        identity = await self._load_identity(identity_id, user_id)
        limit = min(limit, MAX_RESULTS)

        filters: Dict[str, Any] = {
            "kinds": [KIND_ZAP_RECEIPT],
            "#p": [identity.pubkey_hex],
        }
        if since:
            filters["since"] = since

        events = await self.relay_pool.query_events(
            filters, relays=identity.relay_urls, limit=limit
        )

        # Parse zap amounts from bolt11 descriptions
        total_sats = 0
        zaps = []
        for evt in events:
            zap_info = self._parse_zap_receipt(evt)
            if zap_info:
                total_sats += zap_info.get("amount_sats", 0)
                zaps.append(zap_info)

        return {
            "total_sats": total_sats,
            "count": len(zaps),
            "zaps": zaps[:limit],
        }

    # -------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------

    async def _load_identity(self, identity_id: str, user_id: UUID) -> NostrIdentity:
        """Load and validate an identity belongs to the user."""
        async with async_session_factory() as session:
            result = await session.execute(
                select(NostrIdentity).where(
                    NostrIdentity.id == UUID(identity_id),
                    NostrIdentity.user_id == user_id,
                    NostrIdentity.is_active == True,
                )
            )
            identity = result.scalar_one_or_none()

        if not identity:
            raise ValueError(f"Identity not found or access denied: {identity_id}")
        return identity

    def _check_rate_limit(self, identity_id: str):
        """Check posting rate limits, raise ValueError if exceeded."""
        if not _rate_limiter.check(identity_id, 3600, settings.nostr_post_rate_limit_hour):
            raise ValueError(
                f"Rate limit exceeded: max {settings.nostr_post_rate_limit_hour} posts/hour"
            )
        if not _rate_limiter.check(identity_id, 86400, settings.nostr_post_rate_limit_day):
            raise ValueError(
                f"Rate limit exceeded: max {settings.nostr_post_rate_limit_day} posts/day"
            )

    async def _increment_post_count(self, identity_id: UUID):
        """Increment post count and update last_posted_at."""
        async with async_session_factory() as session:
            await session.execute(
                update(NostrIdentity)
                .where(NostrIdentity.id == identity_id)
                .values(
                    post_count=NostrIdentity.post_count + 1,
                    last_posted_at=utc_now(),
                )
            )
            await session.commit()

    async def _get_follow_list(self, pubkey_hex: str) -> List[str]:
        """Get the list of pubkeys this identity follows from relays."""
        events = await self.relay_pool.query_events(
            {"kinds": [KIND_CONTACTS], "authors": [pubkey_hex]}, limit=1
        )
        if not events:
            return []
        return [t[1] for t in events[0].get("tags", []) if len(t) >= 2 and t[0] == "p"]

    async def _cache_author_names(self, events: List[dict]):
        """Populate the name cache for event authors."""
        unknown = set()
        for evt in events:
            pk = evt.get("pubkey", "")
            if pk and not _name_cache.get(pk):
                unknown.add(pk)

        if not unknown:
            return

        # Batch lookup kind-0 metadata
        authors = list(unknown)[:20]  # Limit batch size
        metadata = await self.relay_pool.query_events(
            {"kinds": [KIND_METADATA], "authors": authors}, limit=len(authors)
        )
        for m in metadata:
            try:
                meta = json.loads(m.get("content", "{}"))
                name = meta.get("name", "")
                if name:
                    _name_cache.set(m.get("pubkey", ""), name)
            except json.JSONDecodeError:
                pass

    async def _resolve_lnurl_pay(self, lud16: str) -> Optional[dict]:
        """Resolve a Lightning address (lud16) to LNURL pay data."""
        import httpx

        parts = lud16.split("@")
        if len(parts) != 2:
            return None

        username, domain = parts
        url = f"https://{domain}/.well-known/lnurlp/{username}"

        # SSRF protection: validate constructed URL before making request
        try:
            _validate_http_url(url)
        except ValueError as exc:
            logger.warning(f"SSRF blocked LNURL resolution for {lud16}: {exc}")
            return None

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.debug(f"LNURL resolve failed for {lud16}: {e}")
        return None

    # -------------------------------------------------------------------
    # Event building & signing
    # -------------------------------------------------------------------

    def _build_and_sign(
        self,
        private_key,
        kind: int,
        content: str,
        tags: Optional[List[list]] = None,
    ) -> dict:
        """Build, sign, and serialize a Nostr event."""
        from pynostr.event import Event

        event = Event(content=content, kind=kind)
        event.public_key = private_key.public_key.hex()
        if tags:
            for tag in tags:
                event.tags.append(tag)
        event.sign(private_key.hex())

        return {
            "id": event.id,
            "pubkey": event.public_key,
            "created_at": event.created_at,
            "kind": event.kind,
            "tags": event.tags,
            "content": event.content,
            "sig": event.sig,
        }

    def _build_metadata_event(
        self,
        private_key,
        name: str,
        about: str,
        picture: str = "",
        nip05: str = "",
        lud16: str = "",
    ) -> dict:
        """Build a kind-0 profile metadata event."""
        metadata = {"name": name, "about": about}
        if picture:
            metadata["picture"] = picture
        if nip05:
            metadata["nip05"] = nip05
        if lud16:
            metadata["lud16"] = lud16

        return self._build_and_sign(
            private_key,
            kind=KIND_METADATA,
            content=json.dumps(metadata),
        )

    def _build_text_note(
        self,
        private_key,
        content: str,
        hashtags: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> dict:
        """Build a kind-1 text note event."""
        tags = []
        if reply_to:
            tags.append(["e", reply_to, "", "reply"])
        if hashtags:
            for ht in hashtags:
                tags.append(["t", ht.lower().strip("#")])

        return self._build_and_sign(
            private_key,
            kind=KIND_TEXT_NOTE,
            content=content,
            tags=tags,
        )

    def _build_article(
        self,
        private_key,
        title: str,
        content: str,
        summary: str = "",
        hashtags: Optional[List[str]] = None,
        image: str = "",
    ) -> dict:
        """Build a kind-30023 long-form article event."""
        import hashlib as _hashlib

        # Generate a d-tag from the title for deduplication
        d_tag = title.lower().replace(" ", "-")[:50]

        tags = [
            ["d", d_tag],
            ["title", title],
        ]
        if summary:
            tags.append(["summary", summary])
        if image:
            tags.append(["image", image])
        if hashtags:
            for ht in hashtags:
                tags.append(["t", ht.lower().strip("#")])

        return self._build_and_sign(
            private_key,
            kind=KIND_LONG_FORM,
            content=content,
            tags=tags,
        )

    def _build_zap_request(
        self,
        private_key,
        sender_pubkey: str,
        recipient_pubkey: str,
        amount_sats: int,
        comment: str,
        relays: List[str],
    ) -> dict:
        """Build a kind 9734 zap request event."""
        tags = [
            ["p", recipient_pubkey],
            ["amount", str(amount_sats * 1000)],  # Amount in millisats
            ["relays"] + relays[:5],
        ]
        if comment:
            content = comment
        else:
            content = ""

        return self._build_and_sign(
            private_key,
            kind=KIND_ZAP_REQUEST,
            content=content,
            tags=tags,
        )

    def _parse_zap_receipt(self, event: dict) -> Optional[dict]:
        """Parse a kind-9735 zap receipt into a compact summary."""
        tags = event.get("tags", [])
        bolt11_tag = None
        sender = None
        amount_sats = 0

        for tag in tags:
            if len(tag) >= 2:
                if tag[0] == "bolt11":
                    bolt11_tag = tag[1]
                elif tag[0] == "description":
                    try:
                        desc = json.loads(tag[1])
                        sender = desc.get("pubkey", "")[:12]
                        # Extract amount from zap request tags
                        for dtag in desc.get("tags", []):
                            if dtag[0] == "amount" and len(dtag) >= 2:
                                amount_sats = int(dtag[1]) // 1000
                    except (json.JSONDecodeError, ValueError, IndexError):
                        pass

        if amount_sats == 0:
            return None

        return {
            "sender": (sender + "...") if sender else "unknown",
            "amount_sats": amount_sats,
            "time": _relative_time(event.get("created_at", 0)),
        }
