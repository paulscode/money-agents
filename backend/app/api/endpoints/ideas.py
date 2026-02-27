"""
Ideas API endpoints.

Provides endpoints for managing user ideas and viewing idea counts.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models import User, IdeaStatus, IdeaSource
from app.services.ideas_service import IdeasService
from app.services.strategic_context_service import StrategicContextService

router = APIRouter()


# Schemas
class IdeaCounts(BaseModel):
    """Counts of ideas by status."""
    new: int
    opportunity: int
    tool: int
    processed: int
    total: int


class IdeaCreate(BaseModel):
    """Create a new idea manually."""
    content: str
    source: IdeaSource = IdeaSource.MANUAL


class IdeaResponse(BaseModel):
    """Idea response."""
    id: UUID
    original_content: str
    reformatted_content: str
    distilled_content: Optional[str] = None
    status: str
    source: str
    created_at: str
    reviewed_at: Optional[str] = None
    reviewed_by_agent: Optional[str] = None
    review_notes: Optional[str] = None
    processed_at: Optional[str] = None

    class Config:
        from_attributes = True


class ContextSummary(BaseModel):
    """Strategic context summary."""
    total_entries: int
    by_category: dict
    max_entries: int


# Endpoints
@router.get("/counts", response_model=IdeaCounts)
async def get_idea_counts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IdeaCounts:
    """
    Get counts of ideas by status for the current user.
    
    Used by Brainstorm UI to show idea queue status.
    """
    service = IdeasService(db)
    counts = await service.get_idea_counts(current_user.id)
    return IdeaCounts(**counts)


@router.get("", response_model=list[IdeaResponse])
async def list_ideas(
    status: Optional[IdeaStatus] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[IdeaResponse]:
    """List ideas for the current user, optionally filtered by status."""
    service = IdeasService(db)
    
    if status:
        ideas = await service.get_ideas_by_status(current_user.id, status, limit)
    else:
        ideas = await service.get_recent_ideas(current_user.id, limit)
    
    return [
        IdeaResponse(
            id=idea.id,
            original_content=idea.original_content,
            reformatted_content=idea.reformatted_content,
            distilled_content=idea.distilled_content,
            status=idea.status,
            source=idea.source,
            created_at=idea.created_at.isoformat() if idea.created_at else "",
            reviewed_at=idea.reviewed_at.isoformat() if idea.reviewed_at else None,
            reviewed_by_agent=idea.reviewed_by_agent,
            review_notes=idea.review_notes,
            processed_at=idea.processed_at.isoformat() if idea.processed_at else None,
        )
        for idea in ideas
    ]


@router.post("", response_model=IdeaResponse, status_code=status.HTTP_201_CREATED)
async def create_idea(
    idea_data: IdeaCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IdeaResponse:
    """Create a new idea manually."""
    service = IdeasService(db)
    
    idea = await service.create_idea(
        user_id=current_user.id,
        original_content=idea_data.content,
        reformatted_content=idea_data.content,  # Manual ideas don't get reformatted
        source=idea_data.source,
    )
    
    return IdeaResponse(
        id=idea.id,
        original_content=idea.original_content,
        reformatted_content=idea.reformatted_content,
        distilled_content=idea.distilled_content,
        status=idea.status,
        source=idea.source,
        created_at=idea.created_at.isoformat() if idea.created_at else "",
        reviewed_at=idea.reviewed_at.isoformat() if idea.reviewed_at else None,
        reviewed_by_agent=idea.reviewed_by_agent,
        review_notes=idea.review_notes,
        processed_at=idea.processed_at.isoformat() if idea.processed_at else None,
    )


@router.post("/{idea_id}/archive", response_model=IdeaResponse)
async def archive_idea(
    idea_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> IdeaResponse:
    """Archive an idea."""
    service = IdeasService(db)
    idea = await service.get_idea(idea_id)
    
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    
    if idea.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    idea = await service.archive_idea(idea_id)
    
    return IdeaResponse(
        id=idea.id,
        original_content=idea.original_content,
        reformatted_content=idea.reformatted_content,
        distilled_content=idea.distilled_content,
        status=idea.status,
        source=idea.source,
        created_at=idea.created_at.isoformat() if idea.created_at else "",
        reviewed_at=idea.reviewed_at.isoformat() if idea.reviewed_at else None,
        reviewed_by_agent=idea.reviewed_by_agent,
        review_notes=idea.review_notes,
        processed_at=idea.processed_at.isoformat() if idea.processed_at else None,
    )


@router.get("/context/summary", response_model=ContextSummary)
async def get_context_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContextSummary:
    """Get a summary of the user's strategic context."""
    service = StrategicContextService(db)
    summary = await service.get_context_summary(current_user.id)
    return ContextSummary(**summary)
