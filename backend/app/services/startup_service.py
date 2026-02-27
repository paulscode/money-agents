"""
Startup initialization service.

Handles automatic initialization tasks that run on each application startup:
- System resource detection (CPU, RAM, GPU, Storage)
- Tools catalog management based on configuration

This ensures the system is always in sync with the current configuration,
even if API keys are added/removed after initial setup.
"""
import logging
from typing import Dict, Any
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from uuid import uuid4, UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session_maker
from app.core.config import settings
from app.models import Tool, User, ToolStatus, ToolCategory
from app.services import resource_service
from app.schemas.resource import ResourceStatus, ResourceType

logger = logging.getLogger(__name__)


# =============================================================================
# CPU-Intensive Tool Definitions
# =============================================================================

# Tools that are heavy enough on CPU that you wouldn't want two running at once.
# Lightweight CPU tools (media-toolkit FFmpeg, docling parsing) are NOT included.
_CPU_INTENSIVE_TOOL_SLUGS = [
    "realesrgan-cpu-upscaler",  # Neural network inference on CPU — very heavy
]


# =============================================================================
# GPU Tool Definitions
# =============================================================================

# Base GPU tool slugs (always present when GPU is enabled)
_BASE_GPU_TOOL_SLUGS = [
    "ollama-llm",
    "acestep-music-generation",
    "qwen3-tts-voice",
    "zimage-generation",
    "seedvr2-upscaler",
    "canary-stt",
    "audiosr-enhance",
    "ltx-video-generation",
]


def get_gpu_tool_slugs() -> list[str]:
    """Get all GPU tool slugs including dynamically registered ComfyUI tools."""
    slugs = list(_BASE_GPU_TOOL_SLUGS)
    for comfy in settings.comfyui_tools_list:
        slugs.append(comfy["slug"])
    return slugs


# Backwards-compatible alias
GPU_TOOL_SLUGS = _BASE_GPU_TOOL_SLUGS  # Static reference; use get_gpu_tool_slugs() for dynamic


# =============================================================================
# Tool Definitions for System Tools
# =============================================================================

# Mapping of tool slugs to their availability conditions
SYSTEM_TOOL_CONDITIONS = {
    "z-ai-llm": lambda: bool(settings.z_ai_api_key),
    "anthropic-llm": lambda: bool(settings.anthropic_api_key),
    "openai-llm": lambda: bool(settings.openai_api_key),
    "openai-dall-e-3": lambda: bool(settings.openai_api_key),
    "serper-web-search": lambda: bool(settings.serper_api_key),
    "elevenlabs-voice": lambda: bool(settings.elevenlabs_api_key),
    "suno-ai-music": lambda: settings.use_suno,
    "ollama-llm": lambda: settings.use_ollama,
    "acestep-music-generation": lambda: settings.use_acestep,
    "qwen3-tts-voice": lambda: settings.use_qwen3_tts,
    "zimage-generation": lambda: settings.use_zimage,
    "seedvr2-upscaler": lambda: settings.use_seedvr2,
    "canary-stt": lambda: settings.use_canary_stt,
    "audiosr-enhance": lambda: settings.use_audiosr,
    "ltx-video-generation": lambda: settings.use_ltx_video,
    "dev-sandbox": lambda: settings.use_dev_sandbox,
    "realesrgan-cpu-upscaler": lambda: settings.use_realesrgan_cpu,
    "docling-parser": lambda: settings.use_docling,
}


async def get_or_create_system_user(db: AsyncSession) -> User:
    """Get or create the system user for automated operations."""
    result = await db.execute(
        select(User).where(User.email == "system@money-agents.dev")
    )
    user = result.scalar_one_or_none()
    
    if not user:
        from app.core.security import get_password_hash
        import secrets
        # SGA3-H1: Use cryptographically random password — system account must
        # never accept interactive login.
        user = User(
            username="system",
            email="system@money-agents.dev",
            password_hash=get_password_hash(secrets.token_urlsafe(48)),
            role="admin",
            is_active=True,
            is_superuser=True
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("Created system user for automated operations")
    
    return user


async def sync_tool_status(db: AsyncSession, tool: Tool, should_be_available: bool) -> bool:
    """
    Sync a tool's status based on whether it should be available.
    
    Returns True if the tool status was changed.
    """
    changed = False
    
    if should_be_available:
        # Tool should be enabled - if it was disabled due to missing config, re-enable it
        if tool.status in [ToolStatus.DEPRECATED, ToolStatus.RETIRED]:
            tool.status = ToolStatus.IMPLEMENTED
            tool.updated_at = utc_now()
            changed = True
            logger.info(f"Re-enabled tool '{tool.name}' (config now available)")
    else:
        # Tool should be disabled - if it's currently active, deprecate it
        if tool.status == ToolStatus.IMPLEMENTED:
            tool.status = ToolStatus.DEPRECATED
            tool.updated_at = utc_now()
            changed = True
            logger.info(f"Deprecated tool '{tool.name}' (config no longer available)")
    
    return changed


async def sync_system_tools(db: AsyncSession) -> Dict[str, Any]:
    """
    Sync system tool statuses based on current configuration.
    
    This handles:
    - Re-enabling tools when API keys are added
    - Deprecating tools when API keys are removed
    
    Note: Does NOT create new tools - use init_tools_catalog.py for that.
    The startup sync only manages existing tools' enabled/disabled state.
    
    Returns:
        Dictionary with counts of enabled and disabled tools.
    """
    result = {
        "enabled": 0,
        "disabled": 0,
        "unchanged": 0,
    }
    
    for slug, condition_fn in SYSTEM_TOOL_CONDITIONS.items():
        # Check if tool exists
        query = select(Tool).where(Tool.slug == slug)
        db_result = await db.execute(query)
        tool = db_result.scalar_one_or_none()
        
        if not tool:
            # Tool doesn't exist - will be created by init_tools_catalog.py if needed
            continue
        
        should_be_available = condition_fn()
        changed = await sync_tool_status(db, tool, should_be_available)
        
        if changed:
            if should_be_available:
                result["enabled"] += 1
            else:
                result["disabled"] += 1
        else:
            result["unchanged"] += 1
    
    return result


async def activate_gpu_resources(db: AsyncSession) -> int:
    """
    Activate GPU resources if USE_GPU is enabled.
    
    Changes GPU resources from DISABLED to AVAILABLE so the job queue
    will accept GPU tool executions.
    
    Returns:
        Number of GPU resources activated.
    """
    if not settings.use_gpu:
        logger.info("GPU acceleration disabled (USE_GPU=false), GPU resources stay disabled")
        return 0
    
    gpu_resources = await resource_service.get_resources_by_type(db, ResourceType.GPU)
    activated = 0
    for gpu in gpu_resources:
        if gpu.status == ResourceStatus.DISABLED:
            gpu.status = ResourceStatus.AVAILABLE
            activated += 1
            logger.info(f"Activated GPU resource: {gpu.name}")
    
    return activated


async def activate_cpu_resources(db: AsyncSession) -> int:
    """
    Activate CPU resources so the job queue will accept CPU-intensive tool executions.
    
    Unlike GPU (gated on USE_GPU), the CPU resource is always activated when
    there are CPU-intensive tools enabled — the CPU is always present.
    
    Returns:
        Number of CPU resources activated.
    """
    # Only activate if at least one CPU-intensive tool is enabled
    any_cpu_tool_enabled = any(
        slug in SYSTEM_TOOL_CONDITIONS and SYSTEM_TOOL_CONDITIONS[slug]()
        for slug in _CPU_INTENSIVE_TOOL_SLUGS
    )
    if not any_cpu_tool_enabled:
        logger.debug("No CPU-intensive tools enabled, CPU resource stays as-is")
        return 0
    
    cpu_resources = await resource_service.get_resources_by_type(db, ResourceType.CPU)
    activated = 0
    for cpu in cpu_resources:
        if cpu.status == ResourceStatus.DISABLED:
            cpu.status = ResourceStatus.AVAILABLE
            activated += 1
            logger.info(f"Activated CPU resource: {cpu.name}")
    
    return activated


async def link_cpu_tools_to_resource(db: AsyncSession) -> int:
    """
    Link CPU-intensive tools to the CPU resource for queue serialization.
    
    Only tools in _CPU_INTENSIVE_TOOL_SLUGS are linked — lightweight CPU tools
    (media-toolkit, docling, dev-sandbox) run without queue constraints.
    
    Returns:
        Number of tools linked.
    """
    cpu_resources = await resource_service.get_resources_by_type(db, ResourceType.CPU)
    if not cpu_resources:
        logger.info("No CPU resources found, skipping CPU tool linking")
        return 0
    
    # Use the first (and typically only) CPU resource
    cpu_resource = None
    for r in cpu_resources:
        if r.status != ResourceStatus.DISABLED:
            cpu_resource = r
            break
    
    if not cpu_resource:
        logger.info("CPU resource is disabled, skipping CPU tool linking")
        return 0
    
    linked = 0
    cpu_resource_id = str(cpu_resource.id)
    
    for slug in _CPU_INTENSIVE_TOOL_SLUGS:
        result = await db.execute(select(Tool).where(Tool.slug == slug))
        tool = result.scalar_one_or_none()
        
        if not tool or tool.status != ToolStatus.IMPLEMENTED:
            continue
        
        current_ids = tool.resource_ids or []
        if current_ids != [cpu_resource_id]:
            tool.resource_ids = [cpu_resource_id]
            tool.resource_id = cpu_resource.id
            linked += 1
            logger.info(f"Linked tool '{slug}' to CPU resource: {cpu_resource.name}")
    
    return linked


async def link_gpu_tools_to_resource(db: AsyncSession) -> int:
    """
    Link GPU tools to their assigned GPU resource(s).
    
    Multi-GPU aware: each tool is linked to the GPU(s) it's configured to use
    via GPU_OLLAMA, GPU_ZIMAGE, etc. env vars. ComfyUI tools may be linked
    to multiple GPUs.
    
    Returns:
        Number of tools linked.
    """
    # Get all GPU resources and build index → resource map
    gpu_resources = await resource_service.get_resources_by_type(db, ResourceType.GPU)
    if not gpu_resources:
        logger.info("No GPU resources found, skipping GPU tool linking")
        return 0
    
    gpu_map: dict[int, Resource] = {}
    for gpu in gpu_resources:
        idx = gpu.resource_metadata.get("index", 0) if gpu.resource_metadata else 0
        if gpu.status != ResourceStatus.DISABLED:
            gpu_map[idx] = gpu
    
    if not gpu_map:
        logger.info("All GPU resources are disabled, skipping GPU tool linking")
        return 0
    
    # Build slug → gpu_indices mapping from config
    slug_gpu_map: dict[str, list[int]] = {
        "ollama-llm": settings.gpu_ollama_indices,
        "acestep-music-generation": settings.gpu_acestep_indices,
        "qwen3-tts-voice": settings.gpu_qwen3_tts_indices,
        "zimage-generation": settings.gpu_zimage_indices,
        "seedvr2-upscaler": settings.gpu_seedvr2_indices,
        "canary-stt": settings.gpu_canary_stt_indices,
        "audiosr-enhance": settings.gpu_audiosr_indices,
        "ltx-video-generation": settings.gpu_ltx_video_indices,
    }
    for comfy in settings.comfyui_tools_list:
        slug_gpu_map[comfy["slug"]] = comfy["gpu_indices"]
    
    linked = 0
    
    for slug, gpu_indices in slug_gpu_map.items():
        query = select(Tool).where(Tool.slug == slug)
        result = await db.execute(query)
        tool = result.scalar_one_or_none()
        
        if not tool:
            continue
        
        if tool.status != ToolStatus.IMPLEMENTED:
            continue
        
        # Build resource_ids from GPU indices
        resource_ids = []
        for idx in gpu_indices:
            if idx in gpu_map:
                resource_ids.append(str(gpu_map[idx].id))
        
        if not resource_ids:
            # Fallback: if configured GPU index doesn't exist, use first available
            first_gpu = next(iter(gpu_map.values()), None)
            if first_gpu:
                resource_ids = [str(first_gpu.id)]
                logger.warning(
                    f"Tool '{slug}' configured for GPU {gpu_indices} but "
                    f"those GPUs not available. Falling back to {first_gpu.name}"
                )
        
        if resource_ids:
            # Sort for consistent ordering (important for multi-resource queue deadlock prevention)
            resource_ids = sorted(resource_ids)
            current_ids = tool.resource_ids or []
            if current_ids != resource_ids:
                tool.resource_ids = resource_ids
                tool.resource_id = UUID(resource_ids[0])
                linked += 1
                gpu_names = [gpu_map[i].name for i in gpu_indices if i in gpu_map]
                logger.info(f"Linked tool '{slug}' to GPU(s): {gpu_names}")
    
    return linked


async def sync_comfyui_tools(db: AsyncSession) -> int:
    """
    Create or update Tool records for ComfyUI workflow APIs.
    
    Reads COMFYUI_TOOLS env var (populated by start.py from discovered
    comfy-workflows/) and ensures each wrapper API has a corresponding
    Tool record with the correct interface_config for dynamic REST execution.
    
    Returns:
        Number of ComfyUI tools created or updated.
    """
    comfyui_tools = settings.comfyui_tools_list
    if not comfyui_tools:
        return 0
    
    # Get system user for requester_id (required field)
    system_user = await get_or_create_system_user(db)
    
    synced = 0
    
    for comfy in comfyui_tools:
        slug = comfy["slug"]
        display_name = comfy["display_name"]
        port = comfy["port"]
        wrapper_url = f"http://host.docker.internal:{port}"
        
        # Check if tool exists
        result = await db.execute(select(Tool).where(Tool.slug == slug))
        tool = result.scalar_one_or_none()
        
        # Build interface config for dynamic REST API execution
        interface_config = {
            "base_url": wrapper_url,
            "endpoint": {
                "method": "POST",
                "path": "/generate",
                "headers": {"Content-Type": "application/json"},
            },
            "auth": {"type": "none"},
        }
        
        if tool:
            # Update existing tool
            tool.interface_type = "rest_api"
            tool.interface_config = interface_config
            tool.updated_at = utc_now()
            if tool.status in [ToolStatus.DEPRECATED, ToolStatus.RETIRED]:
                if settings.use_gpu:
                    tool.status = ToolStatus.IMPLEMENTED
            synced += 1
            logger.info(f"Updated ComfyUI tool: {slug}")
        else:
            # Create new tool
            tool = Tool(
                id=uuid4(),
                slug=slug,
                name=f"ComfyUI: {display_name}",
                category=ToolCategory.API,
                status=ToolStatus.IMPLEMENTED if settings.use_gpu else ToolStatus.DEPRECATED,
                requester_id=system_user.id,
                description=(
                    f"ComfyUI workflow: {display_name}. "
                    f"GPU-accelerated generation via ComfyUI server. "
                    f"Runs locally — FREE and unlimited."
                ),
                tags=["comfyui", "gpu", "local", "free", "generation"],
                interface_type="rest_api",
                interface_config=interface_config,
                integration_complexity="medium",
                cost_model="free",
                cost_details={"type": "free", "monthly_cost": 0},
                version="1.0",
                priority="medium",
            )
            db.add(tool)
            synced += 1
            logger.info(f"Created ComfyUI tool: {slug} ({display_name})")
    
    # Deprecate ComfyUI tools that are no longer in config
    active_slugs = {c["slug"] for c in comfyui_tools}
    result = await db.execute(
        select(Tool).where(
            Tool.slug.like("comfy-%"),
            Tool.status == ToolStatus.IMPLEMENTED,
        )
    )
    for tool in result.scalars():
        if tool.slug not in active_slugs:
            tool.status = ToolStatus.DEPRECATED
            tool.updated_at = utc_now()
            logger.info(f"Deprecated removed ComfyUI tool: {tool.slug}")
    
    return synced


async def initialize_on_startup() -> Dict[str, Any]:
    """
    Run all startup initialization tasks.
    
    Called from FastAPI lifespan to ensure system is properly initialized.
    
    Returns:
        Dictionary with results from each initialization task.
    """
    results = {
        "resources": None,
        "tools": None,
        "errors": [],
    }
    
    try:
        async with get_session_maker()() as db:
            # 1. Auto-detect and sync system resources
            # Note: We wrap this in a try/except because there might be 
            # database issues (e.g., duplicate resources from remote agents)
            try:
                logger.info("Startup: Detecting system resources...")
                resource_result = await resource_service.initialize_system_resources(db)
                await db.commit()
                results["resources"] = resource_result
                logger.info(
                    f"Startup: Resources - created {resource_result['created']}, "
                    f"updated {resource_result['updated']}, types: {resource_result['types']}"
                )
            except Exception as e:
                # Resource detection may fail if there are duplicate resources
                # from remote agents - this is not critical for startup
                logger.warning(f"Startup: Resource detection skipped: {e}")
                results["errors"].append(f"Resource detection (non-critical): {str(e)}")
                # Rollback any partial changes
                await db.rollback()
            
            # 2. Activate GPU resources if USE_GPU is enabled
            try:
                gpu_activated = await activate_gpu_resources(db)
                await db.commit()
                results["gpu_activated"] = gpu_activated
                if gpu_activated > 0:
                    logger.info(f"Startup: Activated {gpu_activated} GPU resource(s)")
            except Exception as e:
                logger.warning(f"Startup: GPU activation failed: {e}")
                results["errors"].append(f"GPU activation (non-critical): {str(e)}")
                await db.rollback()
            
            # 3. Sync system tools based on configuration
            try:
                logger.info("Startup: Syncing system tools...")
                tools_result = await sync_system_tools(db)
                await db.commit()
                results["tools"] = tools_result
                logger.info(
                    f"Startup: Tools - enabled {tools_result['enabled']}, "
                    f"disabled {tools_result['disabled']}, unchanged {tools_result['unchanged']}"
                )
            except Exception as e:
                logger.error(f"Startup: Tool sync failed: {e}")
                results["errors"].append(f"Tool sync: {str(e)}")
                await db.rollback()
            
            # 3b. Sync ComfyUI tools from COMFYUI_TOOLS env var
            try:
                comfyui_synced = await sync_comfyui_tools(db)
                await db.commit()
                results["comfyui_tools_synced"] = comfyui_synced
                if comfyui_synced > 0:
                    logger.info(f"Startup: Synced {comfyui_synced} ComfyUI tool(s)")
            except Exception as e:
                logger.warning(f"Startup: ComfyUI tool sync failed: {e}")
                results["errors"].append(f"ComfyUI tool sync (non-critical): {str(e)}")
                await db.rollback()
            
            # 4. Link GPU tools to GPU resource for queue serialization
            try:
                gpu_linked = await link_gpu_tools_to_resource(db)
                await db.commit()
                results["gpu_tools_linked"] = gpu_linked
                if gpu_linked > 0:
                    logger.info(f"Startup: Linked {gpu_linked} GPU tool(s) to GPU resource")
            except Exception as e:
                logger.warning(f"Startup: GPU tool linking failed: {e}")
                results["errors"].append(f"GPU tool linking (non-critical): {str(e)}")
                await db.rollback()
            
            # 5. Activate CPU resource and link CPU-intensive tools
            try:
                cpu_activated = await activate_cpu_resources(db)
                await db.commit()
                results["cpu_activated"] = cpu_activated
                if cpu_activated > 0:
                    logger.info(f"Startup: Activated {cpu_activated} CPU resource(s)")
            except Exception as e:
                logger.warning(f"Startup: CPU activation failed: {e}")
                results["errors"].append(f"CPU activation (non-critical): {str(e)}")
                await db.rollback()
            
            try:
                cpu_linked = await link_cpu_tools_to_resource(db)
                await db.commit()
                results["cpu_tools_linked"] = cpu_linked
                if cpu_linked > 0:
                    logger.info(f"Startup: Linked {cpu_linked} CPU-intensive tool(s) to CPU resource")
            except Exception as e:
                logger.warning(f"Startup: CPU tool linking failed: {e}")
                results["errors"].append(f"CPU tool linking (non-critical): {str(e)}")
                await db.rollback()
            
            # 6. Ensure agents are disabled until admin acknowledges disclaimer
            try:
                from app.services.disclaimer_service import ensure_agents_disabled_on_fresh_install
                await ensure_agents_disabled_on_fresh_install(db)
            except Exception as e:
                logger.warning(f"Startup: Disclaimer agent check failed: {e}")
                results["errors"].append(f"Disclaimer agent check (non-critical): {str(e)}")
                await db.rollback()

        # 7. Refresh LLM model pricing from OpenRouter (outside db session)
        try:
            from app.services.pricing_update_service import refresh_model_pricing
            logger.info("Startup: Refreshing LLM model pricing...")
            pricing_result = await refresh_model_pricing()
            results["pricing_refresh"] = {
                "success": pricing_result.success,
                "models_checked": pricing_result.models_checked,
                "models_updated": pricing_result.models_updated,
            }
            if pricing_result.models_updated > 0:
                logger.warning(
                    "Startup: Pricing updated for %d model(s)",
                    pricing_result.models_updated,
                )
            else:
                logger.info("Startup: Model pricing is up to date")
        except Exception as e:
            logger.warning(f"Startup: Pricing refresh failed (non-critical): {e}")
            results["errors"].append(f"Pricing refresh (non-critical): {str(e)}")

                
    except Exception as e:
        logger.error(f"Startup initialization failed: {e}")
        results["errors"].append(f"Database connection: {str(e)}")
    
    return results


async def check_llm_availability() -> Dict[str, bool]:
    """
    Check which LLM providers are available based on configuration.
    
    Returns:
        Dictionary mapping provider names to availability status.
    """
    return {
        "z_ai": bool(settings.z_ai_api_key),
        "anthropic": bool(settings.anthropic_api_key),
        "openai": bool(settings.openai_api_key),
        "ollama": settings.use_ollama,
        "any_available": bool(
            settings.z_ai_api_key or 
            settings.anthropic_api_key or 
            settings.openai_api_key or 
            settings.use_ollama
        ),
    }


async def check_search_availability() -> bool:
    """Check if web search is available."""
    return bool(settings.serper_api_key)


async def check_acestep_availability() -> Dict[str, Any]:
    """
    Check ACE-Step local music generation availability.
    
    Returns:
        Dictionary with ACE-Step status information.
    """
    if not settings.use_acestep:
        return {
            "enabled": False,
            "available": False,
            "running": False,
            "model": None,
        }
    
    # Check if ACE-Step server is running
    try:
        from app.services.acestep_service import get_acestep_service
        service = get_acestep_service()
        is_running = await service.health_check()
        status = await service.get_status()
        
        return {
            "enabled": True,
            "available": True,
            "running": is_running,
            "model": status.get("model"),
            "max_steps": status.get("max_steps"),
            "installed": status.get("installed"),
        }
    except Exception:
        return {
            "enabled": True,
            "available": False,
            "running": False,
            "model": settings.acestep_model,
            "error": "Failed to check ACE-Step status",
        }


async def check_qwen3_tts_availability() -> Dict[str, Any]:
    """
    Check Qwen3-TTS local voice generation availability.
    
    Returns:
        Dictionary with Qwen3-TTS status information.
    """
    if not settings.use_qwen3_tts:
        return {
            "enabled": False,
            "available": False,
            "running": False,
            "tier": None,
        }
    
    # Check if Qwen3-TTS server is running
    try:
        from app.services.qwen3_tts_service import get_qwen3_tts_service
        service = get_qwen3_tts_service()
        is_running = await service.health_check()
        status = await service.get_status()
        
        return {
            "enabled": True,
            "available": True,
            "running": is_running,
            "tier": status.get("tier"),
            "model_tier": status.get("model_tier"),
            "capabilities": status.get("capabilities", []),
            "installed": status.get("installed"),
        }
    except Exception:
        return {
            "enabled": True,
            "available": False,
            "running": False,
            "tier": settings.qwen3_tts_tier,
            "error": "Failed to check Qwen3-TTS status",
        }
