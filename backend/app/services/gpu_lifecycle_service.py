"""
GPU Lifecycle Service — Cooperative VRAM eviction for multi-GPU systems.

Manages GPU VRAM by evicting models from other services before a new
GPU job starts. Supports multiple GPUs with per-service GPU affinity:
each service declares which GPU(s) it uses, and eviction only targets
services that share the same GPU as the incoming job.

Eviction strategies:
- Ollama: Send keep_alive=0 to unload models instantly (process stays alive)
- Z-Image / Qwen3-TTS: POST /unload endpoint (process stays alive, model freed)
- ACE-Step: Kill process (no cooperative unload capability)
- ComfyUI: POST /free with {unload_models: true, free_memory: true}

Services remain running as HTTP servers even after unloading. They lazy-load
models back on next request, so the cost is only the VRAM free (~1-2s) not
a full restart.

Multi-GPU logic:
- Each service has gpu_indices (e.g., [0] or [0, 1])
- When preparing GPU(s) for a tool, only evict services whose gpu_indices
  OVERLAP with the target tool's gpu_indices
- VRAM checks are per-GPU (checks each target GPU individually)
"""
import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Set

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# Timeout for HTTP calls to GPU services (seconds)
HTTP_TIMEOUT = 10.0


def _gpu_auth_headers() -> Dict[str, str]:
    """Return X-API-Key header for GPU service auth.
    
    GAP-5: Prefers GPU_INTERNAL_API_KEY (for management endpoints like
    /unload, /shutdown) and falls back to GPU_SERVICE_API_KEY.
    """
    # SGA-M7: Use .get_secret_value() for SecretStr fields
    key = settings.gpu_internal_api_key.get_secret_value() or settings.gpu_service_api_key.get_secret_value()
    if key:
        return {"X-API-Key": key}
    return {}


def _service_manager_headers() -> Dict[str, str]:
    """Return X-API-Key header for service manager auth, if configured."""
    # SGA-M7: Use .get_secret_value() for SecretStr fields
    key = settings.service_manager_api_key.get_secret_value()
    if key:
        return {"X-API-Key": key}
    return {}


# How long to wait for VRAM to actually free after eviction (seconds)
VRAM_FREE_TIMEOUT = 30

# Minimum free VRAM (MB) we want before launching a GPU job.
# Most GPU tools need 8-12 GB; set conservatively high so the
# _wait_for_vram_free check catches residual VRAM consumers.
MIN_FREE_VRAM_MB = 8000

# Free VRAM (MB) threshold for skipping Phase 2 forceful eviction.
# If free VRAM exceeds this after cooperative /unload, no /shutdown needed.
VRAM_FREE_THRESHOLD_MB = 12000


# =============================================================================
# Tool Slug → Service Mapping (built dynamically)
# =============================================================================

def _build_slug_to_service() -> Dict[str, str]:
    """Build tool slug → service key mapping from settings."""
    mapping = {
        "ollama-llm": "ollama",
        "zimage-generation": "zimage",
        "seedvr2-upscaler": "seedvr2",
        "canary-stt": "canary_stt",
        "audiosr-enhance": "audiosr",
        "ltx-video-generation": "ltx_video",
        "qwen3-tts-voice": "qwen3-tts",
        "acestep-music-generation": "acestep",
    }
    # Add ComfyUI tools — each wrapper maps to its ComfyUI server
    for comfy in settings.comfyui_tools_list:
        # Service key is per ComfyUI server URL (deduped)
        mapping[comfy["slug"]] = f"comfyui:{comfy['comfyui_url']}"
    return mapping


def _build_service_config() -> Dict[str, Dict[str, Any]]:
    """
    Build GPU service configuration from settings.
    
    Each service includes gpu_indices declaring which GPU(s) it may use.
    """
    services = {
        "ollama": {
            "name": "Ollama",
            "enabled": settings.use_ollama,
            "gpu_indices": settings.gpu_ollama_indices,
            "base_url": settings.ollama_base_url,
            "health_url": f"{settings.ollama_base_url}/api/tags",
            "type": "ollama_api",
        },
        "zimage": {
            "name": "Z-Image",
            "enabled": settings.use_zimage,
            "gpu_indices": settings.gpu_zimage_indices,
            "base_url": settings.zimage_api_url,
            "health_url": f"{settings.zimage_api_url}/health",
            "unload_url": f"{settings.zimage_api_url}/unload",
            "type": "api_unload",
        },
        "seedvr2": {
            "name": "SeedVR2 Upscaler",
            "enabled": settings.use_seedvr2,
            "gpu_indices": settings.gpu_seedvr2_indices,
            "base_url": settings.seedvr2_api_url,
            "health_url": f"{settings.seedvr2_api_url}/health",
            "unload_url": f"{settings.seedvr2_api_url}/unload",
            "type": "api_unload",
        },
        "canary_stt": {
            "name": "Canary-STT",
            "enabled": settings.use_canary_stt,
            "gpu_indices": settings.gpu_canary_stt_indices,
            "base_url": settings.canary_stt_api_url,
            "health_url": f"{settings.canary_stt_api_url}/health",
            "unload_url": f"{settings.canary_stt_api_url}/unload",
            "type": "api_unload",
        },
        "audiosr": {
            "name": "AudioSR",
            "enabled": settings.use_audiosr,
            "gpu_indices": settings.gpu_audiosr_indices,
            "base_url": settings.audiosr_api_url,
            "health_url": f"{settings.audiosr_api_url}/health",
            "unload_url": f"{settings.audiosr_api_url}/unload",
            "type": "api_unload",
        },
        "qwen3-tts": {
            "name": "Qwen3-TTS",
            "enabled": settings.use_qwen3_tts,
            "gpu_indices": settings.gpu_qwen3_tts_indices,
            "base_url": settings.qwen3_tts_api_url,
            "health_url": f"{settings.qwen3_tts_api_url}/health",
            "unload_url": f"{settings.qwen3_tts_api_url}/unload",
            "type": "api_unload",
        },
        "acestep": {
            "name": "ACE-Step",
            "enabled": settings.use_acestep,
            "gpu_indices": settings.gpu_acestep_indices,
            "base_url": settings.acestep_api_url,
            "health_url": f"{settings.acestep_api_url}/health",
            "unload_url": f"{settings.acestep_api_url}/unload",
            "reload_url": f"{settings.acestep_api_url}/reload",
            "type": "api_unload",
        },
        "ltx_video": {
            "name": "LTX-2 Video",
            "enabled": settings.use_ltx_video,
            "gpu_indices": settings.gpu_ltx_video_indices,
            "base_url": settings.ltx_video_api_url,
            "health_url": f"{settings.ltx_video_api_url}/health",
            "unload_url": f"{settings.ltx_video_api_url}/unload",
            "type": "api_unload",
        },
    }
    
    # Add ComfyUI instances — deduplicated by server URL
    # Multiple wrapper APIs may share one ComfyUI server
    comfyui_servers: Dict[str, Set[int]] = {}
    for comfy in settings.comfyui_tools_list:
        url = comfy["comfyui_url"]
        if url not in comfyui_servers:
            comfyui_servers[url] = set(comfy["gpu_indices"])
        else:
            comfyui_servers[url].update(comfy["gpu_indices"])
    
    for url, gpu_set in comfyui_servers.items():
        service_key = f"comfyui:{url}"
        services[service_key] = {
            "name": f"ComfyUI ({url})",
            "enabled": True,  # If it's in the config, it's enabled
            "gpu_indices": sorted(gpu_set),
            "base_url": url,
            "health_url": f"{url}/system_stats",
            "free_url": f"{url}/free",
            "type": "comfyui_free",
        }
    
    return services


# Map of gpu_index → slug → gpu_indices (for fast lookup)
def _get_service_gpu_affinity() -> Dict[str, List[int]]:
    """Get gpu_indices config for each tool slug."""
    slug_gpu = {
        "ollama-llm": settings.gpu_ollama_indices,
        "zimage-generation": settings.gpu_zimage_indices,
        "qwen3-tts-voice": settings.gpu_qwen3_tts_indices,
        "canary-stt": settings.gpu_canary_stt_indices,
        "audiosr-enhance": settings.gpu_audiosr_indices,
        "acestep-music-generation": settings.gpu_acestep_indices,
        "seedvr2-upscaler": settings.gpu_seedvr2_indices,
        "ltx-video-generation": settings.gpu_ltx_video_indices,
    }
    for comfy in settings.comfyui_tools_list:
        slug_gpu[comfy["slug"]] = comfy["gpu_indices"]
    return slug_gpu


# =============================================================================
# GPU Lifecycle Service
# =============================================================================

class GPULifecycleService:
    """Manages GPU VRAM by evicting models before new jobs start.
    
    Multi-GPU aware: only evicts services that share GPUs with the
    target tool.
    """

    # Map gpu_lifecycle_service internal service keys to service_manager names.
    # Most match; only underscored keys differ from the hyphenated manager names.
    _SERVICE_MANAGER_NAMES: Dict[str, str] = {
        "acestep": "acestep",
        "qwen3-tts": "qwen3-tts",
        "zimage": "zimage",
        "seedvr2": "seedvr2",
        "canary_stt": "canary-stt",
        "audiosr": "audiosr",
        "ltx_video": "ltx-video",
    }

    def __init__(self):
        self._services = _build_service_config()
        self._slug_to_service = _build_slug_to_service()
        self._slug_gpu_affinity = _get_service_gpu_affinity()
        self._service_manager_checked = False

    async def _check_service_manager_reachable(self) -> bool:
        """One-time check that the service manager HTTP API is reachable.

        Logs a clear warning on first failure so operators know the
        stop/restart integration won't work.  Called lazily on first use.
        """
        if self._service_manager_checked:
            return True

        manager_url = settings.service_manager_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{manager_url}/health", timeout=5.0)
                if resp.status_code == 200:
                    logger.info(
                        f"Service manager reachable at {manager_url}"
                    )
                    self._service_manager_checked = True
                    return True
        except Exception:
            pass

        logger.warning(
            f"Service manager unreachable at {manager_url}. "
            f"GPU service stop/restart will not work. "
            f"Ensure the service manager is running (python start.py) "
            f"and that Docker containers can reach the host via "
            f"host.docker.internal (extra_hosts: host-gateway)."
        )
        return False

    def _service_for_slug(self, tool_slug: str) -> Optional[str]:
        """Convert a tool slug to a service key."""
        return self._slug_to_service.get(tool_slug)

    def get_gpu_indices_for_slug(self, tool_slug: str) -> List[int]:
        """Get the GPU indices a tool slug is assigned to."""
        return self._slug_gpu_affinity.get(tool_slug, [0])

    async def prepare_gpu_for_tool(self, tool_slug: str) -> Dict[str, Any]:
        """
        Evict GPU tenants that share GPUs with the target tool.
        
        Two-phase eviction:
          Phase 1 — Cooperative: POST /unload on each conflicting service.
          Phase 2 — Forceful:    If VRAM is still insufficient, send
                                 POST /shutdown to services that are still
                                 alive, largest consumer first.
        
        Args:
            tool_slug: The slug of the tool that needs the GPU.
            
        Returns:
            Dict with eviction results for logging/debugging.
        """
        target_service = self._service_for_slug(tool_slug)
        target_gpus = set(self.get_gpu_indices_for_slug(tool_slug))
        
        if not target_service:
            logger.warning(f"Unknown GPU tool slug: {tool_slug}")
            return {"error": f"Unknown tool slug: {tool_slug}"}

        results = {
            "target": target_service,
            "target_gpus": sorted(target_gpus),
            "evictions": {},
        }

        # Collect conflicting services
        conflicting = []
        for service_name, config in self._services.items():
            if service_name == target_service:
                continue
            if not config["enabled"]:
                continue
            service_gpus = set(config.get("gpu_indices", [0]))
            overlap = target_gpus & service_gpus
            if not overlap:
                continue
            conflicting.append((service_name, config, overlap))

        # Phase 1: Cooperative eviction — call /unload on all conflicting services
        for service_name, config, overlap in conflicting:
            try:
                eviction_result = await self._evict_service(service_name, config)
                eviction_result["gpu_overlap"] = sorted(overlap)
                results["evictions"][service_name] = eviction_result
            except Exception as e:
                err_msg = str(e) or f"{type(e).__name__} (no message)"
                logger.error(
                    f"Error evicting {service_name}: {err_msg}",
                    exc_info=True,
                )
                results["evictions"][service_name] = {"status": "error", "error": err_msg}

        # Phase 2: Full shutdown of conflicting services.
        #
        # Even if /unload freed the model weights and VRAM looks plentiful
        # right now, we must kill conflicting processes entirely.  Their
        # CUDA context overhead (200-500 MiB per process) remains until
        # the process exits, and after the *target* service loads its own
        # model the leftover context can be the marginal amount that causes
        # an OOM.  This was the root cause of the Z-Image CUDA OOM: Phase 1
        # freed SeedVR2 weights (21 GiB → 340 MiB), the VRAM-free check
        # passed (23 GiB free > 12 GiB threshold), Phase 2 was skipped,
        # then Z-Image loaded (20.66 GiB) leaving only 942 MiB free — less
        # than the 1024 MiB needed for generation.
        gpu_idx = sorted(target_gpus)[0] if target_gpus else 0

        for service_name, config, overlap in conflicting:
            # Skip services that already reported not running
            prev = results["evictions"].get(service_name, {})
            if prev.get("status") in ("not_running", "evicted_shutdown"):
                continue
            
            # Check if this service is still alive
            is_alive = await self._check_service_alive(config)
            if not is_alive:
                # Service died (crashed or self-exited during Phase 1).
                # Its CUDA context memory may still be held until the
                # process is fully reaped.  Wait briefly and, if VRAM
                # hasn't freed, ask the service-manager to force-kill
                # any zombie via the stop endpoint.
                logger.info(
                    f"Service {service_name} already stopped after Phase 1 "
                    f"— waiting for VRAM release"
                )
                await asyncio.sleep(3)
                free_now = _get_free_vram_mb(gpu_idx)
                if free_now is not None and free_now < MIN_FREE_VRAM_MB:
                    logger.warning(
                        f"{service_name} dead but VRAM still low "
                        f"({free_now} MB free).  Asking service-manager to "
                        f"force-stop."
                    )
                    await self._service_manager_stop(service_name)
                    await asyncio.sleep(3)
                continue
            
            # Send /shutdown to fully release CUDA context
            logger.info(
                f"Phase 2: shutting down {service_name} to free CUDA context "
                f"(GPU {gpu_idx})"
            )
            free_before = _get_free_vram_mb(gpu_idx)
            shutdown_result = await self._force_shutdown_service(config, gpu_idx)
            results["evictions"][service_name]["shutdown"] = shutdown_result
            free_after = _get_free_vram_mb(gpu_idx)
            logger.info(
                f"Shutdown {service_name}: VRAM {free_before}→{free_after} MB free"
            )

        # Final VRAM check
        vram_ok = await self._wait_for_vram_free(
            gpu_indices=sorted(target_gpus),
            timeout=VRAM_FREE_TIMEOUT,
        )
        results["vram_free"] = vram_ok
        
        if not vram_ok:
            logger.warning(
                f"VRAM may not be fully free on GPU(s) {sorted(target_gpus)} after eviction. "
                f"Proceeding anyway — the target service may need to wait for lazy loading."
            )

        return results

    async def ensure_service_running(self, tool_slug: str) -> bool:
        """
        Ensure the target service is running and models are loaded.
        
        For services with /unload (Z-Image, TTS, ComfyUI), the process is
        always alive — they just need to lazy-load the model. No action needed.
        
        For services with /reload (ACE-Step), we check /health for model_loaded
        and trigger /reload if models were previously evicted.
        
        If a service was /shutdown for VRAM eviction and is now dead, we ask the
        host-side service-manager to restart it before proceeding.
        
        Args:
            tool_slug: The slug of the tool that needs to run.
            
        Returns:
            True if service is ready (or will be ready on first request).
        """
        service_name = self._service_for_slug(tool_slug)
        if not service_name:
            return False

        config = self._services.get(service_name)
        if not config or not config["enabled"]:
            return False

        # For API-based services, check if the process is alive
        if config["type"] in ("api_unload", "ollama_api", "comfyui_free"):
            is_alive = await self._check_service_alive(config)
            if not is_alive:
                # Attempt restart via host-side service manager
                logger.info(
                    f"{config['name']} not responding — "
                    f"attempting restart via service manager."
                )
                restarted = await self._restart_via_service_manager(service_name, config)
                if not restarted:
                    logger.warning(
                        f"{config['name']} could not be restarted. "
                        f"Tool execution will likely fail."
                    )
                    return False
                is_alive = True
            
            # If service has a /reload URL, check if models need reloading
            reload_url = config.get("reload_url")
            if reload_url:
                try:
                    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                        health_resp = await client.get(config["health_url"])
                        if health_resp.status_code == 200:
                            health_data = health_resp.json()
                            # Check nested 'data' field (ACE-Step wraps responses)
                            data = health_data.get("data", health_data)

                            # If a lazy-load or reload is already in progress,
                            # the service will be ready on its own — skip.
                            if data.get("model_loading", False):
                                logger.info(
                                    f"{config['name']} models are currently loading "
                                    f"— skipping /reload (will be ready on its own)"
                                )
                            elif not data.get("model_loaded", True):
                                logger.info(
                                    f"{config['name']} models unloaded — "
                                    f"triggering reload via {reload_url}"
                                )
                                reload_resp = await client.post(
                                    reload_url, timeout=300,
                                    headers=_gpu_auth_headers()
                                )
                                if reload_resp.status_code == 200:
                                    logger.info(f"{config['name']} models reloaded successfully")
                                else:
                                    logger.error(
                                        f"{config['name']} reload failed: "
                                        f"HTTP {reload_resp.status_code}"
                                    )
                                    return False
                except Exception as e:
                    logger.warning(f"Error checking/reloading {config['name']}: {e}")
            
            return is_alive

        # For process_stop type, attempt restart via service manager
        if config["type"] == "process_stop":
            is_alive = await self._check_service_alive(config)
            if not is_alive:
                logger.info(
                    f"{config['name']} was stopped — "
                    f"attempting restart via service manager."
                )
                restarted = await self._restart_via_service_manager(service_name, config)
                if not restarted:
                    logger.warning(
                        f"{config['name']} could not be restarted. "
                        f"Tool execution may fail."
                    )
                return restarted or is_alive

        return True

    # =========================================================================
    # Service Manager Integration
    # =========================================================================

    async def _service_manager_stop(self, service_name: str) -> bool:
        """Ask the host-side service-manager to force-stop a GPU service.

        Used when a service has died or crashed but its CUDA context may
        still hold VRAM (zombie process).  The service manager will try
        graceful shutdown first, then ``kill -9`` the tracked PID.

        Returns True if the stop succeeded.
        """
        await self._check_service_manager_reachable()

        manager_name = self._SERVICE_MANAGER_NAMES.get(service_name)
        if not manager_name:
            logger.warning(
                f"No service-manager mapping for '{service_name}' — "
                f"cannot force-stop."
            )
            return False
        manager_url = settings.service_manager_url.rstrip("/")
        stop_url = f"{manager_url}/services/{manager_name}/stop"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    stop_url, timeout=25.0,
                    headers=_service_manager_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(
                        f"Service-manager stop '{manager_name}': "
                        f"{data.get('message', 'ok')}"
                    )
                    return data.get("success", False)
                else:
                    logger.warning(
                        f"Service-manager stop '{manager_name}' "
                        f"returned HTTP {resp.status_code}"
                    )
                    return False
        except Exception as e:
            logger.warning(
                f"Service-manager stop '{manager_name}' failed: {e}"
            )
            return False

    async def _restart_via_service_manager(
        self, service_name: str, config: Dict
    ) -> bool:
        """
        Ask the host-side service-manager to (re)start a GPU service.
        
        Args:
            service_name: Internal service key (e.g. "canary_stt").
            config: Service configuration dict.
            
        Returns:
            True if the service came back up within the timeout.
        """
        await self._check_service_manager_reachable()

        manager_name = self._SERVICE_MANAGER_NAMES.get(service_name)
        if not manager_name:
            logger.warning(
                f"No service-manager mapping for '{service_name}' — "
                f"cannot auto-restart."
            )
            return False

        return await self._restart_via_http(manager_name, config)

    async def _restart_via_http(
        self, manager_name: str, config: Dict
    ) -> bool:
        """Try to restart via the service manager's HTTP API."""
        manager_url = settings.service_manager_url.rstrip("/")
        start_url = f"{manager_url}/services/{manager_name}/start"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                # First check if the service manager itself is reachable
                try:
                    health = await client.get(f"{manager_url}/health", timeout=5.0)
                    if health.status_code != 200:
                        logger.error(
                            "Service manager not healthy — cannot restart services."
                        )
                        return False
                except httpx.ConnectError:
                    logger.warning(
                        f"Service manager unreachable at {manager_url} (ConnectError)."
                    )
                    return False
                except httpx.TimeoutException:
                    logger.warning(
                        f"Service manager unreachable at {manager_url} (timeout)."
                    )
                    return False

                # Request the start — the manager blocks until port is open
                logger.info(f"Requesting service-manager to start '{manager_name}'...")
                resp = await client.post(
                    start_url, timeout=120.0,
                    headers=_service_manager_headers(),
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("success"):
                        logger.info(
                            f"Service manager reports '{manager_name}': "
                            f"{data.get('message')} (port={data.get('port')})"
                        )
                        # Wait a moment then verify health from backend's perspective
                        await asyncio.sleep(2)
                        alive = await self._check_service_alive(config)
                        if alive:
                            logger.info(
                                f"{config['name']} confirmed alive after restart."
                            )
                        else:
                            logger.warning(
                                f"{config['name']} service manager says started, "
                                f"but health check from backend failed. "
                                f"Proceeding anyway — may need network time."
                            )
                        return True
                    else:
                        logger.error(
                            f"Service manager could not start '{manager_name}': "
                            f"{data.get('message')}"
                        )
                        return False
                else:
                    logger.error(
                        f"Service manager returned HTTP {resp.status_code} "
                        f"for start '{manager_name}': {resp.text}"
                    )
                    return False

        except httpx.TimeoutException:
            logger.error(
                f"Timeout waiting for service manager to start '{manager_name}'. "
                f"Service may still be starting."
            )
            # Check if it came up despite timeout
            await asyncio.sleep(2)
            return await self._check_service_alive(config)
        except Exception as e:
            logger.error(f"Error contacting service manager via HTTP: {e}")
            return False

    # =========================================================================
    # Eviction Methods
    # =========================================================================

    async def _evict_service(self, service_name: str, config: Dict) -> Dict[str, Any]:
        """Evict a single service based on its type."""
        evict_type = config["type"]

        if evict_type == "ollama_api":
            return await self._evict_ollama(config)
        elif evict_type == "api_unload":
            return await self._evict_via_unload(service_name, config)
        elif evict_type == "process_stop":
            return await self._evict_via_process_stop(service_name, config)
        elif evict_type == "comfyui_free":
            return await self._evict_comfyui(config)
        else:
            return {"status": "unknown_type", "type": evict_type}

    async def _evict_ollama(self, config: Dict) -> Dict[str, Any]:
        """
        Evict Ollama models using keep_alive=0.
        
        Steps:
        1. GET /api/ps to find loaded models
        2. For each loaded model, POST /api/generate with keep_alive="0"
        """
        base_url = config["base_url"]
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                # Get loaded models
                ps_response = await client.get(f"{base_url}/api/ps")
                if ps_response.status_code != 200:
                    return {"status": "skipped", "reason": "could not get loaded models"}
                
                ps_data = ps_response.json()
                models = ps_data.get("models", [])
                
                if not models:
                    return {"status": "already_clear", "models_unloaded": 0}
                
                # Unload each model
                unloaded = []
                for model_info in models:
                    model_name = model_info.get("name", "")
                    if not model_name:
                        continue
                    
                    try:
                        await client.post(
                            f"{base_url}/api/generate",
                            json={
                                "model": model_name,
                                "prompt": "",
                                "keep_alive": 0,
                            },
                        )
                        unloaded.append(model_name)
                        logger.info(f"Unloaded Ollama model: {model_name}")
                    except Exception as e:
                        logger.warning(f"Failed to unload Ollama model {model_name}: {e}")
                
                return {"status": "evicted", "models_unloaded": len(unloaded), "models": unloaded}
                
        except httpx.ConnectError:
            return {"status": "not_running"}
        except Exception as e:
            return {"status": "error", "error": str(e) or f"{type(e).__name__}"}

    async def _evict_via_unload(self, service_name: str, config: Dict) -> Dict[str, Any]:
        """
        Phase 1 eviction: POST /unload (Z-Image, Qwen3-TTS, ACE-Step, etc.).
        
        Always calls /unload if the service is running, regardless of
        model_loaded status.  This is the cooperative, fast path.
        If VRAM isn't freed, the caller (prepare_gpu_for_tool) will
        escalate to Phase 2 (/shutdown) if needed.
        
        If the service reports ``model_loading: true`` (mid-lazy-load),
        /unload will return ``"unload_pending"`` — the model will be
        freed as soon as loading finishes.  We poll briefly for VRAM to
        drop, then return so Phase 2 can escalate if needed.
        """
        gpu_indices = config.get("gpu_indices", [0])
        gpu_idx = gpu_indices[0] if gpu_indices else 0
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                # Check if service is alive
                try:
                    health_response = await client.get(config["health_url"])
                    if health_response.status_code != 200:
                        return {"status": "not_running"}
                except httpx.ConnectError:
                    return {"status": "not_running"}
                
                # Snapshot free VRAM before unload
                free_before = _get_free_vram_mb(gpu_idx)
                
                # Always call /unload — it's idempotent
                unload_pending = False
                try:
                    unload_response = await client.post(
                        config["unload_url"], headers=_gpu_auth_headers()
                    )
                    if unload_response.status_code == 200:
                        # Check if the service flagged the unload as pending
                        # (a model load was in progress and will unload when done)
                        try:
                            body = unload_response.json()
                            data = body.get("data", body)
                            if data.get("status") == "unload_pending":
                                unload_pending = True
                                logger.info(
                                    f"{config['name']} /unload: load in progress — "
                                    f"will unload when finished"
                                )
                        except Exception:
                            pass
                        if not unload_pending:
                            logger.info(f"Called /unload on {config['name']}")
                    else:
                        logger.warning(
                            f"{config['name']} /unload returned HTTP {unload_response.status_code}"
                        )
                except Exception as e:
                    logger.warning(f"{config['name']} /unload failed: {e}")
                
                # Wait for CUDA memory to be released.
                # If the unload was flagged as pending (mid-load), wait longer
                # since the load needs to finish first.
                wait_seconds = 8 if unload_pending else 2
                await asyncio.sleep(wait_seconds)
                
                # Check free VRAM after unload
                free_after = _get_free_vram_mb(gpu_idx)
                freed_mb = (free_after or 0) - (free_before or 0)
                
                logger.info(
                    f"{config['name']} /unload: free VRAM {free_before}MB -> "
                    f"{free_after}MB (freed {freed_mb}MB)"
                )
                
                status = "evict_pending" if unload_pending else "evicted"
                return {"status": status, "freed_mb": freed_mb}
                    
        except httpx.ConnectError:
            return {"status": "not_running"}
        except Exception as e:
            err_msg = str(e) or f"{type(e).__name__} (no message)"
            logger.warning(
                f"{config['name']} eviction error: {type(e).__name__}: {err_msg}",
                exc_info=True,
            )
            return {"status": "error", "error": err_msg}

    async def _force_shutdown_service(
        self, config: Dict, gpu_idx: int
    ) -> Dict[str, Any]:
        """
        Phase 2 eviction: POST /shutdown to terminate a service process.
        
        Used when /unload didn't free VRAM (CUDA context stuck). The process
        MUST be killed to release GPU memory.
        
        Returns result dict with status and freed_mb.
        """
        shutdown_url = config.get("base_url", "").rstrip("/") + "/shutdown"
        free_before = _get_free_vram_mb(gpu_idx)
        
        logger.warning(
            f"Sending /shutdown to {config['name']} to force VRAM release "
            f"(free VRAM: {free_before}MB)"
        )
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                try:
                    resp = await client.post(
                        shutdown_url, timeout=5.0,
                        headers=_gpu_auth_headers()
                    )
                    if resp.status_code == 200:
                        logger.info(f"Sent /shutdown to {config['name']}")
                    elif resp.status_code == 404:
                        logger.warning(
                            f"{config['name']} has no /shutdown endpoint (404). "
                            f"Service may need manual restart to free VRAM."
                        )
                        return {"status": "no_shutdown_endpoint"}
                    else:
                        logger.warning(
                            f"{config['name']} /shutdown returned HTTP {resp.status_code}"
                        )
                except Exception as e:
                    # Connection reset is expected — server is shutting down
                    logger.info(
                        f"{config['name']} /shutdown connection closed (expected): {e}"
                    )
        except Exception as e:
            return {"status": "error", "error": str(e) or f"{type(e).__name__}"}
        
        # Wait for process to die and VRAM to free
        for _ in range(15):
            await asyncio.sleep(1)
            free_now = _get_free_vram_mb(gpu_idx)
            if free_now is not None and free_now > (free_before or 0) + 500:
                freed = (free_now or 0) - (free_before or 0)
                logger.info(
                    f"{config['name']} VRAM freed after /shutdown "
                    f"({free_before}MB -> {free_now}MB free, +{freed}MB)"
                )
                return {"status": "shutdown_ok", "freed_mb": freed}
        
        final_free = _get_free_vram_mb(gpu_idx)
        logger.error(
            f"{config['name']} VRAM not freed after /shutdown. "
            f"Free VRAM: {final_free}MB."
        )
        return {"status": "shutdown_failed", "free_vram_mb": final_free}

    async def _evict_via_process_stop(self, service_name: str, config: Dict) -> Dict[str, Any]:
        """
        Evict by terminating the process via POST /shutdown.
        
        This is the nuclear option — used only for services that can't
        cooperatively unload their model.
        """
        is_running = await self._check_service_alive(config)
        if not is_running:
            return {"status": "not_running"}

        gpu_indices = config.get("gpu_indices", [0])
        gpu_idx = gpu_indices[0] if gpu_indices else 0
        free_before = _get_free_vram_mb(gpu_idx)
        
        shutdown_url = config.get("base_url", "").rstrip("/") + "/shutdown"
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(
                    shutdown_url, timeout=5.0,
                    headers=_gpu_auth_headers()
                )
                logger.info(f"Sent /shutdown to {config['name']} (HTTP {resp.status_code})")
        except Exception as e:
            # Connection reset is expected — server is shutting down
            logger.info(f"{config['name']} /shutdown connection closed (expected): {e}")
        
        # Wait for process to die and VRAM to free
        for _ in range(15):
            await asyncio.sleep(1)
            free_now = _get_free_vram_mb(gpu_idx)
            if free_now is not None and free_now > (free_before or 0) + 500:
                logger.info(
                    f"{config['name']} process stopped, VRAM freed "
                    f"({free_before}MB -> {free_now}MB free)"
                )
                return {"status": "evicted", "freed_mb": (free_now or 0) - (free_before or 0)}
        
        return {"status": "error", "error": "process may not have exited"}

    async def _evict_comfyui(self, config: Dict) -> Dict[str, Any]:
        """
        Evict ComfyUI models via POST /free.
        
        ComfyUI's /free endpoint accepts:
        {
            "unload_models": true,  — unloads all loaded models from VRAM
            "free_memory": true     — additionally frees cached memory
        }
        """
        free_url = config.get("free_url")
        if not free_url:
            return {"status": "error", "error": "no free_url configured"}
        
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                # Check if ComfyUI is running
                try:
                    health_response = await client.get(config["health_url"])
                    if health_response.status_code != 200:
                        return {"status": "not_running"}
                except httpx.ConnectError:
                    return {"status": "not_running"}
                
                # Tell ComfyUI to free all models and memory
                response = await client.post(
                    free_url,
                    json={
                        "unload_models": True,
                        "free_memory": True,
                    },
                    headers=_gpu_auth_headers(),
                )
                if response.status_code == 200:
                    logger.info(f"Freed ComfyUI models via {free_url}")
                    return {"status": "evicted"}
                else:
                    return {"status": "error", "http_status": response.status_code}
                    
        except httpx.ConnectError:
            return {"status": "not_running"}
        except Exception as e:
            return {"status": "error", "error": str(e) or f"{type(e).__name__}"}

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _check_service_alive(self, config: Dict) -> bool:
        """Check if a service is alive by hitting its health endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(config["health_url"])
                return response.status_code == 200
        except Exception:
            return False

    async def _wait_for_vram_free(
        self,
        gpu_indices: List[int],
        timeout: int = VRAM_FREE_TIMEOUT,
    ) -> bool:
        """
        Wait for GPU VRAM to be sufficiently free on specific GPUs.
        
        Checks each GPU in gpu_indices individually.
        Returns True if ALL target GPUs have enough free VRAM.
        """
        for _ in range(timeout):
            all_free = True
            for gpu_idx in gpu_indices:
                free_mb = _get_free_vram_mb(gpu_idx)
                if free_mb is None:
                    continue  # Can't detect VRAM — assume it's fine
                if free_mb < MIN_FREE_VRAM_MB:
                    all_free = False
                    break
            if all_free:
                return True
            await asyncio.sleep(1)
        
        # Log final state of each GPU
        for gpu_idx in gpu_indices:
            free_mb = _get_free_vram_mb(gpu_idx)
            logger.warning(f"GPU-{gpu_idx} VRAM free: {free_mb}MB after {timeout}s wait")
        return False

    async def _wait_for_port_free(self, port: int, timeout: int = 10) -> bool:
        """Wait for a port to become free after killing a process."""
        import socket
        for _ in range(timeout):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(("localhost", port))
                sock.close()
                if result != 0:
                    return True  # Port is free
            except Exception:
                return True  # Error connecting = port is free
            await asyncio.sleep(1)
        return False


# =============================================================================
# Module-Level Helpers
# =============================================================================

def _get_free_vram_mb(gpu_index: int = 0) -> Optional[int]:
    """Get free GPU VRAM in MB for a specific GPU using nvidia-smi.
    
    Returns None if unavailable or gpu_index out of range.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        if gpu_index < len(lines):
            return int(lines[gpu_index])
        return None
    except Exception:
        return None


# =============================================================================
# Singleton
# =============================================================================

_gpu_lifecycle_service: Optional[GPULifecycleService] = None


def get_gpu_lifecycle_service() -> GPULifecycleService:
    """Get or create the singleton GPULifecycleService."""
    global _gpu_lifecycle_service
    if _gpu_lifecycle_service is None:
        _gpu_lifecycle_service = GPULifecycleService()
    return _gpu_lifecycle_service
