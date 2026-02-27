"""Celery tasks for tool health checks and maintenance.

These tasks run in Celery workers and handle:
- Periodic tool health checks
- Tool validation and monitoring
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any

from app.core.celery_app import celery_app
from app.core.database import get_db_context
from app.core.datetime_utils import utc_now

logger = logging.getLogger(__name__)


async def _cleanup_db_pool():
    """Clean up the database pool for the current event loop."""
    from app.core.database import _engines, _session_makers, _get_loop_id
    loop_id = _get_loop_id()
    if loop_id in _engines:
        engine = _engines.pop(loop_id)
        await engine.dispose()
        logger.debug(f"Disposed engine for loop {loop_id}")
    if loop_id in _session_makers:
        del _session_makers[loop_id]


def run_async(coro):
    """Run an async coroutine in Celery (which is sync)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Clean up database connections before closing loop
        loop.run_until_complete(_cleanup_db_pool())
        loop.close()


# =============================================================================
# Tool Health Check Tasks
# =============================================================================

@celery_app.task(name="app.tasks.tool_tasks.tool_health_check_scheduler")
def tool_health_check_scheduler() -> Dict[str, Any]:
    """
    Periodic task to check tools that need health checks.
    
    This task runs periodically and:
    1. Finds tools with health checks enabled that are due for a check
    2. Runs health checks on those tools
    3. Updates tool health status in the database
    
    Returns:
        Dictionary with check results summary
    """
    return run_async(_tool_health_check_scheduler_async())


async def _tool_health_check_scheduler_async() -> Dict[str, Any]:
    """Async implementation of tool health check scheduler."""
    from app.services.tool_health_service import tool_health_service
    
    results = {
        "checked_at": utc_now().isoformat(),
        "tools_checked": 0,
        "healthy": 0,
        "degraded": 0,
        "unhealthy": 0,
        "unknown": 0,
        "errors": [],
    }
    
    async with get_db_context() as db:
        try:
            # Get tools that need health checks
            tools = await tool_health_service.get_tools_needing_check(db)
            
            if not tools:
                logger.debug("No tools need health checks at this time")
                return results
            
            logger.info(f"Running health checks for {len(tools)} tools")
            
            for tool in tools:
                try:
                    # Run health check
                    health_result = await tool_health_service.check_tool_health(
                        db=db,
                        tool_id=tool.id,
                        check_type="connectivity",
                        is_automatic=True
                    )
                    
                    results["tools_checked"] += 1
                    
                    # Count by status
                    status = health_result.get("status", "unknown")
                    if status == "healthy":
                        results["healthy"] += 1
                    elif status == "degraded":
                        results["degraded"] += 1
                    elif status == "unhealthy":
                        results["unhealthy"] += 1
                    else:
                        results["unknown"] += 1
                    
                    logger.info(f"Tool '{tool.name}' health check: {status}")
                    
                except Exception as e:
                    error_msg = f"Health check failed for tool '{tool.name}': {str(e)}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)
                    results["unknown"] += 1
            
            # Log summary
            logger.info(
                f"Health check complete: {results['tools_checked']} checked, "
                f"{results['healthy']} healthy, {results['degraded']} degraded, "
                f"{results['unhealthy']} unhealthy"
            )
            
        except Exception as e:
            logger.error(f"Tool health check scheduler failed: {str(e)}")
            results["errors"].append(f"Scheduler error: {str(e)}")
    
    return results


@celery_app.task(name="app.tasks.tool_tasks.check_single_tool_health")
def check_single_tool_health(
    tool_id: str,
    check_type: str = "connectivity"
) -> Dict[str, Any]:
    """
    Run a health check on a single tool.
    
    This task can be triggered manually or by other tasks.
    
    Args:
        tool_id: UUID of the tool to check
        check_type: Type of check (connectivity, validation, full)
        
    Returns:
        Health check result
    """
    return run_async(_check_single_tool_health_async(tool_id, check_type))


async def _check_single_tool_health_async(
    tool_id: str,
    check_type: str
) -> Dict[str, Any]:
    """Async implementation of single tool health check."""
    from uuid import UUID
    from app.services.tool_health_service import tool_health_service
    
    async with get_db_context() as db:
        try:
            tool_uuid = UUID(tool_id) if isinstance(tool_id, str) else tool_id
            result = await tool_health_service.check_tool_health(
                db=db,
                tool_id=tool_uuid,
                check_type=check_type,
                is_automatic=False
            )
            return result
        except Exception as e:
            logger.error(f"Health check failed for tool {tool_id}: {str(e)}")
            return {
                "tool_id": str(tool_id),
                "status": "error",
                "message": str(e),
                "checked_at": utc_now().isoformat()
            }


@celery_app.task(name="app.tasks.tool_tasks.check_all_tools_health")
def check_all_tools_health() -> Dict[str, Any]:
    """
    Run health checks on ALL tools (regardless of schedule).
    
    This is typically triggered manually by an admin.
    
    Returns:
        Dictionary with check results for all tools
    """
    return run_async(_check_all_tools_health_async())


async def _check_all_tools_health_async() -> Dict[str, Any]:
    """Async implementation of check all tools health."""
    from app.services.tool_health_service import tool_health_service
    
    results = {
        "checked_at": utc_now().isoformat(),
        "tools_checked": 0,
        "results": [],
        "summary": {
            "healthy": 0,
            "degraded": 0,
            "unhealthy": 0,
            "unknown": 0
        }
    }
    
    async with get_db_context() as db:
        try:
            all_results = await tool_health_service.check_all_tools(db)
            
            results["tools_checked"] = len(all_results)
            results["results"] = all_results
            
            for result in all_results:
                status = result.get("status", "unknown")
                if status in results["summary"]:
                    results["summary"][status] += 1
                else:
                    results["summary"]["unknown"] += 1
            
            logger.info(
                f"All tools health check complete: {results['tools_checked']} checked"
            )
            
        except Exception as e:
            logger.error(f"Check all tools health failed: {str(e)}")
            results["error"] = str(e)
    
    return results


@celery_app.task(name="app.tasks.tool_tasks.get_tool_health_summary")
def get_tool_health_summary() -> Dict[str, Any]:
    """
    Get a summary of tool health status.
    
    Returns:
        Dictionary with health status counts
    """
    return run_async(_get_tool_health_summary_async())


async def _get_tool_health_summary_async() -> Dict[str, Any]:
    """Async implementation of get tool health summary."""
    from app.services.tool_health_service import tool_health_service
    
    async with get_db_context() as db:
        try:
            summary = await tool_health_service.get_health_summary(db)
            return {
                "retrieved_at": utc_now().isoformat(),
                **summary
            }
        except Exception as e:
            logger.error(f"Get health summary failed: {str(e)}")
            return {
                "error": str(e),
                "retrieved_at": utc_now().isoformat()
            }
