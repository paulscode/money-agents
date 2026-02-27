from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from app.core.datetime_utils import utc_now, ensure_utc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, exists
from sqlalchemy.orm import selectinload
from uuid import UUID
from typing import Optional

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_active_user
from app.models import User, Proposal, Campaign, Opportunity
from app.models.opportunity import OpportunityStatus
from app.schemas import ProposalCreate, ProposalUpdate, ProposalResponse


router = APIRouter()


@router.post("/", response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_proposal(
    request: Request,
    proposal_data: ProposalCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new proposal."""
    proposal = Proposal(
        user_id=current_user.id,
        **proposal_data.model_dump()
    )
    
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    
    return proposal


@router.get("/", response_model=list[ProposalResponse])
@limiter.limit("120/minute")
async def list_proposals(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    status: Optional[str] = None,
    has_campaign: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all proposals (visible to all users).
    
    Args:
        has_campaign: Filter by whether proposal has an associated campaign.
                      True = only proposals with campaigns
                      False = only proposals without campaigns (default view)
                      None = all proposals
    """
    query = select(Proposal).options(selectinload(Proposal.campaigns))
    
    if status:
        query = query.where(Proposal.status == status)
    
    # Filter by has_campaign if specified
    if has_campaign is not None:
        campaign_exists = exists().where(Campaign.proposal_id == Proposal.id)
        if has_campaign:
            query = query.where(campaign_exists)
        else:
            query = query.where(~campaign_exists)
    
    query = query.order_by(desc(Proposal.submitted_at)).offset(skip).limit(limit)
    
    result = await db.execute(query)
    proposals = result.scalars().all()
    
    # Build response with has_campaign field
    response = []
    for proposal in proposals:
        campaigns = proposal.campaigns
        proposal_dict = {
            "id": proposal.id,
            "user_id": proposal.user_id,
            "agent_id": proposal.agent_id,
            "title": proposal.title,
            "summary": proposal.summary,
            "detailed_description": proposal.detailed_description,
            "status": proposal.status,
            "initial_budget": proposal.initial_budget,
            "recurring_costs": proposal.recurring_costs,
            "expected_returns": proposal.expected_returns,
            "risk_level": proposal.risk_level,
            "risk_description": proposal.risk_description,
            "stop_loss_threshold": proposal.stop_loss_threshold,
            "success_criteria": proposal.success_criteria,
            "required_tools": proposal.required_tools,
            "required_inputs": proposal.required_inputs,
            "implementation_timeline": proposal.implementation_timeline,
            "similar_proposals": proposal.similar_proposals,
            "similarity_score": proposal.similarity_score,
            "research_context": proposal.research_context,
            "source_opportunity_id": proposal.source_opportunity_id,
            "meta_data": proposal.meta_data,
            "source": proposal.source,
            "tags": proposal.tags,
            "submitted_at": proposal.submitted_at,
            "reviewed_at": proposal.reviewed_at,
            "created_at": proposal.created_at,
            "updated_at": proposal.updated_at,
            "has_campaign": len(campaigns) > 0,
            "campaign_id": campaigns[0].id if campaigns else None,
        }
        response.append(proposal_dict)
    
    return response


@router.get("/{proposal_id}", response_model=ProposalResponse)
@limiter.limit("120/minute")
async def get_proposal(
    request: Request,
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a specific proposal (visible to all users)."""
    result = await db.execute(
        select(Proposal)
        .options(selectinload(Proposal.campaigns))
        .where(Proposal.id == proposal_id)
    )
    proposal = result.scalar_one_or_none()
    
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found"
        )
    
    # Build response with has_campaign field
    campaigns = proposal.campaigns
    return {
        "id": proposal.id,
        "user_id": proposal.user_id,
        "agent_id": proposal.agent_id,
        "title": proposal.title,
        "summary": proposal.summary,
        "detailed_description": proposal.detailed_description,
        "status": proposal.status,
        "initial_budget": proposal.initial_budget,
        "recurring_costs": proposal.recurring_costs,
        "expected_returns": proposal.expected_returns,
        "risk_level": proposal.risk_level,
        "risk_description": proposal.risk_description,
        "stop_loss_threshold": proposal.stop_loss_threshold,
        "success_criteria": proposal.success_criteria,
        "required_tools": proposal.required_tools,
        "required_inputs": proposal.required_inputs,
        "implementation_timeline": proposal.implementation_timeline,
        "similar_proposals": proposal.similar_proposals,
        "similarity_score": proposal.similarity_score,
        "research_context": proposal.research_context,
        "source_opportunity_id": proposal.source_opportunity_id,
        "meta_data": proposal.meta_data,
        "source": proposal.source,
        "tags": proposal.tags,
        "submitted_at": proposal.submitted_at,
        "reviewed_at": proposal.reviewed_at,
        "created_at": proposal.created_at,
        "updated_at": proposal.updated_at,
        "has_campaign": len(campaigns) > 0,
        "campaign_id": campaigns[0].id if campaigns else None,
    }


@router.put("/{proposal_id}", response_model=ProposalResponse)
@limiter.limit("60/minute")
async def update_proposal(
    request: Request,
    proposal_id: UUID,
    proposal_update: ProposalUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Update a proposal.
    
    When status is changed to 'rejected', the linked opportunity
    is also dismissed to prevent it from appearing again.
    """
    result = await db.execute(
        select(Proposal).where(
            Proposal.id == proposal_id,
            Proposal.user_id == current_user.id
        )
    )
    proposal = result.scalar_one_or_none()
    
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found"
        )
    
    # Check if status is changing to rejected
    from app.models import ProposalStatus
    is_rejecting = (
        proposal_update.status == ProposalStatus.REJECTED 
        and proposal.status != ProposalStatus.REJECTED
    )
    
    # Update fields
    update_data = proposal_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(proposal, field, value)
    
    # Update reviewed_at if status changed
    if proposal_update.status and proposal_update.status != proposal.status:
        from datetime import datetime
        proposal.reviewed_at = utc_now()
    
    # If rejecting, also dismiss the linked opportunity
    if is_rejecting:
        opp_result = await db.execute(
            select(Opportunity).where(Opportunity.proposal_id == proposal_id)
        )
        opportunity = opp_result.scalar_one_or_none()
        
        if opportunity:
            opportunity.status = OpportunityStatus.DISMISSED
            opportunity.user_decision = "dismissed"
            opportunity.user_feedback = "Proposal rejected by user"
    
    await db.commit()
    await db.refresh(proposal)
    
    return proposal


@router.delete("/{proposal_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_proposal(
    request: Request,
    proposal_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Delete a proposal and dismiss any linked opportunity.
    
    When a proposal is deleted (not rejected), its source opportunity
    is also dismissed to prevent it from appearing again in the review queue.
    """
    result = await db.execute(
        select(Proposal).where(
            Proposal.id == proposal_id,
            Proposal.user_id == current_user.id
        )
    )
    proposal = result.scalar_one_or_none()
    
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found"
        )
    
    # Find and dismiss any linked opportunity
    opp_result = await db.execute(
        select(Opportunity).where(Opportunity.proposal_id == proposal_id)
    )
    opportunity = opp_result.scalar_one_or_none()
    
    if opportunity:
        # Clear the proposal link and dismiss the opportunity
        opportunity.proposal_id = None
        opportunity.status = OpportunityStatus.DISMISSED
        opportunity.user_decision = "dismissed"
        opportunity.user_feedback = "Proposal deleted by user"
    
    await db.delete(proposal)
    await db.commit()


@router.post("/from-pattern/{pattern_id}", response_model=ProposalResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_proposal_from_pattern(
    request: Request,
    pattern_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Create a draft proposal from a successful campaign pattern.
    
    The proposal is pre-populated with:
    - Title based on pattern name
    - Summary describing the pattern source
    - Required tools from pattern data
    - Implementation timeline from pattern data
    - Budget estimate from pattern history
    - Risk description noting related lessons
    
    The proposal is created in 'draft' status for user review.
    """
    from app.models.campaign_learning import CampaignPattern, CampaignLesson
    from sqlalchemy import or_
    
    # Fetch the pattern
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
            detail="Pattern not found or not accessible"
        )
    
    # Check for related lessons to add as risk notes
    related_lessons = await db.execute(
        select(CampaignLesson)
        .where(
            CampaignLesson.user_id == current_user.id,
            CampaignLesson.tags.op('@>')(pattern.tags or [])  # Overlapping tags
        )
        .order_by(CampaignLesson.impact_severity.desc())
        .limit(3)
    )
    lessons = related_lessons.scalars().all()
    
    # Build risk description from pattern and lessons
    risk_notes = []
    if lessons:
        for lesson in lessons:
            risk_notes.append(f"⚠️ {lesson.impact_severity.upper()}: {lesson.title}")
    
    risk_description = (
        f"Based on pattern '{pattern.name}' with {pattern.confidence_score:.0%} confidence.\n"
        f"Applied {pattern.times_applied} times with {pattern.success_rate:.0%} success rate.\n"
    )
    if risk_notes:
        risk_description += "\nRelated lessons:\n" + "\n".join(risk_notes)
    
    # Extract tools from pattern data
    pattern_data = pattern.pattern_data or {}
    required_tools = {}
    if 'tools' in pattern_data:
        for tool in pattern_data['tools']:
            if isinstance(tool, str):
                required_tools[tool] = {"required": True}
            elif isinstance(tool, dict):
                tool_name = tool.get('name') or tool.get('slug', 'unknown')
                required_tools[tool_name] = {"required": True, **tool}
    
    # Extract timeline from pattern data
    implementation_timeline = pattern_data.get('timeline') or pattern_data.get('implementation_timeline')
    if not implementation_timeline and 'tasks' in pattern_data:
        # Build a basic timeline from tasks
        implementation_timeline = {
            "estimated_duration": "Based on pattern",
            "phases": [{"name": "Execution", "tasks": pattern_data['tasks']}]
        }
    
    # Estimate budget from pattern history (average cost if available)
    initial_budget = pattern_data.get('estimated_budget', 100.0)
    
    # Create the proposal
    proposal = Proposal(
        user_id=current_user.id,
        title=f"New Campaign from Pattern: {pattern.name}",
        summary=f"Campaign based on successful pattern (confidence: {pattern.confidence_score:.0%}, {pattern.times_applied} applications)",
        detailed_description=(
            f"This proposal is auto-generated from the pattern '{pattern.name}'.\n\n"
            f"**Pattern Type:** {pattern.pattern_type.value}\n"
            f"**Description:** {pattern.description}\n\n"
            "Please review and customize before approval."
        ),
        initial_budget=initial_budget,
        risk_level="medium",  # Default, user should adjust
        risk_description=risk_description,
        stop_loss_threshold={"max_budget": initial_budget * 1.5, "max_failures": 3},
        success_criteria={"completion": True, "based_on_pattern": pattern.name},
        required_tools=required_tools,
        required_inputs=pattern_data.get('required_inputs', {}),
        implementation_timeline=implementation_timeline,
        source="pattern",
        status="draft",
        meta_data={
            "source_pattern_id": str(pattern.id),
            "source_pattern_name": pattern.name,
            "pattern_confidence": pattern.confidence_score,
            "pattern_success_rate": pattern.success_rate,
        }
    )
    
    db.add(proposal)
    await db.commit()
    await db.refresh(proposal)
    
    return proposal
