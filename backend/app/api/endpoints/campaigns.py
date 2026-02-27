from fastapi import APIRouter, Depends, HTTPException, status, Query, WebSocket, WebSocketDisconnect, Request
from app.core.datetime_utils import utc_now, ensure_utc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from uuid import UUID
from typing import Optional, List
from pydantic import BaseModel
import asyncio
import logging

from app.core.database import get_db
from app.core.rate_limit import limiter
from app.api.deps import get_current_active_user
from app.models import User, Campaign, Proposal, UserInputRequest, TaskStream, InputStatus
from app.schemas import CampaignCreate, CampaignUpdate, CampaignResponse
from app.api.websocket_security import ws_receive_validated, WSConnectionGuard


router = APIRouter()
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic models for new endpoints
# =============================================================================

class UserInputProvide(BaseModel):
    """Request body for providing user input."""
    input_key: str
    value: str


class UserInputRequestResponse(BaseModel):
    """Response model for user input requests."""
    id: str
    input_key: str
    input_type: str
    title: str
    description: str
    priority: str
    status: str
    options: Optional[List[str]] = None
    default_value: Optional[str] = None
    blocking_count: int
    suggested_value: Optional[str] = None
    
    class Config:
        from_attributes = True


class StreamStatusResponse(BaseModel):
    """Response model for stream status."""
    id: str
    name: str
    description: Optional[str]
    status: str
    tasks_total: int
    tasks_completed: int
    tasks_failed: int
    tasks_blocked: int
    progress_pct: float
    blocking_reasons: List[str]
    
    class Config:
        from_attributes = True


class CampaignStreamsSummary(BaseModel):
    """Summary of campaign streams and execution status."""
    streams: List[StreamStatusResponse]
    blocking_inputs: List[UserInputRequestResponse]
    total_streams: int
    completed_streams: int
    ready_streams: int
    blocked_streams: int
    total_tasks: int
    completed_tasks: int
    overall_progress_pct: float


@router.post("/", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_campaign(
    request: Request,
    campaign_data: CampaignCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Create a new campaign from an approved proposal."""
    # Verify proposal exists and belongs to user
    result = await db.execute(
        select(Proposal).where(
            Proposal.id == campaign_data.proposal_id,
            Proposal.user_id == current_user.id
        )
    )
    proposal = result.scalar_one_or_none()
    
    if not proposal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proposal not found"
        )
    
    # Check if proposal is approved
    from app.models import ProposalStatus
    if proposal.status != ProposalStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only create campaigns from approved proposals"
        )
    
    campaign = Campaign(
        user_id=current_user.id,
        **campaign_data.model_dump()
    )
    
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    
    return campaign


@router.get("/", response_model=list[CampaignResponse])
@limiter.limit("120/minute")
async def list_campaigns(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List all campaigns for the current user."""
    from sqlalchemy.orm import selectinload
    
    query = select(Campaign).options(
        selectinload(Campaign.proposal)
    ).where(Campaign.user_id == current_user.id)
    
    if status:
        query = query.where(Campaign.status == status)
    
    query = query.order_by(desc(Campaign.created_at)).offset(skip).limit(limit)
    
    result = await db.execute(query)
    campaigns = result.scalars().all()
    
    # Build response with proposal titles
    response = []
    for campaign in campaigns:
        campaign_dict = {
            "id": campaign.id,
            "proposal_id": campaign.proposal_id,
            "proposal_title": campaign.proposal.title if campaign.proposal else None,
            "user_id": campaign.user_id,
            "agent_id": campaign.agent_id,
            "status": campaign.status,
            "budget_allocated": campaign.budget_allocated,
            "budget_spent": campaign.budget_spent,
            "revenue_generated": campaign.revenue_generated,
            "success_metrics": campaign.success_metrics,
            "performance_data": campaign.performance_data,
            "tasks_total": campaign.tasks_total,
            "tasks_completed": campaign.tasks_completed,
            "current_phase": campaign.current_phase,
            "requirements_checklist": campaign.requirements_checklist,
            "all_requirements_met": campaign.all_requirements_met,
            "start_date": campaign.start_date,
            "end_date": campaign.end_date,
            "last_activity_at": campaign.last_activity_at,
            "created_at": campaign.created_at,
            "updated_at": campaign.updated_at,
        }
        response.append(campaign_dict)
    
    return response


@router.get("/{campaign_id}", response_model=CampaignResponse)
@limiter.limit("120/minute")
async def get_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get a specific campaign."""
    from sqlalchemy.orm import selectinload
    
    result = await db.execute(
        select(Campaign).options(
            selectinload(Campaign.proposal)
        ).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Build response with proposal title
    return {
        "id": campaign.id,
        "proposal_id": campaign.proposal_id,
        "proposal_title": campaign.proposal.title if campaign.proposal else None,
        "user_id": campaign.user_id,
        "agent_id": campaign.agent_id,
        "status": campaign.status,
        "budget_allocated": campaign.budget_allocated,
        "budget_spent": campaign.budget_spent,
        "revenue_generated": campaign.revenue_generated,
        "success_metrics": campaign.success_metrics,
        "performance_data": campaign.performance_data,
        "tasks_total": campaign.tasks_total,
        "tasks_completed": campaign.tasks_completed,
        "current_phase": campaign.current_phase,
        "requirements_checklist": campaign.requirements_checklist,
        "all_requirements_met": campaign.all_requirements_met,
        "start_date": campaign.start_date,
        "end_date": campaign.end_date,
        "last_activity_at": campaign.last_activity_at,
        "created_at": campaign.created_at,
        "updated_at": campaign.updated_at,
    }


@router.put("/{campaign_id}", response_model=CampaignResponse)
@limiter.limit("60/minute")
async def update_campaign(
    request: Request,
    campaign_id: UUID,
    campaign_update: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Update a campaign."""
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Update fields
    update_data = campaign_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(campaign, field, value)
    
    # Update last_activity_at
    from datetime import datetime
    campaign.last_activity_at = utc_now()
    
    await db.commit()
    await db.refresh(campaign)
    
    return campaign


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_campaign(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Delete a campaign."""
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    await db.delete(campaign)
    await db.commit()


@router.post("/{campaign_id}/assign-remote", response_model=dict)
@limiter.limit("60/minute")
async def assign_campaign_to_remote_worker(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Assign a campaign to an available remote worker.
    
    This allows distributed campaign execution on remote machines
    that have campaign worker mode enabled.
    """
    from app.services.broker_service import broker_service
    from sqlalchemy.orm import selectinload
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"assign-remote called for campaign {campaign_id}")
    
    # Get campaign with proposal
    result = await db.execute(
        select(Campaign)
        .options(selectinload(Campaign.proposal))
        .where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Check for available workers
    workers = broker_service.get_available_campaign_workers()
    if not workers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No remote campaign workers available"
        )
    
    # Get title/summary from proposal
    proposal_title = campaign.proposal.title if campaign.proposal else "Unknown Campaign"
    proposal_summary = campaign.proposal.summary if campaign.proposal else ""
    
    # Read model tier from agent_definitions (honor user configuration)
    from app.models.agent_scheduler import AgentDefinition
    agent_def_result = await db.execute(
        select(AgentDefinition).where(AgentDefinition.slug == "campaign_manager")
    )
    agent_def = agent_def_result.scalar_one_or_none()
    configured_model_tier = agent_def.default_model_tier if agent_def else "reasoning"
    
    # Build campaign data for worker
    campaign_data = {
        'id': str(campaign.id),
        'status': 'executing',  # Set to executing so worker processes it
        'current_phase': campaign.current_phase or 'executing',
        'proposal_title': proposal_title,
        'proposal_summary': proposal_summary,
        'budget_allocated': float(campaign.budget_allocated),
        'budget_spent': float(campaign.budget_spent),
        'revenue_generated': float(campaign.revenue_generated),
        'tasks_total': campaign.tasks_total,
        'tasks_completed': campaign.tasks_completed,
        'requirements_checklist': campaign.requirements_checklist or [],
        'all_requirements_met': True,  # Set to true so worker will process
        'conversation_history': [],
        'available_tools': [],
        'model_tier': configured_model_tier,
        'max_tokens': 6000,
    }
    
    logger.info(f"Assigning campaign to worker with data: model_tier={campaign_data['model_tier']}, all_requirements_met={campaign_data['all_requirements_met']}")
    
    # Assign to worker
    worker_id = await broker_service.assign_campaign_to_worker(
        db=db,
        campaign_id=campaign_id,
        campaign_data=campaign_data
    )
    
    if not worker_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to assign campaign to remote worker"
        )
    
    # Update campaign to track assignment
    campaign.assigned_worker_id = worker_id
    await db.commit()
    
    return {
        "campaign_id": str(campaign_id),
        "worker_id": worker_id,
        "message": f"Campaign assigned to remote worker {worker_id}"
    }


# =============================================================================
# Stream Execution Endpoints
# =============================================================================

@router.get("/{campaign_id}/streams", response_model=CampaignStreamsSummary)
@limiter.limit("120/minute")
async def get_campaign_streams(
    request: Request,
    campaign_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get streams and execution status for a campaign."""
    from app.services.stream_executor_service import get_stream_execution_summary
    
    # Verify campaign belongs to user
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Get stream execution summary
    summary = await get_stream_execution_summary(db, campaign_id)
    
    # Transform to response model
    streams = [
        StreamStatusResponse(
            id=s["id"],
            name=s["name"],
            description=None,
            status=s["status"],
            tasks_total=s["tasks_total"],
            tasks_completed=s["tasks_completed"],
            tasks_failed=s["tasks_failed"],
            tasks_blocked=s["tasks_blocked"],
            progress_pct=s["progress_pct"],
            blocking_reasons=s["blocking_reasons"]
        )
        for s in summary.get("streams", [])
    ]
    
    blocking_inputs = [
        UserInputRequestResponse(
            id="",  # Not in summary
            input_key=inp["key"],
            input_type=inp["type"],
            title=inp["title"],
            description="",
            priority="blocking",
            status="pending",
            blocking_count=inp["blocking_count"]
        )
        for inp in summary.get("blocking_inputs", [])
    ]
    
    return CampaignStreamsSummary(
        streams=streams,
        blocking_inputs=blocking_inputs,
        total_streams=summary.get("total_streams", 0),
        completed_streams=summary.get("completed_streams", 0),
        ready_streams=summary.get("ready_streams", 0),
        blocked_streams=summary.get("blocked_streams", 0),
        total_tasks=summary.get("total_tasks", 0),
        completed_tasks=summary.get("completed_tasks", 0),
        overall_progress_pct=summary.get("overall_progress_pct", 0)
    )


@router.get("/{campaign_id}/inputs", response_model=List[UserInputRequestResponse])
@limiter.limit("120/minute")
async def get_campaign_inputs(
    request: Request,
    campaign_id: UUID,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get all user input requests for a campaign."""
    # Verify campaign belongs to user
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Query input requests
    query = select(UserInputRequest).where(UserInputRequest.campaign_id == campaign_id)
    
    if status_filter:
        try:
            status_enum = InputStatus(status_filter)
            query = query.where(UserInputRequest.status == status_enum)
        except ValueError:
            pass  # Ignore invalid status
    
    query = query.order_by(UserInputRequest.blocking_count.desc())
    
    result = await db.execute(query)
    requests = result.scalars().all()
    
    return [
        UserInputRequestResponse(
            id=str(req.id),
            input_key=req.input_key,
            input_type=req.input_type.value,
            title=req.title,
            description=req.description,
            priority=req.priority.value,
            status=req.status.value,
            options=req.options,
            default_value=req.default_value,
            blocking_count=req.blocking_count,
            suggested_value=req.suggested_value
        )
        for req in requests
    ]


class TaskTimelineResponse(BaseModel):
    """Response model for task timeline data."""
    id: str
    name: str
    stream_name: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    
    class Config:
        from_attributes = True


@router.get("/{campaign_id}/tasks", response_model=List[TaskTimelineResponse])
@limiter.limit("120/minute")
async def get_campaign_tasks(
    request: Request,
    campaign_id: UUID,
    status_filter: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Get tasks for a campaign with timeline data.
    
    Useful for visualizing task execution timeline and duration.
    """
    from app.models.campaign_stream import CampaignTask, TaskStream
    
    # Verify campaign belongs to user
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Query tasks with stream join for stream name
    query = (
        select(CampaignTask, TaskStream.name.label('stream_name'))
        .join(TaskStream, CampaignTask.stream_id == TaskStream.id)
        .where(CampaignTask.campaign_id == campaign_id)
    )
    
    if status_filter:
        from app.models.campaign_stream import TaskStatus
        try:
            status_enum = TaskStatus(status_filter)
            query = query.where(CampaignTask.status == status_enum)
        except ValueError:
            pass
    
    # Order by started_at for timeline, with nulls last
    query = query.order_by(
        CampaignTask.started_at.asc().nullslast(),
        CampaignTask.order_index.asc()
    ).limit(limit)
    
    result = await db.execute(query)
    rows = result.all()
    
    return [
        TaskTimelineResponse(
            id=str(task.id),
            name=task.name,
            stream_name=stream_name,
            status=task.status.value,
            started_at=task.started_at.isoformat() if task.started_at else None,
            completed_at=task.completed_at.isoformat() if task.completed_at else None,
            duration_ms=task.duration_ms
        )
        for task, stream_name in rows
    ]


@router.post("/{campaign_id}/inputs", response_model=dict)
@limiter.limit("60/minute")
async def provide_campaign_input(
    request: Request,
    campaign_id: UUID,
    input_data: UserInputProvide,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Provide a user input for a campaign.
    
    This will update the input request and recalculate stream readiness.
    Also completes any associated task.
    """
    from app.services.stream_executor_service import provide_user_input
    from app.services.task_generation_service import TaskGenerationService
    from app.services.campaign_progress_service import campaign_progress_service
    
    # Verify campaign belongs to user
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Provide the input
    input_request = await provide_user_input(
        db=db,
        campaign_id=campaign_id,
        input_key=input_data.input_key,
        value=input_data.value,
        user_id=current_user.id
    )
    
    if not input_request:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Input request '{input_data.input_key}' not found or already provided"
        )
    
    # Complete the associated task
    task_gen_service = TaskGenerationService(db)
    await task_gen_service.complete_task_for_source(
        user_id=current_user.id,
        source_type="campaign_input",
        source_id=input_request.id,
        completion_notes=f"Input provided: {input_data.value[:100]}..." if len(input_data.value) > 100 else f"Input provided: {input_data.value}",
    )
    
    await db.commit()
    
    # Emit WebSocket event for real-time updates
    await campaign_progress_service.emit_input_provided(
        campaign_id=campaign_id,
        input_key=input_data.input_key,
        unblocked_tasks=input_request.blocking_count if hasattr(input_request, 'blocking_count') else 0
    )
    
    return {
        "success": True,
        "message": f"Input '{input_data.input_key}' provided successfully",
        "input_key": input_data.input_key
    }


@router.post("/{campaign_id}/inputs/bulk", response_model=dict)
@limiter.limit("60/minute")
async def provide_multiple_campaign_inputs(
    request: Request,
    campaign_id: UUID,
    inputs: List[UserInputProvide],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """
    Provide multiple user inputs for a campaign at once.
    
    This is more efficient than calling the single input endpoint multiple times.
    """
    from app.services.stream_executor_service import provide_user_input
    
    # Verify campaign belongs to user
    result = await db.execute(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.user_id == current_user.id
        )
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found"
        )
    
    # Provide all inputs
    results = []
    for input_data in inputs:
        success = await provide_user_input(
            db=db,
            campaign_id=campaign_id,
            input_key=input_data.input_key,
            value=input_data.value,
            user_id=current_user.id
        )
        results.append({
            "input_key": input_data.input_key,
            "success": success
        })
    
    await db.commit()
    
    successful = sum(1 for r in results if r["success"])
    
    return {
        "total": len(inputs),
        "successful": successful,
        "failed": len(inputs) - successful,
        "results": results
    }


# =============================================================================
# WebSocket Endpoints
# =============================================================================

async def authenticate_campaign_websocket(websocket: WebSocket) -> Optional[User]:
    """
    Authenticate a WebSocket connection using first-message auth (SA2-10).
    
    Supports:
    - First-message auth: {"type": "auth", "token": "<jwt>"}
    
    Query-param auth removed to prevent token leakage via logs/history.
    Returns the authenticated User or None if authentication fails.
    """
    from app.core.security import decode_access_token
    from app.core.database import get_db_context
    
    token = None
    
    # First-message auth only (SA2-10)
    try:
        import json as _json
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        msg = _json.loads(raw)
        if isinstance(msg, dict) and msg.get("type") == "auth":
            token = msg.get("token")
    except Exception:
        pass
    
    if not token:
        return None
    
    try:
        payload = decode_access_token(token)
        if not payload:
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        
        async with get_db_context() as db:
            result = await db.execute(
                select(User).where(User.id == UUID(user_id))
            )
            user = result.scalar_one_or_none()
            
            if user and user.is_active:
                return user
            
            return None
    except Exception as e:
        logger.warning(f"WebSocket auth failed: {e}")
        return None


@router.websocket("/{campaign_id}/progress")
async def websocket_campaign_progress(
    websocket: WebSocket,
    campaign_id: UUID
):
    """
    WebSocket endpoint for real-time campaign progress updates.
    
    Protocol:
    1. Connect to WebSocket
    2. Send auth message: {"type": "auth", "token": "<access_token>"}
    3. Receive auth response: {"type": "auth_result", "success": true/false}
    4. Receive initial state: {"type": "initial_state", "data": {...}}
    5. Receive progress updates:
       - {"type": "campaign_status", "data": {...}}
       - {"type": "stream_progress", "data": {...}}
       - {"type": "task_completed", "data": {...}}
       - {"type": "task_failed", "data": {...}}
       - {"type": "input_required", "data": {...}}
       - {"type": "input_provided", "data": {...}}
       - {"type": "overall_progress", "data": {...}}
    6. Send ping: {"type": "ping"} -> receive {"type": "pong"}
    
    Connection closes when:
    - Client disconnects
    - Authentication fails
    - Campaign doesn't exist or user doesn't have access
    """
    from app.services.campaign_progress_service import campaign_progress_service
    from app.services.stream_executor_service import get_stream_execution_summary
    from app.core.database import get_db_context
    
    await websocket.accept()
    
    try:
        # Authenticate
        user = await authenticate_campaign_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return
        
        # Verify campaign access
        async with get_db_context() as db:
            result = await db.execute(
                select(Campaign).where(
                    Campaign.id == campaign_id,
                    Campaign.user_id == user.id
                )
            )
            campaign = result.scalar_one_or_none()
            
            if not campaign:
                await websocket.send_json({
                    "type": "auth_result",
                    "success": False,
                    "error": "Campaign not found or access denied",
                })
                await websocket.close(code=4003, reason="Campaign not found")
                return
            
            # Send auth success
            await websocket.send_json({
                "type": "auth_result",
                "success": True,
                "user_id": str(user.id),
                "campaign_id": str(campaign_id),
            })
            
            # Subscribe to campaign updates
            await campaign_progress_service.subscribe(websocket, user.id, campaign_id)
            
            # Send initial state
            summary = await get_stream_execution_summary(db, campaign_id)
            
            await websocket.send_json({
                "type": "initial_state",
                "data": {
                    "campaign_id": str(campaign_id),
                    "status": campaign.status.value if hasattr(campaign.status, 'value') else str(campaign.status),
                    "budget_allocated": float(campaign.budget_allocated),
                    "budget_spent": float(campaign.budget_spent),
                    "revenue_generated": float(campaign.revenue_generated),
                    "tasks_total": campaign.tasks_total,
                    "tasks_completed": campaign.tasks_completed,
                    "current_phase": campaign.current_phase,
                    "streams": summary.get("streams", []),
                    "blocking_inputs": summary.get("blocking_inputs", []),
                    "total_streams": summary.get("total_streams", 0),
                    "completed_streams": summary.get("completed_streams", 0),
                    "overall_progress_pct": summary.get("overall_progress_pct", 0),
                }
            })
        
        logger.info(f"User {user.id} connected to campaign {campaign_id} progress WebSocket")
        
        # SGA-M2/L3: Use WSConnectionGuard and ws_receive_validated
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return

            rate_state: dict = {}
            # Handle ping/pong to keep connection alive
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") in ("_oversized", "_rate_limited"):
                        continue
                    msg_type = data.get("type")
                    
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                    else:
                        # Client shouldn't send other messages, but handle gracefully
                        await websocket.send_json({
                            "type": "error",
                            "error": f"Unknown message type: {msg_type}. Only 'ping' is supported.",
                        })
                        
                except WebSocketDisconnect:
                    logger.info(f"User {user.id} disconnected from campaign {campaign_id}")
                    break
                except Exception as e:
                    logger.warning(f"Error handling WebSocket message: {e}")
                    break
    
    except WebSocketDisconnect:
        logger.info("Campaign progress WebSocket disconnected during setup")
    except Exception as e:
        logger.exception(f"Campaign progress WebSocket error: {e}")
        try:
            # GAP-16: Don't leak internal error details to clients
            await websocket.send_json({
                "type": "error",
                "error": "An internal error occurred",
            })
        except Exception:
            pass
    finally:
        # Clean up subscription
        await campaign_progress_service.unsubscribe(websocket)
