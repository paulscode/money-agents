"""API endpoints for Campaign Learning features (Phase 5).

Provides access to:
- Campaign Patterns (discovered successful execution sequences)
- Campaign Lessons (failure analysis and prevention)
- Plan Revisions (tracking plan evolution)
- Proactive Suggestions (AI-generated optimization recommendations)
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel, Field
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_active_user, get_current_admin
from app.models import (
    User, Campaign,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus
)


router = APIRouter()


# =============================================================================
# Pydantic Models
# =============================================================================

class PatternResponse(BaseModel):
    """Response model for a campaign pattern."""
    id: str
    name: str
    description: str
    pattern_type: str
    status: str
    confidence_score: float
    pattern_data: dict
    applicability_conditions: dict
    times_applied: int
    times_successful: int
    success_rate: float
    last_applied_at: Optional[datetime]
    source_campaign_id: Optional[str]
    is_global: bool
    tags: Optional[List[str]]
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class PatternListResponse(BaseModel):
    """Paginated list of patterns."""
    patterns: List[PatternResponse]
    total: int
    limit: int
    offset: int


class LessonResponse(BaseModel):
    """Response model for a campaign lesson."""
    id: str
    title: str
    description: str
    category: str
    context: dict
    trigger_event: str
    impact_severity: str
    budget_impact: Optional[float]
    time_impact_minutes: Optional[int]
    prevention_steps: List[str]
    detection_signals: List[str]
    source_campaign_id: str
    times_applied: int
    tags: Optional[List[str]]
    created_at: datetime
    
    class Config:
        from_attributes = True


class LessonListResponse(BaseModel):
    """Paginated list of lessons."""
    lessons: List[LessonResponse]
    total: int
    limit: int
    offset: int


class RevisionResponse(BaseModel):
    """Response model for a plan revision."""
    id: str
    campaign_id: str
    revision_number: int
    trigger: str
    trigger_details: str
    plan_before: dict
    plan_after: dict
    changes_summary: str
    tasks_added: int
    tasks_removed: int
    tasks_modified: int
    streams_added: int
    streams_removed: int
    reasoning: str
    expected_improvement: Optional[str]
    outcome_assessed: bool
    outcome_success: Optional[bool]
    outcome_notes: Optional[str]
    initiated_by: str
    approved_by_user: bool
    created_at: datetime
    outcome_assessed_at: Optional[datetime]
    
    class Config:
        from_attributes = True


class SuggestionResponse(BaseModel):
    """Response model for a proactive suggestion."""
    id: str
    campaign_id: str
    suggestion_type: str
    title: str
    description: str
    status: str
    urgency: str
    confidence: float
    evidence: dict
    based_on_patterns: Optional[List[str]]
    based_on_lessons: Optional[List[str]]
    recommended_action: dict
    estimated_benefit: Optional[str]
    estimated_cost: Optional[float]
    can_auto_apply: bool
    user_feedback: Optional[str]
    outcome_tracked: bool
    actual_benefit: Optional[str]
    expires_at: Optional[datetime]
    is_expired: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


class SuggestionListResponse(BaseModel):
    """Paginated list of suggestions."""
    suggestions: List[SuggestionResponse]
    total: int
    pending_count: int
    limit: int
    offset: int


class SuggestionActionRequest(BaseModel):
    """Request to accept or reject a suggestion."""
    action: str = Field(..., pattern="^(accept|reject)$")
    feedback: Optional[str] = None


class LearningStatsResponse(BaseModel):
    """Statistics about campaign learning."""
    total_patterns: int
    active_patterns: int
    avg_pattern_success_rate: float
    total_lessons: int
    lessons_by_category: dict
    total_suggestions: int
    suggestions_accepted: int
    suggestions_rejected: int
    acceptance_rate: float


# =============================================================================
# Pattern Endpoints
# =============================================================================

@router.get("/patterns", response_model=PatternListResponse)
@limiter.limit("30/minute")
async def list_patterns(
    request: Request,
    pattern_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    min_confidence: float = Query(0.0, ge=0.0, le=1.0),
    include_global: bool = True,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List campaign patterns with optional filtering.
    
    - **pattern_type**: Filter by type (execution_sequence, tool_combination, etc.)
    - **status_filter**: Filter by status (active, deprecated, experimental)
    - **min_confidence**: Minimum confidence score
    - **include_global**: Include global patterns available to all users
    """
    from sqlalchemy import or_
    
    # Build query
    conditions = []
    
    # User's own patterns or global patterns
    if include_global:
        conditions.append(
            or_(
                CampaignPattern.user_id == current_user.id,
                CampaignPattern.is_global == True
            )
        )
    else:
        conditions.append(CampaignPattern.user_id == current_user.id)
    
    if pattern_type:
        try:
            pt = PatternType(pattern_type)
            conditions.append(CampaignPattern.pattern_type == pt)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid pattern_type: {pattern_type}"
            )
    
    if status_filter:
        try:
            ps = PatternStatus(status_filter)
            conditions.append(CampaignPattern.status == ps)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}"
            )
    
    conditions.append(CampaignPattern.confidence_score >= min_confidence)
    
    # Count total
    count_query = select(func.count(CampaignPattern.id)).where(*conditions)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # Fetch patterns
    query = (
        select(CampaignPattern)
        .where(*conditions)
        .order_by(desc(CampaignPattern.confidence_score))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    patterns = result.scalars().all()
    
    return PatternListResponse(
        patterns=[
            PatternResponse(
                id=str(p.id),
                name=p.name,
                description=p.description,
                pattern_type=p.pattern_type.value,
                status=p.status.value,
                confidence_score=p.confidence_score,
                pattern_data=p.pattern_data,
                applicability_conditions=p.applicability_conditions or {},
                times_applied=p.times_applied,
                times_successful=p.times_successful,
                success_rate=p.success_rate,
                last_applied_at=p.last_applied_at,
                source_campaign_id=str(p.source_campaign_id) if p.source_campaign_id else None,
                is_global=p.is_global,
                tags=p.tags,
                created_at=p.created_at,
                updated_at=p.updated_at
            )
            for p in patterns
        ],
        total=total,
        limit=limit,
        offset=offset
    )


@router.get("/patterns/{pattern_id}", response_model=PatternResponse)
@limiter.limit("30/minute")
async def get_pattern(
    request: Request,
    pattern_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a specific pattern by ID."""
    from sqlalchemy import or_
    
    result = await db.execute(
        select(CampaignPattern).where(
            CampaignPattern.id == pattern_id,
            or_(
                CampaignPattern.user_id == current_user.id,
                CampaignPattern.is_global == True
            )
        )
    )
    pattern = result.scalar_one_or_none()
    
    if not pattern:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pattern not found"
        )
    
    return PatternResponse(
        id=str(pattern.id),
        name=pattern.name,
        description=pattern.description,
        pattern_type=pattern.pattern_type.value,
        status=pattern.status.value,
        confidence_score=pattern.confidence_score,
        pattern_data=pattern.pattern_data,
        applicability_conditions=pattern.applicability_conditions or {},
        times_applied=pattern.times_applied,
        times_successful=pattern.times_successful,
        success_rate=pattern.success_rate,
        last_applied_at=pattern.last_applied_at,
        source_campaign_id=str(pattern.source_campaign_id) if pattern.source_campaign_id else None,
        is_global=pattern.is_global,
        tags=pattern.tags,
        created_at=pattern.created_at,
        updated_at=pattern.updated_at
    )


# =============================================================================
# Lesson Endpoints
# =============================================================================

@router.get("/lessons", response_model=LessonListResponse)
@limiter.limit("30/minute")
async def list_lessons(
    request: Request,
    category: Optional[str] = None,
    severity: Optional[str] = None,
    campaign_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    List campaign lessons with optional filtering.
    
    - **category**: Filter by category (failure, inefficiency, user_friction, etc.)
    - **severity**: Filter by impact severity (low, medium, high, critical)
    - **campaign_id**: Filter to specific campaign
    """
    conditions = [CampaignLesson.user_id == current_user.id]
    
    if category:
        try:
            lc = LessonCategory(category)
            conditions.append(CampaignLesson.category == lc)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid category: {category}"
            )
    
    if severity:
        if severity not in ['low', 'medium', 'high', 'critical']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid severity: {severity}"
            )
        conditions.append(CampaignLesson.impact_severity == severity)
    
    if campaign_id:
        conditions.append(CampaignLesson.source_campaign_id == campaign_id)
    
    # Count total
    count_query = select(func.count(CampaignLesson.id)).where(*conditions)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # Fetch lessons
    query = (
        select(CampaignLesson)
        .where(*conditions)
        .order_by(desc(CampaignLesson.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    lessons = result.scalars().all()
    
    return LessonListResponse(
        lessons=[
            LessonResponse(
                id=str(l.id),
                title=l.title,
                description=l.description,
                category=l.category.value,
                context=l.context,
                trigger_event=l.trigger_event,
                impact_severity=l.impact_severity,
                budget_impact=l.budget_impact,
                time_impact_minutes=l.time_impact_minutes,
                prevention_steps=l.prevention_steps,
                detection_signals=l.detection_signals or [],
                source_campaign_id=str(l.source_campaign_id),
                times_applied=l.times_applied,
                tags=l.tags,
                created_at=l.created_at
            )
            for l in lessons
        ],
        total=total,
        limit=limit,
        offset=offset
    )


@router.get("/lessons/{lesson_id}", response_model=LessonResponse)
@limiter.limit("30/minute")
async def get_lesson(
    request: Request,
    lesson_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a specific lesson by ID."""
    result = await db.execute(
        select(CampaignLesson).where(
            CampaignLesson.id == lesson_id,
            CampaignLesson.user_id == current_user.id
        )
    )
    lesson = result.scalar_one_or_none()
    
    if not lesson:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Lesson not found"
        )
    
    return LessonResponse(
        id=str(lesson.id),
        title=lesson.title,
        description=lesson.description,
        category=lesson.category.value,
        context=lesson.context,
        trigger_event=lesson.trigger_event,
        impact_severity=lesson.impact_severity,
        budget_impact=lesson.budget_impact,
        time_impact_minutes=lesson.time_impact_minutes,
        prevention_steps=lesson.prevention_steps,
        detection_signals=lesson.detection_signals or [],
        source_campaign_id=str(lesson.source_campaign_id),
        times_applied=lesson.times_applied,
        tags=lesson.tags,
        created_at=lesson.created_at
    )


# =============================================================================
# Campaign-Specific Endpoints
# =============================================================================

@router.get("/campaigns/{campaign_id}/revisions", response_model=List[RevisionResponse])
@limiter.limit("30/minute")
async def get_campaign_revisions(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all plan revisions for a specific campaign."""
    # Verify campaign belongs to user
    campaign_result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = campaign_result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Fetch revisions
    result = await db.execute(
        select(PlanRevision)
        .where(PlanRevision.campaign_id == campaign_id)
        .order_by(PlanRevision.revision_number)
    )
    revisions = result.scalars().all()
    
    return [
        RevisionResponse(
            id=str(r.id),
            campaign_id=str(r.campaign_id),
            revision_number=r.revision_number,
            trigger=r.trigger.value,
            trigger_details=r.trigger_details,
            plan_before=r.plan_before,
            plan_after=r.plan_after,
            changes_summary=r.changes_summary,
            tasks_added=r.tasks_added,
            tasks_removed=r.tasks_removed,
            tasks_modified=r.tasks_modified,
            streams_added=r.streams_added,
            streams_removed=r.streams_removed,
            reasoning=r.reasoning,
            expected_improvement=r.expected_improvement,
            outcome_assessed=r.outcome_assessed,
            outcome_success=r.outcome_success,
            outcome_notes=r.outcome_notes,
            initiated_by=r.initiated_by,
            approved_by_user=r.approved_by_user,
            created_at=r.created_at,
            outcome_assessed_at=r.outcome_assessed_at
        )
        for r in revisions
    ]


@router.get("/campaigns/{campaign_id}/suggestions", response_model=SuggestionListResponse)
@limiter.limit("30/minute")
async def get_campaign_suggestions(
    request: Request,
    campaign_id: UUID,
    status_filter: Optional[str] = None,
    suggestion_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get proactive suggestions for a specific campaign.
    
    - **status_filter**: Filter by status (pending, accepted, rejected, auto_applied)
    - **suggestion_type**: Filter by type (optimization, warning, opportunity, etc.)
    """
    # Verify campaign belongs to user
    campaign_result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = campaign_result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    conditions = [ProactiveSuggestion.campaign_id == campaign_id]
    
    if status_filter:
        try:
            ss = SuggestionStatus(status_filter)
            conditions.append(ProactiveSuggestion.status == ss)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}"
            )
    
    if suggestion_type:
        try:
            st = SuggestionType(suggestion_type)
            conditions.append(ProactiveSuggestion.suggestion_type == st)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid suggestion_type: {suggestion_type}"
            )
    
    # Count total
    count_query = select(func.count(ProactiveSuggestion.id)).where(*conditions)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # Count pending
    pending_query = select(func.count(ProactiveSuggestion.id)).where(
        ProactiveSuggestion.campaign_id == campaign_id,
        ProactiveSuggestion.status == SuggestionStatus.PENDING
    )
    pending_result = await db.execute(pending_query)
    pending_count = pending_result.scalar() or 0
    
    # Fetch suggestions
    query = (
        select(ProactiveSuggestion)
        .where(*conditions)
        .order_by(desc(ProactiveSuggestion.created_at))
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    suggestions = result.scalars().all()
    
    return SuggestionListResponse(
        suggestions=[
            SuggestionResponse(
                id=str(s.id),
                campaign_id=str(s.campaign_id),
                suggestion_type=s.suggestion_type.value,
                title=s.title,
                description=s.description,
                status=s.status.value,
                urgency=s.urgency,
                confidence=s.confidence,
                evidence=s.evidence,
                based_on_patterns=s.based_on_patterns,
                based_on_lessons=s.based_on_lessons,
                recommended_action=s.recommended_action,
                estimated_benefit=s.estimated_benefit,
                estimated_cost=s.estimated_cost,
                can_auto_apply=s.can_auto_apply,
                user_feedback=s.user_feedback,
                outcome_tracked=s.outcome_tracked,
                actual_benefit=s.actual_benefit,
                expires_at=s.expires_at,
                is_expired=s.is_expired,
                created_at=s.created_at
            )
            for s in suggestions
        ],
        total=total,
        pending_count=pending_count,
        limit=limit,
        offset=offset
    )


@router.post("/suggestions/{suggestion_id}/respond", response_model=SuggestionResponse)
@limiter.limit("30/minute")
async def respond_to_suggestion(
    request: Request,
    suggestion_id: UUID,
    action: SuggestionActionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Accept or reject a proactive suggestion.
    
    - **action**: 'accept' or 'reject'
    - **feedback**: Optional feedback about why
    """
    from sqlalchemy.orm import selectinload
    
    # Get suggestion with campaign
    result = await db.execute(
        select(ProactiveSuggestion)
        .options(selectinload(ProactiveSuggestion.campaign))
        .where(ProactiveSuggestion.id == suggestion_id)
    )
    suggestion = result.scalar_one_or_none()
    
    if not suggestion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Suggestion not found"
        )
    
    # Verify user owns the campaign
    if suggestion.campaign.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to respond to this suggestion"
        )
    
    if suggestion.status != SuggestionStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Suggestion already has status: {suggestion.status.value}"
        )
    
    # Update suggestion
    suggestion.status = SuggestionStatus.ACCEPTED if action.action == 'accept' else SuggestionStatus.REJECTED
    suggestion.user_feedback = action.feedback
    suggestion.user_response_at = utc_now()
    
    await db.commit()
    await db.refresh(suggestion)
    
    return SuggestionResponse(
        id=str(suggestion.id),
        campaign_id=str(suggestion.campaign_id),
        suggestion_type=suggestion.suggestion_type.value,
        title=suggestion.title,
        description=suggestion.description,
        status=suggestion.status.value,
        urgency=suggestion.urgency,
        confidence=suggestion.confidence,
        evidence=suggestion.evidence,
        based_on_patterns=suggestion.based_on_patterns,
        based_on_lessons=suggestion.based_on_lessons,
        recommended_action=suggestion.recommended_action,
        estimated_benefit=suggestion.estimated_benefit,
        estimated_cost=suggestion.estimated_cost,
        can_auto_apply=suggestion.can_auto_apply,
        user_feedback=suggestion.user_feedback,
        outcome_tracked=suggestion.outcome_tracked,
        actual_benefit=suggestion.actual_benefit,
        expires_at=suggestion.expires_at,
        is_expired=suggestion.is_expired,
        created_at=suggestion.created_at
    )


# =============================================================================
# Statistics Endpoint
# =============================================================================

@router.get("/stats", response_model=LearningStatsResponse)
@limiter.limit("30/minute")
async def get_learning_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get aggregate statistics about campaign learning for the user."""
    from sqlalchemy import or_
    
    # Pattern stats
    pattern_result = await db.execute(
        select(
            func.count(CampaignPattern.id),
            func.count(CampaignPattern.id).filter(CampaignPattern.status == PatternStatus.ACTIVE),
            func.avg(
                CampaignPattern.times_successful * 1.0 / 
                func.nullif(CampaignPattern.times_applied, 0)
            )
        ).where(
            or_(
                CampaignPattern.user_id == current_user.id,
                CampaignPattern.is_global == True
            )
        )
    )
    pattern_stats = pattern_result.one()
    
    # Lesson stats by category
    lesson_result = await db.execute(
        select(CampaignLesson.category, func.count(CampaignLesson.id))
        .where(CampaignLesson.user_id == current_user.id)
        .group_by(CampaignLesson.category)
    )
    lessons_by_category = {row[0].value: row[1] for row in lesson_result.all()}
    total_lessons = sum(lessons_by_category.values())
    
    # Suggestion stats
    suggestion_result = await db.execute(
        select(
            func.count(ProactiveSuggestion.id),
            func.count(ProactiveSuggestion.id).filter(
                ProactiveSuggestion.status == SuggestionStatus.ACCEPTED
            ),
            func.count(ProactiveSuggestion.id).filter(
                ProactiveSuggestion.status == SuggestionStatus.REJECTED
            )
        ).select_from(ProactiveSuggestion).join(Campaign).where(
            Campaign.user_id == current_user.id
        )
    )
    suggestion_stats = suggestion_result.one()
    
    total_suggestions = suggestion_stats[0] or 0
    accepted = suggestion_stats[1] or 0
    rejected = suggestion_stats[2] or 0
    
    acceptance_rate = 0.0
    if accepted + rejected > 0:
        acceptance_rate = accepted / (accepted + rejected)
    
    return LearningStatsResponse(
        total_patterns=pattern_stats[0] or 0,
        active_patterns=pattern_stats[1] or 0,
        avg_pattern_success_rate=float(pattern_stats[2]) if pattern_stats[2] else 0.0,
        total_lessons=total_lessons,
        lessons_by_category=lessons_by_category,
        total_suggestions=total_suggestions,
        suggestions_accepted=accepted,
        suggestions_rejected=rejected,
        acceptance_rate=acceptance_rate
    )
