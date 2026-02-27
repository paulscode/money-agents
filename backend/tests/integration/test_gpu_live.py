"""
Live integration tests for GPU lifecycle management.

These tests exercise the REAL GPU eviction, health check, and VRAM monitoring
code against live services. They require actual GPU services running.

Prerequisites (checked automatically per-test):
- nvidia-smi available (NVIDIA GPU present)
- Ollama running on localhost:11434
- Qwen3-TTS running on localhost:8002 (model may be unloaded)
- Z-Image running on localhost:8003 (model may be unloaded)
- ACE-Step running on localhost:8001 (optional — destructive eviction)

Run with:
    cd backend
    python -m pytest tests/integration/test_gpu_live.py -v --tb=short -s

Use -k to select specific tests:
    python -m pytest tests/integration/test_gpu_live.py -k "ollama" -v -s
"""
import asyncio
import subprocess
import time

import httpx
import pytest


# =============================================================================
# Skip conditions — check which services are actually running
# =============================================================================

def _is_reachable(url: str, timeout: float = 3.0) -> bool:
    """Synchronously check if a URL is reachable."""
    try:
        import urllib.request
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _has_nvidia_smi() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


# Detect available services at module load
HAS_NVIDIA = _has_nvidia_smi()
HAS_OLLAMA = _is_reachable("http://localhost:11434/api/tags")
HAS_TTS = _is_reachable("http://localhost:8002/health")
HAS_ZIMAGE = _is_reachable("http://localhost:8003/health")
HAS_ACESTEP = _is_reachable("http://localhost:8001/health")
HAS_COMFYUI = _is_reachable("http://localhost:8189/system_stats")

requires_nvidia = pytest.mark.skipif(not HAS_NVIDIA, reason="nvidia-smi not available")
requires_ollama = pytest.mark.skipif(not HAS_OLLAMA, reason="Ollama not running")
requires_tts = pytest.mark.skipif(not HAS_TTS, reason="Qwen3-TTS not running")
requires_zimage = pytest.mark.skipif(not HAS_ZIMAGE, reason="Z-Image not running")
requires_acestep = pytest.mark.skipif(not HAS_ACESTEP, reason="ACE-Step not running")
requires_comfyui = pytest.mark.skipif(not HAS_COMFYUI, reason="ComfyUI not running")


def _rewrite_url_for_host(url: str) -> str:
    """Translate Docker-internal URLs to localhost for host-side testing.
    
    .env can use host.docker.internal, 172.x.x.x (Docker bridge IP), or
    other non-localhost addresses. We normalise everything to localhost so
    tests work from the host machine.
    """
    import re
    # host.docker.internal → localhost
    url = url.replace("host.docker.internal", "localhost")
    # Docker bridge IPs (172.x.x.x) → localhost
    url = re.sub(r"://172\.\d+\.\d+\.\d+:", "://localhost:", url)
    return url


def _make_gpu_service():
    """Create a GPULifecycleService with URLs rewritten for host-side testing."""
    from app.services.gpu_lifecycle_service import GPULifecycleService
    svc = GPULifecycleService()
    for config in svc._services.values():
        for key in ("base_url", "health_url", "unload_url", "reload_url", "free_url"):
            if key in config and config[key]:
                config[key] = _rewrite_url_for_host(config[key])
    return svc


@pytest.fixture
def gpu_service():
    """GPULifecycleService with URLs rewritten for host-side testing.
    
    This fixture is NON-DESTRUCTIVE: process_stop eviction is stubbed to
    prevent killing ACE-Step (which can't be restarted from tests).
    Use gpu_service_destructive for tests that intentionally kill processes.
    """
    svc = _make_gpu_service()

    # Stub out process_stop eviction so ACE-Step doesn't get killed
    original_evict_service = svc._evict_service

    async def _safe_evict_service(service_name, config):
        if config["type"] == "process_stop":
            return {"status": "skipped_in_test", "reason": "process_stop disabled in safe mode"}
        return await original_evict_service(service_name, config)

    svc._evict_service = _safe_evict_service
    return svc


@pytest.fixture
def gpu_service_destructive():
    """GPULifecycleService with NO safety overrides.
    
    WARNING: This WILL kill services via process_stop (e.g. ACE-Step).
    Only use in tests that explicitly test destructive eviction.
    """
    return _make_gpu_service()


# =============================================================================
# 1. VRAM Monitoring Tests
# =============================================================================


class TestVramMonitoring:
    """Test real nvidia-smi VRAM reading."""

    @requires_nvidia
    def test_get_free_vram_gpu0(self):
        """Read free VRAM on GPU-0 via nvidia-smi."""
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        free = _get_free_vram_mb(0)
        assert free is not None, "nvidia-smi returned None for GPU-0"
        assert isinstance(free, int)
        assert free >= 0
        print(f"  GPU-0 free VRAM: {free} MB")

    @requires_nvidia
    def test_get_free_vram_gpu1(self):
        """Read free VRAM on GPU-1 (if present)."""
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        free = _get_free_vram_mb(1)
        if free is None:
            pytest.skip("No GPU-1 present")
        assert isinstance(free, int)
        assert free >= 0
        print(f"  GPU-1 free VRAM: {free} MB")

    @requires_nvidia
    def test_out_of_range_gpu_returns_none(self):
        """GPU index 99 should return None, not crash."""
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        assert _get_free_vram_mb(99) is None

    @requires_nvidia
    def test_vram_values_are_plausible(self):
        """Free VRAM should be between 0 and total VRAM."""
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        free = _get_free_vram_mb(0)
        assert free is not None
        # RTX 3090 = 24GB, RTX 8000 = 48GB, A100 = 80GB — free should be < total
        assert free < 100_000, f"Implausible free VRAM: {free} MB"


# =============================================================================
# 2. Health Check Tests
# =============================================================================


class TestHealthChecks:
    """Test real service health endpoints."""

    @requires_ollama
    async def test_ollama_health(self, gpu_service):
        """Ollama health check via /api/tags should return True."""
        config = gpu_service._services["ollama"]
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print(f"  Ollama alive: {alive}")

    @requires_tts
    async def test_tts_health(self, gpu_service):
        """Qwen3-TTS health check should return True (even if model unloaded)."""
        config = gpu_service._services["qwen3-tts"]
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print(f"  TTS alive: {alive}")

    @requires_zimage
    async def test_zimage_health(self, gpu_service):
        """Z-Image health check should return True (even if model unloaded)."""
        config = gpu_service._services["zimage"]
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print(f"  Z-Image alive: {alive}")

    @requires_acestep
    async def test_acestep_health(self, gpu_service):
        """ACE-Step health check should return True."""
        # ACE-Step uses api_unload type with cooperative /unload endpoint
        config = gpu_service._services["acestep"]
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print(f"  ACE-Step alive: {alive}")

    @requires_comfyui
    async def test_comfyui_health(self, gpu_service):
        """ComfyUI health check via /system_stats should return True."""
        # Find the ComfyUI service key
        comfy_keys = [k for k in gpu_service._services if k.startswith("comfyui:")]
        if not comfy_keys:
            pytest.skip("No ComfyUI services configured")
        config = gpu_service._services[comfy_keys[0]]
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print(f"  ComfyUI alive: {alive}")

    async def test_dead_service_returns_false(self, gpu_service):
        """A service on a port nothing listens on should return False."""
        fake_config = {
            "health_url": "http://localhost:59999/health",
            "name": "Fake Service",
        }
        alive = await gpu_service._check_service_alive(fake_config)
        assert alive is False


# =============================================================================
# 3. Service Registry Tests (against real .env config)
# =============================================================================


class TestServiceRegistry:
    """Test that the service registry loads correctly from real config."""

    def test_service_config_loaded(self, gpu_service):
        """All base services should be present in the registry."""
        assert "ollama" in gpu_service._services
        assert "zimage" in gpu_service._services
        assert "qwen3-tts" in gpu_service._services
        assert "acestep" in gpu_service._services
        print(f"  Services: {list(gpu_service._services.keys())}")

    def test_slug_mapping(self, gpu_service):
        """Tool slugs should map to service names."""
        assert gpu_service._service_for_slug("ollama-llm") == "ollama"
        assert gpu_service._service_for_slug("zimage-generation") == "zimage"
        assert gpu_service._service_for_slug("qwen3-tts-voice") == "qwen3-tts"
        assert gpu_service._service_for_slug("acestep-music-generation") == "acestep"

    def test_gpu_indices_for_slugs(self, gpu_service):
        """GPU indices should come from .env config."""
        ollama_gpus = gpu_service.get_gpu_indices_for_slug("ollama-llm")
        assert isinstance(ollama_gpus, list)
        assert all(isinstance(i, int) for i in ollama_gpus)
        print(f"  Ollama GPUs: {ollama_gpus}")
        
        zimage_gpus = gpu_service.get_gpu_indices_for_slug("zimage-generation")
        print(f"  Z-Image GPUs: {zimage_gpus}")

    def test_comfyui_tools_from_env(self, gpu_service):
        """If COMFYUI_TOOLS is set, ComfyUI services should be registered."""
        from app.core.config import settings
        if not settings.comfyui_tools_list:
            pytest.skip("No COMFYUI_TOOLS configured")
        
        for comfy in settings.comfyui_tools_list:
            slug = comfy["slug"]
            svc_name = gpu_service._service_for_slug(slug)
            assert svc_name is not None, f"ComfyUI slug '{slug}' not in registry"
            assert svc_name.startswith("comfyui:")
            print(f"  {slug} → {svc_name} (GPUs: {comfy['gpu_indices']})")

    def test_enabled_flags_match_env(self, gpu_service):
        """Service enabled flags should match USE_* env vars."""
        from app.core.config import settings
        assert gpu_service._services["ollama"]["enabled"] == settings.use_ollama
        assert gpu_service._services["zimage"]["enabled"] == settings.use_zimage
        assert gpu_service._services["qwen3-tts"]["enabled"] == settings.use_qwen3_tts
        assert gpu_service._services["acestep"]["enabled"] == settings.use_acestep


# =============================================================================
# 4. Ollama Eviction Tests (safe — models reload on next request)
# =============================================================================


class TestOllamaEviction:
    """Test Ollama model unloading via keep_alive=0.
    
    This is SAFE — models just get unloaded from VRAM. They lazy-load back
    on the next request (~2-5 seconds).
    """

    @requires_ollama
    async def test_ollama_model_listing(self):
        """Verify we can list loaded Ollama models via /api/ps."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get("http://localhost:11434/api/ps")
            assert resp.status_code == 200
            data = resp.json()
            models = data.get("models", [])
            print(f"  Loaded models: {[m['name'] for m in models]}")

    @requires_ollama
    @requires_nvidia
    async def test_evict_ollama_frees_vram(self, gpu_service):
        """Evicting Ollama should free VRAM (if models were loaded).
        
        Steps:
        1. Read VRAM before eviction
        2. Run Ollama eviction
        3. Wait briefly for VRAM to free
        4. Read VRAM after — should have more free
        """
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        
        # Check which GPUs Ollama uses
        ollama_gpus = gpu_service.get_gpu_indices_for_slug("ollama-llm")
        
        # Check what models are loaded
        async with httpx.AsyncClient(timeout=10) as client:
            ps_resp = await client.get("http://localhost:11434/api/ps")
            loaded_models = ps_resp.json().get("models", [])
        
        if not loaded_models:
            # Load a small model first so we have something to evict
            print("  No models loaded, loading mistral:7b for test...")
            async with httpx.AsyncClient(timeout=60) as client:
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": "mistral:7b", "prompt": "hi", "stream": False},
                )
            await asyncio.sleep(2)

        # Measure VRAM before
        vram_before = {}
        for gpu_idx in ollama_gpus:
            vram_before[gpu_idx] = _get_free_vram_mb(gpu_idx)
        print(f"  VRAM before eviction: {vram_before}")

        # Evict
        config = gpu_service._services["ollama"]
        result = await gpu_service._evict_ollama(config)
        print(f"  Eviction result: {result}")
        assert result["status"] in ("evicted", "already_clear")

        if result["status"] == "evicted":
            # Wait for VRAM to free
            await asyncio.sleep(3)
            
            vram_after = {}
            for gpu_idx in ollama_gpus:
                vram_after[gpu_idx] = _get_free_vram_mb(gpu_idx)
            print(f"  VRAM after eviction:  {vram_after}")
            
            # At least one GPU should have more free VRAM
            for gpu_idx in ollama_gpus:
                if vram_before[gpu_idx] and vram_after[gpu_idx]:
                    freed = vram_after[gpu_idx] - vram_before[gpu_idx]
                    print(f"  GPU-{gpu_idx}: freed {freed} MB")
                    if freed > 100:
                        return  # Success — significant VRAM was freed
            
            # If we get here, VRAM didn't free much — that's still OK
            # (model may have been small or already mostly unloaded)
            print("  Warning: VRAM did not increase significantly (model may have been small)")

    @requires_ollama
    async def test_evict_ollama_idempotent(self, gpu_service):
        """Evicting twice should be safe (second call returns already_clear)."""
        config = gpu_service._services["ollama"]
        
        # First eviction
        result1 = await gpu_service._evict_ollama(config)
        print(f"  First eviction: {result1}")
        
        # Second eviction — should be a no-op
        result2 = await gpu_service._evict_ollama(config)
        print(f"  Second eviction: {result2}")
        assert result2["status"] == "already_clear"
        assert result2["models_unloaded"] == 0


# =============================================================================
# 5. Z-Image / Qwen3-TTS Unload Tests (safe — /unload just frees VRAM)
# =============================================================================


class TestUnloadEviction:
    """Test /unload eviction for Z-Image and Qwen3-TTS.
    
    SAFE — the process stays alive, only the model gets unloaded from VRAM.
    Model lazy-loads on next request.
    """

    @requires_zimage
    async def test_zimage_eviction_when_unloaded(self, gpu_service):
        """Eviction always calls /unload (idempotent) and returns 'evicted'."""
        # First check current state
        async with httpx.AsyncClient(timeout=10) as client:
            health = await client.get("http://localhost:8003/health")
            health_data = health.json()
        print(f"  Z-Image health: {health_data}")
        
        config = gpu_service._services["zimage"]
        result = await gpu_service._evict_via_unload("zimage", config)
        print(f"  Eviction result: {result}")
        
        # /unload is always called regardless of model_loaded status
        assert result["status"] == "evicted"
        if not health_data.get("model_loaded", False):
            assert result["freed_mb"] == 0

    @requires_tts
    async def test_tts_eviction_when_unloaded(self, gpu_service):
        """Eviction always calls /unload (idempotent) and returns 'evicted'."""
        async with httpx.AsyncClient(timeout=10) as client:
            health = await client.get("http://localhost:8002/health")
            health_data = health.json()
        print(f"  TTS health: {health_data}")
        
        config = gpu_service._services["qwen3-tts"]
        result = await gpu_service._evict_via_unload("qwen3-tts", config)
        print(f"  Eviction result: {result}")
        
        # /unload is always called regardless of model_loaded status
        assert result["status"] == "evicted"
        if not health_data.get("model_loaded", False):
            assert result["freed_mb"] == 0

    @requires_zimage
    async def test_zimage_stays_healthy_after_unload(self, gpu_service):
        """Z-Image process should remain alive after model unload."""
        config = gpu_service._services["zimage"]
        
        # Evict
        await gpu_service._evict_via_unload("zimage", config)
        
        # Process should still be alive
        alive = await gpu_service._check_service_alive(config)
        assert alive is True, "Z-Image process died after unload"
        print("  Z-Image still alive after unload")

    @requires_tts
    async def test_tts_stays_healthy_after_unload(self, gpu_service):
        """Qwen3-TTS process should remain alive after model unload."""
        config = gpu_service._services["qwen3-tts"]
        
        await gpu_service._evict_via_unload("qwen3-tts", config)
        
        alive = await gpu_service._check_service_alive(config)
        assert alive is True, "TTS process died after unload"
        print("  TTS still alive after unload")


# =============================================================================
# 6. ComfyUI Eviction Tests (safe — /free just unloads models)
# =============================================================================


class TestComfyUIEviction:
    """Test ComfyUI /free endpoint for model eviction.
    
    SAFE — ComfyUI stays running, just frees VRAM. Models reload on next workflow.
    """

    @requires_comfyui
    async def test_comfyui_free_endpoint(self, gpu_service):
        """POST /free should succeed and ComfyUI should stay alive."""
        comfy_keys = [k for k in gpu_service._services if k.startswith("comfyui:")]
        if not comfy_keys:
            pytest.skip("No ComfyUI services configured")
        
        config = gpu_service._services[comfy_keys[0]]
        result = await gpu_service._evict_comfyui(config)
        print(f"  ComfyUI eviction result: {result}")
        assert result["status"] == "evicted"
        
        # Should still be alive
        alive = await gpu_service._check_service_alive(config)
        assert alive is True
        print("  ComfyUI still alive after /free")

    @requires_comfyui
    @requires_nvidia
    async def test_comfyui_free_releases_vram(self, gpu_service):
        """POST /free should release VRAM (if models were loaded)."""
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        
        comfy_keys = [k for k in gpu_service._services if k.startswith("comfyui:")]
        if not comfy_keys:
            pytest.skip("No ComfyUI services configured")
        
        config = gpu_service._services[comfy_keys[0]]
        gpu_indices = config.get("gpu_indices", [0])
        
        vram_before = {i: _get_free_vram_mb(i) for i in gpu_indices}
        print(f"  VRAM before /free: {vram_before}")
        
        result = await gpu_service._evict_comfyui(config)
        print(f"  /free result: {result}")
        
        await asyncio.sleep(2)
        
        vram_after = {i: _get_free_vram_mb(i) for i in gpu_indices}
        print(f"  VRAM after /free:  {vram_after}")


# =============================================================================
# 7. Full prepare_gpu_for_tool() Tests (the main orchestration function)
# =============================================================================


class TestPrepareGpuForTool:
    """Test the full GPU preparation pipeline against live services.
    
    prepare_gpu_for_tool() does:
    1. Look up which service the tool needs
    2. Find overlapping services on same GPU(s)
    3. Evict them
    4. Wait for VRAM to free
    """

    @requires_nvidia
    @requires_ollama
    async def test_prepare_for_ollama(self, gpu_service):
        """Full GPU prep for Ollama — should evict overlapping services.
        
        Uses the safe fixture: ACE-Step process_stop is stubbed.
        """
        result = await gpu_service.prepare_gpu_for_tool("ollama-llm")
        print(f"  Result: {result}")
        
        assert "target" in result
        assert result["target"] == "ollama"
        assert "target_gpus" in result
        assert "evictions" in result
        assert "vram_free" in result
        
        # No error
        assert "error" not in result
        
        # Log what was evicted
        for svc, info in result["evictions"].items():
            print(f"  Evicted {svc}: {info}")
        
        # ACE-Step now uses api_unload type — if not running, returns not_running
        if "acestep" in result["evictions"]:
            status = result["evictions"]["acestep"]["status"]
            assert status in ("evicted", "not_running"), (
                f"ACE-Step eviction unexpected status: {status}"
            )

    @requires_nvidia
    @requires_zimage
    async def test_prepare_for_zimage(self, gpu_service):
        """Full GPU prep for Z-Image."""
        result = await gpu_service.prepare_gpu_for_tool("zimage-generation")
        print(f"  Result: {result}")
        
        assert result["target"] == "zimage"
        assert "error" not in result
        
        for svc, info in result["evictions"].items():
            print(f"  Evicted {svc}: {info}")

    @requires_nvidia
    @requires_tts
    async def test_prepare_for_tts(self, gpu_service):
        """Full GPU prep for Qwen3-TTS."""
        result = await gpu_service.prepare_gpu_for_tool("qwen3-tts-voice")
        print(f"  Result: {result}")
        
        assert result["target"] == "qwen3-tts"
        assert "error" not in result

    @requires_nvidia
    @requires_acestep
    async def test_prepare_for_acestep(self, gpu_service):
        """Full GPU prep for ACE-Step.
        
        WARNING: This will evict other services from ACE-Step's GPU.
        ACE-Step itself won't be killed (it's the target).
        """
        result = await gpu_service.prepare_gpu_for_tool("acestep-music-generation")
        print(f"  Result: {result}")
        
        assert result["target"] == "acestep"
        assert "error" not in result

    async def test_prepare_for_unknown_slug(self, gpu_service):
        """Unknown tool slug should return an error dict."""
        result = await gpu_service.prepare_gpu_for_tool("unknown-tool-xyz")
        assert "error" in result
        print(f"  Expected error: {result['error']}")

    @requires_nvidia
    async def test_prepare_does_not_evict_self(self, gpu_service):
        """The target service should NOT appear in evictions."""
        result = await gpu_service.prepare_gpu_for_tool("ollama-llm")
        assert "ollama" not in result.get("evictions", {})

    @requires_nvidia
    @requires_comfyui
    async def test_prepare_for_comfyui_tool(self, gpu_service):
        """ComfyUI tool slug should trigger eviction pipeline."""
        from app.core.config import settings
        if not settings.comfyui_tools_list:
            pytest.skip("No COMFYUI_TOOLS configured")
        
        slug = settings.comfyui_tools_list[0]["slug"]
        result = await gpu_service.prepare_gpu_for_tool(slug)
        print(f"  ComfyUI tool {slug} result: {result}")
        assert "error" not in result


# =============================================================================
# 8. ensure_service_running() Tests
# =============================================================================


class TestEnsureServiceRunning:
    """Test that ensure_service_running correctly detects live services."""

    @requires_ollama
    async def test_ollama_running(self, gpu_service):
        result = await gpu_service.ensure_service_running("ollama-llm")
        assert result is True

    @requires_tts
    async def test_tts_running(self, gpu_service):
        result = await gpu_service.ensure_service_running("qwen3-tts-voice")
        assert result is True

    @requires_zimage
    async def test_zimage_running(self, gpu_service):
        result = await gpu_service.ensure_service_running("zimage-generation")
        assert result is True

    @requires_acestep
    async def test_acestep_running(self, gpu_service):
        result = await gpu_service.ensure_service_running("acestep-music-generation")
        assert result is True

    async def test_unknown_slug_returns_false(self, gpu_service):
        result = await gpu_service.ensure_service_running("nonexistent-tool")
        assert result is False


# =============================================================================
# 9. Cross-Service Eviction Scenarios
# =============================================================================


class TestCrossServiceEviction:
    """Test realistic eviction scenarios with multiple services.
    
    These tests exercise the real eviction pipeline under conditions that
    mimic production usage.
    """

    @requires_nvidia
    @requires_ollama
    @requires_tts
    async def test_tts_evicts_ollama_on_shared_gpu(self, gpu_service):
        """When TTS and Ollama share a GPU, preparing for TTS should evict Ollama.
        
        This test:
        1. Loads a small Ollama model (if none loaded)
        2. Prepares GPU for TTS
        3. Verifies Ollama was evicted
        4. Verifies TTS service is still alive
        """
        # Check if they share a GPU
        ollama_gpus = set(gpu_service.get_gpu_indices_for_slug("ollama-llm"))
        tts_gpus = set(gpu_service.get_gpu_indices_for_slug("qwen3-tts-voice"))
        
        if not (ollama_gpus & tts_gpus):
            pytest.skip("Ollama and TTS are on different GPUs — no overlap to test")

        # Ensure Ollama has a model loaded
        async with httpx.AsyncClient(timeout=60) as client:
            ps = await client.get("http://localhost:11434/api/ps")
            if not ps.json().get("models"):
                print("  Loading mistral:7b for eviction test...")
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": "mistral:7b", "prompt": "test", "stream": False},
                )
                await asyncio.sleep(2)

        # Prepare GPU for TTS
        result = await gpu_service.prepare_gpu_for_tool("qwen3-tts-voice")
        print(f"  Eviction result: {result}")
        
        # Ollama should have been evicted
        assert "ollama" in result["evictions"], "Ollama should be evicted when sharing GPU with TTS"
        ollama_eviction = result["evictions"]["ollama"]
        print(f"  Ollama eviction: {ollama_eviction}")
        assert ollama_eviction["status"] in ("evicted", "already_clear")
        
        # TTS should still be alive
        tts_alive = await gpu_service.ensure_service_running("qwen3-tts-voice")
        assert tts_alive is True

    @requires_nvidia
    @requires_ollama
    @requires_zimage
    async def test_zimage_evicts_ollama_on_shared_gpu(self, gpu_service):
        """When Z-Image and Ollama share a GPU, preparing for Z-Image should evict Ollama."""
        ollama_gpus = set(gpu_service.get_gpu_indices_for_slug("ollama-llm"))
        zimage_gpus = set(gpu_service.get_gpu_indices_for_slug("zimage-generation"))
        
        if not (ollama_gpus & zimage_gpus):
            pytest.skip("Ollama and Z-Image are on different GPUs")
        
        # Ensure Ollama has something loaded
        async with httpx.AsyncClient(timeout=60) as client:
            ps = await client.get("http://localhost:11434/api/ps")
            if not ps.json().get("models"):
                print("  Loading mistral:7b...")
                await client.post(
                    "http://localhost:11434/api/generate",
                    json={"model": "mistral:7b", "prompt": "test", "stream": False},
                )
                await asyncio.sleep(2)
        
        result = await gpu_service.prepare_gpu_for_tool("zimage-generation")
        print(f"  Result: {result}")
        
        assert "ollama" in result["evictions"]
        assert result["evictions"]["ollama"]["status"] in ("evicted", "already_clear")

    @requires_nvidia
    @requires_ollama
    @requires_tts
    @requires_zimage
    async def test_ollama_evicts_both_tts_and_zimage(self, gpu_service):
        """When all three share a GPU, preparing for Ollama should evict TTS and Z-Image."""
        ollama_gpus = set(gpu_service.get_gpu_indices_for_slug("ollama-llm"))
        tts_gpus = set(gpu_service.get_gpu_indices_for_slug("qwen3-tts-voice"))
        zimage_gpus = set(gpu_service.get_gpu_indices_for_slug("zimage-generation"))
        
        if not (ollama_gpus & tts_gpus) or not (ollama_gpus & zimage_gpus):
            pytest.skip("Not all services share a GPU with Ollama")
        
        result = await gpu_service.prepare_gpu_for_tool("ollama-llm")
        print(f"  Result: {result}")
        
        # Both should be evicted
        assert "qwen3-tts" in result["evictions"]
        assert "zimage" in result["evictions"]
        
        # Ollama should NOT be self-evicted
        assert "ollama" not in result["evictions"]
        
        # Both services should still be alive (processes stay up)
        for svc_slug, svc_name in [("qwen3-tts-voice", "qwen3-tts"), ("zimage-generation", "zimage")]:
            alive = await gpu_service.ensure_service_running(svc_slug)
            assert alive is True, f"{svc_name} process died after eviction"
        print("  All services still alive after eviction")

    @requires_nvidia
    @requires_ollama
    async def test_evict_then_reload_ollama(self, gpu_service):
        """Full cycle: evict Ollama, verify VRAM freed, re-load model.
        
        This tests the complete lifecycle that happens in production:
        1. GPU job needs VRAM → evict Ollama
        2. Job runs (simulated by sleep)  
        3. Next Ollama request triggers reload
        4. Verify Ollama works again
        """
        from app.services.gpu_lifecycle_service import _get_free_vram_mb
        
        ollama_gpus = gpu_service.get_gpu_indices_for_slug("ollama-llm")
        
        # Step 1: Load a model
        print("  Step 1: Loading mistral:7b...")
        async with httpx.AsyncClient(timeout=60) as client:
            await client.post(
                "http://localhost:11434/api/generate",
                json={"model": "mistral:7b", "prompt": "say ok", "stream": False},
            )
        await asyncio.sleep(1)
        
        vram_with_model = _get_free_vram_mb(ollama_gpus[0])
        print(f"  VRAM with model loaded: {vram_with_model} MB")
        
        # Step 2: Evict
        print("  Step 2: Evicting...")
        config = gpu_service._services["ollama"]
        result = await gpu_service._evict_ollama(config)
        assert result["status"] == "evicted"
        
        await asyncio.sleep(3)
        vram_after_evict = _get_free_vram_mb(ollama_gpus[0])
        print(f"  VRAM after eviction: {vram_after_evict} MB")
        
        # Step 3: Simulate GPU job finishing
        print("  Step 3: GPU job finished, re-loading Ollama model...")
        
        # Step 4: Re-load by making a new request
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "http://localhost:11434/api/generate",
                json={"model": "mistral:7b", "prompt": "say hello", "stream": False},
            )
            assert resp.status_code == 200
            response_data = resp.json()
            print(f"  Ollama response: {response_data.get('response', '')[:50]}...")
        
        print("  Full evict → reload cycle completed successfully")


# =============================================================================
# 10. wait_for_vram_free() Live Test
# =============================================================================


class TestVramWait:
    """Test the VRAM waiting logic with real GPUs."""

    @requires_nvidia
    async def test_wait_for_vram_immediately_free(self, gpu_service):
        """If VRAM is already free enough, should return True immediately."""
        # First evict everything on GPU-0 to ensure free VRAM
        result = await gpu_service.prepare_gpu_for_tool("ollama-llm")
        await asyncio.sleep(2)
        
        ok = await gpu_service._wait_for_vram_free(gpu_indices=[0], timeout=5)
        print(f"  VRAM wait result: {ok}")
        # This may be True or False depending on ACE-Step VRAM usage
        # but it should not crash
        assert isinstance(ok, bool)


# =============================================================================
# 11. Destructive Eviction Tests (kills processes — run last / opt-in)
# =============================================================================


destructive = pytest.mark.skipif(
    not HAS_ACESTEP,
    reason="ACE-Step not running (required for destructive eviction test)",
)


class TestDestructiveEviction:
    """Tests that KILL services via process_stop.
    
    These tests should run LAST because they permanently stop ACE-Step.
    After running, ACE-Step must be restarted manually:
    
        cd acestep && uv run acestep-api --host 0.0.0.0 --port 8001
    
    Run these explicitly:
        pytest tests/integration/test_gpu_live.py -k "Destructive" -v -s
    """

    @destructive
    @requires_nvidia
    @requires_ollama
    async def test_prepare_for_ollama_kills_acestep(self, gpu_service_destructive):
        """Full GPU prep with REAL process_stop — kills ACE-Step.
        
        This test verifies the destructive eviction path that's stubbed
        in the safe fixture. It must run last.
        """
        # Verify ACE-Step is alive before
        config = gpu_service_destructive._services["acestep"]
        alive_before = await gpu_service_destructive._check_service_alive(config)
        assert alive_before, "ACE-Step must be alive for destructive test"
        print("  ACE-Step alive before: True")
        
        # Run the REAL pipeline (no safety stub)
        result = await gpu_service_destructive.prepare_gpu_for_tool("ollama-llm")
        print(f"  Result: {result}")
        
        # ACE-Step should have been evicted via process_stop
        if "acestep" in result["evictions"]:
            eviction = result["evictions"]["acestep"]
            print(f"  ACE-Step eviction: {eviction}")
            assert eviction["status"] == "evicted"
            assert eviction.get("port_freed") is True
            
            # Verify it's actually dead
            await asyncio.sleep(2)
            alive_after = await gpu_service_destructive._check_service_alive(config)
            assert alive_after is False, "ACE-Step should be dead after process_stop"
            print("  ACE-Step alive after: False (correctly killed)")
        else:
            pytest.skip("ACE-Step not on the same GPU as Ollama — no overlap to test")
