from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import List

from app.core.database import get_db
from app.core.datetime_utils import utc_now
from app.api.deps import get_current_admin
from app.models import User, UserRole, PasswordResetCode
from app.schemas import UserResponse, ResetCodeResponse
from pydantic import BaseModel


router = APIRouter()


class UserApprovalRequest(BaseModel):
    """Request to approve/reject a user."""
    role: str  # "user" or "admin"


@router.get("/users", response_model=List[UserResponse])
async def list_all_users(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """List all users (admin only)."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return users


@router.get("/users/pending", response_model=List[UserResponse])
async def list_pending_users(
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """List pending users awaiting approval (admin only)."""
    result = await db.execute(
        select(User).where(User.role == UserRole.PENDING.value).order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    return users


@router.post("/users/{user_id}/approve", response_model=UserResponse)
async def approve_user(
    user_id: UUID,
    approval: UserApprovalRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Approve a pending user and assign a role (admin only)."""
    # Validate role
    if approval.role not in [UserRole.USER.value, UserRole.ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be 'user' or 'admin'"
        )
    
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.role != UserRole.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is not pending approval"
        )
    
    # Approve user
    user.role = approval.role
    user.is_active = True
    
    await db.commit()
    await db.refresh(user)
    
    return user


@router.delete("/users/{user_id}")
async def reject_user(
    user_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Reject and delete a pending user (admin only)."""
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.role != UserRole.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only reject pending users. Use delete endpoint for other users."
        )
    
    await db.delete(user)
    await db.commit()
    
    return {"message": "User rejected and deleted"}


@router.delete("/users/{user_id}/delete")
async def delete_user(
    user_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Permanently delete a user account (admin only)."""
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent deleting yourself
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself"
        )
    
    await db.delete(user)
    await db.commit()
    
    return {"message": f"User {user.username} permanently deleted"}


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: UUID,
    approval: UserApprovalRequest,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing user's role (admin only)."""
    # Validate role
    if approval.role not in [UserRole.USER.value, UserRole.ADMIN.value]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be 'user' or 'admin'"
        )
    
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent demoting yourself
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role"
        )
    
    # Update role
    user.role = approval.role
    
    await db.commit()
    await db.refresh(user)
    
    return user


@router.put("/users/{user_id}/deactivate", response_model=UserResponse)
async def deactivate_user(
    user_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Deactivate a user (admin only)."""
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Prevent deactivating yourself
    if user.id == current_admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate yourself"
        )
    
    user.is_active = False
    
    await db.commit()
    await db.refresh(user)
    
    return user


@router.put("/users/{user_id}/reactivate", response_model=UserResponse)
async def reactivate_user(
    user_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Reactivate a deactivated user (admin only)."""
    # Get user
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if user.role == UserRole.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reactivate pending users. Use approve endpoint instead."
        )
    
    user.is_active = True
    
    await db.commit()
    await db.refresh(user)
    
    return user


@router.post("/users/{user_id}/reset-code", response_model=ResetCodeResponse)
async def generate_reset_code(
    user_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Generate a one-time password reset code for a user (admin only).

    The code expires after 1 hour and can only be used once.
    Any previously unused codes for the same user are invalidated.
    """
    import secrets
    from datetime import timedelta

    # Verify target user exists
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Invalidate any existing unused codes for this user
    existing = await db.execute(
        select(PasswordResetCode).where(
            PasswordResetCode.user_id == user_id,
            PasswordResetCode.used_at.is_(None),
        )
    )
    for old_code in existing.scalars().all():
        old_code.used_at = utc_now()  # mark as consumed

    # SA3-L1: Generate a code with adequate entropy (128 bits / 32 hex chars).
    # Previous value was token_hex(4) = 32 bits which is brute-forceable.
    code = secrets.token_hex(16).upper()  # 128-bit entropy
    expires_at = utc_now() + timedelta(hours=1)

    # SA3-M2: Store a SHA-256 hash of the code instead of plaintext.
    # The raw code is returned to the admin but never persisted.
    import hashlib
    code_hash = hashlib.sha256(code.encode()).hexdigest()

    reset_code = PasswordResetCode(
        user_id=user_id,
        code=code_hash,
        expires_at=expires_at,
        created_by_id=current_admin.id,
    )
    db.add(reset_code)
    await db.commit()

    return ResetCodeResponse(code=code, expires_at=expires_at, username=user.username)
