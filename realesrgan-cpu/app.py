"""
Real-ESRGAN CPU Upscaler Standalone FastAPI Server

A CPU-only image & video upscaler using Real-ESRGAN inference on PyTorch CPU.
Designed as a fallback when no GPU is available, or when the GPU is in high
demand from other services.

For video upscaling, frames are extracted with FFmpeg, processed individually
through Real-ESRGAN, and reassembled into the output video with FFmpeg.

Features:
  - Image upscaling (2x or 4x) via Real-ESRGAN models
  - Video upscaling with frame-by-frame processing + FFmpeg reassembly
  - Multiple model options (general, anime, video-optimized)
  - Tiled processing for memory efficiency on CPU
  - Progress tracking for video jobs
  - Cross-service URL resolution (fetch files from sibling services)

API Endpoints:
  POST /upscale/image   - Upscale a single image
  POST /upscale/video   - Upscale a video file
  GET  /health          - Health check
  GET  /info            - Server info (model, capabilities)
  GET  /output/{f}      - Retrieve upscaled files

CPU-only — no GPU required.  Runs on port 8009.
Based on: https://github.com/xinntao/Real-ESRGAN
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
logger = logging.getLogger("realesrgan-cpu")

# =============================================================================
# Configuration
# =============================================================================

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
OUTPUT_DIR = SCRIPT_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_DIR = SCRIPT_DIR.parent  # Parent of realesrgan-cpu/

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
}


def _resolve_local_url(url: str) -> Optional[Path]:
    """If *url* points to a local service output file, return its filesystem path.

    Handles URLs like ``http://localhost:8006/output/LTX2_00018.mp4`` by mapping
    to ``<workspace>/ltx-video/output/LTX2_00018.mp4``.  Returns None when the URL
    is not local or the file doesn't exist on disk.
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
    path_part = parsed.path.lstrip("/")
    if not path_part:
        return None
    candidate = (WORKSPACE_DIR / service_dir / path_part).resolve()
    # Security: prevent path traversal via ../ segments (SA2-01)
    if not candidate.is_relative_to(WORKSPACE_DIR.resolve()):
        return None
    if candidate.is_file():
        return candidate
    return None


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}


_cfg = _load_config()
_server_cfg = _cfg.get("server", {})
_model_cfg = _cfg.get("model", {})
_defaults_cfg = _cfg.get("defaults", {})
_limits_cfg = _cfg.get("limits", {})
_output_cfg = _cfg.get("output", {})

OUTPUT_PREFIX = _output_cfg.get("prefix", "ESRGAN_")

# Model settings
MODEL_NAME = _model_cfg.get("name", "realesr-animevideov3")
MODEL_SCALE = _model_cfg.get("scale", 2)

# Processing defaults
DEFAULT_TILE = _defaults_cfg.get("tile", 4)
DEFAULT_TILE_PAD = _defaults_cfg.get("tile_pad", 10)
DEFAULT_PRE_PAD = _defaults_cfg.get("pre_pad", 0)
VIDEO_BATCH_SIZE = _defaults_cfg.get("video_batch_size", 1)
OUTPUT_FPS = _defaults_cfg.get("output_fps", None)
JPEG_QUALITY = _defaults_cfg.get("jpeg_quality", 95)
IMAGE_FORMAT = _defaults_cfg.get("image_format", "png")

# Limits
MAX_INPUT_SIZE_MB = _limits_cfg.get("max_input_size_mb", 500)
MAX_VIDEO_DURATION = _limits_cfg.get("max_video_duration", 120)
PROCESSING_TIMEOUT = _limits_cfg.get("processing_timeout", 3600)
MAX_OUTPUT_PIXELS = _limits_cfg.get("max_output_pixels", 0)

# FFmpeg binary paths
FFMPEG_PATH = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = shutil.which("ffprobe") or "ffprobe"


# =============================================================================
# Real-ESRGAN Model Manager
# =============================================================================

class ModelManager:
    """Manages the Real-ESRGAN model for CPU inference."""

    def __init__(self):
        self._upsampler = None
        self._model_name = MODEL_NAME
        self._scale = MODEL_SCALE
        self._lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._upsampler is not None

    def load(self, model_name: Optional[str] = None, scale: Optional[int] = None):
        """Load the Real-ESRGAN model for CPU inference."""
        with self._lock:
            if model_name:
                self._model_name = model_name
            if scale:
                self._scale = scale

            if self._upsampler is not None:
                logger.info("Model already loaded, unloading first...")
                self._unload_internal()

            logger.info(f"Loading Real-ESRGAN model: {self._model_name} (scale={self._scale}) on CPU...")
            start = time.time()

            try:
                import torch
                from basicsr.archs.rrdbnet_arch import RRDBNet
                from realesrgan import RealESRGANer

                # Select architecture based on model name
                if self._model_name == "realesrgan-x4plus":
                    model = RRDBNet(
                        num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4,
                    )
                    netscale = 4
                elif self._model_name == "realesrnet-x4plus":
                    model = RRDBNet(
                        num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=23, num_grow_ch=32, scale=4,
                    )
                    netscale = 4
                elif self._model_name == "realesr-animevideov3":
                    from basicsr.archs.rrdbnet_arch import RRDBNet
                    model = RRDBNet(
                        num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=6, num_grow_ch=32, scale=4,
                    )
                    netscale = 4
                elif self._model_name == "realesrgan-x4plus-anime":
                    model = RRDBNet(
                        num_in_ch=3, num_out_ch=3, num_feat=64,
                        num_block=6, num_grow_ch=32, scale=4,
                    )
                    netscale = 4
                else:
                    raise ValueError(f"Unknown model: {self._model_name}")

                # Models are auto-downloaded by RealESRGANer
                self._upsampler = RealESRGANer(
                    scale=netscale,
                    model_path=None,  # Auto-download
                    model=model,
                    tile=DEFAULT_TILE,
                    tile_pad=DEFAULT_TILE_PAD,
                    pre_pad=DEFAULT_PRE_PAD,
                    half=False,  # CPU doesn't support half precision well
                    device="cpu",
                )

                elapsed = time.time() - start
                logger.info(f"Model loaded in {elapsed:.1f}s")

            except Exception as e:
                logger.error(f"Failed to load model: {e}", exc_info=True)
                self._upsampler = None
                raise

    def _unload_internal(self):
        """Unload model (caller must hold lock)."""
        if self._upsampler is not None:
            del self._upsampler
            self._upsampler = None
            gc.collect()
            logger.info("Model unloaded")

    def unload(self):
        """Unload model from memory."""
        with self._lock:
            self._unload_internal()

    def upscale_image(self, img_path: str, output_path: str, outscale: Optional[int] = None):
        """Upscale a single image file.

        Args:
            img_path: Path to input image
            output_path: Path to save upscaled image
            outscale: Output scale factor (default: model scale)
        """
        import cv2

        if not self.is_loaded:
            self.load()

        outscale = outscale or self._scale

        img = cv2.imread(img_path, cv2.IMREAD_UNCHANGED)
        if img is None:
            raise ValueError(f"Failed to read image: {img_path}")

        logger.info(
            f"Upscaling image {img_path} "
            f"({img.shape[1]}x{img.shape[0]}) "
            f"→ {outscale}x on CPU..."
        )

        start = time.time()
        output, _ = self._upsampler.enhance(img, outscale=outscale)
        elapsed = time.time() - start

        logger.info(
            f"Upscaled to {output.shape[1]}x{output.shape[0]} in {elapsed:.1f}s"
        )

        cv2.imwrite(output_path, output)
        return {
            "input_size": f"{img.shape[1]}x{img.shape[0]}",
            "output_size": f"{output.shape[1]}x{output.shape[0]}",
            "scale": outscale,
            "processing_time_seconds": round(elapsed, 2),
        }


# Global model manager
_model_manager = ModelManager()


# =============================================================================
# Helper: Fetch file from URL or local path
# =============================================================================

async def _fetch_file(url: str, suffix: str = "") -> Path:
    """Download a file from URL or resolve from local service.

    Returns path to a temporary or local file.
    """
    import httpx

    # Try local resolution first
    local_path = _resolve_local_url(url)
    if local_path:
        logger.info(f"Resolved local file: {local_path}")
        return local_path

    # Validate URL for SSRF before downloading
    if not validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")

    # Download from URL
    logger.info(f"Downloading file from {url}...")
    # SA3-H1: follow_redirects=False to prevent SSRF via redirect chains
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    if not suffix:
        # Guess suffix from URL or content-type
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path_part = parsed.path
        if "." in path_part.split("/")[-1]:
            suffix = "." + path_part.split(".")[-1]
        else:
            ct = resp.headers.get("content-type", "")
            if "png" in ct:
                suffix = ".png"
            elif "jpeg" in ct or "jpg" in ct:
                suffix = ".jpg"
            elif "mp4" in ct:
                suffix = ".mp4"
            elif "webm" in ct:
                suffix = ".webm"
            else:
                suffix = ".bin"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(OUTPUT_DIR))
    tmp.write(resp.content)
    tmp.close()
    return Path(tmp.name)


# =============================================================================
# Video Processing
# =============================================================================

def _get_video_info(video_path: str) -> Dict[str, Any]:
    """Get video metadata using ffprobe."""
    cmd = [
        FFPROBE_PATH,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {result.stderr}")

    import json
    info = json.loads(result.stdout)

    # Extract key fields
    video_stream = None
    audio_stream = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video" and video_stream is None:
            video_stream = s
        elif s.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = s

    duration = float(info.get("format", {}).get("duration", 0))
    fps = 24.0
    if video_stream:
        # Parse fps from r_frame_rate (e.g., "24000/1001")
        r_fps = video_stream.get("r_frame_rate", "24/1")
        if "/" in r_fps:
            num, den = r_fps.split("/")
            fps = float(num) / float(den) if float(den) else 24.0
        else:
            fps = float(r_fps)

    return {
        "duration": duration,
        "fps": fps,
        "width": int(video_stream.get("width", 0)) if video_stream else 0,
        "height": int(video_stream.get("height", 0)) if video_stream else 0,
        "has_audio": audio_stream is not None,
        "codec": video_stream.get("codec_name", "") if video_stream else "",
        "total_frames": int(duration * fps) if duration else 0,
    }


def _extract_frames(video_path: str, frames_dir: str, fps: Optional[float] = None) -> int:
    """Extract frames from video using ffmpeg.

    Returns the number of frames extracted.
    """
    cmd = [FFMPEG_PATH, "-i", str(video_path)]
    if fps:
        cmd.extend(["-vf", f"fps={fps}"])
    cmd.extend([
        "-qscale:v", "1",
        "-qmin", "1",
        "-qmax", "1",
        os.path.join(frames_dir, "frame_%08d.png"),
    ])

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=PROCESSING_TIMEOUT,
    )
    if result.returncode != 0:
        raise ValueError(f"Frame extraction failed: {result.stderr[:500]}")

    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    return len(frames)


def _extract_audio(video_path: str, audio_path: str) -> bool:
    """Extract audio track from video.  Returns True if audio was found."""
    cmd = [
        FFMPEG_PATH, "-i", str(video_path),
        "-vn", "-acodec", "copy",
        str(audio_path),
        "-y",
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=120,
    )
    return result.returncode == 0 and Path(audio_path).exists()


def _reassemble_video(
    frames_dir: str,
    output_path: str,
    fps: float,
    audio_path: Optional[str] = None,
) -> None:
    """Reassemble upscaled frames into a video using ffmpeg."""
    cmd = [
        FFMPEG_PATH,
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "frame_%08d.png"),
    ]

    if audio_path and Path(audio_path).exists():
        cmd.extend(["-i", str(audio_path)])

    cmd.extend([
        "-c:v", "libx264",
        "-crf", "18",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
    ])

    if audio_path and Path(audio_path).exists():
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-shortest"])

    cmd.extend(["-y", str(output_path)])

    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=PROCESSING_TIMEOUT,
    )
    if result.returncode != 0:
        raise ValueError(f"Video reassembly failed: {result.stderr[:500]}")


def _upscale_video_sync(
    video_path: str,
    output_path: str,
    scale: int,
    tile: int,
    progress_callback=None,
) -> Dict[str, Any]:
    """Upscale a video file synchronously (CPU, frame-by-frame).

    1. Get video info
    2. Extract frames with ffmpeg
    3. Upscale each frame with Real-ESRGAN
    4. Extract audio from original
    5. Reassemble upscaled frames + audio into output video
    """
    import cv2

    if not _model_manager.is_loaded:
        _model_manager.load()

    video_info = _get_video_info(video_path)
    fps = video_info["fps"]

    if MAX_VIDEO_DURATION > 0 and video_info["duration"] > MAX_VIDEO_DURATION:
        raise ValueError(
            f"Video duration ({video_info['duration']:.1f}s) exceeds maximum "
            f"({MAX_VIDEO_DURATION}s). CPU upscaling is slow — use shorter clips."
        )

    logger.info(
        f"Upscaling video: {video_info['width']}x{video_info['height']} "
        f"@ {fps:.1f} fps, {video_info['duration']:.1f}s, "
        f"~{video_info['total_frames']} frames → {scale}x on CPU"
    )

    start_time = time.time()

    with tempfile.TemporaryDirectory(prefix="esrgan_frames_") as tmpdir:
        input_frames_dir = os.path.join(tmpdir, "input")
        output_frames_dir = os.path.join(tmpdir, "output")
        os.makedirs(input_frames_dir)
        os.makedirs(output_frames_dir)

        # Step 1: Extract frames
        logger.info("Extracting frames...")
        frame_count = _extract_frames(video_path, input_frames_dir)
        if frame_count == 0:
            raise ValueError("No frames extracted from video")
        logger.info(f"Extracted {frame_count} frames")

        # Step 2: Extract audio
        audio_path = os.path.join(tmpdir, "audio.aac")
        has_audio = _extract_audio(video_path, audio_path)
        if has_audio:
            logger.info("Audio track extracted")

        # Step 3: Upscale each frame
        input_frames = sorted(Path(input_frames_dir).glob("frame_*.png"))
        for i, frame_path in enumerate(input_frames):
            frame_num = i + 1
            pct = int(frame_num / frame_count * 100)

            if frame_num == 1 or frame_num % 10 == 0 or frame_num == frame_count:
                elapsed = time.time() - start_time
                if frame_num > 1:
                    per_frame = elapsed / frame_num
                    remaining = per_frame * (frame_count - frame_num)
                    logger.info(
                        f"Frame {frame_num}/{frame_count} ({pct}%) — "
                        f"{per_frame:.1f}s/frame, ~{remaining:.0f}s remaining"
                    )
                else:
                    logger.info(f"Frame {frame_num}/{frame_count} ({pct}%)")

            if progress_callback:
                progress_callback(frame_num, frame_count)

            # Read frame
            img = cv2.imread(str(frame_path), cv2.IMREAD_UNCHANGED)
            if img is None:
                logger.warning(f"Failed to read frame {frame_path}, skipping")
                # Copy original frame as fallback
                shutil.copy2(str(frame_path), os.path.join(output_frames_dir, frame_path.name))
                continue

            # Upscale
            output_img, _ = _model_manager._upsampler.enhance(img, outscale=scale)

            # Save upscaled frame
            out_path = os.path.join(output_frames_dir, frame_path.name)
            cv2.imwrite(out_path, output_img)

        # Step 4: Reassemble video
        logger.info("Reassembling upscaled video...")
        output_fps = OUTPUT_FPS if OUTPUT_FPS else fps
        _reassemble_video(
            output_frames_dir,
            output_path,
            output_fps,
            audio_path if has_audio else None,
        )

    elapsed = time.time() - start_time
    per_frame = elapsed / frame_count if frame_count else 0

    # Get output info
    output_info = _get_video_info(output_path)

    result = {
        "input_size": f"{video_info['width']}x{video_info['height']}",
        "output_size": f"{output_info['width']}x{output_info['height']}",
        "scale": scale,
        "input_duration": round(video_info["duration"], 2),
        "output_duration": round(output_info["duration"], 2),
        "fps": round(fps, 2),
        "total_frames": frame_count,
        "has_audio": has_audio,
        "processing_time_seconds": round(elapsed, 2),
        "seconds_per_frame": round(per_frame, 2),
    }

    logger.info(
        f"Video upscaled: {result['input_size']} → {result['output_size']} "
        f"in {elapsed:.1f}s ({per_frame:.1f}s/frame)"
    )

    return result


# =============================================================================
# FastAPI App
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server lifespan — pre-load model on startup."""
    logger.info("Real-ESRGAN CPU Upscaler starting...")
    try:
        _model_manager.load()
        logger.info("Model pre-loaded and ready for inference")
    except Exception as e:
        logger.warning(f"Model pre-load failed (will retry on first request): {e}")
    yield
    logger.info("Shutting down Real-ESRGAN CPU Upscaler...")
    _model_manager.unload()


app = FastAPI(
    title="Real-ESRGAN CPU Upscaler",
    description="CPU-only image & video upscaling using Real-ESRGAN",
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
# Pydantic Models
# =============================================================================

class UpscaleImageRequest(BaseModel):
    image_url: Optional[str] = Field(None, description="URL of image to upscale")
    scale: Optional[int] = Field(None, description="Upscale factor (2 or 4)")
    tile: Optional[int] = Field(None, description="Tile size for processing (0=no tiling)")


class UpscaleVideoRequest(BaseModel):
    video_url: Optional[str] = Field(None, description="URL of video to upscale")
    scale: Optional[int] = Field(None, description="Upscale factor (2 or 4)")
    tile: Optional[int] = Field(None, description="Tile size for processing (0=no tiling)")


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool


class InfoResponse(BaseModel):
    server: str
    version: str
    model: str
    scale: int
    model_loaded: bool
    device: str
    ffmpeg_available: bool
    supported_models: List[str]
    capabilities: Dict[str, bool]


# =============================================================================
# Endpoints
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        model_loaded=_model_manager.is_loaded,
    )


@app.get("/info", response_model=InfoResponse)
async def info():
    """Server info endpoint."""
    ffmpeg_ok = shutil.which("ffmpeg") is not None

    return InfoResponse(
        server="Real-ESRGAN CPU Upscaler",
        version="1.0.0",
        model=_model_manager._model_name,
        scale=_model_manager._scale,
        model_loaded=_model_manager.is_loaded,
        device="cpu",
        ffmpeg_available=ffmpeg_ok,
        supported_models=[
            "realesrgan-x4plus",
            "realesrnet-x4plus",
            "realesr-animevideov3",
            "realesrgan-x4plus-anime",
        ],
        capabilities={
            "image_upscale": True,
            "video_upscale": ffmpeg_ok,
            "cpu_only": True,
        },
    )


@app.post("/upscale/image")
async def upscale_image(
    file: Optional[UploadFile] = File(None),
    image_url: Optional[str] = Form(None),
    scale: Optional[int] = Form(None),
    tile: Optional[int] = Form(None),
    model_name: Optional[str] = Form(None),
):
    """Upscale a single image.

    Provide either a file upload or image_url.
    Optionally specify model_name to switch models on-the-fly.
    """
    import cv2

    scale = scale or MODEL_SCALE
    if scale not in (2, 4):
        raise HTTPException(400, "Scale must be 2 or 4")

    # Hot-swap model if a different one is requested
    if model_name and model_name != _model_manager._model_name:
        valid_models = [
            "realesrgan-x4plus", "realesrnet-x4plus",
            "realesr-animevideov3", "realesrgan-x4plus-anime",
        ]
        if model_name not in valid_models:
            raise HTTPException(400, f"Unknown model: {model_name}. Valid: {valid_models}")
        logger.info(f"Switching model: {_model_manager._model_name} -> {model_name}")
        _model_manager.load(model_name=model_name, scale=scale)

    # Get input file
    input_path = None
    tmp_input = None

    try:
        if file and file.filename:
            # File upload
            content = await file.read()
            if len(content) > MAX_INPUT_SIZE_MB * 1024 * 1024:
                raise HTTPException(413, f"File exceeds {MAX_INPUT_SIZE_MB}MB limit")
            suffix = Path(file.filename).suffix or ".png"
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=str(OUTPUT_DIR)
            )
            tmp.write(content)
            tmp.close()
            input_path = tmp.name
            tmp_input = tmp.name
        elif image_url:
            fetched = await _fetch_file(image_url)
            input_path = str(fetched)
            if not _resolve_local_url(image_url):
                tmp_input = input_path  # Clean up downloaded file
        else:
            raise HTTPException(400, "Provide either file upload or image_url")

        # Set tile if specified
        if tile is not None and _model_manager._upsampler:
            _model_manager._upsampler.tile_size = tile

        # Generate output filename
        output_name = f"{OUTPUT_PREFIX}{secrets.token_hex(4)}.{IMAGE_FORMAT}"
        output_path = str(OUTPUT_DIR / output_name)

        # Run upscaling in thread pool (CPU-bound)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _model_manager.upscale_image,
            input_path,
            output_path,
            scale,
        )

        output_url = f"/output/{output_name}"
        return {
            "output_file": output_url,
            "output_filename": output_name,
            **result,
            "model": _model_manager._model_name,
            "device": "cpu",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image upscaling failed: {e}", exc_info=True)
        raise HTTPException(500, "Upscaling failed. Check server logs for details.")
    finally:
        if tmp_input and Path(tmp_input).exists():
            try:
                os.unlink(tmp_input)
            except Exception:
                pass


@app.post("/upscale/video")
async def upscale_video(
    file: Optional[UploadFile] = File(None),
    video_url: Optional[str] = Form(None),
    scale: Optional[int] = Form(None),
    tile: Optional[int] = Form(None),
    model_name: Optional[str] = Form(None),
):
    """Upscale a video file (frame-by-frame on CPU).

    WARNING: CPU video upscaling is SLOW. A 10-second video at 24fps =
    240 frames, each taking 2-10+ seconds on CPU. Use for short clips only.

    Provide either a file upload or video_url.
    Optionally specify model_name to switch models on-the-fly.
    """
    if not shutil.which("ffmpeg"):
        raise HTTPException(
            500, "FFmpeg not found — required for video upscaling"
        )

    scale = scale or MODEL_SCALE
    if scale not in (2, 4):
        raise HTTPException(400, "Scale must be 2 or 4")

    # Hot-swap model if a different one is requested
    if model_name and model_name != _model_manager._model_name:
        valid_models = [
            "realesrgan-x4plus", "realesrnet-x4plus",
            "realesr-animevideov3", "realesrgan-x4plus-anime",
        ]
        if model_name not in valid_models:
            raise HTTPException(400, f"Unknown model: {model_name}. Valid: {valid_models}")
        logger.info(f"Switching model: {_model_manager._model_name} -> {model_name}")
        _model_manager.load(model_name=model_name, scale=scale)

    # Get input file
    input_path = None
    tmp_input = None

    try:
        if file and file.filename:
            content = await file.read()
            if len(content) > MAX_INPUT_SIZE_MB * 1024 * 1024:
                raise HTTPException(413, f"File exceeds {MAX_INPUT_SIZE_MB}MB limit")
            suffix = Path(file.filename).suffix or ".mp4"
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=str(OUTPUT_DIR)
            )
            tmp.write(content)
            tmp.close()
            input_path = tmp.name
            tmp_input = tmp.name
        elif video_url:
            fetched = await _fetch_file(video_url, suffix=".mp4")
            input_path = str(fetched)
            if not _resolve_local_url(video_url):
                tmp_input = input_path
        else:
            raise HTTPException(400, "Provide either file upload or video_url")

        # Set tile if specified
        if tile is not None and _model_manager._upsampler:
            _model_manager._upsampler.tile_size = tile

        # Generate output filename
        output_name = f"{OUTPUT_PREFIX}{secrets.token_hex(4)}.mp4"
        output_path = str(OUTPUT_DIR / output_name)

        # Run upscaling in thread pool (CPU-bound, very slow)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _upscale_video_sync,
            input_path,
            output_path,
            scale,
            tile or DEFAULT_TILE,
        )

        output_url = f"/output/{output_name}"
        return {
            "output_file": output_url,
            "output_filename": output_name,
            **result,
            "model": _model_manager._model_name,
            "device": "cpu",
            "note": (
                "CPU video upscaling is complete. "
                f"Processed {result['total_frames']} frames "
                f"at {result['seconds_per_frame']:.1f}s/frame."
            ),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Video upscaling failed: {e}", exc_info=True)
        raise HTTPException(500, "Video upscaling failed. Check server logs for details.")
    finally:
        if tmp_input and Path(tmp_input).exists():
            try:
                os.unlink(tmp_input)
            except Exception:
                pass


@app.get("/output/{filename}")
async def get_output(filename: str):
    """Retrieve an upscaled output file."""
    # Path traversal protection
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    filepath = OUTPUT_DIR / safe_name
    # Defence-in-depth: verify resolved path stays within OUTPUT_DIR
    if not filepath.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(400, "Invalid filename")
    if not filepath.exists():
        raise HTTPException(404, f"File not found: {filename}")

    media_type = "application/octet-stream"
    suffix = filepath.suffix.lower()
    _MEDIA_TYPES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }
    media_type = _MEDIA_TYPES.get(suffix, media_type)

    return FileResponse(str(filepath), media_type=media_type, filename=safe_name)


# =============================================================================
# Main
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = _server_cfg.get("host", "0.0.0.0")
    port = _server_cfg.get("port", 8009)
    uvicorn.run(app, host=host, port=port)
