"""API endpoints for Opportunity Scout."""

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_user, get_current_admin
from app.models import User
from app.agents import opportunity_scout_agent, AgentContext
from app.services.opportunity_service import opportunity_service
from app.models import (
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    StrategyStatus,
    InsightType,
)
from app.schemas.opportunity import (
    OpportunityResponse,
    OpportunityListResponse,
    OpportunityCreate,
    OpportunityUpdate,
    BulkDismissRequest,
    BulkDismissResponse,
    HopperStatus,
    DiscoveryStrategyResponse,
    DiscoveryStrategyListResponse,
    AgentInsightResponse,
    AgentInsightListResponse,
    UserScoutSettingsResponse,
    UserScoutSettingsUpdate,
    ScoringRubricResponse,
    ScoringRubricCreate,
    ScoutStatistics,
    OpportunityDecision,
    StrategicPlanResponse,
    DiscoveryRunResponse,
    ReflectionResponse,
)

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


# ==========================================================================
# Opportunity Endpoints
# ==========================================================================

@router.get("", response_model=OpportunityListResponse)
async def list_opportunities(
    status: Optional[OpportunityStatus] = None,
    tier: Optional[RankingTier] = None,
    opportunity_type: Optional[OpportunityType] = None,
    include_dismissed: bool = False,
    search: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityListResponse:
    """
    List opportunities with filtering, search, and pagination.
    
    Results are sorted by rank position and score.
    Search matches against title and summary (case-insensitive).
    """
    opportunities, total = await opportunity_service.get_opportunities(
        db=db,
        status=status,
        tier=tier,
        opportunity_type=opportunity_type,
        skip=skip,
        limit=limit,
        include_dismissed=include_dismissed,
        search=search,
    )
    
    # Get hopper status for current user
    hopper = await opportunity_service.get_hopper_status(db, user_id=current_user.id)
    
    return OpportunityListResponse(
        opportunities=[OpportunityResponse.model_validate(o) for o in opportunities],
        total=total,
        hopper_status=HopperStatus(**hopper),
    )


@router.get("/by-tier")
async def get_opportunities_by_tier(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, List[OpportunityResponse]]:
    """Get all opportunities grouped by ranking tier."""
    grouped = await opportunity_service.get_opportunities_by_tier(db)
    
    return {
        tier: [OpportunityResponse.model_validate(o) for o in opps]
        for tier, opps in grouped.items()
    }


@router.get("/hopper", response_model=HopperStatus)
async def get_hopper_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> HopperStatus:
    """Get the current hopper (proposal capacity) status."""
    hopper = await opportunity_service.get_hopper_status(db, user_id=current_user.id)
    return HopperStatus(**hopper)


@router.get("/pipeline")
async def get_pipeline_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Dict[str, Any]:
    """Get opportunity pipeline funnel statistics for dashboard."""
    stats = await opportunity_service.get_pipeline_stats(db)
    return stats


# ==========================================================================
# Settings Endpoints (must be before /{opportunity_id} to avoid route conflict)
# ==========================================================================

@router.get("/settings", response_model=UserScoutSettingsResponse)
async def get_settings(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserScoutSettingsResponse:
    """Get user's scout settings."""
    settings = await opportunity_service.get_user_settings(db, user_id=current_user.id)
    if not settings:
        # Return defaults
        return UserScoutSettingsResponse(
            max_active_proposals=10,
            hopper_warning_threshold=8,
            auto_pause_discovery=True,
            max_backlog_size=200,
            auto_dismiss_below_score=None,
            auto_dismiss_types=[],
            default_sort=None,
            show_unlikely_tier=True,
            preferred_types=[],
            preferred_domains=[],
            excluded_types=[],
            excluded_keywords=[],
            custom_rubric_weights=None,
        )
    return UserScoutSettingsResponse.model_validate(settings)


@router.put("/settings", response_model=UserScoutSettingsResponse)
async def update_settings(
    updates: UserScoutSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserScoutSettingsResponse:
    """Update user's scout settings."""
    settings = await opportunity_service.update_user_settings(
        db=db,
        user_id=current_user.id,
        max_active_proposals=updates.max_active_proposals,
        hopper_warning_threshold=updates.hopper_warning_threshold,
        auto_pause_discovery=updates.auto_pause_discovery,
        max_backlog_size=updates.max_backlog_size,
        auto_dismiss_below_score=updates.auto_dismiss_below_score,
        auto_dismiss_types=updates.auto_dismiss_types,
        default_sort=updates.default_sort,
        show_unlikely_tier=updates.show_unlikely_tier,
        preferred_types=updates.preferred_types,
        preferred_domains=updates.preferred_domains,
        excluded_types=updates.excluded_types,
        excluded_keywords=updates.excluded_keywords,
        custom_rubric_weights=updates.custom_rubric_weights,
    )
    return UserScoutSettingsResponse.model_validate(settings)


# ==========================================================================
# Single Opportunity Endpoints
# ==========================================================================

@router.get("/{opportunity_id}", response_model=OpportunityResponse)
async def get_opportunity(
    opportunity_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityResponse:
    """Get a single opportunity by ID."""
    opportunity = await opportunity_service.get_opportunity(db, opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return OpportunityResponse.model_validate(opportunity)


@router.post("/{opportunity_id}/approve", response_model=OpportunityResponse)
async def approve_opportunity(
    opportunity_id: UUID,
    decision: OpportunityDecision,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityResponse:
    """
    Approve an opportunity to become a proposal.
    
    This will mark the opportunity as approved and eventually
    create a proposal from it.
    """
    opportunity = await opportunity_service.approve_opportunity(
        db=db,
        opportunity_id=opportunity_id,
        user_notes=decision.notes,
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return OpportunityResponse.model_validate(opportunity)


@router.post("/{opportunity_id}/dismiss", response_model=OpportunityResponse)
async def dismiss_opportunity(
    opportunity_id: UUID,
    decision: OpportunityDecision,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityResponse:
    """Dismiss an opportunity."""
    opportunity = await opportunity_service.dismiss_opportunity(
        db=db,
        opportunity_id=opportunity_id,
        reason=decision.notes,
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return OpportunityResponse.model_validate(opportunity)


@router.post("/{opportunity_id}/research", response_model=OpportunityResponse)
async def request_more_research(
    opportunity_id: UUID,
    decision: OpportunityDecision,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OpportunityResponse:
    """Request additional research on an opportunity."""
    # Parse research questions from notes
    questions = []
    if decision.notes:
        questions = [q.strip() for q in decision.notes.split('\n') if q.strip()]
    
    opportunity = await opportunity_service.research_more(
        db=db,
        opportunity_id=opportunity_id,
        research_questions=questions if questions else None,
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return OpportunityResponse.model_validate(opportunity)


@router.post("/bulk-dismiss", response_model=BulkDismissResponse)
async def bulk_dismiss_opportunities(
    request: BulkDismissRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BulkDismissResponse:
    """
    Bulk dismiss multiple opportunities.
    
    Can dismiss by:
    - Specific IDs
    - All in a tier
    - All below a score threshold
    """
    count = await opportunity_service.bulk_dismiss(
        db=db,
        opportunity_ids=request.opportunity_ids,
        tier=request.tier,
        below_score=request.below_score,
        reason=request.reason,
    )
    
    return BulkDismissResponse(
        dismissed_count=count,
        message=f"Dismissed {count} opportunities",
    )


# ==========================================================================
# Strategy Endpoints
# ==========================================================================

@router.get("/strategies", response_model=DiscoveryStrategyListResponse)
async def list_strategies(
    status: Optional[StrategyStatus] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiscoveryStrategyListResponse:
    """List discovery strategies."""
    strategies, total = await opportunity_service.get_strategies(
        db=db,
        status=status,
        skip=skip,
        limit=limit,
    )
    
    return DiscoveryStrategyListResponse(
        strategies=[DiscoveryStrategyResponse.model_validate(s) for s in strategies],
        total=total,
    )


@router.post("/strategies/{strategy_id}/pause", response_model=DiscoveryStrategyResponse)
async def pause_strategy(
    strategy_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> DiscoveryStrategyResponse:
    """Pause a discovery strategy."""
    strategy = await opportunity_service.pause_strategy(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return DiscoveryStrategyResponse.model_validate(strategy)


@router.post("/strategies/{strategy_id}/activate", response_model=DiscoveryStrategyResponse)
async def activate_strategy(
    strategy_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> DiscoveryStrategyResponse:
    """Activate a paused strategy."""
    strategy = await opportunity_service.activate_strategy(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return DiscoveryStrategyResponse.model_validate(strategy)


@router.post("/strategies/{strategy_id}/deprecate", response_model=DiscoveryStrategyResponse)
async def deprecate_strategy(
    strategy_id: UUID,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> DiscoveryStrategyResponse:
    """Deprecate a strategy permanently."""
    strategy = await opportunity_service.deprecate_strategy(db, strategy_id, reason)
    if not strategy:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return DiscoveryStrategyResponse.model_validate(strategy)


# ==========================================================================
# Insights Endpoints
# ==========================================================================

@router.get("/insights", response_model=AgentInsightListResponse)
async def list_insights(
    insight_type: Optional[InsightType] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentInsightListResponse:
    """List agent insights."""
    insights, total = await opportunity_service.get_insights(
        db=db,
        insight_type=insight_type,
        skip=skip,
        limit=limit,
    )
    
    return AgentInsightListResponse(
        insights=[AgentInsightResponse.model_validate(i) for i in insights],
        total=total,
    )


@router.post("/insights/{insight_id}/validate")
async def validate_insight(
    insight_id: UUID,
    is_validated: bool,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentInsightResponse:
    """Validate or invalidate an agent insight."""
    insight = await opportunity_service.validate_insight(
        db=db,
        insight_id=insight_id,
        is_validated=is_validated,
        validation_notes=notes,
    )
    if not insight:
        raise HTTPException(status_code=404, detail="Insight not found")
    return AgentInsightResponse.model_validate(insight)


# ==========================================================================
# Scoring Rubric Endpoints
# ==========================================================================

@router.get("/rubric", response_model=Optional[ScoringRubricResponse])
async def get_active_rubric(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Optional[ScoringRubricResponse]:
    """Get the currently active scoring rubric."""
    rubric = await opportunity_service.get_active_rubric(db)
    if not rubric:
        return None
    return ScoringRubricResponse.model_validate(rubric)


@router.post("/rubric", response_model=ScoringRubricResponse)
async def create_rubric(
    request: ScoringRubricCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> ScoringRubricResponse:
    """Create a new scoring rubric."""
    rubric = await opportunity_service.create_rubric(
        db=db,
        name=request.name,
        factors=request.factors,
        description=request.description,
        activate=request.activate,
    )
    return ScoringRubricResponse.model_validate(rubric)


# ==========================================================================
# Statistics Endpoints
# ==========================================================================

@router.get("/statistics", response_model=ScoutStatistics)
async def get_statistics(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ScoutStatistics:
    """Get overall Opportunity Scout statistics."""
    stats = await opportunity_service.get_scout_statistics(db, days=days)
    return ScoutStatistics(**stats)


# ==========================================================================
# Agent Action Endpoints
# ==========================================================================

@router.post("/agent/plan", response_model=StrategicPlanResponse)
@limiter.limit("5/minute")
async def create_strategic_plan(
    request: Request,
    force_new: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> StrategicPlanResponse:
    """
    Have the agent create or update its strategic plan.
    
    This uses the quality LLM to deeply think about discovery strategies.
    """
    context = AgentContext(db=db)
    result = await opportunity_scout_agent.create_strategic_plan(
        context=context,
        force_new=force_new,
    )
    
    return StrategicPlanResponse(
        success=result.success,
        message=result.message,
        plan=result.data.get("plan") if result.data else None,
        strategies_created=result.data.get("strategies_created", []) if result.data else [],
        tokens_used=result.tokens_used,
        llm_model=result.model_used,
    )


@router.post("/agent/discover", response_model=DiscoveryRunResponse)
@limiter.limit("5/minute")
async def run_discovery(
    request: Request,
    strategy_id: Optional[UUID] = None,
    max_opportunities: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> DiscoveryRunResponse:
    """
    Run discovery to find new opportunities.
    
    Can run a specific strategy or all active strategies.
    """
    context = AgentContext(db=db)
    result = await opportunity_scout_agent.run_discovery(
        context=context,
        strategy_id=strategy_id,
        max_opportunities=max_opportunities,
    )
    
    return DiscoveryRunResponse(
        success=result.success,
        message=result.message,
        opportunities_created=result.data.get("opportunities_created", 0) if result.data else 0,
        strategies_run=result.data.get("strategies_run", 0) if result.data else 0,
        opportunity_ids=result.data.get("opportunity_ids", []) if result.data else [],
        tokens_used=result.tokens_used,
    )


@router.post("/agent/evaluate")
@limiter.limit("5/minute")
async def evaluate_opportunities_endpoint(
    request: Request,
    opportunity_ids: Optional[List[UUID]] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> Dict[str, Any]:
    """
    Evaluate and score discovered opportunities.
    
    Can evaluate specific opportunities or all pending ones.
    """
    context = AgentContext(db=db)
    result = await opportunity_scout_agent.evaluate_opportunities(
        context=context,
        opportunity_ids=opportunity_ids,
    )
    
    return {
        "success": result.success,
        "message": result.message,
        "evaluated": result.data.get("evaluated", 0) if result.data else 0,
        "tokens_used": result.tokens_used,
    }


@router.post("/agent/reflect", response_model=ReflectionResponse)
@limiter.limit("5/minute")
async def reflect_and_learn(
    request: Request,
    deep_reflection: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
) -> ReflectionResponse:
    """
    Have the agent reflect on recent outcomes and extract learnings.
    
    Use deep_reflection=True for more thorough analysis.
    """
    context = AgentContext(db=db)
    result = await opportunity_scout_agent.reflect_and_learn(
        context=context,
        deep_reflection=deep_reflection,
    )
    
    return ReflectionResponse(
        success=result.success,
        message=result.message,
        insights_created=result.data.get("insights_created", 0) if result.data else 0,
        reflection=result.data.get("reflection") if result.data else None,
        tokens_used=result.tokens_used,
        llm_model=result.model_used,
    )
