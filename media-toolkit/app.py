"""
Media Toolkit — FFmpeg-based media composition server.

Provides operations for splitting, combining, mixing, and assembling
media files produced by other GPU tools (LTX-Video, Z-Image, ACEStep,
Qwen3-TTS, AudioSR, SeedVR2).

CPU-only — no GPU required.  Runs on port 8008.
"""

import asyncio
import gc
import hashlib
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urlparse, parse_qs

import ffmpeg
import httpx
import yaml
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.gpu_service_security import validate_url, add_security_middleware

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("media-toolkit")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).parent / "config.yaml"

def _load_config() -> dict:
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}
    return {}

_cfg = _load_config()
_server_cfg = _cfg.get("server", {})
_ffmpeg_cfg = _cfg.get("ffmpeg", {})
_limits_cfg = _cfg.get("limits", {})
_output_cfg = _cfg.get("output", {})
_slideshow_cfg = _cfg.get("slideshow", {})

OUTPUT_DIR = Path(__file__).parent / _output_cfg.get("directory", "output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_PREFIX = _output_cfg.get("prefix", "MT_")

# FFmpeg binary paths
FFMPEG_PATH = _ffmpeg_cfg.get("path") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_PATH = _ffmpeg_cfg.get("ffprobe_path") or shutil.which("ffprobe") or "ffprobe"

# Limits
MAX_INPUT_SIZE_MB = _limits_cfg.get("max_input_size_mb", 2048)
MAX_OUTPUT_DURATION = _limits_cfg.get("max_output_duration", 3600)
MAX_INPUT_FILES = _limits_cfg.get("max_input_files", 50)
MAX_AUDIO_TRACKS = _limits_cfg.get("max_audio_tracks", 20)
MAX_SLIDESHOW_IMAGES = _limits_cfg.get("max_slideshow_images", 100)
PROCESSING_TIMEOUT = _limits_cfg.get("processing_timeout", 600)

# Output defaults
VIDEO_CODEC = _output_cfg.get("video_codec", "libx264")
VIDEO_CRF = _output_cfg.get("video_crf", 23)
VIDEO_PRESET = _output_cfg.get("video_preset", "medium")
AUDIO_CODEC = _output_cfg.get("audio_codec", "aac")
AUDIO_BITRATE = _output_cfg.get("audio_bitrate", "192k")

# ---------------------------------------------------------------------------
# Cross-service local file resolution  (same pattern as AudioSR / SeedVR2)
# ---------------------------------------------------------------------------

_LOCAL_SERVICE_DIRS: Dict[int, str] = {
    8001: "acestep",
    8002: "qwen3-tts",
    8003: "z-image",
    8004: "seedvr2-upscaler",
    8005: "canary-stt",
    8006: "ltx-video",
    8007: "audiosr",
    8008: "media-toolkit",
}


def _resolve_local_url(url: str) -> Optional[Path]:
    """If *url* points at a sibling service's /output/, return the local Path."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if host not in ("localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal"):
            return None
        port = parsed.port
        if port is None:
            return None

        project_root = Path(__file__).parent.parent

        # ACEStep uses /v1/audio?file=<path>
        if port == 8001 and parsed.path == "/v1/audio":
            qs = parse_qs(parsed.query)
            file_vals = qs.get("file", [])
            if file_vals:
                acestep_output = (project_root / "acestep" / "output").resolve()
                candidate = Path(file_vals[0]).resolve()
                # Security: restrict to ACEStep output dir (SA2-02)
                if not candidate.is_relative_to(acestep_output):
                    return None
                if candidate.is_file():
                    return candidate
            return None

        if port not in _LOCAL_SERVICE_DIRS:
            return None
        service_dir = _LOCAL_SERVICE_DIRS[port]
        # e.g. /output/ZIMG_00001.png → /home/.../z-image/output/ZIMG_00001.png
        rel = parsed.path.lstrip("/")
        if not rel:
            return None
        candidate = (project_root / service_dir / rel).resolve()
        # Security: prevent path traversal via ../ segments (SA2-01)
        if not candidate.is_relative_to(project_root.resolve()):
            return None
        if candidate.is_file():
            return candidate
        return None
    except Exception:
        return None


async def _fetch_to_tempfile(url: str, suffix: str = "") -> Path:
    """Download *url* to a temporary file, resolving local URLs first."""
    local = _resolve_local_url(url)
    if local:
        return local  # No copy needed — read directly

    if not validate_url(url):
        raise HTTPException(status_code=400, detail="Invalid or blocked URL")

    # SA3-H1: follow_redirects=False to prevent SSRF via redirect chains
    async with httpx.AsyncClient(timeout=120, follow_redirects=False) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=str(OUTPUT_DIR))
    tmp.write(resp.content)
    tmp.close()
    return Path(tmp.name)


def _output_filename(ext: str) -> str:
    """Generate a unique output filename."""
    uid = uuid.uuid4().hex[:8]
    return f"{OUTPUT_PREFIX}{uid}{ext}"


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _run_ffmpeg(args: List[str], timeout: int = PROCESSING_TIMEOUT) -> subprocess.CompletedProcess:
    """Run an FFmpeg command and return the result."""
    cmd = [FFMPEG_PATH] + args
    logger.info(f"Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr[:2000]}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[:500]}")
    return result


def _run_ffprobe(args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run an FFprobe command and return the result."""
    cmd = [FFPROBE_PATH] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {result.stderr[:500]}")
    return result


def _probe_file(path: str) -> dict:
    """Run ffprobe and return parsed JSON metadata."""
    import json
    result = _run_ffprobe([
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ])
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class AudioTrack(BaseModel):
    url: str = Field(..., description="URL of audio file")
    volume: float = Field(1.0, ge=0.0, le=5.0, description="Volume multiplier")
    start_time: float = Field(0.0, ge=0.0, description="Start offset in seconds")
    fade_in: float = Field(0.0, ge=0.0, description="Fade-in duration in seconds")
    fade_out: float = Field(0.0, ge=0.0, description="Fade-out duration in seconds")
    loop: bool = Field(False, description="Loop audio to fill duration")

class ImageEntry(BaseModel):
    url: str = Field(..., description="URL of image file")
    duration: float = Field(5.0, gt=0, le=60.0, description="Display duration in seconds")
    effect: str = Field("none", description="Ken Burns effect: none, zoom_in, zoom_out, pan_left, pan_right, ken_burns")

class OperationRequest(BaseModel):
    operation: str = Field(..., description="Operation to perform")

    # Common
    url: Optional[str] = Field(None, description="URL of media file (probe/trim/adjust_volume)")
    video_url: Optional[str] = Field(None, description="URL of video file")
    audio_url: Optional[str] = Field(None, description="URL of audio file")
    format: Optional[str] = Field(None, description="Output format: wav, mp3, mp4")
    sample_rate: Optional[int] = Field(None, description="Output sample rate")

    # combine
    audio_tracks: Optional[List[AudioTrack]] = Field(None, description="Audio tracks for combine/mix")
    replace_audio: bool = Field(True, description="Replace existing audio in video")

    # mix_audio / adjust_volume
    tracks: Optional[List[AudioTrack]] = Field(None, description="Audio tracks for mix_audio")
    volume: Optional[float] = Field(None, ge=0.0, le=5.0, description="Volume multiplier")
    normalize: bool = Field(False, description="Normalize audio loudness")
    duration: Optional[float] = Field(None, description="Output duration in seconds")
    output_format: Optional[str] = Field(None, description="Output format for mix_audio")

    # trim
    start_time: Optional[float] = Field(None, ge=0.0, description="Trim start time")
    end_time: Optional[float] = Field(None, description="Trim end time")

    # slideshow
    images: Optional[List[ImageEntry]] = Field(None, description="Images for slideshow")
    fps: int = Field(24, ge=1, le=60, description="Output FPS")
    transition: str = Field("none", description="Transition: none, crossfade")
    transition_duration: float = Field(0.5, ge=0.0, le=5.0, description="Transition duration")

    # concat
    files: Optional[List[str]] = Field(None, description="Files to concatenate")


# ---------------------------------------------------------------------------
# Operation implementations
# ---------------------------------------------------------------------------

async def _op_probe(req: OperationRequest) -> dict:
    """Inspect media file metadata via ffprobe."""
    url = req.url or req.video_url or req.audio_url
    if not url:
        raise HTTPException(400, "probe requires 'url', 'video_url', or 'audio_url'")

    path = await _fetch_to_tempfile(url)
    is_temp = (path != _resolve_local_url(url))

    try:
        info = _probe_file(str(path))
        fmt = info.get("format", {})

        streams_info = []
        for s in info.get("streams", []):
            si = {
                "type": s.get("codec_type"),
                "codec": s.get("codec_name"),
            }
            if s.get("codec_type") == "video":
                si["width"] = s.get("width")
                si["height"] = s.get("height")
                fps_str = s.get("r_frame_rate", "0/1")
                try:
                    num, den = fps_str.split("/")
                    si["fps"] = round(int(num) / int(den), 2) if int(den) else 0
                except (ValueError, ZeroDivisionError):
                    si["fps"] = 0
            elif s.get("codec_type") == "audio":
                si["sample_rate"] = int(s.get("sample_rate", 0))
                si["channels"] = s.get("channels")
                si["channel_layout"] = s.get("channel_layout")
            streams_info.append(si)

        return {
            "duration_seconds": float(fmt.get("duration", 0)),
            "size_bytes": int(fmt.get("size", 0)),
            "format_name": fmt.get("format_name"),
            "bitrate": int(fmt.get("bit_rate", 0)),
            "streams": streams_info,
            "has_video": any(s["type"] == "video" for s in streams_info),
            "has_audio": any(s["type"] == "audio" for s in streams_info),
        }
    finally:
        if is_temp and path.exists():
            path.unlink(missing_ok=True)


async def _op_extract_audio(req: OperationRequest) -> dict:
    """Extract audio track from a video file."""
    if not req.video_url:
        raise HTTPException(400, "extract_audio requires 'video_url'")

    video_path = await _fetch_to_tempfile(req.video_url)
    is_temp = (video_path != _resolve_local_url(req.video_url))

    fmt = req.format or "wav"
    if fmt not in ("wav", "mp3", "flac", "aac", "ogg"):
        fmt = "wav"

    out_name = _output_filename(f".{fmt}")
    out_path = OUTPUT_DIR / out_name

    try:
        args = ["-y", "-i", str(video_path), "-vn"]
        if req.sample_rate:
            args += ["-ar", str(req.sample_rate)]
        if fmt == "wav":
            args += ["-acodec", "pcm_s16le"]
        elif fmt == "mp3":
            args += ["-acodec", "libmp3lame", "-b:a", "192k"]
        elif fmt == "aac":
            args += ["-acodec", "aac", "-b:a", AUDIO_BITRATE]
        args.append(str(out_path))

        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        # Probe output
        info = _probe_file(str(out_path))
        audio_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
        out_sr = int(audio_streams[0].get("sample_rate", 0)) if audio_streams else 0

        return {
            "output_file": f"/output/{out_name}",
            "format": fmt,
            "sample_rate": out_sr,
            "duration_seconds": float(info.get("format", {}).get("duration", 0)),
        }
    finally:
        if is_temp and video_path.exists():
            video_path.unlink(missing_ok=True)


async def _op_strip_audio(req: OperationRequest) -> dict:
    """Remove audio track from a video, keeping only video."""
    if not req.video_url:
        raise HTTPException(400, "strip_audio requires 'video_url'")

    video_path = await _fetch_to_tempfile(req.video_url)
    is_temp = (video_path != _resolve_local_url(req.video_url))

    out_name = _output_filename(".mp4")
    out_path = OUTPUT_DIR / out_name

    try:
        args = ["-y", "-i", str(video_path), "-an", "-c:v", "copy", str(out_path)]
        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        info = _probe_file(str(out_path))
        video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
        w = video_streams[0].get("width", 0) if video_streams else 0
        h = video_streams[0].get("height", 0) if video_streams else 0

        return {
            "output_file": f"/output/{out_name}",
            "duration_seconds": float(info.get("format", {}).get("duration", 0)),
            "resolution": f"{w}x{h}",
            "has_audio": False,
        }
    finally:
        if is_temp and video_path.exists():
            video_path.unlink(missing_ok=True)


async def _op_combine(req: OperationRequest) -> dict:
    """Combine a video with one or more audio tracks."""
    if not req.video_url:
        raise HTTPException(400, "combine requires 'video_url'")
    if not req.audio_tracks or len(req.audio_tracks) == 0:
        raise HTTPException(400, "combine requires at least one entry in 'audio_tracks'")
    if len(req.audio_tracks) > MAX_AUDIO_TRACKS:
        raise HTTPException(400, f"Maximum {MAX_AUDIO_TRACKS} audio tracks")

    video_path = await _fetch_to_tempfile(req.video_url)
    audio_paths = []
    temp_files = []

    try:
        for track in req.audio_tracks:
            ap = await _fetch_to_tempfile(track.url)
            audio_paths.append((ap, track))
            if ap != _resolve_local_url(track.url):
                temp_files.append(ap)

        if _resolve_local_url(req.video_url) is None:
            temp_files.append(video_path)

        # Get video duration for looping
        video_info = _probe_file(str(video_path))
        video_duration = float(video_info.get("format", {}).get("duration", 0))

        out_name = _output_filename(".mp4")
        out_path = OUTPUT_DIR / out_name

        # Build FFmpeg complex filter for mixing audio tracks
        inputs = ["-y", "-i", str(video_path)]
        for ap, _ in audio_paths:
            inputs.append("-i")
            inputs.append(str(ap))

        filter_parts = []
        n_audio = len(audio_paths)

        for i, (ap, track) in enumerate(audio_paths):
            stream_idx = i + 1  # 0 is video
            label = f"a{i}"

            filters = []

            # Loop if requested
            if track.loop:
                filters.append(f"aloop=loop=-1:size=2e+09")
                # Trim to video duration after looping
                filters.append(f"atrim=0:{video_duration}")
                filters.append("asetpts=PTS-STARTPTS")

            # Volume
            if track.volume != 1.0:
                filters.append(f"volume={track.volume}")

            # Start time offset
            if track.start_time > 0:
                filters.append(f"adelay={int(track.start_time * 1000)}|{int(track.start_time * 1000)}")

            # Fade
            if track.fade_in > 0:
                filters.append(f"afade=t=in:d={track.fade_in}")
            if track.fade_out > 0:
                filters.append(f"afade=t=out:st={max(0, video_duration - track.fade_out)}:d={track.fade_out}")

            if filters:
                filter_chain = ",".join(filters)
                filter_parts.append(f"[{stream_idx}:a]{filter_chain}[{label}]")
            else:
                filter_parts.append(f"[{stream_idx}:a]acopy[{label}]")

        # Mix all audio tracks together
        mix_inputs = "".join(f"[a{i}]" for i in range(n_audio))
        if n_audio > 1:
            filter_parts.append(f"{mix_inputs}amix=inputs={n_audio}:duration=longest:normalize=0[aout]")
        else:
            filter_parts.append(f"[a0]acopy[aout]")

        filter_graph = ";".join(filter_parts)

        args = inputs + [
            "-filter_complex", filter_graph,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", AUDIO_CODEC,
            "-b:a", AUDIO_BITRATE,
            "-shortest",
            str(out_path),
        ]

        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "duration_seconds": float(info.get("format", {}).get("duration", 0)),
            "audio_tracks_mixed": n_audio,
        }
    finally:
        for tf in temp_files:
            if tf.exists():
                tf.unlink(missing_ok=True)


async def _op_mix_audio(req: OperationRequest) -> dict:
    """Mix multiple audio tracks together with volume/timing/fade control."""
    tracks = req.tracks or req.audio_tracks
    if not tracks or len(tracks) == 0:
        raise HTTPException(400, "mix_audio requires 'tracks' (array of audio tracks)")
    if len(tracks) > MAX_AUDIO_TRACKS:
        raise HTTPException(400, f"Maximum {MAX_AUDIO_TRACKS} audio tracks")

    audio_paths = []
    temp_files = []

    try:
        for track in tracks:
            ap = await _fetch_to_tempfile(track.url)
            audio_paths.append((ap, track))
            if ap != _resolve_local_url(track.url):
                temp_files.append(ap)

        fmt = req.output_format or req.format or "wav"
        if fmt not in ("wav", "mp3", "flac", "aac"):
            fmt = "wav"

        out_name = _output_filename(f".{fmt}")
        out_path = OUTPUT_DIR / out_name

        inputs = ["-y"]
        for ap, _ in audio_paths:
            inputs.append("-i")
            inputs.append(str(ap))

        filter_parts = []
        n_audio = len(tracks)

        for i, (ap, track) in enumerate(audio_paths):
            label = f"a{i}"
            filters = []

            if track.loop and req.duration:
                filters.append(f"aloop=loop=-1:size=2e+09")
                filters.append(f"atrim=0:{req.duration}")
                filters.append("asetpts=PTS-STARTPTS")

            if track.volume != 1.0:
                filters.append(f"volume={track.volume}")

            if track.start_time > 0:
                filters.append(f"adelay={int(track.start_time * 1000)}|{int(track.start_time * 1000)}")

            if track.fade_in > 0:
                filters.append(f"afade=t=in:d={track.fade_in}")
            if track.fade_out > 0 and req.duration:
                filters.append(f"afade=t=out:st={max(0, req.duration - track.fade_out)}:d={track.fade_out}")

            if filters:
                filter_chain = ",".join(filters)
                filter_parts.append(f"[{i}:a]{filter_chain}[{label}]")
            else:
                filter_parts.append(f"[{i}:a]acopy[{label}]")

        mix_inputs = "".join(f"[a{i}]" for i in range(n_audio))
        if n_audio > 1:
            duration_mode = "first" if not req.duration else "longest"
            filter_parts.append(f"{mix_inputs}amix=inputs={n_audio}:duration={duration_mode}:normalize=0[aout]")
        else:
            filter_parts.append(f"[a0]acopy[aout]")

        # Optional trim to exact duration
        if req.duration:
            filter_parts.append(f"[aout]atrim=0:{req.duration}[afinal]")
            map_label = "[afinal]"
        else:
            map_label = "[aout]"

        filter_graph = ";".join(filter_parts)

        args = inputs + ["-filter_complex", filter_graph, "-map", map_label]

        if fmt == "wav":
            args += ["-acodec", "pcm_s16le"]
        elif fmt == "mp3":
            args += ["-acodec", "libmp3lame", "-b:a", "192k"]
        elif fmt == "aac":
            args += ["-acodec", "aac", "-b:a", AUDIO_BITRATE]

        if req.sample_rate:
            args += ["-ar", str(req.sample_rate)]

        args.append(str(out_path))

        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "format": fmt,
            "duration_seconds": float(info.get("format", {}).get("duration", 0)),
            "tracks_mixed": n_audio,
        }
    finally:
        for tf in temp_files:
            if tf.exists():
                tf.unlink(missing_ok=True)


async def _op_adjust_volume(req: OperationRequest) -> dict:
    """Adjust volume or normalize a single audio/video file."""
    url = req.url or req.audio_url
    if not url:
        raise HTTPException(400, "adjust_volume requires 'url' or 'audio_url'")

    path = await _fetch_to_tempfile(url)
    is_temp = (path != _resolve_local_url(url))

    try:
        # Detect if input has video
        info = _probe_file(str(path))
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))

        fmt = req.format or ("mp4" if has_video else "wav")
        out_name = _output_filename(f".{fmt}")
        out_path = OUTPUT_DIR / out_name

        if req.normalize:
            # Two-pass loudnorm
            # Pass 1: measure
            measure_args = [
                "-y", "-i", str(path),
                "-af", "loudnorm=print_format=json",
                "-f", "null", "-",
            ]
            result = subprocess.run(
                [FFMPEG_PATH] + measure_args,
                capture_output=True, text=True, timeout=120,
            )
            # Pass 2: apply
            args = ["-y", "-i", str(path)]
            audio_filter = "loudnorm=I=-16:TP=-1.5:LRA=11"
            if has_video:
                args += ["-c:v", "copy", "-af", audio_filter]
            else:
                args += ["-af", audio_filter]
                if fmt == "wav":
                    args += ["-acodec", "pcm_s16le"]
            args.append(str(out_path))
        else:
            vol = req.volume if req.volume is not None else 1.0
            args = ["-y", "-i", str(path)]
            audio_filter = f"volume={vol}"
            if has_video:
                args += ["-c:v", "copy", "-af", audio_filter]
            else:
                args += ["-af", audio_filter]
                if fmt == "wav":
                    args += ["-acodec", "pcm_s16le"]
            args.append(str(out_path))

        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        out_info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "format": fmt,
            "duration_seconds": float(out_info.get("format", {}).get("duration", 0)),
            "volume_applied": req.volume if not req.normalize else "normalized",
        }
    finally:
        if is_temp and path.exists():
            path.unlink(missing_ok=True)


async def _op_trim(req: OperationRequest) -> dict:
    """Trim a media file to a time range."""
    url = req.url or req.video_url or req.audio_url
    if not url:
        raise HTTPException(400, "trim requires 'url', 'video_url', or 'audio_url'")
    if req.start_time is None and req.end_time is None and req.duration is None:
        raise HTTPException(400, "trim requires at least 'start_time', 'end_time', or 'duration'")

    path = await _fetch_to_tempfile(url)
    is_temp = (path != _resolve_local_url(url))

    try:
        info = _probe_file(str(path))
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))

        fmt = req.format or ("mp4" if has_video else "wav")
        out_name = _output_filename(f".{fmt}")
        out_path = OUTPUT_DIR / out_name

        args = ["-y"]
        start = req.start_time or 0.0
        args += ["-ss", str(start)]

        if req.end_time is not None:
            args += ["-to", str(req.end_time)]
        elif req.duration is not None:
            args += ["-t", str(req.duration)]

        args += ["-i", str(path)]

        if has_video:
            args += ["-c:v", "copy", "-c:a", "copy"]
        else:
            if fmt == "wav":
                args += ["-acodec", "pcm_s16le"]
            else:
                args += ["-c:a", "copy"]

        args.append(str(out_path))

        await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        out_info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "format": fmt,
            "duration_seconds": float(out_info.get("format", {}).get("duration", 0)),
            "start_time": start,
            "end_time": req.end_time,
        }
    finally:
        if is_temp and path.exists():
            path.unlink(missing_ok=True)


async def _op_concat(req: OperationRequest) -> dict:
    """Concatenate multiple media files sequentially."""
    if not req.files or len(req.files) < 2:
        raise HTTPException(400, "concat requires 'files' with at least 2 URLs")
    if len(req.files) > MAX_INPUT_FILES:
        raise HTTPException(400, f"Maximum {MAX_INPUT_FILES} files")

    file_paths = []
    temp_files = []

    try:
        for url in req.files:
            fp = await _fetch_to_tempfile(url)
            file_paths.append(fp)
            if fp != _resolve_local_url(url):
                temp_files.append(fp)

        # Detect if video or audio
        info = _probe_file(str(file_paths[0]))
        has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))

        fmt = req.format or ("mp4" if has_video else "wav")
        out_name = _output_filename(f".{fmt}")
        out_path = OUTPUT_DIR / out_name

        if req.transition == "crossfade" and has_video:
            # Use xfade filter for video crossfade
            await _concat_with_crossfade(file_paths, out_path, req.transition_duration, has_video)
        else:
            # Use concat demuxer for simple joining
            concat_file = OUTPUT_DIR / f"concat_{uuid.uuid4().hex[:8]}.txt"
            with open(concat_file, "w") as f:
                for fp in file_paths:
                    f.write(f"file '{fp}'\n")

            args = [
                "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(out_path),
            ]
            try:
                await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)
            finally:
                concat_file.unlink(missing_ok=True)

        out_info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "format": fmt,
            "duration_seconds": float(out_info.get("format", {}).get("duration", 0)),
            "files_concatenated": len(req.files),
            "transition": req.transition,
        }
    finally:
        for tf in temp_files:
            if tf.exists():
                tf.unlink(missing_ok=True)


async def _concat_with_crossfade(
    file_paths: List[Path], out_path: Path, xfade_dur: float, has_video: bool
):
    """Concat files with crossfade transitions using the xfade filter."""
    n = len(file_paths)

    # Get durations
    durations = []
    for fp in file_paths:
        info = _probe_file(str(fp))
        d = float(info.get("format", {}).get("duration", 0))
        durations.append(d)

    inputs = ["-y"]
    for fp in file_paths:
        inputs += ["-i", str(fp)]

    if has_video:
        # Chain xfade filters for video
        filter_parts = []
        offset = durations[0] - xfade_dur
        if n == 2:
            filter_parts.append(f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[vout]")
        else:
            prev = f"[0:v][1:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[v1]"
            filter_parts.append(prev)
            for i in range(2, n):
                offset += durations[i - 1] - xfade_dur
                if i == n - 1:
                    filter_parts.append(f"[v{i-1}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[vout]")
                else:
                    filter_parts.append(f"[v{i-1}][{i}:v]xfade=transition=fade:duration={xfade_dur}:offset={offset}[v{i}]")

        # Audio crossfade
        if n == 2:
            aoffset = durations[0] - xfade_dur
            filter_parts.append(f"[0:a][1:a]acrossfade=d={xfade_dur}[aout]")
        else:
            filter_parts.append(f"[0:a][1:a]acrossfade=d={xfade_dur}[a1]")
            for i in range(2, n):
                if i == n - 1:
                    filter_parts.append(f"[a{i-1}][{i}:a]acrossfade=d={xfade_dur}[aout]")
                else:
                    filter_parts.append(f"[a{i-1}][{i}:a]acrossfade=d={xfade_dur}[a{i}]")

        filter_graph = ";".join(filter_parts)
        args = inputs + [
            "-filter_complex", filter_graph,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", VIDEO_CODEC, "-crf", str(VIDEO_CRF), "-preset", VIDEO_PRESET,
            "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
            str(out_path),
        ]
    else:
        # Audio-only crossfade
        filter_parts = []
        if n == 2:
            filter_parts.append(f"[0:a][1:a]acrossfade=d={xfade_dur}[aout]")
        else:
            filter_parts.append(f"[0:a][1:a]acrossfade=d={xfade_dur}[a1]")
            for i in range(2, n):
                if i == n - 1:
                    filter_parts.append(f"[a{i-1}][{i}:a]acrossfade=d={xfade_dur}[aout]")
                else:
                    filter_parts.append(f"[a{i-1}][{i}:a]acrossfade=d={xfade_dur}[a{i}]")

        filter_graph = ";".join(filter_parts)
        args = inputs + [
            "-filter_complex", filter_graph,
            "-map", "[aout]",
            str(out_path),
        ]

    await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)


async def _op_create_slideshow(req: OperationRequest) -> dict:
    """Create a video from images with optional Ken Burns effects and audio."""
    if not req.images or len(req.images) < 1:
        raise HTTPException(400, "create_slideshow requires 'images' with at least 1 entry")
    if len(req.images) > MAX_SLIDESHOW_IMAGES:
        raise HTTPException(400, f"Maximum {MAX_SLIDESHOW_IMAGES} images")

    image_paths = []
    temp_files = []
    audio_path = None

    try:
        for img in req.images:
            ip = await _fetch_to_tempfile(img.url, suffix=".png")
            image_paths.append((ip, img))
            if ip != _resolve_local_url(img.url):
                temp_files.append(ip)

        if req.audio_url:
            audio_path = await _fetch_to_tempfile(req.audio_url)
            if audio_path != _resolve_local_url(req.audio_url):
                temp_files.append(audio_path)

        out_name = _output_filename(".mp4")
        out_path = OUTPUT_DIR / out_name
        fps = req.fps

        # Build per-image video segments then concat
        segment_paths = []
        zoom_range = _slideshow_cfg.get("zoom_range", 1.2)

        for i, (ip, entry) in enumerate(image_paths):
            seg_path = OUTPUT_DIR / f"_seg_{uuid.uuid4().hex[:8]}.mp4"
            segment_paths.append(seg_path)

            dur = entry.duration
            total_frames = int(dur * fps)
            effect = entry.effect.lower()

            # Build zoompan filter based on effect
            if effect == "zoom_in":
                zp = f"zoompan=z='min(zoom+{(zoom_range-1)/total_frames:.6f},pzoom+0.002)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"
            elif effect == "zoom_out":
                zp = f"zoompan=z='if(eq(on,1),{zoom_range},max(zoom-{(zoom_range-1)/total_frames:.6f},1))':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"
            elif effect == "pan_left":
                zp = f"zoompan=z='{zoom_range}':x='if(eq(on,1),0,min(x+2,iw))':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"
            elif effect == "pan_right":
                zp = f"zoompan=z='{zoom_range}':x='if(eq(on,1),iw,max(x-2,0))':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"
            elif effect == "ken_burns":
                # Zoom in + slight pan
                zp = f"zoompan=z='min(zoom+{(zoom_range-1)/total_frames:.6f},pzoom+0.002)':x='iw/2-(iw/zoom/2)+on*0.5':y='ih/2-(ih/zoom/2)+on*0.3':d={total_frames}:s=1920x1080:fps={fps}"
            else:
                # Static — no effect
                zp = f"zoompan=z='1':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1920x1080:fps={fps}"

            # Scale input image to at least 1920x1080 first so zoompan has pixels to work with
            vf = f"scale=max(1920\\,iw):max(1080\\,ih):force_original_aspect_ratio=increase,crop=max(1920\\,iw):max(1080\\,ih),{zp},format=yuv420p"

            args = [
                "-y", "-loop", "1", "-i", str(ip),
                "-vf", vf,
                "-t", str(dur),
                "-c:v", VIDEO_CODEC, "-crf", str(VIDEO_CRF), "-preset", VIDEO_PRESET,
                "-pix_fmt", "yuv420p",
                str(seg_path),
            ]
            await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)

        # Concatenate segments
        if len(segment_paths) == 1:
            # Single image — just rename
            segment_paths[0].rename(out_path)
        elif req.transition == "crossfade" and len(segment_paths) > 1:
            await _concat_with_crossfade(
                segment_paths, out_path,
                req.transition_duration, has_video=True
            )
        else:
            # Simple concat
            concat_file = OUTPUT_DIR / f"concat_{uuid.uuid4().hex[:8]}.txt"
            with open(concat_file, "w") as f:
                for sp in segment_paths:
                    f.write(f"file '{sp}'\n")
            args = ["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                    "-c", "copy", str(out_path)]
            try:
                await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)
            finally:
                concat_file.unlink(missing_ok=True)

        # Add audio if provided
        if audio_path:
            final_name = _output_filename(".mp4")
            final_path = OUTPUT_DIR / final_name
            args = [
                "-y", "-i", str(out_path), "-i", str(audio_path),
                "-c:v", "copy", "-c:a", AUDIO_CODEC, "-b:a", AUDIO_BITRATE,
                "-shortest",
                str(final_path),
            ]
            await asyncio.get_event_loop().run_in_executor(None, _run_ffmpeg, args)
            out_path.unlink(missing_ok=True)
            out_path = final_path
            out_name = final_name

        # Clean up segments
        for sp in segment_paths:
            sp.unlink(missing_ok=True)

        info = _probe_file(str(out_path))
        return {
            "output_file": f"/output/{out_name}",
            "duration_seconds": float(info.get("format", {}).get("duration", 0)),
            "image_count": len(req.images),
            "has_audio": audio_path is not None,
            "resolution": "1920x1080",
            "fps": fps,
        }
    finally:
        for tf in temp_files:
            if tf.exists():
                tf.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Operation dispatch
# ---------------------------------------------------------------------------

_OPERATIONS = {
    "probe": _op_probe,
    "extract_audio": _op_extract_audio,
    "strip_audio": _op_strip_audio,
    "combine": _op_combine,
    "mix_audio": _op_mix_audio,
    "adjust_volume": _op_adjust_volume,
    "trim": _op_trim,
    "concat": _op_concat,
    "create_slideshow": _op_create_slideshow,
}

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup / shutdown."""
    logger.info("Media Toolkit server starting...")
    # Verify FFmpeg is available
    try:
        result = subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, text=True, timeout=5)
        version_line = result.stdout.split("\n")[0] if result.stdout else "unknown"
        logger.info(f"FFmpeg: {version_line}")
    except Exception as e:
        logger.error(f"FFmpeg not found at '{FFMPEG_PATH}': {e}")
        logger.error("Install FFmpeg: sudo apt install ffmpeg")
    yield
    logger.info("Media Toolkit server shutting down.")


app = FastAPI(
    title="Media Toolkit",
    description="FFmpeg-based media composition — split, combine, mix, and assemble media files.",
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


# ---------------------------------------------------------------------------
# Standard endpoints (matching other GPU tool servers)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Health check."""
    ffmpeg_ok = shutil.which(FFMPEG_PATH) is not None or Path(FFMPEG_PATH).exists()
    return {"status": "ok" if ffmpeg_ok else "degraded", "ffmpeg_available": ffmpeg_ok}


@app.get("/info")
async def info():
    """Server information."""
    # Get FFmpeg version
    ff_version = "unknown"
    try:
        result = subprocess.run([FFMPEG_PATH, "-version"], capture_output=True, text=True, timeout=5)
        if result.stdout:
            ff_version = result.stdout.split("\n")[0]
    except Exception:
        pass

    return {
        "service": "media-toolkit",
        "ffmpeg_version": ff_version,
        "operations": list(_OPERATIONS.keys()),
        "output_directory": str(OUTPUT_DIR),
        "limits": {
            "max_input_size_mb": MAX_INPUT_SIZE_MB,
            "max_output_duration": MAX_OUTPUT_DURATION,
            "max_input_files": MAX_INPUT_FILES,
            "max_audio_tracks": MAX_AUDIO_TRACKS,
            "max_slideshow_images": MAX_SLIDESHOW_IMAGES,
            "processing_timeout": PROCESSING_TIMEOUT,
        },
    }


@app.post("/shutdown")
async def shutdown():
    """Graceful shutdown."""
    import signal
    os.kill(os.getpid(), signal.SIGTERM)
    return {"status": "shutting_down"}


@app.get("/output/{filename}")
async def serve_output(filename: str):
    """Serve generated output files."""
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    if safe_name != filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")
    path = OUTPUT_DIR / safe_name
    if not path.resolve().parent == OUTPUT_DIR.resolve():
        raise HTTPException(400, "Invalid filename")
    if not path.exists():
        raise HTTPException(404, f"File not found: {filename}")

    # Determine media type
    ext = path.suffix.lower()
    media_types = {
        ".mp4": "video/mp4",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".webm": "video/webm",
    }
    return FileResponse(str(path), media_type=media_types.get(ext, "application/octet-stream"))


# ---------------------------------------------------------------------------
# Main operation endpoint
# ---------------------------------------------------------------------------

@app.post("/process")
async def process(req: OperationRequest):
    """Execute a media operation."""
    op_name = req.operation.lower().strip()

    if op_name not in _OPERATIONS:
        raise HTTPException(
            400,
            f"Unknown operation '{op_name}'. Available: {', '.join(_OPERATIONS.keys())}"
        )

    start = time.time()
    try:
        result = await _OPERATIONS[op_name](req)
        elapsed = time.time() - start
        result["processing_time_seconds"] = round(elapsed, 3)
        result["operation"] = op_name
        logger.info(f"Operation '{op_name}' completed in {elapsed:.1f}s")
        return result
    except HTTPException:
        raise
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"Operation '{op_name}' timed out after {PROCESSING_TIMEOUT}s")
    except RuntimeError as e:
        logger.error(f"Operation '{op_name}' RuntimeError: {e}")
        raise HTTPException(500, "Operation failed")
    except Exception as e:
        logger.exception(f"Operation '{op_name}' failed")
        raise HTTPException(500, "Operation failed")
