"""Thumbnail generation service for the Media Library.

Generates thumbnails for images (Pillow), videos (FFmpeg frame extraction),
and audio files (FFmpeg waveform visualization). Thumbnails are cached on disk
in the media_cache Docker volume.
"""
import asyncio
import io
import logging
from pathlib import Path
from typing import Optional

from app.core.path_security import validate_tool_file_path

logger = logging.getLogger(__name__)

# Thumbnail dimensions
THUMB_MAX_WIDTH = 300
THUMB_MAX_HEIGHT = 300
THUMB_JPEG_QUALITY = 85

# Waveform image settings
WAVEFORM_WIDTH = 300
WAVEFORM_HEIGHT = 120

# FFmpeg timeout (seconds)
FFMPEG_TIMEOUT = 30


async def generate_image_thumbnail(source_path: Path) -> Optional[bytes]:
    """Generate a thumbnail for an image file using Pillow.
    
    Returns JPEG bytes or None on failure.
    """
    try:
        from PIL import Image

        def _resize():
            with Image.open(source_path) as img:
                # Convert to RGB (handles RGBA, palette, etc.)
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.thumbnail((THUMB_MAX_WIDTH, THUMB_MAX_HEIGHT), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=THUMB_JPEG_QUALITY)
                return buf.getvalue()

        # Run in thread pool to avoid blocking async loop
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _resize)
    except Exception as e:
        logger.warning(f"Image thumbnail generation failed for {source_path}: {e}")
        return None


async def generate_video_thumbnail(source_path: Path) -> Optional[bytes]:
    """Generate a thumbnail from a video file by extracting a frame at 25% duration.
    
    Uses FFmpeg. Returns JPEG bytes or None on failure.
    """
    try:
        # First, get video duration
        probe_proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(source_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            probe_proc.communicate(), timeout=FFMPEG_TIMEOUT
        )
        
        duration = float(stdout.decode().strip()) if stdout.decode().strip() else 2.0
        seek_time = max(0.5, duration * 0.25)  # 25% into the video

        # Extract frame
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-ss", str(seek_time),
            "-i", str(source_path),
            "-vframes", "1",
            "-vf", f"scale={THUMB_MAX_WIDTH}:{THUMB_MAX_HEIGHT}:force_original_aspect_ratio=decrease",
            "-f", "image2",
            "-c:v", "mjpeg",
            "-q:v", "5",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT
        )

        if proc.returncode != 0:
            logger.warning(f"FFmpeg frame extraction failed for {source_path}: {stderr.decode()[:200]}")
            return None

        if not stdout or len(stdout) < 100:
            return None

        return stdout
    except asyncio.TimeoutError:
        logger.warning(f"FFmpeg timed out extracting frame from {source_path}")
        return None
    except Exception as e:
        logger.warning(f"Video thumbnail generation failed for {source_path}: {e}")
        return None


async def generate_audio_thumbnail(source_path: Path) -> Optional[bytes]:
    """Generate a waveform visualization thumbnail for an audio file.
    
    Uses FFmpeg's showwavespic filter. Returns PNG bytes or None on failure.
    The waveform is drawn in neon-cyan (#00d9ff) on a transparent/dark background.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-i", str(source_path),
            "-filter_complex",
            f"showwavespic=s={WAVEFORM_WIDTH}x{WAVEFORM_HEIGHT}:colors=#00d9ff",
            "-frames:v", "1",
            "-f", "image2",
            "-c:v", "png",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT
        )

        if proc.returncode != 0:
            logger.warning(f"FFmpeg waveform generation failed for {source_path}: {stderr.decode()[:200]}")
            return None

        if not stdout or len(stdout) < 100:
            return None

        return stdout
    except asyncio.TimeoutError:
        logger.warning(f"FFmpeg timed out generating waveform for {source_path}")
        return None
    except Exception as e:
        logger.warning(f"Audio thumbnail generation failed for {source_path}: {e}")
        return None


async def generate_thumbnail(source_path: Path, media_type: str) -> Optional[bytes]:
    """Generate a thumbnail based on media type.
    
    Args:
        source_path: Path to the source media file
        media_type: One of "image", "video", "audio"
    
    Returns:
        Thumbnail bytes (JPEG for image/video, PNG for audio waveform) or None
    """
    # SGA3-L2: Validate the source path to prevent path traversal
    validate_tool_file_path(str(source_path), label="thumbnail source")

    if media_type == "image":
        return await generate_image_thumbnail(source_path)
    elif media_type == "video":
        return await generate_video_thumbnail(source_path)
    elif media_type == "audio":
        return await generate_audio_thumbnail(source_path)
    else:
        return None


async def check_ffmpeg_available() -> bool:
    """Check if ffmpeg and ffprobe are available."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0
    except Exception:
        return False
