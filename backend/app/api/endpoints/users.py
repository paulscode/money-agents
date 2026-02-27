from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID
from typing import List

from app.core.database import get_db
from app.api.deps import get_current_active_user
from app.core.rate_limit import limiter
from app.models import User
from app.schemas import UserResponse, UserPublicResponse, UserUpdate


router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: User = Depends(get_current_active_user)
):
    """Get current user info."""
    return current_user


@router.get("/", response_model=List[UserPublicResponse])
async def list_users(
    search: str = Query(None, description="Search by username or display name"),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """List active users (for mentions, etc).
    
    Returns public profiles without email (SA2-08).
    Email search is removed for non-admin users to prevent enumeration.
    """
    query = select(User).where(User.is_active == True, User.role != "pending")
    
    if search:
        search_pattern = f"%{search}%"
        # SA2-08: Only search by username/display_name, never email
        query = query.where(
            (User.username.ilike(search_pattern)) | (User.display_name.ilike(search_pattern))
        )
    
    query = query.order_by(User.username).limit(limit)
    
    result = await db.execute(query)
    users = result.scalars().all()
    
    return users


@router.put("/me", response_model=UserResponse)
@limiter.limit("3/minute")  # SGA3-L5: Reduced from 10/min to prevent password brute-force
async def update_current_user(
    request: Request,
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db)
):
    """Update current user."""
    from app.core.security import get_password_hash, verify_password
    
    # Update fields
    if user_update.email is not None:
        # Check if email is already taken
        result = await db.execute(
            select(User).where(User.email == user_update.email, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            # SGA-L2: Generic error to prevent email/username enumeration
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The requested email or username is not available."
            )
        current_user.email = user_update.email
    
    if user_update.username is not None:
        # Check if username is already taken
        result = await db.execute(
            select(User).where(User.username == user_update.username, User.id != current_user.id)
        )
        if result.scalar_one_or_none():
            # SGA-L2: Generic error to prevent email/username enumeration
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The requested email or username is not available."
            )
        current_user.username = user_update.username
    
    if user_update.password is not None:
        # SA2-05: Require current password verification before changing password
        if not user_update.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is required to set a new password"
            )
        if not verify_password(user_update.current_password, current_user.password_hash):
            # SGA-L2: Generic error to prevent password guess confirmation
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password update failed. Please check your current password."
            )
        current_user.password_hash = get_password_hash(user_update.password)
        # SA2-13: Record password change time for token invalidation
        from app.core.datetime_utils import utc_now
        current_user.password_changed_at = utc_now()
    
    # Profile fields
    if user_update.display_name is not None:
        current_user.display_name = user_update.display_name
    
    if user_update.avatar_url is not None:
        # SA2-19: Validate avatar URL — only allow https:// from known image hosts
        import re
        avatar = user_update.avatar_url.strip()
        if avatar:
            _AVATAR_ALLOWED_HOSTS = {
                "gravatar.com", "www.gravatar.com",
                "avatars.githubusercontent.com", "github.com",
                "i.imgur.com", "imgur.com",
                "cdn.discordapp.com",
                "pbs.twimg.com",
            }
            from urllib.parse import urlparse as _parse_url
            try:
                parsed = _parse_url(avatar)
                if parsed.scheme != "https":
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Avatar URL must use HTTPS"
                    )
                host = (parsed.hostname or "").lower()
                if host not in _AVATAR_ALLOWED_HOSTS:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Avatar URL host not allowed. Supported: {', '.join(sorted(_AVATAR_ALLOWED_HOSTS))}"
                    )
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid avatar URL"
                )
        current_user.avatar_url = avatar or None
    
    await db.commit()
    await db.refresh(current_user)
    
    return current_user


@router.get("/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
):
    """Get user by ID.

    SA3-M1: Returns full UserResponse only when requesting own profile
    or when the caller is an admin. Otherwise returns UserPublicResponse
    (no email field) to prevent user enumeration / data leakage.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # SA3-M1: Only expose email to the user themselves or admins
    if current_user.id != user_id and getattr(current_user, "role", None) != "admin":
        return UserPublicResponse.model_validate(user)
    
    return user
