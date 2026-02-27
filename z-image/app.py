"""
Z-Image Standalone FastAPI Server

A native image generation server using Z-Image PyTorch inference directly,
bypassing ComfyUI for lower latency and direct GPU control.

Features:
  - Z-Image-Turbo: 8-step fast inference (~3-8s on RTX 3090)
  - Z-Image (Base): 50-step high-quality inference
  - Lazy model loading with idle GPU memory unloading
  - Automatic model download from HuggingFace
  - Negative prompts, batch generation, multiple resolutions

API Endpoints:
  POST /generate    - Generate image(s) from text prompt
  GET  /health      - Health check
  GET  /info        - Server info (model, VRAM, capabilities)
  GET  /output/{f}  - Retrieve generated image files
"""

import asyncio
import logging
import os
import secrets
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reduce CUDA memory fragmentation — without this, PyTorch's block allocator
# can reserve large contiguous chunks during model loading that later become
# unusable holes.  On a 24 GiB GPU (RTX 3090) with ~791 MiB taken by the
# display compositor, the 1.5+ GiB of fragmentation pushes us over the edge
# and the 1024 MiB generation allocation fails with CUDA OOM.
# Must be set BEFORE torch is first imported.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import add_security_middleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("z-image")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
    Manages Z-Image model lifecycle with lazy loading and idle unloading.

    The model components (DiT, VAE, text encoder, tokenizer, scheduler) are
    loaded on first /generate request and unloaded after a configurable idle
    timeout to free GPU memory for other tasks (ACE-Step, Qwen3-TTS).
    """

    def __init__(self, config: dict):
        self.config = config
        self._components: Optional[Dict[str, Any]] = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._idle_timeout = config.get("memory", {}).get("idle_timeout", 300)
        self._unload_task: Optional[asyncio.Task] = None
        self._variant = config.get("model", {}).get("variant", "turbo")
        self._compile = config.get("model", {}).get("compile", False)
        self._attn_backend = config.get("model", {}).get("attention_backend", "_native_flash")
        self._dtype_str = config.get("memory", {}).get("dtype", "bfloat16")

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
            f"variant={self._variant}, compile={self._compile}"
        )

    @property
    def is_loaded(self) -> bool:
        return self._components is not None

    @property
    def variant(self) -> str:
        return self._variant

    async def get_components(self) -> Dict[str, Any]:
        """Get loaded model components, loading if necessary."""
        async with self._lock:
            if self._components is None:
                await self._load_model()
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._components

    async def _load_model(self):
        """Load Z-Image model components."""
        import torch

        model_config = self.config.get("model", {})
        if self._variant == "turbo":
            repo_id = model_config.get("turbo_repo", "Tongyi-MAI/Z-Image-Turbo")
        else:
            repo_id = model_config.get("base_repo", "Tongyi-MAI/Z-Image")

        # Determine the local model path
        zimage_src = SCRIPT_DIR / "Z-Image" / "src"
        ckpts_dir = SCRIPT_DIR / "Z-Image" / "ckpts" / repo_id.split("/")[-1]

        if not zimage_src.exists():
            raise RuntimeError(
                f"Z-Image source not found at {zimage_src}. "
                f"Run install to clone the Z-Image repository."
            )

        # Add Z-Image src to Python path for imports
        src_str = str(zimage_src)
        if src_str not in sys.path:
            sys.path.insert(0, src_str)

        logger.info(f"Loading Z-Image model: {repo_id} (variant={self._variant})...")
        start = time.time()

        dtype = torch.bfloat16 if self._dtype_str == "bfloat16" else torch.float16

        try:
            # Run blocking model load in thread pool
            loop = asyncio.get_event_loop()
            self._components = await loop.run_in_executor(
                None,
                lambda: self._load_sync(str(ckpts_dir), dtype),
            )

            elapsed = time.time() - start
            logger.info(f"Model loaded in {elapsed:.1f}s (variant={self._variant})")

        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def _load_sync(self, model_path: str, dtype) -> Dict[str, Any]:
        """Synchronous model loading — runs in thread pool."""
        import torch

        # These imports come from Z-Image's src directory (already on sys.path)
        from utils import (
            AttentionBackend,
            ensure_model_weights,
            load_from_local_dir,
            set_attention_backend,
        )

        # Determine repo_id for auto-download
        model_config = self.config.get("model", {})
        if self._variant == "turbo":
            repo_id = model_config.get("turbo_repo", "Tongyi-MAI/Z-Image-Turbo")
        else:
            repo_id = model_config.get("base_repo", "Tongyi-MAI/Z-Image")

        # Ensure weights are downloaded (auto-downloads from HuggingFace if missing)
        verified_path = ensure_model_weights(model_path, repo_id=repo_id, verify=False)

        # Set attention backend
        AttentionBackend.print_available_backends()
        set_attention_backend(self._attn_backend)
        logger.info(f"Attention backend: {self._attn_backend}")

        # Load all components
        components = load_from_local_dir(
            verified_path,
            device=self._device,
            dtype=dtype,
            verbose=True,
            compile=self._compile,
        )

        return components

    async def unload_model(self):
        """Unload model to free GPU memory."""
        async with self._lock:
            if self._components is not None:
                import torch

                logger.info("Unloading Z-Image model to free GPU memory...")
                del self._components
                self._components = None
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


# =============================================================================
# Request / Response Models
# =============================================================================


class GenerateRequest(BaseModel):
    """Request body for /generate endpoint."""
    prompt: str = Field(..., description="Text prompt for image generation")
    negative_prompt: Optional[str] = Field(
        None, description="What to avoid in the image"
    )
    width: int = Field(default=1024, description="Image width (must be divisible by 16)")
    height: int = Field(default=1024, description="Image height (must be divisible by 16)")
    num_inference_steps: Optional[int] = Field(
        None, description="Denoising steps (default: 8 for turbo, 50 for base)"
    )
    guidance_scale: Optional[float] = Field(
        None, description="CFG scale (default: 0.0 for turbo, 3.5 for base)"
    )
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    num_images_per_prompt: int = Field(
        default=1, ge=1, le=4, description="Number of images to generate (1-4)"
    )


class GenerateResponse(BaseModel):
    """Response body for /generate endpoint."""
    success: bool
    image_url: str
    image_urls: List[str] = []
    seed: int
    width: int
    height: int
    num_inference_steps: int
    guidance_scale: float
    generation_time_seconds: float
    model_variant: str


# =============================================================================
# Output File Management
# =============================================================================

_name_lock = threading.Lock()


def _next_unique_name(ext: str = "png") -> str:
    """Generate next sequential filename."""
    prefix = CONFIG.get("output", {}).get("prefix", "ZIMG_")
    digits = CONFIG.get("output", {}).get("digits", 5)
    ext = ext.lower().lstrip(".")

    max_n = 0
    pattern = f"{prefix}{'[0-9]' * digits}.{ext}"
    for p in OUTPUT_DIR.glob(pattern):
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
# FastAPI Application
# =============================================================================

model_manager: Optional[ModelManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize model manager."""
    global model_manager
    model_manager = ModelManager(CONFIG)
    logger.info("Z-Image server starting...")
    yield
    logger.info("Z-Image server shutting down...")
    if model_manager and model_manager.is_loaded:
        await model_manager.unload_model()


app = FastAPI(
    title="Z-Image Server",
    description="Native Z-Image image generation server — text-to-image with lazy model loading and idle GPU unloading",
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
    
    The model will be lazy-loaded again on the next /generate request.
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
        "model_variant": model_manager.variant,
        "device": model_manager._device,
        "compile": model_manager._compile,
        "attention_backend": model_manager._attn_backend,
        "gpu": gpu_info,
        "idle_timeout": model_manager._idle_timeout,
        "defaults": {
            "width": defaults.get("width", 1024),
            "height": defaults.get("height", 1024),
            "num_inference_steps": defaults.get("num_inference_steps", 8),
            "guidance_scale": defaults.get("guidance_scale", 0.0),
        },
    }


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate image(s) from a text prompt."""
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")

    # Validate dimensions
    if request.width % 16 != 0:
        raise HTTPException(status_code=400, detail="Width must be divisible by 16")
    if request.height % 16 != 0:
        raise HTTPException(status_code=400, detail="Height must be divisible by 16")
    if request.width < 256 or request.width > 2048:
        raise HTTPException(status_code=400, detail="Width must be between 256 and 2048")
    if request.height < 256 or request.height > 2048:
        raise HTTPException(status_code=400, detail="Height must be between 256 and 2048")

    # Apply defaults based on variant
    defaults = CONFIG.get("defaults", {})
    is_turbo = model_manager.variant == "turbo"

    num_steps = request.num_inference_steps
    if num_steps is None:
        num_steps = defaults.get("num_inference_steps", 8 if is_turbo else 50)

    guidance_scale = request.guidance_scale
    if guidance_scale is None:
        guidance_scale = defaults.get("guidance_scale", 0.0 if is_turbo else 3.5)

    seed = request.seed if request.seed is not None else secrets.randbelow(2**31)

    start_time = time.time()

    try:
        import torch
        from zimage import generate as zimage_generate

        components = await model_manager.get_components()

        # Reclaim any fragmented reserved blocks before generation
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        generator = torch.Generator(model_manager._device).manual_seed(seed)

        # On GPUs with ≤24 GiB (RTX 3090, 4090), the full model (~19 GiB)
        # plus generation tensors (~1-2 GiB) plus display compositor VRAM
        # (~0.8 GiB) won't fit.  The text encoder (7.5 GiB) is only used
        # for prompt encoding, so we offload it to CPU before the denoising
        # loop runs, freeing ~7.5 GiB for generation buffers.
        text_encoder = components["text_encoder"]
        offload_text_encoder = (
            torch.cuda.is_available()
            and model_manager._device == "cuda"
            and next(text_encoder.parameters()).is_cuda
        )

        def _generate_with_offload():
            # 1. Encode prompt (text_encoder is still on GPU)
            #    — generate() uses text_encoder only at the start
            # 2. Offload text_encoder to CPU
            # 3. Run denoising + VAE decode with freed VRAM
            #
            # We hook this by moving text_encoder to CPU right after
            # prompt encoding completes within generate().  Since the
            # pipeline calls text_encoder() early then never again,
            # we wrap it with a one-shot hook.
            if offload_text_encoder:
                _original_forward = text_encoder.forward

                def _offloading_forward(*args, **kwargs):
                    result = _original_forward(*args, **kwargs)
                    # Move to CPU immediately after encoding
                    text_encoder.to("cpu")
                    torch.cuda.empty_cache()
                    logger.info(
                        "Text encoder offloaded to CPU — freed ~%.1f GiB VRAM",
                        sum(p.numel() * p.element_size() for p in text_encoder.parameters()) / 1024**3,
                    )
                    # Restore original forward for subsequent calls
                    text_encoder.forward = _original_forward
                    return result

                text_encoder.forward = _offloading_forward

            try:
                return zimage_generate(
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    **components,
                    height=request.height,
                    width=request.width,
                    num_inference_steps=num_steps,
                    guidance_scale=guidance_scale,
                    generator=generator,
                    num_images_per_prompt=request.num_images_per_prompt,
                )
            finally:
                # Ensure text_encoder forward is always restored
                if offload_text_encoder:
                    text_encoder.forward = _original_forward

        # Run inference in thread pool (CPU-bound with GPU ops)
        loop = asyncio.get_event_loop()
        images = await loop.run_in_executor(None, _generate_with_offload)

        # Move text encoder back to GPU for next request (in background)
        if offload_text_encoder and not next(text_encoder.parameters()).is_cuda:
            text_encoder.to(model_manager._device)

        elapsed = time.time() - start_time

        # Save image(s)
        image_urls = []
        port = CONFIG.get("server", {}).get("port", 8003)
        base_url = f"http://127.0.0.1:{port}"

        for img in images:
            with _name_lock:
                filename = _next_unique_name("png")
            filepath = OUTPUT_DIR / filename
            img.save(filepath)
            image_urls.append(f"{base_url}/output/{filename}")

        logger.info(
            f"Generated {len(images)} image(s): {request.width}x{request.height}, "
            f"steps={num_steps}, seed={seed}, time={elapsed:.1f}s"
        )

        return GenerateResponse(
            success=True,
            image_url=image_urls[0],
            image_urls=image_urls,
            seed=seed,
            width=request.width,
            height=request.height,
            num_inference_steps=num_steps,
            guidance_scale=guidance_scale,
            generation_time_seconds=round(elapsed, 2),
            model_variant=model_manager.variant,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Generation failed. Check server logs for details.")


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve a generated image file."""
    # Sanitize filename
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if not safe or safe != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = OUTPUT_DIR / safe
    # Defence-in-depth: verify resolved path stays within OUTPUT_DIR
    if not path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path, media_type="image/png")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Z-Image Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=CONFIG.get("server", {}).get("port", 8003),
    )
    parser.add_argument(
        "--variant",
        default=None,
        choices=["turbo", "base"],
        help="Model variant",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Idle unload timeout (seconds)",
    )

    args = parser.parse_args()

    # Override config with CLI args
    if args.variant:
        CONFIG.setdefault("model", {})["variant"] = args.variant
    if args.idle_timeout is not None:
        CONFIG.setdefault("memory", {})["idle_timeout"] = args.idle_timeout

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
