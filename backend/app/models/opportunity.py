"""Opportunity Scout models for discovery, learning, and memory."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, Boolean, DateTime, Enum, Float, Integer, Text, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


# =============================================================================
# Enums
# =============================================================================

class OpportunityStatus(str, enum.Enum):
    """Opportunity lifecycle status."""
    DISCOVERED = "discovered"      # Just found, minimal analysis
    RESEARCHING = "researching"    # Agent is gathering more data
    EVALUATED = "evaluated"        # Agent has scored and assessed
    PRESENTED = "presented"        # Shown to user for review
    APPROVED = "approved"          # User approved → creating proposal
    REJECTED = "rejected"          # User rejected with feedback
    DISMISSED = "dismissed"        # User dismissed without detailed feedback
    EXPIRED = "expired"            # Time-sensitive opportunity passed
    MERGED = "merged"              # Combined with similar opportunity


class OpportunityType(str, enum.Enum):
    """Classification of opportunity types."""
    ARBITRAGE = "arbitrage"           # Price differences to exploit
    CONTENT = "content"               # Content creation monetization
    SERVICE = "service"               # Service offering
    PRODUCT = "product"               # Physical or digital product
    AUTOMATION = "automation"         # Automating existing processes
    AFFILIATE = "affiliate"           # Affiliate/referral opportunities
    INVESTMENT = "investment"         # Investment opportunities
    OTHER = "other"


class RankingTier(str, enum.Enum):
    """Ranking tier for presentation sorting."""
    TOP_PICK = "top_pick"       # Score >= 0.8
    PROMISING = "promising"     # Score 0.6-0.79
    MAYBE = "maybe"             # Score 0.4-0.59
    UNLIKELY = "unlikely"       # Score < 0.4


class TimeSensitivity(str, enum.Enum):
    """How time-sensitive is the opportunity."""
    IMMEDIATE = "immediate"    # < 24 hours
    SHORT = "short"            # Days
    MEDIUM = "medium"          # Weeks
    EVERGREEN = "evergreen"    # No time pressure


class EffortLevel(str, enum.Enum):
    """Estimated effort to pursue."""
    MINIMAL = "minimal"
    MODERATE = "moderate"
    SIGNIFICANT = "significant"
    MAJOR = "major"


class StrategyStatus(str, enum.Enum):
    """Discovery strategy status."""
    ACTIVE = "active"           # Currently in use
    PAUSED = "paused"           # Temporarily disabled
    RETIRED = "retired"         # No longer used (learned it doesn't work)
    DEPRECATED = "deprecated"   # Marked for removal
    EXPERIMENTAL = "experimental"  # Being tested


class InsightType(str, enum.Enum):
    """Type of agent insight."""
    PRINCIPLE = "principle"      # General rule learned
    PATTERN = "pattern"          # Recurring pattern observed
    ANTI_PATTERN = "anti_pattern"  # What to avoid
    HYPOTHESIS = "hypothesis"    # Untested theory
    VALIDATED = "validated"      # Hypothesis confirmed by data


class SummaryType(str, enum.Enum):
    """Memory summary period type."""
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


# =============================================================================
# Models
# =============================================================================

class DiscoveryStrategy(Base):
    """Strategy for discovering opportunities."""
    __tablename__ = "discovery_strategies"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    created_by: Mapped[str] = mapped_column(String(20), default="agent")  # "agent" or "user"
    
    # Strategy Definition
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "search", "monitor", "analyze", "combine"
    
    # Execution Details
    search_queries: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Query templates
    source_types: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # ["web_search", "news", etc.]
    filters: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict)  # Criteria for what to look for
    schedule: Mapped[str] = mapped_column(String(50), default="on_demand")  # "hourly", "daily", "weekly", "on_demand"
    
    # Performance Tracking
    status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus, name="strategy_status", values_callable=lambda x: [e.value for e in x]),
        default=StrategyStatus.ACTIVE,
        nullable=False,
        index=True
    )
    times_executed: Mapped[int] = mapped_column(Integer, default=0)
    last_executed: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_approved: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_rejected: Mapped[int] = mapped_column(Integer, default=0)
    
    # Learning
    effectiveness_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # Calculated from outcomes
    agent_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Agent's observations
    improvement_ideas: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Ideas for refinement
    
    # Lineage
    parent_strategy_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovery_strategies.id", ondelete="SET NULL"),
        nullable=True
    )
    child_strategy_ids: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Strategies derived from this
    
    # Relationships
    opportunities: Mapped[list["Opportunity"]] = relationship("Opportunity", back_populates="discovery_strategy")
    outcomes: Mapped[list["StrategyOutcome"]] = relationship("StrategyOutcome", back_populates="strategy")
    
    __table_args__ = (
        Index("ix_discovery_strategies_status_effectiveness", "status", "effectiveness_score"),
    )
    
    def __repr__(self) -> str:
        return f"<DiscoveryStrategy {self.name}>"


class Opportunity(Base):
    """A discovered potential money-making opportunity."""
    __tablename__ = "opportunities"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Discovery
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    discovery_strategy_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovery_strategies.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "web_search", "rss", "api", "derived"
    source_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # The search query or source identifier
    source_urls: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # URLs where signal was found
    raw_signal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # The actual content that triggered interest
    
    # Classification
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    opportunity_type: Mapped[OpportunityType] = mapped_column(
        Enum(OpportunityType, name="opportunity_type", values_callable=lambda x: [e.value for e in x]),
        default=OpportunityType.OTHER,
        nullable=False,
        index=True
    )
    status: Mapped[OpportunityStatus] = mapped_column(
        Enum(OpportunityStatus, name="opportunity_status", values_callable=lambda x: [e.value for e in x]),
        default=OpportunityStatus.DISCOVERED,
        nullable=False,
        index=True
    )
    
    # Assessment
    initial_assessment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Quick AI evaluation
    detailed_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Deep research findings
    confidence_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # 0-1, how confident agent is
    time_sensitivity: Mapped[Optional[TimeSensitivity]] = mapped_column(
        Enum(TimeSensitivity, name="time_sensitivity", values_callable=lambda x: [e.value for e in x]),
        nullable=True
    )
    estimated_effort: Mapped[Optional[EffortLevel]] = mapped_column(
        Enum(EffortLevel, name="effort_level", values_callable=lambda x: [e.value for e in x]),
        nullable=True
    )
    estimated_revenue_potential: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # {min, max, timeframe, recurring}
    
    # Scoring
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Individual factor scores
    overall_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)  # Weighted composite score
    ranking_tier: Mapped[Optional[RankingTier]] = mapped_column(
        Enum(RankingTier, name="ranking_tier", values_callable=lambda x: [e.value for e in x]),
        nullable=True,
        index=True
    )
    ranking_factors: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Why this ranked where it did
    rank_position: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Position in sorted list when presented
    
    # Resources
    required_tools: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # List of tool slugs needed
    required_skills: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Skills/capabilities needed
    estimated_cost: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # {upfront, ongoing, currency}
    blocking_requirements: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Things that must exist before pursuing
    
    # User Interaction
    presented_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    user_decision: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "approve", "reject", "research_more", "modify"
    user_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # User's comments/reasoning
    proposal_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposals.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Relationships
    similar_opportunity_ids: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # IDs of related opportunities
    derived_from_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Bulk Actions
    bulk_dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    bulk_dismiss_reason: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    
    # Agent Annotations
    agent_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Auto-dismiss reasons, dedup notes, etc.
    
    # Timestamps
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    discovery_strategy: Mapped[Optional["DiscoveryStrategy"]] = relationship(
        "DiscoveryStrategy", 
        back_populates="opportunities"
    )
    
    __table_args__ = (
        Index("ix_opportunities_status_score", "status", "overall_score"),
        Index("ix_opportunities_presented_at", "presented_at"),
        Index("ix_opportunities_user_decision", "user_decision"),
    )
    
    def __repr__(self) -> str:
        return f"<Opportunity {self.title[:50]}>"


class StrategyOutcome(Base):
    """Outcome tracking for strategy executions."""
    __tablename__ = "strategy_outcomes"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    strategy_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("discovery_strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    opportunity_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True  # Not all runs find opportunities
    )
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    
    # Execution Context
    execution_context: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Market conditions, time of day, etc.
    queries_run: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Actual queries executed
    results_count: Mapped[int] = mapped_column(Integer, default=0)  # Raw results before filtering
    
    # Results
    opportunities_discovered: Mapped[int] = mapped_column(Integer, default=0)
    quality_assessment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Agent's assessment of result quality
    
    # User Feedback (if opportunity reached user)
    user_decision: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # approve/reject/etc.
    user_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Learning Extracted
    what_worked: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Agent's notes on successes
    what_failed: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Agent's notes on failures
    suggested_adjustments: Mapped[Optional[list]] = mapped_column(JSONB, default=list)
    
    # Relationships
    strategy: Mapped["DiscoveryStrategy"] = relationship("DiscoveryStrategy", back_populates="outcomes")
    
    def __repr__(self) -> str:
        return f"<StrategyOutcome {self.strategy_id} @ {self.executed_at}>"


class AgentInsight(Base):
    """Generalized learnings extracted by the agent."""
    __tablename__ = "agent_insights"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    last_validated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Content
    insight_type: Mapped[InsightType] = mapped_column(
        Enum(InsightType, name="insight_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Links to outcomes/opportunities that support this
    confidence: Mapped[float] = mapped_column(Float, default=0.5)  # How sure, based on evidence
    
    # Applicability
    domains: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # ["saas", "b2b", "enterprise"]
    conditions: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # When this insight applies
    
    # Validation
    times_applied: Mapped[int] = mapped_column(Integer, default=0)
    times_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    times_contradicted: Mapped[int] = mapped_column(Integer, default=0)
    
    # Hierarchy
    parent_insight_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_insights.id", ondelete="SET NULL"),
        nullable=True
    )
    superseded_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_insights.id", ondelete="SET NULL"),
        nullable=True
    )
    
    __table_args__ = (
        Index("ix_agent_insights_type_confidence", "insight_type", "confidence"),
    )
    
    def __repr__(self) -> str:
        return f"<AgentInsight {self.title[:50]}>"


class MemorySummary(Base):
    """Compressed long-term memory summaries."""
    __tablename__ = "memory_summaries"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    
    # Summary Content
    summary_type: Mapped[SummaryType] = mapped_column(
        Enum(SummaryType, name="summary_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)  # 2-3 paragraph high-level summary
    
    # Structured Learnings
    top_strategies: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Best performing strategies this period
    failed_strategies: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # What didn't work
    new_insights: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Insights gained
    
    # Statistics
    opportunities_found: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_approved: Mapped[int] = mapped_column(Integer, default=0)
    opportunities_rejected: Mapped[int] = mapped_column(Integer, default=0)
    proposals_created: Mapped[int] = mapped_column(Integer, default=0)
    successful_campaigns: Mapped[int] = mapped_column(Integer, default=0)  # If tracked
    
    # Recommendations
    focus_areas: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Where to concentrate efforts
    avoid_areas: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # What to deprioritize
    experiments_suggested: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # New things to try
    
    # Compression
    detailed_records_archived: Mapped[int] = mapped_column(Integer, default=0)  # How many records this summarizes
    
    def __repr__(self) -> str:
        return f"<MemorySummary {self.summary_type.value} {self.period_start.date()}>"


class UserScoutSettings(Base):
    """Per-user settings for opportunity scout."""
    __tablename__ = "user_scout_settings"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Hopper Management
    max_active_proposals: Mapped[int] = mapped_column(Integer, default=10)  # Proposal capacity
    hopper_warning_threshold: Mapped[int] = mapped_column(Integer, default=7)  # When to warn "filling up"
    auto_pause_discovery: Mapped[bool] = mapped_column(Boolean, default=False)  # Stop when hopper full?
    
    # Backlog Management
    max_backlog_size: Mapped[int] = mapped_column(Integer, default=200)  # Skip scouting when this many unreviewed opps exist (0 = disabled)
    
    # Auto-Dismiss
    auto_dismiss_below_score: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = disabled
    auto_dismiss_types: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Types to auto-reject
    
    # Presentation
    default_sort: Mapped[str] = mapped_column(String(50), default="score_desc")  # How to sort by default
    show_unlikely_tier: Mapped[bool] = mapped_column(Boolean, default=True)  # Whether to show lowest tier
    
    # Focus Areas (positive signals)
    preferred_types: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Opportunity types to prioritize
    preferred_domains: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Industries/areas of interest
    
    # Avoid Areas (negative signals)
    excluded_types: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Never show these types
    excluded_keywords: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Filter out if these appear
    
    # Scoring Rubric (user can customize weights)
    custom_rubric_weights: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Override default weights
    
    def __repr__(self) -> str:
        return f"<UserScoutSettings user={self.user_id}>"


class ScoringRubric(Base):
    """Agent's scoring rubric - evolves over time."""
    __tablename__ = "scoring_rubrics"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)  # Only one active at a time
    
    # Rubric Definition
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False)  # {factor_name: {weight, description, signals, etc.}}
    
    # Performance
    opportunities_scored: Mapped[int] = mapped_column(Integer, default=0)
    approval_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # How often scored opportunities get approved
    
    # Evolution
    evolved_from_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scoring_rubrics.id", ondelete="SET NULL"),
        nullable=True
    )
    evolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Why this version was created
    
    def __repr__(self) -> str:
        return f"<ScoringRubric v{self.version} {'(active)' if self.is_active else ''}>"
