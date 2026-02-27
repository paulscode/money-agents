"""Campaign Learning models for intelligent agent behavior.

This module implements Phase 5: Agent Intelligence features:
- CampaignPattern: Successful execution patterns that can be reused
- CampaignLesson: Lessons learned from failures/issues
- PlanRevision: History of plan revisions and their outcomes
- ProactiveSuggestion: Agent-generated optimization suggestions

The goal is to enable:
1. Self-planning agents that can revise execution plans
2. Proactive communication with optimization suggestions
3. Inter-campaign learning to improve over time
"""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List
from uuid import UUID, uuid4
import enum

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, Index, Float
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


# =============================================================================
# Enums
# =============================================================================

class PatternType(str, enum.Enum):
    """Type of learned pattern."""
    EXECUTION_SEQUENCE = "execution_sequence"  # Successful task sequences
    INPUT_COLLECTION = "input_collection"      # Effective input gathering
    TOOL_COMBINATION = "tool_combination"      # Effective tool combinations
    ERROR_RECOVERY = "error_recovery"          # Successful error handling
    OPTIMIZATION = "optimization"              # Performance improvements
    TIMING = "timing"                          # Best times to execute


class PatternStatus(str, enum.Enum):
    """Status of a pattern."""
    ACTIVE = "active"        # Currently being used
    DEPRECATED = "deprecated"  # No longer recommended
    EXPERIMENTAL = "experimental"  # Being tested


class LessonCategory(str, enum.Enum):
    """Category of lesson learned."""
    FAILURE = "failure"          # Something that failed
    INEFFICIENCY = "inefficiency"  # Something that worked but was slow
    USER_FRICTION = "user_friction"  # Caused unnecessary user interaction
    BUDGET_ISSUE = "budget_issue"   # Budget-related problems
    TIMING = "timing"            # Timing-related issues
    TOOL_ISSUE = "tool_issue"    # Tool-related problems


class RevisionTrigger(str, enum.Enum):
    """What triggered a plan revision."""
    TASK_FAILURE = "task_failure"        # A task failed
    STREAM_BLOCKED = "stream_blocked"    # Stream became blocked
    BUDGET_CONCERN = "budget_concern"    # Budget running low/over
    USER_FEEDBACK = "user_feedback"      # User requested change
    NEW_INFORMATION = "new_information"  # Discovered new info during execution
    OPTIMIZATION = "optimization"        # Found better approach
    EXTERNAL_CHANGE = "external_change"  # External factor changed


class SuggestionType(str, enum.Enum):
    """Type of proactive suggestion."""
    OPTIMIZATION = "optimization"        # Improve current plan
    WARNING = "warning"                  # Potential issue ahead
    OPPORTUNITY = "opportunity"          # New opportunity discovered
    COST_SAVING = "cost_saving"          # Save money
    TIME_SAVING = "time_saving"          # Save time
    RISK_MITIGATION = "risk_mitigation"  # Reduce risk


class SuggestionStatus(str, enum.Enum):
    """Status of a suggestion."""
    PENDING = "pending"        # Awaiting user review
    ACCEPTED = "accepted"      # User accepted
    REJECTED = "rejected"      # User rejected
    AUTO_APPLIED = "auto_applied"  # Applied automatically (within agent authority)
    EXPIRED = "expired"        # No longer relevant


# =============================================================================
# Models
# =============================================================================

class CampaignPattern(Base):
    """
    A successful execution pattern that can be reused.
    
    Patterns are learned from successful campaign executions
    and can be applied to similar future campaigns.
    """
    __tablename__ = "campaign_patterns"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Pattern identification
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    pattern_type: Mapped[PatternType] = mapped_column(
        Enum(PatternType, name="pattern_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    
    # Status and confidence
    status: Mapped[PatternStatus] = mapped_column(
        Enum(PatternStatus, name="pattern_status", values_callable=lambda x: [e.value for e in x]),
        default=PatternStatus.EXPERIMENTAL,
        index=True
    )
    confidence_score: Mapped[float] = mapped_column(Float, default=0.5)  # 0-1 confidence
    
    # Pattern content
    # For execution_sequence: {"tasks": [...], "dependencies": {...}}
    # For tool_combination: {"tools": [...], "synergy": "description"}
    # For error_recovery: {"error_type": "...", "recovery_steps": [...]}
    pattern_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    
    # Applicability conditions
    # When should this pattern be considered?
    # E.g., {"proposal_type": "social_media", "budget_range": [100, 1000]}
    applicability_conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # Usage statistics
    times_applied: Mapped[int] = mapped_column(Integer, default=0)
    times_successful: Mapped[int] = mapped_column(Integer, default=0)
    last_applied_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Source tracking
    source_campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="SET NULL"), 
        nullable=True,
        index=True
    )
    discovered_by_agent: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # User ownership (patterns can be user-specific or global)
    user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=True,  # null = global pattern
        index=True
    )
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Tags for searching
    tags: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    __table_args__ = (
        Index('idx_pattern_type_status', 'pattern_type', 'status'),
        Index('idx_pattern_confidence', 'confidence_score'),
    )
    
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.times_applied == 0:
            return 0.0
        return self.times_successful / self.times_applied
    
    def __repr__(self) -> str:
        return f"<CampaignPattern {self.name} ({self.pattern_type.value})>"


class CampaignLesson(Base):
    """
    A lesson learned from campaign execution.
    
    Lessons capture what went wrong and how to avoid it,
    enabling agents to improve over time.
    """
    __tablename__ = "campaign_lessons"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Lesson identification
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[LessonCategory] = mapped_column(
        Enum(LessonCategory, name="lesson_category", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    
    # What happened
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Situation description
    trigger_event: Mapped[str] = mapped_column(Text, nullable=False)  # What triggered the issue
    
    # Impact assessment
    impact_severity: Mapped[str] = mapped_column(String(20), default="medium")  # low/medium/high/critical
    budget_impact: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Cost of the issue
    time_impact_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Time lost
    
    # Prevention strategy
    prevention_steps: Mapped[list] = mapped_column(JSONB, nullable=False)  # How to avoid in future
    detection_signals: Mapped[list] = mapped_column(JSONB, default=list)  # Early warning signs
    
    # Source tracking
    source_campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    source_task_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaign_tasks.id", ondelete="SET NULL"), 
        nullable=True
    )
    
    # User ownership
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # How many times this lesson helped avoid the issue
    times_applied: Mapped[int] = mapped_column(Integer, default=0)
    
    # Tags for searching
    tags: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    
    __table_args__ = (
        Index('idx_lesson_category_severity', 'category', 'impact_severity'),
        Index('idx_lesson_campaign', 'source_campaign_id'),
    )
    
    def __repr__(self) -> str:
        return f"<CampaignLesson {self.title}>"


class PlanRevision(Base):
    """
    History of plan revisions for a campaign.
    
    Tracks when and why plans were modified, enabling
    analysis of what revisions work well.
    """
    __tablename__ = "plan_revisions"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Campaign reference
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # Revision metadata
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[RevisionTrigger] = mapped_column(
        Enum(RevisionTrigger, name="revision_trigger", values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    trigger_details: Mapped[str] = mapped_column(Text, nullable=False)  # What specifically triggered this
    
    # Plan snapshots
    plan_before: Mapped[dict] = mapped_column(JSONB, nullable=False)  # State before revision
    plan_after: Mapped[dict] = mapped_column(JSONB, nullable=False)   # State after revision
    
    # What changed
    changes_summary: Mapped[str] = mapped_column(Text, nullable=False)
    tasks_added: Mapped[int] = mapped_column(Integer, default=0)
    tasks_removed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_modified: Mapped[int] = mapped_column(Integer, default=0)
    streams_added: Mapped[int] = mapped_column(Integer, default=0)
    streams_removed: Mapped[int] = mapped_column(Integer, default=0)
    
    # Reasoning
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)  # Why we made this change
    expected_improvement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Outcome tracking (filled in later)
    outcome_assessed: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    outcome_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Was this a user-requested or agent-initiated revision?
    initiated_by: Mapped[str] = mapped_column(String(50), nullable=False)  # "agent" or "user"
    approved_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    outcome_assessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    __table_args__ = (
        Index('idx_revision_campaign_number', 'campaign_id', 'revision_number'),
        Index('idx_revision_trigger', 'trigger'),
    )
    
    def __repr__(self) -> str:
        return f"<PlanRevision campaign={self.campaign_id} rev={self.revision_number}>"


class ProactiveSuggestion(Base):
    """
    Agent-generated optimization suggestions.
    
    Agents can proactively suggest improvements based on
    what they observe during execution.
    """
    __tablename__ = "proactive_suggestions"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Campaign reference
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # Suggestion details
    suggestion_type: Mapped[SuggestionType] = mapped_column(
        Enum(SuggestionType, name="suggestion_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Status
    status: Mapped[SuggestionStatus] = mapped_column(
        Enum(SuggestionStatus, name="suggestion_status", values_callable=lambda x: [e.value for e in x]),
        default=SuggestionStatus.PENDING,
        index=True
    )
    
    # Urgency and confidence
    urgency: Mapped[str] = mapped_column(String(20), default="medium")  # low/medium/high/critical
    confidence: Mapped[float] = mapped_column(Float, default=0.7)  # 0-1 confidence in suggestion
    
    # Supporting evidence
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)  # Data backing this suggestion
    based_on_patterns: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Pattern IDs used
    based_on_lessons: Mapped[Optional[list]] = mapped_column(JSONB, default=list)   # Lesson IDs used
    
    # Recommended action
    recommended_action: Mapped[dict] = mapped_column(JSONB, nullable=False)  # What to do
    estimated_benefit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    estimated_cost: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Cost to implement
    
    # Can the agent apply this automatically?
    can_auto_apply: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_apply_conditions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # User response
    user_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Outcome tracking (filled in after action)
    outcome_tracked: Mapped[bool] = mapped_column(Boolean, default=False)
    actual_benefit: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Expiration (some suggestions are time-sensitive)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    
    __table_args__ = (
        Index('idx_suggestion_campaign_status', 'campaign_id', 'status'),
        Index('idx_suggestion_type_urgency', 'suggestion_type', 'urgency'),
    )
    
    @property
    def is_expired(self) -> bool:
        """Check if suggestion has expired."""
        if not self.expires_at:
            return False
        return utc_now() > ensure_utc(self.expires_at)
    
    def __repr__(self) -> str:
        return f"<ProactiveSuggestion {self.title} ({self.suggestion_type.value})>"


# Import for relationship in Campaign model
from app.models import Campaign
Campaign.patterns = relationship(
    "CampaignPattern",
    foreign_keys="[CampaignPattern.source_campaign_id]",
    backref="source_campaign"
)
Campaign.lessons = relationship(
    "CampaignLesson",
    back_populates="source_campaign",
    cascade="all, delete-orphan"
)
Campaign.plan_revisions = relationship(
    "PlanRevision",
    back_populates="campaign",
    cascade="all, delete-orphan",
    order_by="PlanRevision.revision_number"
)
Campaign.suggestions = relationship(
    "ProactiveSuggestion",
    back_populates="campaign",
    cascade="all, delete-orphan"
)

# Back populates for the relationships
CampaignLesson.source_campaign = relationship("Campaign", back_populates="lessons")
PlanRevision.campaign = relationship("Campaign", back_populates="plan_revisions")
ProactiveSuggestion.campaign = relationship("Campaign", back_populates="suggestions")
