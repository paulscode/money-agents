"""Tests for startup_service.py — GPU tool linking, ComfyUI tool sync.

Uses real in-memory SQLite DB fixtures from conftest.py for integration testing
of sync_comfyui_tools() and link_gpu_tools_to_resource().
"""
import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4, UUID

from app.core.config import _parse_gpu_indices
from app.models import Tool, ToolStatus, ToolCategory, User
from app.models.resource import Resource
from app.schemas.resource import ResourceStatus, ResourceType


# =============================================================================
# Helpers
# =============================================================================

def _mock_settings_for_startup(
    use_gpu=True,
    use_ollama=True,
    use_zimage=True,
    use_qwen3_tts=True,
    use_acestep=True,
    gpu_ollama="0",
    gpu_zimage="0",
    gpu_qwen3_tts="0",
    gpu_acestep="0",
    comfyui_tools="",
):
    """Build mock settings for startup service tests."""
    s = MagicMock()
    s.use_gpu = use_gpu
    s.use_ollama = use_ollama
    s.use_zimage = use_zimage
    s.use_qwen3_tts = use_qwen3_tts
    s.use_acestep = use_acestep
    s.gpu_ollama_indices = _parse_gpu_indices(gpu_ollama)
    s.gpu_zimage_indices = _parse_gpu_indices(gpu_zimage)
    s.gpu_qwen3_tts_indices = _parse_gpu_indices(gpu_qwen3_tts)
    s.gpu_acestep_indices = _parse_gpu_indices(gpu_acestep)

    # Parse comfyui_tools
    entries = []
    if comfyui_tools:
        for entry in comfyui_tools.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("|")
            if len(parts) >= 5:
                name = parts[0].strip()
                entries.append({
                    "slug": f"comfy-{name}",
                    "name": name,
                    "display_name": parts[1].strip(),
                    "port": int(parts[2].strip()),
                    "comfyui_url": parts[3].strip(),
                    "gpu_indices": _parse_gpu_indices(parts[4]),
                })
    s.comfyui_tools_list = entries
    return s


async def _create_test_user(db) -> User:
    """Create a minimal test user for tool requester_id FK."""
    from app.core.security import get_password_hash
    user = User(
        username="gpu_test_user",
        email="gpu_test@test.com",
        password_hash=get_password_hash("testpass"),
        role="admin",
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _create_gpu_resource(db, index: int, name: str = None) -> Resource:
    """Create a GPU Resource record with the given index."""
    if name is None:
        name = f"GPU-{index} (Test GPU)"
    gpu = Resource(
        id=uuid4(),
        name=name,
        resource_type=ResourceType.GPU,
        category="compute",
        status=ResourceStatus.AVAILABLE,
        is_system_resource=True,
        resource_metadata={"index": index, "name": f"Test GPU {index}", "memory_mb": 24000},
    )
    db.add(gpu)
    await db.commit()
    await db.refresh(gpu)
    return gpu


async def _create_tool(db, user, slug: str, status=ToolStatus.IMPLEMENTED) -> Tool:
    """Create a minimal Tool record."""
    tool = Tool(
        id=uuid4(),
        name=f"Test {slug}",
        slug=slug,
        category=ToolCategory.API,
        status=status,
        description=f"Test tool for {slug}",
        requester_id=user.id,
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return tool


# =============================================================================
# get_gpu_tool_slugs() Tests
# =============================================================================


class TestGetGpuToolSlugs:
    """Test get_gpu_tool_slugs dynamic slug list."""

    def test_base_slugs_without_comfyui(self):
        mock_s = _mock_settings_for_startup(comfyui_tools="")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import get_gpu_tool_slugs
            slugs = get_gpu_tool_slugs()
        assert "ollama-llm" in slugs
        assert "acestep-music-generation" in slugs
        assert "qwen3-tts-voice" in slugs
        assert "zimage-generation" in slugs
        assert "seedvr2-upscaler" in slugs
        assert "canary-stt" in slugs
        assert "audiosr-enhance" in slugs
        assert "ltx-video-generation" in slugs
        assert len(slugs) == 8

    def test_includes_comfyui_slugs(self):
        mock_s = _mock_settings_for_startup(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8190|1"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import get_gpu_tool_slugs
            slugs = get_gpu_tool_slugs()
        assert "comfy-ltx-2" in slugs
        assert "comfy-wan" in slugs
        assert len(slugs) == 10  # 8 base + 2 comfyui


# =============================================================================
# sync_comfyui_tools() Tests — Uses real DB
# =============================================================================


class TestSyncComfyuiTools:
    """Test sync_comfyui_tools creates/updates Tool records from env config."""

    async def test_no_comfyui_tools_returns_zero(self, db_session):
        mock_s = _mock_settings_for_startup(comfyui_tools="")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            result = await sync_comfyui_tools(db_session)
        assert result == 0

    async def test_creates_new_tool(self, db_session):
        mock_s = _mock_settings_for_startup(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            result = await sync_comfyui_tools(db_session)
        assert result == 1

        # Verify tool was created correctly
        from sqlalchemy import select
        stmt = select(Tool).where(Tool.slug == "comfy-ltx-2")
        tool = (await db_session.execute(stmt)).scalar_one_or_none()
        assert tool is not None
        assert tool.interface_type == "rest_api"
        assert "base_url" in tool.interface_config
        assert "9902" in tool.interface_config["base_url"]
        assert tool.status == ToolStatus.IMPLEMENTED

    async def test_creates_multiple_tools(self, db_session):
        mock_s = _mock_settings_for_startup(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8190|1"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            result = await sync_comfyui_tools(db_session)
        assert result == 2

    async def test_updates_existing_tool(self, db_session):
        """If a comfy tool already exists, update its interface_config."""
        user = await _create_test_user(db_session)
        tool = await _create_tool(db_session, user, "comfy-ltx-2")
        assert tool.interface_type is None

        mock_s = _mock_settings_for_startup(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            result = await sync_comfyui_tools(db_session)
        assert result == 1

        await db_session.refresh(tool)
        assert tool.interface_type == "rest_api"
        assert tool.interface_config is not None

    async def test_reactivates_deprecated_tool(self, db_session):
        """A deprecated ComfyUI tool should be re-implemented if back in config."""
        user = await _create_test_user(db_session)
        tool = await _create_tool(db_session, user, "comfy-ltx-2", status=ToolStatus.DEPRECATED)

        mock_s = _mock_settings_for_startup(
            use_gpu=True,
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            await sync_comfyui_tools(db_session)

        await db_session.refresh(tool)
        assert tool.status == ToolStatus.IMPLEMENTED

    async def test_deprecates_removed_tool(self, db_session):
        """A comfy-* tool not in config should be deprecated."""
        user = await _create_test_user(db_session)
        # Create an old ComfyUI tool that's no longer configured
        tool = await _create_tool(db_session, user, "comfy-removed-workflow")

        # Config only has ltx-2 (not comfy-removed-workflow)
        mock_s = _mock_settings_for_startup(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            await sync_comfyui_tools(db_session)
            await db_session.commit()

        await db_session.refresh(tool)
        assert tool.status == ToolStatus.DEPRECATED

    async def test_gpu_disabled_creates_deprecated_tool(self, db_session):
        """If GPU is disabled, new ComfyUI tools should be created as DEPRECATED."""
        mock_s = _mock_settings_for_startup(
            use_gpu=False,
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools
            await sync_comfyui_tools(db_session)

        from sqlalchemy import select
        stmt = select(Tool).where(Tool.slug == "comfy-ltx-2")
        tool = (await db_session.execute(stmt)).scalar_one_or_none()
        assert tool is not None
        assert tool.status == ToolStatus.DEPRECATED


# =============================================================================
# link_gpu_tools_to_resource() Tests — Uses real DB
# =============================================================================


class TestLinkGpuToolsToResource:
    """Test link_gpu_tools_to_resource: multi-GPU aware tool-resource linking."""

    async def test_no_gpu_resources_returns_zero(self, db_session):
        mock_s = _mock_settings_for_startup()
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            result = await link_gpu_tools_to_resource(db_session)
        assert result == 0

    async def test_links_tool_to_gpu0(self, db_session):
        """Ollama (gpu_ollama=0) should be linked to GPU-0 resource."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0)
        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked = await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        assert linked >= 1
        assert tool.resource_ids is not None
        assert str(gpu0.id) in tool.resource_ids

    async def test_links_tool_to_gpu1(self, db_session):
        """Z-Image (gpu_zimage=1) should be linked to GPU-1 resource."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (RTX 3090)")
        gpu1 = await _create_gpu_resource(db_session, 1, "GPU-1 (RTX 4090)")
        tool = await _create_tool(db_session, user, "zimage-generation")

        mock_s = _mock_settings_for_startup(gpu_zimage="1")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked = await link_gpu_tools_to_resource(db_session)

        # Flush + refresh to ensure DB round-trip
        await db_session.flush()
        await db_session.refresh(tool)
        assert linked >= 1
        assert str(gpu1.id) in tool.resource_ids
        assert str(gpu0.id) not in tool.resource_ids

    async def test_links_tool_to_multiple_gpus(self, db_session):
        """A tool configured for GPU 0,1 should have both resource IDs."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (test)")
        gpu1 = await _create_gpu_resource(db_session, 1, "GPU-1 (test)")
        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0,1")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        assert len(tool.resource_ids) == 2
        assert str(gpu0.id) in tool.resource_ids
        assert str(gpu1.id) in tool.resource_ids

    async def test_resource_ids_are_sorted(self, db_session):
        """resource_ids should always be sorted for deadlock prevention."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (sorted)")
        gpu1 = await _create_gpu_resource(db_session, 1, "GPU-1 (sorted)")
        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0,1")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        assert tool.resource_ids == sorted(tool.resource_ids)

    async def test_resource_id_set_to_first(self, db_session):
        """tool.resource_id should be set to the first resource for backwards compat."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (compat)")
        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        assert str(tool.resource_id) == tool.resource_ids[0]

    async def test_fallback_when_configured_gpu_missing(self, db_session):
        """If configured GPU index doesn't exist, should fallback to first available."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (fallback)")
        tool = await _create_tool(db_session, user, "ollama-llm")

        # Ollama configured for GPU-3 which doesn't exist
        mock_s = _mock_settings_for_startup(gpu_ollama="3")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked = await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        assert linked >= 1
        # Should fallback to GPU-0
        assert str(gpu0.id) in tool.resource_ids

    async def test_skips_non_implemented_tool(self, db_session):
        """Only IMPLEMENTED tools should be linked."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (skip)")
        tool = await _create_tool(db_session, user, "ollama-llm", status=ToolStatus.DEPRECATED)

        mock_s = _mock_settings_for_startup()
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked = await link_gpu_tools_to_resource(db_session)

        await db_session.flush()
        await db_session.refresh(tool)
        # Tool is DEPRECATED so should not be linked
        assert tool.resource_ids is None or len(tool.resource_ids) == 0

    async def test_links_comfyui_tool_to_gpu(self, db_session):
        """ComfyUI tools from comfyui_tools_list should be linked to correct GPU."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (comfy)")
        gpu1 = await _create_gpu_resource(db_session, 1, "GPU-1 (comfy)")

        # First sync the ComfyUI tool to create the Tool record
        mock_s = _mock_settings_for_startup(
            use_gpu=True,
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|1",
        )
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import sync_comfyui_tools, link_gpu_tools_to_resource
            await sync_comfyui_tools(db_session)
            await db_session.commit()
            linked = await link_gpu_tools_to_resource(db_session)

        from sqlalchemy import select
        stmt = select(Tool).where(Tool.slug == "comfy-ltx-2")
        tool = (await db_session.execute(stmt)).scalar_one_or_none()
        assert tool is not None
        # Should be linked to GPU-1
        assert str(gpu1.id) in tool.resource_ids
        assert str(gpu0.id) not in tool.resource_ids

    async def test_no_change_on_repeat(self, db_session):
        """Running link twice with same config should return 0 on second run."""
        user = await _create_test_user(db_session)
        gpu0 = await _create_gpu_resource(db_session, 0, "GPU-0 (repeat)")
        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked1 = await link_gpu_tools_to_resource(db_session)
            await db_session.commit()
            linked2 = await link_gpu_tools_to_resource(db_session)

        assert linked1 >= 1
        assert linked2 == 0  # No changes needed

    async def test_disabled_gpu_resource_skipped(self, db_session):
        """Disabled GPU resources should not be in the gpu_map."""
        user = await _create_test_user(db_session)
        gpu0 = Resource(
            id=uuid4(),
            name="GPU-0 (disabled test)",
            resource_type=ResourceType.GPU,
            category="compute",
            status=ResourceStatus.DISABLED,
            is_system_resource=True,
            resource_metadata={"index": 0},
        )
        db_session.add(gpu0)
        await db_session.commit()

        tool = await _create_tool(db_session, user, "ollama-llm")

        mock_s = _mock_settings_for_startup(gpu_ollama="0")
        with patch("app.services.startup_service.settings", mock_s):
            from app.services.startup_service import link_gpu_tools_to_resource
            linked = await link_gpu_tools_to_resource(db_session)
        assert linked == 0
