"""
Tool Approval Models - Human-in-loop approval for tool executions.

Some tools may require human approval before execution, such as:
- Financial transactions
- Content publishing
- External API calls with side effects
- Resource-intensive operations

This module provides:
- ToolApprovalRequest: Pending approval requests
- Approval workflow status tracking
- Expiration handling for time-sensitive requests
"""
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, Boolean, DateTime, Enum, Integer, Text, Index, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class ApprovalStatus(str, enum.Enum):
    """Status of an approval request."""
    PENDING = "pending"  # Awaiting human review
    APPROVED = "approved"  # Human approved execution
    REJECTED = "rejected"  # Human rejected execution
    EXPIRED = "expired"  # Request timed out
    CANCELLED = "cancelled"  # Requester cancelled


class ApprovalUrgency(str, enum.Enum):
    """Urgency level for approval requests."""
    LOW = "low"  # Can wait hours/days
    MEDIUM = "medium"  # Should be reviewed within an hour
    HIGH = "high"  # Needs attention within minutes
    CRITICAL = "critical"  # Immediate attention required


class ToolApprovalRequest(Base):
    """
    A request for human approval before tool execution.
    
    When a tool has requires_approval=True, execution creates an approval
    request instead of running immediately. A human must approve/reject
    before execution proceeds.
    """
    __tablename__ = "tool_approval_requests"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # What tool and with what parameters
    tool_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    
    # Who requested this execution
    requested_by_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Campaign context (optional - may be standalone execution)
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Approval status
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, name="approval_status", values_callable=lambda x: [e.value for e in x]),
        default=ApprovalStatus.PENDING,
        nullable=False,
        index=True
    )
    urgency: Mapped[ApprovalUrgency] = mapped_column(
        Enum(ApprovalUrgency, name="approval_urgency", values_callable=lambda x: [e.value for e in x]),
        default=ApprovalUrgency.MEDIUM,
        nullable=False,
        index=True
    )
    
    # Context for the reviewer
    reason: Mapped[str] = mapped_column(Text, nullable=False)  # Why is this tool being called?
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # What should happen?
    risk_assessment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Potential risks
    estimated_cost: Mapped[Optional[float]] = mapped_column(nullable=True)  # Estimated $ cost
    
    # Reviewer decision
    reviewed_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Execution result (populated after approval and execution)
    execution_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool_executions.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Timing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Relationships
    tool: Mapped["Tool"] = relationship("Tool", foreign_keys=[tool_id])
    requested_by: Mapped["User"] = relationship("User", foreign_keys=[requested_by_id])
    reviewed_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[reviewed_by_id])
    campaign: Mapped[Optional["Campaign"]] = relationship("Campaign", foreign_keys=[campaign_id])
    execution: Mapped[Optional["ToolExecution"]] = relationship("ToolExecution", foreign_keys=[execution_id])
    
    __table_args__ = (
        Index('idx_approval_status_urgency', 'status', 'urgency'),
        Index('idx_approval_expires_at', 'expires_at'),
        Index('idx_approval_created_at', 'created_at'),
    )
    
    def __repr__(self) -> str:
        return f"<ToolApprovalRequest {self.id} tool={self.tool_id} status={self.status}>"
    
    def is_expired(self) -> bool:
        """Check if this request has expired."""
        if self.expires_at is None:
            return False
        return utc_now() > ensure_utc(self.expires_at)
    
    def can_be_reviewed(self) -> bool:
        """Check if this request can still be reviewed."""
        return self.status == ApprovalStatus.PENDING and not self.is_expired()
    
    @classmethod
    def default_expiry_for_urgency(cls, urgency: ApprovalUrgency) -> timedelta:
        """Get default expiry time based on urgency."""
        return {
            ApprovalUrgency.LOW: timedelta(days=7),
            ApprovalUrgency.MEDIUM: timedelta(hours=24),
            ApprovalUrgency.HIGH: timedelta(hours=4),
            ApprovalUrgency.CRITICAL: timedelta(hours=1),
        }.get(urgency, timedelta(hours=24))
