from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager
import logging
import re

from app.core.config import settings
from app.core.database import init_db
from app.core.rate_limit import limiter
from app.api.api import api_router
from app.services.startup_service import initialize_on_startup, check_llm_availability
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded


# SGA3-L4: Global log filter to redact accidental secret leakage
class SensitiveDataFilter(logging.Filter):
    """Redact potential secrets from log messages."""
    PATTERNS = [
        (re.compile(r'(password|passwd|pwd)\s*[=:]\s*\S+', re.I), r'\1=***'),
        (re.compile(r'(api[_-]?key|secret[_-]?key|token|macaroon|nsec)\s*[=:]\s*\S+', re.I), r'\1=***'),
        (re.compile(r'(Authorization:\s*Bearer\s+)\S+', re.I), r'\1***'),
    ]

    def filter(self, record):
        if record.args:
            # Format first so patterns match the final string
            record.msg = record.getMessage()
            record.args = ()
        msg = str(record.msg)
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        return True


# Configure root logger so all application log messages (INFO+) are visible
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addFilter(SensitiveDataFilter())

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting Money Agents API...")
    
    # SGA3-L12: Validate encryption health before processing any requests
    try:
        from app.core.encryption import validate_encryption_health
        validate_encryption_health()
        logger.info("Encryption health check passed.")
    except AssertionError as e:
        logger.critical("Encryption health check FAILED: %s", e)
        # Don't prevent startup — but log critically so it's visible
    
    # Run startup initialization (resource detection, tool sync)
    try:
        logger.info("Running startup initialization...")
        results = await initialize_on_startup()
        
        # Log summary
        if results.get("resources"):
            r = results["resources"]
            logger.info("Resources: %d new, %d updated", r['created'], r['updated'])
        
        if results.get("tools"):
            t = results["tools"]
            logger.info("Tools: %d enabled, %d disabled, %d unchanged", t['enabled'], t['disabled'], t['unchanged'])
        
        if results.get("errors"):
            for err in results["errors"]:
                logger.warning("Startup warning: %s", err)
        
        # Check LLM availability and warn if none configured
        llm_status = await check_llm_availability()
        if not llm_status["any_available"]:
            logger.warning(
                "No LLM provider configured! "
                "The system requires at least one LLM (Z.ai, Anthropic, OpenAI, or Ollama)."
            )
        else:
            providers = [k for k, v in llm_status.items() if v and k != "any_available"]
            logger.info("LLM providers available: %s", ', '.join(providers))
        
        # Check ACE-Step server if enabled
        # Note: ACE-Step runs on the HOST (for GPU access), not in the container
        # The container only checks if the server is reachable
        if settings.use_acestep:
            try:
                from app.services.acestep_service import get_acestep_service
                service = get_acestep_service()
                if await service.health_check():
                    logger.info("ACE-Step server available on port %d", settings.acestep_api_port)
                else:
                    logger.warning("ACE-Step server not reachable at %s", settings.acestep_api_url)
            except Exception as e:
                logger.warning("ACE-Step check error: %s", e)
        
        # Check Qwen3-TTS server if enabled
        # Note: Qwen3-TTS runs on the HOST (for GPU access), not in the container
        if settings.use_qwen3_tts:
            try:
                from app.services.qwen3_tts_service import get_qwen3_tts_service
                service = get_qwen3_tts_service()
                if await service.health_check():
                    logger.info("Qwen3-TTS server available on port %d", settings.qwen3_tts_api_port)
                else:
                    logger.warning("Qwen3-TTS server not reachable at %s", settings.qwen3_tts_api_url)
            except Exception as e:
                logger.warning("Qwen3-TTS check error: %s", e)
        
        # Check Z-Image server if enabled
        # Note: Z-Image runs on the HOST (for GPU access), not in the container
        if settings.use_zimage:
            try:
                from app.services.zimage_service import get_zimage_service
                service = get_zimage_service()
                if await service.health_check():
                    logger.info("Z-Image server available on port %d", settings.zimage_api_port)
                else:
                    logger.warning("Z-Image server not reachable at %s", settings.zimage_api_url)
            except Exception as e:
                logger.warning("Z-Image check error: %s", e)
        
        logger.info("Startup initialization complete.")
        
    except Exception as e:
        logger.error("Startup initialization error: %s", e)
        # Don't fail startup - the app can still serve some requests
    
    yield
    
    # Shutdown
    logger.info("Shutting down Money Agents API...")


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="AI-powered system for identifying, proposing, and executing automated money-making opportunities",
    lifespan=lifespan,
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url="/redoc" if settings.enable_docs else None,
)

# Register rate limiter with the app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # SGA3-I3: No cookie auth — Bearer tokens only
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
)


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # SA3-L3: Restrictive CSP for API responses (no HTML rendering expected)
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        # SGA-M5: Prevent caching of authenticated API responses
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        # HSTS — only in production when TLS termination is in place
        if settings.environment == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


# SGA3-H2: Reject oversized request bodies before they consume memory
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject request bodies larger than MAX_BYTES (50 MB)."""
    MAX_BYTES = 50 * 1024 * 1024  # 50 MB

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.MAX_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large"},
                    )
            except (ValueError, TypeError):
                pass
        return await call_next(request)


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)

# Include API router
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.app_name,
        "status": "running",
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
