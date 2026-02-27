"""
Docling Document Parser Standalone FastAPI Server

A CPU-only document parsing and conversion service using IBM Docling.
Converts PDF, DOCX, PPTX, HTML, images, Markdown, AsciiDoc, and more
into structured Markdown or JSON output.

Features:
  - Parse documents from file upload or URL
  - Table structure recognition (TableFormer)
  - OCR for scanned documents and images (EasyOCR)
  - Multiple output formats: Markdown, JSON, text
  - Document metadata extraction
  - Cross-service URL resolution (fetch files from sibling services)

API Endpoints:
  POST /parse         - Parse a document (file upload or URL)
  GET  /health        - Health check
  GET  /info          - Server info (capabilities, supported formats)
  GET  /output/{f}    - Retrieve parsed output files

CPU-only — no GPU required.  Runs on port 8010.
Based on: https://github.com/docling-project/docling
"""

import asyncio
import gc
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import validate_url, add_security_middleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("docling-parser")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = SCRIPT_DIR.parent  # Parent of docling-parser/

# Map local service ports to their workspace directories.
_LOCAL_SERVICE_DIRS: Dict[int, str] = {
    8001: "acestep",
    8002: "qwen3-tts",
    8003: "z-image",
    8004: "seedvr2-upscaler",
    8005: "canary-stt",
    8006: "ltx-video",
    8007: "audiosr",
    8008: "media-toolkit",
    8009: "realesrgan-cpu",
    8010: "docling-parser",
}


def _load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


CONFIG = _load_config()
PORT = int(os.environ.get("DOCLING_API_PORT", CONFIG.get("port", 8010)))


# =============================================================================
# Cross-Service URL Resolution
# =============================================================================

def _resolve_local_url(url: str) -> Optional[str]:
    """Resolve a URL pointing to a sibling service to a local file path.

    For example, ``http://localhost:8003/output/ZIMG_00001.png`` becomes
    ``<WORKSPACE_DIR>/z-image/output/ZIMG_00001.png``.
    """
    import re

    m = re.match(
        r"https?://(?:localhost|host\.docker\.internal|127\.0\.0\.1):(\d+)/output/(.+)",
        url,
    )
    if not m:
        return None
    port = int(m.group(1))
    filename = m.group(2)
    service_dir = _LOCAL_SERVICE_DIRS.get(port)
    if service_dir is None:
        return None
    local_path = (WORKSPACE_DIR / service_dir / "output" / filename).resolve()
    # Security: prevent path traversal via ../ segments (SA2-12)
    if not local_path.is_relative_to(WORKSPACE_DIR.resolve()):
        return None
    if local_path.is_file():
        return str(local_path)
    return None


# =============================================================================
# Docling Converter Manager
# =============================================================================

class ConverterManager:
    """Manages lazy loading of the Docling DocumentConverter."""

    def __init__(self):
        self._converter = None
        self._lock = threading.Lock()
        self._loaded = False

    def load(self):
        """Load the Docling DocumentConverter (lazy, thread-safe)."""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            logger.info("Loading Docling DocumentConverter...")
            start_time = time.time()

            try:
                from docling.document_converter import DocumentConverter

                self._converter = DocumentConverter()
                self._loaded = True

                elapsed = time.time() - start_time
                logger.info(f"Docling converter loaded in {elapsed:.1f}s")

            except Exception as e:
                logger.error(f"Failed to load Docling converter: {e}")
                raise

    @property
    def converter(self):
        """Get the converter, loading it if necessary."""
        if not self._loaded:
            self.load()
        return self._converter

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def unload(self):
        """Unload the converter to free memory."""
        with self._lock:
            if self._loaded:
                logger.info("Unloading Docling converter...")
                self._converter = None
                self._loaded = False
                gc.collect()
                logger.info("Docling converter unloaded")


_manager = ConverterManager()


# =============================================================================
# Response Models
# =============================================================================

class HealthResponse(BaseModel):
    status: str = "ok"
    converter_loaded: bool = False


class InfoResponse(BaseModel):
    name: str = "docling-parser"
    version: str = "1.0.0"
    description: str = "Document parsing and conversion using IBM Docling"
    converter_loaded: bool = False
    supported_input_formats: List[str] = [
        "pdf", "docx", "pptx", "xlsx", "html", "md",
        "asciidoc", "csv", "png", "jpg", "jpeg", "tiff", "bmp", "gif",
    ]
    supported_output_formats: List[str] = ["markdown", "json", "text"]


class ParseResponse(BaseModel):
    success: bool
    output_format: str
    content: Optional[str] = None
    output_file: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    page_count: Optional[int] = None
    tables_found: Optional[int] = None
    figures_found: Optional[int] = None
    processing_time_seconds: float = 0.0
    source: Optional[str] = None


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    logger.info(f"Docling Parser server starting on port {PORT}")
    # Pre-load converter on startup so first request is fast
    try:
        _manager.load()
    except Exception as e:
        logger.warning(f"Failed to pre-load converter (will retry on first request): {e}")
    yield
    logger.info("Docling Parser server shutting down")
    _manager.unload()


app = FastAPI(
    title="Docling Document Parser",
    description="Document parsing and conversion using IBM Docling",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://backend:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)

add_security_middleware(app)

# Reject uploads larger than 100 MB
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

@app.middleware("http")
async def limit_upload_size(request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=413,
            content={"detail": f"Upload too large. Maximum size is {MAX_UPLOAD_BYTES // (1024*1024)} MB"},
        )
    return await call_next(request)


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        converter_loaded=_manager.is_loaded,
    )


@app.get("/info", response_model=InfoResponse)
async def server_info():
    """Get server information and capabilities."""
    return InfoResponse(
        converter_loaded=_manager.is_loaded,
    )


@app.post("/parse", response_model=ParseResponse)
async def parse_document(
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    output_format: Optional[str] = Form("markdown"),
):
    """
    Parse a document and convert to structured output.

    Provide either a file upload or a URL to a document.

    Args:
        file: Document file to parse (PDF, DOCX, PPTX, HTML, image, etc.)
        url: URL of document to parse (can be a URL to a sibling service output)
        output_format: Output format — 'markdown' (default), 'json', or 'text'

    Returns:
        Parsed document content with metadata.
    """
    if not file and not url:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a URL")

    if output_format not in ("markdown", "json", "text"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid output_format '{output_format}'. Use: markdown, json, text"
        )

    start_time = time.time()
    source_path = None
    temp_path = None
    source_label = None

    try:
        # --- Resolve input source ---
        if file:
            # Save uploaded file to temp location
            suffix = Path(file.filename).suffix if file.filename else ".pdf"
            temp_fd, temp_path = tempfile.mkstemp(suffix=suffix)
            os.close(temp_fd)
            with open(temp_path, "wb") as f:
                content = await file.read()
                f.write(content)
            source_path = temp_path
            source_label = file.filename or "uploaded_file"

        elif url:
            # Try local resolution first
            local_path = _resolve_local_url(url)
            if local_path:
                source_path = local_path
                source_label = url
            else:
                # Download from remote URL
                if not validate_url(url):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid or blocked URL",
                    )
                import httpx as _httpx
                # SA3-H1: follow_redirects=False to prevent SSRF via redirect chains
                async with _httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                    response = await client.get(url)
                    response.raise_for_status()

                # Determine extension from URL or content-type
                url_path = url.split("?")[0]
                suffix = Path(url_path).suffix or ".pdf"
                temp_fd, temp_path = tempfile.mkstemp(suffix=suffix)
                os.close(temp_fd)
                with open(temp_path, "wb") as f:
                    f.write(response.content)
                source_path = temp_path
                source_label = url

        # --- Parse with Docling ---
        logger.info(f"Parsing document: {source_label} (format: {output_format})")

        converter = _manager.converter

        # Run conversion in thread pool to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: converter.convert(source_path)
        )

        # --- Extract output ---
        doc = result.document
        metadata = {}

        # Count tables and figures
        tables_found = 0
        figures_found = 0
        page_count = None

        if hasattr(doc, "tables"):
            tables_found = len(doc.tables)
        if hasattr(doc, "pictures"):
            figures_found = len(doc.pictures)
        if hasattr(doc, "pages") and doc.pages:
            page_count = len(doc.pages)

        # Extract metadata if available
        if hasattr(doc, "name") and doc.name:
            metadata["title"] = doc.name
        if hasattr(doc, "origin") and doc.origin:
            if hasattr(doc.origin, "mimetype"):
                metadata["mimetype"] = doc.origin.mimetype
            if hasattr(doc.origin, "filename"):
                metadata["filename"] = doc.origin.filename

        # Generate output based on format
        if output_format == "markdown":
            content = doc.export_to_markdown()
        elif output_format == "json":
            import json
            content = json.dumps(doc.export_to_dict(), indent=2, default=str)
        elif output_format == "text":
            content = doc.export_to_markdown()
            # Strip markdown formatting for plain text
            import re
            content = re.sub(r'[#*_`\[\]()!]', '', content)
            content = re.sub(r'\n{3,}', '\n\n', content)

        # Save output to file
        output_id = secrets.token_hex(6)
        ext = {"markdown": ".md", "json": ".json", "text": ".txt"}[output_format]
        output_filename = f"DOC_{output_id}{ext}"
        output_path = OUTPUT_DIR / output_filename
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        elapsed = time.time() - start_time

        logger.info(
            f"Parsed '{source_label}' → {output_format} in {elapsed:.1f}s "
            f"({page_count or '?'} pages, {tables_found} tables, {figures_found} figures)"
        )

        return ParseResponse(
            success=True,
            output_format=output_format,
            content=content,
            output_file=f"/output/{output_filename}",
            metadata=metadata if metadata else None,
            page_count=page_count,
            tables_found=tables_found,
            figures_found=figures_found,
            processing_time_seconds=round(elapsed, 2),
            source=source_label,
        )

    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Parse failed for '{source_label}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Document parsing failed")

    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


@app.get("/output/{filename}")
async def get_output_file(filename: str):
    """Retrieve a parsed output file."""
    # Security: prevent path traversal
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = OUTPUT_DIR / filename
    if file_path.resolve().parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Determine media type
    ext = file_path.suffix.lower()
    media_types = {
        ".md": "text/markdown",
        ".json": "application/json",
        ".txt": "text/plain",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(str(file_path), media_type=media_type, filename=filename)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, log_level="info")
