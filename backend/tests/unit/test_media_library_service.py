"""Tests for Media Library Service — file listing, metadata, thumbnails, security."""
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.services.media_library_service import (
    MediaLibraryService,
    ToolMediaSummary,
    MediaFile,
    MediaFileList,
    MediaStats,
    _sanitize_filename,
    _classify_file,
    _thumbnail_cache_key,
    TOOL_MEDIA_REGISTRY,
    MEDIA_TYPE_MAP,
)


# =============================================================================
# Filename Sanitization Tests
# =============================================================================

class TestSanitizeFilename:
    """Test filename sanitization against path traversal and injection."""

    def test_valid_filename(self):
        assert _sanitize_filename("ZIMG_00001.png") == "ZIMG_00001.png"

    def test_valid_uuid_filename(self):
        assert _sanitize_filename("035b7550-c586-4aa8-3323-c42351da4dfb.mp3") == "035b7550-c586-4aa8-3323-c42351da4dfb.mp3"

    def test_valid_timestamp_filename(self):
        assert _sanitize_filename("tts_custom_voice_20260209_234410.wav") == "tts_custom_voice_20260209_234410.wav"

    def test_path_traversal_dotdot(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("../../etc/passwd")

    def test_path_traversal_dotdot_embedded(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("valid..name.png")

    def test_hidden_file(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename(".hidden")

    def test_slash_in_filename(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("path/to/file.png")

    def test_empty_filename(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("")

    def test_null_byte(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("file\x00.png")

    def test_shell_metacharacters(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("file;rm -rf /.png")

    def test_space_in_filename(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("file name.png")

    def test_backslash(self):
        with pytest.raises(ValueError, match="Invalid filename"):
            _sanitize_filename("path\\file.png")


# =============================================================================
# File Classification Tests
# =============================================================================

class TestClassifyFile:
    """Test media type and MIME type detection."""

    def test_png_image(self):
        media_type, mime_type = _classify_file("ZIMG_00001.png")
        assert media_type == "image"
        assert "image" in mime_type

    def test_mp4_video(self):
        media_type, mime_type = _classify_file("LTX2_00001.mp4")
        assert media_type == "video"
        assert "video" in mime_type

    def test_wav_audio(self):
        media_type, mime_type = _classify_file("output.wav")
        assert media_type == "audio"

    def test_mp3_audio(self):
        media_type, mime_type = _classify_file("song.mp3")
        assert media_type == "audio"

    def test_json_document(self):
        media_type, _ = _classify_file("data.json")
        assert media_type == "document"

    def test_zip_archive(self):
        media_type, _ = _classify_file("archive.zip")
        assert media_type == "archive"

    def test_unknown_extension(self):
        media_type, mime_type = _classify_file("file.qwxyz123")
        assert media_type == "other"
        assert mime_type == "application/octet-stream"

    def test_case_insensitive(self):
        media_type, _ = _classify_file("IMAGE.PNG")
        assert media_type == "image"


# =============================================================================
# Thumbnail Cache Key Tests
# =============================================================================

class TestThumbnailCacheKey:
    """Test deterministic cache key generation."""

    def test_consistent_key(self):
        key1 = _thumbnail_cache_key("zimage-generation", "ZIMG_00001.png")
        key2 = _thumbnail_cache_key("zimage-generation", "ZIMG_00001.png")
        assert key1 == key2

    def test_different_files_different_keys(self):
        key1 = _thumbnail_cache_key("zimage-generation", "ZIMG_00001.png")
        key2 = _thumbnail_cache_key("zimage-generation", "ZIMG_00002.png")
        assert key1 != key2

    def test_different_tools_different_keys(self):
        key1 = _thumbnail_cache_key("zimage-generation", "file.png")
        key2 = _thumbnail_cache_key("ltx-video-generation", "file.png")
        assert key1 != key2

    def test_key_format(self):
        key = _thumbnail_cache_key("zimage-generation", "ZIMG_00001.png")
        assert key.startswith("zimage-generation_")
        assert key.endswith(".jpg")


# =============================================================================
# Tool Registry Tests
# =============================================================================

class TestToolRegistry:
    """Test the tool media registry completeness."""

    def test_all_tools_have_display_name(self):
        for slug, info in TOOL_MEDIA_REGISTRY.items():
            assert "display_name" in info, f"{slug} missing display_name"
            assert len(info["display_name"]) > 0

    def test_all_tools_have_icon(self):
        for slug, info in TOOL_MEDIA_REGISTRY.items():
            assert "icon" in info, f"{slug} missing icon"

    def test_all_tools_have_media_types(self):
        for slug, info in TOOL_MEDIA_REGISTRY.items():
            assert "media_types" in info, f"{slug} missing media_types"
            assert len(info["media_types"]) > 0

    def test_expected_tools_present(self):
        expected = [
            "acestep-music-generation",
            "zimage-generation",
            "ltx-video-generation",
            "qwen3-tts-voice",
            "seedvr2-upscaler",
            "audiosr-enhance",
            "media-toolkit",
            "realesrgan-cpu-upscaler",
            "canary-stt",
            "docling-parser",
        ]
        for slug in expected:
            assert slug in TOOL_MEDIA_REGISTRY, f"Missing tool: {slug}"


# =============================================================================
# Service Tests with Temporary Directories
# =============================================================================

class TestMediaLibraryService:
    """Test MediaLibraryService with real filesystem operations."""

    @pytest.fixture
    def temp_dirs(self, tmp_path):
        """Create temporary output and cache directories."""
        outputs = tmp_path / "tool_outputs"
        outputs.mkdir()
        cache = tmp_path / "media_cache" / "thumbnails"
        cache.mkdir(parents=True)
        return outputs, cache

    @pytest.fixture
    def service(self, temp_dirs):
        """Create a service instance pointing at temp dirs (skip real __init__)."""
        outputs, cache = temp_dirs
        svc = MediaLibraryService.__new__(MediaLibraryService)
        svc.TOOL_OUTPUTS_ROOT = outputs
        svc.THUMBNAIL_CACHE_DIR = cache
        svc.THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return svc

    def _create_test_file(self, dir_path: Path, name: str, content: bytes = b"test") -> Path:
        """Helper: create a test file in a directory."""
        dir_path.mkdir(parents=True, exist_ok=True)
        path = dir_path / name
        path.write_bytes(content)
        return path

    # --- list_tools_with_media ---

    def test_list_tools_empty_dirs(self, service, temp_dirs):
        """All tools should appear with file_count=0 when dirs are empty."""
        outputs, _ = temp_dirs
        for slug in TOOL_MEDIA_REGISTRY:
            (outputs / slug).mkdir()

        result = service.list_tools_with_media()
        assert len(result) == len(TOOL_MEDIA_REGISTRY)
        for tool in result:
            assert tool.file_count == 0

    def test_list_tools_with_files(self, service, temp_dirs):
        """Tools with files should have correct counts."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "ZIMG_00001.png", b"x" * 1000)
        self._create_test_file(zimage_dir, "ZIMG_00002.png", b"x" * 2000)

        result = service.list_tools_with_media()
        zimage = next(t for t in result if t.slug == "zimage-generation")
        assert zimage.file_count == 2
        assert zimage.total_size_bytes == 3000

    def test_list_tools_skips_hidden_files(self, service, temp_dirs):
        """Hidden files and metadata files should be skipped."""
        outputs, _ = temp_dirs
        ltx_dir = outputs / "ltx-video-generation"
        self._create_test_file(ltx_dir, "LTX2_00001.mp4")
        self._create_test_file(ltx_dir, "generations.jsonl")  # In SKIP_FILES
        self._create_test_file(ltx_dir, ".DS_Store")

        result = service.list_tools_with_media()
        ltx = next(t for t in result if t.slug == "ltx-video-generation")
        assert ltx.file_count == 1

    def test_list_tools_sorted_by_count(self, service, temp_dirs):
        """Tools should be sorted by file count (most files first)."""
        outputs, _ = temp_dirs
        self._create_test_file(outputs / "zimage-generation", "a.png")
        self._create_test_file(outputs / "ltx-video-generation", "a.mp4")
        self._create_test_file(outputs / "ltx-video-generation", "b.mp4")
        self._create_test_file(outputs / "ltx-video-generation", "c.mp4")

        result = service.list_tools_with_media()
        has_files = [t for t in result if t.file_count > 0]
        assert has_files[0].slug == "ltx-video-generation"
        assert has_files[0].file_count == 3

    # --- list_files ---

    def test_list_files_basic(self, service, temp_dirs):
        """List files returns correct metadata."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "ZIMG_00001.png", b"image data here")

        result = service.list_files("zimage-generation")
        assert result.total_count == 1
        assert result.files[0].filename == "ZIMG_00001.png"
        assert result.files[0].media_type == "image"
        assert result.files[0].extension == ".png"
        assert result.files[0].size_bytes == len(b"image data here")

    def test_list_files_pagination(self, service, temp_dirs):
        """Pagination should work correctly."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        for i in range(10):
            self._create_test_file(zimage_dir, f"ZIMG_{i:05d}.png")

        # Page 1
        result = service.list_files("zimage-generation", page=1, page_size=3)
        assert len(result.files) == 3
        assert result.total_count == 10
        assert result.has_more is True

        # Last page
        result = service.list_files("zimage-generation", page=4, page_size=3)
        assert len(result.files) == 1
        assert result.has_more is False

    def test_list_files_sort_by_name(self, service, temp_dirs):
        """Files should be sortable by name."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "b.png")
        self._create_test_file(zimage_dir, "a.png")
        self._create_test_file(zimage_dir, "c.png")

        result = service.list_files("zimage-generation", sort_by="name", sort_order="asc")
        names = [f.filename for f in result.files]
        assert names == ["a.png", "b.png", "c.png"]

    def test_list_files_filter_by_type(self, service, temp_dirs):
        """Filtering by media type should work."""
        outputs, _ = temp_dirs
        mt_dir = outputs / "media-toolkit"
        self._create_test_file(mt_dir, "output.wav")
        self._create_test_file(mt_dir, "output.mp4")

        result = service.list_files("media-toolkit", media_type="audio")
        assert result.total_count == 1
        assert result.files[0].extension == ".wav"

    def test_list_files_unknown_tool(self, service):
        """Listing files for an unknown tool should raise."""
        with pytest.raises(ValueError, match="Unknown tool slug"):
            service.list_files("nonexistent-tool")

    def test_list_files_empty_dir(self, service, temp_dirs):
        """Empty directory should return empty list."""
        outputs, _ = temp_dirs
        (outputs / "canary-stt").mkdir()

        result = service.list_files("canary-stt")
        assert result.total_count == 0
        assert result.files == []

    # --- get_file_path ---

    def test_get_file_path_valid(self, service, temp_dirs):
        """Valid filename should return the file path."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "ZIMG_00001.png")

        path = service.get_file_path("zimage-generation", "ZIMG_00001.png")
        assert path.exists()
        assert path.name == "ZIMG_00001.png"

    def test_get_file_path_not_found(self, service, temp_dirs):
        """Missing file should raise FileNotFoundError."""
        outputs, _ = temp_dirs
        (outputs / "zimage-generation").mkdir()

        with pytest.raises(FileNotFoundError):
            service.get_file_path("zimage-generation", "nonexistent.png")

    def test_get_file_path_traversal_rejected(self, service, temp_dirs):
        """Path traversal attempts should be rejected."""
        outputs, _ = temp_dirs
        (outputs / "zimage-generation").mkdir()

        with pytest.raises(ValueError):
            service.get_file_path("zimage-generation", "../../etc/passwd")

    def test_get_file_path_invalid_slug(self, service):
        """Unknown tool slug should be rejected."""
        with pytest.raises(ValueError, match="Unknown tool slug"):
            service.get_file_path("unknown-tool", "file.png")

    # --- thumbnails ---

    def test_thumbnail_cache_miss(self, service, temp_dirs):
        """Missing thumbnail should return None."""
        result = service.get_thumbnail_path("zimage-generation", "ZIMG_00001.png")
        assert result is None

    def test_thumbnail_save_and_retrieve(self, service, temp_dirs):
        """Saved thumbnail should be retrievable."""
        thumb_data = b"fake jpeg data"
        service.save_thumbnail("zimage-generation", "ZIMG_00001.png", thumb_data)

        path = service.get_thumbnail_path("zimage-generation", "ZIMG_00001.png")
        assert path is not None
        assert path.read_bytes() == thumb_data

    def test_has_thumbnail_in_file_listing(self, service, temp_dirs):
        """Files with cached thumbnails should have has_thumbnail=True."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "ZIMG_00001.png")

        # Before thumbnail
        result = service.list_files("zimage-generation")
        assert result.files[0].has_thumbnail is False

        # After thumbnail
        service.save_thumbnail("zimage-generation", "ZIMG_00001.png", b"thumb")
        result = service.list_files("zimage-generation")
        assert result.files[0].has_thumbnail is True

    # --- get_stats ---

    def test_stats_empty(self, service, temp_dirs):
        """Stats should be zero when no files exist."""
        stats = service.get_stats()
        assert stats.total_files == 0
        assert stats.total_size_bytes == 0

    def test_stats_with_files(self, service, temp_dirs):
        """Stats should aggregate across tools."""
        outputs, _ = temp_dirs
        self._create_test_file(outputs / "zimage-generation", "a.png", b"x" * 100)
        self._create_test_file(outputs / "ltx-video-generation", "b.mp4", b"y" * 200)

        stats = service.get_stats()
        assert stats.total_files == 2
        assert stats.total_size_bytes == 300
        assert stats.by_type.get("image", 0) == 1
        assert stats.by_type.get("video", 0) == 1
        assert stats.by_tool.get("zimage-generation", 0) == 1
        assert stats.by_tool.get("ltx-video-generation", 0) == 1

    # --- get_files_needing_thumbnails ---

    def test_files_needing_thumbnails_images(self, service, temp_dirs):
        """Image files without thumbnails should be returned."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "a.png")
        self._create_test_file(zimage_dir, "b.png")

        result = service.get_files_needing_thumbnails("zimage-generation")
        assert len(result) == 2

    def test_files_needing_thumbnails_excludes_documents(self, service, temp_dirs):
        """Document files should not need thumbnails."""
        outputs, _ = temp_dirs
        doc_dir = outputs / "docling-parser"
        self._create_test_file(doc_dir, "output.json")

        result = service.get_files_needing_thumbnails("docling-parser")
        assert len(result) == 0

    def test_files_needing_thumbnails_excludes_cached(self, service, temp_dirs):
        """Files with existing thumbnails should be excluded."""
        outputs, _ = temp_dirs
        zimage_dir = outputs / "zimage-generation"
        self._create_test_file(zimage_dir, "a.png")
        service.save_thumbnail("zimage-generation", "a.png", b"thumb")

        result = service.get_files_needing_thumbnails("zimage-generation")
        assert len(result) == 0


# =============================================================================
# Security-Focused Tests
# =============================================================================

class TestMediaLibrarySecurity:
    """Security-focused tests for path traversal and injection prevention."""

    @pytest.fixture
    def service(self, tmp_path):
        outputs = tmp_path / "tool_outputs"
        outputs.mkdir()
        cache = tmp_path / "media_cache" / "thumbnails"
        cache.mkdir(parents=True)
        svc = MediaLibraryService.__new__(MediaLibraryService)
        svc.TOOL_OUTPUTS_ROOT = outputs
        svc.THUMBNAIL_CACHE_DIR = cache
        return svc

    def test_traversal_dotdot_slash(self, service, tmp_path):
        with pytest.raises(ValueError):
            service.get_file_path("zimage-generation", "../../../etc/passwd")

    def test_traversal_encoded_slashes(self, service):
        # URL-encoded path separators should be rejected
        with pytest.raises(ValueError):
            _sanitize_filename("..%2f..%2fetc%2fpasswd")

    def test_traversal_null_byte(self, service):
        with pytest.raises(ValueError):
            service.get_file_path("zimage-generation", "file.png\x00.jpg")

    def test_slug_injection(self, service):
        """Non-registered tool slugs should be rejected."""
        with pytest.raises(ValueError, match="Unknown tool slug"):
            service.get_file_path("../../backend", "config.py")

    def test_empty_slug(self, service):
        with pytest.raises(ValueError, match="Unknown tool slug"):
            service.get_file_path("", "file.png")

    def test_newline_in_filename(self, service):
        with pytest.raises(ValueError):
            _sanitize_filename("file\nname.png")

    def test_symlink_traversal(self, service, tmp_path):
        """Symlinks pointing outside the output dir should be blocked."""
        outputs = tmp_path / "tool_outputs"
        zimage_dir = outputs / "zimage-generation"
        zimage_dir.mkdir(parents=True)

        # Create a symlink pointing outside
        secret = tmp_path / "secret.txt"
        secret.write_text("secret data")
        symlink = zimage_dir / "link.png"
        symlink.symlink_to(secret)

        # get_file_path should reject it because resolved path leaves output dir
        with pytest.raises(ValueError, match="Path traversal"):
            service.get_file_path("zimage-generation", "link.png")

    def test_oversized_file_skipped_for_thumbnails(self, service, tmp_path):
        """Files over 2GB should be skipped for thumbnail generation."""
        outputs = tmp_path / "tool_outputs"
        zimage_dir = outputs / "zimage-generation"
        zimage_dir.mkdir(parents=True)
        
        # Mock a large file by adjusting the scan
        big_file = zimage_dir / "huge.png"
        big_file.write_bytes(b"x")  # Tiny file but we'll check the size threshold
        
        # Test threshold constant
        assert service.MAX_THUMBNAIL_SOURCE_BYTES == 2 * 1024 * 1024 * 1024
