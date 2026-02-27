"""Agent scheduler models for managing agent execution and budgets."""
from datetime import datetime, timezone
from app.core.datetime_utils import utc_now
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, Boolean, DateTime, Enum, Float, Integer, Text, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class AgentStatus(str, enum.Enum):
    """Agent execution status."""
    IDLE = "idle"              # Ready to run, waiting for schedule
    RUNNING = "running"        # Currently executing
    PAUSED = "paused"          # Manually paused by user
    ERROR = "error"            # Last run failed
    BUDGET_EXCEEDED = "budget_exceeded"  # Paused due to budget limit


class AgentRunStatus(str, enum.Enum):
    """Individual agent run status."""
    PENDING = "pending"        # Queued, waiting to start
    RUNNING = "running"        # Currently executing
    COMPLETED = "completed"    # Finished successfully
    FAILED = "failed"          # Finished with error
    CANCELLED = "cancelled"    # Cancelled by user
    TIMEOUT = "timeout"        # Exceeded time limit


class BudgetPeriod(str, enum.Enum):
    """Budget period for spending limits."""
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class AgentDefinition(Base):
    """
    Registered agent definitions.
    
    Each agent type (opportunity_scout, proposal_writer, etc.) has one row here.
    This tracks the agent's current status and configuration.
    """
    __tablename__ = "agent_definitions"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Identity
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Status
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, name="agent_status", values_callable=lambda x: [e.value for e in x]),
        default=AgentStatus.IDLE,
        nullable=False,
        index=True
    )
    status_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Error message or status details
    
    # Scheduling
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    schedule_interval_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)  # Default: 1 hour
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_run_duration_minutes: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True
    )  # Expected runtime in minutes, for staleness detection
    
    # Budget controls
    budget_limit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # USD limit
    budget_period: Mapped[BudgetPeriod] = mapped_column(
        Enum(BudgetPeriod, name="budget_period", values_callable=lambda x: [e.value for e in x]),
        default=BudgetPeriod.DAILY,
        nullable=False
    )
    budget_used: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)  # Current period usage
    budget_reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    budget_warning_threshold: Mapped[float] = mapped_column(Float, default=0.8, nullable=False)  # Warn at 80%
    
    # Configuration
    default_model_tier: Mapped[str] = mapped_column(String(50), default="fast", nullable=False)
    config: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)  # Agent-specific config
    
    # Statistics
    total_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_runs: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: utc_now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: utc_now(),
        onupdate=lambda: utc_now()
    )
    
    # Relationships
    runs: Mapped[list["AgentRun"]] = relationship("AgentRun", back_populates="agent", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_agent_definitions_status_enabled", "status", "is_enabled"),
        Index("ix_agent_definitions_next_run", "next_run_at"),
    )
    
    def __repr__(self) -> str:
        return f"<AgentDefinition {self.slug} ({self.status})>"


class AgentRun(Base):
    """
    Individual agent execution record.
    
    Each time an agent runs, a new row is created here to track:
    - When it ran and for how long
    - What it accomplished (items processed)
    - Resource usage (tokens, cost)
    - Any errors that occurred
    """
    __tablename__ = "agent_runs"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Agent reference
    agent_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Execution details
    status: Mapped[AgentRunStatus] = mapped_column(
        Enum(AgentRunStatus, name="agent_run_status", values_callable=lambda x: [e.value for e in x]),
        default=AgentRunStatus.PENDING,
        nullable=False,
        index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduled")  # "scheduled", "manual", "event"
    trigger_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Why this run was triggered
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Results
    items_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # e.g., opportunities found
    items_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)    # e.g., proposals created
    items_updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)    # e.g., items updated
    
    # Resource usage
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Campaign context (nullable — only set for campaign-triggered runs)
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Metadata
    run_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)  # Additional run details
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: utc_now())
    
    # Relationships
    agent: Mapped["AgentDefinition"] = relationship("AgentDefinition", back_populates="runs")
    
    __table_args__ = (
        Index("ix_agent_runs_agent_status", "agent_id", "status"),
        Index("ix_agent_runs_created_at", "created_at"),
    )
    
    def __repr__(self) -> str:
        return f"<AgentRun {self.id} ({self.status})>"


class AgentEvent(Base):
    """
    Events that trigger agent actions.
    
    Used for event-driven agent execution, e.g.:
    - opportunity.approved → triggers proposal_writer
    - proposal.approved → triggers campaign_manager
    """
    __tablename__ = "agent_events"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Event details
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "opportunity.approved"
    source_type: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "opportunity", "proposal"
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    
    # Target agent
    target_agent_slug: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Processing status
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_by_run_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Event data
    event_data: Mapped[Optional[dict]] = mapped_column(JSONB, default=dict, nullable=True)  # Event payload
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: utc_now())
    
    __table_args__ = (
        Index("ix_agent_events_unprocessed", "target_agent_slug", "is_processed"),
        Index("ix_agent_events_event_type", "event_type"),
        Index("ix_agent_events_source_id", "source_id"),
        Index("ix_agent_events_target_agent_slug", "target_agent_slug"),
        Index("ix_agent_events_is_processed", "is_processed"),
    )
    
    def __repr__(self) -> str:
        return f"<AgentEvent {self.event_type} → {self.target_agent_slug}>"
