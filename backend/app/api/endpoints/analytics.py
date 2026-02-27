"""
API endpoints for analytics dashboards.

Provides:
- GET /analytics/tool-operations/summary - Complete tool operations summary
- GET /analytics/tool-operations/alerts - Active alerts requiring attention
- GET /analytics/executions/trends - Tool execution trends (hourly)
- GET /analytics/violations/trends - Rate limit violation trends (daily)
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, get_db
from app.core.rate_limit import limiter
from app.models import User
from app.services.analytics_service import AnalyticsService, get_analytics_service

router = APIRouter()


# =============================================================================
# Response Schemas
# =============================================================================

class HealthSummaryResponse(BaseModel):
    """Summary of tool health statuses."""
    healthy: int
    degraded: int
    unhealthy: int
    unknown: int
    total: int


class ApprovalSummaryResponse(BaseModel):
    """Summary of pending approvals."""
    pending_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    oldest_pending_minutes: Optional[int] = None


class RateLimitAlertResponse(BaseModel):
    """Alert for rate limit approaching threshold."""
    tool_id: Optional[UUID] = None
    tool_name: str
    tool_slug: str
    limit_name: str
    current_usage: int
    max_allowed: int
    usage_percent: float
    period: str
    period_resets_at: Optional[datetime] = None


class UnhealthyToolResponse(BaseModel):
    """Unhealthy or degraded tool info."""
    id: str
    name: str
    slug: str
    health_status: str
    health_message: Optional[str] = None
    last_health_check: Optional[str] = None
    response_time_ms: Optional[int] = None
    unhealthy_minutes: Optional[int] = None


class PendingApprovalResponse(BaseModel):
    """Pending approval info."""
    id: str
    urgency: str
    tool_id: str
    tool_name: str
    tool_slug: str
    reason: str
    estimated_cost: Optional[float] = None
    requested_at: Optional[str] = None
    pending_minutes: Optional[int] = None


class ToolOperationsSummaryResponse(BaseModel):
    """Complete tool operations summary."""
    health: HealthSummaryResponse
    approvals: ApprovalSummaryResponse
    rate_limit_alerts: List[RateLimitAlertResponse]
    unhealthy_tools: List[UnhealthyToolResponse]
    recent_violations_count: int
    pending_approvals: List[PendingApprovalResponse]


class AlertResponse(BaseModel):
    """Active alert requiring attention."""
    id: str
    severity: str
    category: str
    title: str
    message: str
    source_type: str
    source_id: Optional[str] = None
    source_name: Optional[str] = None
    action_url: Optional[str] = None
    created_at: Optional[str] = None


class ExecutionTrendResponse(BaseModel):
    """Tool execution trend data point."""
    hour: Optional[str] = None
    tool_id: str
    tool_slug: str
    tool_name: str
    execution_count: int
    success_count: int
    failure_count: int
    avg_duration_ms: float


class ViolationTrendResponse(BaseModel):
    """Rate limit violation trend data point."""
    day: Optional[str] = None
    tool_id: Optional[str] = None
    tool_slug: str
    tool_name: str
    violation_count: int


class FailureReasonResponse(BaseModel):
    """Top failure reason for an agent."""
    reason: str
    count: int


class AgentPerformanceResponse(BaseModel):
    """Performance metrics for a single agent."""
    agent_id: str
    agent_slug: str
    agent_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float
    avg_duration_seconds: float
    avg_cost_usd: float
    total_cost_usd: float
    avg_items_processed: float
    top_failure_reasons: List[FailureReasonResponse] = []


class CostTrendPointResponse(BaseModel):
    """Single point in cost trend data."""
    date: str
    agent_slug: str
    agent_name: str
    total_cost_usd: float
    run_count: int


class AgentSuggestionResponse(BaseModel):
    """AI-generated optimization suggestion."""
    agent_slug: str
    agent_name: str
    suggestion_type: str  # efficiency, cost, schedule, reliability
    severity: str  # info, warning, recommendation
    title: str
    description: str
    potential_savings: Optional[str] = None
    action: Optional[str] = None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/tool-operations/summary", response_model=ToolOperationsSummaryResponse)
# Production-reviewed: Dashboard panels poll at 10-30s intervals; 30/min caused
# 429s when multiple analytics panels are open simultaneously.
@limiter.limit("60/minute")
async def get_tool_operations_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get comprehensive tool operations summary.
    
    Includes:
    - Health status counts for all implemented tools
    - Pending approval counts by urgency
    - Rate limits approaching threshold (>80%)
    - List of unhealthy/degraded tools
    - Recent rate limit violation count (24h)
    - Top pending approvals ordered by urgency
    
    This endpoint provides all data needed for the Tool Operations Dashboard
    in a single API call.
    """
    service = get_analytics_service(db)
    summary = await service.get_tool_operations_summary()
    
    return ToolOperationsSummaryResponse(
        health=HealthSummaryResponse(
            healthy=summary.health.healthy,
            degraded=summary.health.degraded,
            unhealthy=summary.health.unhealthy,
            unknown=summary.health.unknown,
            total=summary.health.total,
        ),
        approvals=ApprovalSummaryResponse(
            pending_count=summary.approvals.pending_count,
            critical_count=summary.approvals.critical_count,
            high_count=summary.approvals.high_count,
            medium_count=summary.approvals.medium_count,
            low_count=summary.approvals.low_count,
            oldest_pending_minutes=summary.approvals.oldest_pending_minutes,
        ),
        rate_limit_alerts=[
            RateLimitAlertResponse(
                tool_id=alert.tool_id,
                tool_name=alert.tool_name,
                tool_slug=alert.tool_slug,
                limit_name=alert.limit_name,
                current_usage=alert.current_usage,
                max_allowed=alert.max_allowed,
                usage_percent=alert.usage_percent,
                period=alert.period,
                period_resets_at=alert.period_resets_at,
            )
            for alert in summary.rate_limit_alerts
        ],
        unhealthy_tools=[
            UnhealthyToolResponse(**tool)
            for tool in summary.unhealthy_tools
        ],
        recent_violations_count=summary.recent_violations_count,
        pending_approvals=[
            PendingApprovalResponse(**approval)
            for approval in summary.pending_approvals
        ],
    )


@router.get("/alerts", response_model=List[AlertResponse])
# Production-reviewed: UsageAnalyticsPanel polls alerts at 10s intervals.
@limiter.limit("60/minute")
async def get_active_alerts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get all active alerts requiring attention.
    
    Aggregates alerts from multiple sources:
    - Unhealthy or degraded tools
    - Pending approvals (critical and high urgency)
    - Rate limits above 90% usage
    - Agent errors or budget issues
    
    Alerts are sorted by severity (critical first).
    """
    service = get_analytics_service(db)
    alerts = await service.get_active_alerts()
    
    return [AlertResponse(**alert) for alert in alerts]


@router.get("/executions/trends", response_model=List[ExecutionTrendResponse])
# Production-reviewed: Dashboard polls trends for sparkline charts.
@limiter.limit("60/minute")
async def get_execution_trends(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168, description="Hours to look back (max 7 days)"),
    tool_ids: Optional[str] = Query(default=None, description="Comma-separated tool IDs to filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get tool execution trends in hourly buckets.
    
    Returns execution counts, success/failure counts, and average duration
    per tool per hour. Useful for sparkline charts showing tool activity.
    
    Args:
        hours: Number of hours to look back (default 24, max 168)
        tool_ids: Optional comma-separated list of tool UUIDs to filter
    """
    service = get_analytics_service(db)
    
    # Parse tool IDs if provided
    parsed_tool_ids = None
    if tool_ids:
        try:
            parsed_tool_ids = [UUID(tid.strip()) for tid in tool_ids.split(',')]
        except ValueError:
            pass
    
    trends = await service.get_execution_trends(hours=hours, tool_ids=parsed_tool_ids)
    
    return [ExecutionTrendResponse(**trend) for trend in trends]


@router.get("/violations/trends", response_model=List[ViolationTrendResponse])
# Production-reviewed: Violation trend panel polling.
@limiter.limit("60/minute")
async def get_violation_trends(
    request: Request,
    days: int = Query(default=7, ge=1, le=30, description="Days to look back (max 30)"),
    tool_id: Optional[UUID] = Query(default=None, description="Optional tool ID to filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get rate limit violation trends by day.
    
    Returns violation counts per tool per day. Useful for identifying
    patterns and determining if rate limits need adjustment.
    
    Args:
        days: Number of days to look back (default 7, max 30)
        tool_id: Optional tool UUID to filter to a specific tool
    """
    service = get_analytics_service(db)
    trends = await service.get_violation_trends(days=days, tool_id=tool_id)
    
    return [ViolationTrendResponse(**trend) for trend in trends]


@router.get("/alerts/count")
# Production-reviewed: Header badge polls alert count frequently.
@limiter.limit("60/minute")
async def get_alert_count(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get count of active alerts by severity.
    
    Useful for showing alert badge counts in the header without
    fetching full alert details.
    """
    service = get_analytics_service(db)
    alerts = await service.get_active_alerts()
    
    counts = {
        'critical': 0,
        'high': 0,
        'medium': 0,
        'low': 0,
        'total': len(alerts),
    }
    
    for alert in alerts:
        severity = alert.get('severity', 'low')
        if severity in counts:
            counts[severity] += 1
    
    return counts


# =============================================================================
# Agent Performance Endpoints
# =============================================================================

@router.get("/agents/performance", response_model=List[AgentPerformanceResponse])
# Production-reviewed: Agent performance dashboard polling.
@limiter.limit("60/minute")
async def get_agent_performance(
    request: Request,
    days: int = Query(default=7, ge=1, le=90, description="Days to analyze (max 90)"),
    agent_slugs: Optional[str] = Query(default=None, description="Comma-separated agent slugs to filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get performance metrics for agents.
    
    Returns efficiency metrics including:
    - Run counts (total, successful, failed)
    - Success rate
    - Average duration and cost
    - Average items processed per run
    - Top failure reasons
    
    Args:
        days: Number of days to analyze (default 7, max 90)
        agent_slugs: Optional comma-separated list of agent slugs to filter
    """
    service = get_analytics_service(db)
    
    # Parse agent slugs if provided
    parsed_slugs = None
    if agent_slugs:
        parsed_slugs = [s.strip() for s in agent_slugs.split(',') if s.strip()]
    
    metrics = await service.get_agent_performance(days=days, agent_slugs=parsed_slugs)
    
    return [
        AgentPerformanceResponse(
            agent_id=m['agent_id'],
            agent_slug=m['agent_slug'],
            agent_name=m['agent_name'],
            total_runs=m['total_runs'],
            successful_runs=m['successful_runs'],
            failed_runs=m['failed_runs'],
            success_rate=m['success_rate'],
            avg_duration_seconds=m['avg_duration_seconds'],
            avg_cost_usd=m['avg_cost_usd'],
            total_cost_usd=m['total_cost_usd'],
            avg_items_processed=m['avg_items_processed'],
            top_failure_reasons=[
                FailureReasonResponse(**r) for r in m.get('top_failure_reasons', [])
            ],
        )
        for m in metrics
    ]


@router.get("/agents/cost-trend", response_model=List[CostTrendPointResponse])
# Production-reviewed: Cost trend chart polling.
@limiter.limit("60/minute")
async def get_agent_cost_trend(
    request: Request,
    days: int = Query(default=30, ge=1, le=90, description="Days to analyze (max 90)"),
    agent_slugs: Optional[str] = Query(default=None, description="Comma-separated agent slugs to filter"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get daily cost breakdown by agent.
    
    Returns costs aggregated by day for trend visualization.
    Admin only - contains cost data.
    
    Args:
        days: Number of days to analyze (default 30, max 90)
        agent_slugs: Optional comma-separated list of agent slugs to filter
    """
    service = get_analytics_service(db)
    
    # Parse agent slugs if provided
    parsed_slugs = None
    if agent_slugs:
        parsed_slugs = [s.strip() for s in agent_slugs.split(',') if s.strip()]
    
    trends = await service.get_agent_cost_trend(days=days, agent_slugs=parsed_slugs)
    
    return [CostTrendPointResponse(**t) for t in trends]


@router.get("/agents/suggestions", response_model=List[AgentSuggestionResponse])
# Production-reviewed: Suggestions panel refresh.
@limiter.limit("60/minute")
async def get_agent_suggestions(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get AI-generated optimization suggestions for agents.
    
    Analyzes patterns to identify:
    - High failure rates with common causes
    - Low yield agents (running frequently but finding little)
    - Cost optimization opportunities
    - Schedule optimization suggestions
    
    Admin only - contains operational recommendations.
    """
    service = get_analytics_service(db)
    suggestions = await service.get_agent_suggestions()
    
    return [AgentSuggestionResponse(**s) for s in suggestions]


# =============================================================================
# Campaign Intelligence Endpoints
# =============================================================================

class PatternSummaryResponse(BaseModel):
    """Summary of a campaign pattern for intelligence dashboard."""
    id: str
    name: str
    description: str
    pattern_type: str
    confidence_score: float
    times_applied: int
    times_successful: int
    success_rate: float
    pattern_data: dict
    tags: List[str]
    source_campaign_id: Optional[str]
    last_applied_at: Optional[str]
    created_at: Optional[str]


class LessonSummaryResponse(BaseModel):
    """Summary of a campaign lesson for intelligence dashboard."""
    id: str
    title: str
    description: str
    category: str
    impact_severity: str
    budget_impact: Optional[float]
    time_impact_minutes: Optional[int]
    prevention_steps: List[str]
    detection_signals: List[str]
    source_campaign_id: str
    times_applied: int
    tags: List[str]
    created_at: Optional[str]


class EffectivenessTrendResponse(BaseModel):
    """Weekly campaign effectiveness trend."""
    week: Optional[str]
    total_campaigns: int
    successful_campaigns: int
    success_rate: float


class IntelligenceSummaryResponse(BaseModel):
    """Overall campaign intelligence summary."""
    patterns: dict
    lessons: dict
    campaigns: dict


@router.get(
    "/campaigns/patterns",
    response_model=List[PatternSummaryResponse],
    summary="Get top campaign patterns"
)
# Production-reviewed: Campaign intelligence dashboard polling.
@limiter.limit("60/minute")
async def get_top_patterns(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    min_confidence: float = Query(0.5, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get top performing campaign patterns.
    
    Returns patterns sorted by success rate and usage count.
    Includes both user's patterns and global patterns.
    """
    service = get_analytics_service(db)
    patterns = await service.get_top_patterns(
        user_id=current_user.id,
        limit=limit,
        min_confidence=min_confidence,
    )
    
    return [PatternSummaryResponse(**p) for p in patterns]


@router.get(
    "/campaigns/lessons",
    response_model=List[LessonSummaryResponse],
    summary="Get recent campaign lessons"
)
# Production-reviewed: Campaign lessons panel polling.
@limiter.limit("60/minute")
async def get_recent_lessons(
    request: Request,
    days: int = Query(30, ge=1, le=90),
    limit: int = Query(10, ge=1, le=50),
    severity: Optional[str] = Query(None, pattern="^(low|medium|high|critical)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get recent lessons learned from campaigns.
    
    Returns lessons sorted by severity and recency.
    Useful for understanding what went wrong and how to prevent it.
    """
    service = get_analytics_service(db)
    lessons = await service.get_recent_lessons(
        user_id=current_user.id,
        days=days,
        limit=limit,
        severity_filter=severity,
    )
    
    return [LessonSummaryResponse(**l) for l in lessons]


@router.get(
    "/campaigns/effectiveness",
    response_model=List[EffectivenessTrendResponse],
    summary="Get campaign effectiveness trends"
)
# Production-reviewed: Effectiveness trend chart polling.
@limiter.limit("60/minute")
async def get_effectiveness_trend(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get campaign effectiveness over time (weekly).
    
    Shows how campaign success rates trend over the period.
    Useful for understanding if patterns and lessons are improving outcomes.
    """
    service = get_analytics_service(db)
    trends = await service.get_pattern_effectiveness_trend(
        user_id=current_user.id,
        days=days,
    )
    
    return [EffectivenessTrendResponse(**t) for t in trends]


@router.get(
    "/campaigns/summary",
    response_model=IntelligenceSummaryResponse,
    summary="Get campaign intelligence summary"
)
# Production-reviewed: Intelligence summary dashboard polling.
@limiter.limit("60/minute")
async def get_intelligence_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get overall campaign intelligence summary stats.
    
    Provides:
    - Pattern statistics (total, active, avg confidence)
    - Lesson statistics (total, by severity)
    - Campaign statistics (success rates)
    """
    service = get_analytics_service(db)
    summary = await service.get_campaign_intelligence_summary(
        user_id=current_user.id,
    )
    
    return IntelligenceSummaryResponse(**summary)
