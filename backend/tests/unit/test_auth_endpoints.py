"""Tests for auth endpoints: registration, login, and logout.

Validates:
- User registration with pending approval flow
- Login with email or username
- Rate limiting (structure, not enforcement)
- Password validation at the schema level
- Pending/inactive account rejection
- Logout with token revocation
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from app.core.security import get_password_hash, create_access_token, revoke_token, is_token_revoked, decode_access_token
from app.models import User, UserRole
from app.schemas import UserCreate, LoginRequest


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """Disable slowapi rate limiter during tests to prevent 429 responses."""
    from app.core.rate_limit import limiter
    limiter.enabled = False
    yield
    limiter.enabled = True


# =========================================================================
# Schema Validation Tests
# =========================================================================

class TestUserCreateSchema:
    """Tests for UserCreate schema password complexity validation."""

    def test_valid_password(self):
        user = UserCreate(email="a@b.com", username="testuser", password="Str0ng!Pass")
        assert user.password == "Str0ng!Pass"

    def test_password_missing_uppercase(self):
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="str0ng!pass")

    def test_password_missing_lowercase(self):
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="STR0NG!PASS")

    def test_password_missing_digit(self):
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="Strong!Pass")

    def test_password_missing_special_char(self):
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="Str0ngPass1")

    def test_password_too_short(self):
        with pytest.raises(Exception):
            UserCreate(email="a@b.com", username="testuser", password="Ab1!")


# =========================================================================
# Registration Tests (via async_client integration)
# =========================================================================

class TestRegistration:
    """Tests for POST /api/v1/auth/register."""

    @pytest.mark.asyncio
    async def test_register_creates_pending_user(self, async_client, db_session):
        """New registrations should create inactive, pending-role users."""
        response = await async_client.post("/api/v1/auth/register", json={
            "email": "newuser@example.com",
            "username": "newuser",
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "newuser"
        assert data["role"] == UserRole.PENDING.value
        assert data["is_active"] is False

    @pytest.mark.asyncio
    async def test_register_duplicate_email_fails(self, async_client, db_session, test_user):
        """Emails must be unique."""
        response = await async_client.post("/api/v1/auth/register", json={
            "email": test_user.email,
            "username": "different",
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 400
        assert "Registration failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_duplicate_username_fails(self, async_client, db_session, test_user):
        """Usernames must be unique."""
        response = await async_client.post("/api/v1/auth/register", json={
            "email": "different@example.com",
            "username": test_user.username,
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 400
        assert "Registration failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_register_invalid_password_rejected(self, async_client, db_session):
        """Weak passwords should be rejected at validation level."""
        response = await async_client.post("/api/v1/auth/register", json={
            "email": "weak@example.com",
            "username": "weakuser",
            "password": "weak",
        })
        assert response.status_code == 422  # Pydantic validation error


# =========================================================================
# Login Tests
# =========================================================================

class TestLogin:
    """Tests for POST /api/v1/auth/login."""

    @pytest.mark.asyncio
    async def test_login_with_email(self, async_client, db_session, test_user):
        """Active users can login with email."""
        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": test_user.email,
            "password": "testpassword123",
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_login_with_username(self, async_client, db_session, test_user):
        """Active users can login with username."""
        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": test_user.username,
            "password": "testpassword123",
        })
        assert response.status_code == 200
        assert "access_token" in response.json()

    @pytest.mark.asyncio
    async def test_login_wrong_password(self, async_client, db_session, test_user):
        """Wrong password returns 401."""
        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": test_user.email,
            "password": "WrongPassword1!",
        })
        assert response.status_code == 401
        assert "Incorrect" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, async_client, db_session):
        """Non-existent user returns 401 (same as wrong password for security)."""
        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": "noone@nowhere.com",
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_login_pending_user_rejected(self, async_client, db_session):
        """Pending users cannot login."""
        from app.core.security import get_password_hash
        pending_user = User(
            username="pendinguser",
            email="pending@example.com",
            password_hash=get_password_hash("Str0ng!Pass"),
            role=UserRole.PENDING.value,
            is_active=False,
        )
        db_session.add(pending_user)
        await db_session.commit()

        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": "pending@example.com",
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 401
        assert "Incorrect" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_inactive_user_rejected(self, async_client, db_session):
        """Deactivated users cannot login."""
        from app.core.security import get_password_hash
        inactive_user = User(
            username="inactiveuser",
            email="inactive@example.com",
            password_hash=get_password_hash("Str0ng!Pass"),
            role=UserRole.USER.value,
            is_active=False,
        )
        db_session.add(inactive_user)
        await db_session.commit()

        response = await async_client.post("/api/v1/auth/login", json={
            "identifier": "inactive@example.com",
            "password": "Str0ng!Pass",
        })
        assert response.status_code == 401
        assert "Incorrect" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_login_updates_last_login(self, async_client, db_session, test_user):
        """Successful login updates user.last_login."""
        assert test_user.last_login is None
        await async_client.post("/api/v1/auth/login", json={
            "identifier": test_user.email,
            "password": "testpassword123",
        })
        await db_session.refresh(test_user)
        assert test_user.last_login is not None


# =========================================================================
# Logout / Token Revocation Tests
# =========================================================================

class TestLogout:
    """Tests for POST /api/v1/auth/logout and token revocation."""

    def test_revoke_and_check_token(self):
        """Revoking a JTI marks it as revoked."""
        jti = f"test-jti-{uuid4().hex}"
        assert not is_token_revoked(jti)
        revoke_token(jti)
        assert is_token_revoked(jti)

    def test_decode_revoked_token_returns_none(self):
        """A token with a revoked JTI should decode to None."""
        token = create_access_token(data={"sub": str(uuid4())})
        payload = decode_access_token(token)
        assert payload is not None
        jti = payload["jti"]
        revoke_token(jti)
        assert decode_access_token(token) is None

    @pytest.mark.asyncio
    async def test_logout_without_bearer(self, async_client):
        """Logout without a Bearer token returns 401 or 403."""
        response = await async_client.post("/api/v1/auth/logout")
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_logout_with_valid_token(self, async_client, db_session, test_user):
        """Logout with a valid token returns 204 and revokes the token."""
        token = create_access_token(data={"sub": str(test_user.id)})
        response = await async_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204
        # Token should now be revoked
        assert decode_access_token(token) is None
