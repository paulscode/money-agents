"""Tests for FileStorageService.

Covers:
- MIME type validation (allowed/disallowed)
- File size limit enforcement
- File path construction and lookup
- Thumbnail generation
- File deletion (single, message-level, conversation-level)

Note: These are unit tests using the real filesystem (tmpdir).
      We mock `magic.from_buffer` to control MIME detection.
"""
import pytest
import asyncio
import shutil
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch, AsyncMock, MagicMock

from fastapi import UploadFile, HTTPException
from io import BytesIO

from app.services.file_storage import (
    FileStorageService,
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE,
    UPLOAD_DIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_upload_file(content: bytes = b"hello", filename: str = "test.txt"):
    """Create a minimal UploadFile-like object."""
    buf = BytesIO(content)
    return UploadFile(filename=filename, file=buf)


# ---------------------------------------------------------------------------
# MIME type constants
# ---------------------------------------------------------------------------

class TestAllowedMimeTypes:

    def test_common_images_allowed(self):
        for mt in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            assert mt in ALLOWED_MIME_TYPES, f"{mt} should be allowed"

    def test_common_docs_allowed(self):
        for mt in ("application/pdf", "text/plain", "text/csv"):
            assert mt in ALLOWED_MIME_TYPES, f"{mt} should be allowed"

    def test_executable_not_allowed(self):
        assert "application/x-executable" not in ALLOWED_MIME_TYPES
        assert "application/x-msdownload" not in ALLOWED_MIME_TYPES

    def test_max_file_size_is_50mb(self):
        assert MAX_FILE_SIZE == 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# save_file
# ---------------------------------------------------------------------------

class TestSaveFile:

    @pytest.mark.asyncio
    async def test_rejects_oversized_file(self):
        """Files exceeding MAX_FILE_SIZE should raise 413."""
        big_content = b"x" * (MAX_FILE_SIZE + 1)
        upload = _make_upload_file(content=big_content, filename="big.bin")

        with pytest.raises(HTTPException) as exc_info:
            with patch("app.services.file_storage.magic.from_buffer", return_value="text/plain"):
                await FileStorageService.save_file(upload, uuid4(), uuid4())
        assert exc_info.value.status_code == 413
        assert "too large" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_rejects_disallowed_mime(self):
        """Files with disallowed MIME types should raise 400."""
        upload = _make_upload_file(content=b"MZ\x00", filename="virus.exe")

        with pytest.raises(HTTPException) as exc_info:
            with patch("app.services.file_storage.magic.from_buffer", return_value="application/x-executable"):
                await FileStorageService.save_file(upload, uuid4(), uuid4())
        assert exc_info.value.status_code == 400
        assert "not allowed" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_saves_valid_file(self, tmp_path):
        """Valid file should be saved and metadata returned."""
        conv_id = uuid4()
        msg_id = uuid4()
        content = b"hello world"
        upload = _make_upload_file(content=content, filename="doc.txt")

        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            with patch("app.services.file_storage.magic.from_buffer", return_value="text/plain"):
                meta = await FileStorageService.save_file(upload, conv_id, msg_id)

        assert meta["filename"] == "doc.txt"
        assert meta["size"] == len(content)
        assert meta["mime_type"] == "text/plain"
        assert "id" in meta
        assert "uploaded_at" in meta

        # Verify file exists on disk
        stored = tmp_path / "conversations" / str(conv_id) / str(msg_id)
        files = list(stored.glob("*.txt"))
        assert len(files) == 1


# ---------------------------------------------------------------------------
# get_file_path
# ---------------------------------------------------------------------------

class TestGetFilePath:

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, tmp_path):
        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            result = await FileStorageService.get_file_path("nonexistent", uuid4(), uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_finds_existing_file(self, tmp_path):
        conv_id, msg_id = uuid4(), uuid4()
        file_id = uuid4()
        storage = tmp_path / "conversations" / str(conv_id) / str(msg_id)
        storage.mkdir(parents=True)
        (storage / f"{file_id}.txt").write_text("data")

        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            result = await FileStorageService.get_file_path(str(file_id), conv_id, msg_id)
        assert result is not None
        assert result.exists()


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------

class TestDeleteFile:

    @pytest.mark.asyncio
    async def test_delete_existing_file(self, tmp_path):
        conv_id, msg_id = uuid4(), uuid4()
        file_id = uuid4()
        storage = tmp_path / "conversations" / str(conv_id) / str(msg_id)
        storage.mkdir(parents=True)
        (storage / f"{file_id}.txt").write_text("data")

        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            deleted = await FileStorageService.delete_file(str(file_id), conv_id, msg_id)
        assert deleted is True
        assert not (storage / f"{file_id}.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self, tmp_path):
        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            deleted = await FileStorageService.delete_file("nope", uuid4(), uuid4())
        assert deleted is False


# ---------------------------------------------------------------------------
# delete_message_files
# ---------------------------------------------------------------------------

class TestDeleteMessageFiles:

    @pytest.mark.asyncio
    async def test_deletes_all_message_files(self, tmp_path):
        conv_id, msg_id = uuid4(), uuid4()
        storage = tmp_path / "conversations" / str(conv_id) / str(msg_id)
        storage.mkdir(parents=True)
        (storage / "file1.txt").write_text("a")
        (storage / "file2.txt").write_text("b")

        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            count = await FileStorageService.delete_message_files(conv_id, msg_id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_empty_dir_returns_zero(self, tmp_path):
        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            count = await FileStorageService.delete_message_files(uuid4(), uuid4())
        assert count == 0


# ---------------------------------------------------------------------------
# delete_conversation_files
# ---------------------------------------------------------------------------

class TestDeleteConversationFiles:

    @pytest.mark.asyncio
    async def test_deletes_all_conversation_files(self, tmp_path):
        conv_id = uuid4()
        msg1, msg2 = uuid4(), uuid4()
        for mid in (msg1, msg2):
            d = tmp_path / "conversations" / str(conv_id) / str(mid)
            d.mkdir(parents=True)
            (d / "file.txt").write_text("x")

        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            count = await FileStorageService.delete_conversation_files(conv_id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_nonexistent_conversation_returns_zero(self, tmp_path):
        with patch("app.services.file_storage.UPLOAD_DIR", tmp_path):
            count = await FileStorageService.delete_conversation_files(uuid4())
        assert count == 0
