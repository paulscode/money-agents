from datetime import timedelta
from pydantic import BaseModel
from app.core.datetime_utils import utc_now, ensure_utc
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.security import verify_password, create_access_token, get_password_hash, decode_access_token, revoke_token, create_refresh_token, decode_refresh_token
from app.core.config import settings
from app.core.rate_limit import limiter
from app.api.deps import get_current_user
from app.models import User, UserRole, PasswordResetCode
from app.schemas import UserCreate, UserResponse, Token, LoginRequest, ResetPasswordRequest

import os

router = APIRouter()


@router.get("/platform")
@limiter.limit("10/minute")
async def get_platform(request: Request):
    """Return the host operating system. Used by the reset-password page
    to display the correct start script command.
    
    No auth required because this endpoint is called from the
    reset-password page *before* the user can authenticate.
    Rate-limited to prevent enumeration (GAP-12).
    """
    host_os = os.environ.get("HOST_OS", "linux")
    return {"host_os": host_os}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """Register a new user."""
    from sqlalchemy import or_

    # Check if email or username already exists (single query to prevent timing oracle)
    result = await db.execute(
        select(User).where(
            or_(
                User.email == user_data.email,
                User.username == user_data.username,
            )
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed. Please check your details and try again."
        )
    
    # Create new user with pending role (requires admin approval)
    user = User(
        email=user_data.email,
        username=user_data.username,
        password_hash=get_password_hash(user_data.password),
        role=UserRole.PENDING.value,
        is_active=False,  # Inactive until approved by admin
        is_superuser=False,
    )
    
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    return user


@router.post("/login", response_model=Token)
@limiter.limit("10/minute")
async def login(
    request: Request,
    login_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """Login and get access token. Accepts email or username."""
    from sqlalchemy import or_
    
    # Generic error message to avoid user enumeration
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email/username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # Get user by email or username
    identifier = login_data.identifier.strip()
    result = await db.execute(
        select(User).where(
            or_(
                User.email == identifier,
                User.username == identifier
            )
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        # SGA-L1: Consume bcrypt time even when user not found (timing oracle prevention)
        verify_password("dummy_password", "$2b$12$LJ3m4ys3FBKx7v7JMtIb6ebDPIVCLJH90n6rIp7E1FtDa17BGwEKq")
        raise credentials_error

    # SGA3-H1: Block system service account from interactive login
    if user.email == "system@money-agents.dev":
        verify_password("dummy_password", "$2b$12$LJ3m4ys3FBKx7v7JMtIb6ebDPIVCLJH90n6rIp7E1FtDa17BGwEKq")
        raise credentials_error
    
    # SA2-09: Check per-account lockout before attempting password verification
    # SGA-L1: Always call verify_password for constant-time response (timing oracle prevention)
    if user.locked_until is not None and user.locked_until > utc_now():
        verify_password(login_data.password, user.password_hash)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Account temporarily locked due to too many failed login attempts. Try again later.",
        )
    
    if not verify_password(login_data.password, user.password_hash):
        # SA2-09: Increment failed login counter and apply progressive lockout
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        attempts = user.failed_login_attempts
        if attempts >= 10:
            # 1 hour lockout after 10+ failures
            user.locked_until = utc_now() + timedelta(hours=1)
        elif attempts >= 7:
            # 15 min lockout after 7-9 failures
            user.locked_until = utc_now() + timedelta(minutes=15)
        elif attempts >= 5:
            # 5 min lockout after 5-6 failures
            user.locked_until = utc_now() + timedelta(minutes=5)
        await db.commit()
        raise credentials_error
    
    # Check if account is not yet active (pending/deactivated → same generic error)
    if user.role == UserRole.PENDING.value:
        raise credentials_error
    
    # Check if account is not active
    if not user.is_active:
        raise credentials_error
    
    # SA2-09: Reset failed login counter on successful login
    if user.failed_login_attempts > 0:
        user.failed_login_attempts = 0
        user.locked_until = None
    
    # Update last login
    from datetime import datetime
    user.last_login = utc_now()
    await db.commit()
    
    # Create access token
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires
    )
    
    # SGA3-L5: Issue refresh token for session renewal
    refresh_token = create_refresh_token(data={"sub": str(user.id)})
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": refresh_token,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Logout by revoking the current access token.

    GAP-22: Uses Depends(get_current_user) to ensure the user is active
    and not deactivated before revoking the token, matching the auth
    pattern used by all other authenticated endpoints.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    token = auth_header[len("Bearer "):]
    payload = decode_access_token(token)
    if not payload or "jti" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    revoke_token(payload["jti"])
    return None


@router.post("/reset-password", status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    reset_data: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using an admin-generated reset code.

    The code must be valid (not expired, not already used). On success the
    user's password is updated and the code is marked as consumed.
    """
    # Look up the reset code
    # SA3-M2: Hash the submitted code before lookup — codes are stored as
    # SHA-256 digests, never in plaintext.
    import hashlib
    code_hash = hashlib.sha256(reset_data.code.strip().encode()).hexdigest()
    result = await db.execute(
        select(PasswordResetCode).where(PasswordResetCode.code == code_hash)
    )
    reset_code = result.scalar_one_or_none()

    # Use a generic error message to avoid leaking whether a code exists
    invalid_msg = "Invalid or expired reset code."

    if not reset_code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_msg)

    if reset_code.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_msg)

    if reset_code.expires_at < utc_now():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_msg)

    # Fetch the target user
    user_result = await db.execute(select(User).where(User.id == reset_code.user_id))
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=invalid_msg)

    # Update password and mark code as used
    user.password_hash = get_password_hash(reset_data.new_password)
    user.password_changed_at = utc_now()  # SA2-13: invalidate existing tokens
    reset_code.used_at = utc_now()

    await db.commit()

    return {"message": "Password has been reset successfully."}


class RefreshTokenRequest(BaseModel):
    """Request body for token refresh."""
    refresh_token: str


@router.post("/refresh", response_model=Token)
@limiter.limit("30/minute")
async def refresh_access_token(
    request: Request,
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a valid refresh token for a new access + refresh token pair.

    SGA3-L5: Implements token rotation — each refresh invalidates the
    previous refresh token and issues a new pair.
    """
    payload = decode_refresh_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Verify user still exists and is active
    from uuid import UUID as _UUID
    result = await db.execute(select(User).where(User.id == _UUID(user_id)))
    user = result.scalar_one_or_none()

    if not user or not user.is_active or user.email == "system@money-agents.dev":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    # Check if password was changed after the refresh token was issued
    # (SA2-13 token invalidation)
    token_iat = payload.get("iat")
    if token_iat and user.password_changed_at:
        from datetime import datetime, timezone
        iat_dt = datetime.fromtimestamp(token_iat, tz=timezone.utc)
        pwd_changed = ensure_utc(user.password_changed_at)
        if iat_dt < pwd_changed:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token invalidated by password change",
            )

    # Revoke the old refresh token (rotation)
    old_jti = payload.get("jti")
    if old_jti:
        revoke_token(old_jti, expires_in=7 * 24 * 3600)

    # Issue new pair
    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    new_access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )
    new_refresh_token = create_refresh_token(data={"sub": str(user.id)})

    return {
        "access_token": new_access_token,
        "token_type": "bearer",
        "refresh_token": new_refresh_token,
    }
