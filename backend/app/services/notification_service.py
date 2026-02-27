"""
Notification Service - Manages user notifications.

This service handles:
- Creating notifications from system events
- Smart notification rules (avoiding spam)
- Notification queries with filtering
- Bulk operations (mark all read, dismiss)
"""

import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, and_, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import (
    Notification,
    NotificationType,
    NotificationPriority,
)

logger = logging.getLogger(__name__)


# ==========================================================================
# Smart Notification Rules
# ==========================================================================

# Define which notification types should be rate-limited
RATE_LIMIT_RULES = {
    # (notification_type, source_type): (max_count, time_window_minutes)
    (NotificationType.OPPORTUNITIES_DISCOVERED, "opportunity_batch"): (1, 60),  # Max 1 per hour
    (NotificationType.TASK_DUE_SOON, "task"): (1, 1440),  # Max 1 per day per task
    (NotificationType.THRESHOLD_WARNING, "campaign"): (1, 60),  # Max 1 per hour per campaign
}

# Default priorities by notification type
DEFAULT_PRIORITIES = {
    NotificationType.TASK_CREATED: NotificationPriority.LOW,
    NotificationType.TASK_DUE_SOON: NotificationPriority.MEDIUM,
    NotificationType.TASK_OVERDUE: NotificationPriority.HIGH,
    NotificationType.TASK_COMPLETED: NotificationPriority.LOW,
    NotificationType.CAMPAIGN_STARTED: NotificationPriority.LOW,
    NotificationType.CAMPAIGN_COMPLETED: NotificationPriority.MEDIUM,
    NotificationType.CAMPAIGN_FAILED: NotificationPriority.HIGH,
    NotificationType.INPUT_REQUIRED: NotificationPriority.HIGH,
    NotificationType.THRESHOLD_WARNING: NotificationPriority.MEDIUM,
    NotificationType.OPPORTUNITIES_DISCOVERED: NotificationPriority.MEDIUM,
    NotificationType.HIGH_VALUE_OPPORTUNITY: NotificationPriority.HIGH,
    NotificationType.PROPOSAL_SUBMITTED: NotificationPriority.LOW,
    NotificationType.PROPOSAL_APPROVED: NotificationPriority.MEDIUM,
    NotificationType.PROPOSAL_NEEDS_REVIEW: NotificationPriority.MEDIUM,
    NotificationType.AGENT_ERROR: NotificationPriority.URGENT,
    NotificationType.SYSTEM_ALERT: NotificationPriority.HIGH,
    NotificationType.CREDENTIAL_EXPIRING: NotificationPriority.HIGH,
}


class NotificationService:
    """
    Service for managing user notifications.
    
    Provides:
    - Smart notification creation with rate limiting
    - Query methods with filtering
    - Bulk operations (mark all read, dismiss old)
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # ==========================================================================
    # Create Notifications
    # ==========================================================================
    
    async def create(
        self,
        user_id: UUID,
        type: NotificationType,
        title: str,
        message: str,
        link: Optional[str] = None,
        link_text: Optional[str] = None,
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        priority: Optional[NotificationPriority] = None,
        extra_data: Optional[dict] = None,
        skip_rate_limit: bool = False,
    ) -> Optional[Notification]:
        """
        Create a notification for a user.
        
        Applies rate limiting to prevent notification spam.
        
        Args:
            user_id: User to notify
            type: Notification type
            title: Short title
            message: Full message (can be markdown)
            link: Optional link to relevant page
            link_text: Text for link button
            source_type: Type of source entity (e.g., "task", "campaign")
            source_id: ID of source entity
            priority: Priority level (defaults based on type)
            extra_data: Additional data
            skip_rate_limit: Force create even if rate limited
            
        Returns:
            Created notification or None if rate limited
        """
        # Apply rate limiting
        if not skip_rate_limit:
            rate_key = (type, source_type)
            if rate_key in RATE_LIMIT_RULES:
                max_count, window_minutes = RATE_LIMIT_RULES[rate_key]
                if await self._is_rate_limited(user_id, type, source_type, source_id, max_count, window_minutes):
                    logger.debug(f"Rate limited notification: {type} for user {user_id}")
                    return None
        
        # Determine priority
        if priority is None:
            priority = DEFAULT_PRIORITIES.get(type, NotificationPriority.MEDIUM)
        
        notification = Notification(
            user_id=user_id,
            type=type,
            priority=priority,
            title=title,
            message=message,
            link=link,
            link_text=link_text,
            source_type=source_type,
            source_id=source_id,
            extra_data=extra_data,
        )
        
        self.db.add(notification)
        await self.db.flush()
        
        logger.info(f"Created notification {notification.id} ({type.value}) for user {user_id}")
        return notification
    
    async def _is_rate_limited(
        self,
        user_id: UUID,
        type: NotificationType,
        source_type: Optional[str],
        source_id: Optional[UUID],
        max_count: int,
        window_minutes: int,
    ) -> bool:
        """Check if notification should be rate limited."""
        window_start = utc_now() - timedelta(minutes=window_minutes)
        
        query = select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.type == type,
            Notification.created_at >= window_start,
        )
        
        if source_type:
            query = query.where(Notification.source_type == source_type)
        if source_id:
            query = query.where(Notification.source_id == source_id)
        
        result = await self.db.execute(query)
        count = result.scalar() or 0
        
        return count >= max_count
    
    # ==========================================================================
    # Convenience Methods for Common Notifications
    # ==========================================================================
    
    async def notify_task_created(
        self,
        user_id: UUID,
        task_id: UUID,
        task_title: str,
    ) -> Optional[Notification]:
        """Notify user of a new task."""
        return await self.create(
            user_id=user_id,
            type=NotificationType.TASK_CREATED,
            title="New task created",
            message=f"A new task has been created: **{task_title}**",
            link=f"/tasks",
            link_text="View Tasks",
            source_type="task",
            source_id=task_id,
        )
    
    async def notify_task_due_soon(
        self,
        user_id: UUID,
        task_id: UUID,
        task_title: str,
        due_date: datetime,
    ) -> Optional[Notification]:
        """Notify user of upcoming task deadline."""
        hours_until = max(0, (due_date - utc_now()).total_seconds() / 3600)
        if hours_until < 24:
            time_str = f"{int(hours_until)} hours"
        else:
            time_str = f"{int(hours_until / 24)} days"
        
        return await self.create(
            user_id=user_id,
            type=NotificationType.TASK_DUE_SOON,
            title=f"Task due in {time_str}",
            message=f"**{task_title}** is due soon.",
            link=f"/tasks",
            link_text="View Task",
            source_type="task",
            source_id=task_id,
        )
    
    async def notify_input_required(
        self,
        user_id: UUID,
        campaign_id: UUID,
        campaign_title: str,
        input_title: str,
    ) -> Optional[Notification]:
        """Notify user that campaign needs input."""
        return await self.create(
            user_id=user_id,
            type=NotificationType.INPUT_REQUIRED,
            title="Input required",
            message=f"Campaign **{campaign_title}** needs your input: {input_title}",
            link=f"/campaigns/{campaign_id}",
            link_text="Provide Input",
            source_type="campaign",
            source_id=campaign_id,
            priority=NotificationPriority.HIGH,
        )

    async def notify_budget_threshold(
        self,
        user_id: UUID,
        campaign_id: UUID,
        campaign_title: str,
        threshold_label: str,
        severity: str,
        spent_sats: int,
        budget_sats: int,
        remaining_sats: int,
        percent_used: float,
    ) -> Optional[Notification]:
        """
        Notify user that a campaign's Bitcoin spend has crossed a budget
        threshold (80 %, 90 %, or 95 %).

        Rate-limited to 1 per hour per campaign via RATE_LIMIT_RULES.
        """
        priority_map = {
            "warning":  NotificationPriority.MEDIUM,
            "critical": NotificationPriority.HIGH,
            "danger":   NotificationPriority.URGENT,
        }
        priority = priority_map.get(severity, NotificationPriority.MEDIUM)

        remaining_fmt = f"{remaining_sats:,}"
        title = f"Budget {threshold_label} used — {campaign_title}"
        message = (
            f"Campaign **{campaign_title}** has used **{percent_used}%** of its "
            f"Bitcoin budget ({spent_sats:,} / {budget_sats:,} sats). "
            f"**{remaining_fmt} sats** remaining."
        )

        return await self.create(
            user_id=user_id,
            type=NotificationType.THRESHOLD_WARNING,
            title=title,
            message=message,
            link=f"/budget",
            link_text="View Budget",
            source_type="campaign",
            source_id=campaign_id,
            priority=priority,
            extra_data={
                "threshold_label": threshold_label,
                "severity": severity,
                "percent_used": percent_used,
                "spent_sats": spent_sats,
                "budget_sats": budget_sats,
                "remaining_sats": remaining_sats,
            },
        )
    
    async def notify_opportunities_discovered(
        self,
        user_id: UUID,
        count: int,
    ) -> Optional[Notification]:
        """Notify user of new opportunities discovered."""
        return await self.create(
            user_id=user_id,
            type=NotificationType.OPPORTUNITIES_DISCOVERED,
            title=f"{count} new opportunities",
            message=f"The Opportunity Scout discovered {count} new opportunities for you to review.",
            link="/scout?filter=new",
            link_text="Review Opportunities",
            source_type="opportunity_batch",
            source_id=None,
        )
    
    async def notify_campaign_completed(
        self,
        user_id: UUID,
        campaign_id: UUID,
        campaign_title: str,
        revenue: Optional[float] = None,
    ) -> Optional[Notification]:
        """Notify user that a campaign completed."""
        message = f"Campaign **{campaign_title}** has completed successfully."
        if revenue and revenue > 0:
            message += f" Generated ${revenue:,.2f} in revenue."
        
        return await self.create(
            user_id=user_id,
            type=NotificationType.CAMPAIGN_COMPLETED,
            title="Campaign completed",
            message=message,
            link=f"/campaigns/{campaign_id}",
            link_text="View Results",
            source_type="campaign",
            source_id=campaign_id,
        )
    
    async def notify_agent_error(
        self,
        user_id: UUID,
        campaign_id: Optional[UUID],
        error_message: str,
    ) -> Optional[Notification]:
        """Notify user of an agent error."""
        return await self.create(
            user_id=user_id,
            type=NotificationType.AGENT_ERROR,
            title="Agent error",
            message=f"An agent encountered an error:\n\n{error_message}",
            link=f"/campaigns/{campaign_id}" if campaign_id else "/campaigns",
            link_text="View Campaign",
            source_type="campaign" if campaign_id else None,
            source_id=campaign_id,
            priority=NotificationPriority.URGENT,
        )

    async def notify_velocity_breaker_tripped(
        self,
        user_id: UUID,
        tx_count: int,
        window_seconds: int,
        threshold: int,
    ) -> Optional[Notification]:
        """
        Notify an admin that the velocity circuit breaker has tripped.

        All agent payments are now blocked until a human resets the breaker
        via the Budget page.
        """
        window_min = window_seconds // 60
        return await self.create(
            user_id=user_id,
            type=NotificationType.SYSTEM_ALERT,
            title="⚠ Velocity breaker tripped — agent payments blocked",
            message=(
                f"The velocity circuit breaker has tripped: **{tx_count} send transactions** "
                f"in the last **{window_min} minutes** exceeded the threshold of **{threshold}**.\n\n"
                f"All agent payments are now **blocked** until you review and reset the breaker."
            ),
            link="/budget",
            link_text="Review & Reset",
            source_type="velocity_breaker",
            priority=NotificationPriority.URGENT,
            skip_rate_limit=True,
            extra_data={
                "tx_count": tx_count,
                "window_seconds": window_seconds,
                "threshold": threshold,
            },
        )
    
    # ==========================================================================
    # Query Methods
    # ==========================================================================
    
    async def get_by_id(
        self,
        notification_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[Notification]:
        """Get notification by ID."""
        query = select(Notification).where(Notification.id == notification_id)
        if user_id:
            query = query.where(Notification.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalars().first()
    
    async def get_notifications(
        self,
        user_id: UUID,
        unread_only: bool = False,
        include_dismissed: bool = False,
        types: Optional[List[NotificationType]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Notification]:
        """Get notifications for a user."""
        query = select(Notification).where(Notification.user_id == user_id)
        
        if unread_only:
            query = query.where(Notification.read_at.is_(None))
        
        if not include_dismissed:
            query = query.where(Notification.dismissed_at.is_(None))
        
        if types:
            query = query.where(Notification.type.in_(types))
        
        query = query.order_by(Notification.created_at.desc())
        query = query.limit(limit).offset(offset)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_unread_count(self, user_id: UUID) -> int:
        """Get count of unread notifications."""
        query = select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.read_at.is_(None),
            Notification.dismissed_at.is_(None),
        )
        result = await self.db.execute(query)
        return result.scalar() or 0
    
    async def get_counts_by_priority(self, user_id: UUID) -> dict:
        """Get unread notification counts grouped by priority."""
        query = (
            select(Notification.priority, func.count(Notification.id))
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
            .group_by(Notification.priority)
        )
        result = await self.db.execute(query)
        counts = {row[0].value: row[1] for row in result.fetchall()}
        
        # Ensure all priorities are present
        for priority in NotificationPriority:
            if priority.value not in counts:
                counts[priority.value] = 0
        
        return counts
    
    # ==========================================================================
    # Update Methods
    # ==========================================================================
    
    async def mark_as_read(
        self,
        notification_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Mark a notification as read."""
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=utc_now())
        )
        return result.rowcount > 0
    
    async def mark_all_as_read(self, user_id: UUID) -> int:
        """Mark all notifications as read for a user."""
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=utc_now())
        )
        return result.rowcount
    
    async def dismiss(
        self,
        notification_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Dismiss a notification."""
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.dismissed_at.is_(None),
            )
            .values(dismissed_at=utc_now())
        )
        return result.rowcount > 0
    
    async def dismiss_old(
        self,
        user_id: UUID,
        older_than_days: int = 30,
    ) -> int:
        """Dismiss notifications older than specified days."""
        cutoff = utc_now() - timedelta(days=older_than_days)
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.created_at < cutoff,
                Notification.dismissed_at.is_(None),
            )
            .values(dismissed_at=utc_now())
        )
        return result.rowcount
    
    # ==========================================================================
    # Delete Methods
    # ==========================================================================
    
    async def delete_old_dismissed(
        self,
        older_than_days: int = 90,
    ) -> int:
        """Delete old dismissed notifications (cleanup job)."""
        cutoff = utc_now() - timedelta(days=older_than_days)
        result = await self.db.execute(
            delete(Notification)
            .where(
                Notification.dismissed_at.is_not(None),
                Notification.dismissed_at < cutoff,
            )
        )
        logger.info(f"Deleted {result.rowcount} old dismissed notifications")
        return result.rowcount
