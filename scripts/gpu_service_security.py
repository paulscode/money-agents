"""
Shared security utilities for GPU services.

Provides:
  - validate_url(): SSRF protection — blocks internal/private IPs (RT-04)
  - gpu_auth_middleware(): API key authentication middleware (RT-05)
  - upload_size_middleware(): Request body size limiting (RT-12)

Import in each GPU service's app.py:

    from scripts.gpu_service_security import validate_url, add_security_middleware

    add_security_middleware(app)  # adds auth + upload size
    # Then use validate_url(url) before any httpx.get(url)
"""

import hmac
import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

# ---------------------------------------------------------------------------
# RT-04: SSRF Protection — validate_url()
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / cloud metadata
    ipaddress.ip_network("127.0.0.0/8"),       # loopback
    ipaddress.ip_network("::1/128"),           # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),          # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),         # IPv6 link-local
]

# These host names map to internal services and must be blocked
_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata",
    "localhost",
    "0.0.0.0",
    "127.0.0.1",
    "[::]",
    "[::1]",
}

_ALLOWED_SCHEMES = {"http", "https"}

# Host-internal services that GPU services legitimately call (peer services)
# These are resolved to host.docker.internal or localhost in dev
_ALLOWED_INTERNAL_HOSTS: set[str] = set()


def _is_ip_private(ip_str: str) -> bool:
    """Check whether an IP address falls in any blocked range."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except ValueError:
        return False


def validate_url(url: str, *, allow_internal: bool = False) -> bool:
    """Validate a URL for SSRF safety.

    Returns True if the URL is safe to fetch, False otherwise.

    Args:
        url: The URL to validate.
        allow_internal: If True, skip private-IP checks.  Used when the
            caller is the backend orchestrating local GPU services
            (which live on private addresses by design).
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    # Scheme check
    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # Blocked hostname check
    if hostname.lower() in _BLOCKED_HOSTNAMES:
        return False

    if allow_internal:
        return True

    # Resolve hostname → IP and check against blocked ranges
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _type, _proto, _canonname, sockaddr in resolved:
            ip_str = sockaddr[0]
            if _is_ip_private(ip_str):
                return False
    except socket.gaierror:
        # Cannot resolve → reject
        return False

    return True


# ---------------------------------------------------------------------------
# RT-05: GPU Service Authentication Middleware
# ---------------------------------------------------------------------------

_GPU_API_KEY: Optional[str] = os.environ.get("GPU_SERVICE_API_KEY", "") or None
_GPU_INTERNAL_KEY: Optional[str] = os.environ.get("GPU_INTERNAL_API_KEY", "") or None

# GAP-4: Fail-closed when no API key is configured.
# start.py auto-generates GPU_SERVICE_API_KEY via ensure_service_api_keys(),
# so this only triggers when services are started manually without start.py.
# Explicit opt-out: set GPU_AUTH_SKIP=true for development without keys.
_GPU_AUTH_SKIP = os.environ.get("GPU_AUTH_SKIP", "").lower() in ("1", "true", "yes")

if _GPU_API_KEY is None:
    import logging as _logging
    if _GPU_AUTH_SKIP:
        _logging.getLogger(__name__).warning(
            "GPU_SERVICE_API_KEY is not set and GPU_AUTH_SKIP=true — GPU service "
            "endpoints are UNAUTHENTICATED. This is only safe for local development."
        )
    else:
        _logging.getLogger(__name__).error(
            "GPU_SERVICE_API_KEY is not set — GPU service endpoints will reject "
            "all requests. Set GPU_SERVICE_API_KEY in .env or use GPU_AUTH_SKIP=true "
            "for development. start.py auto-generates this key."
        )

# Paths that skip ALL authentication (health checks, docs only).
_PUBLIC_PATHS = {
    "/health", "/docs", "/openapi.json", "/redoc",
}

# GAP-5: Management endpoints require internal key (if configured) or
# fall back to the main GPU API key for authentication. These are used
# by the GPU lifecycle service for cooperative VRAM eviction.
_INTERNAL_PATHS = {
    "/unload", "/shutdown", "/reload",
}


class GPUAuthMiddleware(BaseHTTPMiddleware):
    """Require X-API-Key header on all non-public endpoints.
    
    Management endpoints (/unload, /shutdown, /reload) accept either
    the GPU_INTERNAL_API_KEY or GPU_SERVICE_API_KEY for authentication,
    allowing the lifecycle service to use a separate internal credential.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Truly public paths — no auth needed
        if path in _PUBLIC_PATHS:
            return await call_next(request)

        # GAP-5: Internal management paths — require internal key or main key
        if path in _INTERNAL_PATHS:
            # If neither key is configured, check explicit dev skip
            if _GPU_API_KEY is None and _GPU_INTERNAL_KEY is None:
                if _GPU_AUTH_SKIP:
                    return await call_next(request)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "GPU service API key not configured. "
                             "Set GPU_SERVICE_API_KEY or GPU_AUTH_SKIP=true for development."},
                )
            provided = request.headers.get("X-API-Key", "")
            if not provided:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized — missing X-API-Key for management endpoint"},
                )
            # Accept either internal key or main key
            key_ok = False
            if _GPU_INTERNAL_KEY and hmac.compare_digest(provided, _GPU_INTERNAL_KEY):
                key_ok = True
            elif _GPU_API_KEY and hmac.compare_digest(provided, _GPU_API_KEY):
                key_ok = True
            if not key_ok:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized — invalid X-API-Key"},
                )
            return await call_next(request)

        # Regular endpoints — require main API key
        if _GPU_API_KEY is None:
            # GAP-4: Fail-closed when no key configured (unless dev skip)
            if _GPU_AUTH_SKIP:
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={"detail": "GPU service API key not configured. "
                         "Set GPU_SERVICE_API_KEY or GPU_AUTH_SKIP=true for development."},
            )

        provided = request.headers.get("X-API-Key", "")
        # SA2-14: Use constant-time comparison to prevent timing side-channel
        if not provided or not hmac.compare_digest(provided, _GPU_API_KEY):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized — missing or invalid X-API-Key"},
            )
        return await call_next(request)


# ---------------------------------------------------------------------------
# RT-12: Upload Size Limiting Middleware
# ---------------------------------------------------------------------------

_MAX_UPLOAD_BYTES = int(os.environ.get("GPU_MAX_UPLOAD_BYTES", 100 * 1024 * 1024))  # 100 MB


class UploadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the configured max.
    
    Also enforces size limits on chunked transfer-encoded requests
    (which lack Content-Length) by wrapping the body stream.
    """

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _MAX_UPLOAD_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "detail": f"Request body too large. Maximum allowed: {_MAX_UPLOAD_BYTES} bytes"
                },
            )

        # For chunked/streaming requests without Content-Length,
        # wrap the receive callable to count bytes on the fly.
        if request.method in ("POST", "PUT", "PATCH") and not content_length:
            bytes_received = 0
            original_receive = request._receive

            async def size_limited_receive():
                nonlocal bytes_received
                message = await original_receive()
                if message.get("type") == "http.request":
                    body = message.get("body", b"")
                    bytes_received += len(body)
                    if bytes_received > _MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Request body too large. Maximum allowed: {_MAX_UPLOAD_BYTES} bytes",
                        )
                return message

            request._receive = size_limited_receive

        return await call_next(request)


# ---------------------------------------------------------------------------
# Convenience: add all security middleware at once
# ---------------------------------------------------------------------------


def add_security_middleware(app) -> None:
    """Add authentication and upload-size middlewares to a FastAPI app.

    Call this in each GPU service's startup:

        from scripts.gpu_service_security import add_security_middleware
        add_security_middleware(app)
    """
    # Order matters: outermost middleware runs first.
    # Upload size should reject early, before auth wastes time.
    app.add_middleware(UploadSizeLimitMiddleware)
    app.add_middleware(GPUAuthMiddleware)
