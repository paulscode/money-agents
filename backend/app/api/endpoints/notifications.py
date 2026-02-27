"""API endpoints for Notification management."""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_user
from app.models import User
from app.models.notification import NotificationType as ModelNotificationType
from app.services.notification_service import NotificationService
from app.schemas.notification import (
    NotificationType,
    NotificationPriority,
    NotificationResponse,
    NotificationListResponse,
    NotificationCountsResponse,
    MarkReadResponse,
    DismissResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def get_notification_service(db: AsyncSession = Depends(get_db)) -> NotificationService:
    """Dependency to get NotificationService instance."""
    return NotificationService(db)


# ==========================================================================
# Query Endpoints
# ==========================================================================

@router.get("", response_model=NotificationListResponse)
@limiter.limit("120/minute")
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False, description="Only return unread notifications"),
    include_dismissed: bool = Query(False, description="Include dismissed notifications"),
    types: Optional[List[NotificationType]] = Query(None, description="Filter by notification types"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> NotificationListResponse:
    """
    List notifications for the current user.
    
    Returns notifications sorted by creation date (newest first).
    """
    # Convert schema types to model types
    model_types = None
    if types:
        model_types = [ModelNotificationType(t.value) for t in types]
    
    notifications = await notification_service.get_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        include_dismissed=include_dismissed,
        types=model_types,
        limit=limit,
        offset=offset,
    )
    
    unread_count = await notification_service.get_unread_count(current_user.id)
    
    return NotificationListResponse(
        notifications=[
            NotificationResponse(
                id=n.id,
                user_id=n.user_id,
                type=NotificationType(n.type.value),
                priority=NotificationPriority(n.priority.value),
                title=n.title,
                message=n.message,
                link=n.link,
                link_text=n.link_text,
                source_type=n.source_type,
                source_id=n.source_id,
                metadata=n.metadata,
                read_at=n.read_at,
                dismissed_at=n.dismissed_at,
                created_at=n.created_at,
                is_read=n.is_read,
                is_dismissed=n.is_dismissed,
            )
            for n in notifications
        ],
        total_unread=unread_count,
    )


@router.get("/counts", response_model=NotificationCountsResponse)
@limiter.limit("120/minute")
async def get_notification_counts(
    request: Request,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> NotificationCountsResponse:
    """
    Get notification counts (unread, by priority).
    
    This is a lightweight endpoint for updating notification badges.
    """
    total = await notification_service.get_unread_count(current_user.id)
    by_priority = await notification_service.get_counts_by_priority(current_user.id)
    
    return NotificationCountsResponse(
        total=total,
        by_priority=by_priority,
    )


@router.get("/{notification_id}", response_model=NotificationResponse)
@limiter.limit("120/minute")
async def get_notification(
    request: Request,
    notification_id: UUID,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
) -> NotificationResponse:
    """Get a specific notification."""
    notification = await notification_service.get_by_id(
        notification_id=notification_id,
        user_id=current_user.id,
    )
    
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return NotificationResponse(
        id=notification.id,
        user_id=notification.user_id,
        type=NotificationType(notification.type.value),
        priority=NotificationPriority(notification.priority.value),
        title=notification.title,
        message=notification.message,
        link=notification.link,
        link_text=notification.link_text,
        source_type=notification.source_type,
        source_id=notification.source_id,
        metadata=notification.metadata,
        read_at=notification.read_at,
        dismissed_at=notification.dismissed_at,
        created_at=notification.created_at,
        is_read=notification.is_read,
        is_dismissed=notification.is_dismissed,
    )


# ==========================================================================
# Action Endpoints
# ==========================================================================

@router.post("/{notification_id}/read", response_model=MarkReadResponse)
@limiter.limit("120/minute")
async def mark_notification_read(
    request: Request,
    notification_id: UUID,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    """Mark a notification as read."""
    success = await notification_service.mark_as_read(
        notification_id=notification_id,
        user_id=current_user.id,
    )
    
    await db.commit()
    
    return MarkReadResponse(success=success, count=1 if success else 0)


@router.post("/read-all", response_model=MarkReadResponse)
@limiter.limit("120/minute")
async def mark_all_notifications_read(
    request: Request,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MarkReadResponse:
    """Mark all notifications as read."""
    count = await notification_service.mark_all_as_read(user_id=current_user.id)
    await db.commit()
    
    return MarkReadResponse(success=True, count=count)


@router.post("/{notification_id}/dismiss", response_model=DismissResponse)
@limiter.limit("120/minute")
async def dismiss_notification(
    request: Request,
    notification_id: UUID,
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DismissResponse:
    """Dismiss a notification (remove from list)."""
    success = await notification_service.dismiss(
        notification_id=notification_id,
        user_id=current_user.id,
    )
    
    await db.commit()
    
    return DismissResponse(success=success, count=1 if success else 0)


@router.post("/dismiss-old", response_model=DismissResponse)
@limiter.limit("120/minute")
async def dismiss_old_notifications(
    request: Request,
    days: int = Query(30, ge=1, le=365, description="Dismiss notifications older than this many days"),
    notification_service: NotificationService = Depends(get_notification_service),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DismissResponse:
    """Dismiss notifications older than specified days."""
    count = await notification_service.dismiss_old(
        user_id=current_user.id,
        older_than_days=days,
    )
    
    await db.commit()
    
    return DismissResponse(success=True, count=count)
