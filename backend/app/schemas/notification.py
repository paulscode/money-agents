"""Pydantic schemas for Notifications."""
from datetime import datetime
from typing import Optional, List
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, Field


class NotificationType(str, Enum):
    """Notification type enum for API."""
    TASK_CREATED = "task_created"
    TASK_DUE_SOON = "task_due_soon"
    TASK_OVERDUE = "task_overdue"
    TASK_COMPLETED = "task_completed"
    CAMPAIGN_STARTED = "campaign_started"
    CAMPAIGN_COMPLETED = "campaign_completed"
    CAMPAIGN_FAILED = "campaign_failed"
    INPUT_REQUIRED = "input_required"
    THRESHOLD_WARNING = "threshold_warning"
    OPPORTUNITIES_DISCOVERED = "opportunities_discovered"
    HIGH_VALUE_OPPORTUNITY = "high_value_opportunity"
    PROPOSAL_SUBMITTED = "proposal_submitted"
    PROPOSAL_APPROVED = "proposal_approved"
    PROPOSAL_NEEDS_REVIEW = "proposal_needs_review"
    AGENT_ERROR = "agent_error"
    SYSTEM_ALERT = "system_alert"
    CREDENTIAL_EXPIRING = "credential_expiring"


class NotificationPriority(str, Enum):
    """Notification priority levels for API."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


# ==========================================================================
# Response Schemas
# ==========================================================================

class NotificationResponse(BaseModel):
    """Notification response schema."""
    id: UUID
    user_id: UUID
    type: NotificationType
    priority: NotificationPriority
    title: str
    message: str
    link: Optional[str] = None
    link_text: Optional[str] = None
    source_type: Optional[str] = None
    source_id: Optional[UUID] = None
    extra_data: Optional[dict] = None
    read_at: Optional[datetime] = None
    dismissed_at: Optional[datetime] = None
    created_at: datetime
    
    # Computed properties
    is_read: bool
    is_dismissed: bool
    
    class Config:
        from_attributes = True


class NotificationListResponse(BaseModel):
    """Response for list of notifications."""
    notifications: List[NotificationResponse]
    total_unread: int


class NotificationCountsResponse(BaseModel):
    """Response for notification counts."""
    total: int
    by_priority: dict = Field(
        default_factory=lambda: {"low": 0, "medium": 0, "high": 0, "urgent": 0}
    )


class MarkReadResponse(BaseModel):
    """Response for mark as read operations."""
    success: bool
    count: int = 1


class DismissResponse(BaseModel):
    """Response for dismiss operations."""
    success: bool
    count: int = 1
