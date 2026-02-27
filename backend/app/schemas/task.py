"""Pydantic schemas for Task management."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums (mirror the SQLAlchemy enums)
# =============================================================================

class TaskType(str, Enum):
    CAMPAIGN_ACTION = "campaign_action"
    REVIEW_REQUIRED = "review_required"
    FOLLOW_UP = "follow_up"
    PERSONAL = "personal"
    SYSTEM = "system"
    IDEA_ACTION = "idea_action"


class TaskStatus(str, Enum):
    CREATED = "created"
    READY = "ready"
    BLOCKED = "blocked"
    DEFERRED = "deferred"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DELEGATED = "delegated"


class TaskSortBy(str, Enum):
    PRIORITY = "priority"
    DUE_DATE = "due_date"
    VALUE = "value"
    VALUE_PER_HOUR = "value_per_hour"
    CREATED = "created"
    UPDATED = "updated"


# =============================================================================
# Base Schemas
# =============================================================================

class TaskBase(BaseModel):
    """Base task fields."""
    title: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=10_000)
    task_type: TaskType = TaskType.PERSONAL
    due_date: Optional[datetime] = None
    estimated_value: Optional[float] = Field(None, ge=0)
    estimated_effort_minutes: Optional[int] = Field(None, ge=1)


class TaskCreate(TaskBase):
    """Schema for creating a new task."""
    source_type: Optional[str] = Field(None, max_length=100)
    source_id: Optional[UUID] = None
    source_context: Optional[Dict[str, Any]] = None


class TaskUpdate(BaseModel):
    """Schema for updating a task."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=10_000)
    task_type: Optional[TaskType] = None
    due_date: Optional[datetime] = None
    estimated_value: Optional[float] = Field(None, ge=0)
    estimated_effort_minutes: Optional[int] = Field(None, ge=1)
    status: Optional[TaskStatus] = None
    blocked_by: Optional[str] = Field(None, max_length=1_000)
    blocked_by_task_id: Optional[UUID] = None
    deferred_until: Optional[datetime] = None
    source_context: Optional[Dict[str, Any]] = None


class TaskComplete(BaseModel):
    """Schema for completing a task."""
    completion_notes: Optional[str] = Field(None, max_length=10_000)
    actual_value: Optional[float] = Field(None, ge=0)


class TaskDefer(BaseModel):
    """Schema for deferring a task."""
    defer_until: datetime


class TaskBlock(BaseModel):
    """Schema for blocking a task."""
    blocked_by: str = Field(..., min_length=1)
    blocked_by_task_id: Optional[UUID] = None


# =============================================================================
# Response Schemas
# =============================================================================

class TaskResponse(TaskBase):
    """Full task response."""
    id: UUID
    user_id: UUID
    priority_score: float
    status: TaskStatus
    blocked_by: Optional[str] = None
    blocked_by_task_id: Optional[UUID] = None
    deferred_until: Optional[datetime] = None
    source_type: Optional[str] = None
    source_id: Optional[UUID] = None
    source_context: Optional[Dict[str, Any]] = None
    completed_at: Optional[datetime] = None
    completion_notes: Optional[str] = None
    actual_value: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    last_viewed_at: Optional[datetime] = None
    
    # Computed properties
    is_overdue: bool = False
    is_actionable: bool = False
    value_per_hour: Optional[float] = None
    
    class Config:
        from_attributes = True
    
    @classmethod
    def from_task(cls, task) -> "TaskResponse":
        """Create response from Task model with computed properties."""
        data = {
            "id": task.id,
            "user_id": task.user_id,
            "title": task.title,
            "description": task.description,
            "task_type": task.task_type,
            "due_date": task.due_date,
            "estimated_value": task.estimated_value,
            "estimated_effort_minutes": task.estimated_effort_minutes,
            "priority_score": task.priority_score,
            "status": task.status,
            "blocked_by": task.blocked_by,
            "blocked_by_task_id": task.blocked_by_task_id,
            "deferred_until": task.deferred_until,
            "source_type": task.source_type,
            "source_id": task.source_id,
            "source_context": task.source_context,
            "completed_at": task.completed_at,
            "completion_notes": task.completion_notes,
            "actual_value": task.actual_value,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "last_viewed_at": task.last_viewed_at,
            "is_overdue": task.is_overdue,
            "is_actionable": task.is_actionable,
            "value_per_hour": task.value_per_hour,
        }
        return cls(**data)


class TaskListResponse(BaseModel):
    """Response for list of tasks."""
    tasks: List[TaskResponse]
    total: int
    limit: int
    offset: int


class TaskCountsResponse(BaseModel):
    """Response for task counts by status."""
    created: int = 0
    ready: int = 0
    blocked: int = 0
    deferred: int = 0
    in_progress: int = 0
    completed: int = 0
    cancelled: int = 0
    delegated: int = 0
    
    # Computed counts
    overdue: int = 0
    due_today: int = 0
    active: int = 0


class TaskSummaryResponse(BaseModel):
    """Summary response for dashboard widgets."""
    counts: TaskCountsResponse
    top_tasks: List[TaskResponse]
    overdue_tasks: List[TaskResponse]
    due_soon_tasks: List[TaskResponse]


class TaskCompletionTrend(BaseModel):
    """Daily completion data for trend chart."""
    date: str
    completed: int


class TaskAnalyticsResponse(BaseModel):
    """Response for task analytics/dashboard data."""
    period_days: int
    completed_count: int
    value_captured: float
    active_value: float
    avg_completion_hours: Optional[float]
    on_time_rate: Optional[float]
    by_type: dict[str, int]
    completion_trend: List[TaskCompletionTrend]


class DashboardTasksResponse(BaseModel):
    """Combined response for dashboard task widget."""
    summary: TaskSummaryResponse
    analytics: TaskAnalyticsResponse


# =============================================================================
# Query Parameters
# =============================================================================

class TaskFilters(BaseModel):
    """Query filters for task list."""
    statuses: Optional[List[TaskStatus]] = None
    task_types: Optional[List[TaskType]] = None
    include_completed: bool = False
    sort_by: TaskSortBy = TaskSortBy.PRIORITY
    limit: int = Field(50, ge=1, le=100)
    offset: int = Field(0, ge=0)
