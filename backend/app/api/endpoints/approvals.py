"""
API endpoints for tool approval workflow (human-in-loop).

Provides:
- POST /approvals - Create approval request
- GET /approvals - List pending approvals (admin)
- GET /approvals/{id} - Get approval request
- POST /approvals/{id}/approve - Approve request (admin)
- POST /approvals/{id}/reject - Reject request (admin)
- POST /approvals/{id}/cancel - Cancel request (requester)
- POST /approvals/{id}/execute - Execute after approval
- GET /approvals/pending/count - Get pending counts by urgency
- GET /approvals/my-requests - List user's own requests
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user, get_db
from app.models import User, ApprovalStatus, ApprovalUrgency, ToolApprovalRequest
from app.services.tool_approval_service import (
    ToolApprovalService,
    ApprovalNotFoundError,
    ApprovalExpiredError,
    ApprovalAlreadyReviewedError,
    ApprovalServiceError,
)
from app.services.tool_execution_service import tool_execution_service

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Request/Response Schemas
# =============================================================================

class ApprovalRequestCreate(BaseModel):
    """Schema for creating an approval request."""
    tool_id: UUID
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(..., min_length=5, max_length=2000)
    campaign_id: Optional[UUID] = None
    urgency: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    expected_outcome: Optional[str] = Field(None, max_length=2000)
    risk_assessment: Optional[str] = Field(None, max_length=2000)
    estimated_cost: Optional[float] = Field(None, ge=0)
    expires_in_hours: Optional[float] = Field(None, ge=0.5, le=168)  # 30min to 7 days


class ApprovalReviewRequest(BaseModel):
    """Schema for approving/rejecting a request."""
    notes: Optional[str] = Field(None, max_length=2000)


class ApprovalResponse(BaseModel):
    """Schema for approval request response."""
    id: UUID
    tool_id: UUID
    tool_name: Optional[str] = None
    parameters: Dict[str, Any]
    requested_by_id: UUID
    requested_by_name: Optional[str] = None
    campaign_id: Optional[UUID] = None
    status: str
    urgency: str
    reason: str
    expected_outcome: Optional[str] = None
    risk_assessment: Optional[str] = None
    estimated_cost: Optional[float] = None
    reviewed_by_id: Optional[UUID] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_notes: Optional[str] = None
    execution_id: Optional[UUID] = None
    created_at: str
    expires_at: Optional[str] = None
    
    class Config:
        from_attributes = True


class ApprovalListResponse(BaseModel):
    """Paginated list of approval requests."""
    items: List[ApprovalResponse]
    total: int
    limit: int
    offset: int


class PendingCountResponse(BaseModel):
    """Counts of pending approvals by urgency."""
    critical: int
    high: int
    medium: int
    low: int
    total: int


def approval_to_response(request: ToolApprovalRequest) -> ApprovalResponse:
    """Convert model to response schema."""
    return ApprovalResponse(
        id=request.id,
        tool_id=request.tool_id,
        tool_name=request.tool.name if request.tool else None,
        parameters=request.parameters or {},
        requested_by_id=request.requested_by_id,
        requested_by_name=(
            request.requested_by.display_name or request.requested_by.username
        ) if request.requested_by else None,
        campaign_id=request.campaign_id,
        status=request.status.value,
        urgency=request.urgency.value,
        reason=request.reason,
        expected_outcome=request.expected_outcome,
        risk_assessment=request.risk_assessment,
        estimated_cost=request.estimated_cost,
        reviewed_by_id=request.reviewed_by_id,
        reviewed_by_name=(
            request.reviewed_by.display_name or request.reviewed_by.username
        ) if request.reviewed_by else None,
        reviewed_at=request.reviewed_at.isoformat() if request.reviewed_at else None,
        review_notes=request.review_notes,
        execution_id=request.execution_id,
        created_at=request.created_at.isoformat(),
        expires_at=request.expires_at.isoformat() if request.expires_at else None,
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.post("", response_model=ApprovalResponse, status_code=status.HTTP_201_CREATED)
async def create_approval_request(
    data: ApprovalRequestCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a new approval request for tool execution.
    
    Use this when a tool requires human approval before execution.
    The request will be pending until an admin approves or rejects it.
    """
    service = ToolApprovalService(db)
    
    # Parse urgency
    urgency = None
    if data.urgency:
        try:
            urgency = ApprovalUrgency(data.urgency)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid urgency: {data.urgency}"
            )
    
    # Parse expiry
    expires_in = None
    if data.expires_in_hours:
        expires_in = timedelta(hours=data.expires_in_hours)
    
    try:
        request = await service.create_request(
            tool_id=data.tool_id,
            user_id=current_user.id,
            parameters=data.parameters,
            reason=data.reason,
            campaign_id=data.campaign_id,
            urgency=urgency,
            expected_outcome=data.expected_outcome,
            risk_assessment=data.risk_assessment,
            estimated_cost=data.estimated_cost,
            expires_in=expires_in,
        )
    except ApprovalServiceError as e:
        # GAP-16: Generic error message to prevent information leakage
        logger.warning("Approval request creation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request could not be processed"
        )
    
    # Reload with relations
    request = await service.get_request(request.id)
    return approval_to_response(request)


@router.get("", response_model=ApprovalListResponse)
async def list_pending_approvals(
    urgency: Optional[str] = Query(None, pattern="^(low|medium|high|critical)$"),
    campaign_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    List pending approval requests (admin only).
    
    Returns requests ordered by urgency (critical first) then creation time.
    """
    service = ToolApprovalService(db)
    
    urgency_enum = None
    if urgency:
        try:
            urgency_enum = ApprovalUrgency(urgency)
        except ValueError:
            pass
    
    requests, total = await service.list_pending(
        urgency=urgency_enum,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
    
    return ApprovalListResponse(
        items=[approval_to_response(r) for r in requests],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/pending/count", response_model=PendingCountResponse)
async def get_pending_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Get count of pending approvals by urgency (admin only).
    
    Useful for dashboard badges/notifications.
    """
    service = ToolApprovalService(db)
    counts = await service.get_pending_count()
    return PendingCountResponse(**counts)


@router.get("/my-requests", response_model=ApprovalListResponse)
async def list_my_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List current user's approval requests.
    
    Optionally filter by status (pending, approved, rejected, expired, cancelled).
    """
    service = ToolApprovalService(db)
    
    status_enum = None
    if status_filter:
        try:
            status_enum = ApprovalStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}"
            )
    
    requests, total = await service.list_by_user(
        user_id=current_user.id,
        status=status_enum,
        limit=limit,
        offset=offset,
    )
    
    return ApprovalListResponse(
        items=[approval_to_response(r) for r in requests],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{request_id}", response_model=ApprovalResponse)
async def get_approval_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a specific approval request.
    
    Users can only view their own requests. Admins can view all.
    """
    service = ToolApprovalService(db)
    request = await service.get_request(request_id)
    
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found"
        )
    
    # Check access
    is_admin = current_user.role == "admin" or current_user.is_superuser
    if not is_admin and request.requested_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this request"
        )
    
    return approval_to_response(request)


@router.post("/{request_id}/approve", response_model=ApprovalResponse)
async def approve_request(
    request_id: UUID,
    data: ApprovalReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Approve an execution request (admin only).
    
    After approval, the requester can execute the tool.
    """
    service = ToolApprovalService(db)
    
    try:
        request = await service.approve(
            request_id=request_id,
            reviewer_id=current_user.id,
            notes=data.notes,
        )
    except ApprovalNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found"
        )
    except ApprovalExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Approval request has expired"
        )
    except ApprovalAlreadyReviewedError as e:
        # GAP-16: Generic error message to prevent information leakage
        logger.info("Approve request already reviewed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has already been reviewed"
        )
    
    # Reload with relations
    request = await service.get_request(request_id)
    return approval_to_response(request)


@router.post("/{request_id}/reject", response_model=ApprovalResponse)
async def reject_request(
    request_id: UUID,
    data: ApprovalReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin),
):
    """
    Reject an execution request (admin only).
    
    After rejection, the tool will not be executed.
    """
    service = ToolApprovalService(db)
    
    try:
        request = await service.reject(
            request_id=request_id,
            reviewer_id=current_user.id,
            notes=data.notes,
        )
    except ApprovalNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found"
        )
    except ApprovalExpiredError:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Approval request has expired"
        )
    except ApprovalAlreadyReviewedError as e:
        # GAP-16: Generic error message to prevent information leakage
        logger.info("Reject request already reviewed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has already been reviewed"
        )
    
    # Reload with relations
    request = await service.get_request(request_id)
    return approval_to_response(request)


@router.post("/{request_id}/cancel", response_model=ApprovalResponse)
async def cancel_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cancel a pending approval request (requester only).
    
    Only the original requester can cancel their own request.
    """
    service = ToolApprovalService(db)
    
    try:
        request = await service.cancel(
            request_id=request_id,
            user_id=current_user.id,
        )
    except ApprovalNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found"
        )
    except ApprovalServiceError as e:
        # GAP-16: Generic error message to prevent information leakage
        logger.warning("Cancel request authorization failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel this request"
        )
    except ApprovalAlreadyReviewedError as e:
        # GAP-16: Generic error message to prevent information leakage
        logger.info("Cancel request already reviewed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This request has already been reviewed"
        )
    
    # Reload with relations
    request = await service.get_request(request_id)
    return approval_to_response(request)


class ExecuteApprovedRequest(BaseModel):
    """Schema for executing an approved request."""
    conversation_id: Optional[UUID] = None
    message_id: Optional[UUID] = None


class ExecutionResponse(BaseModel):
    """Response after executing approved tool."""
    execution_id: UUID
    status: str
    output: Optional[Any] = None
    error: Optional[str] = None


@router.post("/{request_id}/execute", response_model=ExecutionResponse)
async def execute_approved_request(
    request_id: UUID,
    data: ExecuteApprovedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Execute a tool after approval has been granted.
    
    This endpoint actually runs the tool using the approved parameters.
    Can only be called by the original requester after admin approval.
    """
    service = ToolApprovalService(db)
    
    # Get the approved request
    request = await service.get_approved_request(request_id)
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approved request not found or already executed"
        )
    
    # Check requester
    if request.requested_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the original requester can execute this request"
        )
    
    # Execute the tool (it will skip approval check since we have approval)
    execution = await tool_execution_service.execute_tool(
        db=db,
        tool_id=request.tool_id,
        params=request.parameters,
        conversation_id=data.conversation_id,
        message_id=data.message_id,
        user_id=current_user.id,
    )
    
    # Link the approval request to the execution
    await service.link_execution(request_id, execution.id)
    
    return ExecutionResponse(
        execution_id=execution.id,
        status=execution.status.value,
        output=execution.output_data,
        error=execution.error_message,
    )
