"""
Task management models.

Tasks represent actionable items for the user - things that need to be done.
Tasks can be auto-generated from system events or manually created.
"""

import uuid
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, String, Text, Index,
    Integer, CheckConstraint, Boolean
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.core.database import Base


class TaskType(str, Enum):
    """Type of task based on origin."""
    CAMPAIGN_ACTION = "campaign_action"  # Campaign needs user input
    REVIEW_REQUIRED = "review_required"  # Something needs review/decision
    FOLLOW_UP = "follow_up"  # Scheduled check-in
    PERSONAL = "personal"  # User-created task
    SYSTEM = "system"  # System maintenance/admin
    IDEA_ACTION = "idea_action"  # Next step from processed idea


class TaskStatus(str, Enum):
    """Status of a task."""
    CREATED = "created"  # New task, not yet triaged
    READY = "ready"  # Can be worked on now
    BLOCKED = "blocked"  # Waiting on something
    DEFERRED = "deferred"  # Intentionally delayed
    IN_PROGRESS = "in_progress"  # Currently being worked on
    COMPLETED = "completed"  # Done
    CANCELLED = "cancelled"  # Abandoned
    DELEGATED = "delegated"  # Handed to agent/system


class Task(Base):
    """
    A task representing something the user needs to do.
    
    Tasks can be auto-generated from:
    - Campaign input requests
    - Opportunity review needs
    - Idea processing results
    - System events (expiring credentials, etc.)
    
    Or manually created by the user.
    """
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    # Core fields
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    task_type = Column(
        String(30),
        nullable=False,
        default=TaskType.PERSONAL.value,
        index=True
    )
    
    # Urgency & Value
    due_date = Column(DateTime(timezone=True), nullable=True)
    estimated_value = Column(Float, nullable=True)  # Potential $ value
    estimated_effort_minutes = Column(Integer, nullable=True)  # How long it takes
    priority_score = Column(Float, default=50.0, nullable=False)  # AI-calculated 0-100
    
    # Status tracking
    status = Column(
        String(20),
        nullable=False,
        default=TaskStatus.CREATED.value,
        index=True
    )
    blocked_by = Column(Text, nullable=True)  # Description of blocker
    blocked_by_task_id = Column(UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    deferred_until = Column(DateTime(timezone=True), nullable=True)
    
    # Source tracking (where did this task come from?)
    source_type = Column(String(50), nullable=True)  # "campaign", "opportunity", "idea", "agent", "user"
    source_id = Column(UUID(as_uuid=True), nullable=True)  # ID of source entity
    source_context = Column(JSONB, nullable=True)  # Additional context from source
    
    # Completion
    completed_at = Column(DateTime(timezone=True), nullable=True)
    completion_notes = Column(Text, nullable=True)
    actual_value = Column(Float, nullable=True)  # What it actually produced
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), default=utc_now)
    updated_at = Column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    user = relationship("User", back_populates="tasks")
    blocking_task = relationship("Task", remote_side=[id], foreign_keys=[blocked_by_task_id])
    
    __table_args__ = (
        Index("ix_tasks_user_status", "user_id", "status"),
        Index("ix_tasks_user_due", "user_id", "due_date"),
        Index("ix_tasks_priority", "user_id", "priority_score"),
        Index("ix_tasks_source", "source_type", "source_id"),
        CheckConstraint(
            "(status != 'deferred') OR (deferred_until IS NOT NULL)",
            name="ck_tasks_deferred_has_until"
        ),
    )
    
    def __repr__(self) -> str:
        return f"<Task {self.title[:30]}... ({self.status})>"
    
    @property
    def is_overdue(self) -> bool:
        """Check if task is past due date."""
        if not self.due_date:
            return False
        return utc_now() > ensure_utc(self.due_date)
    
    @property
    def is_actionable(self) -> bool:
        """Check if task can be worked on now."""
        return self.status in (TaskStatus.CREATED.value, TaskStatus.READY.value)
    
    @property
    def value_per_hour(self) -> Optional[float]:
        """Calculate value per hour of effort."""
        if not self.estimated_value or not self.estimated_effort_minutes:
            return None
        if self.estimated_effort_minutes == 0:
            return None
        return (self.estimated_value / self.estimated_effort_minutes) * 60
