"""
Strategic Context service for managing distilled insights.

The strategic context is a lightweight, optimized collection of insights
that the Opportunity Scout uses for strategy planning. It needs to be
kept manageable through relevance scoring, pruning, and merging.
"""

import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    StrategicContextEntry,
    StrategicContextCategory,
    UserIdea,
)

logger = logging.getLogger(__name__)

# Configuration for context management
MAX_CONTEXT_ENTRIES_PER_USER = 50  # Hard limit to keep context lightweight
RELEVANCE_DECAY_RATE = 0.1  # Decay per week of non-use
MIN_RELEVANCE_THRESHOLD = 0.2  # Below this, entries are candidates for pruning
STALE_DAYS_THRESHOLD = 90  # Days without use before considered stale


class StrategicContextService:
    """Service for managing strategic context entries."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def add_entry(
        self,
        user_id: UUID,
        content: str,
        category: StrategicContextCategory,
        keywords: Optional[list[str]] = None,
        source_idea_id: Optional[UUID] = None,
    ) -> StrategicContextEntry:
        """
        Add a new strategic context entry.
        
        Args:
            user_id: The user this context belongs to
            content: The distilled insight
            category: Category classification
            keywords: Optional keywords for similarity matching
            source_idea_id: Optional source idea that was distilled
            
        Returns:
            The created entry
        """
        entry = StrategicContextEntry(
            user_id=user_id,
            content=content,
            category=category.value,
            keywords=keywords or [],
            relevance_score=1.0,  # New entries start fully relevant
            use_count=0,
        )
        
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        
        # Link source idea if provided
        if source_idea_id:
            idea = await self.db.execute(
                select(UserIdea).where(UserIdea.id == source_idea_id)
            )
            idea_obj = idea.scalar_one_or_none()
            if idea_obj:
                idea_obj.strategic_context_id = entry.id
                await self.db.commit()
        
        logger.info(f"Added strategic context entry {entry.id} for user {user_id}")
        
        # Check if we need to prune
        await self._check_and_prune(user_id)
        
        return entry
    
    async def get_entry(self, entry_id: UUID) -> Optional[StrategicContextEntry]:
        """Get an entry by ID."""
        result = await self.db.execute(
            select(StrategicContextEntry).where(StrategicContextEntry.id == entry_id)
        )
        return result.scalar_one_or_none()
    
    async def get_context_for_planning(
        self,
        user_id: UUID,
        categories: Optional[list[StrategicContextCategory]] = None,
        limit: int = 30,
    ) -> list[StrategicContextEntry]:
        """
        Get strategic context entries for planning.
        
        Returns entries sorted by relevance, optionally filtered by category.
        Updates last_used_at and use_count for retrieved entries.
        
        Args:
            user_id: The user to get context for
            categories: Optional filter by categories
            limit: Maximum entries to return
            
        Returns:
            List of relevant context entries
        """
        query = (
            select(StrategicContextEntry)
            .where(StrategicContextEntry.user_id == user_id)
            .order_by(StrategicContextEntry.relevance_score.desc())
            .limit(limit)
        )
        
        if categories:
            category_values = [c.value for c in categories]
            query = query.where(StrategicContextEntry.category.in_(category_values))
        
        result = await self.db.execute(query)
        entries = list(result.scalars().all())
        
        # Mark entries as used
        now = utc_now()
        for entry in entries:
            entry.last_used_at = now
            entry.use_count += 1
            # Boost relevance slightly when used
            entry.relevance_score = min(1.0, entry.relevance_score + 0.05)
        
        await self.db.commit()
        
        return entries
    
    async def get_all_context(
        self,
        user_id: UUID,
        include_low_relevance: bool = False,
    ) -> list[StrategicContextEntry]:
        """Get all context entries for a user."""
        query = (
            select(StrategicContextEntry)
            .where(StrategicContextEntry.user_id == user_id)
            .order_by(StrategicContextEntry.relevance_score.desc())
        )
        
        if not include_low_relevance:
            query = query.where(
                StrategicContextEntry.relevance_score >= MIN_RELEVANCE_THRESHOLD
            )
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def find_similar_entry(
        self,
        user_id: UUID,
        content: str,
        category: StrategicContextCategory,
    ) -> Optional[StrategicContextEntry]:
        """
        Find a similar existing entry (to avoid duplicates).
        
        Uses simple keyword matching for now. Could be enhanced with
        embeddings/vector search later.
        """
        # Extract simple keywords from content
        words = set(content.lower().split())
        stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                      'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
                      'would', 'could', 'should', 'may', 'might', 'must', 'shall',
                      'can', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
                      'from', 'as', 'into', 'through', 'during', 'before', 'after',
                      'above', 'below', 'between', 'under', 'again', 'further',
                      'then', 'once', 'here', 'there', 'when', 'where', 'why',
                      'how', 'all', 'each', 'few', 'more', 'most', 'other', 'some',
                      'such', 'no', 'nor', 'not', 'only', 'own', 'same', 'so',
                      'than', 'too', 'very', 'just', 'and', 'but', 'if', 'or',
                      'because', 'until', 'while', 'although', 'though', 'after',
                      'this', 'that', 'these', 'those', 'i', 'we', 'you', 'he',
                      'she', 'it', 'they', 'what', 'which', 'who', 'whom'}
        keywords = [w for w in words if len(w) > 3 and w not in stop_words][:10]
        
        if not keywords:
            return None
        
        # Search for entries with overlapping keywords in same category
        entries = await self.db.execute(
            select(StrategicContextEntry)
            .where(
                and_(
                    StrategicContextEntry.user_id == user_id,
                    StrategicContextEntry.category == category.value,
                )
            )
        )
        
        best_match = None
        best_overlap = 0
        
        for entry in entries.scalars().all():
            if entry.keywords:
                overlap = len(set(keywords) & set(entry.keywords))
                if overlap > best_overlap and overlap >= 2:  # Require at least 2 keyword overlap
                    best_match = entry
                    best_overlap = overlap
        
        return best_match
    
    async def merge_with_existing(
        self,
        existing_entry_id: UUID,
        new_content: str,
        source_idea_id: Optional[UUID] = None,
    ) -> Optional[StrategicContextEntry]:
        """
        Merge new insight with existing entry (avoiding duplicates).
        
        The merge keeps the existing entry but may update content if
        the new insight adds value.
        """
        entry = await self.get_entry(existing_entry_id)
        if not entry:
            return None
        
        # For now, just boost relevance - a more sophisticated approach
        # would use LLM to synthesize the insights
        entry.relevance_score = min(1.0, entry.relevance_score + 0.1)
        entry.updated_at = utc_now()
        
        await self.db.commit()
        await self.db.refresh(entry)
        
        # Link the source idea to the existing entry
        if source_idea_id:
            idea = await self.db.execute(
                select(UserIdea).where(UserIdea.id == source_idea_id)
            )
            idea_obj = idea.scalar_one_or_none()
            if idea_obj:
                idea_obj.strategic_context_id = entry.id
                await self.db.commit()
        
        logger.info(f"Merged insight into existing entry {entry.id}")
        return entry
    
    async def decay_relevance(self, user_id: UUID) -> int:
        """
        Apply relevance decay to all entries for a user.
        
        Should be run periodically (e.g., weekly) to keep context fresh.
        
        Returns:
            Number of entries updated
        """
        entries = await self.get_all_context(user_id, include_low_relevance=True)
        now = utc_now()
        updated = 0
        
        for entry in entries:
            # Calculate weeks since last use
            if entry.last_used_at:
                weeks_unused = (now - entry.last_used_at).days / 7
            else:
                weeks_unused = (now - entry.created_at).days / 7
            
            # Apply decay
            decay = RELEVANCE_DECAY_RATE * weeks_unused
            new_score = max(0.0, entry.relevance_score - decay)
            
            if new_score != entry.relevance_score:
                entry.relevance_score = new_score
                updated += 1
        
        await self.db.commit()
        logger.info(f"Applied relevance decay to {updated} entries for user {user_id}")
        return updated
    
    async def prune_stale_entries(self, user_id: UUID) -> int:
        """
        Remove entries that are too old and low-relevance.
        
        Returns:
            Number of entries pruned
        """
        threshold_date = utc_now() - timedelta(days=STALE_DAYS_THRESHOLD)
        
        result = await self.db.execute(
            select(StrategicContextEntry)
            .where(
                and_(
                    StrategicContextEntry.user_id == user_id,
                    StrategicContextEntry.relevance_score < MIN_RELEVANCE_THRESHOLD,
                    or_(
                        StrategicContextEntry.last_used_at < threshold_date,
                        and_(
                            StrategicContextEntry.last_used_at.is_(None),
                            StrategicContextEntry.created_at < threshold_date,
                        )
                    )
                )
            )
        )
        
        entries = list(result.scalars().all())
        for entry in entries:
            await self.db.delete(entry)
        
        await self.db.commit()
        logger.info(f"Pruned {len(entries)} stale entries for user {user_id}")
        return len(entries)
    
    async def _check_and_prune(self, user_id: UUID) -> None:
        """Check if user has too many entries and prune if needed."""
        result = await self.db.execute(
            select(func.count(StrategicContextEntry.id))
            .where(StrategicContextEntry.user_id == user_id)
        )
        count = result.scalar() or 0
        
        if count > MAX_CONTEXT_ENTRIES_PER_USER:
            # Remove lowest relevance entries to get back under limit
            entries_to_remove = count - MAX_CONTEXT_ENTRIES_PER_USER + 5  # Buffer
            
            result = await self.db.execute(
                select(StrategicContextEntry)
                .where(StrategicContextEntry.user_id == user_id)
                .order_by(StrategicContextEntry.relevance_score.asc())
                .limit(entries_to_remove)
            )
            
            for entry in result.scalars().all():
                await self.db.delete(entry)
            
            await self.db.commit()
            logger.info(f"Auto-pruned {entries_to_remove} low-relevance entries for user {user_id}")
    
    async def get_context_summary(self, user_id: UUID) -> dict:
        """Get a summary of context for a user."""
        result = await self.db.execute(
            select(
                StrategicContextEntry.category,
                func.count(StrategicContextEntry.id),
                func.avg(StrategicContextEntry.relevance_score),
            )
            .where(StrategicContextEntry.user_id == user_id)
            .group_by(StrategicContextEntry.category)
        )
        
        by_category = {}
        total = 0
        for category, count, avg_relevance in result.all():
            by_category[category] = {
                "count": count,
                "avg_relevance": float(avg_relevance) if avg_relevance else 0.0,
            }
            total += count
        
        return {
            "total_entries": total,
            "by_category": by_category,
            "max_entries": MAX_CONTEXT_ENTRIES_PER_USER,
        }
    
    async def format_context_for_prompt(
        self,
        user_id: UUID,
        max_chars: int = 3000,
    ) -> str:
        """
        Format context entries into a string for LLM prompts.
        
        Keeps it lightweight by limiting total characters.
        """
        entries = await self.get_context_for_planning(user_id, limit=20)
        
        if not entries:
            return "No strategic context available yet."
        
        lines = ["## User Strategic Context"]
        char_count = len(lines[0])
        
        # Group by category
        by_category: dict[str, list[str]] = {}
        for entry in entries:
            cat = entry.category
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(entry.content)
        
        for category, contents in by_category.items():
            header = f"\n### {category.replace('_', ' ').title()}"
            if char_count + len(header) > max_chars:
                break
            lines.append(header)
            char_count += len(header)
            
            for content in contents:
                item = f"- {content}"
                if char_count + len(item) > max_chars:
                    break
                lines.append(item)
                char_count += len(item)
        
        return "\n".join(lines)
