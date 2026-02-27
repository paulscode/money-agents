"""
API endpoints for tool health checks and validation.

Provides:
- GET /tools/{id}/health - Get tool health status
- POST /tools/{id}/health/check - Trigger health check
- GET /tools/{id}/health/history - Get health check history
- GET /tools/health/summary - Get health summary across all tools
- GET /tools/health/unhealthy - List unhealthy tools
- PUT /tools/{id}/health/settings - Configure health check settings
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, get_db
from app.models import User, Tool, ToolHealthCheck, HealthStatus
from app.services.tool_health_service import ToolHealthService

router = APIRouter()


# =============================================================================
# Request/Response Schemas
# =============================================================================

class HealthCheckResponse(BaseModel):
    """Response for a health check."""
    id: UUID
    tool_id: UUID
    status: str
    message: Optional[str] = None
    response_time_ms: Optional[int] = None
    check_type: str
    details: Optional[Dict[str, Any]] = None
    is_automatic: bool
    triggered_by_id: Optional[UUID] = None
    checked_at: str
    
    class Config:
        from_attributes = True


class ToolHealthResponse(BaseModel):
    """Response for tool health status."""
    tool_id: UUID
    tool_name: str
    tool_slug: str
    health_status: str
    health_message: Optional[str] = None
    health_response_ms: Optional[int] = None
    last_health_check: Optional[str] = None
    health_check_enabled: bool
    health_check_interval_minutes: Optional[int] = None


class HealthSummaryResponse(BaseModel):
    """Summary of tool health statuses."""
    healthy: int
    degraded: int
    unhealthy: int
    unknown: int
    total: int


class HealthCheckRequest(BaseModel):
    """Request to trigger a health check."""
    check_type: str = Field(default="full", pattern="^(connectivity|validation|full)$")


class HealthSettingsRequest(BaseModel):
    """Request to update health check settings."""
    enabled: bool
    interval_minutes: int = Field(default=60, ge=5, le=1440)  # 5 min to 24 hours


def health_check_to_response(check: ToolHealthCheck) -> HealthCheckResponse:
    """Convert model to response."""
    return HealthCheckResponse(
        id=check.id,
        tool_id=check.tool_id,
        status=check.status,
        message=check.message,
        response_time_ms=check.response_time_ms,
        check_type=check.check_type,
        details=check.details,
        is_automatic=check.is_automatic,
        triggered_by_id=check.triggered_by_id,
        checked_at=check.checked_at.isoformat(),
    )


def tool_to_health_response(tool: Tool) -> ToolHealthResponse:
    """Convert tool to health response."""
    return ToolHealthResponse(
        tool_id=tool.id,
        tool_name=tool.name,
        tool_slug=tool.slug,
        health_status=tool.health_status or "unknown",
        health_message=tool.health_message,
        health_response_ms=tool.health_response_ms,
        last_health_check=tool.last_health_check.isoformat() if tool.last_health_check else None,
        health_check_enabled=tool.health_check_enabled,
        health_check_interval_minutes=tool.health_check_interval_minutes,
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/tools/{tool_id}/health", response_model=ToolHealthResponse)
async def get_tool_health(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get the current health status of a tool.
    """
    from sqlalchemy import select
    
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    return tool_to_health_response(tool)


@router.post("/tools/{tool_id}/health/check", response_model=HealthCheckResponse)
async def trigger_health_check(
    tool_id: UUID,
    data: HealthCheckRequest = HealthCheckRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Trigger a health check for a tool.
    
    Check types:
    - connectivity: Check if the tool's endpoint/command is reachable
    - validation: Validate the tool's configuration
    - full: Both connectivity and validation (default)
    """
    service = ToolHealthService(db)
    
    try:
        check = await service.check_tool_health(
            tool_id=tool_id,
            user_id=current_user.id,
            check_type=data.check_type,
        )
    except ValueError as e:
        import logging
        logging.getLogger(__name__).error("Tool health check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    return health_check_to_response(check)


@router.get("/tools/{tool_id}/health/history", response_model=List[HealthCheckResponse])
async def get_health_history(
    tool_id: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get health check history for a tool.
    
    Returns the most recent health checks, ordered by time descending.
    """
    service = ToolHealthService(db)
    checks = await service.get_health_history(tool_id, limit=limit)
    return [health_check_to_response(c) for c in checks]


@router.get("/tools/health/summary", response_model=HealthSummaryResponse)
async def get_health_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a summary of tool health statuses.
    
    Returns counts of tools in each health state.
    """
    service = ToolHealthService(db)
    summary = await service.get_health_summary()
    summary["total"] = sum(summary.values())
    return HealthSummaryResponse(**summary)


@router.get("/tools/health/unhealthy", response_model=List[ToolHealthResponse])
async def get_unhealthy_tools(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get list of unhealthy or degraded tools (admin only).
    
    Useful for monitoring and alerting.
    """
    service = ToolHealthService(db)
    tools = await service.get_unhealthy_tools()
    return [tool_to_health_response(t) for t in tools]


@router.put("/tools/{tool_id}/health/settings", response_model=ToolHealthResponse)
async def update_health_settings(
    tool_id: UUID,
    data: HealthSettingsRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Update health check settings for a tool (admin only).
    
    - enabled: Whether to run automatic health checks
    - interval_minutes: How often to check (5-1440 minutes)
    """
    from sqlalchemy import select
    
    service = ToolHealthService(db)
    
    # Get tool first
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tool not found"
        )
    
    if data.enabled:
        await service.enable_health_checks(tool_id, data.interval_minutes)
    else:
        await service.disable_health_checks(tool_id)
    
    # Refresh tool
    await db.refresh(tool)
    return tool_to_health_response(tool)


@router.post("/tools/health/check-all", response_model=List[HealthCheckResponse])
async def check_all_tools(
    only_enabled: bool = Query(True, description="Only check tools with health checks enabled"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Trigger health checks for all tools (admin only).
    
    By default only checks tools with health_check_enabled=True.
    Set only_enabled=False to check all implemented tools.
    """
    service = ToolHealthService(db)
    checks = await service.check_all_tools(only_enabled=only_enabled)
    return [health_check_to_response(c) for c in checks]
