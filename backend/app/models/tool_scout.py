"""
Tool Scout Knowledge Base models.

The Tool Scout maintains two key resources:

1. ToolKnowledge - Living knowledge about the AI/tool landscape
   - Discoveries from internet searches
   - Insights about trends, capabilities, limitations
   - Auto-pruned to stay relevant and manageable

2. ToolIdeaEntry - Processed ideas from the user ideas queue
   - Ideas flagged as "tool" by Opportunity Scout
   - Distilled and optimized for long-term use
   - Informs tool scouting priorities

Both resources use relevance scoring with decay and automatic pruning
to prevent unbounded growth while maintaining usefulness.
"""

import uuid
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from enum import Enum
from typing import Optional

from sqlalchemy import Column, DateTime, Float, ForeignKey, String, Text, Index, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class ToolKnowledgeCategory(str, Enum):
    """Categories for tool knowledge entries."""
    TOOL = "tool"  # Specific tool or service
    PLATFORM = "platform"  # Platform/ecosystem (OpenAI, Hugging Face, etc.)
    TECHNIQUE = "technique"  # AI technique or methodology
    TREND = "trend"  # Market or technology trend
    LIMITATION = "limitation"  # Known limitations or constraints
    INTEGRATION = "integration"  # How things work together
    COST = "cost"  # Pricing and cost information
    CAPABILITY = "capability"  # What's possible with current tech


class ToolKnowledgeStatus(str, Enum):
    """Status of knowledge entries."""
    ACTIVE = "active"  # Current and relevant
    STALE = "stale"  # Needs verification/update
    ARCHIVED = "archived"  # No longer relevant


class ToolKnowledge(Base):
    """
    A piece of knowledge about the AI/tool landscape.
    
    The Tool Scout builds and maintains this knowledge base through:
    - Periodic internet searches for new tools and developments
    - Processing and integrating search results
    - Regular pruning of stale or low-relevance entries
    """
    __tablename__ = "tool_knowledge"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Content
    title = Column(String(255), nullable=False)  # Brief title/name
    summary = Column(Text, nullable=False)  # Distilled summary
    full_content = Column(Text, nullable=True)  # Full details if available
    
    # Classification
    category = Column(
        String(30),
        nullable=False,
        default=ToolKnowledgeCategory.TOOL.value,
        index=True
    )
    
    # Related tool if this is about a specific tool
    related_tool_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("tools.id", ondelete="SET NULL"), 
        nullable=True,
        index=True
    )
    
    # Source tracking
    source_url = Column(String(500), nullable=True)  # Where we found this
    source_type = Column(String(50), nullable=True)  # web_search, api, manual
    discovered_at = Column(DateTime(timezone=True), default=utc_now)
    
    # Relevance management
    relevance_score = Column(Float, nullable=False, default=1.0)  # 0.0 to 1.0
    last_validated_at = Column(DateTime(timezone=True), default=utc_now)
    validation_count = Column(Float, default=0)  # How often re-validated
    
    # Status
    status = Column(
        String(20),
        nullable=False,
        default=ToolKnowledgeStatus.ACTIVE.value,
        index=True
    )
    
    # Keywords for similarity matching and search
    keywords = Column(JSONB, default=list)  # List of relevant keywords
    
    # Agent tracking
    created_by_agent = Column(String(50), nullable=False, default="tool_scout")
    last_updated_by = Column(String(50), nullable=True)
    agent_notes = Column(Text, nullable=True)  # Agent's reasoning/notes
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    
    # Relationships
    related_tool = relationship("Tool", foreign_keys=[related_tool_id])
    
    __table_args__ = (
        Index('idx_tool_knowledge_status_category', 'status', 'category'),
        Index('idx_tool_knowledge_relevance', 'relevance_score'),
        Index('idx_tool_knowledge_discovered', 'discovered_at'),
    )

    def __repr__(self) -> str:
        return f"<ToolKnowledge {self.title[:50]} ({self.category})>"


class ToolIdeaEntry(Base):
    """
    A tool idea from the user ideas queue, processed and stored for reference.
    
    When Opportunity Scout marks an idea as "tool", Tool Scout:
    1. Picks it up from the ideas queue
    2. Distills it into an optimized form
    3. Stores it here for future reference
    4. Marks the original idea as PROCESSED
    
    These entries inform what kinds of tools the user is interested in,
    helping prioritize scouting efforts.
    """
    __tablename__ = "tool_idea_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Link to original idea (if still exists)
    original_idea_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("user_ideas.id", ondelete="SET NULL"), 
        nullable=True
    )
    user_id = Column(
        UUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False
    )
    
    # Content (distilled from original)
    summary = Column(Text, nullable=False)  # What tool/capability is wanted
    use_case = Column(Text, nullable=True)  # What problem it would solve
    context = Column(Text, nullable=True)  # Additional context
    
    # Priority/relevance
    relevance_score = Column(Float, nullable=False, default=1.0)
    priority = Column(String(20), nullable=True)  # low, medium, high
    
    # Status
    is_addressed = Column(Boolean, default=False)  # Has a tool been found/created?
    addressed_by_tool_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Keywords for matching with discoveries
    keywords = Column(JSONB, default=list)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    
    # Relationships
    original_idea = relationship("UserIdea", foreign_keys=[original_idea_id])
    user = relationship("User", foreign_keys=[user_id])
    addressed_by_tool = relationship("Tool", foreign_keys=[addressed_by_tool_id])
    
    __table_args__ = (
        Index('idx_tool_idea_user', 'user_id'),
        Index('idx_tool_idea_relevance', 'relevance_score'),
        Index('idx_tool_idea_addressed', 'is_addressed'),
    )

    def __repr__(self) -> str:
        return f"<ToolIdeaEntry {self.summary[:50]}>"


class ToolStrategyStatus(str, Enum):
    """Status of tool discovery strategies."""
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"


class ToolDiscoveryStrategy(Base):
    """
    A discovery strategy for Tool Scout - defines search focus areas.
    
    Similar to Opportunity Scout's DiscoveryStrategy, this allows Tool Scout
    to have evolving, data-driven search strategies that improve over time.
    
    Strategies are evaluated based on:
    - How many useful knowledge entries they produce
    - Whether discovered tools are actually used
    - User feedback on tool relevance
    """
    __tablename__ = "tool_discovery_strategies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # Identity
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    
    # Focus area
    focus_area = Column(String(50), nullable=False)  # content_generation, automation, local_ai, etc.
    
    # Search configuration - the actual queries to run
    search_queries = Column(JSONB, nullable=False, default=list)  # ["query 1", "query 2", ...]
    
    # Targeting criteria
    target_categories = Column(JSONB, default=list)  # ["tool", "api", "platform"]
    priority_keywords = Column(JSONB, default=list)  # Keywords to prioritize in results
    
    # Performance tracking
    times_executed = Column(Float, default=0)
    knowledge_entries_found = Column(Float, default=0)  # Total entries added
    tools_proposed = Column(Float, default=0)  # Tools that were proposed
    tools_approved = Column(Float, default=0)  # Tools that user approved
    effectiveness_score = Column(Float, nullable=True)  # Calculated effectiveness
    
    # Status
    status = Column(
        String(20),
        nullable=False,
        default=ToolStrategyStatus.ACTIVE.value,
        index=True
    )
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    last_executed_at = Column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        Index('idx_tool_strategy_status', 'status'),
        Index('idx_tool_strategy_effectiveness', 'effectiveness_score'),
    )

    def __repr__(self) -> str:
        return f"<ToolDiscoveryStrategy {self.name} ({self.focus_area})>"
