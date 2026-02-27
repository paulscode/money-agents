"""
Rate Limit Service - Enforces tool execution rate limits.

Provides:
- Rate limit checking before tool execution
- Rate limit configuration management (CRUD)
- Violation logging and alerting
- Usage statistics within rate limit windows
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ToolRateLimit,
    RateLimitViolation,
    ToolExecution,
    ToolExecutionStatus,
    RateLimitScope,
    RateLimitPeriod,
    Tool,
    User,
)

logger = logging.getLogger(__name__)


# Period durations in seconds
PERIOD_SECONDS = {
    RateLimitPeriod.MINUTE: 60,
    RateLimitPeriod.HOUR: 3600,
    RateLimitPeriod.DAY: 86400,
    RateLimitPeriod.WEEK: 604800,
    RateLimitPeriod.MONTH: 2592000,  # 30 days
}


@dataclass
class RateLimitStatus:
    """Status of a rate limit check."""
    allowed: bool  # Whether the execution is allowed
    limit: Optional[ToolRateLimit] = None  # The limit that was checked (if any)
    current_count: int = 0  # Current executions in this period
    max_count: int = 0  # Maximum allowed
    period_start: Optional[datetime] = None  # When this period started
    period_end: Optional[datetime] = None  # When this period ends
    remaining: int = 0  # Remaining executions allowed
    retry_after_seconds: Optional[int] = None  # When to retry if blocked
    violation_id: Optional[UUID] = None  # If violated, the violation record ID


@dataclass
class RateLimitSummary:
    """Summary of applicable rate limits for a user/tool."""
    limits: List[Dict]  # List of applicable limits with current usage
    total_remaining: int  # Minimum remaining across all limits
    most_restrictive: Optional[Dict] = None  # The limit closest to being hit


class RateLimitService:
    """
    Service for enforcing tool execution rate limits.
    
    Rate limits can be configured at multiple scopes:
    - GLOBAL: System-wide limits (e.g., prevent runaway costs)
    - USER: Per-user limits (e.g., free tier limits)
    - TOOL: Per-tool limits (e.g., external API limits)
    - USER_TOOL: Per user+tool combination (e.g., user X can only use tool Y 10x/day)
    
    Limits are evaluated in order of specificity:
    1. USER_TOOL (most specific)
    2. USER
    3. TOOL
    4. GLOBAL (least specific)
    
    All applicable limits must pass for execution to proceed.
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # ==========================================================================
    # Rate Limit Checking
    # ==========================================================================
    
    async def check_rate_limit(
        self,
        tool_id: UUID,
        user_id: Optional[UUID] = None,
        agent_name: Optional[str] = None,
    ) -> RateLimitStatus:
        """
        Check if an execution is allowed under rate limits.
        
        Evaluates all applicable rate limits and returns the most restrictive result.
        
        Args:
            tool_id: ID of the tool being executed
            user_id: ID of the user triggering the execution
            agent_name: Name of the agent (for logging)
            
        Returns:
            RateLimitStatus indicating if execution is allowed
        """
        # Get all applicable limits
        limits = await self._get_applicable_limits(tool_id, user_id)
        
        if not limits:
            # No limits configured - allow execution
            return RateLimitStatus(allowed=True)
        
        now = utc_now()
        most_restrictive: Optional[Tuple[ToolRateLimit, int, int, datetime]] = None
        
        for limit in limits:
            period_start = self._get_period_start(limit.period, now)
            current_count = await self._count_executions(
                tool_id=tool_id if limit.scope in [RateLimitScope.TOOL, RateLimitScope.USER_TOOL] else None,
                user_id=user_id if limit.scope in [RateLimitScope.USER, RateLimitScope.USER_TOOL] else None,
                since=period_start,
            )
            
            max_allowed = limit.max_executions
            if limit.allow_burst:
                max_allowed = limit.max_executions * (limit.burst_multiplier or 2)
            
            remaining = max_allowed - current_count
            
            # Track most restrictive (lowest remaining)
            if most_restrictive is None or remaining < most_restrictive[1]:
                most_restrictive = (limit, remaining, current_count, period_start)
            
            # Check if limit is exceeded
            if current_count >= max_allowed:
                # Limit exceeded - log violation and return blocked
                violation = await self._log_violation(
                    limit=limit,
                    user_id=user_id,
                    tool_id=tool_id,
                    current_count=current_count,
                    period_start=period_start,
                    agent_name=agent_name,
                )
                
                period_end = period_start + timedelta(seconds=PERIOD_SECONDS[limit.period])
                retry_after = int((period_end - now).total_seconds())
                
                return RateLimitStatus(
                    allowed=False,
                    limit=limit,
                    current_count=current_count,
                    max_count=max_allowed,
                    period_start=period_start,
                    period_end=period_end,
                    remaining=0,
                    retry_after_seconds=max(0, retry_after),
                    violation_id=violation.id if violation else None,
                )
        
        # All limits passed
        if most_restrictive:
            limit, remaining, current_count, period_start = most_restrictive
            period_end = period_start + timedelta(seconds=PERIOD_SECONDS[limit.period])
            return RateLimitStatus(
                allowed=True,
                limit=limit,
                current_count=current_count,
                max_count=limit.max_executions,
                period_start=period_start,
                period_end=period_end,
                remaining=remaining,
            )
        
        return RateLimitStatus(allowed=True)
    
    async def get_rate_limit_summary(
        self,
        tool_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
    ) -> RateLimitSummary:
        """
        Get a summary of all applicable rate limits with current usage.
        
        Args:
            tool_id: Filter by tool
            user_id: Filter by user
            
        Returns:
            Summary of limits and current usage
        """
        limits = await self._get_applicable_limits(tool_id, user_id)
        
        if not limits:
            return RateLimitSummary(limits=[], total_remaining=-1)  # -1 = unlimited
        
        now = utc_now()
        limit_details = []
        min_remaining = float('inf')
        most_restrictive = None
        
        for limit in limits:
            period_start = self._get_period_start(limit.period, now)
            current_count = await self._count_executions(
                tool_id=limit.tool_id,
                user_id=limit.user_id,
                since=period_start,
            )
            
            remaining = limit.max_executions - current_count
            period_end = period_start + timedelta(seconds=PERIOD_SECONDS[limit.period])
            
            detail = {
                "id": str(limit.id),
                "scope": limit.scope.value,
                "max_executions": limit.max_executions,
                "period": limit.period.value,
                "current_count": current_count,
                "remaining": max(0, remaining),
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "name": limit.name,
                "tool_id": str(limit.tool_id) if limit.tool_id else None,
                "user_id": str(limit.user_id) if limit.user_id else None,
            }
            limit_details.append(detail)
            
            if remaining < min_remaining:
                min_remaining = remaining
                most_restrictive = detail
        
        return RateLimitSummary(
            limits=limit_details,
            total_remaining=max(0, int(min_remaining)) if min_remaining != float('inf') else -1,
            most_restrictive=most_restrictive,
        )
    
    # ==========================================================================
    # Rate Limit Management (CRUD)
    # ==========================================================================
    
    async def create_rate_limit(
        self,
        scope: RateLimitScope,
        max_executions: int,
        period: RateLimitPeriod,
        user_id: Optional[UUID] = None,
        tool_id: Optional[UUID] = None,
        max_cost_units: Optional[int] = None,
        allow_burst: bool = False,
        burst_multiplier: int = 2,
        name: Optional[str] = None,
        description: Optional[str] = None,
        created_by_id: Optional[UUID] = None,
    ) -> ToolRateLimit:
        """
        Create a new rate limit configuration.
        
        Args:
            scope: Scope of the limit (GLOBAL, USER, TOOL, USER_TOOL)
            max_executions: Maximum executions allowed per period
            period: Time period (MINUTE, HOUR, DAY, WEEK, MONTH)
            user_id: User ID (required for USER, USER_TOOL scopes)
            tool_id: Tool ID (required for TOOL, USER_TOOL scopes)
            max_cost_units: Optional cost-based limit
            allow_burst: Allow temporary burst above limit
            burst_multiplier: Multiplier for burst (e.g., 2 = 2x normal limit)
            name: Optional friendly name
            description: Optional description
            created_by_id: ID of user creating this limit
            
        Returns:
            Created ToolRateLimit
            
        Raises:
            ValueError: If required IDs are missing for scope
        """
        # Validate scope requirements
        if scope == RateLimitScope.USER and not user_id:
            raise ValueError("USER scope requires user_id")
        if scope == RateLimitScope.TOOL and not tool_id:
            raise ValueError("TOOL scope requires tool_id")
        if scope == RateLimitScope.USER_TOOL and (not user_id or not tool_id):
            raise ValueError("USER_TOOL scope requires both user_id and tool_id")
        
        # Check for duplicate
        existing = await self._find_existing_limit(scope, user_id, tool_id, period)
        if existing:
            raise ValueError(f"Rate limit already exists for this scope/period combination: {existing.id}")
        
        limit = ToolRateLimit(
            scope=scope,
            user_id=user_id,
            tool_id=tool_id,
            max_executions=max_executions,
            period=period,
            max_cost_units=max_cost_units,
            allow_burst=allow_burst,
            burst_multiplier=burst_multiplier,
            name=name,
            description=description,
            created_by_id=created_by_id,
        )
        
        self.db.add(limit)
        await self.db.commit()
        await self.db.refresh(limit)
        
        logger.info(f"Created rate limit: {limit}")
        return limit
    
    async def update_rate_limit(
        self,
        limit_id: UUID,
        max_executions: Optional[int] = None,
        period: Optional[RateLimitPeriod] = None,
        max_cost_units: Optional[int] = None,
        allow_burst: Optional[bool] = None,
        burst_multiplier: Optional[int] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[ToolRateLimit]:
        """
        Update an existing rate limit.
        
        Args:
            limit_id: ID of the limit to update
            (other args): Fields to update (None = no change)
            
        Returns:
            Updated ToolRateLimit or None if not found
        """
        result = await self.db.execute(
            select(ToolRateLimit).where(ToolRateLimit.id == limit_id)
        )
        limit = result.scalar_one_or_none()
        
        if not limit:
            return None
        
        if max_executions is not None:
            limit.max_executions = max_executions
        if period is not None:
            limit.period = period
        if max_cost_units is not None:
            limit.max_cost_units = max_cost_units
        if allow_burst is not None:
            limit.allow_burst = allow_burst
        if burst_multiplier is not None:
            limit.burst_multiplier = burst_multiplier
        if name is not None:
            limit.name = name
        if description is not None:
            limit.description = description
        if is_active is not None:
            limit.is_active = is_active
        
        await self.db.commit()
        await self.db.refresh(limit)
        
        logger.info(f"Updated rate limit: {limit}")
        return limit
    
    async def delete_rate_limit(self, limit_id: UUID) -> bool:
        """
        Delete a rate limit.
        
        Args:
            limit_id: ID of the limit to delete
            
        Returns:
            True if deleted, False if not found
        """
        result = await self.db.execute(
            select(ToolRateLimit).where(ToolRateLimit.id == limit_id)
        )
        limit = result.scalar_one_or_none()
        
        if not limit:
            return False
        
        await self.db.delete(limit)
        await self.db.commit()
        
        logger.info(f"Deleted rate limit: {limit_id}")
        return True
    
    async def get_rate_limit(self, limit_id: UUID) -> Optional[ToolRateLimit]:
        """Get a rate limit by ID."""
        result = await self.db.execute(
            select(ToolRateLimit).where(ToolRateLimit.id == limit_id)
        )
        return result.scalar_one_or_none()
    
    async def list_rate_limits(
        self,
        scope: Optional[RateLimitScope] = None,
        user_id: Optional[UUID] = None,
        tool_id: Optional[UUID] = None,
        is_active: Optional[bool] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[ToolRateLimit]:
        """
        List rate limits with optional filtering.
        
        Args:
            scope: Filter by scope
            user_id: Filter by user
            tool_id: Filter by tool
            is_active: Filter by active status
            limit: Maximum results
            offset: Skip this many results
            
        Returns:
            List of matching rate limits
        """
        query = select(ToolRateLimit)
        
        conditions = []
        if scope:
            conditions.append(ToolRateLimit.scope == scope)
        if user_id:
            conditions.append(ToolRateLimit.user_id == user_id)
        if tool_id:
            conditions.append(ToolRateLimit.tool_id == tool_id)
        if is_active is not None:
            conditions.append(ToolRateLimit.is_active == is_active)
        
        if conditions:
            query = query.where(and_(*conditions))
        
        query = query.order_by(ToolRateLimit.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    # ==========================================================================
    # Violation Management
    # ==========================================================================
    
    async def list_violations(
        self,
        rate_limit_id: Optional[UUID] = None,
        user_id: Optional[UUID] = None,
        tool_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[RateLimitViolation]:
        """
        List rate limit violations with optional filtering.
        
        Args:
            rate_limit_id: Filter by specific limit
            user_id: Filter by user
            tool_id: Filter by tool
            since: Only violations after this time
            limit: Maximum results
            offset: Skip this many results
            
        Returns:
            List of violations
        """
        query = select(RateLimitViolation)
        
        conditions = []
        if rate_limit_id:
            conditions.append(RateLimitViolation.rate_limit_id == rate_limit_id)
        if user_id:
            conditions.append(RateLimitViolation.user_id == user_id)
        if tool_id:
            conditions.append(RateLimitViolation.tool_id == tool_id)
        if since:
            conditions.append(RateLimitViolation.violated_at >= since)
        
        if conditions:
            query = query.where(and_(*conditions))
        
        query = query.order_by(RateLimitViolation.violated_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_violation_count(
        self,
        user_id: Optional[UUID] = None,
        tool_id: Optional[UUID] = None,
        since: Optional[datetime] = None,
    ) -> int:
        """Get count of violations."""
        query = select(func.count(RateLimitViolation.id))
        
        conditions = []
        if user_id:
            conditions.append(RateLimitViolation.user_id == user_id)
        if tool_id:
            conditions.append(RateLimitViolation.tool_id == tool_id)
        if since:
            conditions.append(RateLimitViolation.violated_at >= since)
        
        if conditions:
            query = query.where(and_(*conditions))
        
        result = await self.db.execute(query)
        return result.scalar() or 0
    
    # ==========================================================================
    # Private Helper Methods
    # ==========================================================================
    
    async def _get_applicable_limits(
        self,
        tool_id: Optional[UUID],
        user_id: Optional[UUID],
    ) -> List[ToolRateLimit]:
        """
        Get all rate limits that apply to this tool/user combination.
        
        Returns limits in order of specificity (most specific first).
        """
        conditions = [ToolRateLimit.is_active == True]
        
        # Build OR conditions for applicable scopes
        scope_conditions = [
            # Global always applies
            ToolRateLimit.scope == RateLimitScope.GLOBAL
        ]
        
        if user_id:
            # User-specific limits
            scope_conditions.append(
                and_(
                    ToolRateLimit.scope == RateLimitScope.USER,
                    ToolRateLimit.user_id == user_id
                )
            )
        
        if tool_id:
            # Tool-specific limits
            scope_conditions.append(
                and_(
                    ToolRateLimit.scope == RateLimitScope.TOOL,
                    ToolRateLimit.tool_id == tool_id
                )
            )
        
        if user_id and tool_id:
            # User+tool specific limits
            scope_conditions.append(
                and_(
                    ToolRateLimit.scope == RateLimitScope.USER_TOOL,
                    ToolRateLimit.user_id == user_id,
                    ToolRateLimit.tool_id == tool_id
                )
            )
        
        conditions.append(or_(*scope_conditions))
        
        query = select(ToolRateLimit).where(and_(*conditions))
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def _count_executions(
        self,
        tool_id: Optional[UUID],
        user_id: Optional[UUID],
        since: datetime,
    ) -> int:
        """Count tool executions in a time window."""
        query = select(func.count(ToolExecution.id))
        
        conditions = [
            ToolExecution.created_at >= since,
            # Only count completed or in-progress (not failed before starting)
            ToolExecution.status.in_([
                ToolExecutionStatus.PENDING,
                ToolExecutionStatus.RUNNING,
                ToolExecutionStatus.COMPLETED,
            ])
        ]
        
        if tool_id:
            conditions.append(ToolExecution.tool_id == tool_id)
        if user_id:
            conditions.append(ToolExecution.triggered_by_user_id == user_id)
        
        query = query.where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalar() or 0
    
    def _get_period_start(self, period: RateLimitPeriod, now: datetime) -> datetime:
        """Get the start of the current rate limit period."""
        if period == RateLimitPeriod.MINUTE:
            return now.replace(second=0, microsecond=0)
        elif period == RateLimitPeriod.HOUR:
            return now.replace(minute=0, second=0, microsecond=0)
        elif period == RateLimitPeriod.DAY:
            return now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == RateLimitPeriod.WEEK:
            # Start of week (Monday)
            days_since_monday = now.weekday()
            return (now - timedelta(days=days_since_monday)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        elif period == RateLimitPeriod.MONTH:
            return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            return now
    
    async def _log_violation(
        self,
        limit: ToolRateLimit,
        user_id: Optional[UUID],
        tool_id: UUID,
        current_count: int,
        period_start: datetime,
        agent_name: Optional[str] = None,
    ) -> RateLimitViolation:
        """Log a rate limit violation."""
        violation = RateLimitViolation(
            rate_limit_id=limit.id,
            user_id=user_id,
            tool_id=tool_id,
            current_count=current_count,
            limit_count=limit.max_executions,
            period_start=period_start,
            agent_name=agent_name,
        )
        
        self.db.add(violation)
        await self.db.flush()
        
        logger.warning(
            f"Rate limit violation: {current_count}/{limit.max_executions} "
            f"(user={user_id}, tool={tool_id}, limit={limit.id})"
        )
        
        return violation
    
    async def _find_existing_limit(
        self,
        scope: RateLimitScope,
        user_id: Optional[UUID],
        tool_id: Optional[UUID],
        period: RateLimitPeriod,
    ) -> Optional[ToolRateLimit]:
        """Find an existing limit with the same scope/period."""
        conditions = [
            ToolRateLimit.scope == scope,
            ToolRateLimit.period == period,
        ]
        
        if user_id:
            conditions.append(ToolRateLimit.user_id == user_id)
        else:
            conditions.append(ToolRateLimit.user_id.is_(None))
        
        if tool_id:
            conditions.append(ToolRateLimit.tool_id == tool_id)
        else:
            conditions.append(ToolRateLimit.tool_id.is_(None))
        
        query = select(ToolRateLimit).where(and_(*conditions))
        result = await self.db.execute(query)
        return result.scalar_one_or_none()


# Module-level service factory
async def get_rate_limit_service(db: AsyncSession) -> RateLimitService:
    """Factory function to get a RateLimitService instance."""
    return RateLimitService(db)
