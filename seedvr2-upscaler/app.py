"""
SeedVR2 Upscaler Standalone FastAPI Server

A native image & video upscaler server using SeedVR2 (ByteDance) PyTorch inference
directly, bypassing ComfyUI for lower latency and direct GPU control.

SeedVR2 is a one-step diffusion-based super-resolution model (3B/7B DiT).
It upscales images and videos to higher resolutions while adding realistic detail.

Features:
  - Image upscaling with configurable output resolution
  - Video upscaling with temporal consistency
  - Multiple model options (3B/7B, FP16/FP8/GGUF)
  - Lazy model loading with idle GPU memory unloading
  - Automatic model download from HuggingFace
  - Color correction methods (LAB, wavelet, HSV, AdaIN)
  - BlockSwap for low-VRAM systems

API Endpoints:
  POST /upscale/image   - Upscale a single image
  POST /upscale/video   - Upscale a video file
  POST /unload          - Unload models from GPU to free VRAM
  GET  /health          - Health check
  GET  /info            - Server info (model, VRAM, capabilities)
  GET  /output/{f}      - Retrieve upscaled files

Based on: https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler
"""

import asyncio
import logging
import os
import secrets
import shutil
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
logger = logging.getLogger("seedvr2-upscaler")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = SCRIPT_DIR.parent  # Parent of seedvr2-upscaler/

# Map local service ports to their workspace directories.
# GPU services may be shut down (evicted) but their output files persist on disk.
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

    Handles URLs like ``http://localhost:8006/output/LTX2_00018.mp4`` by mapping
    to ``<workspace>/ltx-video/output/LTX2_00018.mp4``.  Returns None when the URL
    is not local or the file doesn't exist on disk.

    Security: resolved path must stay within WORKSPACE_DIR (RT-08).
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
    rel_path = parsed.path.lstrip("/")
    if not rel_path:
        return None

    candidate = (WORKSPACE_DIR / service_dir / rel_path).resolve()
    # RT-08: prevent path traversal via ../ segments
    if not candidate.is_relative_to(WORKSPACE_DIR.resolve()):
        return None
    if candidate.is_file():
        logger.info(f"Resolved local URL to file: {candidate}")
        return candidate

    return None


# SeedVR2 clone directory
SEEDVR2_DIR = SCRIPT_DIR / "seedvr2"


def load_config() -> dict:
    """Load server configuration from config.yaml."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


CONFIG = load_config()


def _setup_seedvr2_imports():
    """Add SeedVR2 source to Python path for imports."""
    seedvr2_src = SEEDVR2_DIR
    if not seedvr2_src.exists():
        raise RuntimeError(
            f"SeedVR2 source not found at {seedvr2_src}. "
            f"Run install to clone the SeedVR2 repository."
        )
    src_str = str(seedvr2_src)
    if src_str not in sys.path:
        sys.path.insert(0, src_str)


# =============================================================================
# Model Manager
# =============================================================================


class ModelManager:
    """
    Manages SeedVR2 model lifecycle with lazy loading and idle unloading.

    The model components (DiT, VAE) are loaded on first upscale request
    and unloaded after a configurable idle timeout to free GPU memory for
    other tasks (Z-Image, ACE-Step, Qwen3-TTS, Ollama).
    """

    def __init__(self, config: dict):
        self.config = config
        self._state = None  # SeedVR2 state dict {runner, ctx, cache_context, debug, model_dir}
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._unload_task: Optional[asyncio.Task] = None

        # Config values
        mem_config = config.get("memory", {})
        model_config = config.get("model", {})
        self._idle_timeout = mem_config.get("idle_timeout", 300)
        self._dit_model = model_config.get("dit", "seedvr2_ema_3b_fp8_e4m3fn.safetensors")
        self._vae_model = model_config.get("vae", "ema_vae_fp16.safetensors")
        self._blocks_to_swap = mem_config.get("blocks_to_swap", 0)
        self._dit_offload = mem_config.get("dit_offload", False)
        self._vae_tiling = mem_config.get("vae_tiling", False)

        # Detect device
        try:
            import torch
            if torch.cuda.is_available():
                self._device = "cuda"
            else:
                self._device = "cpu"
        except ImportError:
            self._device = "cpu"

        logger.info(
            f"ModelManager initialized: device={self._device}, "
            f"dit={self._dit_model}, blocks_to_swap={self._blocks_to_swap}"
        )

    @property
    def is_loaded(self) -> bool:
        return self._state is not None

    @property
    def dit_model(self) -> str:
        return self._dit_model

    async def get_state(self):
        """Get loaded SeedVR2 state dict, loading if necessary."""
        async with self._lock:
            if self._state is None:
                await self._load_model()
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._state

    async def _load_model(self):
        """Load SeedVR2 model components."""
        import torch

        logger.info(f"Loading SeedVR2 model: {self._dit_model}...")
        start = time.time()

        try:
            _setup_seedvr2_imports()
            loop = asyncio.get_event_loop()
            self._state = await loop.run_in_executor(
                None,
                self._load_sync,
            )
            elapsed = time.time() - start
            logger.info(f"SeedVR2 model loaded in {elapsed:.1f}s")
        except Exception as e:
            logger.error(f"Failed to load SeedVR2 model: {e}", exc_info=True)
            raise

    def _load_sync(self):
        """Synchronous model loading — runs in thread pool."""
        import torch

        # Import SeedVR2 components
        from src.core.generation_utils import (
            setup_generation_context,
            prepare_runner,
        )
        from src.utils.debug import Debug
        from src.utils.constants import SEEDVR2_FOLDER_NAME
        from src.utils.downloads import download_weight

        debug = Debug(enabled=False)

        # Setup generation context (device configuration)
        dit_offload = "cpu" if self._dit_offload else None
        ctx = setup_generation_context(
            dit_device=self._device,
            vae_device=self._device,
            dit_offload_device=dit_offload,
            vae_offload_device=None,
            tensor_offload_device=None,
            debug=debug,
        )

        # Model directory
        model_dir = str(SEEDVR2_DIR / "models" / SEEDVR2_FOLDER_NAME)

        # Download models from HuggingFace if not already present
        dit_path = Path(model_dir) / self._dit_model
        vae_path = Path(model_dir) / self._vae_model
        if not dit_path.exists() or not vae_path.exists():
            logger.info(f"Downloading models to {model_dir} ...")
            Path(model_dir).mkdir(parents=True, exist_ok=True)
            ok = download_weight(
                dit_model=self._dit_model,
                vae_model=self._vae_model,
                model_dir=model_dir,
                debug=debug,
            )
            if not ok:
                raise RuntimeError("Failed to download SeedVR2 model weights from HuggingFace")

        # Build block swap config
        block_swap_config = None
        if self._blocks_to_swap > 0:
            block_swap_config = {
                "blocks_to_swap": self._blocks_to_swap,
                "swap_io_components": False,
                "offload_device": dit_offload,
            }

        # Prepare runner (loads DiT + VAE models)
        logger.info(f"Preparing runner: dit={self._dit_model}, vae={self._vae_model}")
        runner, cache_context = prepare_runner(
            dit_model=self._dit_model,
            vae_model=self._vae_model,
            model_dir=model_dir,
            debug=debug,
            ctx=ctx,
            dit_cache=True,
            vae_cache=True,
            dit_id="server_dit",
            vae_id="server_vae",
            block_swap_config=block_swap_config,
            decode_tiled=self._vae_tiling,
            encode_tiled=self._vae_tiling,
            attention_mode="sdpa",
        )

        logger.info("SeedVR2 runner prepared successfully")

        # Store context alongside runner for use in inference
        return {
            "runner": runner,
            "ctx": ctx,
            "cache_context": cache_context,
            "debug": debug,
            "model_dir": model_dir,
        }

    async def unload_model(self):
        """Unload model to free GPU memory."""
        async with self._lock:
            if self._state is not None:
                import torch

                logger.info("Unloading SeedVR2 model to free GPU memory...")
                # Clean up runner and cached models
                try:
                    runner = self._state.get("runner")
                    if runner and hasattr(runner, 'cleanup'):
                        runner.cleanup()
                except Exception:
                    pass
                try:
                    from src.optimization.memory_manager import clear_memory
                    clear_memory()
                except Exception:
                    pass
                del self._state
                self._state = None
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("SeedVR2 model unloaded")

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


# =============================================================================
# Request / Response Models
# =============================================================================


class UpscaleImageRequest(BaseModel):
    """Request body for /upscale/image endpoint."""
    image_path: Optional[str] = Field(
        None, description="Local file path to the image to upscale"
    )
    image_url: Optional[str] = Field(
        None, description="URL to download the image from"
    )
    resolution: int = Field(
        default=1080, description="Target short-side resolution (e.g. 1080 for ~1080p)"
    )
    max_resolution: int = Field(
        default=0, description="Max resolution cap (0 = no limit)"
    )
    color_correction: str = Field(
        default="lab", description="Color correction: lab, wavelet, wavelet_adaptive, hsv, adain, none"
    )
    seed: Optional[int] = Field(
        None, description="Random seed for reproducibility"
    )


class UpscaleVideoRequest(BaseModel):
    """Request body for /upscale/video endpoint."""
    video_path: Optional[str] = Field(
        None, description="Local file path to the video to upscale"
    )
    video_url: Optional[str] = Field(
        None, description="URL to download the video from"
    )
    resolution: int = Field(
        default=1080, description="Target short-side resolution"
    )
    max_resolution: int = Field(
        default=0, description="Max resolution cap (0 = no limit)"
    )
    batch_size: int = Field(
        default=5, description="Frames per batch (must follow 4n+1: 5, 9, 13...)"
    )
    temporal_overlap: int = Field(
        default=2, description="Temporal overlap frames for smooth blending"
    )
    color_correction: str = Field(
        default="lab", description="Color correction method"
    )
    seed: Optional[int] = Field(
        None, description="Random seed for reproducibility"
    )


class UpscaleImageResponse(BaseModel):
    """Response body for /upscale/image endpoint."""
    success: bool
    output_path: str
    output_url: str
    input_resolution: str
    output_resolution: str
    processing_time_seconds: float
    model_used: str
    seed: int


class UpscaleVideoResponse(BaseModel):
    """Response body for /upscale/video endpoint."""
    success: bool
    output_path: str
    output_url: str
    input_resolution: str
    output_resolution: str
    total_frames: int
    processing_time_seconds: float
    model_used: str
    seed: int


# =============================================================================
# Output File Management
# =============================================================================

_name_lock = threading.Lock()


def _next_unique_name(ext: str = "png") -> str:
    """Generate next sequential filename."""
    prefix = CONFIG.get("output", {}).get("prefix", "SEEDVR2_")
    digits = CONFIG.get("output", {}).get("digits", 5)
    ext = ext.lower().lstrip(".")

    max_n = 0
    pattern_glob = f"{prefix}{'[0-9]' * digits}.{ext}"
    for p in OUTPUT_DIR.glob(pattern_glob):
        stem = p.stem
        if not stem.startswith(prefix):
            continue
        num_part = stem[len(prefix):]
        if num_part.isdigit():
            max_n = max(max_n, int(num_part))

    n = max_n + 1
    while True:
        candidate = f"{prefix}{n:0{digits}d}.{ext}"
        if not (OUTPUT_DIR / candidate).exists():
            return candidate
        n += 1


# =============================================================================
# Helper Functions
# =============================================================================


async def _download_image(url: str) -> tuple:
    """Download an image from URL to a temp file.

    Returns (path, is_temp) — is_temp is False when the file was resolved
    locally and must NOT be deleted.
    """
    # Try local filesystem resolution first (handles evicted GPU services)
    local_path = _resolve_local_url(url)
    if local_path:
        return local_path, False

    import httpx

    if not validate_url(url, allow_internal=True):
        raise HTTPException(status_code=400, detail="URL not allowed (blocked by SSRF protection)")

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to download image from URL: {response.status_code}"
            )
        # Determine extension
        content_type = response.headers.get("content-type", "")
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"
        elif "jpeg" in content_type or "jpg" in content_type:
            ext = ".jpg"
        else:
            ext = ".png"

        tmp = Path(tempfile.mktemp(suffix=ext, dir=str(OUTPUT_DIR)))
        tmp.write_bytes(response.content)
        return tmp, True


def _validate_image_path(image_path: str) -> Path:
    """Validate and resolve an image path."""
    path = Path(image_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Image not found: {image_path}")
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {image_path}")
    return path


def _validate_video_path(video_path: str) -> Path:
    """Validate and resolve a video path."""
    path = Path(video_path)
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Video not found: {video_path}")
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {video_path}")
    return path


async def _download_video(url: str) -> Path:
    """Download a video from URL to a temp file."""
    # Try local filesystem resolution first (handles evicted GPU services)
    local_path = _resolve_local_url(url)
    if local_path:
        return local_path

    import httpx

    if not validate_url(url, allow_internal=True):
        raise HTTPException(status_code=400, detail="URL not allowed (blocked by SSRF protection)")

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to download video from URL: {response.status_code}"
            )
        content_type = response.headers.get("content-type", "")
        if "webm" in content_type:
            ext = ".webm"
        elif "avi" in content_type:
            ext = ".avi"
        elif "mov" in content_type or "quicktime" in content_type:
            ext = ".mov"
        else:
            ext = ".mp4"

        tmp = Path(tempfile.mktemp(suffix=ext, dir=str(OUTPUT_DIR)))
        tmp.write_bytes(response.content)
        return tmp, True


# =============================================================================
# FastAPI Application
# =============================================================================

model_manager: Optional[ModelManager] = None

# Serialize upscale requests — the runner shares mutable context (ctx)
# so concurrent requests would corrupt each other's state.
_upscale_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize model manager."""
    global model_manager
    model_manager = ModelManager(CONFIG)
    logger.info("SeedVR2 Upscaler server starting...")
    yield
    logger.info("SeedVR2 Upscaler server shutting down...")
    if model_manager and model_manager.is_loaded:
        await model_manager.unload_model()


app = FastAPI(
    title="SeedVR2 Upscaler Server",
    description=(
        "Native SeedVR2 image & video upscaler server — "
        "diffusion-based super-resolution with lazy model loading and idle GPU unloading"
    ),
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


# =============================================================================
# Endpoints
# =============================================================================


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "model_loaded": model_manager.is_loaded if model_manager else False,
        "device": model_manager._device if model_manager else "unknown",
    }


@app.post("/unload")
async def unload():
    """Unload model from GPU to free VRAM.

    The model will be lazy-loaded again on the next upscale request.
    The server process stays alive — only GPU memory is released.
    """
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")
    if not model_manager.is_loaded:
        return {"status": "already_unloaded"}
    await model_manager.unload_model()
    return {"status": "unloaded"}


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


@app.get("/info")
async def info():
    """Server information endpoint."""
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")

    gpu_info = {}
    try:
        import torch
        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            gpu_info = {
                "name": props.name,
                "vram_total_mb": props.total_memory // (1024 * 1024),
                "vram_free_mb": (props.total_memory - torch.cuda.memory_allocated(0)) // (1024 * 1024),
            }
    except Exception:
        pass

    defaults = CONFIG.get("defaults", {})
    return {
        "model_loaded": model_manager.is_loaded,
        "dit_model": model_manager.dit_model,
        "device": model_manager._device,
        "gpu": gpu_info,
        "idle_timeout": model_manager._idle_timeout,
        "capabilities": ["image_upscale", "video_upscale"],
        "defaults": {
            "resolution": defaults.get("resolution", 1080),
            "max_resolution": defaults.get("max_resolution", 0),
            "color_correction": defaults.get("color_correction", "lab"),
            "batch_size": defaults.get("batch_size", 5),
        },
    }


@app.post("/upscale/image", response_model=UpscaleImageResponse)
async def upscale_image(request: UpscaleImageRequest):
    """Upscale a single image to higher resolution."""
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")

    # Validate input — need either image_path or image_url
    if not request.image_path and not request.image_url:
        raise HTTPException(
            status_code=400, detail="Provide either image_path or image_url"
        )

    # Resolve input image
    tmp_downloaded = None
    try:
        if request.image_url:
            input_path, is_temp = await _download_image(request.image_url)
            tmp_downloaded = input_path if is_temp else None
        else:
            input_path = _validate_image_path(request.image_path)

        # Apply defaults
        defaults = CONFIG.get("defaults", {})
        resolution = request.resolution or defaults.get("resolution", 1080)
        max_resolution = request.max_resolution or defaults.get("max_resolution", 0)
        color_correction = request.color_correction or defaults.get("color_correction", "lab")
        seed = request.seed if request.seed is not None else secrets.randbelow(2**31)

        start_time = time.time()

        # Serialize GPU work — runner ctx is shared mutable state
        async with _upscale_lock:
            # Get state (lazy-loads model)
            state = await model_manager.get_state()

            # Run upscaling in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _upscale_image_sync(
                    state=state,
                    input_path=str(input_path),
                    resolution=resolution,
                    max_resolution=max_resolution,
                    color_correction=color_correction,
                    seed=seed,
                ),
            )

        elapsed = time.time() - start_time

        # Save output
        with _name_lock:
            filename = _next_unique_name("png")
        output_path = OUTPUT_DIR / filename
        result["image"].save(str(output_path))

        port = CONFIG.get("server", {}).get("port", 8004)
        base_url = f"http://127.0.0.1:{port}"

        logger.info(
            f"Upscaled image: {result['input_resolution']} → {result['output_resolution']}, "
            f"seed={seed}, time={elapsed:.1f}s"
        )

        return UpscaleImageResponse(
            success=True,
            output_path=str(output_path),
            output_url=f"{base_url}/output/{filename}",
            input_resolution=result["input_resolution"],
            output_resolution=result["output_resolution"],
            processing_time_seconds=round(elapsed, 2),
            model_used=model_manager.dit_model,
            seed=seed,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image upscaling failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Upscaling failed")
    finally:
        # Clean up downloaded temp file
        if tmp_downloaded and tmp_downloaded.exists():
            try:
                tmp_downloaded.unlink()
            except Exception:
                pass


@app.post("/upscale/video", response_model=UpscaleVideoResponse)
async def upscale_video(request: UpscaleVideoRequest):
    """Upscale a video file to higher resolution."""
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")

    if not request.video_path and not request.video_url:
        raise HTTPException(
            status_code=400, detail="Provide either video_path or video_url"
        )

    # Resolve input video
    tmp_downloaded = None
    if request.video_url:
        input_path, is_temp = await _download_video(request.video_url)
        tmp_downloaded = input_path if is_temp else None
    else:
        input_path = _validate_video_path(request.video_path)

    # Validate batch_size follows 4n+1 formula
    if (request.batch_size - 1) % 4 != 0:
        raise HTTPException(
            status_code=400,
            detail=f"batch_size must follow 4n+1 formula (5, 9, 13, 17...). Got: {request.batch_size}"
        )

    defaults = CONFIG.get("defaults", {})
    resolution = request.resolution or defaults.get("resolution", 1080)
    max_resolution = request.max_resolution or defaults.get("max_resolution", 0)
    color_correction = request.color_correction or defaults.get("color_correction", "lab")
    batch_size = request.batch_size or defaults.get("batch_size", 5)
    temporal_overlap = request.temporal_overlap if request.temporal_overlap is not None else defaults.get("temporal_overlap", 2)
    seed = request.seed if request.seed is not None else secrets.randbelow(2**31)

    start_time = time.time()

    try:
        # Serialize GPU work — runner ctx is shared mutable state
        async with _upscale_lock:
            runner = await model_manager.get_state()

            # Run video upscaling in thread pool (long-running)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: _upscale_video_sync(
                    state=runner,
                    input_path=str(input_path),
                    resolution=resolution,
                    max_resolution=max_resolution,
                    color_correction=color_correction,
                    batch_size=batch_size,
                    temporal_overlap=temporal_overlap,
                    seed=seed,
                ),
            )

        elapsed = time.time() - start_time

        # Move output to our output directory
        with _name_lock:
            filename = _next_unique_name("mp4")
        output_path = OUTPUT_DIR / filename

        if result.get("output_file") and Path(result["output_file"]).exists():
            shutil.move(result["output_file"], str(output_path))
        else:
            raise RuntimeError("Video upscaling produced no output file")

        port = CONFIG.get("server", {}).get("port", 8004)
        base_url = f"http://127.0.0.1:{port}"

        logger.info(
            f"Upscaled video: {result['input_resolution']} → {result['output_resolution']}, "
            f"frames={result.get('total_frames', 0)}, seed={seed}, time={elapsed:.1f}s"
        )

        return UpscaleVideoResponse(
            success=True,
            output_path=str(output_path),
            output_url=f"{base_url}/output/{filename}",
            input_resolution=result["input_resolution"],
            output_resolution=result["output_resolution"],
            total_frames=result.get("total_frames", 0),
            processing_time_seconds=round(elapsed, 2),
            model_used=model_manager.dit_model,
            seed=seed,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video upscaling failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Video upscaling failed")
    finally:
        if tmp_downloaded:
            try:
                tmp_downloaded.unlink()
            except Exception:
                pass


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve an upscaled image or video file."""
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if not safe or safe != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = OUTPUT_DIR / safe
    # Ensure resolved path stays within OUTPUT_DIR (path traversal protection)
    if path.resolve().parent != OUTPUT_DIR.resolve():
        raise HTTPException(status_code=403, detail="Access denied")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    # Determine media type
    ext = path.suffix.lower()
    media_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(path, media_type=media_type)


# =============================================================================
# Synchronous Upscale Functions (run in thread pool)
# =============================================================================


def _upscale_image_sync(
    state: dict,
    input_path: str,
    resolution: int,
    max_resolution: int,
    color_correction: str,
    seed: int,
) -> dict:
    """
    Synchronous image upscaling using the SeedVR2 4-phase pipeline.

    Pipeline: encode → upscale (DiT) → decode (VAE) → postprocess (color correction)
    """
    import torch
    import numpy as np
    from PIL import Image

    from src.core.generation_utils import (
        compute_generation_info,
        log_generation_start,
        load_text_embeddings,
        script_directory,
    )
    from src.core.generation_phases import (
        encode_all_batches,
        upscale_all_batches,
        decode_all_batches,
        postprocess_all_batches,
    )

    runner = state["runner"]
    ctx = state["ctx"]
    cache_context = state["cache_context"]
    debug = state["debug"]

    # Clear previous run data from context while keeping device config
    keys_to_keep = {
        "dit_device", "vae_device", "dit_offload_device",
        "vae_offload_device", "tensor_offload_device", "compute_dtype",
        "interrupt_fn", "comfyui_available", "cache_context",
    }
    for key in list(ctx.keys()):
        if key not in keys_to_keep:
            ctx[key] = None if key in ("video_transform", "text_embeds", "final_video") else ([] if key in ("all_latents", "all_upscaled_latents", "batch_samples") else ctx.get(key))

    # Re-initialize mutable context fields
    ctx["cache_context"] = cache_context
    ctx["video_transform"] = None
    ctx["text_embeds"] = None
    ctx["all_latents"] = []
    ctx["all_upscaled_latents"] = []
    ctx["batch_samples"] = []
    ctx["final_video"] = None

    # Load input image and convert to tensor [T, H, W, C] float16 range [0, 1]
    input_image = Image.open(input_path).convert("RGB")
    input_w, input_h = input_image.size
    img_array = np.array(input_image).astype(np.float32) / 255.0
    # Shape: [1, H, W, 3] — single frame "video"
    frames_tensor = torch.from_numpy(img_array).unsqueeze(0)

    # Preload text embeddings
    ctx["text_embeds"] = load_text_embeddings(
        script_directory, ctx["dit_device"], ctx["compute_dtype"], debug
    )

    # Compute generation info (handles resolution calculation internally)
    frames_tensor, gen_info = compute_generation_info(
        ctx=ctx,
        images=frames_tensor,
        resolution=resolution,
        max_resolution=max_resolution,
        batch_size=1,
        uniform_batch_size=False,
        seed=seed,
        prepend_frames=0,
        temporal_overlap=0,
        debug=debug,
    )
    log_generation_start(gen_info, debug)

    output_w = gen_info.get("output_width", input_w * 2)
    output_h = gen_info.get("output_height", input_h * 2)

    # Phase 1: Encode
    ctx = encode_all_batches(
        runner, ctx=ctx, images=frames_tensor,
        debug=debug,
        batch_size=1,
        uniform_batch_size=False,
        seed=seed,
        progress_callback=None,
        temporal_overlap=0,
        resolution=resolution,
        max_resolution=max_resolution,
        input_noise_scale=0.0,
        color_correction=color_correction,
    )

    # Phase 2: Upscale (DiT inference)
    ctx = upscale_all_batches(
        runner, ctx=ctx, debug=debug, progress_callback=None,
        seed=seed,
        latent_noise_scale=0.0,
        cache_model=True,
    )

    # Phase 3: Decode (VAE)
    ctx = decode_all_batches(
        runner, ctx=ctx, debug=debug, progress_callback=None,
        cache_model=True,
    )

    # Phase 4: Post-process (color correction, etc.)
    ctx = postprocess_all_batches(
        ctx=ctx, debug=debug, progress_callback=None,
        color_correction=color_correction,
        prepend_frames=0,
        temporal_overlap=0,
        batch_size=1,
    )

    result_tensor = ctx["final_video"]

    # Convert to CPU float32
    if result_tensor.is_cuda:
        result_tensor = result_tensor.cpu()
    if result_tensor.dtype != torch.float32:
        result_tensor = result_tensor.to(torch.float32)

    # Convert tensor [T, H, W, C] → PIL Image (first frame)
    frame = result_tensor[0].clamp(0, 1).numpy()
    output_image = Image.fromarray((frame * 255).astype(np.uint8))

    actual_w, actual_h = output_image.size

    return {
        "image": output_image,
        "input_resolution": f"{input_w}x{input_h}",
        "output_resolution": f"{actual_w}x{actual_h}",
    }


def _upscale_video_sync(
    state: dict,
    input_path: str,
    resolution: int,
    max_resolution: int,
    color_correction: str,
    batch_size: int,
    temporal_overlap: int,
    seed: int,
) -> dict:
    """
    Synchronous video upscaling using the SeedVR2 4-phase pipeline.

    Processes video frames in batches with temporal overlap for smooth blending.
    """
    import torch
    import cv2
    import numpy as np

    from src.core.generation_utils import (
        compute_generation_info,
        log_generation_start,
        load_text_embeddings,
        script_directory,
    )
    from src.core.generation_phases import (
        encode_all_batches,
        upscale_all_batches,
        decode_all_batches,
        postprocess_all_batches,
    )

    runner = state["runner"]
    ctx = state["ctx"]
    cache_context = state["cache_context"]
    debug = state["debug"]

    # Re-initialize mutable context fields
    ctx["cache_context"] = cache_context
    ctx["video_transform"] = None
    ctx["text_embeds"] = None
    ctx["all_latents"] = []
    ctx["all_upscaled_latents"] = []
    ctx["batch_samples"] = []
    ctx["final_video"] = None

    # Load video frames using OpenCV
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        # BGR → RGB, normalize to [0, 1]
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        frames.append(frame_rgb)
    cap.release()

    if not frames:
        raise RuntimeError("Video has no frames")

    total_frames = len(frames)
    input_h, input_w = frames[0].shape[:2]
    frames_tensor = torch.from_numpy(np.stack(frames))

    logger.info(f"Video loaded: {total_frames} frames, {input_w}x{input_h}, {fps:.1f} fps")

    # Preload text embeddings
    ctx["text_embeds"] = load_text_embeddings(
        script_directory, ctx["dit_device"], ctx["compute_dtype"], debug
    )

    # Compute generation info
    frames_tensor, gen_info = compute_generation_info(
        ctx=ctx,
        images=frames_tensor,
        resolution=resolution,
        max_resolution=max_resolution,
        batch_size=batch_size,
        uniform_batch_size=False,
        seed=seed,
        prepend_frames=0,
        temporal_overlap=temporal_overlap,
        debug=debug,
    )
    log_generation_start(gen_info, debug)

    output_w = gen_info.get("output_width", input_w * 2)
    output_h = gen_info.get("output_height", input_h * 2)

    # Phase 1: Encode
    ctx = encode_all_batches(
        runner, ctx=ctx, images=frames_tensor,
        debug=debug,
        batch_size=batch_size,
        uniform_batch_size=False,
        seed=seed,
        progress_callback=None,
        temporal_overlap=temporal_overlap,
        resolution=resolution,
        max_resolution=max_resolution,
        input_noise_scale=0.0,
        color_correction=color_correction,
    )

    # Phase 2: Upscale
    ctx = upscale_all_batches(
        runner, ctx=ctx, debug=debug, progress_callback=None,
        seed=seed,
        latent_noise_scale=0.0,
        cache_model=True,
    )

    # Phase 3: Decode
    ctx = decode_all_batches(
        runner, ctx=ctx, debug=debug, progress_callback=None,
        cache_model=True,
    )

    # Phase 4: Post-process
    ctx = postprocess_all_batches(
        ctx=ctx, debug=debug, progress_callback=None,
        color_correction=color_correction,
        prepend_frames=0,
        temporal_overlap=temporal_overlap,
        batch_size=batch_size,
    )

    result_tensor = ctx["final_video"]

    if result_tensor.is_cuda:
        result_tensor = result_tensor.cpu()
    if result_tensor.dtype != torch.float32:
        result_tensor = result_tensor.to(torch.float32)

    # Save frames to video using OpenCV
    tmp_output = str(OUTPUT_DIR / f"tmp_{secrets.token_hex(8)}.mp4")
    out_h, out_w = result_tensor.shape[1], result_tensor.shape[2]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_output, fourcc, fps, (out_w, out_h))

    for i in range(result_tensor.shape[0]):
        frame = result_tensor[i].clamp(0, 1).numpy()
        frame_bgr = cv2.cvtColor((frame * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)
    writer.release()

    return {
        "output_file": tmp_output,
        "input_resolution": f"{input_w}x{input_h}",
        "output_resolution": f"{out_w}x{out_h}",
        "total_frames": result_tensor.shape[0],
    }


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="SeedVR2 Upscaler Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=CONFIG.get("server", {}).get("port", 8004),
    )
    parser.add_argument(
        "--dit-model",
        default=None,
        help="DiT model filename (e.g. seedvr2_ema_3b_fp8_e4m3fn.safetensors)",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Idle unload timeout (seconds)",
    )

    args = parser.parse_args()

    # Override config with CLI args
    if args.dit_model:
        CONFIG.setdefault("model", {})["dit"] = args.dit_model
    if args.idle_timeout is not None:
        CONFIG.setdefault("memory", {})["idle_timeout"] = args.idle_timeout

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
