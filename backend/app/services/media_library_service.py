"""Media Library Service — browse and manage generated media files from tool services.

Provides file listing, metadata, thumbnail generation, and file serving
for output directories mounted from host GPU/CPU tool services.
"""
import hashlib
import logging
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# =============================================================================
# Response Models
# =============================================================================


class ToolMediaSummary(BaseModel):
    """Summary of a tool's generated media."""
    slug: str
    display_name: str
    icon: str  # "music" | "image" | "video" | "document" | "audio" | "archive"
    file_count: int
    total_size_bytes: int
    newest_file_date: Optional[str] = None
    media_types: list[str]


class MediaFile(BaseModel):
    """Metadata for a single generated media file."""
    filename: str
    size_bytes: int
    created_at: str
    modified_at: str
    media_type: str  # "image" | "video" | "audio" | "document" | "archive" | "other"
    mime_type: str
    extension: str
    has_thumbnail: bool
    download_url: str
    thumbnail_url: Optional[str] = None


class MediaFileList(BaseModel):
    """Paginated list of media files."""
    files: list[MediaFile]
    total_count: int
    total_size_bytes: int
    page: int
    page_size: int
    has_more: bool


class MediaStats(BaseModel):
    """Global media library statistics."""
    total_files: int
    total_size_bytes: int
    by_type: dict[str, int]
    by_tool: dict[str, int]


# =============================================================================
# Tool Registry
# =============================================================================

# Maps tool slug → metadata. Output directories are mounted at
# /app/tool_outputs/<slug>/ in the Docker container.
TOOL_MEDIA_REGISTRY: dict[str, dict] = {
    "acestep-music-generation": {
        "display_name": "ACE-Step Music",
        "icon": "music",
        "media_types": ["audio"],
    },
    "qwen3-tts-voice": {
        "display_name": "Qwen3 TTS",
        "icon": "audio",
        "media_types": ["audio"],
    },
    "zimage-generation": {
        "display_name": "Z-Image",
        "icon": "image",
        "media_types": ["image"],
    },
    "seedvr2-upscaler": {
        "display_name": "SeedVR2 Upscaler",
        "icon": "image",
        "media_types": ["image", "video"],
    },
    "canary-stt": {
        "display_name": "Canary STT",
        "icon": "document",
        "media_types": ["document"],
    },
    "ltx-video-generation": {
        "display_name": "LTX-2 Video",
        "icon": "video",
        "media_types": ["video"],
    },
    "audiosr-enhance": {
        "display_name": "AudioSR",
        "icon": "audio",
        "media_types": ["audio"],
    },
    "media-toolkit": {
        "display_name": "Media Toolkit",
        "icon": "video",
        "media_types": ["audio", "video"],
    },
    "realesrgan-cpu-upscaler": {
        "display_name": "Real-ESRGAN CPU",
        "icon": "image",
        "media_types": ["image", "video"],
    },
    "docling-parser": {
        "display_name": "Docling Parser",
        "icon": "document",
        "media_types": ["document"],
    },
}

# =============================================================================
# File Type Classification
# =============================================================================

MEDIA_TYPE_MAP: dict[str, str] = {
    # Images
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
    ".gif": "image", ".bmp": "image", ".tiff": "image", ".svg": "image",
    # Video
    ".mp4": "video", ".webm": "video", ".mov": "video", ".avi": "video",
    ".mkv": "video", ".flv": "video",
    # Audio
    ".wav": "audio", ".mp3": "audio", ".flac": "audio", ".ogg": "audio",
    ".m4a": "audio", ".aac": "audio", ".wma": "audio",
    # Documents
    ".json": "document", ".jsonl": "document", ".md": "document",
    ".txt": "document", ".pdf": "document", ".html": "document",
    ".csv": "document", ".xml": "document",
    # Archives
    ".zip": "archive", ".tar": "archive", ".gz": "archive",
    ".7z": "archive", ".rar": "archive",
    # Code
    ".py": "document", ".js": "document", ".ts": "document",
}

# Ensured mimetypes module is initialised
mimetypes.init()


# =============================================================================
# Helpers
# =============================================================================

def _sanitize_filename(filename: str) -> str:
    """Sanitize a filename, allowing only safe characters.
    
    Returns the sanitized filename or raises ValueError if invalid.
    """
    safe = "".join(c for c in filename if c.isalnum() or c in "-_.")
    if not safe or safe != filename or ".." in filename or filename.startswith("."):
        raise ValueError(f"Invalid filename: {filename}")
    return safe


def _classify_file(filename: str) -> tuple[str, str]:
    """Return (media_type, mime_type) for a filename."""
    ext = Path(filename).suffix.lower()
    media_type = MEDIA_TYPE_MAP.get(ext, "other")
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return media_type, mime_type


def _thumbnail_cache_key(tool_slug: str, filename: str) -> str:
    """Generate a deterministic cache filename for a thumbnail."""
    h = hashlib.sha256(f"{tool_slug}/{filename}".encode()).hexdigest()[:16]
    return f"{tool_slug}_{h}.jpg"


# =============================================================================
# Service
# =============================================================================

class MediaLibraryService:
    """Service for browsing generated media files from tool output directories."""

    TOOL_OUTPUTS_ROOT = Path("/data/tool_outputs")
    THUMBNAIL_CACHE_DIR = Path("/data/media_cache/thumbnails")
    API_PREFIX = "/api/v1/media"

    # Files to skip in listings (metadata files, not user-facing media)
    SKIP_FILES = {"generations.jsonl", ".gitkeep", ".DS_Store", "Thumbs.db"}

    # Max file size for thumbnail generation (2GB)
    MAX_THUMBNAIL_SOURCE_BYTES = 2 * 1024 * 1024 * 1024

    def __init__(self) -> None:
        self.THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _tool_output_dir(self, tool_slug: str) -> Path:
        """Get the output directory path for a tool, validated."""
        if tool_slug not in TOOL_MEDIA_REGISTRY:
            raise ValueError(f"Unknown tool slug: {tool_slug}")
        path = self.TOOL_OUTPUTS_ROOT / tool_slug
        return path

    def _scan_files(self, tool_slug: str) -> list[dict]:
        """Scan a tool's output directory and return file metadata dicts."""
        out_dir = self._tool_output_dir(tool_slug)
        if not out_dir.exists() or not out_dir.is_dir():
            return []

        files = []
        try:
            with os.scandir(out_dir) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue
                    if entry.name in self.SKIP_FILES:
                        continue
                    if entry.name.startswith("."):
                        continue
                    try:
                        stat = entry.stat()
                        media_type, mime_type = _classify_file(entry.name)
                        ext = Path(entry.name).suffix.lower()

                        # Check thumbnail availability
                        thumb_key = _thumbnail_cache_key(tool_slug, entry.name)
                        has_thumb = (self.THUMBNAIL_CACHE_DIR / thumb_key).exists()

                        files.append({
                            "filename": entry.name,
                            "size_bytes": stat.st_size,
                            "created_at": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
                            "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                            "media_type": media_type,
                            "mime_type": mime_type,
                            "extension": ext,
                            "has_thumbnail": has_thumb,
                            "download_url": f"{self.API_PREFIX}/{tool_slug}/files/{entry.name}",
                            "thumbnail_url": f"{self.API_PREFIX}/{tool_slug}/files/{entry.name}/thumbnail" if (has_thumb or media_type in ("image", "video", "audio")) else None,
                        })
                    except OSError:
                        continue  # Skip files we can't stat
        except OSError as e:
            logger.warning(f"Cannot scan output dir for {tool_slug}: {e}")

        return files

    # =========================================================================
    # Public API
    # =========================================================================

    def list_tools_with_media(self) -> list[ToolMediaSummary]:
        """List all tools that have output files with counts and sizes."""
        summaries = []
        for slug, info in TOOL_MEDIA_REGISTRY.items():
            files = self._scan_files(slug)
            if not files:
                # Still include tools with no files — UI shows empty state
                summaries.append(ToolMediaSummary(
                    slug=slug,
                    display_name=info["display_name"],
                    icon=info["icon"],
                    file_count=0,
                    total_size_bytes=0,
                    newest_file_date=None,
                    media_types=info["media_types"],
                ))
                continue

            total_size = sum(f["size_bytes"] for f in files)
            newest = max(files, key=lambda f: f["modified_at"])

            summaries.append(ToolMediaSummary(
                slug=slug,
                display_name=info["display_name"],
                icon=info["icon"],
                file_count=len(files),
                total_size_bytes=total_size,
                newest_file_date=newest["modified_at"],
                media_types=info["media_types"],
            ))

        # Sort by file count descending (tools with most media first)
        summaries.sort(key=lambda s: s.file_count, reverse=True)
        return summaries

    def list_files(
        self,
        tool_slug: str,
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "modified_at",
        sort_order: str = "desc",
        media_type: Optional[str] = None,
    ) -> MediaFileList:
        """List files in a tool's output directory with pagination."""
        files = self._scan_files(tool_slug)

        # Filter by media type
        if media_type:
            files = [f for f in files if f["media_type"] == media_type]

        total_count = len(files)
        total_size = sum(f["size_bytes"] for f in files)

        # Sort
        reverse = sort_order == "desc"
        if sort_by == "name":
            files.sort(key=lambda f: f["filename"].lower(), reverse=reverse)
        elif sort_by == "size":
            files.sort(key=lambda f: f["size_bytes"], reverse=reverse)
        else:  # modified_at (default)
            files.sort(key=lambda f: f["modified_at"], reverse=reverse)

        # Paginate
        start = (page - 1) * page_size
        end = start + page_size
        page_files = files[start:end]

        return MediaFileList(
            files=[MediaFile(**f) for f in page_files],
            total_count=total_count,
            total_size_bytes=total_size,
            page=page,
            page_size=page_size,
            has_more=end < total_count,
        )

    def get_file_path(self, tool_slug: str, filename: str) -> Path:
        """Get the full path to a file, with security validation."""
        safe_name = _sanitize_filename(filename)
        out_dir = self._tool_output_dir(tool_slug)
        file_path = out_dir / safe_name

        # Defence-in-depth: verify resolved path stays within output dir
        resolved = file_path.resolve()
        if not str(resolved).startswith(str(out_dir.resolve())):
            raise ValueError(f"Path traversal detected: {filename}")

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {filename}")

        return file_path

    def get_thumbnail_path(self, tool_slug: str, filename: str) -> Optional[Path]:
        """Get cached thumbnail path, or None if not cached."""
        thumb_key = _thumbnail_cache_key(tool_slug, filename)
        thumb_path = self.THUMBNAIL_CACHE_DIR / thumb_key
        if thumb_path.exists():
            return thumb_path
        return None

    def save_thumbnail(self, tool_slug: str, filename: str, data: bytes) -> Path:
        """Save thumbnail data to cache and return the path."""
        thumb_key = _thumbnail_cache_key(tool_slug, filename)
        thumb_path = self.THUMBNAIL_CACHE_DIR / thumb_key
        thumb_path.write_bytes(data)
        return thumb_path

    def get_stats(self) -> MediaStats:
        """Get global media library statistics."""
        by_type: dict[str, int] = {}
        by_tool: dict[str, int] = {}
        total_files = 0
        total_size = 0

        for slug in TOOL_MEDIA_REGISTRY:
            files = self._scan_files(slug)
            tool_count = len(files)
            tool_size = sum(f["size_bytes"] for f in files)

            by_tool[slug] = tool_count
            total_files += tool_count
            total_size += tool_size

            for f in files:
                mt = f["media_type"]
                by_type[mt] = by_type.get(mt, 0) + 1

        return MediaStats(
            total_files=total_files,
            total_size_bytes=total_size,
            by_type=by_type,
            by_tool=by_tool,
        )

    def get_files_needing_thumbnails(self, tool_slug: str) -> list[str]:
        """Return filenames that could have thumbnails but don't yet."""
        files = self._scan_files(tool_slug)
        result = []
        for f in files:
            if f["media_type"] in ("image", "video", "audio") and not f["has_thumbnail"]:
                # Skip oversized files
                if f["size_bytes"] <= self.MAX_THUMBNAIL_SOURCE_BYTES:
                    result.append(f["filename"])
        return result


# =============================================================================
# Singleton
# =============================================================================

_service: Optional[MediaLibraryService] = None


def get_media_library_service() -> MediaLibraryService:
    """Get the singleton MediaLibraryService instance."""
    global _service
    if _service is None:
        _service = MediaLibraryService()
    return _service
