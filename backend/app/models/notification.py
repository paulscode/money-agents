"""Notification model for user alerts and updates."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, Text, DateTime, Enum, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class NotificationType(str, enum.Enum):
    """Notification type enum."""
    # Task-related
    TASK_CREATED = "task_created"
    TASK_DUE_SOON = "task_due_soon"
    TASK_OVERDUE = "task_overdue"
    TASK_COMPLETED = "task_completed"
    
    # Campaign-related
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_COMPLETED = "campaign_completed"
    CAMPAIGN_FAILED = "campaign_failed"
    INPUT_REQUIRED = "input_required"
    THRESHOLD_WARNING = "threshold_warning"
    
    # Opportunity-related
    OPPORTUNITIES_DISCOVERED = "opportunities_discovered"
    HIGH_VALUE_OPPORTUNITY = "high_value_opportunity"
    
    # Proposal-related
    PROPOSAL_SUBMITTED = "proposal_submitted"
    PROPOSAL_APPROVED = "proposal_approved"
    PROPOSAL_NEEDS_REVIEW = "proposal_needs_review"
    
    # System
    AGENT_ERROR = "agent_error"
    SYSTEM_ALERT = "system_alert"
    CREDENTIAL_EXPIRING = "credential_expiring"


class NotificationPriority(str, enum.Enum):
    """Notification priority levels."""
    LOW = "low"           # Informational, can be dismissed easily
    MEDIUM = "medium"     # Worth reading, but not urgent
    HIGH = "high"         # Important, should be addressed soon
    URGENT = "urgent"     # Requires immediate attention


class Notification(Base):
    """User notification model."""
    __tablename__ = "notifications"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False, 
        index=True
    )
    
    # Notification content
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type_v2", values_callable=lambda x: [e.value for e in x], create_type=False),
        nullable=False,
        index=True
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(NotificationPriority, name="notification_priority", values_callable=lambda x: [e.value for e in x], create_type=False),
        default=NotificationPriority.MEDIUM,
        nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Navigation
    link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # URL to relevant page
    link_text: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Text for link button
    
    # Source tracking (what generated this notification)
    source_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # "task", "campaign", "opportunity"
    source_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    
    # Additional data (named extra_data to avoid SQLAlchemy reserved 'metadata')
    extra_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Status tracking
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        nullable=False,
        index=True
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="notifications")
    
    # Composite indexes for common queries
    __table_args__ = (
        Index('idx_notifications_user_unread', 'user_id', 'read_at'),
        Index('idx_notifications_user_created', 'user_id', 'created_at'),
    )
    
    @property
    def is_read(self) -> bool:
        """Check if notification has been read."""
        return self.read_at is not None
    
    @property
    def is_dismissed(self) -> bool:
        """Check if notification has been dismissed."""
        return self.dismissed_at is not None
    
    def mark_as_read(self) -> None:
        """Mark notification as read."""
        if not self.read_at:
            self.read_at = utc_now()
    
    def dismiss(self) -> None:
        """Dismiss notification."""
        if not self.dismissed_at:
            self.dismissed_at = utc_now()
    
    def __repr__(self) -> str:
        return f"<Notification {self.id} - {self.type.value} - {'read' if self.is_read else 'unread'}>"
