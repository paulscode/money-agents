"""
AudioSR Standalone FastAPI Server

Audio super-resolution using AudioSR (Versatile Audio Super-Resolution at Scale).
Upsamples any audio (music, speech, environmental sounds) to 48kHz high-fidelity output.

Built on latent diffusion with CLAP conditioning for versatile audio enhancement.

Features:
  - Universal audio super-resolution (music, speech, environmental, etc.)
  - Two model variants: basic (general) and speech (optimized for speech)
  - Lazy model loading with idle GPU memory unloading
  - Long audio support via chunked processing with cross-fade blending
  - File upload or URL-based processing
  - Configurable DDIM steps and guidance scale

API Endpoints:
  POST /enhance       - Enhance/upscale audio file or URL
  GET  /health        - Health check
  POST /unload        - Manually unload model from GPU
  GET  /info          - Server info (model, VRAM, capabilities)
  GET  /output/{f}    - Retrieve enhanced audio files

Paper: https://arxiv.org/abs/2309.07314
GitHub: https://github.com/haoheliu/versatile_audio_super_resolution
License: MIT (AudioSR code), model weights under their own license
"""

import argparse
import asyncio
import gc
import logging
import os
import secrets
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
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
logger = logging.getLogger("audiosr")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = SCRIPT_DIR.parent  # Parent of audiosr/

# Map local service ports to their workspace directories.
# GPU services may be shut down (evicted) but their output files persist on disk.
_LOCAL_SERVICE_DIRS: Dict[int, str] = {
    8001: "acestep",
    8002: "qwen3-tts",
    8003: "z-image",
    8004: "seedvr2-upscaler",
    8005: "canary-stt",
    8006: "ltx-video",
    8007: "audiosr",
}

# Supported audio formats
SUPPORTED_FORMATS = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".webm", ".mp4", ".wma"}


def _resolve_local_url(url: str) -> Optional[Path]:
    """If *url* points to a local service output file, return its filesystem path."""
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
    Manages AudioSR model lifecycle with lazy loading and idle unloading.

    The model is loaded on first /enhance request and unloaded after a
    configurable idle timeout to free GPU memory for other tasks.
    """

    def __init__(self, config: dict):
        self.config = config
        self._model = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._idle_timeout = config.get("memory", {}).get("idle_timeout", 300)
        self._model_name = config.get("model", {}).get("variant", "basic")
        self._ddim_steps = config.get("inference", {}).get("ddim_steps", 50)
        self._guidance_scale = config.get("inference", {}).get("guidance_scale", 3.5)
        self._max_audio_duration = config.get("inference", {}).get("max_audio_duration", 120)
        self._chunking = config.get("inference", {}).get("chunking", True)
        self._chunk_duration = config.get("inference", {}).get("chunk_duration", 15)
        self._overlap_duration = config.get("inference", {}).get("overlap_duration", 2)
        self._output_sr = config.get("model", {}).get("output_sample_rate", 48000)
        self._unload_task: Optional[asyncio.Task] = None

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
            f"model={self._model_name}, ddim_steps={self._ddim_steps}, "
            f"guidance_scale={self._guidance_scale}"
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
        """Load AudioSR model."""
        logger.info(f"Loading AudioSR model: {self._model_name}...")
        start = time.time()

        try:
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(None, self._load_sync)
            elapsed = time.time() - start
            logger.info(f"Model loaded in {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            # Clean up any partial GPU allocations
            self._model = None
            import torch, gc
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            raise

    def _load_sync(self):
        """Synchronous model loading — runs in thread pool."""
        import torch

        os.environ["TOKENIZERS_PARALLELISM"] = "true"
        torch.set_float32_matmul_precision("high")

        from audiosr import build_model

        logger.info(f"Building AudioSR model '{self._model_name}' on {self._device}...")
        model = build_model(model_name=self._model_name, device=self._device)
        logger.info(f"AudioSR model loaded on {self._device}")
        return model

    async def switch_model(self, model_name: str):
        """Switch to a different model variant (unloads current, loads new)."""
        valid_models = ["basic", "speech"]
        if model_name not in valid_models:
            raise ValueError(f"Unknown model variant: {model_name}. Valid: {valid_models}")
        if model_name == self._model_name and self._model is not None:
            return  # Already loaded
        logger.info(f"Switching AudioSR model: {self._model_name} -> {model_name}")
        async with self._lock:
            if self._model is not None:
                import torch
                del self._model
                self._model = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            self._model_name = model_name
            await self._load_model()
            self._last_used = time.time()
            self._schedule_idle_unload()
        logger.info(f"Switched to AudioSR model: {model_name}")

    async def unload_model(self):
        """Unload model to free GPU memory."""
        async with self._lock:
            if self._model is not None:
                import torch

                logger.info("Unloading AudioSR model to free GPU memory...")
                del self._model
                self._model = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("Model unloaded")

    def _schedule_idle_unload(self):
        """Schedule model unload after idle timeout."""
        if self._idle_timeout <= 0:
            return

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
            pass

    def get_info(self) -> Dict[str, Any]:
        """Get model and device information."""
        info = {
            "model_loaded": self.is_loaded,
            "model_variant": self._model_name,
            "device": self._device,
            "idle_timeout": self._idle_timeout,
            "ddim_steps": self._ddim_steps,
            "guidance_scale": self._guidance_scale,
            "max_audio_duration": self._max_audio_duration,
            "chunking_enabled": self._chunking,
            "chunk_duration": self._chunk_duration,
            "overlap_duration": self._overlap_duration,
            "output_sample_rate": self._output_sr,
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
# Audio Processing
# =============================================================================


def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds."""
    import soundfile as sf

    info = sf.info(audio_path)
    return info.duration


def _enhance_audio_sync(
    model,
    input_path: str,
    output_path: str,
    seed: int,
    ddim_steps: int,
    guidance_scale: float,
) -> dict:
    """Run AudioSR super-resolution synchronously — called via run_in_executor."""
    import numpy as np
    import soundfile as sf
    import torch

    from audiosr import super_resolution

    start_time = time.time()

    # Get input duration
    input_info = sf.info(input_path)
    input_duration = input_info.duration
    input_sr = input_info.samplerate

    logger.info(f"Processing audio ({input_duration:.1f}s, {input_sr}Hz) → 48kHz")
    waveform = super_resolution(
        model,
        input_path,
        seed=seed,
        ddim_steps=ddim_steps,
        guidance_scale=guidance_scale,
        latent_t_per_second=12.8,
    )

    # Convert to writable format
    if isinstance(waveform, torch.Tensor):
        waveform = waveform.cpu().numpy()

    if isinstance(waveform, np.ndarray):
        # Handle various shapes from AudioSR output
        if waveform.ndim == 3:
            # Shape: [batch, channels, samples] or [batch, 1, samples]
            waveform = waveform[0]  # Take first batch
        if waveform.ndim == 2:
            if waveform.shape[0] == 1:
                waveform = waveform[0]  # Mono: [1, samples] → [samples]
            else:
                waveform = waveform.T  # Multi-channel: [channels, samples] → [samples, channels]

        # Normalize to int16 range if floating point
        if waveform.dtype in (np.float32, np.float64):
            # Clip to [-1, 1] range first
            waveform = np.clip(waveform, -1.0, 1.0)
            waveform = (waveform * 32767).astype(np.int16)

    # Write output
    sf.write(output_path, data=waveform, samplerate=48000, subtype="PCM_16")

    processing_time = time.time() - start_time

    # Clean up GPU memory
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    output_info = sf.info(output_path)

    return {
        "input_duration_seconds": round(input_duration, 2),
        "input_sample_rate": input_sr,
        "output_duration_seconds": round(output_info.duration, 2),
        "output_sample_rate": 48000,
        "processing_time_seconds": round(processing_time, 3),
        "ddim_steps": ddim_steps,
        "guidance_scale": guidance_scale,
        "seed": seed,
    }


# =============================================================================
# Request / Response Models
# =============================================================================


class EnhanceResponse(BaseModel):
    """Response from /enhance endpoint."""

    output_file: str = Field(..., description="URL to enhanced audio file")
    input_duration_seconds: float = Field(..., description="Input audio duration")
    input_sample_rate: int = Field(..., description="Input sample rate")
    output_duration_seconds: float = Field(..., description="Output audio duration")
    output_sample_rate: int = Field(48000, description="Output sample rate (always 48kHz)")
    processing_time_seconds: float = Field(..., description="Processing time")
    ddim_steps: int = Field(..., description="DDIM steps used")
    guidance_scale: float = Field(..., description="Guidance scale used")
    seed: int = Field(..., description="Random seed used")
    chunking_used: bool = Field(False, description="Whether chunked processing was used")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = "ok"
    model_loaded: bool = False


class InfoResponse(BaseModel):
    """Server info response."""

    model_loaded: bool = False
    model_variant: str = ""
    device: str = ""
    idle_timeout: int = 300
    ddim_steps: int = 50
    guidance_scale: float = 3.5
    max_audio_duration: int = 120
    chunking_enabled: bool = True
    chunk_duration: int = 15
    overlap_duration: int = 2
    output_sample_rate: int = 48000
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
    logger.info("AudioSR server started")
    yield
    if app.state.model_manager.is_loaded:
        await app.state.model_manager.unload_model()
    logger.info("AudioSR server stopped")


app = FastAPI(
    title="AudioSR",
    description="Audio super-resolution using AudioSR — upscale any audio to 48kHz",
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
    """Gracefully terminate the server process to fully release VRAM."""
    import signal
    import threading

    def _kill():
        import time as t
        t.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_kill, daemon=True).start()
    return {"status": "shutting_down"}


@app.get("/info", response_model=InfoResponse)
async def info():
    """Get server and model information."""
    mm = app.state.model_manager
    return InfoResponse(**mm.get_info())


@app.post("/enhance", response_model=EnhanceResponse)
async def enhance(
    file: Optional[UploadFile] = File(None),
    audio_url: Optional[str] = Form(None),
    ddim_steps: Optional[int] = Form(None),
    guidance_scale: Optional[float] = Form(None),
    seed: Optional[int] = Form(None),
    model_name: Optional[str] = Form(None),
):
    """
    Enhance/upscale audio to 48kHz high-fidelity output.

    Accepts either a file upload or an audio URL. Works on all types of audio:
    music, speech, environmental sounds, etc.

    Supported formats: WAV, FLAC, MP3, OGG, M4A, WebM, MP4, WMA
    """
    import random

    mm: ModelManager = app.state.model_manager
    temp_files = []

    # Use request params or fall back to config defaults
    steps = ddim_steps if ddim_steps is not None else mm._ddim_steps
    scale = guidance_scale if guidance_scale is not None else mm._guidance_scale
    audio_seed = seed if seed is not None else random.randint(0, 2**31 - 1)

    # If model_name differs from current, hot-swap the model
    if model_name and model_name != mm._model_name:
        try:
            await mm.switch_model(model_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    try:
        # Get audio data from file upload or URL
        if file is not None:
            ext = Path(file.filename or ".wav").suffix.lower()
            if ext not in SUPPORTED_FORMATS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported audio format: {ext}. Supported: {sorted(SUPPORTED_FORMATS)}",
                )

            suffix = ext or ".wav"
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=str(OUTPUT_DIR)
            ) as tmp:
                content = await file.read()
                tmp.write(content)
                audio_path = tmp.name
                temp_files.append(audio_path)

        elif audio_url:
            local_path = _resolve_local_url(audio_url)
            if local_path:
                audio_path = str(local_path)
            else:
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
                                detail=f"Failed to download audio: HTTP {response.status_code}",
                            )

                        url_path = audio_url.split("?")[0]
                        ext = Path(url_path).suffix.lower() if "." in url_path else ".wav"
                        if ext not in SUPPORTED_FORMATS:
                            ext = ".wav"

                        with tempfile.NamedTemporaryFile(
                            delete=False, suffix=ext, dir=str(OUTPUT_DIR)
                        ) as tmp:
                            tmp.write(response.content)
                            audio_path = tmp.name
                            temp_files.append(audio_path)
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

        # Check duration
        duration = get_audio_duration(audio_path)
        if duration > mm._max_audio_duration:
            raise HTTPException(
                status_code=400,
                detail=f"Audio too long: {duration:.1f}s (max {mm._max_audio_duration}s). "
                f"Consider splitting the audio.",
            )

        if duration < 0.1:
            raise HTTPException(
                status_code=400,
                detail="Audio too short (< 0.1s). Provide a longer audio clip.",
            )

        # Generate output filename
        output_name = (
            f"{CONFIG.get('output', {}).get('prefix', 'AUDIOSR_')}"
            f"{secrets.token_hex(4)}.wav"
        )
        output_path = str(OUTPUT_DIR / output_name)

        # Load model and enhance
        model = await mm.get_model()

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _enhance_audio_sync(
                model=model,
                input_path=audio_path,
                output_path=output_path,
                seed=audio_seed,
                ddim_steps=steps,
                guidance_scale=scale,
            ),
        )

        logger.info(
            f"Enhanced {result['input_duration_seconds']}s audio "
            f"({result['input_sample_rate']}Hz → 48kHz) "
            f"in {result['processing_time_seconds']:.1f}s"
        )

        return EnhanceResponse(
            output_file=f"/output/{output_name}",
            **result,
        )

    finally:
        for tf in temp_files:
            try:
                os.unlink(tf)
            except OSError:
                pass


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve an enhanced audio file."""
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    file_path = OUTPUT_DIR / safe_name
    if not file_path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(status_code=403, detail="Access denied")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(file_path), media_type="audio/wav")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description="AudioSR Server")
    parser.add_argument(
        "--host",
        default=CONFIG.get("server", {}).get("host", "0.0.0.0"),
        help="Host to bind to",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CONFIG.get("server", {}).get("port", 8007),
        help="Port to listen on",
    )
    args = parser.parse_args()

    uvicorn.run("app:app", host=args.host, port=args.port, reload=False)
