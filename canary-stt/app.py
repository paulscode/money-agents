"""
Canary-STT Standalone FastAPI Server

Speech-to-text transcription using NVIDIA Canary-Qwen-2.5B, a state-of-the-art
English ASR model with 2.5B parameters and 418 RTFx processing speed.

Built on NeMo's SALM (Speech-Augmented Language Model) architecture combining
FastConformer encoder with Qwen3-1.7B decoder.

Features:
  - English speech-to-text with punctuation and capitalization
  - Lazy model loading with idle GPU memory unloading
  - 16kHz mono audio input (auto-resampled)
  - Multiple input formats: WAV, FLAC, MP3, OGG, M4A, WebM
  - File upload or URL-based transcription

API Endpoints:
  POST /transcribe   - Transcribe audio file or URL
  GET  /health       - Health check
  POST /unload       - Manually unload model from GPU
  GET  /info         - Server info (model, VRAM, capabilities)
  GET  /output/{f}   - Retrieve saved transcript files

Model: https://huggingface.co/nvidia/canary-qwen-2.5b
License: CC-BY-4.0
"""

import argparse
import asyncio
import logging
import os
import secrets
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import validate_url, add_security_middleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("canary-stt")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = SCRIPT_DIR.parent  # Parent of canary-stt/

# Map local service ports to their workspace directories.
# GPU services may be shut down (evicted) but their output files persist on disk.
# This lets us read files directly instead of needing the HTTP server to be up.
_LOCAL_SERVICE_DIRS: Dict[int, str] = {
    8001: "acestep",
    8002: "qwen3-tts",
    8003: "z-image",
    8004: "seedvr2-upscaler",
    8005: "canary-stt",
    8006: "ltx-video",
}


def _resolve_local_url(url: str) -> Optional[Path]:
    """If *url* points to a local service output file, return its filesystem path.

    Handles URLs like ``http://localhost:8002/output/file.wav`` by mapping to
    ``<workspace>/qwen3-tts/output/file.wav``.  Returns None when the URL is
    not local or the file doesn't exist on disk.
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = parsed.hostname or ""
    if host not in ("localhost", "127.0.0.1"):
        return None

    port = parsed.port
    if port not in _LOCAL_SERVICE_DIRS:
        return None

    service_dir = _LOCAL_SERVICE_DIRS[port]
    # URL path is e.g. "/output/file.wav" → strip leading slash
    rel_path = parsed.path.lstrip("/")
    if not rel_path:
        return None

    candidate = (WORKSPACE_DIR / service_dir / rel_path).resolve()
    # Security: prevent path traversal via ../ segments (SA2-01)
    if not candidate.is_relative_to(WORKSPACE_DIR.resolve()):
        return None
    if candidate.is_file():
        logger.info(f"Resolved local URL to file: {candidate}")
        return candidate

    return None


# Supported audio formats (will auto-convert to WAV 16kHz mono)
SUPPORTED_FORMATS = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".webm", ".mp4", ".wma"}


def load_config() -> dict:
    """Load server configuration from config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


CONFIG = load_config()

# =============================================================================
# Model Manager
# =============================================================================


class ModelManager:
    """
    Manages NVIDIA Canary-Qwen-2.5B model lifecycle with lazy loading and
    idle unloading.

    The model is loaded from HuggingFace on first /transcribe request and
    unloaded after a configurable idle timeout to free GPU memory for other
    tasks (Z-Image, Qwen3-TTS, etc.).
    """

    def __init__(self, config: dict):
        self.config = config
        self._model = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._idle_timeout = config.get("memory", {}).get("idle_timeout", 300)
        self._unload_task: Optional[asyncio.Task] = None
        self._dtype_str = config.get("memory", {}).get("dtype", "bfloat16")
        self._repo_id = config.get("model", {}).get("repo_id", "nvidia/canary-qwen-2.5b")
        self._max_new_tokens = config.get("model", {}).get("max_new_tokens", 1024)
        self._max_audio_duration = config.get("model", {}).get("max_audio_duration", 40)

        # Detect device
        try:
            import torch

            if torch.cuda.is_available():
                self._device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self._device = "mps"
            else:
                self._device = "cpu"
        except ImportError:
            self._device = "cpu"

        logger.info(
            f"ModelManager initialized: device={self._device}, "
            f"repo={self._repo_id}, dtype={self._dtype_str}"
        )

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    async def get_model(self):
        """Get loaded model, loading if necessary."""
        async with self._lock:
            if self._model is None:
                await self._load_model()
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._model

    async def _load_model(self):
        """Load Canary-Qwen model from HuggingFace."""
        logger.info(f"Loading Canary-Qwen model: {self._repo_id}...")
        start = time.time()

        try:
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(None, self._load_sync)
            elapsed = time.time() - start
            logger.info(f"Model loaded in {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def _load_sync(self):
        """Synchronous model loading — runs in thread pool."""
        import torch
        from nemo.collections.speechlm2.models import SALM

        logger.info(f"Loading SALM model from {self._repo_id}...")

        # Set dtype
        dtype = torch.bfloat16 if self._dtype_str == "bfloat16" else torch.float16

        model = SALM.from_pretrained(self._repo_id)

        # Move to device with desired dtype
        if self._device == "cuda":
            model = model.to(device=self._device, dtype=dtype)
        model.eval()

        logger.info(f"Model loaded on {self._device} with dtype={self._dtype_str}")
        return model

    async def unload_model(self):
        """Unload model to free GPU memory."""
        async with self._lock:
            if self._model is not None:
                import torch

                logger.info("Unloading Canary-Qwen model to free GPU memory...")
                del self._model
                self._model = None
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("Model unloaded")

    def _schedule_idle_unload(self):
        """Schedule model unload after idle timeout."""
        if self._idle_timeout <= 0:
            return  # Never unload

        # Cancel existing task
        if self._unload_task and not self._unload_task.done():
            self._unload_task.cancel()

        async def _check_and_unload():
            await asyncio.sleep(self._idle_timeout)
            if time.time() - self._last_used >= self._idle_timeout:
                await self.unload_model()

        try:
            loop = asyncio.get_event_loop()
            self._unload_task = loop.create_task(_check_and_unload())
        except RuntimeError:
            pass  # No event loop yet

    def get_info(self) -> Dict[str, Any]:
        """Get model and device information."""
        info = {
            "model_loaded": self.is_loaded,
            "model_repo": self._repo_id,
            "device": self._device,
            "dtype": self._dtype_str,
            "idle_timeout": self._idle_timeout,
            "max_audio_duration": self._max_audio_duration,
            "max_new_tokens": self._max_new_tokens,
            "supported_formats": sorted(SUPPORTED_FORMATS),
        }

        if self._device == "cuda":
            try:
                import torch

                gpu_name = torch.cuda.get_device_name(0)
                vram_total = torch.cuda.get_device_properties(0).total_mem / 1024**3
                vram_used = torch.cuda.memory_allocated(0) / 1024**3
                vram_reserved = torch.cuda.memory_reserved(0) / 1024**3
                info.update(
                    {
                        "gpu": gpu_name,
                        "vram_total_gb": round(vram_total, 1),
                        "vram_used_gb": round(vram_used, 1),
                        "vram_reserved_gb": round(vram_reserved, 1),
                    }
                )
            except Exception:
                pass

        return info


# =============================================================================
# Audio Processing Utilities
# =============================================================================


def prepare_audio(audio_path: str, target_sr: int = 16000) -> str:
    """
    Prepare audio file for Canary: ensure 16kHz mono WAV.

    Returns path to the processed file (may be same as input if already OK).
    """
    import soundfile as sf

    info = sf.info(audio_path)

    # If already 16kHz mono WAV, use as-is
    if info.samplerate == target_sr and info.channels == 1 and audio_path.endswith(".wav"):
        return audio_path

    # Need to resample/convert
    import librosa

    logger.info(
        f"Resampling audio: {info.samplerate}Hz/{info.channels}ch → {target_sr}Hz/mono"
    )

    audio_data, _ = librosa.load(audio_path, sr=target_sr, mono=True)

    # Write to temp WAV file
    output_path = audio_path + ".16k.wav"
    sf.write(output_path, audio_data, target_sr, subtype="PCM_16")
    return output_path


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    import soundfile as sf

    info = sf.info(audio_path)
    return info.duration


# =============================================================================
# Request / Response Models
# =============================================================================


class TranscribeResponse(BaseModel):
    """Response from /transcribe endpoint."""

    text: str = Field(..., description="Transcribed text")
    duration_seconds: float = Field(..., description="Audio duration in seconds")
    processing_time_seconds: float = Field(
        ..., description="Time taken to process in seconds"
    )
    audio_file: Optional[str] = Field(None, description="Original audio filename")
    transcript_file: Optional[str] = Field(
        None, description="URL to saved transcript file"
    )


class TranscribeURLRequest(BaseModel):
    """Request body for URL-based transcription."""

    audio_url: str = Field(..., description="URL of audio file to transcribe")
    save_transcript: bool = Field(
        False, description="Save transcript to output directory"
    )


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    model_loaded: bool = False


class InfoResponse(BaseModel):
    """Server info response."""

    model_loaded: bool = False
    model_repo: str = ""
    device: str = ""
    dtype: str = ""
    idle_timeout: int = 300
    max_audio_duration: int = 40
    max_new_tokens: int = 1024
    supported_formats: list = []
    gpu: Optional[str] = None
    vram_total_gb: Optional[float] = None
    vram_used_gb: Optional[float] = None
    vram_reserved_gb: Optional[float] = None


# =============================================================================
# FastAPI Application
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — create ModelManager on startup."""
    app.state.model_manager = ModelManager(CONFIG)
    logger.info("Canary-STT server started")
    yield
    # Cleanup on shutdown
    if app.state.model_manager.is_loaded:
        await app.state.model_manager.unload_model()
    logger.info("Canary-STT server stopped")


app = FastAPI(
    title="Canary-STT",
    description="Speech-to-text using NVIDIA Canary-Qwen-2.5B",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware
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
async def health():
    """Health check endpoint."""
    mm = app.state.model_manager
    return HealthResponse(status="ok", model_loaded=mm.is_loaded)


@app.post("/unload")
async def unload():
    """Manually unload model from GPU to free VRAM."""
    mm = app.state.model_manager
    if mm.is_loaded:
        await mm.unload_model()
        return {"status": "unloaded", "message": "Model unloaded from GPU"}
    return {"status": "already_unloaded", "message": "Model was not loaded"}


@app.post("/shutdown")
async def graceful_shutdown():
    """Gracefully terminate the server process to fully release VRAM.

    Used by the GPU lifecycle manager when /unload fails to free CUDA
    context memory. Sends SIGTERM to self after returning response.
    """
    import os, signal, threading
    def _kill():
        import time
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)
    threading.Thread(target=_kill, daemon=True).start()
    return {"status": "shutting_down"}


@app.get("/info", response_model=InfoResponse)
async def info():
    """Get server and model information."""
    mm = app.state.model_manager
    return InfoResponse(**mm.get_info())


@app.post("/transcribe", response_model=TranscribeResponse)
async def transcribe(
    file: Optional[UploadFile] = File(None),
    audio_url: Optional[str] = None,
    save_transcript: bool = False,
):
    """
    Transcribe audio to text.

    Accepts either a file upload or an audio URL. Audio is automatically
    resampled to 16kHz mono if needed.

    Supported formats: WAV, FLAC, MP3, OGG, M4A, WebM, MP4, WMA
    Maximum audio duration: 40 seconds (model training limit)
    """
    mm: ModelManager = app.state.model_manager
    temp_files = []

    try:
        # Get audio data from file upload or URL
        if file is not None:
            # Validate file extension
            ext = Path(file.filename or ".wav").suffix.lower()
            if ext not in SUPPORTED_FORMATS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported audio format: {ext}. Supported: {sorted(SUPPORTED_FORMATS)}",
                )

            # Save uploaded file to temp path
            suffix = ext or ".wav"
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=str(OUTPUT_DIR)
            ) as tmp:
                content = await file.read()
                tmp.write(content)
                audio_path = tmp.name
                temp_files.append(audio_path)
                original_filename = file.filename

        elif audio_url:
            # Try local filesystem resolution first (handles evicted GPU services)
            local_path = _resolve_local_url(audio_url)
            if local_path:
                # IMPORTANT: Do NOT add this path to temp_files — it's an
                # original file belonging to another service (e.g.
                # qwen3-tts/output/...) and must not be deleted.
                audio_path = str(local_path)
                original_filename = local_path.name
            else:
                # Download from URL via HTTP
                import httpx

                if not validate_url(audio_url):
                    raise HTTPException(
                        status_code=400,
                        detail="Invalid or blocked URL",
                    )

                try:
                    async with httpx.AsyncClient(timeout=60) as client:
                        response = await client.get(audio_url)
                        if response.status_code != 200:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Failed to download audio from URL: HTTP {response.status_code}",
                            )

                        # Determine extension from URL or content-type
                        url_path = audio_url.split("?")[0]
                        ext = Path(url_path).suffix.lower() if "." in url_path else ".wav"
                        if ext not in SUPPORTED_FORMATS:
                            ext = ".wav"  # Default fallback

                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=ext, dir=str(OUTPUT_DIR)
                        ) as tmp:
                            tmp.write(response.content)
                            audio_path = tmp.name
                            temp_files.append(audio_path)
                            original_filename = Path(url_path).name or "audio_from_url"
                except httpx.RequestError as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Failed to download audio: {e}",
                    )
        else:
            raise HTTPException(
                status_code=400,
                detail="Either 'file' (upload) or 'audio_url' parameter is required",
            )

        # Prepare audio (resample to 16kHz mono if needed)
        prepared_path = prepare_audio(audio_path)
        if prepared_path != audio_path:
            temp_files.append(prepared_path)

        # Check duration
        duration = get_audio_duration(prepared_path)
        if duration > mm._max_audio_duration:
            raise HTTPException(
                status_code=400,
                detail=f"Audio too long: {duration:.1f}s (max {mm._max_audio_duration}s). "
                f"Split the audio into shorter segments.",
            )

        if duration < 0.1:
            raise HTTPException(
                status_code=400,
                detail="Audio too short (< 0.1s). Provide a longer audio clip.",
            )

        # Transcribe
        start_time = time.time()
        model = await mm.get_model()

        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: _transcribe_sync(model, prepared_path, mm._max_new_tokens),
        )

        processing_time = time.time() - start_time
        logger.info(
            f"Transcribed {duration:.1f}s audio in {processing_time:.2f}s "
            f"({duration/processing_time:.0f}x realtime)"
        )

        # Optionally save transcript
        transcript_url = None
        if save_transcript:
            tx_name = f"transcript_{secrets.token_hex(4)}.txt"
            tx_path = OUTPUT_DIR / tx_name
            tx_path.write_text(transcript, encoding="utf-8")
            transcript_url = f"/output/{tx_name}"

        return TranscribeResponse(
            text=transcript,
            duration_seconds=round(duration, 2),
            processing_time_seconds=round(processing_time, 3),
            audio_file=original_filename,
            transcript_file=transcript_url,
        )

    finally:
        # Clean up temp files
        for tf in temp_files:
            try:
                os.unlink(tf)
            except OSError:
                pass


def _transcribe_sync(model, audio_path: str, max_new_tokens: int) -> str:
    """Run model transcription synchronously — called via run_in_executor."""
    # Canary-Qwen uses the SALM generate() API
    # ASR mode: "Transcribe the following: <audio>"
    answer_ids = model.generate(
        prompts=[
            [
                {
                    "role": "user",
                    "content": f"Transcribe the following: {model.audio_locator_tag}",
                    "audio": [audio_path],
                }
            ]
        ],
        max_new_tokens=max_new_tokens,
    )

    # Decode token IDs to text
    transcript = model.tokenizer.ids_to_text(answer_ids[0].cpu())
    return transcript.strip()


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve a saved transcript file."""
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = OUTPUT_DIR / safe_name
    if not file_path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path))


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="Canary-STT Server")
    parser.add_argument(
        "--host",
        default=CONFIG.get("server", {}).get("host", "0.0.0.0"),
        help="Host to bind to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CONFIG.get("server", {}).get("port", 8005),
        help="Port to listen on",
    )
    args = parser.parse_args()

    uvicorn.run("app:app", host=args.host, port=args.port, reload=False)
