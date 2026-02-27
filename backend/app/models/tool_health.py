"""
Tool Health Check Models - Track tool health and validation status.

Provides:
- ToolHealthCheck: Historical record of health check results
- Health status tracking over time
- Response time monitoring
"""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, DateTime, Integer, Text, Index, ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class HealthStatus(str, enum.Enum):
    """Health status of a tool."""
    HEALTHY = "healthy"  # Tool is working correctly
    DEGRADED = "degraded"  # Tool works but with issues (slow, partial)
    UNHEALTHY = "unhealthy"  # Tool is not working
    UNKNOWN = "unknown"  # Health not yet checked


class ToolHealthCheck(Base):
    """
    Historical record of a tool health check.
    
    Stores results of health checks for monitoring and debugging.
    """
    __tablename__ = "tool_health_checks"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Which tool was checked
    tool_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Health check result
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # healthy, degraded, unhealthy
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Performance metrics
    response_time_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Check details
    check_type: Mapped[str] = mapped_column(String(50), nullable=False)  # connectivity, validation, full
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Additional check data
    
    # Whether this was a manual or automatic check
    is_automatic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Who triggered the check (null if automatic)
    triggered_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )
    
    # Timestamp
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    
    # Relationships
    tool: Mapped["Tool"] = relationship("Tool", foreign_keys=[tool_id])
    triggered_by: Mapped[Optional["User"]] = relationship("User", foreign_keys=[triggered_by_id])
    
    __table_args__ = (
        Index('idx_health_check_tool_time', 'tool_id', 'checked_at'),
        Index('idx_health_check_status', 'status'),
    )
    
    def __repr__(self) -> str:
        return f"<ToolHealthCheck {self.tool_id} status={self.status}>"
