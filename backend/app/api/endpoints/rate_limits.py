"""Rate limit management API endpoints."""
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db, get_current_admin
from app.models import User, RateLimitScope, RateLimitPeriod
from app.services.rate_limit_service import RateLimitService

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class RateLimitCreate(BaseModel):
    """Request to create a rate limit."""
    scope: RateLimitScope
    max_executions: int = Field(..., ge=1, description="Maximum executions per period")
    period: RateLimitPeriod
    user_id: Optional[UUID] = Field(None, description="Required for USER and USER_TOOL scopes")
    tool_id: Optional[UUID] = Field(None, description="Required for TOOL and USER_TOOL scopes")
    max_cost_units: Optional[int] = Field(None, ge=0, description="Optional cost-based limit")
    allow_burst: bool = Field(False, description="Allow temporary burst above limit")
    burst_multiplier: int = Field(2, ge=1, le=10, description="Burst multiplier (e.g., 2 = 2x limit)")
    name: Optional[str] = Field(None, max_length=255, description="Friendly name")
    description: Optional[str] = Field(None, description="Description of why this limit exists")

    class Config:
        json_schema_extra = {
            "example": {
                "scope": "tool",
                "max_executions": 100,
                "period": "hour",
                "tool_id": "123e4567-e89b-12d3-a456-426614174000",
                "name": "Serper API Hourly Limit",
                "description": "Limit to 100 searches/hour due to API quota"
            }
        }


class RateLimitUpdate(BaseModel):
    """Request to update a rate limit."""
    max_executions: Optional[int] = Field(None, ge=1)
    period: Optional[RateLimitPeriod] = None
    max_cost_units: Optional[int] = Field(None, ge=0)
    allow_burst: Optional[bool] = None
    burst_multiplier: Optional[int] = Field(None, ge=1, le=10)
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class RateLimitResponse(BaseModel):
    """Response with rate limit details."""
    id: UUID
    scope: str
    user_id: Optional[UUID]
    tool_id: Optional[UUID]
    max_executions: int
    period: str
    max_cost_units: Optional[int]
    allow_burst: bool
    burst_multiplier: int
    is_active: bool
    name: Optional[str]
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    created_by_id: Optional[UUID]

    class Config:
        from_attributes = True


class RateLimitStatusResponse(BaseModel):
    """Response with current rate limit status for a tool/user."""
    allowed: bool
    current_count: int
    max_count: int
    remaining: int
    period: Optional[str] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    retry_after_seconds: Optional[int] = None
    limit_id: Optional[UUID] = None
    limit_name: Optional[str] = None


class RateLimitSummaryResponse(BaseModel):
    """Summary of all applicable rate limits."""
    limits: List[dict]
    total_remaining: int  # -1 = unlimited
    most_restrictive: Optional[dict] = None


class ViolationResponse(BaseModel):
    """Response with violation details."""
    id: UUID
    rate_limit_id: UUID
    user_id: Optional[UUID]
    tool_id: Optional[UUID]
    current_count: int
    limit_count: int
    period_start: datetime
    agent_name: Optional[str]
    violated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Rate Limit Management Endpoints (Admin)
# =============================================================================

@router.post("", response_model=RateLimitResponse, status_code=status.HTTP_201_CREATED)
async def create_rate_limit(
    data: RateLimitCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Create a new rate limit configuration.
    
    Scopes:
    - **global**: Applies to all tool executions system-wide
    - **user**: Per-user limit (requires user_id)
    - **tool**: Per-tool limit (requires tool_id)
    - **user_tool**: Per user+tool combination (requires both)
    
    Admin only.
    """
    service = RateLimitService(db)
    
    try:
        limit = await service.create_rate_limit(
            scope=data.scope,
            max_executions=data.max_executions,
            period=data.period,
            user_id=data.user_id,
            tool_id=data.tool_id,
            max_cost_units=data.max_cost_units,
            allow_burst=data.allow_burst,
            burst_multiplier=data.burst_multiplier,
            name=data.name,
            description=data.description,
            created_by_id=current_user.id,
        )
        
        return RateLimitResponse(
            id=limit.id,
            scope=limit.scope.value,
            user_id=limit.user_id,
            tool_id=limit.tool_id,
            max_executions=limit.max_executions,
            period=limit.period.value,
            max_cost_units=limit.max_cost_units,
            allow_burst=limit.allow_burst,
            burst_multiplier=limit.burst_multiplier or 2,
            is_active=limit.is_active,
            name=limit.name,
            description=limit.description,
            created_at=limit.created_at,
            updated_at=limit.updated_at,
            created_by_id=limit.created_by_id,
        )
    except ValueError as e:
        import logging
        logging.getLogger(__name__).error("Rate limit operation failed: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid rate limit configuration")


@router.get("", response_model=List[RateLimitResponse])
async def list_rate_limits(
    scope: Optional[RateLimitScope] = Query(None, description="Filter by scope"),
    user_id: Optional[UUID] = Query(None, description="Filter by user"),
    tool_id: Optional[UUID] = Query(None, description="Filter by tool"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    List all rate limit configurations.
    
    Admin only.
    """
    service = RateLimitService(db)
    
    limits = await service.list_rate_limits(
        scope=scope,
        user_id=user_id,
        tool_id=tool_id,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    
    return [
        RateLimitResponse(
            id=l.id,
            scope=l.scope.value,
            user_id=l.user_id,
            tool_id=l.tool_id,
            max_executions=l.max_executions,
            period=l.period.value,
            max_cost_units=l.max_cost_units,
            allow_burst=l.allow_burst,
            burst_multiplier=l.burst_multiplier or 2,
            is_active=l.is_active,
            name=l.name,
            description=l.description,
            created_at=l.created_at,
            updated_at=l.updated_at,
            created_by_id=l.created_by_id,
        )
        for l in limits
    ]


@router.get("/{limit_id}", response_model=RateLimitResponse)
async def get_rate_limit(
    limit_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get a specific rate limit configuration.
    
    Admin only.
    """
    service = RateLimitService(db)
    limit = await service.get_rate_limit(limit_id)
    
    if not limit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rate limit not found")
    
    return RateLimitResponse(
        id=limit.id,
        scope=limit.scope.value,
        user_id=limit.user_id,
        tool_id=limit.tool_id,
        max_executions=limit.max_executions,
        period=limit.period.value,
        max_cost_units=limit.max_cost_units,
        allow_burst=limit.allow_burst,
        burst_multiplier=limit.burst_multiplier or 2,
        is_active=limit.is_active,
        name=limit.name,
        description=limit.description,
        created_at=limit.created_at,
        updated_at=limit.updated_at,
        created_by_id=limit.created_by_id,
    )


@router.patch("/{limit_id}", response_model=RateLimitResponse)
async def update_rate_limit(
    limit_id: UUID,
    data: RateLimitUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Update a rate limit configuration.
    
    Admin only.
    """
    service = RateLimitService(db)
    
    limit = await service.update_rate_limit(
        limit_id=limit_id,
        max_executions=data.max_executions,
        period=data.period,
        max_cost_units=data.max_cost_units,
        allow_burst=data.allow_burst,
        burst_multiplier=data.burst_multiplier,
        name=data.name,
        description=data.description,
        is_active=data.is_active,
    )
    
    if not limit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rate limit not found")
    
    return RateLimitResponse(
        id=limit.id,
        scope=limit.scope.value,
        user_id=limit.user_id,
        tool_id=limit.tool_id,
        max_executions=limit.max_executions,
        period=limit.period.value,
        max_cost_units=limit.max_cost_units,
        allow_burst=limit.allow_burst,
        burst_multiplier=limit.burst_multiplier or 2,
        is_active=limit.is_active,
        name=limit.name,
        description=limit.description,
        created_at=limit.created_at,
        updated_at=limit.updated_at,
        created_by_id=limit.created_by_id,
    )


@router.delete("/{limit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate_limit(
    limit_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Delete a rate limit configuration.
    
    Admin only.
    """
    service = RateLimitService(db)
    
    if not await service.delete_rate_limit(limit_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rate limit not found")


# =============================================================================
# Rate Limit Status Endpoints (User)
# =============================================================================

@router.get("/check/{tool_id}", response_model=RateLimitStatusResponse)
async def check_rate_limit(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Check current rate limit status for a tool.
    
    Returns whether the user can execute the tool and remaining quota.
    """
    service = RateLimitService(db)
    
    status = await service.check_rate_limit(
        tool_id=tool_id,
        user_id=current_user.id,
    )
    
    return RateLimitStatusResponse(
        allowed=status.allowed,
        current_count=status.current_count,
        max_count=status.max_count,
        remaining=status.remaining,
        period=status.limit.period.value if status.limit else None,
        period_start=status.period_start,
        period_end=status.period_end,
        retry_after_seconds=status.retry_after_seconds,
        limit_id=status.limit.id if status.limit else None,
        limit_name=status.limit.name if status.limit else None,
    )


@router.get("/summary/me", response_model=RateLimitSummaryResponse)
async def get_my_rate_limit_summary(
    tool_id: Optional[UUID] = Query(None, description="Optionally filter by tool"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get a summary of all rate limits applying to the current user.
    
    Shows all applicable limits with current usage.
    """
    service = RateLimitService(db)
    
    summary = await service.get_rate_limit_summary(
        tool_id=tool_id,
        user_id=current_user.id,
    )
    
    return RateLimitSummaryResponse(
        limits=summary.limits,
        total_remaining=summary.total_remaining,
        most_restrictive=summary.most_restrictive,
    )


@router.get("/summary/tool/{tool_id}", response_model=RateLimitSummaryResponse)
async def get_tool_rate_limit_summary(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Get a summary of rate limits for a specific tool.
    
    Includes both tool-wide limits and user+tool specific limits.
    """
    service = RateLimitService(db)
    
    summary = await service.get_rate_limit_summary(
        tool_id=tool_id,
        user_id=current_user.id,
    )
    
    return RateLimitSummaryResponse(
        limits=summary.limits,
        total_remaining=summary.total_remaining,
        most_restrictive=summary.most_restrictive,
    )


# =============================================================================
# Violation Tracking Endpoints (Admin)
# =============================================================================

@router.get("/violations", response_model=List[ViolationResponse])
async def list_violations(
    rate_limit_id: Optional[UUID] = Query(None, description="Filter by limit"),
    user_id: Optional[UUID] = Query(None, description="Filter by user"),
    tool_id: Optional[UUID] = Query(None, description="Filter by tool"),
    since_hours: int = Query(24, ge=1, le=720, description="Violations in last N hours"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    List rate limit violations.
    
    Useful for monitoring and alerting on rate limit issues.
    
    Admin only.
    """
    service = RateLimitService(db)
    
    since = utc_now() - timedelta(hours=since_hours)
    
    violations = await service.list_violations(
        rate_limit_id=rate_limit_id,
        user_id=user_id,
        tool_id=tool_id,
        since=since,
        limit=limit,
        offset=offset,
    )
    
    return [
        ViolationResponse(
            id=v.id,
            rate_limit_id=v.rate_limit_id,
            user_id=v.user_id,
            tool_id=v.tool_id,
            current_count=v.current_count,
            limit_count=v.limit_count,
            period_start=v.period_start,
            agent_name=v.agent_name,
            violated_at=v.violated_at,
        )
        for v in violations
    ]


@router.get("/violations/count")
async def get_violation_count(
    user_id: Optional[UUID] = Query(None, description="Filter by user"),
    tool_id: Optional[UUID] = Query(None, description="Filter by tool"),
    since_hours: int = Query(24, ge=1, le=720, description="Violations in last N hours"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get count of rate limit violations.
    
    Admin only.
    """
    service = RateLimitService(db)
    
    since = utc_now() - timedelta(hours=since_hours)
    
    count = await service.get_violation_count(
        user_id=user_id,
        tool_id=tool_id,
        since=since,
    )
    
    return {"count": count, "since_hours": since_hours}
