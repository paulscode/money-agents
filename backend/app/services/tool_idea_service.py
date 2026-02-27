"""
Tool Idea Entry Service.

Manages tool ideas that have been processed from the user ideas queue.
These inform the Tool Scout about what tools users are interested in.
"""

import logging
from datetime import datetime, timedelta, timezone
from app.core.datetime_utils import utc_now
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_scout import ToolIdeaEntry
from app.models.ideas import UserIdea, IdeaStatus

logger = logging.getLogger(__name__)

# Configuration
MAX_TOOL_IDEAS = 100  # Maximum tool ideas to keep per user
RELEVANCE_DECAY_RATE = 0.03  # 3% decay per week (slower than knowledge)
MIN_RELEVANCE_THRESHOLD = 0.1  # Below this gets pruned


class ToolIdeaService:
    """Service for managing tool idea entries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def process_idea_from_queue(
        self,
        idea: UserIdea,
        distilled_summary: str,
        use_case: Optional[str] = None,
        context: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        priority: Optional[str] = None,
    ) -> ToolIdeaEntry:
        """
        Process an idea from the queue and create a tool idea entry.
        Also marks the original idea as PROCESSED.
        """
        entry = ToolIdeaEntry(
            original_idea_id=idea.id,
            user_id=idea.user_id,
            summary=distilled_summary,
            use_case=use_case,
            context=context,
            keywords=keywords or [],
            priority=priority,
            relevance_score=1.0,
            is_addressed=False,
        )
        self.db.add(entry)
        
        # Mark original idea as processed
        idea.status = IdeaStatus.PROCESSED.value
        idea.distilled_content = distilled_summary
        idea.reviewed_at = utc_now()
        idea.reviewed_by_agent = "tool_scout"
        
        await self.db.commit()
        await self.db.refresh(entry)
        
        logger.info(f"Processed tool idea: {distilled_summary[:50]}")
        return entry

    async def add_entry(
        self,
        user_id: UUID,
        summary: str,
        use_case: Optional[str] = None,
        context: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        priority: Optional[str] = None,
    ) -> ToolIdeaEntry:
        """Add a tool idea entry directly (without queue)."""
        entry = ToolIdeaEntry(
            user_id=user_id,
            summary=summary,
            use_case=use_case,
            context=context,
            keywords=keywords or [],
            priority=priority,
            relevance_score=1.0,
            is_addressed=False,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def get_entry(self, entry_id: UUID) -> Optional[ToolIdeaEntry]:
        """Get a tool idea entry by ID."""
        result = await self.db.execute(
            select(ToolIdeaEntry).where(ToolIdeaEntry.id == entry_id)
        )
        return result.scalar_one_or_none()

    async def get_user_entries(
        self,
        user_id: UUID,
        include_addressed: bool = False,
        limit: int = 50,
    ) -> List[ToolIdeaEntry]:
        """Get tool idea entries for a user."""
        query = select(ToolIdeaEntry).where(ToolIdeaEntry.user_id == user_id)
        
        if not include_addressed:
            query = query.where(ToolIdeaEntry.is_addressed == False)
        
        query = query.order_by(desc(ToolIdeaEntry.relevance_score)).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_unaddressed_entries(self, limit: int = 100) -> List[ToolIdeaEntry]:
        """Get all unaddressed tool idea entries across users."""
        result = await self.db.execute(
            select(ToolIdeaEntry).where(
                ToolIdeaEntry.is_addressed == False
            ).order_by(desc(ToolIdeaEntry.relevance_score)).limit(limit)
        )
        return list(result.scalars().all())

    async def find_similar_entry(
        self,
        user_id: UUID,
        summary: str,
        keywords: List[str],
        threshold: float = 0.5,
    ) -> Optional[ToolIdeaEntry]:
        """Find a similar existing entry to avoid duplicates."""
        entries = await self.get_user_entries(user_id, include_addressed=True, limit=100)
        
        summary_lower = summary.lower()
        query_keywords = set(k.lower() for k in keywords)
        
        for entry in entries:
            # Check summary similarity (simple containment check)
            if summary_lower in entry.summary.lower() or entry.summary.lower() in summary_lower:
                return entry
            
            # Check keyword overlap
            entry_keywords = set(k.lower() for k in (entry.keywords or []))
            if entry_keywords and query_keywords:
                overlap = len(entry_keywords & query_keywords)
                union = len(entry_keywords | query_keywords)
                similarity = overlap / union if union > 0 else 0
                
                if similarity >= threshold:
                    return entry
        
        return None

    async def mark_as_addressed(
        self,
        entry_id: UUID,
        tool_id: UUID,
    ) -> Optional[ToolIdeaEntry]:
        """Mark a tool idea as addressed by a tool."""
        entry = await self.get_entry(entry_id)
        if not entry:
            return None
        
        entry.is_addressed = True
        entry.addressed_by_tool_id = tool_id
        entry.updated_at = utc_now()
        
        await self.db.commit()
        await self.db.refresh(entry)
        
        logger.info(f"Marked tool idea {entry_id} as addressed by tool {tool_id}")
        return entry

    async def boost_relevance(
        self,
        entry_id: UUID,
        boost: float = 0.1,
    ) -> Optional[ToolIdeaEntry]:
        """Boost relevance of an entry (e.g., when user mentions again)."""
        entry = await self.get_entry(entry_id)
        if not entry:
            return None
        
        entry.relevance_score = min(1.0, entry.relevance_score + boost)
        entry.updated_at = utc_now()
        
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def decay_relevance(self) -> int:
        """Apply relevance decay to entries based on age."""
        now = utc_now()
        one_week_ago = now - timedelta(days=7)
        
        result = await self.db.execute(
            select(ToolIdeaEntry).where(
                and_(
                    ToolIdeaEntry.is_addressed == False,
                    ToolIdeaEntry.updated_at < one_week_ago,
                )
            )
        )
        entries = list(result.scalars().all())
        
        decayed = 0
        for entry in entries:
            weeks_since_update = (now - entry.updated_at).days / 7
            decay = RELEVANCE_DECAY_RATE * weeks_since_update
            entry.relevance_score = max(0, entry.relevance_score - decay)
            decayed += 1
        
        if decayed > 0:
            await self.db.commit()
            logger.info(f"Decayed relevance for {decayed} tool ideas")
        
        return decayed

    async def prune_low_relevance(self) -> int:
        """Delete entries below relevance threshold."""
        result = await self.db.execute(
            select(ToolIdeaEntry).where(
                and_(
                    ToolIdeaEntry.relevance_score < MIN_RELEVANCE_THRESHOLD,
                    ToolIdeaEntry.is_addressed == False,
                )
            )
        )
        entries = list(result.scalars().all())
        
        for entry in entries:
            await self.db.delete(entry)
        
        if entries:
            await self.db.commit()
            logger.info(f"Pruned {len(entries)} low-relevance tool ideas")
        
        return len(entries)

    async def enforce_user_limit(self, user_id: UUID) -> int:
        """Enforce max entries per user by removing lowest relevance."""
        result = await self.db.execute(
            select(func.count()).select_from(ToolIdeaEntry).where(
                ToolIdeaEntry.user_id == user_id
            )
        )
        count = result.scalar()
        
        if count <= MAX_TOOL_IDEAS:
            return 0
        
        excess = count - MAX_TOOL_IDEAS
        result = await self.db.execute(
            select(ToolIdeaEntry).where(
                ToolIdeaEntry.user_id == user_id
            ).order_by(ToolIdeaEntry.relevance_score).limit(excess)
        )
        to_delete = list(result.scalars().all())
        
        for entry in to_delete:
            await self.db.delete(entry)
        
        if to_delete:
            await self.db.commit()
            logger.info(f"Removed {len(to_delete)} tool ideas for user {user_id} to enforce limit")
        
        return len(to_delete)

    async def run_maintenance(self) -> Dict[str, int]:
        """Run all maintenance tasks."""
        decayed = await self.decay_relevance()
        pruned = await self.prune_low_relevance()
        
        return {
            "decayed": decayed,
            "pruned": pruned,
        }

    async def format_for_prompt(self, user_id: Optional[UUID] = None, limit: int = 20) -> str:
        """Format tool ideas for inclusion in agent prompts."""
        if user_id:
            entries = await self.get_user_entries(user_id, limit=limit)
        else:
            entries = await self.get_unaddressed_entries(limit=limit)
        
        if not entries:
            return "No tool ideas in queue."
        
        lines = []
        for entry in entries:
            priority_str = f" [{entry.priority}]" if entry.priority else ""
            lines.append(f"- {entry.summary}{priority_str}")
            if entry.use_case:
                lines.append(f"  Use case: {entry.use_case}")
        
        return "\n".join(lines)

    async def get_counts(self, user_id: Optional[UUID] = None) -> Dict[str, int]:
        """Get counts of tool ideas."""
        query = select(func.count()).select_from(ToolIdeaEntry)
        
        if user_id:
            query = query.where(ToolIdeaEntry.user_id == user_id)
        
        # Total
        result = await self.db.execute(query)
        total = result.scalar()
        
        # Unaddressed
        unaddressed_query = query.where(ToolIdeaEntry.is_addressed == False)
        result = await self.db.execute(unaddressed_query)
        unaddressed = result.scalar()
        
        return {
            "total": total,
            "unaddressed": unaddressed,
            "addressed": total - unaddressed,
        }
