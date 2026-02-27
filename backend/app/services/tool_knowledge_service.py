"""
Tool Knowledge Service.

Manages the Tool Scout's knowledge base about the AI/tool landscape.
Handles CRUD operations, similarity detection, relevance decay, and pruning.
"""

import logging
from datetime import datetime, timedelta, timezone
from app.core.datetime_utils import utc_now
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, func, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tool_scout import (
    ToolKnowledge,
    ToolKnowledgeCategory,
    ToolKnowledgeStatus,
)

logger = logging.getLogger(__name__)

# Configuration
MAX_KNOWLEDGE_ENTRIES = 200  # Maximum entries to keep
RELEVANCE_DECAY_RATE = 0.05  # 5% decay per week
MIN_RELEVANCE_THRESHOLD = 0.15  # Below this gets archived
STALE_THRESHOLD_DAYS = 30  # Mark stale after this many days without validation


class ToolKnowledgeService:
    """Service for managing tool knowledge entries."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def add_entry(
        self,
        title: str,
        summary: str,
        category: ToolKnowledgeCategory,
        full_content: Optional[str] = None,
        source_url: Optional[str] = None,
        source_type: str = "web_search",
        keywords: Optional[List[str]] = None,
        related_tool_id: Optional[UUID] = None,
        agent_notes: Optional[str] = None,
        relevance_score: float = 1.0,
    ) -> ToolKnowledge:
        """Add a new knowledge entry."""
        entry = ToolKnowledge(
            title=title,
            summary=summary,
            category=category.value,
            full_content=full_content,
            source_url=source_url,
            source_type=source_type,
            keywords=keywords or [],
            related_tool_id=related_tool_id,
            agent_notes=agent_notes,
            relevance_score=relevance_score,
            status=ToolKnowledgeStatus.ACTIVE.value,
        )
        self.db.add(entry)
        await self.db.commit()
        await self.db.refresh(entry)
        
        logger.info(f"Added knowledge entry: {title}")
        return entry

    async def get_entry(self, entry_id: UUID) -> Optional[ToolKnowledge]:
        """Get a knowledge entry by ID."""
        result = await self.db.execute(
            select(ToolKnowledge).where(ToolKnowledge.id == entry_id)
        )
        return result.scalar_one_or_none()

    async def get_active_entries(
        self,
        category: Optional[ToolKnowledgeCategory] = None,
        limit: int = 100,
    ) -> List[ToolKnowledge]:
        """Get active knowledge entries, optionally filtered by category."""
        query = select(ToolKnowledge).where(
            ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value
        )
        
        if category:
            query = query.where(ToolKnowledge.category == category.value)
        
        query = query.order_by(desc(ToolKnowledge.relevance_score)).limit(limit)
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_recent_entries(
        self,
        limit: int = 20,
        days: int = 7,
    ) -> List[ToolKnowledge]:
        """Get recently discovered knowledge entries."""
        from datetime import timedelta
        cutoff = utc_now() - timedelta(days=days)
        
        query = select(ToolKnowledge).where(
            ToolKnowledge.discovered_at >= cutoff
        ).order_by(desc(ToolKnowledge.discovered_at)).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def search_entries(
        self,
        query: str,
        category: Optional[ToolKnowledgeCategory] = None,
        include_stale: bool = False,
        limit: int = 20,
    ) -> List[ToolKnowledge]:
        """Search knowledge entries by title, summary, or keywords."""
        status_filter = [ToolKnowledgeStatus.ACTIVE.value]
        if include_stale:
            status_filter.append(ToolKnowledgeStatus.STALE.value)
        
        # Simple text search - could be enhanced with full-text search
        search_pattern = f"%{query.lower()}%"
        
        stmt = select(ToolKnowledge).where(
            and_(
                ToolKnowledge.status.in_(status_filter),
                or_(
                    func.lower(ToolKnowledge.title).like(search_pattern),
                    func.lower(ToolKnowledge.summary).like(search_pattern),
                )
            )
        )
        
        if category:
            stmt = stmt.where(ToolKnowledge.category == category.value)
        
        stmt = stmt.order_by(desc(ToolKnowledge.relevance_score)).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def find_similar_entry(
        self,
        title: str,
        keywords: List[str],
        threshold: float = 0.5,
    ) -> Optional[ToolKnowledge]:
        """Find an existing entry that's similar to avoid duplicates."""
        # First try exact title match
        result = await self.db.execute(
            select(ToolKnowledge).where(
                and_(
                    func.lower(ToolKnowledge.title) == title.lower(),
                    ToolKnowledge.status != ToolKnowledgeStatus.ARCHIVED.value,
                )
            )
        )
        exact_match = result.scalar_one_or_none()
        if exact_match:
            return exact_match
        
        # Try keyword overlap
        if keywords:
            active_entries = await self.get_active_entries(limit=200)
            for entry in active_entries:
                entry_keywords = set(k.lower() for k in (entry.keywords or []))
                query_keywords = set(k.lower() for k in keywords)
                
                if entry_keywords and query_keywords:
                    overlap = len(entry_keywords & query_keywords)
                    union = len(entry_keywords | query_keywords)
                    similarity = overlap / union if union > 0 else 0
                    
                    if similarity >= threshold:
                        return entry
        
        return None

    async def update_entry(
        self,
        entry_id: UUID,
        **updates,
    ) -> Optional[ToolKnowledge]:
        """Update a knowledge entry."""
        entry = await self.get_entry(entry_id)
        if not entry:
            return None
        
        for key, value in updates.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        
        entry.updated_at = utc_now()
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def validate_entry(
        self,
        entry_id: UUID,
        boost_relevance: float = 0.1,
        agent_name: str = "tool_scout",
    ) -> Optional[ToolKnowledge]:
        """Mark an entry as validated and optionally boost relevance."""
        entry = await self.get_entry(entry_id)
        if not entry:
            return None
        
        entry.last_validated_at = utc_now()
        entry.validation_count = (entry.validation_count or 0) + 1
        entry.relevance_score = min(1.0, entry.relevance_score + boost_relevance)
        entry.last_updated_by = agent_name
        
        # Re-activate if stale
        if entry.status == ToolKnowledgeStatus.STALE.value:
            entry.status = ToolKnowledgeStatus.ACTIVE.value
        
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def archive_entry(self, entry_id: UUID) -> bool:
        """Archive a knowledge entry."""
        entry = await self.get_entry(entry_id)
        if not entry:
            return False
        
        entry.status = ToolKnowledgeStatus.ARCHIVED.value
        await self.db.commit()
        return True

    async def decay_relevance(self) -> int:
        """Apply relevance decay to all active entries based on age."""
        now = utc_now()
        one_week_ago = now - timedelta(days=7)
        
        # Get entries that haven't been validated in the last week
        result = await self.db.execute(
            select(ToolKnowledge).where(
                and_(
                    ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value,
                    ToolKnowledge.last_validated_at < one_week_ago,
                )
            )
        )
        entries = list(result.scalars().all())
        
        decayed = 0
        for entry in entries:
            weeks_since_validation = (now - entry.last_validated_at).days / 7
            decay = RELEVANCE_DECAY_RATE * weeks_since_validation
            entry.relevance_score = max(0, entry.relevance_score - decay)
            decayed += 1
        
        if decayed > 0:
            await self.db.commit()
            logger.info(f"Decayed relevance for {decayed} entries")
        
        return decayed

    async def mark_stale_entries(self) -> int:
        """Mark entries as stale if not validated recently."""
        threshold = utc_now() - timedelta(days=STALE_THRESHOLD_DAYS)
        
        result = await self.db.execute(
            select(ToolKnowledge).where(
                and_(
                    ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value,
                    ToolKnowledge.last_validated_at < threshold,
                )
            )
        )
        entries = list(result.scalars().all())
        
        for entry in entries:
            entry.status = ToolKnowledgeStatus.STALE.value
        
        if entries:
            await self.db.commit()
            logger.info(f"Marked {len(entries)} entries as stale")
        
        return len(entries)

    async def prune_low_relevance(self) -> int:
        """Archive entries below relevance threshold."""
        result = await self.db.execute(
            select(ToolKnowledge).where(
                and_(
                    ToolKnowledge.status.in_([
                        ToolKnowledgeStatus.ACTIVE.value,
                        ToolKnowledgeStatus.STALE.value,
                    ]),
                    ToolKnowledge.relevance_score < MIN_RELEVANCE_THRESHOLD,
                )
            )
        )
        entries = list(result.scalars().all())
        
        for entry in entries:
            entry.status = ToolKnowledgeStatus.ARCHIVED.value
        
        if entries:
            await self.db.commit()
            logger.info(f"Archived {len(entries)} low-relevance entries")
        
        return len(entries)

    async def enforce_max_entries(self) -> int:
        """Remove oldest/lowest relevance entries if over limit."""
        # Count active entries
        result = await self.db.execute(
            select(func.count()).select_from(ToolKnowledge).where(
                ToolKnowledge.status.in_([
                    ToolKnowledgeStatus.ACTIVE.value,
                    ToolKnowledgeStatus.STALE.value,
                ])
            )
        )
        count = result.scalar()
        
        if count <= MAX_KNOWLEDGE_ENTRIES:
            return 0
        
        # Get excess entries (lowest relevance)
        excess = count - MAX_KNOWLEDGE_ENTRIES
        result = await self.db.execute(
            select(ToolKnowledge).where(
                ToolKnowledge.status.in_([
                    ToolKnowledgeStatus.ACTIVE.value,
                    ToolKnowledgeStatus.STALE.value,
                ])
            ).order_by(ToolKnowledge.relevance_score).limit(excess)
        )
        to_archive = list(result.scalars().all())
        
        for entry in to_archive:
            entry.status = ToolKnowledgeStatus.ARCHIVED.value
        
        if to_archive:
            await self.db.commit()
            logger.info(f"Archived {len(to_archive)} entries to enforce max limit")
        
        return len(to_archive)

    async def run_maintenance(self) -> Dict[str, int]:
        """Run all maintenance tasks: decay, stale marking, pruning."""
        decayed = await self.decay_relevance()
        stale = await self.mark_stale_entries()
        pruned = await self.prune_low_relevance()
        enforced = await self.enforce_max_entries()
        
        return {
            "decayed": decayed,
            "marked_stale": stale,
            "pruned": pruned,
            "enforced_limit": enforced,
        }

    async def format_knowledge_for_prompt(
        self,
        categories: Optional[List[ToolKnowledgeCategory]] = None,
        limit: int = 30,
    ) -> str:
        """Format knowledge entries for inclusion in agent prompts."""
        query = select(ToolKnowledge).where(
            ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value
        )
        
        if categories:
            query = query.where(
                ToolKnowledge.category.in_([c.value for c in categories])
            )
        
        query = query.order_by(desc(ToolKnowledge.relevance_score)).limit(limit)
        result = await self.db.execute(query)
        entries = list(result.scalars().all())
        
        if not entries:
            return "No knowledge base entries yet."
        
        lines = []
        for entry in entries:
            keywords_str = ", ".join(entry.keywords[:5]) if entry.keywords else ""
            lines.append(f"- **{entry.title}** ({entry.category}): {entry.summary}")
            if keywords_str:
                lines.append(f"  Keywords: {keywords_str}")
        
        return "\n".join(lines)

    async def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the knowledge base."""
        # Count by status
        result = await self.db.execute(
            select(
                ToolKnowledge.status,
                func.count(ToolKnowledge.id).label("count"),
            ).group_by(ToolKnowledge.status)
        )
        status_counts = {row.status: row.count for row in result.all()}
        
        # Count by category
        result = await self.db.execute(
            select(
                ToolKnowledge.category,
                func.count(ToolKnowledge.id).label("count"),
            ).where(
                ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value
            ).group_by(ToolKnowledge.category)
        )
        category_counts = {row.category: row.count for row in result.all()}
        
        # Average relevance
        result = await self.db.execute(
            select(func.avg(ToolKnowledge.relevance_score)).where(
                ToolKnowledge.status == ToolKnowledgeStatus.ACTIVE.value
            )
        )
        avg_relevance = result.scalar() or 0
        
        return {
            "by_status": status_counts,
            "by_category": category_counts,
            "average_relevance": round(avg_relevance, 3),
            "total_active": status_counts.get(ToolKnowledgeStatus.ACTIVE.value, 0),
        }
