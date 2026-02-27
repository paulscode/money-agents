"""API endpoints for Task management."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_user
from app.models import User
from app.models.task import TaskType as ModelTaskType, TaskStatus as ModelTaskStatus
from app.services.task_service import TaskService, TaskSortBy as ServiceSortBy
from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TaskComplete,
    TaskDefer,
    TaskBlock,
    TaskResponse,
    TaskListResponse,
    TaskCountsResponse,
    TaskSummaryResponse,
    TaskAnalyticsResponse,
    DashboardTasksResponse,
    TaskType,
    TaskStatus,
    TaskSortBy,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


def get_task_service(db: AsyncSession = Depends(get_db)) -> TaskService:
    """Dependency to get TaskService instance."""
    return TaskService(db)


# ==========================================================================
# Task CRUD Endpoints
# ==========================================================================

@router.get("", response_model=TaskListResponse)
@limiter.limit("120/minute")
async def list_tasks(
    request: Request,
    statuses: Optional[List[TaskStatus]] = Query(None),
    task_types: Optional[List[TaskType]] = Query(None),
    include_completed: bool = False,
    sort_by: TaskSortBy = TaskSortBy.PRIORITY,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskListResponse:
    """
    List tasks with filtering, sorting, and pagination.
    
    By default, returns active (non-completed) tasks sorted by priority score.
    """
    # Convert schema enums to model enums
    model_statuses = None
    if statuses:
        model_statuses = [ModelTaskStatus(s.value) for s in statuses]
    
    model_types = None
    if task_types:
        model_types = [ModelTaskType(t.value) for t in task_types]
    
    # Map sort_by to service enum
    service_sort = ServiceSortBy(sort_by.value)
    
    tasks = await task_service.get_tasks(
        user_id=current_user.id,
        statuses=model_statuses,
        task_types=model_types,
        include_completed=include_completed,
        sort_by=service_sort,
        limit=limit,
        offset=offset,
    )
    
    # Get total count for pagination
    counts = await task_service.get_task_counts(current_user.id)
    total = counts.get("active", 0) if not include_completed else sum(
        v for k, v in counts.items() if k not in ["overdue", "due_today", "active"]
    )
    
    return TaskListResponse(
        tasks=[TaskResponse.from_task(t) for t in tasks],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TaskResponse, status_code=201)
@limiter.limit("60/minute")
async def create_task(
    request: Request,
    task_data: TaskCreate,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Create a new task."""
    task = await task_service.create_task(
        user_id=current_user.id,
        title=task_data.title,
        task_type=ModelTaskType(task_data.task_type.value),
        description=task_data.description,
        due_date=task_data.due_date,
        estimated_value=task_data.estimated_value,
        estimated_effort_minutes=task_data.estimated_effort_minutes,
        source_type=task_data.source_type,
        source_id=task_data.source_id,
        source_context=task_data.source_context,
    )
    
    return TaskResponse.from_task(task)


@router.get("/summary", response_model=TaskSummaryResponse)
@limiter.limit("120/minute")
async def get_task_summary(
    request: Request,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskSummaryResponse:
    """
    Get a summary of tasks for dashboard display.
    
    Includes counts, top priority tasks, overdue tasks, and tasks due soon.
    """
    counts_data = await task_service.get_task_counts(current_user.id)
    top_tasks = await task_service.get_actionable_tasks(current_user.id, limit=5)
    overdue_tasks = await task_service.get_overdue_tasks(current_user.id, limit=5)
    due_soon_tasks = await task_service.get_due_soon(current_user.id, hours=48, limit=5)
    
    return TaskSummaryResponse(
        counts=TaskCountsResponse(**counts_data),
        top_tasks=[TaskResponse.from_task(t) for t in top_tasks],
        overdue_tasks=[TaskResponse.from_task(t) for t in overdue_tasks],
        due_soon_tasks=[TaskResponse.from_task(t) for t in due_soon_tasks],
    )


@router.get("/analytics", response_model=TaskAnalyticsResponse)
@limiter.limit("120/minute")
async def get_task_analytics(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskAnalyticsResponse:
    """
    Get task analytics for dashboard charts.
    
    Includes completion rate, value captured, trend data, and more.
    """
    analytics_data = await task_service.get_dashboard_analytics(
        current_user.id, days=days
    )
    return TaskAnalyticsResponse(**analytics_data)


@router.get("/dashboard", response_model=DashboardTasksResponse)
@limiter.limit("120/minute")
async def get_dashboard_tasks(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> DashboardTasksResponse:
    """
    Get combined task data for dashboard widget.
    
    Includes summary (counts, top tasks) and analytics (trends, value).
    """
    # Get summary
    counts_data = await task_service.get_task_counts(current_user.id)
    top_tasks = await task_service.get_actionable_tasks(current_user.id, limit=5)
    overdue_tasks = await task_service.get_overdue_tasks(current_user.id, limit=5)
    due_soon_tasks = await task_service.get_due_soon(current_user.id, hours=48, limit=5)
    
    summary = TaskSummaryResponse(
        counts=TaskCountsResponse(**counts_data),
        top_tasks=[TaskResponse.from_task(t) for t in top_tasks],
        overdue_tasks=[TaskResponse.from_task(t) for t in overdue_tasks],
        due_soon_tasks=[TaskResponse.from_task(t) for t in due_soon_tasks],
    )
    
    # Get analytics
    analytics_data = await task_service.get_dashboard_analytics(
        current_user.id, days=days
    )
    analytics = TaskAnalyticsResponse(**analytics_data)
    
    return DashboardTasksResponse(
        summary=summary,
        analytics=analytics,
    )


@router.get("/counts", response_model=TaskCountsResponse)
@limiter.limit("120/minute")
async def get_task_counts(
    request: Request,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskCountsResponse:
    """Get task counts by status."""
    counts = await task_service.get_task_counts(current_user.id)
    return TaskCountsResponse(**counts)


@router.get("/actionable", response_model=List[TaskResponse])
@limiter.limit("120/minute")
async def get_actionable_tasks(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> List[TaskResponse]:
    """
    Get tasks that are actionable right now.
    
    Returns tasks that are ready, not blocked, and not deferred.
    Sorted by priority score.
    """
    tasks = await task_service.get_actionable_tasks(current_user.id, limit=limit)
    return [TaskResponse.from_task(t) for t in tasks]


@router.get("/overdue", response_model=List[TaskResponse])
@limiter.limit("120/minute")
async def get_overdue_tasks(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> List[TaskResponse]:
    """Get tasks that are past their due date."""
    tasks = await task_service.get_overdue_tasks(current_user.id, limit=limit)
    return [TaskResponse.from_task(t) for t in tasks]


@router.get("/due-soon", response_model=List[TaskResponse])
@limiter.limit("120/minute")
async def get_due_soon_tasks(
    request: Request,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(50, ge=1, le=100),
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> List[TaskResponse]:
    """Get tasks due within the specified hours."""
    tasks = await task_service.get_due_soon(current_user.id, hours=hours, limit=limit)
    return [TaskResponse.from_task(t) for t in tasks]


@router.get("/by-source/{source_type}", response_model=List[TaskResponse])
@limiter.limit("120/minute")
async def get_tasks_by_source(
    request: Request,
    source_type: str,
    source_id: Optional[UUID] = None,
    include_completed: bool = False,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> List[TaskResponse]:
    """Get tasks linked to a specific source (campaign, opportunity, idea)."""
    tasks = await task_service.get_tasks_by_source(
        user_id=current_user.id,
        source_type=source_type,
        source_id=source_id,
        include_completed=include_completed,
    )
    return [TaskResponse.from_task(t) for t in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
@limiter.limit("120/minute")
async def get_task(
    request: Request,
    task_id: UUID,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Get a specific task by ID."""
    task = await task_service.get_task(task_id, user_id=current_user.id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Mark as viewed
    await task_service.mark_viewed(task_id, current_user.id)
    
    return TaskResponse.from_task(task)


@router.patch("/{task_id}", response_model=TaskResponse)
@limiter.limit("60/minute")
async def update_task(
    request: Request,
    task_id: UUID,
    updates: TaskUpdate,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Update a task's properties."""
    # Convert update data, excluding None values
    update_dict = updates.model_dump(exclude_unset=True)
    
    task = await task_service.update_task(
        task_id=task_id,
        user_id=current_user.id,
        **update_dict,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.delete("/{task_id}", status_code=204, response_model=None)
@limiter.limit("60/minute")
async def delete_task(
    request: Request,
    task_id: UUID,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
):
    """Delete a task."""
    deleted = await task_service.delete_task(task_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")


# ==========================================================================
# Task State Transitions
# ==========================================================================

@router.post("/{task_id}/complete", response_model=TaskResponse)
@limiter.limit("60/minute")
async def complete_task(
    request: Request,
    task_id: UUID,
    completion: TaskComplete = None,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Mark a task as completed."""
    completion = completion or TaskComplete()
    
    task = await task_service.complete_task(
        task_id=task_id,
        user_id=current_user.id,
        completion_notes=completion.completion_notes,
        actual_value=completion.actual_value,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.post("/{task_id}/defer", response_model=TaskResponse)
@limiter.limit("60/minute")
async def defer_task(
    request: Request,
    task_id: UUID,
    defer_data: TaskDefer,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Defer a task until a later date."""
    task = await task_service.defer_task(
        task_id=task_id,
        user_id=current_user.id,
        defer_until=defer_data.defer_until,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.post("/{task_id}/block", response_model=TaskResponse)
@limiter.limit("60/minute")
async def block_task(
    request: Request,
    task_id: UUID,
    block_data: TaskBlock,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Mark a task as blocked."""
    task = await task_service.block_task(
        task_id=task_id,
        user_id=current_user.id,
        blocked_by=block_data.blocked_by,
        blocked_by_task_id=block_data.blocked_by_task_id,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.post("/{task_id}/unblock", response_model=TaskResponse)
@limiter.limit("60/minute")
async def unblock_task(
    request: Request,
    task_id: UUID,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Unblock a blocked task."""
    task = await task_service.update_task(
        task_id=task_id,
        user_id=current_user.id,
        status=ModelTaskStatus.READY,
        blocked_by=None,
        blocked_by_task_id=None,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
@limiter.limit("60/minute")
async def cancel_task(
    request: Request,
    task_id: UUID,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Cancel a task."""
    task = await task_service.update_task(
        task_id=task_id,
        user_id=current_user.id,
        status=ModelTaskStatus.CANCELLED,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


@router.post("/{task_id}/start", response_model=TaskResponse)
@limiter.limit("60/minute")
async def start_task(
    request: Request,
    task_id: UUID,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> TaskResponse:
    """Mark a task as in progress."""
    task = await task_service.update_task(
        task_id=task_id,
        user_id=current_user.id,
        status=ModelTaskStatus.IN_PROGRESS,
    )
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return TaskResponse.from_task(task)


# ==========================================================================
# Priority Management
# ==========================================================================

@router.post("/recalculate-priorities", response_model=dict)
@limiter.limit("60/minute")
async def recalculate_priorities(
    request: Request,
    task_service: TaskService = Depends(get_task_service),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Recalculate priority scores for all active tasks."""
    count = await task_service.recalculate_priorities(current_user.id)
    return {"updated": count}


# ==========================================================================
# Auto-Generated Tasks Sync
# ==========================================================================

@router.post("/sync", response_model=dict)
@limiter.limit("60/minute")
async def sync_auto_generated_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Sync auto-generated tasks from system events.
    
    This will:
    1. Create tasks for pending campaign inputs
    2. Create/update opportunity review batch task
    3. Activate any deferred tasks that are now due
    
    Safe to call frequently - handles deduplication.
    """
    from app.services.task_generation_service import TaskGenerationService
    
    task_gen_service = TaskGenerationService(db)
    
    # Sync campaign input tasks
    campaign_tasks = await task_gen_service.create_tasks_for_pending_inputs(
        user_id=current_user.id
    )
    
    # Sync opportunity review task
    opportunity_task = await task_gen_service.create_or_update_opportunity_review_task(
        user_id=current_user.id,
        min_batch_size=3,  # Lower threshold for better UX
    )
    
    # Activate deferred tasks
    activated_count = await task_gen_service.activate_deferred_tasks(
        user_id=current_user.id
    )
    
    await db.commit()
    
    return {
        "campaign_tasks_created": len(campaign_tasks),
        "opportunity_review_task": "updated" if opportunity_task else "not_needed",
        "deferred_tasks_activated": activated_count,
    }


# ==========================================================================
# AI Integration Endpoints
# ==========================================================================

@router.get("/ai/context", response_model=dict)
@limiter.limit("120/minute")
async def get_ai_task_context(
    request: Request,
    max_tasks: int = Query(5, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Get task context formatted for AI prompts.
    
    Returns:
        - Task summary (counts, overdue, total value)
        - Top priority tasks with details
        - Formatted prompt text ready for injection
    """
    from app.services.task_context_service import TaskContextService, get_brainstorm_task_prompt
    
    task_ctx_service = TaskContextService(db)
    
    # Get summary statistics
    summary = await task_ctx_service.get_task_summary(current_user.id)
    
    # Get formatted prompt context
    task_context = await task_ctx_service.get_task_context_for_prompt(
        user_id=current_user.id,
        max_tasks=max_tasks,
    )
    
    # Get full prompt section
    prompt_section = get_brainstorm_task_prompt(task_context)
    
    return {
        "summary": summary,
        "prompt_context": task_context,
        "full_prompt_section": prompt_section,
    }


@router.get("/ai/summary", response_model=dict)
@limiter.limit("120/minute")
async def get_ai_task_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """
    Get a concise task summary for AI context.
    
    Returns counts and statistics without full task details.
    """
    from app.services.task_context_service import TaskContextService
    
    task_ctx_service = TaskContextService(db)
    summary = await task_ctx_service.get_task_summary(current_user.id)
    
    return summary

