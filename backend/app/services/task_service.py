"""
Task service for managing user tasks.

This service handles task CRUD operations, priority calculations,
smart filtering, and automatic task generation from system events.
"""

import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List
from uuid import UUID
from enum import Enum

from sqlalchemy import select, func, and_, or_, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Task,
    User,
)
from app.models.task import TaskType, TaskStatus

logger = logging.getLogger(__name__)


class TaskSortBy(str, Enum):
    """Options for sorting tasks."""
    PRIORITY = "priority"          # Highest priority_score first
    DUE_DATE = "due_date"         # Soonest due first
    VALUE = "value"               # Highest estimated_value first
    VALUE_PER_HOUR = "value_per_hour"  # Best ROI first
    CREATED = "created"           # Newest first
    UPDATED = "updated"           # Recently updated first


class TaskService:
    """Service for managing user tasks."""
    
    # Priority calculation weights (can be made configurable per-user later)
    PRIORITY_WEIGHTS = {
        "urgency": 0.35,          # Time-based urgency
        "value": 0.30,            # Estimated dollar value
        "effort_roi": 0.15,       # Value relative to effort
        "source_importance": 0.10,  # System-generated vs personal
        "staleness": 0.10,        # Penalty for ignored tasks
    }
    
    # Urgency scoring based on days until due
    URGENCY_SCORES = {
        "overdue": 100,
        "today": 95,
        "tomorrow": 85,
        "this_week": 60,
        "next_week": 40,
        "later": 20,
        "no_due_date": 30,
    }
    
    # Source importance scores
    SOURCE_IMPORTANCE = {
        "campaign": 80,           # From active campaigns
        "opportunity": 70,        # From opportunity scout
        "idea": 60,               # From ideas processing
        "system": 50,             # System notifications
        "personal": 40,           # User created
    }
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # CRUD Operations
    # =========================================================================
    
    async def create_task(
        self,
        user_id: UUID,
        title: str,
        task_type: TaskType = TaskType.PERSONAL,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        estimated_value: Optional[float] = None,
        estimated_effort_minutes: Optional[int] = None,
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        source_context: Optional[dict] = None,
        priority_score: Optional[float] = None,
    ) -> Task:
        """
        Create a new task.
        
        If priority_score is not provided, it will be calculated automatically.
        """
        task = Task(
            user_id=user_id,
            title=title,
            task_type=task_type.value,
            description=description,
            due_date=due_date,
            estimated_value=estimated_value,
            estimated_effort_minutes=estimated_effort_minutes,
            source_type=source_type,
            source_id=source_id,
            source_context=source_context or {},
            status=TaskStatus.CREATED.value,
        )
        
        # Calculate priority if not provided
        if priority_score is not None:
            task.priority_score = priority_score
        else:
            task.priority_score = self._calculate_priority_score(task)
        
        self.db.add(task)
        await self.db.commit()
        await self.db.refresh(task)
        
        logger.info(f"Created task {task.id} for user {user_id}: {title}")
        return task
    
    async def get_task(
        self,
        task_id: UUID,
        user_id: Optional[UUID] = None,
    ) -> Optional[Task]:
        """Get a task by ID, optionally filtered by user."""
        query = select(Task).where(Task.id == task_id)
        if user_id:
            query = query.where(Task.user_id == user_id)
        
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def update_task(
        self,
        task_id: UUID,
        user_id: UUID,
        **updates,
    ) -> Optional[Task]:
        """
        Update a task's properties.
        
        Valid update fields: title, description, due_date, estimated_value,
        estimated_effort_minutes, status, blocked_by, deferred_until, etc.
        """
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        
        allowed_fields = {
            "title", "description", "task_type", "due_date",
            "estimated_value", "estimated_effort_minutes",
            "status", "blocked_by", "blocked_by_task_id",
            "deferred_until", "completion_notes", "actual_value",
            "source_context",
        }
        
        for field, value in updates.items():
            if field in allowed_fields and value is not None:
                # Convert enums to values
                if field == "task_type" and isinstance(value, TaskType):
                    value = value.value
                elif field == "status" and isinstance(value, TaskStatus):
                    value = value.value
                setattr(task, field, value)
        
        # Recalculate priority if relevant fields changed
        priority_relevant = {"due_date", "estimated_value", "estimated_effort_minutes", "status"}
        if priority_relevant & set(updates.keys()):
            task.priority_score = self._calculate_priority_score(task)
        
        task.updated_at = utc_now()
        await self.db.commit()
        await self.db.refresh(task)
        
        logger.info(f"Updated task {task_id}")
        return task
    
    async def delete_task(
        self,
        task_id: UUID,
        user_id: UUID,
    ) -> bool:
        """Delete a task."""
        task = await self.get_task(task_id, user_id)
        if not task:
            return False
        
        await self.db.delete(task)
        await self.db.commit()
        
        logger.info(f"Deleted task {task_id}")
        return True
    
    async def complete_task(
        self,
        task_id: UUID,
        user_id: UUID,
        completion_notes: Optional[str] = None,
        actual_value: Optional[float] = None,
    ) -> Optional[Task]:
        """Mark a task as completed."""
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        
        task.status = TaskStatus.COMPLETED.value
        task.completed_at = utc_now()
        task.completion_notes = completion_notes
        task.actual_value = actual_value
        task.updated_at = utc_now()
        
        await self.db.commit()
        await self.db.refresh(task)
        
        logger.info(f"Completed task {task_id}")
        return task
    
    async def defer_task(
        self,
        task_id: UUID,
        user_id: UUID,
        defer_until: datetime,
    ) -> Optional[Task]:
        """Defer a task until a later date."""
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        
        task.status = TaskStatus.DEFERRED.value
        task.deferred_until = defer_until
        task.updated_at = utc_now()
        task.priority_score = self._calculate_priority_score(task)
        
        await self.db.commit()
        await self.db.refresh(task)
        
        logger.info(f"Deferred task {task_id} until {defer_until}")
        return task
    
    async def block_task(
        self,
        task_id: UUID,
        user_id: UUID,
        blocked_by: str,
        blocked_by_task_id: Optional[UUID] = None,
    ) -> Optional[Task]:
        """Mark a task as blocked."""
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        
        task.status = TaskStatus.BLOCKED.value
        task.blocked_by = blocked_by
        task.blocked_by_task_id = blocked_by_task_id
        task.updated_at = utc_now()
        task.priority_score = self._calculate_priority_score(task)
        
        await self.db.commit()
        await self.db.refresh(task)
        
        logger.info(f"Blocked task {task_id}: {blocked_by}")
        return task
    
    async def mark_viewed(
        self,
        task_id: UUID,
        user_id: UUID,
    ) -> Optional[Task]:
        """Mark a task as viewed (for staleness tracking)."""
        task = await self.get_task(task_id, user_id)
        if not task:
            return None
        
        task.last_viewed_at = utc_now()
        await self.db.commit()
        
        return task
    
    # =========================================================================
    # Query Methods
    # =========================================================================
    
    async def get_tasks(
        self,
        user_id: UUID,
        statuses: Optional[List[TaskStatus]] = None,
        task_types: Optional[List[TaskType]] = None,
        include_completed: bool = False,
        sort_by: TaskSortBy = TaskSortBy.PRIORITY,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Task]:
        """
        Get tasks for a user with flexible filtering.
        
        By default, returns active tasks (not completed/cancelled) sorted by priority.
        """
        query = select(Task).where(Task.user_id == user_id)
        
        # Filter by status
        if statuses:
            status_values = [s.value for s in statuses]
            query = query.where(Task.status.in_(status_values))
        elif not include_completed:
            # Exclude terminal states by default
            excluded = [TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]
            query = query.where(~Task.status.in_(excluded))
        
        # Filter by task type
        if task_types:
            type_values = [t.value for t in task_types]
            query = query.where(Task.task_type.in_(type_values))
        
        # Apply sorting
        if sort_by == TaskSortBy.PRIORITY:
            query = query.order_by(Task.priority_score.desc())
        elif sort_by == TaskSortBy.DUE_DATE:
            # Put tasks with no due date at the end
            query = query.order_by(
                case((Task.due_date == None, 1), else_=0),
                Task.due_date.asc(),
            )
        elif sort_by == TaskSortBy.VALUE:
            query = query.order_by(Task.estimated_value.desc().nullslast())
        elif sort_by == TaskSortBy.VALUE_PER_HOUR:
            # Calculate value per hour, handle nulls
            query = query.order_by(
                case(
                    (
                        and_(Task.estimated_value != None, Task.estimated_effort_minutes != None, Task.estimated_effort_minutes > 0),
                        Task.estimated_value / (Task.estimated_effort_minutes / 60.0)
                    ),
                    else_=0
                ).desc()
            )
        elif sort_by == TaskSortBy.CREATED:
            query = query.order_by(Task.created_at.desc())
        elif sort_by == TaskSortBy.UPDATED:
            query = query.order_by(Task.updated_at.desc())
        
        query = query.offset(offset).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_actionable_tasks(
        self,
        user_id: UUID,
        limit: int = 10,
    ) -> List[Task]:
        """
        Get tasks that are actionable right now.
        
        Actionable = ready or created status, not blocked, not deferred,
        and deferred_until has passed.
        """
        now = utc_now()
        
        query = select(Task).where(
            and_(
                Task.user_id == user_id,
                # Active statuses only
                Task.status.in_([TaskStatus.READY.value, TaskStatus.CREATED.value]),
                # Not blocked
                or_(Task.blocked_by == None, Task.blocked_by == ""),
            )
        ).order_by(Task.priority_score.desc()).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_overdue_tasks(
        self,
        user_id: UUID,
        limit: int = 50,
    ) -> List[Task]:
        """Get tasks that are past their due date."""
        now = utc_now()
        
        query = select(Task).where(
            and_(
                Task.user_id == user_id,
                Task.due_date != None,
                Task.due_date < now,
                ~Task.status.in_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]),
            )
        ).order_by(Task.due_date.asc()).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_due_soon(
        self,
        user_id: UUID,
        hours: int = 24,
        limit: int = 50,
    ) -> List[Task]:
        """Get tasks due within the specified hours."""
        now = utc_now()
        cutoff = now + timedelta(hours=hours)
        
        query = select(Task).where(
            and_(
                Task.user_id == user_id,
                Task.due_date != None,
                Task.due_date >= now,
                Task.due_date <= cutoff,
                ~Task.status.in_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]),
            )
        ).order_by(Task.due_date.asc()).limit(limit)
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_tasks_by_source(
        self,
        user_id: UUID,
        source_type: str,
        source_id: Optional[UUID] = None,
        include_completed: bool = False,
    ) -> List[Task]:
        """Get tasks linked to a specific source (campaign, opportunity, etc)."""
        query = select(Task).where(
            and_(
                Task.user_id == user_id,
                Task.source_type == source_type,
            )
        )
        
        if source_id:
            query = query.where(Task.source_id == source_id)
        
        if not include_completed:
            excluded = [TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]
            query = query.where(~Task.status.in_(excluded))
        
        query = query.order_by(Task.priority_score.desc())
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_task_counts(self, user_id: UUID) -> dict:
        """Get counts of tasks by status for a user."""
        result = await self.db.execute(
            select(Task.status, func.count(Task.id))
            .where(Task.user_id == user_id)
            .group_by(Task.status)
        )
        
        counts = {row[0]: row[1] for row in result.fetchall()}
        
        # Ensure all statuses have a count
        for status in TaskStatus:
            if status.value not in counts:
                counts[status.value] = 0
        
        # Add computed counts
        now = utc_now()
        
        # Overdue count
        overdue_result = await self.db.execute(
            select(func.count(Task.id)).where(
                and_(
                    Task.user_id == user_id,
                    Task.due_date != None,
                    Task.due_date < now,
                    ~Task.status.in_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]),
                )
            )
        )
        counts["overdue"] = overdue_result.scalar() or 0
        
        # Due today count
        today_end = datetime(now.year, now.month, now.day, 23, 59, 59)
        due_today_result = await self.db.execute(
            select(func.count(Task.id)).where(
                and_(
                    Task.user_id == user_id,
                    Task.due_date != None,
                    Task.due_date >= now,
                    Task.due_date <= today_end,
                    ~Task.status.in_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value]),
                )
            )
        )
        counts["due_today"] = due_today_result.scalar() or 0
        
        # Active (not completed/cancelled)
        counts["active"] = sum(
            counts.get(s.value, 0) for s in TaskStatus
            if s not in [TaskStatus.COMPLETED, TaskStatus.CANCELLED]
        )
        
        return counts
    
    # =========================================================================
    # Priority Calculation
    # =========================================================================
    
    def _calculate_priority_score(self, task: Task) -> float:
        """
        Calculate the priority score for a task (0-100).
        
        Factors:
        - Urgency: Based on due date proximity
        - Value: Estimated dollar value
        - Effort ROI: Value relative to effort
        - Source importance: System vs personal
        - Staleness: Penalty for ignored tasks
        """
        scores = {}
        
        # 1. Urgency Score (0-100)
        scores["urgency"] = self._calculate_urgency_score(task)
        
        # 2. Value Score (0-100)
        scores["value"] = self._calculate_value_score(task)
        
        # 3. Effort ROI Score (0-100)
        scores["effort_roi"] = self._calculate_roi_score(task)
        
        # 4. Source Importance Score (0-100)
        scores["source_importance"] = self._calculate_source_score(task)
        
        # 5. Staleness Penalty (reduces score)
        scores["staleness"] = self._calculate_staleness_penalty(task)
        
        # Calculate weighted average
        total = 0.0
        for factor, weight in self.PRIORITY_WEIGHTS.items():
            total += scores.get(factor, 50) * weight
        
        # Clamp to 0-100
        return max(0, min(100, total))
    
    def _calculate_urgency_score(self, task: Task) -> float:
        """Calculate urgency based on due date."""
        # Blocked or deferred tasks have lower urgency
        if task.status in [TaskStatus.BLOCKED.value, TaskStatus.DEFERRED.value]:
            return 10
        
        if not task.due_date:
            return self.URGENCY_SCORES["no_due_date"]
        
        now = utc_now()
        days_until = (ensure_utc(task.due_date) - now).days
        
        if days_until < 0:
            return self.URGENCY_SCORES["overdue"]
        elif days_until == 0:
            return self.URGENCY_SCORES["today"]
        elif days_until == 1:
            return self.URGENCY_SCORES["tomorrow"]
        elif days_until <= 7:
            return self.URGENCY_SCORES["this_week"]
        elif days_until <= 14:
            return self.URGENCY_SCORES["next_week"]
        else:
            return self.URGENCY_SCORES["later"]
    
    def _calculate_value_score(self, task: Task) -> float:
        """Calculate score based on estimated value."""
        if not task.estimated_value:
            return 50  # Neutral if unknown
        
        # Logarithmic scaling - $1 = 20, $100 = 60, $1000 = 80, $10000 = 100
        import math
        if task.estimated_value <= 0:
            return 20
        
        # Log scale with $10000 = 100
        score = 20 + (math.log10(task.estimated_value + 1) / math.log10(10001)) * 80
        return min(100, score)
    
    def _calculate_roi_score(self, task: Task) -> float:
        """Calculate score based on value per hour of effort."""
        if not task.estimated_value or not task.estimated_effort_minutes:
            return 50  # Neutral if unknown
        
        if task.estimated_effort_minutes <= 0:
            return 90  # Very high ROI if minimal effort
        
        # Value per hour
        hours = task.estimated_effort_minutes / 60.0
        value_per_hour = task.estimated_value / hours
        
        # Score: $10/hr = 30, $50/hr = 60, $200/hr = 80, $500/hr = 100
        import math
        if value_per_hour <= 0:
            return 20
        
        score = 20 + (math.log10(value_per_hour + 1) / math.log10(501)) * 80
        return min(100, score)
    
    def _calculate_source_score(self, task: Task) -> float:
        """Calculate score based on source importance."""
        if not task.source_type:
            return self.SOURCE_IMPORTANCE.get("personal", 40)
        
        return self.SOURCE_IMPORTANCE.get(task.source_type, 50)
    
    def _calculate_staleness_penalty(self, task: Task) -> float:
        """
        Calculate staleness - older unviewed tasks get lower scores.
        Returns a score where 100 = fresh, lower = stale.
        """
        reference_time = ensure_utc(task.last_viewed_at or task.created_at)
        if not reference_time:
            return 50
        
        now = utc_now()
        days_stale = (now - reference_time).days
        
        # Fresh: 100, 7 days: 80, 14 days: 60, 30 days: 40, 60+ days: 20
        if days_stale <= 0:
            return 100
        elif days_stale <= 7:
            return 100 - (days_stale * 3)  # 3 points per day
        elif days_stale <= 14:
            return 80 - ((days_stale - 7) * 3)
        elif days_stale <= 30:
            return 60 - ((days_stale - 14) * 1.5)
        else:
            return max(20, 40 - (days_stale - 30) * 0.5)
    
    async def recalculate_priorities(self, user_id: UUID) -> int:
        """
        Recalculate priority scores for all active tasks.
        
        Returns the number of tasks updated.
        """
        tasks = await self.get_tasks(user_id, include_completed=False, limit=1000)
        
        count = 0
        for task in tasks:
            new_score = self._calculate_priority_score(task)
            if abs(task.priority_score - new_score) > 0.5:
                task.priority_score = new_score
                count += 1
        
        if count > 0:
            await self.db.commit()
            logger.info(f"Recalculated priorities for {count} tasks for user {user_id}")
        
        return count
    
    # =========================================================================
    # Analytics & Dashboard
    # =========================================================================
    
    async def get_dashboard_analytics(self, user_id: UUID, days: int = 30) -> dict:
        """
        Get comprehensive task analytics for dashboard display.
        
        Args:
            user_id: The user to get analytics for
            days: Number of days to look back for historical data
        
        Returns:
            Dictionary with analytics data
        """
        from datetime import timedelta
        
        now = utc_now()
        period_start = now - timedelta(days=days)
        
        # Completion stats
        completed_query = select(Task).where(
            Task.user_id == user_id,
            Task.status == TaskStatus.COMPLETED,
            Task.completed_at >= period_start,
        )
        result = await self.db.execute(completed_query)
        completed_tasks = list(result.scalars().all())
        
        # Value captured
        total_value_captured = sum(
            t.estimated_value or 0 for t in completed_tasks
        )
        
        # Average completion time (for tasks with due dates)
        completion_times = []
        for task in completed_tasks:
            if task.completed_at and task.created_at:
                hours = (task.completed_at - task.created_at).total_seconds() / 3600
                completion_times.append(hours)
        
        avg_completion_hours = (
            sum(completion_times) / len(completion_times) 
            if completion_times else None
        )
        
        # On-time completion rate
        on_time = sum(
            1 for t in completed_tasks 
            if t.due_date and t.completed_at and t.completed_at <= t.due_date
        )
        tasks_with_due = sum(1 for t in completed_tasks if t.due_date)
        on_time_rate = (on_time / tasks_with_due * 100) if tasks_with_due else None
        
        # Tasks by type
        type_query = (
            select(Task.task_type, func.count(Task.id))
            .where(
                Task.user_id == user_id,
                Task.status == TaskStatus.COMPLETED,
                Task.completed_at >= period_start,
            )
            .group_by(Task.task_type)
        )
        result = await self.db.execute(type_query)
        by_type = {row[0]: row[1] for row in result.fetchall()}
        
        # Completion trend (last 7 days)
        trend = []
        for i in range(7):
            day_start = now - timedelta(days=i+1)
            day_end = now - timedelta(days=i)
            day_query = select(func.count(Task.id)).where(
                Task.user_id == user_id,
                Task.status == TaskStatus.COMPLETED,
                Task.completed_at >= day_start,
                Task.completed_at < day_end,
            )
            result = await self.db.execute(day_query)
            count = result.scalar() or 0
            trend.append({
                "date": day_start.strftime("%Y-%m-%d"),
                "completed": count,
            })
        
        trend.reverse()  # Oldest first
        
        # Active value (potential value of current tasks)
        active_query = select(func.sum(Task.estimated_value)).where(
            Task.user_id == user_id,
            Task.status.in_([TaskStatus.READY, TaskStatus.IN_PROGRESS]),
            Task.estimated_value.isnot(None),
        )
        result = await self.db.execute(active_query)
        active_value = result.scalar() or 0
        
        return {
            "period_days": days,
            "completed_count": len(completed_tasks),
            "value_captured": total_value_captured,
            "active_value": active_value,
            "avg_completion_hours": avg_completion_hours,
            "on_time_rate": on_time_rate,
            "by_type": by_type,
            "completion_trend": trend,
        }
    
    # =========================================================================
    # Automatic Task Creation
    # =========================================================================
    
    async def create_campaign_task(
        self,
        user_id: UUID,
        campaign_id: UUID,
        title: str,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        estimated_value: Optional[float] = None,
        context: Optional[dict] = None,
    ) -> Task:
        """Create a task from a campaign action."""
        return await self.create_task(
            user_id=user_id,
            title=title,
            task_type=TaskType.CAMPAIGN_ACTION,
            description=description,
            due_date=due_date,
            estimated_value=estimated_value,
            source_type="campaign",
            source_id=campaign_id,
            source_context=context or {},
        )
    
    async def create_opportunity_task(
        self,
        user_id: UUID,
        opportunity_id: UUID,
        title: str,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        estimated_value: Optional[float] = None,
        context: Optional[dict] = None,
    ) -> Task:
        """Create a task from an opportunity."""
        return await self.create_task(
            user_id=user_id,
            title=title,
            task_type=TaskType.FOLLOW_UP,
            description=description,
            due_date=due_date,
            estimated_value=estimated_value,
            source_type="opportunity",
            source_id=opportunity_id,
            source_context=context or {},
        )
    
    async def create_idea_task(
        self,
        user_id: UUID,
        idea_id: UUID,
        title: str,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        context: Optional[dict] = None,
    ) -> Task:
        """Create a task from an idea that needs action."""
        return await self.create_task(
            user_id=user_id,
            title=title,
            task_type=TaskType.IDEA_ACTION,
            description=description,
            due_date=due_date,
            source_type="idea",
            source_id=idea_id,
            source_context=context or {},
        )
    
    async def create_review_task(
        self,
        user_id: UUID,
        title: str,
        description: Optional[str] = None,
        due_date: Optional[datetime] = None,
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        context: Optional[dict] = None,
    ) -> Task:
        """Create a review/approval task."""
        return await self.create_task(
            user_id=user_id,
            title=title,
            task_type=TaskType.REVIEW_REQUIRED,
            description=description,
            due_date=due_date,
            source_type=source_type,
            source_id=source_id,
            source_context=context or {},
        )
