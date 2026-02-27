"""Tests for GPULifecycleService — multi-GPU eviction, ComfyUI integration.

Tests the service registry building, GPU overlap eviction logic, and
individual eviction strategies (Ollama, unload, process stop, ComfyUI).
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock, PropertyMock
import httpx


# =============================================================================
# Helper: build a mock settings object for GPU lifecycle tests
# =============================================================================

def _mock_settings(
    use_ollama=True,
    use_zimage=True,
    use_qwen3_tts=True,
    use_acestep=True,
    gpu_ollama="0",
    gpu_zimage="0",
    gpu_qwen3_tts="0",
    gpu_acestep="0",
    comfyui_tools="",
    ollama_base_url="http://localhost:11434",
    zimage_api_url="http://localhost:8003",
    qwen3_tts_api_url="http://localhost:8002",
    acestep_api_url="http://localhost:8001",
    acestep_api_port=8001,
):
    """Create a mock settings object with GPU-related attributes."""
    from app.core.config import _parse_gpu_indices

    s = MagicMock()
    s.use_ollama = use_ollama
    s.use_zimage = use_zimage
    s.use_qwen3_tts = use_qwen3_tts
    s.use_acestep = use_acestep
    s.ollama_base_url = ollama_base_url
    s.zimage_api_url = zimage_api_url
    s.qwen3_tts_api_url = qwen3_tts_api_url
    s.acestep_api_url = acestep_api_url
    s.acestep_api_port = acestep_api_port
    s.gpu_ollama_indices = _parse_gpu_indices(gpu_ollama)
    s.gpu_zimage_indices = _parse_gpu_indices(gpu_zimage)
    s.gpu_qwen3_tts_indices = _parse_gpu_indices(gpu_qwen3_tts)
    s.gpu_acestep_indices = _parse_gpu_indices(gpu_acestep)
    s.use_gpu = True

    # Parse comfyui_tools the same way config does
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


def _build_service_with_settings(**kwargs):
    """Build a GPULifecycleService with mocked settings."""
    mock_s = _mock_settings(**kwargs)
    with patch("app.services.gpu_lifecycle_service.settings", mock_s):
        from app.services.gpu_lifecycle_service import GPULifecycleService
        return GPULifecycleService()


# =============================================================================
# Service Registry Building Tests
# =============================================================================


class TestBuildSlugToService:
    """Test _build_slug_to_service: maps tool slugs to service keys."""

    def test_base_slugs_always_present(self):
        mock_s = _mock_settings()
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_slug_to_service
            mapping = _build_slug_to_service()
        assert mapping["ollama-llm"] == "ollama"
        assert mapping["zimage-generation"] == "zimage"
        assert mapping["qwen3-tts-voice"] == "qwen3-tts"
        assert mapping["acestep-music-generation"] == "acestep"

    def test_comfyui_tools_added(self):
        mock_s = _mock_settings(
            comfyui_tools="ltx-2|LTX-2 Video|9902|http://localhost:8189|0"
        )
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_slug_to_service
            mapping = _build_slug_to_service()
        assert mapping["comfy-ltx-2"] == "comfyui:http://localhost:8189"

    def test_two_comfyui_tools_same_server(self):
        """Two wrapper APIs sharing one ComfyUI server get same service key."""
        mock_s = _mock_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8189|0"
        )
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_slug_to_service
            mapping = _build_slug_to_service()
        assert mapping["comfy-ltx-2"] == mapping["comfy-wan"]
        assert mapping["comfy-ltx-2"] == "comfyui:http://localhost:8189"


class TestBuildServiceConfig:
    """Test _build_service_config: builds full service configs with GPU affinity."""

    def test_four_base_services(self):
        mock_s = _mock_settings()
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        assert "ollama" in services
        assert "zimage" in services
        assert "qwen3-tts" in services
        assert "acestep" in services

    def test_ollama_config(self):
        mock_s = _mock_settings(gpu_ollama="1")
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        cfg = services["ollama"]
        assert cfg["gpu_indices"] == [1]
        assert cfg["type"] == "ollama_api"
        assert cfg["enabled"] is True

    def test_comfyui_server_added(self):
        mock_s = _mock_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        key = "comfyui:http://localhost:8189"
        assert key in services
        assert services[key]["type"] == "comfyui_free"
        assert services[key]["gpu_indices"] == [0]
        assert services[key]["free_url"] == "http://localhost:8189/free"

    def test_comfyui_gpu_indices_merged_from_multiple_tools(self):
        """Two tools sharing a server should have their gpu_indices UNIONED."""
        mock_s = _mock_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8189|1"
        )
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        key = "comfyui:http://localhost:8189"
        assert key in services
        assert services[key]["gpu_indices"] == [0, 1]

    def test_two_different_comfyui_servers(self):
        """Two different ComfyUI servers should be separate entries."""
        mock_s = _mock_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0;wan|WAN|9903|http://localhost:8190|1"
        )
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        assert "comfyui:http://localhost:8189" in services
        assert "comfyui:http://localhost:8190" in services

    def test_disabled_service(self):
        mock_s = _mock_settings(use_ollama=False)
        with patch("app.services.gpu_lifecycle_service.settings", mock_s):
            from app.services.gpu_lifecycle_service import _build_service_config
            services = _build_service_config()
        assert services["ollama"]["enabled"] is False


# =============================================================================
# GPULifecycleService Tests
# =============================================================================


class TestGPULifecycleServiceInit:
    """Test service initialization."""

    def test_init_loads_services(self):
        svc = _build_service_with_settings()
        assert "ollama" in svc._services
        assert "zimage" in svc._services

    def test_service_for_slug(self):
        svc = _build_service_with_settings()
        assert svc._service_for_slug("ollama-llm") == "ollama"
        assert svc._service_for_slug("zimage-generation") == "zimage"
        assert svc._service_for_slug("nonexistent") is None

    def test_get_gpu_indices_for_slug(self):
        svc = _build_service_with_settings(gpu_ollama="1,2")
        assert svc.get_gpu_indices_for_slug("ollama-llm") == [1, 2]

    def test_get_gpu_indices_for_unknown_slug(self):
        svc = _build_service_with_settings()
        assert svc.get_gpu_indices_for_slug("nonexistent") == [0]

    def test_comfyui_slug_mapping(self):
        svc = _build_service_with_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        assert svc._service_for_slug("comfy-ltx-2") == "comfyui:http://localhost:8189"
        assert svc.get_gpu_indices_for_slug("comfy-ltx-2") == [0]


# =============================================================================
# GPU Overlap Eviction Logic
# =============================================================================


class TestPrepareGpuForTool:
    """Test prepare_gpu_for_tool: only evicts services with overlapping GPUs."""

    @pytest.fixture
    def multi_gpu_service(self):
        """Service with Ollama on GPU-0 and Z-Image on GPU-1."""
        return _build_service_with_settings(
            gpu_ollama="0",
            gpu_zimage="1",
            gpu_qwen3_tts="0",
            gpu_acestep="0",
        )

    async def test_unknown_slug_returns_error(self):
        svc = _build_service_with_settings()
        result = await svc.prepare_gpu_for_tool("nonexistent")
        assert "error" in result

    async def test_no_eviction_for_non_overlapping_gpus(self, multi_gpu_service):
        """Z-Image on GPU-1 should NOT evict Ollama on GPU-0."""
        svc = multi_gpu_service
        with patch.object(svc, "_evict_service", new_callable=AsyncMock) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("zimage-generation")
            # Should NOT evict Ollama (GPU-0 doesn't overlap GPU-1)
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "ollama" not in evicted_keys

    async def test_eviction_for_overlapping_gpus(self, multi_gpu_service):
        """Qwen3-TTS on GPU-0 should evict Ollama on GPU-0."""
        svc = multi_gpu_service
        with patch.object(svc, "_evict_service", new_callable=AsyncMock, return_value={"status": "evicted"}) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("qwen3-tts-voice")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "ollama" in evicted_keys

    async def test_zimage_on_gpu1_only_evicts_gpu1_services(self):
        """Only services on GPU-1 should be evicted when running Z-Image on GPU-1."""
        svc = _build_service_with_settings(
            gpu_ollama="0",
            gpu_zimage="1",
            gpu_qwen3_tts="1",  # TTS also on GPU-1
            gpu_acestep="0",
        )
        with patch.object(svc, "_evict_service", new_callable=AsyncMock, return_value={"status": "evicted"}) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("zimage-generation")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            # Should evict qwen3-tts (also on GPU-1) but NOT ollama or acestep (GPU-0)
            assert "qwen3-tts" in evicted_keys
            assert "ollama" not in evicted_keys
            assert "acestep" not in evicted_keys

    async def test_comfyui_evicted_on_gpu_overlap(self):
        """ComfyUI on GPU-0 should be evicted when Ollama (GPU-0) runs."""
        svc = _build_service_with_settings(
            gpu_ollama="0",
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0",
        )
        with patch.object(svc, "_evict_service", new_callable=AsyncMock, return_value={"status": "evicted"}) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "comfyui:http://localhost:8189" in evicted_keys

    async def test_comfyui_not_evicted_different_gpu(self):
        """ComfyUI on GPU-1 should NOT be evicted when Ollama (GPU-0) runs."""
        svc = _build_service_with_settings(
            gpu_ollama="0",
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|1",
        )
        with patch.object(svc, "_evict_service", new_callable=AsyncMock) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "comfyui:http://localhost:8189" not in evicted_keys

    async def test_disabled_service_not_evicted(self):
        """Disabled services should not be evicted."""
        svc = _build_service_with_settings(use_zimage=False)
        with patch.object(svc, "_evict_service", new_callable=AsyncMock) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "zimage" not in evicted_keys

    async def test_self_not_evicted(self):
        """A service should never try to evict itself."""
        svc = _build_service_with_settings()
        with patch.object(svc, "_evict_service", new_callable=AsyncMock) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            assert "ollama" not in evicted_keys

    async def test_eviction_result_includes_gpu_overlap(self):
        """Eviction results should have gpu_overlap set."""
        svc = _build_service_with_settings(
            gpu_ollama="0,1",
            gpu_zimage="1,2",
        )
        with patch.object(
            svc, "_evict_service",
            new_callable=AsyncMock,
            # Must return a NEW dict each call — shared dict gets mutated by later iterations
            side_effect=lambda *a, **kw: {"status": "evicted"},
        ), patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            # Z-Image overlaps on GPU-1
            assert "zimage" in result["evictions"]
            assert result["evictions"]["zimage"]["gpu_overlap"] == [1]

    async def test_multi_gpu_tool_evicts_all_overlapping(self):
        """A tool on GPU 0,1 should evict services on GPU-0 AND GPU-1."""
        svc = _build_service_with_settings(
            gpu_ollama="0",
            gpu_zimage="1",
            gpu_qwen3_tts="0",
            gpu_acestep="1",
            comfyui_tools="big-render|Big Render|9999|http://localhost:8189|0,1",
        )
        with patch.object(svc, "_evict_service", new_callable=AsyncMock, return_value={"status": "evicted"}) as mock_evict, \
             patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc.prepare_gpu_for_tool("comfy-big-render")
            evicted_keys = [call.args[0] for call in mock_evict.call_args_list]
            # All four base services should be evicted (all overlap with GPU 0 or 1)
            assert "ollama" in evicted_keys
            assert "zimage" in evicted_keys
            assert "qwen3-tts" in evicted_keys
            assert "acestep" in evicted_keys

    async def test_vram_free_result_included(self):
        """Result should include vram_free status."""
        svc = _build_service_with_settings()
        with patch.object(svc, "_evict_service", new_callable=AsyncMock, return_value={"status": "evicted"}), \
             patch.object(svc, "_wait_for_vram_free", new_callable=AsyncMock, return_value=True):
            result = await svc.prepare_gpu_for_tool("ollama-llm")
            assert result["vram_free"] is True


# =============================================================================
# Individual Eviction Strategy Tests
# =============================================================================


class TestEvictOllama:
    """Test _evict_ollama strategy."""

    async def test_unloads_models(self):
        svc = _build_service_with_settings()
        config = svc._services["ollama"]

        mock_ps_response = MagicMock()
        mock_ps_response.status_code = 200
        mock_ps_response.json.return_value = {
            "models": [{"name": "mistral:7b"}, {"name": "qwen2.5:14b"}]
        }
        mock_gen_response = MagicMock()
        mock_gen_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_ps_response)
        mock_client.post = AsyncMock(return_value=mock_gen_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_ollama(config)

        assert result["status"] == "evicted"
        assert result["models_unloaded"] == 2
        assert "mistral:7b" in result["models"]

    async def test_no_models_loaded(self):
        svc = _build_service_with_settings()
        config = svc._services["ollama"]

        mock_ps = MagicMock()
        mock_ps.status_code = 200
        mock_ps.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_ps)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_ollama(config)

        assert result["status"] == "already_clear"

    async def test_not_running(self):
        svc = _build_service_with_settings()
        config = svc._services["ollama"]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_ollama(config)

        assert result["status"] == "not_running"


class TestEvictViaUnload:
    """Test _evict_via_unload strategy (Z-Image, Qwen3-TTS)."""

    async def test_successful_unload(self):
        svc = _build_service_with_settings()
        config = svc._services["zimage"]

        mock_health = MagicMock(status_code=200)
        mock_health.json.return_value = {"model_loaded": True}
        mock_unload = MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_health)
        mock_client.post = AsyncMock(return_value=mock_unload)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_via_unload("zimage", config)

        assert result["status"] == "evicted"

    async def test_already_unloaded(self):
        """Even if model_loaded=False, /unload is still called (idempotent)."""
        svc = _build_service_with_settings()
        config = svc._services["zimage"]

        mock_health = MagicMock(status_code=200)
        mock_health.json.return_value = {"model_loaded": False}

        mock_unload = MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_health)
        mock_client.post = AsyncMock(return_value=mock_unload)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_via_unload("zimage", config)

        assert result["status"] == "evicted"

    async def test_not_running(self):
        svc = _build_service_with_settings()
        config = svc._services["qwen3-tts"]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_via_unload("qwen3-tts", config)

        assert result["status"] == "not_running"


class TestEvictComfyUI:
    """Test _evict_comfyui strategy."""

    def _make_service_with_comfyui(self):
        return _build_service_with_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )

    async def test_successful_free(self):
        svc = self._make_service_with_comfyui()
        config = svc._services["comfyui:http://localhost:8189"]

        mock_health = MagicMock(status_code=200)
        mock_free = MagicMock(status_code=200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_health)
        mock_client.post = AsyncMock(return_value=mock_free)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_comfyui(config)

        assert result["status"] == "evicted"
        # Verify correct payload sent (headers may include auth key if configured)
        mock_client.post.assert_called_once_with(
            "http://localhost:8189/free",
            json={"unload_models": True, "free_memory": True},
            headers=mock_client.post.call_args.kwargs.get("headers", {}),
        )

    async def test_comfyui_not_running(self):
        svc = self._make_service_with_comfyui()
        config = svc._services["comfyui:http://localhost:8189"]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_comfyui(config)

        assert result["status"] == "not_running"

    async def test_comfyui_free_error(self):
        svc = self._make_service_with_comfyui()
        config = svc._services["comfyui:http://localhost:8189"]

        mock_health = MagicMock(status_code=200)
        mock_free = MagicMock(status_code=500)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_health)
        mock_client.post = AsyncMock(return_value=mock_free)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.gpu_lifecycle_service.httpx.AsyncClient", return_value=mock_client):
            result = await svc._evict_comfyui(config)

        assert result["status"] == "error"
        assert result["http_status"] == 500

    async def test_no_free_url(self):
        svc = self._make_service_with_comfyui()
        config = {"health_url": "http://localhost:8189/system_stats"}  # Missing free_url

        result = await svc._evict_comfyui(config)
        assert result["status"] == "error"


# =============================================================================
# VRAM Monitoring Tests
# =============================================================================


class TestGetFreeVramMb:
    """Test _get_free_vram_mb helper."""

    def test_single_gpu(self):
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "20480\n"

        with patch("app.services.gpu_lifecycle_service.subprocess.run", return_value=mock_result):
            assert _get_free_vram_mb(0) == 20480

    def test_multi_gpu_select_by_index(self):
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "20480\n16000\n8000\n"

        with patch("app.services.gpu_lifecycle_service.subprocess.run", return_value=mock_result):
            assert _get_free_vram_mb(0) == 20480
            assert _get_free_vram_mb(1) == 16000
            assert _get_free_vram_mb(2) == 8000

    def test_index_out_of_range(self):
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "20480\n"

        with patch("app.services.gpu_lifecycle_service.subprocess.run", return_value=mock_result):
            assert _get_free_vram_mb(5) is None

    def test_nvidia_smi_not_available(self):
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        with patch("app.services.gpu_lifecycle_service.subprocess.run", side_effect=FileNotFoundError):
            assert _get_free_vram_mb(0) is None

    def test_nvidia_smi_error_exit(self):
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("app.services.gpu_lifecycle_service.subprocess.run", return_value=mock_result):
            assert _get_free_vram_mb(0) is None


class TestWaitForVramFree:
    """Test _wait_for_vram_free with per-GPU checks."""

    async def test_immediate_free(self):
        svc = _build_service_with_settings()
        with patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=20000):
            result = await svc._wait_for_vram_free(gpu_indices=[0], timeout=5)
        assert result is True

    async def test_checks_all_gpus(self):
        """Should check each GPU in the list."""
        svc = _build_service_with_settings()
        call_count = {"value": 0}
        returned_values = {0: 20000, 1: 20000}

        def mock_vram(gpu_index=0):
            call_count["value"] += 1
            return returned_values.get(gpu_index, None)

        with patch("app.services.gpu_lifecycle_service._get_free_vram_mb", side_effect=mock_vram):
            result = await svc._wait_for_vram_free(gpu_indices=[0, 1], timeout=1)
        assert result is True
        # Should have checked at least both GPUs
        assert call_count["value"] >= 2

    async def test_one_gpu_not_free(self):
        """If one GPU has insufficient VRAM, should wait and eventually timeout."""
        svc = _build_service_with_settings()

        def mock_vram(gpu_index=0):
            if gpu_index == 0:
                return 20000  # Enough
            return 1000  # Not enough on GPU-1

        with patch("app.services.gpu_lifecycle_service._get_free_vram_mb", side_effect=mock_vram), \
             patch("asyncio.sleep", new_callable=AsyncMock):
            result = await svc._wait_for_vram_free(gpu_indices=[0, 1], timeout=2)
        assert result is False

    async def test_no_nvidia_passes(self):
        """If nvidia-smi is not available (returns None), treat as free."""
        svc = _build_service_with_settings()
        with patch("app.services.gpu_lifecycle_service._get_free_vram_mb", return_value=None):
            result = await svc._wait_for_vram_free(gpu_indices=[0], timeout=1)
        assert result is True


# =============================================================================
# ensure_service_running Tests
# =============================================================================


class TestEnsureServiceRunning:
    """Test ensure_service_running health checks."""

    async def test_unknown_slug(self):
        svc = _build_service_with_settings()
        result = await svc.ensure_service_running("nonexistent-slug")
        assert result is False

    async def test_disabled_service(self):
        svc = _build_service_with_settings(use_ollama=False)
        result = await svc.ensure_service_running("ollama-llm")
        assert result is False

    async def test_healthy_service(self):
        svc = _build_service_with_settings()
        with patch.object(svc, "_check_service_alive", new_callable=AsyncMock, return_value=True):
            result = await svc.ensure_service_running("ollama-llm")
        assert result is True

    async def test_unhealthy_service(self):
        svc = _build_service_with_settings()
        with patch.object(svc, "_check_service_alive", new_callable=AsyncMock, return_value=False):
            result = await svc.ensure_service_running("ollama-llm")
        assert result is False

    async def test_comfyui_tool_health(self):
        svc = _build_service_with_settings(
            comfyui_tools="ltx-2|LTX-2|9902|http://localhost:8189|0"
        )
        with patch.object(svc, "_check_service_alive", new_callable=AsyncMock, return_value=True):
            result = await svc.ensure_service_running("comfy-ltx-2")
        assert result is True


# =============================================================================
# get_gpu_lifecycle_service singleton
# =============================================================================


class TestGetGPULifecycleService:
    """Test the singleton accessor."""

    def test_returns_instance(self):
        with patch("app.services.gpu_lifecycle_service.settings", _mock_settings()):
            # Reset the singleton
            import app.services.gpu_lifecycle_service as mod
            mod._gpu_lifecycle_service = None
            svc = mod.get_gpu_lifecycle_service()
            assert isinstance(svc, mod.GPULifecycleService)

    def test_returns_same_instance(self):
        with patch("app.services.gpu_lifecycle_service.settings", _mock_settings()):
            import app.services.gpu_lifecycle_service as mod
            mod._gpu_lifecycle_service = None
            svc1 = mod.get_gpu_lifecycle_service()
            svc2 = mod.get_gpu_lifecycle_service()
            assert svc1 is svc2
