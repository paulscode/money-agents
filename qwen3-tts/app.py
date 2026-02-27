"""
Qwen3-TTS Standalone FastAPI Server

A native TTS server using the qwen-tts Python package directly,
bypassing ComfyUI for lower latency and full feature access.

Supports 4 generation modes:
  1. custom_voice - Use one of 9 built-in speakers (1.7B only)
  2. voice_clone  - Clone a voice from a reference audio sample
  3. voice_design - Generate a voice from a text description (1.7B only)
  4. voice_design_clone - Design a voice then use it as clone reference (1.7B only)

API Endpoints:
  POST /generate       - Generate speech
  POST /upload_voice   - Upload a voice sample for cloning
  GET  /voices         - List available voices (built-in + uploaded)
  GET  /health         - Health check
  GET  /info           - Server info (model, VRAM, capabilities)
"""

import asyncio
import hashlib
import io
import json
import logging
import os
import shutil
import sys
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import soundfile as sf
import numpy as np
import torch
import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import add_security_middleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("qwen3-tts")

# =============================================================================
# Configuration
# =============================================================================

CONFIG_PATH = Path(__file__).parent / "config.yaml"
VOICES_DIR = Path(__file__).parent / "voices"
OUTPUT_DIR = Path(__file__).parent / "output"

# Ensure directories exist
VOICES_DIR.mkdir(parents=True, exist_ok=True)
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
    Manages Qwen3-TTS model lifecycle with lazy loading, idle unloading,
    and automatic model variant swapping.

    Qwen3-TTS has three separate model checkpoints, each supporting
    different generation methods:
      - CustomVoice  → generate_custom_voice()
      - VoiceDesign  → generate_voice_design()
      - Base         → generate_voice_clone() + create_voice_clone_prompt()

    The manager hot-swaps between variants as needed: when a request
    requires a different variant than what's loaded, it unloads the
    current model and loads the required one.
    """

    # Map each generation mode to the model variant it requires
    MODE_TO_VARIANT = {
        "custom_voice": "custom_voice",
        "voice_clone": "base",
        "voice_design": "voice_design",
        "voice_design_clone": "voice_design",  # first step; second step needs "base"
    }

    
    def __init__(self, config: dict):
        self.config = config
        self._model = None
        self._model_variant = None  # "custom_voice", "voice_design", or "base"
        self._lock = asyncio.Lock()
        self._last_used = 0.0
        self._idle_timeout = config.get("memory", {}).get("idle_timeout", 300)
        self._unload_task: Optional[asyncio.Task] = None
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        # Determine tier (full = 1.7B variants, lite = 0.6B base only)
        default_tier = config.get("models", {}).get("default_tier", "auto")
        if default_tier == "auto":
            self._tier = self._auto_detect_tier()
        else:
            self._tier = default_tier

        logger.info(f"ModelManager initialized: device={self._device}, tier={self._tier}")

    def _auto_detect_tier(self) -> str:
        """Auto-detect best model tier based on GPU VRAM."""
        if not torch.cuda.is_available():
            return "lite"
        try:
            vram_bytes = torch.cuda.get_device_properties(0).total_memory
            vram_gb = vram_bytes / (1024 ** 3)
            logger.info(f"GPU VRAM detected: {vram_gb:.1f}GB")
            return "full" if vram_gb >= 8 else "lite"
        except Exception as e:
            logger.warning(f"VRAM detection failed: {e}, defaulting to lite")
            return "lite"

    def _get_model_id(self, variant: str) -> str:
        """Get the HuggingFace model ID for a given variant."""
        models = self.config.get("models", {})
        if variant == "custom_voice":
            return models.get("custom_voice", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
        elif variant == "voice_design":
            return models.get("voice_design", "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign")
        elif variant == "base":
            if self._tier == "full":
                return models.get("base_full", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
            else:
                return models.get("base_lite", "Qwen/Qwen3-TTS-12Hz-0.6B-Base")
        raise ValueError(f"Unknown model variant: {variant}")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def current_variant(self) -> Optional[str]:
        return self._model_variant

    @property
    def capabilities(self) -> List[str]:
        """Get supported generation modes for current tier."""
        if self._tier == "full":
            return ["custom_voice", "voice_clone", "voice_design", "voice_design_clone"]
        else:
            return ["voice_clone"]

    async def get_model_for_mode(self, mode: str):
        """
        Get a loaded model suitable for the given mode, hot-swapping if needed.

        If the currently loaded model variant doesn't match what the mode
        requires, unloads the current model and loads the correct one.
        """
        required_variant = self.MODE_TO_VARIANT.get(mode)
        if not required_variant:
            raise ValueError(f"Unknown mode: {mode}")

        async with self._lock:
            if self._model is None or self._model_variant != required_variant:
                if self._model is not None:
                    logger.info(
                        f"Mode '{mode}' needs variant '{required_variant}' "
                        f"but '{self._model_variant}' is loaded — swapping..."
                    )
                    await self._unload_model_unlocked()
                await self._load_model(required_variant)
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._model

    async def get_model(self):
        """Get the currently loaded model (loads default variant if none loaded)."""
        async with self._lock:
            if self._model is None:
                default_variant = "custom_voice" if self._tier == "full" else "base"
                await self._load_model(default_variant)
            self._last_used = time.time()
            self._schedule_idle_unload()
            return self._model

    async def _load_model(self, variant: str):
        """Load a specific model variant (must be called with lock held)."""
        from qwen_tts import Qwen3TTSModel

        model_id = self._get_model_id(variant)
        logger.info(f"Loading model variant '{variant}': {model_id} ...")
        start = time.time()

        try:
            dtype_str = self.config.get("memory", {}).get("dtype", "float16")
            dtype = torch.float16 if dtype_str == "float16" else torch.float32
            device_map = self._device if self._device == "cpu" else "cuda:0"

            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: Qwen3TTSModel.from_pretrained(
                    model_id, device_map=device_map, torch_dtype=dtype
                )
            )
            self._model_variant = variant
            elapsed = time.time() - start
            logger.info(f"Model '{variant}' loaded in {elapsed:.1f}s ({model_id})")
        except Exception as e:
            logger.error(f"Failed to load model {model_id}: {e}")
            raise

    async def _unload_model_unlocked(self):
        """Unload model (must be called with lock held)."""
        if self._model is not None:
            variant = self._model_variant
            logger.info(f"Unloading model variant '{variant}'...")
            del self._model
            self._model = None
            self._model_variant = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            logger.info(f"Model '{variant}' unloaded")

    async def unload_model(self):
        """Unload model to free GPU memory."""
        async with self._lock:
            await self._unload_model_unlocked()
    
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
    text: str = Field(..., description="Text to convert to speech", max_length=4096)
    mode: str = Field(
        "custom_voice",
        description="Generation mode: custom_voice, voice_clone, voice_design, voice_design_clone"
    )
    # For custom_voice mode
    voice: Optional[str] = Field(None, description="Built-in voice name (e.g., 'Ryan', 'Aiden')")
    instruct: Optional[str] = Field(None, description="Instruction for voice style (e.g., 'Speak happily')")
    # For voice_clone mode
    reference_audio: Optional[str] = Field(
        None, description="Filename of uploaded voice sample (from /upload_voice)"
    )
    reference_text: Optional[str] = Field(
        None, description="Transcript of the reference audio (optional, improves quality)"
    )
    # For voice_design mode
    voice_description: Optional[str] = Field(
        None, description="Natural language description of desired voice (e.g., 'A warm female voice with slight British accent')"
    )
    # Common parameters
    sample_rate: Optional[int] = Field(None, description="Output sample rate (default from config)")
    output_format: str = Field("wav", description="Output format: wav or mp3")


class GenerateResponse(BaseModel):
    """Response body for /generate endpoint."""
    success: bool
    mode: str
    audio_url: str
    duration_seconds: float
    sample_rate: int
    generation_time_seconds: float
    model_tier: str


class VoiceInfo(BaseModel):
    """Info about an available voice."""
    name: str
    type: str  # "builtin" or "uploaded"
    language: Optional[str] = None
    description: Optional[str] = None
    filename: Optional[str] = None  # For uploaded voices


# =============================================================================
# FastAPI Application
# =============================================================================

model_manager: Optional[ModelManager] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize model manager."""
    global model_manager
    model_manager = ModelManager(CONFIG)
    logger.info("Qwen3-TTS server starting...")
    yield
    logger.info("Qwen3-TTS server shutting down...")
    if model_manager and model_manager.is_loaded:
        await model_manager.unload_model()


app = FastAPI(
    title="Qwen3-TTS Server",
    description="Native Qwen3-TTS voice generation server with voice cloning, custom voices, and voice design",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware (SA-12)
from fastapi.middleware.cors import CORSMiddleware
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
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        gpu_info = {
            "name": props.name,
            "vram_total_mb": props.total_memory // (1024 * 1024),
            "vram_free_mb": (props.total_memory - torch.cuda.memory_allocated(0)) // (1024 * 1024),
        }
    
    return {
        "model_loaded": model_manager.is_loaded,
        "model_variant": model_manager.current_variant,
        "tier": model_manager._tier,
        "device": model_manager._device,
        "capabilities": model_manager.capabilities,
        "gpu": gpu_info,
        "idle_timeout": model_manager._idle_timeout,
        "custom_voices": [v["name"] for v in CONFIG.get("custom_voices", [])],
    }


@app.get("/voices")
async def list_voices():
    """List all available voices (built-in + uploaded)."""
    voices = []
    
    # Built-in custom voices (1.7B only)
    tier = model_manager._tier if model_manager else "unknown"
    if tier == "full":
        for v in CONFIG.get("custom_voices", []):
            voices.append(VoiceInfo(
                name=v["name"],
                type="builtin",
                language=v.get("language"),
                description=v.get("description"),
            ))
    
    # Uploaded voice samples (available for voice cloning with any model)
    if VOICES_DIR.exists():
        for f in sorted(VOICES_DIR.iterdir()):
            if f.suffix.lower() in (".wav", ".mp3", ".flac", ".ogg"):
                voices.append(VoiceInfo(
                    name=f.stem,
                    type="uploaded",
                    filename=f.name,
                    description=f"Uploaded voice sample ({f.suffix})",
                ))
    
    return {"voices": [v.model_dump() for v in voices], "model_tier": tier}


@app.post("/upload_voice")
async def upload_voice(
    file: UploadFile = File(..., description="Audio file (WAV, MP3, FLAC, OGG)"),
    name: Optional[str] = None,
):
    """Upload a voice sample for voice cloning."""
    # Validate file type
    allowed = {".wav", ".mp3", ".flac", ".ogg"}
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{ext}'. Use: {', '.join(allowed)}"
        )
    
    # Use provided name or filename stem
    voice_name = name or Path(file.filename).stem
    # Sanitize filename
    safe_name = "".join(c for c in voice_name if c.isalnum() or c in "-_").strip()
    if not safe_name:
        safe_name = f"voice_{int(time.time())}"
    
    dest = VOICES_DIR / f"{safe_name}{ext}"
    
    # Read file with size limit (50 MB max for voice samples)
    MAX_VOICE_SIZE = 50 * 1024 * 1024  # 50 MB
    content = await file.read()
    if len(content) > MAX_VOICE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content)} bytes). Maximum voice sample size is {MAX_VOICE_SIZE // (1024*1024)} MB."
        )
    
    with open(dest, "wb") as f:
        f.write(content)
    
    logger.info(f"Voice sample uploaded: {dest.name} ({len(content)} bytes)")
    
    return {
        "success": True,
        "name": safe_name,
        "filename": dest.name,
        "size_bytes": len(content),
    }


@app.post("/generate")
async def generate(request: GenerateRequest):
    """
    Generate speech from text.
    
    Modes:
    - custom_voice: Use a built-in speaker with optional instructions (1.7B only)
    - voice_clone: Clone voice from uploaded audio sample
    - voice_design: Create voice from text description (1.7B only)
    - voice_design_clone: Design a voice then use as clone prompt (1.7B only)
    """
    if not model_manager:
        raise HTTPException(status_code=503, detail="Server not initialized")
    
    mode = request.mode
    
    # Validate mode against capabilities
    if mode not in model_manager.capabilities:
        available = ", ".join(model_manager.capabilities)
        raise HTTPException(
            status_code=400,
            detail=f"Mode '{mode}' not available with {model_manager._tier} tier. Available modes: {available}"
        )
    
    start_time = time.time()
    
    try:
        # Get the correct model variant for this mode (auto-swaps if needed)
        model = await model_manager.get_model_for_mode(mode)
        
        # Generate based on mode — each returns (audio_np, sample_rate)
        if mode == "custom_voice":
            audio, sr = await _generate_custom_voice(model, request)
        elif mode == "voice_clone":
            audio, sr = await _generate_voice_clone(model, request)
        elif mode == "voice_design":
            audio, sr = await _generate_voice_design(model, request)
        elif mode == "voice_design_clone":
            audio, sr = await _generate_voice_design_clone(model, request)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown mode: {mode}")
        
        # Save output file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"tts_{mode}_{timestamp}.wav"
        output_path = OUTPUT_DIR / filename
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, lambda: sf.write(str(output_path), audio, sr)
        )
        
        duration = len(audio) / sr
        gen_time = time.time() - start_time
        
        logger.info(
            f"Generated: mode={mode}, duration={duration:.1f}s, "
            f"gen_time={gen_time:.1f}s, file={filename}"
        )
        
        return GenerateResponse(
            success=True,
            mode=mode,
            audio_url=f"/output/{filename}",
            duration_seconds=round(duration, 2),
            sample_rate=sr,
            generation_time_seconds=round(gen_time, 2),
            model_tier=model_manager.current_variant or "unknown",
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Generation failed. Check server logs for details.")


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Download a generated audio file."""
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = OUTPUT_DIR / safe_name
    if not path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path, media_type="audio/wav", filename=filename)


# =============================================================================
# Generation Functions
# =============================================================================


async def _generate_custom_voice(model, request: GenerateRequest):
    """Generate speech using a built-in custom voice (1.7B only)."""
    voice = request.voice
    if not voice:
        voice = "Ryan"  # Default English voice
    
    # Validate voice name
    valid_voices = [v["name"] for v in CONFIG.get("custom_voices", [])]
    if voice not in valid_voices:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown voice '{voice}'. Available: {', '.join(valid_voices)}"
        )
    
    instruct = request.instruct or ""
    text = request.text
    
    logger.info(f"Generating custom_voice: voice={voice}, instruct='{instruct[:50]}', text='{text[:50]}...'")
    
    loop = asyncio.get_event_loop()
    wavs, sr = await loop.run_in_executor(
        None,
        lambda: model.generate_custom_voice(
            text=text,
            speaker=voice,
            instruct=instruct if instruct else None,
        )
    )
    
    audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
    return audio, sr


async def _generate_voice_clone(model, request: GenerateRequest):
    """Generate speech by cloning a voice from a reference audio sample."""
    ref_audio = request.reference_audio
    if not ref_audio:
        raise HTTPException(
            status_code=400,
            detail="reference_audio is required for voice_clone mode. Upload a voice sample first via /upload_voice."
        )
    
    # Find the reference audio file
    ref_path = VOICES_DIR / ref_audio
    if not ref_path.exists():
        # Try with common extensions
        for ext in [".wav", ".mp3", ".flac", ".ogg"]:
            candidate = VOICES_DIR / f"{ref_audio}{ext}"
            if candidate.exists():
                ref_path = candidate
                break
        else:
            raise HTTPException(
                status_code=404,
                detail=f"Reference audio '{ref_audio}' not found. Upload via /upload_voice first."
            )
    
    text = request.text
    ref_text = request.reference_text or None
    
    logger.info(f"Generating voice_clone: ref={ref_path.name}, text='{text[:50]}...'")
    
    loop = asyncio.get_event_loop()
    
    # Generate speech with cloned voice — pass ref_audio path directly
    wavs, sr = await loop.run_in_executor(
        None,
        lambda: model.generate_voice_clone(
            text=text,
            ref_audio=str(ref_path),
            ref_text=ref_text,
        )
    )
    
    audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
    return audio, sr


async def _generate_voice_design(model, request: GenerateRequest):
    """Generate speech with a voice designed from a text description (1.7B only)."""
    description = request.voice_description
    if not description:
        raise HTTPException(
            status_code=400,
            detail="voice_description is required for voice_design mode."
        )
    
    text = request.text
    
    logger.info(f"Generating voice_design: desc='{description[:50]}', text='{text[:50]}...'")
    
    loop = asyncio.get_event_loop()
    wavs, sr = await loop.run_in_executor(
        None,
        lambda: model.generate_voice_design(
            text=text,
            instruct=description,
        )
    )
    
    audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
    return audio, sr


async def _generate_voice_design_clone(model, request: GenerateRequest):
    """Design a voice from description, then use it as a clone reference (1.7B only).
    
    This two-step process:
    1. Generates a short speech sample using the VoiceDesign model
    2. Swaps to the Base model and uses that sample as a voice clone prompt
    
    This can produce more stable results than voice_design alone for longer texts.
    """
    description = request.voice_description
    if not description:
        raise HTTPException(
            status_code=400,
            detail="voice_description is required for voice_design_clone mode."
        )
    
    text = request.text
    
    logger.info(f"Generating voice_design_clone: desc='{description[:50]}', text='{text[:50]}...'")
    
    loop = asyncio.get_event_loop()
    
    # Step 1: Generate a short sample using voice_design model (already loaded)
    sample_text = text[:100] if len(text) > 100 else text
    design_wavs, design_sr = await loop.run_in_executor(
        None,
        lambda: model.generate_voice_design(
            text=sample_text,
            instruct=description,
        )
    )
    design_audio = np.concatenate(design_wavs) if len(design_wavs) > 1 else design_wavs[0]
    
    # Step 2: Swap to Base model for voice clone
    logger.info("voice_design_clone: swapping to Base model for clone step...")
    clone_model = await model_manager.get_model_for_mode("voice_clone")
    
    wavs, sr = await loop.run_in_executor(
        None,
        lambda: clone_model.generate_voice_clone(
            text=text,
            ref_audio=(design_audio, design_sr),
            ref_text=sample_text,
        )
    )
    
    audio = np.concatenate(wavs) if len(wavs) > 1 else wavs[0]
    return audio, sr


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import argparse
    import uvicorn
    
    parser = argparse.ArgumentParser(description="Qwen3-TTS Server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=CONFIG.get("server", {}).get("port", 8002))
    parser.add_argument("--tier", default=None, help="Model tier: full, lite, auto")
    parser.add_argument("--idle-timeout", type=int, default=None, help="Idle unload timeout (seconds)")
    
    args = parser.parse_args()
    
    # Override config with CLI args
    if args.tier:
        CONFIG.setdefault("models", {})["default_tier"] = args.tier
    if args.idle_timeout is not None:
        CONFIG.setdefault("memory", {})["idle_timeout"] = args.idle_timeout
    
    uvicorn.run(
        "app:app",
        host=args.host,
        port=args.port,
        log_level="info",
    )
