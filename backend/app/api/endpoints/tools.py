"""Tools API endpoints."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_current_admin, get_db
from app.core.rate_limit import limiter
from app.models import Tool, User, ToolStatus, ToolCategory
from app.schemas import (
    ToolCreate,
    ToolUpdate,
    ToolResponse,
    AssignToolRequest,
    UpdateToolStatusRequest
)

router = APIRouter()


@router.get("/", response_model=list[ToolResponse])
async def list_tools(
    status: Optional[ToolStatus] = None,
    category: Optional[ToolCategory] = None,
    search: Optional[str] = None,
    assigned_to_me: Optional[bool] = False,
    requested_by_me: Optional[bool] = False,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> list[ToolResponse]:
    """
    List tools with optional filtering.
    """
    query = select(Tool).options(
        selectinload(Tool.requester),
        selectinload(Tool.assigned_to_user)
    )
    
    # Apply filters
    filters = []
    if status:
        filters.append(Tool.status == status)
    if category:
        filters.append(Tool.category == category)
    if search:
        search_pattern = f"%{search}%"
        filters.append(
            or_(
                Tool.name.ilike(search_pattern),
                Tool.description.ilike(search_pattern),
                Tool.tags.astext.ilike(search_pattern)
            )
        )
    if assigned_to_me:
        filters.append(Tool.assigned_to_id == current_user.id)
    if requested_by_me:
        filters.append(Tool.requester_id == current_user.id)
    
    if filters:
        query = query.where(and_(*filters))
    
    # Order by created_at descending
    query = query.order_by(Tool.created_at.desc()).offset(skip).limit(limit)
    
    result = await db.execute(query)
    tools = result.scalars().all()
    
    # Convert to response models and add usernames
    response_tools = []
    for tool in tools:
        tool_dict = {
            **tool.__dict__,
            "requester_username": tool.requester.username if tool.requester else None,
            "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
            "unread_count": 0  # TODO: Implement when conversations are linked
        }
        response_tools.append(ToolResponse.model_validate(tool_dict))
    
    return response_tools


@router.get("/available", response_model=list[ToolResponse])
async def list_available_tools(
    category: Optional[ToolCategory] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> list[ToolResponse]:
    """
    List only implemented and active tools (for agents).
    """
    query = select(Tool).options(
        selectinload(Tool.requester),
        selectinload(Tool.assigned_to_user)
    ).where(Tool.status == ToolStatus.IMPLEMENTED)
    
    if category:
        query = query.where(Tool.category == category)
    
    query = query.order_by(Tool.name)
    
    result = await db.execute(query)
    tools = result.scalars().all()
    
    # Convert to response models
    response_tools = []
    for tool in tools:
        tool_dict = {
            **tool.__dict__,
            "requester_username": tool.requester.username if tool.requester else None,
            "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
            "unread_count": 0
        }
        response_tools.append(ToolResponse.model_validate(tool_dict))
    
    return response_tools


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ToolResponse:
    """
    Get a specific tool by ID.
    """
    query = select(Tool).options(
        selectinload(Tool.requester),
        selectinload(Tool.assigned_to_user)
    ).where(Tool.id == tool_id)
    
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.post("/", response_model=ToolResponse)
async def create_tool(
    tool_data: ToolCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ToolResponse:
    """
    Create a new tool request.
    """
    # Check if tool with same name or slug already exists
    existing_query = select(Tool).where(
        or_(Tool.name == tool_data.name, Tool.slug == tool_data.slug)
    )
    result = await db.execute(existing_query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="Tool with this name or slug already exists"
        )
    
    # Create new tool
    now = utc_now()
    tool = Tool(
        id=uuid4(),
        **tool_data.model_dump(),
        status=ToolStatus.REQUESTED,
        requester_id=current_user.id,
        requested_at=now,
        created_at=now,
        updated_at=now
    )
    
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    
    # Load relationships
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    tool_data: ToolUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ToolResponse:
    """
    Update a tool. Only requester, assigned user, or admin can update.
    """
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check permissions
    is_admin = current_user.role == "admin"
    is_requester = tool.requester_id == current_user.id
    is_assigned = tool.assigned_to_id == current_user.id
    
    if not (is_admin or is_requester or is_assigned):
        raise HTTPException(
            status_code=403,
            detail="Not authorized to update this tool"
        )
    
    # Update fields
    update_data = tool_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tool, field, value)
    
    tool.updated_at = utc_now()
    
    await db.commit()
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.post("/{tool_id}/approve", response_model=ToolResponse)
async def approve_tool(
    tool_id: UUID,
    assign_request: Optional[AssignToolRequest] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ToolResponse:
    """
    Approve a tool request (admin only).
    Auto-assigns to approver if no assignee specified.
    """
    
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    if tool.status != ToolStatus.REQUESTED and tool.status != ToolStatus.UNDER_REVIEW and tool.status != ToolStatus.CHANGES_REQUESTED:
        raise HTTPException(
            status_code=400,
            detail="Only requested/under_review/changes_requested tools can be approved"
        )
    
    # Update status
    now = utc_now()
    tool.status = ToolStatus.APPROVED
    tool.approved_at = now
    tool.approved_by_id = current_user.id
    
    # Auto-assign to approver if no assignee specified
    if assign_request and assign_request.user_id:
        tool.assigned_to_id = assign_request.user_id
    else:
        tool.assigned_to_id = current_user.id
    
    tool.updated_at = now
    
    await db.commit()
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.post("/{tool_id}/reject", response_model=ToolResponse)
async def reject_tool(
    tool_id: UUID,
    notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ToolResponse:
    """
    Reject a tool request (admin only).
    """
    
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    tool.status = ToolStatus.REJECTED
    if notes:
        tool.implementation_notes = notes
    tool.updated_at = utc_now()
    
    await db.commit()
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.put("/{tool_id}/assign", response_model=ToolResponse)
async def assign_tool(
    tool_id: UUID,
    assign_request: AssignToolRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ToolResponse:
    """
    Assign or reassign a tool to another user.
    Only assignee or admin can reassign.
    """
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check permissions
    is_admin = current_user.role == "admin"
    is_assigned = tool.assigned_to_id == current_user.id
    
    if not (is_admin or is_assigned):
        raise HTTPException(
            status_code=403,
            detail="Only the assigned user or admin can reassign this tool"
        )
    
    # Verify new assignee exists
    user_query = select(User).where(User.id == assign_request.user_id)
    user_result = await db.execute(user_query)
    new_assignee = user_result.scalar_one_or_none()
    
    if not new_assignee:
        raise HTTPException(status_code=404, detail="User not found")
    
    tool.assigned_to_id = assign_request.user_id
    tool.updated_at = utc_now()
    
    await db.commit()
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.put("/{tool_id}/status", response_model=ToolResponse)
async def update_tool_status(
    tool_id: UUID,
    status_request: UpdateToolStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> ToolResponse:
    """
    Update tool implementation status.
    Only assigned user or admin can update status.
    """
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check permissions
    is_admin = current_user.role == "admin"
    is_assigned = tool.assigned_to_id == current_user.id
    
    if not (is_admin or is_assigned):
        raise HTTPException(
            status_code=403,
            detail="Only the assigned user or admin can update status"
        )
    
    tool.status = status_request.status
    
    # Set implemented_at when status changes to implemented
    if status_request.status == ToolStatus.IMPLEMENTED and not tool.implemented_at:
        tool.implemented_at = utc_now()
    
    # Update notes if provided
    if status_request.notes:
        if tool.implementation_notes:
            tool.implementation_notes += f"\n\n{utc_now().isoformat()}: {status_request.notes}"
        else:
            tool.implementation_notes = f"{utc_now().isoformat()}: {status_request.notes}"
    
    tool.updated_at = utc_now()
    
    await db.commit()
    await db.refresh(tool, ["requester", "assigned_to_user"])
    
    tool_dict = {
        **tool.__dict__,
        "requester_username": tool.requester.username if tool.requester else None,
        "assigned_to_username": tool.assigned_to_user.username if tool.assigned_to_user else None,
        "unread_count": 0
    }
    
    return ToolResponse.model_validate(tool_dict)


@router.delete("/{tool_id}", status_code=204)
async def delete_tool(
    tool_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a tool.
    Only admin or the requester can delete a tool.
    """
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check permissions
    is_admin = current_user.role == "admin"
    is_requester = tool.requester_id == current_user.id
    
    if not (is_admin or is_requester):
        raise HTTPException(
            status_code=403,
            detail="Only the requester or admin can delete this tool"
        )
    
    await db.delete(tool)
    await db.commit()


# -------------------------------------------------------------------------
# Tool Execution
# -------------------------------------------------------------------------

from pydantic import BaseModel, Field
from app.services.tool_execution_service import tool_execution_service
from app.models import ToolExecution, ToolExecutionStatus


class ToolExecuteRequest(BaseModel):
    """Request to execute a tool."""
    params: dict = Field(default_factory=dict, description="Tool-specific parameters")
    conversation_id: Optional[UUID] = Field(None, description="Optional conversation context")
    queue_timeout: Optional[int] = Field(None, description="Max seconds to wait in queue (default 30)")
    wait_for_resource: bool = Field(True, description="If False, return immediately when resource busy")


class ToolExecutionResponse(BaseModel):
    """Response from tool execution."""
    id: UUID
    tool_id: UUID
    tool_name: str
    status: str
    success: bool
    output: Optional[dict] = None
    error: Optional[str] = None
    duration_ms: Optional[int] = None
    cost_units: Optional[int] = None
    # Queue info for resource-dependent tools
    job_id: Optional[UUID] = None  # For checking status when wait_for_resource=False
    queue_position: Optional[int] = None  # Position when queued
    
    class Config:
        from_attributes = True


@router.post("/{tool_id}/execute", response_model=ToolExecutionResponse)
@limiter.limit("20/minute")
async def execute_tool(
    tool_id: UUID,
    request: Request,
    body: ToolExecuteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Execute a tool with given parameters.
    
    Only implemented tools can be executed.
    """
    # Get the tool
    query = select(Tool).where(Tool.id == tool_id)
    result = await db.execute(query)
    tool = result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    if tool.status != ToolStatus.IMPLEMENTED:
        raise HTTPException(
            status_code=400,
            detail=f"Tool is not implemented (status: {tool.status.value})"
        )
    
    # Execute the tool
    try:
        execution = await tool_execution_service.execute_tool(
            db=db,
            tool_id=tool_id,
            params=body.params,
            conversation_id=body.conversation_id,
            user_id=current_user.id,
            queue_timeout=body.queue_timeout,
            wait_for_resource=body.wait_for_resource,
        )
        
        # Extract job_id and queue_position from error message if queued/busy
        job_id = None
        queue_position = None
        error_msg = execution.error_message or ""
        if "RESOURCE_BUSY:" in error_msg or "QUEUE_TIMEOUT:" in error_msg:
            # Parse job ID if present: "... Job ID: <uuid>"
            import re
            job_match = re.search(r'Job ID: ([a-f0-9-]+)', error_msg)
            if job_match:
                job_id = UUID(job_match.group(1))
            # Parse position if present: "... position X"
            pos_match = re.search(r'position (\d+)', error_msg)
            if pos_match:
                queue_position = int(pos_match.group(1))
        
        return ToolExecutionResponse(
            id=execution.id,
            tool_id=execution.tool_id,
            tool_name=tool.name,
            status=execution.status.value,
            success=execution.status == ToolExecutionStatus.COMPLETED,
            output=execution.output_result,
            error=execution.error_message,
            duration_ms=execution.duration_ms,
            cost_units=execution.cost_units,
            job_id=job_id,
            queue_position=queue_position,
        )
    except ValueError as e:
        import logging
        logging.getLogger(__name__).error("Tool execution parameter error: %s", e)
        raise HTTPException(status_code=400, detail="Invalid tool execution parameters")
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Tool execution failed")
        raise HTTPException(status_code=500, detail="Tool execution failed due to an internal error")


@router.get("/{tool_id}/executions", response_model=list[ToolExecutionResponse])
async def list_tool_executions(
    tool_id: UUID,
    limit: int = Query(default=20, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List recent executions of a tool.
    
    Admin sees all, regular users see their own executions.
    """
    # Verify tool exists
    tool_query = select(Tool).where(Tool.id == tool_id)
    tool_result = await db.execute(tool_query)
    tool = tool_result.scalar_one_or_none()
    
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Build query
    query = select(ToolExecution).where(ToolExecution.tool_id == tool_id)
    
    # Non-admins only see their own executions
    if current_user.role != "admin":
        query = query.where(ToolExecution.triggered_by_user_id == current_user.id)
    
    query = query.order_by(ToolExecution.created_at.desc()).limit(limit)
    
    result = await db.execute(query)
    executions = result.scalars().all()
    
    return [
        ToolExecutionResponse(
            id=e.id,
            tool_id=e.tool_id,
            tool_name=tool.name,
            status=e.status.value,
            success=e.status == ToolExecutionStatus.COMPLETED,
            output=e.output_result,
            error=e.error_message,
            duration_ms=e.duration_ms,
            cost_units=e.cost_units,
        )
        for e in executions
    ]
