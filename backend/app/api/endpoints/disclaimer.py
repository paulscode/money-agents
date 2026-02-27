"""
Disclaimer API endpoints.

Handles disclaimer acknowledgement flow for all users.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.api.deps import get_current_active_user
from app.models import User
from app.services import disclaimer_service


router = APIRouter()


class DisclaimerStatusResponse(BaseModel):
    """Response for disclaimer status check."""
    requires_disclaimer: bool
    disclaimer_text: str
    is_initial_admin: bool
    agents_enabled: bool
    acknowledged_at: Optional[str] = None
    show_on_login: bool


class AcknowledgeRequest(BaseModel):
    """Request to acknowledge the disclaimer."""
    show_on_login: bool = True


class AcknowledgeResponse(BaseModel):
    """Response after acknowledging the disclaimer."""
    acknowledged: bool
    acknowledged_at: str
    show_on_login: bool
    agents_enabled: bool
    agents_just_enabled: bool


@router.get("/status", response_model=DisclaimerStatusResponse)
async def get_disclaimer_status(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Check if the current user needs to see/acknowledge the disclaimer.
    
    Called by the frontend after login to determine if the disclaimer
    modal should be shown.
    """
    return await disclaimer_service.get_disclaimer_status(db, current_user)


@router.post("/acknowledge", response_model=AcknowledgeResponse)
async def acknowledge_disclaimer(
    request: AcknowledgeRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Acknowledge the disclaimer.
    
    For the initial admin, this also enables all agents and starts
    normal scheduling. This only happens once — subsequent admin
    acknowledgements do not re-disable agents.
    
    The show_on_login flag controls whether the disclaimer appears
    on future logins for this user.
    """
    return await disclaimer_service.acknowledge_disclaimer(
        db, current_user, show_on_login=request.show_on_login
    )
