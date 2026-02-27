"""
Ideas service for managing the user ideas queue.

Handles the lifecycle of ideas from capture to processing.
"""

import logging
from datetime import datetime, timezone
from app.core.datetime_utils import utc_now
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    UserIdea,
    IdeaStatus,
    IdeaSource,
)

logger = logging.getLogger(__name__)


class IdeasService:
    """Service for managing user ideas."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def create_idea(
        self,
        user_id: UUID,
        original_content: str,
        reformatted_content: str,
        source: IdeaSource = IdeaSource.BRAINSTORM,
        source_conversation_id: Optional[UUID] = None,
    ) -> UserIdea:
        """
        Create a new idea in the queue.
        
        Args:
            user_id: The user who shared the idea
            original_content: Exactly what the user said
            reformatted_content: Basic cleanup by the assistant
            source: Where the idea came from
            source_conversation_id: Optional link to conversation
            
        Returns:
            The created UserIdea
        """
        idea = UserIdea(
            user_id=user_id,
            original_content=original_content,
            reformatted_content=reformatted_content,
            source=source.value,
            source_conversation_id=source_conversation_id,
            status=IdeaStatus.NEW.value,
        )
        
        self.db.add(idea)
        await self.db.commit()
        await self.db.refresh(idea)
        
        logger.info(f"Created new idea {idea.id} for user {user_id}")
        return idea
    
    async def get_idea(self, idea_id: UUID) -> Optional[UserIdea]:
        """Get an idea by ID."""
        result = await self.db.execute(
            select(UserIdea).where(UserIdea.id == idea_id)
        )
        return result.scalar_one_or_none()
    
    async def get_ideas_by_status(
        self,
        user_id: UUID,
        status: IdeaStatus,
        limit: int = 50,
    ) -> list[UserIdea]:
        """Get ideas for a user filtered by status."""
        result = await self.db.execute(
            select(UserIdea)
            .where(
                and_(
                    UserIdea.user_id == user_id,
                    UserIdea.status == status.value,
                )
            )
            .order_by(UserIdea.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def get_new_ideas(self, user_id: UUID, limit: int = 50) -> list[UserIdea]:
        """Get new (unreviewed) ideas for a user."""
        return await self.get_ideas_by_status(user_id, IdeaStatus.NEW, limit)
    
    async def get_all_new_ideas(self, limit: int = 100) -> list[UserIdea]:
        """Get all new ideas across all users (for agent processing)."""
        result = await self.db.execute(
            select(UserIdea)
            .where(UserIdea.status == IdeaStatus.NEW.value)
            .order_by(UserIdea.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def mark_for_tool_scout(
        self,
        idea_id: UUID,
        agent_name: str = "opportunity_scout",
        notes: Optional[str] = None,
    ) -> Optional[UserIdea]:
        """Mark an idea as needing Tool Scout review."""
        idea = await self.get_idea(idea_id)
        if not idea:
            return None
        
        idea.status = IdeaStatus.TOOL.value
        idea.reviewed_at = utc_now()
        idea.reviewed_by_agent = agent_name
        idea.review_notes = notes
        
        await self.db.commit()
        await self.db.refresh(idea)
        
        logger.info(f"Marked idea {idea_id} for Tool Scout")
        return idea
    
    async def mark_for_opportunity(
        self,
        idea_id: UUID,
        agent_name: str = "opportunity_scout",
        notes: Optional[str] = None,
    ) -> Optional[UserIdea]:
        """Mark an idea as opportunity-related (pending processing)."""
        idea = await self.get_idea(idea_id)
        if not idea:
            return None
        
        idea.status = IdeaStatus.OPPORTUNITY.value
        idea.reviewed_at = utc_now()
        idea.reviewed_by_agent = agent_name
        idea.review_notes = notes
        
        await self.db.commit()
        await self.db.refresh(idea)
        
        logger.info(f"Marked idea {idea_id} for opportunity processing")
        return idea
    
    async def mark_as_processed(
        self,
        idea_id: UUID,
        distilled_content: str,
        strategic_context_id: Optional[UUID] = None,
    ) -> Optional[UserIdea]:
        """Mark an idea as fully processed."""
        idea = await self.get_idea(idea_id)
        if not idea:
            return None
        
        idea.status = IdeaStatus.PROCESSED.value
        idea.distilled_content = distilled_content
        idea.strategic_context_id = strategic_context_id
        idea.processed_at = utc_now()
        
        await self.db.commit()
        await self.db.refresh(idea)
        
        logger.info(f"Processed idea {idea_id}")
        return idea
    
    async def archive_idea(self, idea_id: UUID) -> Optional[UserIdea]:
        """Archive an idea (manual cleanup)."""
        idea = await self.get_idea(idea_id)
        if not idea:
            return None
        
        idea.status = IdeaStatus.ARCHIVED.value
        
        await self.db.commit()
        await self.db.refresh(idea)
        
        logger.info(f"Archived idea {idea_id}")
        return idea
    
    async def get_idea_counts(self, user_id: UUID) -> dict[str, int]:
        """
        Get counts of ideas by status for a user.
        
        Returns:
            Dict with counts: {new, opportunity, tool, processed, total}
        """
        result = await self.db.execute(
            select(UserIdea.status, func.count(UserIdea.id))
            .where(UserIdea.user_id == user_id)
            .group_by(UserIdea.status)
        )
        
        counts = {
            "new": 0,
            "opportunity": 0,
            "tool": 0,
            "processed": 0,
            "archived": 0,
            "total": 0,
        }
        
        for status, count in result.all():
            counts[status] = count
            if status != "archived":
                counts["total"] += count
        
        return counts
    
    async def get_recent_ideas(
        self,
        user_id: UUID,
        limit: int = 10,
    ) -> list[UserIdea]:
        """Get recent ideas for a user (all statuses except archived)."""
        result = await self.db.execute(
            select(UserIdea)
            .where(
                and_(
                    UserIdea.user_id == user_id,
                    UserIdea.status != IdeaStatus.ARCHIVED.value,
                )
            )
            .order_by(UserIdea.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_ideas_for_tool_scout(self, limit: int = 50) -> list[UserIdea]:
        """Get ideas flagged for Tool Scout review (status=TOOL)."""
        result = await self.db.execute(
            select(UserIdea)
            .where(UserIdea.status == IdeaStatus.TOOL.value)
            .order_by(UserIdea.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())
