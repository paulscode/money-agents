from passlib.context import CryptContext
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, Set
import ipaddress
import jwt
from jwt.exceptions import PyJWTError
import logging
import os
import socket
import threading
from urllib.parse import urlparse
import uuid

from app.core.config import settings

_logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Token blocklist — Redis-backed with in-memory fallback (RT-13)
#
# Revoked JTIs are stored in Redis DB 4 as keys with TTL equal to the
# remaining token lifetime.  This survives process restarts and is shared
# across multiple workers.  Falls back to an in-memory set when Redis
# is unavailable.
# ---------------------------------------------------------------------------

_revoked_jtis: dict[str, float] = {}   # in-memory fallback: {jti: expiry_timestamp}
_revoked_lock = threading.Lock()

_redis_client = None  # will be set lazily


def _get_redis():
    """Lazy-initialise a synchronous Redis client for blocklist ops."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return None

    try:
        import redis as _redis_mod
        # Use DB 4 to avoid collisions with app(0), broker(1), results(2), rate-limit(3)
        base = redis_url.rsplit("/", 1)[0]
        _redis_client = _redis_mod.Redis.from_url(
            f"{base}/4", decode_responses=True, socket_connect_timeout=2,
        )
        _redis_client.ping()
        _logger.info("Token blocklist using Redis (DB 4)")
        return _redis_client
    except Exception as exc:
        # SGA3-M4: In production, refuse to operate without Redis — in-memory
        # fallback would silently break token revocation across workers.
        if settings.environment == "production":
            raise RuntimeError(
                "SGA3-M4: Redis is required for the token blocklist in "
                "production mode but is unavailable. Set ENVIRONMENT=development "
                "to allow in-memory fallback. Error: %s" % exc,
            )
        # SA3-M3: Use CRITICAL level when Redis is unavailable — in-memory
        # fallback means each worker has an independent blocklist, so a
        # token revoked in worker A remains valid in worker B.
        _logger.critical(
            "SA3-M3: Redis unavailable for token blocklist — in-memory "
            "fallback is NOT safe for multi-worker deployments.  Token "
            "revocation will be unreliable.  Error: %s", exc,
        )
        return None


def revoke_token(jti: str, expires_in: Optional[int] = None) -> None:
    """Add a JTI to the blocklist so the token can no longer be used.

    Args:
        jti: The JWT ID to revoke.
        expires_in: TTL in seconds.  Defaults to the configured token
            expiry so Redis keys auto-expire after the token would have
            expired anyway.
    """
    if expires_in is None:
        expires_in = settings.access_token_expire_minutes * 60

    r = _get_redis()
    if r is not None:
        try:
            r.setex(f"revoked:{jti}", expires_in, "1")
            return
        except Exception as exc:
            _logger.warning("Redis revoke_token failed, falling back to in-memory: %s", exc)

    # In-memory fallback with TTL
    with _revoked_lock:
        import time as _time_mod
        expiry = _time_mod.time() + expires_in
        _revoked_jtis[jti] = expiry
        # Evict expired entries periodically (every ~50 additions)
        if len(_revoked_jtis) % 50 == 0:
            now = _time_mod.time()
            expired = [k for k, v in _revoked_jtis.items() if v < now]
            for k in expired:
                del _revoked_jtis[k]


def is_token_revoked(jti: str) -> bool:
    """Check whether a JTI has been revoked."""
    r = _get_redis()
    if r is not None:
        try:
            return r.exists(f"revoked:{jti}") > 0
        except Exception as exc:
            _logger.warning("Redis is_token_revoked failed, using in-memory: %s", exc)

    with _revoked_lock:
        import time as _time_mod
        expiry = _revoked_jtis.get(jti)
        if expiry is None:
            return False
        if expiry < _time_mod.time():
            # Expired entry — clean up and report not revoked
            del _revoked_jtis[jti]
            return False
        return True


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash.
    
    SGA-L6: Passwords > 72 UTF-8 bytes are truncated at verification time
    (for backwards compatibility with any existing hashes created before
    the rejection was added to get_password_hash).
    """
    if len(plain_password.encode('utf-8')) > 72:
        plain_password = plain_password.encode('utf-8')[:72].decode('utf-8', errors='ignore')
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash.
    
    SGA-L6: Rejects passwords exceeding 72 UTF-8 bytes (bcrypt limit).
    """
    pw_bytes = password.encode('utf-8')
    if len(pw_bytes) > 72:
        raise ValueError(
            "Password is too long. Maximum length is 72 bytes in UTF-8 encoding. "
            "Please choose a shorter password."
        )
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token with a unique JTI for revocation support."""
    to_encode = data.copy()
    
    if expires_delta:
        expire = utc_now() + expires_delta
    else:
        expire = utc_now() + timedelta(minutes=settings.access_token_expire_minutes)
    
    to_encode.update({
        "exp": expire,
        "iat": utc_now(),
        "jti": uuid.uuid4().hex,
        "iss": "money-agents",
        "aud": "money-agents",
        "typ": "access",       # SA3-L2: Explicit token type to prevent confusion
    })
    encoded_jwt = jwt.encode(to_encode, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)
    
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """Create JWT refresh token for session renewal (SGA3-L5).

    Refresh tokens have a 7-day expiry and ``typ: refresh`` to prevent
    them being used as access tokens.
    """
    to_encode = data.copy()
    expire = utc_now() + timedelta(days=7)
    to_encode.update({
        "exp": expire,
        "iat": utc_now(),
        "jti": uuid.uuid4().hex,
        "iss": "money-agents",
        "aud": "money-agents",
        "typ": "refresh",
    })
    return jwt.encode(to_encode, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def decode_refresh_token(token: str) -> Optional[dict]:
    """Decode and verify a refresh token.

    Returns None if the token is invalid, expired, revoked, or not a
    refresh token.
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            audience="money-agents",
            issuer="money-agents",
        )
        # Must be a refresh token
        if payload.get("typ") != "refresh":
            return None
        # Check blocklist
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            return None
        return payload
    except PyJWTError:
        return None


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify JWT token. Returns None if expired, invalid, or revoked."""
    try:
        payload = jwt.decode(
            token, settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            audience="money-agents",
            issuer="money-agents",
        )
        # SGA3-L1: Positively assert typ=access (not just reject refresh)
        if payload.get("typ") != "access":
            return None
        # Check blocklist
        jti = payload.get("jti")
        if jti and is_token_revoked(jti):
            return None
        return payload
    except PyJWTError:
        return None


# ---------------------------------------------------------------------------
# SSRF Guard — Validate target URLs before making outbound requests (SA2-03)
# ---------------------------------------------------------------------------

# Hosts that are allowed to bypass private-IP checks (e.g. Docker internal)
_SSRF_ALLOWED_HOSTS: set[str] = set()
_ssrf_hosts_loaded = False


def _load_ssrf_allowed_hosts() -> set[str]:
    """Load allowed hosts from env var SSRF_ALLOWED_HOSTS (comma-separated)."""
    global _ssrf_hosts_loaded, _SSRF_ALLOWED_HOSTS
    if _ssrf_hosts_loaded:
        return _SSRF_ALLOWED_HOSTS
    raw = os.environ.get("SSRF_ALLOWED_HOSTS", "host.docker.internal")
    _SSRF_ALLOWED_HOSTS = {h.strip().lower() for h in raw.split(",") if h.strip()}
    _ssrf_hosts_loaded = True
    return _SSRF_ALLOWED_HOSTS


def _is_private_ip(ip_str: str) -> bool:
    """Return True if ip_str is a private, loopback, or link-local address."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
        )
    except ValueError:
        return True  # unparseable → reject


def validate_target_url(url: str, *, return_resolved_ips: bool = False) -> str | tuple[str, list[str]]:
    """Validate that *url* does not target a private/internal IP address.

    Resolves the hostname via DNS and checks all resulting IPs.  Raises
    ``ValueError`` if the URL targets a private address or is malformed.

    Args:
        url: The URL to validate.
        return_resolved_ips: If True, returns ``(url, resolved_ips)`` so
            callers can pin connections to resolved addresses and prevent
            DNS rebinding attacks (SGA3-M1).

    Returns:
        The validated URL (or ``(url, ips)`` tuple if *return_resolved_ips*).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Malformed URL: {url}")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError(f"URL has no hostname: {url}")

    # Allow explicitly configured hosts (e.g. host.docker.internal)
    allowed = _load_ssrf_allowed_hosts()
    if hostname in allowed:
        if return_resolved_ips:
            return url, []
        return url

    # Block well-known cloud metadata endpoints by hostname
    if hostname in ("metadata.google.internal", "metadata"):
        raise ValueError(f"Cloud metadata hostname blocked: {hostname}")

    # Resolve DNS and check all IPs
    try:
        infos = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ValueError(f"DNS resolution failed for: {hostname}")

    resolved_ips: list[str] = []
    for family, _type, _proto, _canonname, sockaddr in infos:
        ip_str = sockaddr[0]
        resolved_ips.append(ip_str)
        if _is_private_ip(ip_str):
            raise ValueError(
                f"URL resolves to private/internal IP ({ip_str}): {url}"
            )

    if return_resolved_ips:
        return url, resolved_ips
    return url
