"""Tests for Media Library API endpoints — auth, listing, serving, security."""
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from app.core.security import create_access_token
from app.models import User
from app.services.media_library_service import (
    MediaLibraryService,
    ToolMediaSummary,
    MediaFile,
    MediaFileList,
    MediaStats,
    TOOL_MEDIA_REGISTRY,
    get_media_library_service,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Disable slowapi rate limiter for all tests in this file."""
    from app.core.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


@pytest_asyncio.fixture
async def auth_headers(test_user):
    """Authorization headers for a regular user."""
    token = create_access_token(data={"sub": str(test_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_headers(test_admin_user):
    """Authorization headers for an admin user."""
    token = create_access_token(data={"sub": str(test_admin_user.id)})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_service(tmp_path):
    """Create a real MediaLibraryService pointing at temp directories."""
    outputs = tmp_path / "tool_outputs"
    outputs.mkdir()
    cache = tmp_path / "media_cache" / "thumbnails"
    cache.mkdir(parents=True)

    svc = MediaLibraryService.__new__(MediaLibraryService)
    svc.TOOL_OUTPUTS_ROOT = outputs
    svc.THUMBNAIL_CACHE_DIR = cache

    # Create output dirs for a couple tools
    for slug in ["zimage-generation", "ltx-video-generation", "media-toolkit"]:
        (outputs / slug).mkdir()

    return svc


@pytest.fixture
def _patch_service(mock_service):
    """Patch get_media_library_service to return our mock service."""
    with patch(
        "app.api.endpoints.media_library.get_media_library_service",
        return_value=mock_service,
    ):
        yield mock_service


# =============================================================================
# Authentication Tests
# =============================================================================


class TestMediaLibraryAuth:
    """Endpoints should require authentication."""

    @pytest.mark.asyncio
    async def test_list_tools_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/media/tools")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_stats_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/media/stats")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_list_files_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/media/zimage-generation/files")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_file_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/media/zimage-generation/files/test.png")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_get_thumbnail_unauthenticated(self, async_client):
        response = await async_client.get("/api/v1/media/zimage-generation/files/test.png/thumbnail")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_generate_thumbnails_unauthenticated(self, async_client):
        response = await async_client.post("/api/v1/media/zimage-generation/thumbnails/generate")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_file_unauthenticated(self, async_client):
        response = await async_client.delete("/api/v1/media/zimage-generation/files/test.png")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_file_requires_admin(self, async_client, auth_headers):
        """Delete endpoint should reject non-admin users."""
        response = await async_client.delete(
            "/api/v1/media/zimage-generation/files/test.png",
            headers=auth_headers,
        )
        # Should be 403 (non-admin) or 401 depending on middleware
        assert response.status_code in (401, 403)


# =============================================================================
# List Tools Tests
# =============================================================================


class TestListTools:
    """Test GET /api/v1/media/tools."""

    @pytest.mark.asyncio
    async def test_list_tools_empty(self, async_client, auth_headers, _patch_service):
        response = await async_client.get("/api/v1/media/tools", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        # All registered tools should appear
        assert len(data) == len(TOOL_MEDIA_REGISTRY)

    @pytest.mark.asyncio
    async def test_list_tools_with_files(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        # Create some files
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "img1.png").write_bytes(b"x" * 100)
        (zdir / "img2.png").write_bytes(b"x" * 200)

        response = await async_client.get("/api/v1/media/tools", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        # Find zimage in response
        zimage = next(t for t in data if t["slug"] == "zimage-generation")
        assert zimage["file_count"] == 2
        assert zimage["total_size_bytes"] == 300


# =============================================================================
# List Files Tests
# =============================================================================


class TestListFiles:
    """Test GET /api/v1/media/{slug}/files."""

    @pytest.mark.asyncio
    async def test_list_files_basic(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "ZIMG_00001.png").write_bytes(b"image data")

        response = await async_client.get(
            "/api/v1/media/zimage-generation/files", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] == 1
        assert data["files"][0]["filename"] == "ZIMG_00001.png"
        assert data["files"][0]["media_type"] == "image"

    @pytest.mark.asyncio
    async def test_list_files_unknown_tool(self, async_client, auth_headers, _patch_service):
        response = await async_client.get(
            "/api/v1/media/nonexistent-tool/files", headers=auth_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_files_pagination(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        for i in range(5):
            (zdir / f"img_{i:03d}.png").write_bytes(b"x")

        response = await async_client.get(
            "/api/v1/media/zimage-generation/files?page=1&page_size=2",
            headers=auth_headers,
        )
        data = response.json()
        assert len(data["files"]) == 2
        assert data["total_count"] == 5
        assert data["has_more"] is True

    @pytest.mark.asyncio
    async def test_list_files_filter_by_type(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        mtdir = mock_service.TOOL_OUTPUTS_ROOT / "media-toolkit"
        (mtdir / "clip.mp4").write_bytes(b"video")
        (mtdir / "sound.wav").write_bytes(b"audio")

        response = await async_client.get(
            "/api/v1/media/media-toolkit/files?media_type=audio",
            headers=auth_headers,
        )
        data = response.json()
        assert data["total_count"] == 1
        assert data["files"][0]["extension"] == ".wav"


# =============================================================================
# Get File Tests
# =============================================================================


class TestGetFile:
    """Test GET /api/v1/media/{slug}/files/{filename}."""

    @pytest.mark.asyncio
    async def test_get_file_inline(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "ZIMG_00001.png").write_bytes(b"\x89PNG fake data")

        response = await async_client.get(
            "/api/v1/media/zimage-generation/files/ZIMG_00001.png",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert b"\x89PNG fake data" in response.content

    @pytest.mark.asyncio
    async def test_get_file_download(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "ZIMG_00001.png").write_bytes(b"image data")

        response = await async_client.get(
            "/api/v1/media/zimage-generation/files/ZIMG_00001.png?download=true",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert "attachment" in response.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_get_file_not_found(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.get(
            "/api/v1/media/zimage-generation/files/nonexistent.png",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_file_path_traversal(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.get(
            "/api/v1/media/zimage-generation/files/..%2F..%2Fetc%2Fpasswd",
            headers=auth_headers,
        )
        # %2F decoded as / creates extra path segments → route mismatch (404)
        # or handler rejects via _sanitize_filename (400). Either blocks traversal.
        assert response.status_code in (400, 404)

    @pytest.mark.asyncio
    async def test_get_file_unknown_tool(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.get(
            "/api/v1/media/bad-tool/files/file.png",
            headers=auth_headers,
        )
        assert response.status_code == 404


# =============================================================================
# Thumbnail Tests
# =============================================================================


class TestGetThumbnail:
    """Test GET /api/v1/media/{slug}/files/{filename}/thumbnail."""

    @pytest.mark.asyncio
    async def test_thumbnail_cached(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        # Create the source file and a cached thumbnail
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "ZIMG_00001.png").write_bytes(b"image data")
        mock_service.save_thumbnail("zimage-generation", "ZIMG_00001.png", b"thumb jpeg")

        response = await async_client.get(
            "/api/v1/media/zimage-generation/files/ZIMG_00001.png/thumbnail",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.content == b"thumb jpeg"

    @pytest.mark.asyncio
    async def test_thumbnail_unknown_tool(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.get(
            "/api/v1/media/bad-tool/files/file.png/thumbnail",
            headers=auth_headers,
        )
        assert response.status_code == 404


# =============================================================================
# Stats Tests
# =============================================================================


class TestGetStats:
    """Test GET /api/v1/media/stats."""

    @pytest.mark.asyncio
    async def test_stats_empty(self, async_client, auth_headers, _patch_service):
        response = await async_client.get("/api/v1/media/stats", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_files"] == 0
        assert data["total_size_bytes"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_files(
        self, async_client, auth_headers, _patch_service, mock_service
    ):
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "a.png").write_bytes(b"x" * 100)

        response = await async_client.get("/api/v1/media/stats", headers=auth_headers)
        data = response.json()
        assert data["total_files"] == 1
        assert data["total_size_bytes"] == 100
        assert data["by_type"]["image"] == 1


# =============================================================================
# Batch Thumbnail Generation Tests
# =============================================================================


class TestGenerateThumbnails:
    """Test POST /api/v1/media/{slug}/thumbnails/generate."""

    @pytest.mark.asyncio
    async def test_generate_all_up_to_date(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.post(
            "/api/v1/media/zimage-generation/thumbnails/generate",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_generate_unknown_tool(
        self, async_client, auth_headers, _patch_service
    ):
        response = await async_client.post(
            "/api/v1/media/bad-tool/thumbnails/generate",
            headers=auth_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.endpoints.media_library.generate_thumbnail")
    async def test_generate_with_files(
        self, mock_gen, async_client, auth_headers, _patch_service, mock_service
    ):
        """When files need thumbnails, the endpoint should generate them."""
        zdir = mock_service.TOOL_OUTPUTS_ROOT / "zimage-generation"
        (zdir / "a.png").write_bytes(b"x" * 100)

        mock_gen.return_value = b"thumb data"

        response = await async_client.post(
            "/api/v1/media/zimage-generation/thumbnails/generate",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["generated"] == 1

        # Thumbnail should be cached
        thumb = mock_service.get_thumbnail_path("zimage-generation", "a.png")
        assert thumb is not None


# =============================================================================
# Delete Endpoint Tests
# =============================================================================


class TestDeleteFile:
    """Test DELETE /api/v1/media/{slug}/files/{filename}."""

    @pytest.mark.asyncio
    async def test_delete_requires_admin(self, async_client, auth_headers, _patch_service):
        response = await async_client.delete(
            "/api/v1/media/zimage-generation/files/test.png",
            headers=auth_headers,
        )
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_delete_unknown_tool(self, async_client, admin_headers, _patch_service):
        response = await async_client.delete(
            "/api/v1/media/bad-tool/files/file.png",
            headers=admin_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_file_not_found(
        self, async_client, admin_headers, _patch_service
    ):
        response = await async_client.delete(
            "/api/v1/media/zimage-generation/files/nonexistent.png",
            headers=admin_headers,
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_path_traversal(
        self, async_client, admin_headers, _patch_service
    ):
        response = await async_client.delete(
            "/api/v1/media/zimage-generation/files/..%2F..%2Fetc%2Fpasswd",
            headers=admin_headers,
        )
        # %2F decoded as / creates extra path segments → route mismatch (404)
        # or handler rejects via _sanitize_filename (400). Either blocks traversal.
        assert response.status_code in (400, 404)
