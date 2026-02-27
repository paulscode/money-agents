"""Campaign Execution Stream models for parallel task execution.

This module implements the multi-stream architecture for campaign automation:
- TaskStream: Parallel execution tracks within a campaign
- CampaignTask: Individual tasks with dependencies
- UserInputRequest: Consolidated user input requests

The goal is to maximize parallel execution and minimize manual touch points
by breaking campaigns into independent streams that can run concurrently.
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

class TaskStreamStatus(str, enum.Enum):
    """Status of a task stream."""
    PENDING = "pending"          # Not started yet
    READY = "ready"              # All dependencies met, can execute
    BLOCKED = "blocked"          # Waiting for user input or dependent stream
    IN_PROGRESS = "in_progress"  # Currently executing tasks
    COMPLETED = "completed"      # All tasks completed
    FAILED = "failed"            # Stream failed
    CANCELLED = "cancelled"      # User cancelled


class TaskStatus(str, enum.Enum):
    """Status of an individual task."""
    PENDING = "pending"          # Not started
    QUEUED = "queued"            # Ready to run, in execution queue
    RUNNING = "running"          # Currently executing
    COMPLETED = "completed"      # Finished successfully
    FAILED = "failed"            # Failed with error
    SKIPPED = "skipped"          # Skipped (non-critical or dependency failed)
    BLOCKED = "blocked"          # Waiting for input/dependency
    CANCELLED = "cancelled"      # User cancelled


class TaskType(str, enum.Enum):
    """Type of task."""
    TOOL_EXECUTION = "tool_execution"    # Execute a tool
    LLM_REASONING = "llm_reasoning"      # LLM decision/analysis
    USER_INPUT = "user_input"            # Wait for user input
    WAIT = "wait"                        # Wait for condition/time
    CHECKPOINT = "checkpoint"            # Milestone marker
    PARALLEL_GATE = "parallel_gate"      # Wait for parallel tasks to complete


class InputType(str, enum.Enum):
    """Type of user input required."""
    CREDENTIALS = "credentials"      # API keys, passwords
    TEXT = "text"                    # Free-form text
    CONFIRMATION = "confirmation"    # Yes/no approval
    SELECTION = "selection"          # Choose from options
    FILE = "file"                    # File upload
    BUDGET_APPROVAL = "budget_approval"  # Approve budget
    CONTENT = "content"              # Content like prompts, copy


class InputPriority(str, enum.Enum):
    """Priority of user input request."""
    BLOCKING = "blocking"    # Campaign cannot proceed without this
    HIGH = "high"            # Needed soon for optimal execution
    MEDIUM = "medium"        # Would improve campaign but not critical
    LOW = "low"              # Nice to have


class InputStatus(str, enum.Enum):
    """Status of user input request."""
    PENDING = "pending"      # Awaiting user
    PROVIDED = "provided"    # User has provided value
    EXPIRED = "expired"      # Deadline passed
    CANCELLED = "cancelled"  # No longer needed


# =============================================================================
# Models
# =============================================================================

class TaskStream(Base):
    """
    A parallel execution track within a campaign.
    
    Streams can run independently when their dependencies are met.
    Examples: "research", "setup", "content_creation", "execution"
    """
    __tablename__ = "task_streams"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    
    # Stream identification
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)  # For display ordering
    
    # Status
    status: Mapped[TaskStreamStatus] = mapped_column(
        Enum(TaskStreamStatus, name="task_stream_status", values_callable=lambda x: [e.value for e in x]),
        default=TaskStreamStatus.PENDING,
        index=True
    )
    blocking_reasons: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Why blocked
    
    # Dependencies
    depends_on_streams: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Stream IDs
    requires_inputs: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Input keys needed
    
    # Progress tracking
    tasks_total: Mapped[int] = mapped_column(Integer, default=0)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_failed: Mapped[int] = mapped_column(Integer, default=0)
    tasks_blocked: Mapped[int] = mapped_column(Integer, default=0)
    
    # Execution settings
    can_run_parallel: Mapped[bool] = mapped_column(Boolean, default=False)  # Tasks can run concurrently
    max_concurrent: Mapped[int] = mapped_column(Integer, default=1)  # Max concurrent tasks
    
    # Timing
    estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="task_streams")
    tasks: Mapped[List["CampaignTask"]] = relationship(
        "CampaignTask", 
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="CampaignTask.order_index"
    )
    
    __table_args__ = (
        Index('idx_task_stream_campaign_status', 'campaign_id', 'status'),
    )
    
    @property
    def progress_pct(self) -> float:
        """Calculate progress percentage."""
        if self.tasks_total == 0:
            return 0.0
        return (self.tasks_completed / self.tasks_total) * 100
    
    @property
    def is_blocked(self) -> bool:
        """Check if stream is blocked."""
        return self.status == TaskStreamStatus.BLOCKED
    
    def __repr__(self) -> str:
        return f"<TaskStream {self.name} ({self.status.value})>"


class CampaignTask(Base):
    """
    An individual task within a stream.
    
    Tasks can have dependencies on other tasks and user inputs.
    """
    __tablename__ = "campaign_tasks"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    stream_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("task_streams.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    
    # Task identification
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)  # Execution order within stream
    
    # Task type and execution
    task_type: Mapped[TaskType] = mapped_column(
        Enum(TaskType, name="campaign_task_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    tool_slug: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # For tool_execution tasks
    tool_params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Parameters for tool
    llm_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # For llm_reasoning tasks
    
    # Status
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, name="campaign_task_status", values_callable=lambda x: [e.value for e in x]),
        default=TaskStatus.PENDING,
        index=True
    )
    blocked_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Dependencies
    depends_on_tasks: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Task UUIDs
    depends_on_inputs: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Input keys
    
    # Execution settings
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer, default=5)
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=30)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=True)  # If false, can skip on failure
    
    # Results
    result: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Timing
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    stream: Mapped["TaskStream"] = relationship("TaskStream", back_populates="tasks")
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="tasks")
    
    __table_args__ = (
        Index('idx_campaign_task_stream_status', 'stream_id', 'status'),
        Index('idx_campaign_task_campaign', 'campaign_id', 'status'),
    )
    
    def __repr__(self) -> str:
        return f"<CampaignTask {self.name} ({self.status.value})>"


class UserInputRequest(Base):
    """
    Consolidated user input request.
    
    Instead of asking for inputs one at a time, we batch them and show
    impact (how many tasks/streams are blocked by each input).
    """
    __tablename__ = "user_input_requests"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    
    # Input identification
    input_key: Mapped[str] = mapped_column(String(100), nullable=False)  # Unique within campaign
    input_type: Mapped[InputType] = mapped_column(
        Enum(InputType, name="user_input_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    
    # Request details
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)  # For selection type
    default_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    validation_rules: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Priority and urgency
    priority: Mapped[InputPriority] = mapped_column(
        Enum(InputPriority, name="user_input_priority", values_callable=lambda x: [e.value for e in x]),
        default=InputPriority.MEDIUM
    )
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Impact tracking - what's waiting for this input
    blocking_streams: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Stream IDs
    blocking_tasks: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Task IDs
    blocking_count: Mapped[int] = mapped_column(Integer, default=0)  # Total things blocked
    
    # Status and value
    status: Mapped[InputStatus] = mapped_column(
        Enum(InputStatus, name="user_input_status", values_callable=lambda x: [e.value for e in x]),
        default=InputStatus.PENDING,
        index=True
    )
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # The provided value
    value_metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Additional value data
    provided_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="SET NULL"), 
        nullable=True
    )
    provided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Smart suggestions
    suggested_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suggestion_source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # 'history', 'default', etc.
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    campaign: Mapped["Campaign"] = relationship("Campaign", back_populates="input_requests")
    provided_by: Mapped[Optional["User"]] = relationship("User")
    
    __table_args__ = (
        Index('idx_user_input_campaign_status', 'campaign_id', 'status'),
        Index('idx_user_input_campaign_key', 'campaign_id', 'input_key', unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<UserInputRequest {self.input_key} ({self.status.value})>"


class AutoApprovalRule(Base):
    """
    Configurable rules for what the agent can do without asking.
    
    Each campaign can have custom rules, or use defaults.
    """
    __tablename__ = "auto_approval_rules"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("campaigns.id", ondelete="CASCADE"), 
        nullable=True,  # NULL = global default
        index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # Budget rules
    max_single_spend: Mapped[float] = mapped_column(Float, default=50.0)  # Max spend per action
    daily_spend_limit: Mapped[float] = mapped_column(Float, default=500.0)  # Max daily spend
    
    # Tool execution rules
    approved_tools: Mapped[Optional[list]] = mapped_column(JSONB, default=list)  # Tool slugs
    tool_rate_limits: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # slug -> max/hour
    
    # Content rules
    auto_approve_research: Mapped[bool] = mapped_column(Boolean, default=True)
    content_review_threshold: Mapped[float] = mapped_column(Float, default=100.0)  # Review if > $X
    
    # Retry rules
    retry_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    skip_non_critical_failures: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Escalation rules
    escalate_after_hours: Mapped[int] = mapped_column(Integer, default=24)  # Blocked > X hours
    escalate_budget_pct: Mapped[float] = mapped_column(Float, default=0.8)  # At X% budget
    
    # Active flag
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    campaign: Mapped[Optional["Campaign"]] = relationship("Campaign", back_populates="auto_approval_rules")
    user: Mapped["User"] = relationship("User")
    
    __table_args__ = (
        Index('idx_auto_approval_user_campaign', 'user_id', 'campaign_id'),
    )
    
    def __repr__(self) -> str:
        scope = f"Campaign {self.campaign_id}" if self.campaign_id else "Global"
        return f"<AutoApprovalRule {scope}>"
