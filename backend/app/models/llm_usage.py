"""LLM Usage model for tracking all LLM API calls across the application.

This provides comprehensive cost tracking for:
- Brainstorm chat (no conversation)
- Agent chats (with conversation)
- Celery agent tasks
- Remote campaign workers
- Any other LLM usage

This is the authoritative source for cost analysis and budgeting.
"""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Float, JSON, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class LLMUsageSource(str, enum.Enum):
    """Source of the LLM call for categorization."""
    BRAINSTORM = "brainstorm"  # Brainstorm chat endpoint
    AGENT_CHAT = "agent_chat"  # WebSocket agent conversations
    AGENT_TASK = "agent_task"  # Celery scheduled agent runs
    CAMPAIGN = "campaign"  # Remote campaign worker
    TOOL = "tool"  # Tool execution (e.g., LLM-based tools)
    OTHER = "other"  # Catch-all


class LLMUsage(Base):
    """Track individual LLM API calls for cost analysis.
    
    This is separate from Message to enable:
    - Tracking brainstorm (which doesn't use conversations)
    - Multiple LLM calls per message (e.g., search + follow-up)
    - Detailed cost aggregation by source/model/user
    """
    __tablename__ = "llm_usage"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Who/what made the call
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    source = Column(
        Enum(LLMUsageSource, name="llm_usage_source", values_callable=lambda x: [e.value for e in x]),
        default=LLMUsageSource.OTHER,
        nullable=False,
        index=True
    )
    
    # Optional context references
    conversation_id = Column(PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    message_id = Column(PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    agent_run_id = Column(PGUUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    campaign_id = Column(PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # LLM details
    provider = Column(String(50), nullable=False, index=True)  # glm, claude, openai, ollama
    model = Column(String(100), nullable=False, index=True)
    
    # Token counts
    prompt_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    total_tokens = Column(Integer, nullable=False, default=0)
    
    # Cost tracking
    cost_usd = Column(Float, nullable=True)  # Calculated cost in USD
    
    # Performance
    latency_ms = Column(Integer, nullable=True)  # Response time
    
    # Additional context
    meta_data = Column("metadata", JSON, nullable=True)  # e.g., search query, tier
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now, index=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id])
    conversation = relationship("Conversation", foreign_keys=[conversation_id])
    
    # Composite indexes for common queries
    __table_args__ = (
        Index('idx_llm_usage_user_created', 'user_id', 'created_at'),
        Index('idx_llm_usage_source_created', 'source', 'created_at'),
        Index('idx_llm_usage_model_created', 'model', 'created_at'),
    )
    
    def __repr__(self) -> str:
        return f"<LLMUsage {self.id} {self.model} {self.total_tokens}t ${self.cost_usd or 0:.6f}>"
