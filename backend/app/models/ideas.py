"""
User Ideas and Strategic Context models.

Ideas flow through the system:
1. User shares idea in Brainstorm → captured to ideas queue (status=NEW)
2. Opportunity Scout reviews new ideas
3. Tool-related ideas → marked for Tool Scout (status=TOOL)
4. Opportunity ideas → distilled to StrategicContext (status=PROCESSED)
5. Strategic context used in future opportunity planning
"""

import uuid
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from enum import Enum
from typing import Optional

from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text, Index
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.orm import relationship

from app.core.database import Base


class IdeaStatus(str, Enum):
    """Status of an idea in the queue."""
    NEW = "new"  # Just added, not reviewed
    OPPORTUNITY = "opportunity"  # Reviewed, relevant to opportunities (pending processing)
    TOOL = "tool"  # Reviewed, needs Tool Scout agent
    PROCESSED = "processed"  # Distilled into strategic context
    ARCHIVED = "archived"  # Pruned or manually archived


class IdeaSource(str, Enum):
    """Where the idea came from."""
    BRAINSTORM = "brainstorm"  # From Brainstorm chat
    CONVERSATION = "conversation"  # From regular conversation
    MANUAL = "manual"  # Manually added via API


class StrategicContextCategory(str, Enum):
    """Categories for strategic context entries."""
    CAPABILITY = "capability"  # What user can do (skills, tools, resources)
    INTEREST = "interest"  # What user is interested in
    CONSTRAINT = "constraint"  # Limitations (time, budget, skills)
    GOAL = "goal"  # What user wants to achieve
    INSIGHT = "insight"  # General insights about opportunities
    PREFERENCE = "preference"  # How user likes to work


class UserIdea(Base):
    """
    An idea captured from user interactions.
    
    Ideas start as NEW and get reviewed by the Opportunity Scout.
    - Tool ideas → marked TOOL for Tool Scout
    - Opportunity ideas → processed and distilled → PROCESSED
    """
    __tablename__ = "user_ideas"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Content
    original_content = Column(Text, nullable=False)  # Exactly what user said
    reformatted_content = Column(Text, nullable=False)  # Basic cleanup by assistant
    distilled_content = Column(Text, nullable=True)  # Optimized form after processing
    
    # Classification
    status = Column(
        String(20),
        nullable=False,
        default=IdeaStatus.NEW.value,
        index=True
    )
    
    # Source tracking
    source = Column(String(20), nullable=False, default=IdeaSource.BRAINSTORM.value)
    source_conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    
    # Review tracking
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_agent = Column(String(50), nullable=True)  # e.g., "opportunity_scout"
    review_notes = Column(Text, nullable=True)  # Agent's reasoning
    
    # Processing tracking
    processed_at = Column(DateTime(timezone=True), nullable=True)
    strategic_context_id = Column(UUID(as_uuid=True), ForeignKey("strategic_context_entries.id", ondelete="SET NULL"), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    
    # Relationships
    user = relationship("User", back_populates="ideas")
    strategic_context_entry = relationship("StrategicContextEntry", back_populates="source_ideas", foreign_keys=[strategic_context_id])

    __table_args__ = (
        Index("ix_user_ideas_user_status", "user_id", "status"),
        Index("ix_user_ideas_created", "created_at"),
    )

    def __repr__(self):
        return f"<UserIdea {self.id} status={self.status}>"


class StrategicContextEntry(Base):
    """
    Optimized, distilled insights for strategy planning.
    
    This is the lightweight resource that Opportunity Scout uses
    for planning. It's kept small and relevant through:
    - Relevance scoring based on usage
    - Periodic pruning of stale entries
    - Merging similar entries
    """
    __tablename__ = "strategic_context_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Content
    content = Column(Text, nullable=False)  # Distilled, optimized insight
    category = Column(String(30), nullable=False, index=True)
    
    # Metadata for optimization
    keywords = Column(ARRAY(String), nullable=True)  # List of strings for similarity matching
    relevance_score = Column(Float, default=1.0)  # Decays over time, increases on use
    use_count = Column(Float, default=0)  # How many times used in planning
    
    # Tracking
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)  # When Scout confirmed still relevant
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    
    # Relationships
    user = relationship("User", back_populates="strategic_context")
    source_ideas = relationship("UserIdea", back_populates="strategic_context_entry", foreign_keys=[UserIdea.strategic_context_id])

    __table_args__ = (
        Index("ix_strategic_context_user_category", "user_id", "category"),
        Index("ix_strategic_context_relevance", "relevance_score"),
    )

    def __repr__(self):
        return f"<StrategicContextEntry {self.id} category={self.category}>"
