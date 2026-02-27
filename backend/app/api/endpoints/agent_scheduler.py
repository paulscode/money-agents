"""Agent management API endpoints."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_admin, get_db
from app.core.rate_limit import limiter
from app.models import User
from app.models.agent_scheduler import (
    AgentDefinition,
    AgentRun,
    AgentStatus,
    AgentRunStatus,
    BudgetPeriod,
)
from app.services.agent_scheduler_service import agent_scheduler_service

router = APIRouter(prefix="/agents", tags=["agents"])


# =============================================================================
# Schemas
# =============================================================================

class AgentSummary(BaseModel):
    """Summary of an agent's current status."""
    id: UUID
    name: str
    slug: str
    description: str
    status: AgentStatus
    status_message: Optional[str]
    is_enabled: bool
    
    # Scheduling
    schedule_interval_seconds: int
    last_run_at: Optional[datetime]
    next_run_at: Optional[datetime]
    
    # Budget
    budget_limit: Optional[float]
    budget_period: BudgetPeriod
    budget_used: float
    budget_remaining: Optional[float]
    budget_percentage_used: float
    budget_warning: bool
    budget_warning_threshold: float
    
    # Statistics
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    total_tokens_used: int
    total_cost_usd: float
    
    # Configuration
    default_model_tier: str
    config: Optional[dict] = None
    expected_run_duration_minutes: Optional[int] = None  # For staleness detection
    
    class Config:
        from_attributes = True


class AgentRunSummary(BaseModel):
    """Summary of an agent run."""
    id: UUID
    agent_slug: str
    status: AgentRunStatus
    trigger_type: str
    trigger_reason: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    items_processed: int
    items_created: int
    tokens_used: int
    cost_usd: float
    model_used: Optional[str]
    error_message: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True


class AgentUpdateRequest(BaseModel):
    """Request to update agent settings."""
    is_enabled: Optional[bool] = None
    schedule_interval_seconds: Optional[int] = Field(None, ge=60, le=86400 * 7)  # 1 min to 7 days
    default_model_tier: Optional[str] = Field(None, pattern="^(fast|reasoning|quality)$")
    config: Optional[dict] = None
    expected_run_duration_minutes: Optional[int] = Field(None, ge=1, le=1440)  # 1 min to 24 hours


class BudgetUpdateRequest(BaseModel):
    """Request to update agent budget settings."""
    budget_limit: Optional[float] = Field(None, ge=0)
    budget_period: Optional[BudgetPeriod] = None
    warning_threshold: Optional[float] = Field(None, ge=0, le=1)


class TriggerAgentRequest(BaseModel):
    """Request to manually trigger an agent."""
    reason: Optional[str] = Field(None, max_length=500)


class AgentActionResponse(BaseModel):
    """Response for agent actions."""
    success: bool
    message: str
    agent_slug: Optional[str] = None
    run_id: Optional[UUID] = None


# =============================================================================
# Helper Functions
# =============================================================================

def agent_to_summary(agent: AgentDefinition) -> AgentSummary:
    """Convert an AgentDefinition to an AgentSummary."""
    budget_remaining = None
    budget_percentage = 0.0
    budget_warning = False
    
    if agent.budget_limit:
        budget_remaining = max(0, agent.budget_limit - agent.budget_used)
        budget_percentage = (agent.budget_used / agent.budget_limit) * 100
        budget_warning = budget_percentage >= (agent.budget_warning_threshold * 100)
    
    success_rate = 0.0
    if agent.total_runs > 0:
        success_rate = (agent.successful_runs / agent.total_runs) * 100
    
    return AgentSummary(
        id=agent.id,
        name=agent.name,
        slug=agent.slug,
        description=agent.description,
        status=agent.status,
        status_message=agent.status_message,
        is_enabled=agent.is_enabled,
        schedule_interval_seconds=agent.schedule_interval_seconds,
        last_run_at=agent.last_run_at,
        next_run_at=agent.next_run_at,
        budget_limit=agent.budget_limit,
        budget_period=agent.budget_period,
        budget_used=agent.budget_used,
        budget_remaining=budget_remaining,
        budget_percentage_used=budget_percentage,
        budget_warning=budget_warning,
        budget_warning_threshold=agent.budget_warning_threshold,
        total_runs=agent.total_runs,
        successful_runs=agent.successful_runs,
        failed_runs=agent.failed_runs,
        success_rate=success_rate,
        total_tokens_used=agent.total_tokens_used,
        total_cost_usd=agent.total_cost_usd,
        default_model_tier=agent.default_model_tier,
        config=agent.config,
        expected_run_duration_minutes=agent.expected_run_duration_minutes,
    )


def run_to_summary(run: AgentRun, agent_slug: str) -> AgentRunSummary:
    """Convert an AgentRun to an AgentRunSummary."""
    return AgentRunSummary(
        id=run.id,
        agent_slug=agent_slug,
        status=run.status,
        trigger_type=run.trigger_type,
        trigger_reason=run.trigger_reason,
        started_at=run.started_at,
        completed_at=run.completed_at,
        duration_seconds=run.duration_seconds,
        items_processed=run.items_processed,
        items_created=run.items_created,
        tokens_used=run.tokens_used,
        cost_usd=run.cost_usd,
        model_used=run.model_used,
        error_message=run.error_message,
        created_at=run.created_at,
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("", response_model=List[AgentSummary])
# Production-reviewed: Frontend polls agent list at 15-30s intervals across
# multiple components (useAgents.ts). 10/min caused 429s with normal UI usage.
@limiter.limit("90/minute")
async def list_agents(
    request: Request,
    include_disabled: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all registered agents with their current status."""
    agents = await agent_scheduler_service.get_all_agents(db, include_disabled=include_disabled)
    return [agent_to_summary(agent) for agent in agents]


@router.get("/{agent_slug}", response_model=AgentSummary)
# Production-reviewed: polled at 15s intervals on agent detail pages.
@limiter.limit("90/minute")
async def get_agent(
    request: Request,
    agent_slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get details for a specific agent."""
    agent = await agent_scheduler_service.get_agent(db, slug=agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    return agent_to_summary(agent)


@router.patch("/{agent_slug}", response_model=AgentSummary)
@limiter.limit("10/minute")
async def update_agent(
    request: Request,
    agent_slug: str,
    update_data: AgentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),  # Admin only
):
    """Update agent settings (admin only)."""
    update_data = update_data.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update",
        )
    
    agent = await agent_scheduler_service.get_agent(db, slug=agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    updated = await agent_scheduler_service.update_agent(db, agent.id, **update_data)
    return agent_to_summary(updated)


@router.post("/{agent_slug}/pause", response_model=AgentActionResponse)
@limiter.limit("10/minute")
async def pause_agent(
    request: Request,
    agent_slug: str,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),  # Admin only
):
    """Pause an agent (admin only)."""
    agent = await agent_scheduler_service.pause_agent(db, agent_slug, reason)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    return AgentActionResponse(
        success=True,
        message=f"Agent '{agent_slug}' paused",
        agent_slug=agent_slug,
    )


@router.post("/{agent_slug}/resume", response_model=AgentActionResponse)
@limiter.limit("10/minute")
async def resume_agent(
    request: Request,
    agent_slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),  # Admin only
):
    """Resume a paused agent (admin only)."""
    agent = await agent_scheduler_service.resume_agent(db, agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    return AgentActionResponse(
        success=True,
        message=f"Agent '{agent_slug}' resumed",
        agent_slug=agent_slug,
    )


@router.post("/{agent_slug}/trigger", response_model=AgentActionResponse)
@limiter.limit("10/minute")
async def trigger_agent(
    request: Request,
    agent_slug: str,
    trigger_data: TriggerAgentRequest = TriggerAgentRequest(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),  # Admin only
):
    """Manually trigger an agent to run immediately (admin only)."""
    agent = await agent_scheduler_service.get_agent(db, slug=agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    if agent.status == AgentStatus.RUNNING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{agent_slug}' is already running",
        )
    
    if agent.status == AgentStatus.BUDGET_EXCEEDED:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Agent '{agent_slug}' budget exceeded",
        )
    
    # Create a run record
    run = await agent_scheduler_service.create_run(
        db,
        slug=agent_slug,
        trigger_type="manual",
        trigger_reason=trigger_data.reason or f"Manual trigger by {current_user.username}",
    )
    
    if not run:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create run",
        )
    
    # Set agent status to running immediately so UI reflects the action
    await agent_scheduler_service.set_agent_status(
        db, agent_slug, AgentStatus.RUNNING, "Starting manual run..."
    )
    
    # Trigger the Celery task
    from app.tasks.agent_tasks import run_opportunity_scout, run_tool_scout, run_campaign_manager
    
    if agent_slug == "opportunity_scout":
        run_opportunity_scout.delay(force=True)
    elif agent_slug == "tool_scout":
        run_tool_scout.delay(force=True)
    elif agent_slug == "proposal_writer":
        # Proposal writer is event-driven, but we can trigger a check
        from app.tasks.agent_tasks import check_approved_opportunities
        check_approved_opportunities.delay()
    elif agent_slug == "campaign_manager":
        run_campaign_manager.delay(force=True)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent '{agent_slug}' cannot be manually triggered",
        )
    
    return AgentActionResponse(
        success=True,
        message=f"Agent '{agent_slug}' triggered",
        agent_slug=agent_slug,
        run_id=run.id,
    )


@router.get("/{agent_slug}/budget")
# Production-reviewed: polled at 15s on agent management page.
@limiter.limit("90/minute")
async def get_agent_budget(
    request: Request,
    agent_slug: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get detailed budget information for an agent."""
    budget_info = await agent_scheduler_service.check_budget(db, agent_slug)
    if "error" in budget_info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=budget_info["error"],
        )
    return budget_info


@router.patch("/{agent_slug}/budget", response_model=AgentSummary)
@limiter.limit("10/minute")
async def update_agent_budget(
    request: Request,
    agent_slug: str,
    budget_data: BudgetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),  # Admin only
):
    """Update agent budget settings (admin only)."""
    agent = await agent_scheduler_service.update_budget(
        db,
        slug=agent_slug,
        limit=budget_data.budget_limit,
        period=budget_data.budget_period,
        warning_threshold=budget_data.warning_threshold,
    )
    
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    return agent_to_summary(agent)


@router.get("/{agent_slug}/runs", response_model=List[AgentRunSummary])
# Production-reviewed: polled at 15s on agent detail page.
@limiter.limit("90/minute")
async def list_agent_runs(
    request: Request,
    agent_slug: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recent runs for an agent."""
    agent = await agent_scheduler_service.get_agent(db, slug=agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    runs = await agent_scheduler_service.get_recent_runs(db, slug=agent_slug, limit=limit)
    return [run_to_summary(run, agent_slug) for run in runs]


@router.get("/runs/recent", response_model=List[AgentRunSummary])
# Production-reviewed: polled at 15s on agent overview page.
@limiter.limit("90/minute")
async def list_all_recent_runs(
    request: Request,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get recent runs across all agents."""
    runs = await agent_scheduler_service.get_recent_runs(db, limit=limit)
    return [run_to_summary(run, run.agent.slug if run.agent else "unknown") for run in runs]


class RunStatistics(BaseModel):
    """Statistics about agent run performance."""
    agent_slug: str
    total_runs: int
    completed_runs: int
    failed_runs: int
    avg_duration_seconds: Optional[float]
    min_duration_seconds: Optional[float]
    max_duration_seconds: Optional[float]
    avg_items_processed: float
    total_tokens_used: int
    total_cost_usd: float
    schedule_interval_seconds: int
    avg_utilization_percent: Optional[float]  # How much of the scheduled interval is used


@router.get("/{agent_slug}/stats", response_model=RunStatistics)
# Production-reviewed: polled at 15s on agent detail page.
@limiter.limit("90/minute")
async def get_agent_statistics(
    request: Request,
    agent_slug: str,
    days: int = 7,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get run statistics for an agent over a time period."""
    from sqlalchemy import select, func
    from datetime import timedelta
    
    agent = await agent_scheduler_service.get_agent(db, slug=agent_slug)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_slug}' not found",
        )
    
    # Get runs from the last N days
    cutoff = utc_now() - timedelta(days=days)
    
    result = await db.execute(
        select(
            func.count(AgentRun.id).label("total_runs"),
            func.count(AgentRun.id).filter(AgentRun.status == AgentRunStatus.COMPLETED).label("completed_runs"),
            func.count(AgentRun.id).filter(AgentRun.status == AgentRunStatus.FAILED).label("failed_runs"),
            func.avg(AgentRun.duration_seconds).label("avg_duration"),
            func.min(AgentRun.duration_seconds).label("min_duration"),
            func.max(AgentRun.duration_seconds).label("max_duration"),
            func.avg(AgentRun.items_processed).label("avg_items"),
            func.sum(AgentRun.tokens_used).label("total_tokens"),
            func.sum(AgentRun.cost_usd).label("total_cost"),
        )
        .where(AgentRun.agent_id == agent.id)
        .where(AgentRun.created_at >= cutoff)
    )
    
    row = result.first()
    
    # Calculate utilization (how much of scheduled time is used by runs)
    avg_utilization = None
    if row.avg_duration and agent.schedule_interval_seconds > 0:
        avg_utilization = (row.avg_duration / agent.schedule_interval_seconds) * 100
    
    return RunStatistics(
        agent_slug=agent_slug,
        total_runs=row.total_runs or 0,
        completed_runs=row.completed_runs or 0,
        failed_runs=row.failed_runs or 0,
        avg_duration_seconds=row.avg_duration,
        min_duration_seconds=row.min_duration,
        max_duration_seconds=row.max_duration,
        avg_items_processed=row.avg_items or 0,
        total_tokens_used=row.total_tokens or 0,
        total_cost_usd=row.total_cost or 0,
        schedule_interval_seconds=agent.schedule_interval_seconds,
        avg_utilization_percent=avg_utilization,
    )
