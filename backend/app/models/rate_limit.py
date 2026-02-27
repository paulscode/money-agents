"""Rate limit models for controlling tool usage."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class RateLimitScope(str, enum.Enum):
    """Scope for rate limit application."""
    GLOBAL = "global"  # Applies to all users/tools
    USER = "user"  # Per-user limit
    TOOL = "tool"  # Per-tool limit
    USER_TOOL = "user_tool"  # Per user+tool combination


class RateLimitPeriod(str, enum.Enum):
    """Time period for rate limit window."""
    MINUTE = "minute"
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class ToolRateLimit(Base):
    """
    Rate limit configuration for tools.
    
    Supports multiple scopes:
    - GLOBAL: Applies to all tool executions system-wide
    - USER: Per-user limits (user_id must be set)
    - TOOL: Per-tool limits (tool_id must be set)
    - USER_TOOL: Per user+tool combination (both must be set)
    
    Examples:
    - Global: 1000 executions/hour across all tools
    - User: User A can run 100 executions/day
    - Tool: Tool X can only run 50 times/hour (external API limit)
    - User+Tool: User A can run Tool X only 10 times/hour
    """
    __tablename__ = "tool_rate_limits"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Scope configuration
    scope = Column(
        Enum(RateLimitScope, name="rate_limit_scope", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    
    # Optional FKs based on scope
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tool_id = Column(PGUUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=True, index=True)
    
    # Limit configuration
    max_executions = Column(Integer, nullable=False)  # Maximum allowed executions
    period = Column(
        Enum(RateLimitPeriod, name="rate_limit_period", values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    
    # Optional: cost-based limits (alternative/additional to count-based)
    max_cost_units = Column(Integer, nullable=True)  # Maximum cost units per period
    
    # Behavior on limit
    allow_burst = Column(Boolean, default=False)  # Allow temporary burst above limit
    burst_multiplier = Column(Integer, default=2)  # Burst can be 2x normal limit
    
    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    
    # Description for UI
    name = Column(String(255), nullable=True)  # Optional friendly name
    description = Column(Text, nullable=True)  # Why this limit exists
    
    # Metadata
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    created_by_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    
    # Relationships
    user = relationship("User", foreign_keys=[user_id], backref="rate_limits")
    tool = relationship("Tool", backref="rate_limits")
    created_by = relationship("User", foreign_keys=[created_by_id])
    
    def __repr__(self) -> str:
        scope_desc = self.scope.value
        if self.user_id:
            scope_desc += f" user={self.user_id}"
        if self.tool_id:
            scope_desc += f" tool={self.tool_id}"
        return f"<ToolRateLimit {self.max_executions}/{self.period.value} ({scope_desc})>"


class RateLimitViolation(Base):
    """
    Log of rate limit violations for monitoring and alerting.
    
    Tracks when users/tools hit rate limits for:
    - Alerting admins to potential issues
    - User notification
    - Analytics on limit appropriateness
    """
    __tablename__ = "rate_limit_violations"
    
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Which limit was violated
    rate_limit_id = Column(PGUUID(as_uuid=True), ForeignKey("tool_rate_limits.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Context of violation
    user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tool_id = Column(PGUUID(as_uuid=True), ForeignKey("tools.id", ondelete="SET NULL"), nullable=True, index=True)
    
    # Violation details
    current_count = Column(Integer, nullable=False)  # How many executions user had
    limit_count = Column(Integer, nullable=False)  # What the limit was
    period_start = Column(DateTime(timezone=True), nullable=False)  # When the period started
    
    # Additional context
    agent_name = Column(String(100), nullable=True)  # Which agent triggered
    request_context = Column(JSON, nullable=True)  # Optional request details
    
    # Timestamp
    violated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    
    # Relationships
    rate_limit = relationship("ToolRateLimit", backref="violations")
    user = relationship("User", foreign_keys=[user_id])
    tool = relationship("Tool")
    
    def __repr__(self) -> str:
        return f"<RateLimitViolation {self.current_count}/{self.limit_count} at {self.violated_at}>"
