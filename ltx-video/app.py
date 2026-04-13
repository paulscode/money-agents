"""
LTX-2.3 Native Video Generation Server

A standalone FastAPI server for text-to-video generation with synchronized audio
using the LTX-2.3 22B distilled FP8 model via the official ltx-pipelines package.

Features:
  - LTX-2.3 22B DiT (FP8 quantized, distilled 8+4 step schedule)
  - Gemma 3 12B text encoder (auto-managed lifecycle via building blocks)
  - 2x spatial latent upsampling (384×256 → 768×512)
  - Synchronized audio generation
  - Layer streaming for low-VRAM GPUs (≤26 GB)
  - Lazy model loading with idle GPU memory unloading
  - Optional Ollama prompt enhancement (zero VRAM cost)

API Endpoints:
  POST /generate    - Generate video from text prompt
  GET  /health      - Health check
  GET  /info        - Server info (model, VRAM, capabilities)
  POST /unload      - Release GPU VRAM (for GPU lifecycle)
  GET  /output/{f}  - Retrieve generated video files
"""

import asyncio
import gc
import json
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import add_security_middleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ltx-video")

# Ensure expandable segments for VRAM management (critical for 24GB cards)
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

# NOTE: Flash/CUTLASS SDPA backends are configured per-GPU in
# _configure_gpu_backends() — Flash Attention on Ampere+ (CC ≥ 8.0),
# CUTLASS mem-efficient via bf16→fp16 cast on Turing (CC 7.x).
# See _install_turing_fp16_attention() — patches F.scaled_dot_product_attention
# globally so both the LTX transformer and Gemma text encoder benefit.


# =============================================================================
# Turing GPU Compatibility — bf16→fp16 SDPA cast
# =============================================================================


def _install_turing_fp16_attention():
    """
    Wrap torch.nn.functional.scaled_dot_product_attention to cast bf16→fp16.

    On Turing GPUs (CC 7.x), the CUTLASS mem-efficient SDPA backend supports fp16
    but NOT bf16 ("cutlassF: no kernel found to launch!").  Flash Attention is
    Ampere+ only.  Without this patch, Turing falls back to the O(N²) math backend
    which materialises the full N×N attention matrix — costing 4-19 GiB extra for
    typical LTX-2 sequence lengths (4.5 GiB at 121 frames / 768×512).

    The fix: wrap Q/K/V in .half() before SDPA and cast output back to the original
    dtype.  CUTLASS mem_efficient then runs in O(N) memory — same profile as Flash
    Attention on Ampere.

    This patches at the torch.nn.functional level so it covers ALL SDPA callers:
    - ltx_core transformer attention (via PytorchAttention)
    - transformers Gemma text encoder (via sdpa_attention_forward)
    - Any other code path using F.scaled_dot_product_attention

    Precision impact is negligible: fp16 has 10 mantissa bits (vs bf16's 7) so the
    intermediate attention computation is actually *more* precise; only the dynamic
    range narrows from ±3.4e38 to ±6.5e4, which is fine for the small scaled values
    in Q·K^T / √d.

    Measured on Quadro RTX 8000 (CC 7.5) with N=6144 (121 frames, 768×512):
      math fallback:   10.508 GiB
      fp16 mem-efficient: 0.188 GiB  (savings: 10.32 GiB)
    """
    import torch
    import torch.nn.functional as F

    _original_sdpa = F.scaled_dot_product_attention

    def _fp16_sdpa(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False, scale=None, **kwargs):
        original_dtype = query.dtype

        if original_dtype == torch.bfloat16:
            # Cast bf16→fp16 so CUTLASS mem-efficient kernel can run
            query = query.half()
            key = key.half()
            value = value.half()
            if attn_mask is not None and attn_mask.dtype == torch.bfloat16:
                attn_mask = attn_mask.half()
            out = _original_sdpa(query, key, value, attn_mask=attn_mask,
                                 dropout_p=dropout_p, is_causal=is_causal,
                                 scale=scale, **kwargs)
            return out.to(original_dtype)

        return _original_sdpa(query, key, value, attn_mask=attn_mask,
                              dropout_p=dropout_p, is_causal=is_causal,
                              scale=scale, **kwargs)

    F.scaled_dot_product_attention = _fp16_sdpa
    # Also patch the module-level reference that some callers use
    torch.nn.functional.scaled_dot_product_attention = _fp16_sdpa
    logger.info("[compat] F.scaled_dot_product_attention patched: bf16→fp16 cast for CUTLASS mem-efficient SDPA")


def _configure_gpu_backends(device):
    """Configure SDPA backends based on GPU compute capability."""
    import torch
    try:
        import torch.backends.cudnn
        import torch.backends.cuda as cuda_be
        cc = torch.cuda.get_device_capability(device)
        if cc < (8, 0):
            # Turing (CC 7.x): no native bf16 Flash Attention.
            # CUTLASS mem-efficient backend works on Turing with fp16 inputs.
            # Install a lightweight wrapper that casts bf16→fp16 around SDPA,
            # giving O(N) memory attention instead of O(N²) math fallback.
            torch.backends.cudnn.enabled = False
            cuda_be.enable_flash_sdp(False)       # Flash: Ampere+ only
            cuda_be.enable_mem_efficient_sdp(True) # CUTLASS: works on Turing w/ fp16
            _install_turing_fp16_attention()
            logger.info(
                f"[compat] Turing GPU {device} (CC {cc[0]}.{cc[1]}): "
                "cuDNN disabled, mem-efficient SDPA via bf16→fp16 cast"
            )
        else:
            # Ampere+ (CC 8.x+): enable Flash Attention for O(N) memory
            cuda_be.enable_flash_sdp(True)
            cuda_be.enable_mem_efficient_sdp(True)
            logger.info(
                f"[compat] Ampere+ GPU {device} (CC {cc[0]}.{cc[1]}): "
                "Flash Attention + mem-efficient SDPA enabled"
            )
    except Exception as e:
        logger.warning(f"Could not configure GPU backends: {e}")



# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


GENERATION_LOG = OUTPUT_DIR / "generations.jsonl"


def _log_generation(entry: dict):
    """Append a generation record to the JSONL log for later analysis."""
    try:
        with open(GENERATION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"Failed to write generation log: {e}")


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
    Manages LTX-2.3 model lifecycle with lazy loading and idle unloading.

    Uses the official ltx-pipelines DistilledPipeline which handles:
    - Building blocks (PromptEncoder, DiffusionStage, etc.) that auto-manage
      model lifecycle — each block loads its model, uses it, then frees GPU memory
    - `streaming_prefetch_count` for low-VRAM GPUs (≤26 GB) — streams transformer
      and text encoder layers through GPU one batch at a time
    - BF16 dequantized checkpoint (fp8 weights pre-multiplied by weight_scale)
    - Two-stage distilled inference (8 steps + 3 steps)
    """

    def __init__(self, config: dict):
        self.config = config
        self._pipeline = None
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._in_use = 0  # active generation count — blocks idle unload
        self._idle_timeout = config.get("memory", {}).get("idle_timeout", 300)
        self._unload_task: Optional[asyncio.Task] = None
        self._streaming_prefetch_count: Optional[int] = None

        # Resolve model directory
        model_config = config.get("model", {})
        model_dir = model_config.get("model_dir", "../models/ltx-2")
        self._model_dir = Path(model_dir)
        if not self._model_dir.is_absolute():
            self._model_dir = (SCRIPT_DIR / self._model_dir).resolve()

        self._checkpoint = model_config.get("checkpoint", "ltx-2.3-22b-distilled.safetensors")
        self._spatial_upsampler = model_config.get("spatial_upsampler", "ltx-2.3-spatial-upscaler-x2-1.1.safetensors")
        self._gemma_dir = model_config.get("gemma_dir", "gemma-3-12b")

        # Select GPU: default cuda:0, overridable via config cuda_device
        self._device = "cpu"
        try:
            import torch
            if torch.cuda.is_available():
                cuda_device = str(model_config.get("cuda_device", "0"))
                idx = int(cuda_device)
                torch.cuda.set_device(idx)
                self._device = f"cuda:{idx}"
        except ImportError:
            pass

        logger.info(
            f"ModelManager initialized: device={self._device}, "
            f"model_dir={self._model_dir}"
        )

    @property
    def is_loaded(self) -> bool:
        return self._pipeline is not None

    async def get_pipeline(self):
        """Get loaded pipeline, loading if necessary."""
        async with self._lock:
            if self._pipeline is None:
                await self._load_model()
            self._in_use += 1
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._pipeline

    def release_pipeline(self):
        """Mark pipeline as no longer in active use and reschedule idle unload."""
        self._in_use = max(0, self._in_use - 1)
        self._last_used = time.time()
        self._schedule_idle_unload()

    async def _load_model(self):
        """Load LTX-2.3 DistilledPipeline."""
        import torch

        checkpoint_path = self._model_dir / self._checkpoint
        upsampler_path = self._model_dir / self._spatial_upsampler
        gemma_root = self._model_dir / self._gemma_dir

        # Validate model files exist
        missing = []
        if not checkpoint_path.exists():
            missing.append(str(checkpoint_path))
        if not upsampler_path.exists():
            missing.append(str(upsampler_path))
        if not gemma_root.exists():
            missing.append(str(gemma_root))

        if missing:
            raise RuntimeError(
                f"Missing model files: {', '.join(missing)}. "
                f"Download from https://huggingface.co/Lightricks/LTX-2.3-fp8"
            )

        logger.info(f"Loading LTX-2.3 DistilledPipeline from {self._model_dir}...")
        start = time.time()

        try:
            loop = asyncio.get_event_loop()
            self._pipeline = await loop.run_in_executor(
                None,
                lambda: self._load_sync(
                    str(checkpoint_path),
                    str(upsampler_path),
                    str(gemma_root),
                ),
            )
            elapsed = time.time() - start
            logger.info(f"Pipeline loaded in {elapsed:.1f}s")

        except Exception as e:
            logger.error(f"Failed to load pipeline: {e}", exc_info=True)
            raise

    def _load_sync(self, checkpoint_path: str, upsampler_path: str, gemma_root: str):
        """Synchronous pipeline loading — runs in thread pool."""
        import torch
        from ltx_pipelines.distilled import DistilledPipeline

        device = torch.device(self._device)

        # Configure GPU SDPA backends before loading the pipeline
        _configure_gpu_backends(device)

        pipeline = DistilledPipeline(
            distilled_checkpoint_path=checkpoint_path,
            spatial_upsampler_path=upsampler_path,
            gemma_root=gemma_root,
            loras=[],
            device=device,
        )

        # Determine streaming prefetch count based on VRAM.
        # The Gemma 3 12B text encoder in fp32 is ~46 GB, so it won't fit fully
        # on any GPU with less than ~48 GB VRAM.  The upstream building blocks
        # support layer streaming: models are built on CPU and layers are streamed
        # to GPU one batch at a time, keeping GPU usage manageable.
        # On 80 GB+ cards, disable streaming for speed.
        gpu_idx = device.index if device.index is not None else 0
        total_gb = torch.cuda.get_device_properties(gpu_idx).total_memory / (1024**3)
        if total_gb < 48:
            self._streaming_prefetch_count = 2
            logger.info(
                f"[streaming] GPU {gpu_idx} has {total_gb:.0f} GB — "
                f"layer streaming enabled (prefetch={self._streaming_prefetch_count})"
            )
        else:
            self._streaming_prefetch_count = None
            logger.info(
                f"[streaming] GPU {gpu_idx} has {total_gb:.0f} GB — "
                "full model loading (no streaming)"
            )

        return pipeline

    async def unload_model(self):
        """Unload pipeline to free GPU memory."""
        async with self._lock:
            if self._pipeline is not None:
                import torch

                logger.info("Unloading LTX-2.3 pipeline to free GPU memory...")
                del self._pipeline
                self._pipeline = None
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                logger.info("Pipeline unloaded, GPU memory freed")

    def _schedule_idle_unload(self):
        """Schedule pipeline unload after idle timeout."""
        if self._idle_timeout <= 0:
            return

        if self._unload_task and not self._unload_task.done():
            self._unload_task.cancel()

        async def _check_and_unload():
            await asyncio.sleep(self._idle_timeout)
            if self._in_use > 0:
                return  # generation in progress — skip unload
            if time.time() - self._last_used >= self._idle_timeout:
                logger.info(f"Idle timeout ({self._idle_timeout}s) reached, unloading...")
                await self.unload_model()

        try:
            loop = asyncio.get_event_loop()
            self._unload_task = loop.create_task(_check_and_unload())
        except RuntimeError:
            pass


# =============================================================================
# Request / Response Models
# =============================================================================


class GenerateRequest(BaseModel):
    """Request body for /generate endpoint."""
    prompt: str = Field(
        ...,
        description=(
            "Text prompt for video generation. LTX-2 generates synchronized "
            "audio from the prompt, so include audio descriptions — e.g. "
            "background sounds, ambient noise, SFX, speech/dialogue in quotes."
        ),
    )
    width: int = Field(default=768, description="Final output width (divisible by 32)")
    height: int = Field(default=512, description="Final output height (divisible by 32)")
    num_frames: int = Field(
        default=241,
        description="Number of frames. Must be (N*8)+1. 241 = ~10s at 24fps",
    )
    fps: int = Field(default=24, description="Output framerate")
    seed: Optional[int] = Field(None, description="Random seed for reproducibility")
    enhance_prompt: bool = Field(
        default=False,
        description="Enhance prompt via local Ollama before generation",
    )


class GenerateResponse(BaseModel):
    """Response body for /generate endpoint."""
    success: bool
    video_url: str
    filename: str
    duration_seconds: float
    resolution: str
    frames: int
    fps: int
    has_audio: bool
    inference_time: float
    seed: int
    model: str


# =============================================================================
# Output File Management
# =============================================================================

_name_lock = threading.Lock()


def _next_unique_name() -> str:
    """Generate next sequential filename."""
    prefix = CONFIG.get("output", {}).get("prefix", "LTX2_")
    digits = CONFIG.get("output", {}).get("digits", 5)
    ext = CONFIG.get("output", {}).get("extension", "mp4")

    max_n = 0
    for p in OUTPUT_DIR.glob(f"{prefix}{'[0-9]' * digits}.{ext}"):
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
# Ollama Prompt Enhancement
# =============================================================================

ENHANCE_SYSTEM_PROMPT = """You are a video prompt engineer. Given a short description, expand it into a detailed video generation prompt following these rules:
- Single flowing paragraph, max 200 words
- Use present-progressive verbs ("is walking", "speaking")
- Include: lighting, textures, camera angles, character details (gender, clothing, hair, expressions)
- Describe chronological flow with temporal connectors ("as", "then", "while")
- Include audio layer: background sounds, ambient noise, SFX, speech with exact dialogue in quotes
- Start directly with the scene — no preamble
- Be specific and literal, not dramatic or exaggerated
- Do NOT invent characters, speech, or camera motion unless requested
- Prefix with style if clear: "Style: realistic with cinematic lighting."
Output ONLY the enhanced prompt, nothing else."""


async def enhance_prompt_via_ollama(raw_prompt: str) -> str:
    """Enhance a short/vague prompt using Ollama. Zero VRAM since Ollama is separate."""
    import httpx

    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model = os.getenv("OLLAMA_ENHANCE_MODEL", "mistral:7b")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": ollama_model,
                    "prompt": raw_prompt,
                    "system": ENHANCE_SYSTEM_PROMPT,
                    "stream": False,
                    "options": {"num_predict": 400},
                },
                timeout=30,
            )
            if resp.status_code == 200:
                enhanced = resp.json().get("response", "").strip()
                if enhanced:
                    logger.info(f"Prompt enhanced via Ollama ({ollama_model})")
                    return enhanced
    except Exception as e:
        logger.warning(f"Ollama prompt enhancement failed (using original): {e}")

    return raw_prompt


# =============================================================================
# FastAPI Application
# =============================================================================

model_manager: Optional[ModelManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize model manager."""
    global model_manager
    model_manager = ModelManager(CONFIG)
    logger.info("LTX-2.3 video server starting...")
    yield
    logger.info("LTX-2.3 video server shutting down...")
    if model_manager and model_manager.is_loaded:
        await model_manager.unload_model()


app = FastAPI(
    title="LTX-2.3 Video Server",
    description=(
        "Native LTX-2.3 22B distilled FP8 video generation server — "
        "text-to-video with synchronized audio, lazy model loading, "
        "and idle GPU unloading"
    ),
    version="2.0.0",
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
    """Unload pipeline from GPU to free VRAM.

    The pipeline will be lazy-loaded again on the next /generate request.
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
            gpu_idx = int(model_manager._device.split(":")[-1]) if "cuda" in model_manager._device else 0
            props = torch.cuda.get_device_properties(gpu_idx)
            gpu_info = {
                "name": props.name,
                "vram_total_mb": props.total_memory // (1024 * 1024),
                "vram_free_mb": (
                    props.total_memory - torch.cuda.memory_allocated(gpu_idx)
                ) // (1024 * 1024),
            }
    except Exception:
        pass

    defaults = CONFIG.get("defaults", {})
    return {
        "model_loaded": model_manager.is_loaded,
        "model": "ltx-2.3-22b-distilled",
        "text_encoder": "gemma-3-12b",
        "pipeline": "DistilledPipeline",
        "stages": "2 (8 steps + 4 steps)",
        "device": model_manager._device,
        "gpu": gpu_info,
        "idle_timeout": model_manager._idle_timeout,
        "defaults": {
            "width": defaults.get("width", 768),
            "height": defaults.get("height", 512),
            "num_frames": defaults.get("num_frames", 241),
            "fps": defaults.get("fps", 24),
        },
        "capabilities": {
            "audio": True,
            "image_to_video": True,
            "max_verified_resolution": "768x512",
            "max_verified_frames": 241,
            "max_verified_duration_s": 10,
        },
    }


@app.get("/generations")
async def get_generation_log(limit: int = 100, offset: int = 0):
    """Retrieve the generation log for prompt analysis.

    Returns JSONL entries with original/enhanced prompts, parameters, and results.
    Most recent entries first.
    """
    if not GENERATION_LOG.exists():
        return {"entries": [], "total": 0}

    lines = GENERATION_LOG.read_text(encoding="utf-8").strip().split("\n")
    lines = [l for l in lines if l.strip()]
    total = len(lines)

    # Most recent first
    lines.reverse()
    page = lines[offset : offset + limit]

    entries = []
    for line in page:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return {"entries": entries, "total": total, "limit": limit, "offset": offset}


@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Generate video with synchronized audio from a text prompt."""
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")

    # Validate dimensions
    if request.width % 32 != 0:
        raise HTTPException(status_code=400, detail="Width must be divisible by 32")
    if request.height % 32 != 0:
        raise HTTPException(status_code=400, detail="Height must be divisible by 32")
    if request.width < 256 or request.width > 1280:
        raise HTTPException(status_code=400, detail="Width must be between 256 and 1280")
    if request.height < 256 or request.height > 720:
        raise HTTPException(status_code=400, detail="Height must be between 256 and 720")

    # Validate frame count: must be (N*8)+1
    if (request.num_frames - 1) % 8 != 0:
        raise HTTPException(
            status_code=400,
            detail=f"num_frames must be (N*8)+1, e.g. 97, 161, 241. Got {request.num_frames}",
        )
    if request.num_frames < 9 or request.num_frames > 257:
        raise HTTPException(status_code=400, detail="num_frames must be between 9 and 257")

    # Apply defaults from config
    defaults = CONFIG.get("defaults", {})

    seed = request.seed if request.seed is not None else secrets.randbelow(2**31)

    # Optional prompt enhancement via Ollama
    original_prompt = request.prompt
    prompt = original_prompt
    prompt_was_enhanced = False
    if request.enhance_prompt:
        prompt = await enhance_prompt_via_ollama(prompt)
        prompt_was_enhanced = (prompt != original_prompt)

    start_time = time.time()

    try:
        pipeline = await model_manager.get_pipeline()

        try:
            # Run generation in thread pool (blocking GPU work)
            loop = asyncio.get_event_loop()

            with _name_lock:
                filename = _next_unique_name()
            output_path = OUTPUT_DIR / filename

            await loop.run_in_executor(
                None,
                lambda: _generate_sync(
                    pipeline,
                    prompt=prompt,
                    seed=seed,
                    height=request.height,
                    width=request.width,
                    num_frames=request.num_frames,
                    frame_rate=float(request.fps),
                    output_path=str(output_path),
                    streaming_prefetch_count=model_manager._streaming_prefetch_count,
                    device=model_manager._device,
                ),
            )
        finally:
            model_manager.release_pipeline()

        elapsed = time.time() - start_time
        duration_seconds = round(request.num_frames / request.fps, 2)

        port = CONFIG.get("server", {}).get("port", 8006)
        base_url = f"http://127.0.0.1:{port}"

        logger.info(
            f"Generated video: {filename}, {request.width}x{request.height}, "
            f"{request.num_frames} frames, seed={seed}, time={elapsed:.1f}s"
        )

        # Log generation details for later analysis
        _log_generation({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
            "original_prompt": original_prompt,
            "enhanced_prompt": prompt if prompt_was_enhanced else None,
            "prompt_was_enhanced": prompt_was_enhanced,
            "width": request.width,
            "height": request.height,
            "num_frames": request.num_frames,
            "fps": request.fps,
            "seed": seed,
            "duration_seconds": duration_seconds,
            "inference_time": round(elapsed, 2),
            "success": True,
            "error": None,
        })

        return GenerateResponse(
            success=True,
            video_url=f"{base_url}/output/{filename}",
            filename=filename,
            duration_seconds=duration_seconds,
            resolution=f"{request.width}x{request.height}",
            frames=request.num_frames,
            fps=request.fps,
            has_audio=True,
            inference_time=round(elapsed, 2),
            seed=seed,
            model="ltx-2.3-22b-distilled",
        )

    except HTTPException:
        raise
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Generation failed: {e}", exc_info=True)
        _log_generation({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "filename": None,
            "original_prompt": original_prompt,
            "enhanced_prompt": prompt if prompt_was_enhanced else None,
            "prompt_was_enhanced": prompt_was_enhanced,
            "width": request.width,
            "height": request.height,
            "num_frames": request.num_frames,
            "fps": request.fps,
            "seed": seed,
            "duration_seconds": None,
            "inference_time": round(elapsed, 2),
            "success": False,
            "error": str(e),
        })
        raise HTTPException(status_code=500, detail="Generation failed")


def _generate_sync(
    pipeline,
    prompt: str,
    seed: int,
    height: int,
    width: int,
    num_frames: int,
    frame_rate: float,
    output_path: str,
    streaming_prefetch_count: int | None = None,
    device: str = "cuda:0",
):
    """Synchronous video generation — runs in thread pool."""
    import torch
    from ltx_core.model.video_vae import TilingConfig, get_video_chunks_number
    from ltx_pipelines.utils.media_io import encode_video

    # Ensure correct CUDA device in worker thread (thread pool doesn't inherit device context)
    if device.startswith("cuda:"):
        torch.cuda.set_device(int(device.split(":")[-1]))

    tiling_config = TilingConfig.default()
    video_chunks_number = get_video_chunks_number(num_frames, tiling_config)

    logger.info(
        f"Starting generation: {width}x{height}, {num_frames} frames, "
        f"seed={seed}, frame_rate={frame_rate}"
        + (f", streaming_prefetch={streaming_prefetch_count}" if streaming_prefetch_count else "")
    )

    with torch.no_grad():
        video, audio = pipeline(
            prompt=prompt,
            seed=seed,
            height=height,
            width=width,
            num_frames=num_frames,
            frame_rate=frame_rate,
            images=[],
            tiling_config=tiling_config,
            enhance_prompt=False,
            streaming_prefetch_count=streaming_prefetch_count,
        )

        logger.info("Encoding video + audio to MP4...")

        encode_video(
            video=video,
            fps=frame_rate,
            audio=audio,
            output_path=output_path,
            video_chunks_number=video_chunks_number,
        )

    logger.info(f"Video saved to {output_path}")


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve a generated video file."""
    # Sanitize filename
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if not safe or safe != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = OUTPUT_DIR / safe
    if not path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path, media_type="video/mp4")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="LTX-2.3 Video Generation Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=CONFIG.get("server", {}).get("port", 8006),
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Idle unload timeout in seconds (0 = never)",
    )

    args = parser.parse_args()

    if args.idle_timeout is not None:
        CONFIG.setdefault("memory", {})["idle_timeout"] = args.idle_timeout

    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
