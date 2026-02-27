"""Tests for rate limiting service and integration."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ToolRateLimit,
    RateLimitViolation,
    ToolExecution,
    ToolExecutionStatus,
    RateLimitScope,
    RateLimitPeriod,
    Tool,
    User,
)
from app.services.rate_limit_service import RateLimitService, RateLimitStatus


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock(spec=AsyncSession)


@pytest.fixture
def mock_tool():
    """Create a mock tool."""
    tool = Tool(
        id=uuid4(),
        name="Test Tool",
        slug="test-tool",
        description="A test tool",
    )
    return tool


@pytest.fixture
def mock_user():
    """Create a mock user."""
    user = User(
        id=uuid4(),
        email="test@example.com",
        username="testuser",
    )
    return user


# =============================================================================
# RateLimitService Unit Tests
# =============================================================================

class TestRateLimitService:
    """Tests for RateLimitService methods."""

    @pytest.mark.asyncio
    async def test_check_rate_limit_no_limits(self, mock_db):
        """Test that execution is allowed when no limits are configured."""
        service = RateLimitService(mock_db)
        
        # Mock no limits found - use MagicMock for result since scalars().all() is sync
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.check_rate_limit(
            tool_id=uuid4(),
            user_id=uuid4(),
        )
        
        assert result.allowed is True
        assert result.limit is None

    @pytest.mark.asyncio
    async def test_check_rate_limit_under_limit(self, mock_db, mock_tool, mock_user):
        """Test that execution is allowed when under the limit."""
        service = RateLimitService(mock_db)
        
        # Create a limit allowing 100 executions per hour
        limit = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.TOOL,
            tool_id=mock_tool.id,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
            is_active=True,
        )
        
        # Mock: return the limit - use MagicMock for results since scalars().all() is sync
        mock_limit_result = MagicMock()
        mock_limit_result.scalars.return_value.all.return_value = [limit]
        
        # Mock: return 50 executions (under limit)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 50
        
        mock_db.execute = AsyncMock(side_effect=[mock_limit_result, mock_count_result])
        
        result = await service.check_rate_limit(
            tool_id=mock_tool.id,
            user_id=mock_user.id,
        )
        
        assert result.allowed is True
        assert result.current_count == 50
        assert result.max_count == 100
        assert result.remaining == 50

    @pytest.mark.asyncio
    async def test_check_rate_limit_at_limit(self, mock_db, mock_tool, mock_user):
        """Test that execution is blocked when at the limit."""
        service = RateLimitService(mock_db)
        
        # Create a limit allowing 100 executions per hour
        limit = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.TOOL,
            tool_id=mock_tool.id,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
            is_active=True,
            allow_burst=False,
        )
        
        # Mock: return the limit - use MagicMock for results since scalars().all() is sync
        mock_limit_result = MagicMock()
        mock_limit_result.scalars.return_value.all.return_value = [limit]
        
        # Mock: return 100 executions (at limit)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 100
        
        mock_db.execute = AsyncMock(side_effect=[mock_limit_result, mock_count_result])
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        
        result = await service.check_rate_limit(
            tool_id=mock_tool.id,
            user_id=mock_user.id,
        )
        
        assert result.allowed is False
        assert result.current_count == 100
        assert result.max_count == 100
        assert result.remaining == 0
        assert result.retry_after_seconds is not None

    @pytest.mark.asyncio
    async def test_check_rate_limit_with_burst(self, mock_db, mock_tool, mock_user):
        """Test that burst mode allows exceeding normal limit."""
        service = RateLimitService(mock_db)
        
        # Create a limit with burst allowed (2x multiplier)
        limit = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.TOOL,
            tool_id=mock_tool.id,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
            is_active=True,
            allow_burst=True,
            burst_multiplier=2,
        )
        
        # Mock: return the limit - use MagicMock for results since scalars().all() is sync
        mock_limit_result = MagicMock()
        mock_limit_result.scalars.return_value.all.return_value = [limit]
        
        # Mock: return 150 executions (over normal, under burst)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 150
        
        mock_db.execute = AsyncMock(side_effect=[mock_limit_result, mock_count_result])
        
        result = await service.check_rate_limit(
            tool_id=mock_tool.id,
            user_id=mock_user.id,
        )
        
        # Should be allowed because 150 < 200 (100 * 2 burst)
        assert result.allowed is True
        assert result.current_count == 150
        assert result.remaining == 50  # 200 - 150

    def test_get_period_start_minute(self):
        """Test period start calculation for minute periods."""
        service = RateLimitService(AsyncMock())
        now = datetime(2026, 2, 1, 14, 35, 42, 123456)
        
        result = service._get_period_start(RateLimitPeriod.MINUTE, now)
        
        assert result == datetime(2026, 2, 1, 14, 35, 0, 0)

    def test_get_period_start_hour(self):
        """Test period start calculation for hour periods."""
        service = RateLimitService(AsyncMock())
        now = datetime(2026, 2, 1, 14, 35, 42, 123456)
        
        result = service._get_period_start(RateLimitPeriod.HOUR, now)
        
        assert result == datetime(2026, 2, 1, 14, 0, 0, 0)

    def test_get_period_start_day(self):
        """Test period start calculation for day periods."""
        service = RateLimitService(AsyncMock())
        now = datetime(2026, 2, 1, 14, 35, 42, 123456)
        
        result = service._get_period_start(RateLimitPeriod.DAY, now)
        
        assert result == datetime(2026, 2, 1, 0, 0, 0, 0)

    def test_get_period_start_week(self):
        """Test period start calculation for week periods."""
        service = RateLimitService(AsyncMock())
        # Feb 1, 2026 is a Sunday, so week starts on Monday Jan 26
        now = datetime(2026, 2, 1, 14, 35, 42, 123456)
        
        result = service._get_period_start(RateLimitPeriod.WEEK, now)
        
        # Should be the previous Monday
        assert result.weekday() == 0  # Monday

    def test_get_period_start_month(self):
        """Test period start calculation for month periods."""
        service = RateLimitService(AsyncMock())
        now = datetime(2026, 2, 15, 14, 35, 42, 123456)
        
        result = service._get_period_start(RateLimitPeriod.MONTH, now)
        
        assert result == datetime(2026, 2, 1, 0, 0, 0, 0)


# =============================================================================
# Rate Limit CRUD Tests
# =============================================================================

class TestRateLimitCRUD:
    """Tests for rate limit CRUD operations."""

    @pytest.mark.asyncio
    async def test_create_rate_limit_global(self, mock_db):
        """Test creating a global rate limit."""
        service = RateLimitService(mock_db)
        
        # Mock: no existing limit - use MagicMock for result since scalar_one_or_none() is sync
        mock_find_result = MagicMock()
        mock_find_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_find_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        
        limit = await service.create_rate_limit(
            scope=RateLimitScope.GLOBAL,
            max_executions=1000,
            period=RateLimitPeriod.DAY,
            name="Daily Global Limit",
        )
        
        assert limit.scope == RateLimitScope.GLOBAL
        assert limit.max_executions == 1000
        assert limit.period == RateLimitPeriod.DAY
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_rate_limit_user_requires_user_id(self, mock_db):
        """Test that USER scope requires user_id."""
        service = RateLimitService(mock_db)
        
        with pytest.raises(ValueError, match="USER scope requires user_id"):
            await service.create_rate_limit(
                scope=RateLimitScope.USER,
                max_executions=100,
                period=RateLimitPeriod.HOUR,
            )

    @pytest.mark.asyncio
    async def test_create_rate_limit_tool_requires_tool_id(self, mock_db):
        """Test that TOOL scope requires tool_id."""
        service = RateLimitService(mock_db)
        
        with pytest.raises(ValueError, match="TOOL scope requires tool_id"):
            await service.create_rate_limit(
                scope=RateLimitScope.TOOL,
                max_executions=100,
                period=RateLimitPeriod.HOUR,
            )

    @pytest.mark.asyncio
    async def test_create_rate_limit_user_tool_requires_both(self, mock_db):
        """Test that USER_TOOL scope requires both user_id and tool_id."""
        service = RateLimitService(mock_db)
        
        with pytest.raises(ValueError, match="USER_TOOL scope requires both"):
            await service.create_rate_limit(
                scope=RateLimitScope.USER_TOOL,
                max_executions=100,
                period=RateLimitPeriod.HOUR,
                user_id=uuid4(),
                # Missing tool_id
            )

    @pytest.mark.asyncio
    async def test_update_rate_limit(self, mock_db):
        """Test updating a rate limit."""
        service = RateLimitService(mock_db)
        
        limit_id = uuid4()
        existing_limit = ToolRateLimit(
            id=limit_id,
            scope=RateLimitScope.GLOBAL,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
            is_active=True,
        )
        
        # Use MagicMock for result since scalar_one_or_none() is sync
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_limit
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        
        result = await service.update_rate_limit(
            limit_id=limit_id,
            max_executions=200,
            is_active=False,
        )
        
        assert result.max_executions == 200
        assert result.is_active is False
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_rate_limit(self, mock_db):
        """Test deleting a rate limit."""
        service = RateLimitService(mock_db)
        
        limit_id = uuid4()
        existing_limit = ToolRateLimit(
            id=limit_id,
            scope=RateLimitScope.GLOBAL,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
        )
        
        # Use MagicMock for result since scalar_one_or_none() is sync
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing_limit
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()
        
        result = await service.delete_rate_limit(limit_id)
        
        assert result is True
        mock_db.delete.assert_called_once_with(existing_limit)
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_rate_limit_not_found(self, mock_db):
        """Test deleting a non-existent rate limit."""
        service = RateLimitService(mock_db)
        
        # Use MagicMock for result since scalar_one_or_none() is sync
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await service.delete_rate_limit(uuid4())
        
        assert result is False


# =============================================================================
# Violation Logging Tests
# =============================================================================

class TestViolationLogging:
    """Tests for rate limit violation logging."""

    @pytest.mark.asyncio
    async def test_violation_logged_on_limit_exceeded(self, mock_db, mock_tool, mock_user):
        """Test that a violation is logged when limit is exceeded."""
        service = RateLimitService(mock_db)
        
        limit = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.TOOL,
            tool_id=mock_tool.id,
            max_executions=10,
            period=RateLimitPeriod.HOUR,
            is_active=True,
        )
        
        # Mock: return the limit - use MagicMock for results since scalars().all() is sync
        mock_limit_result = MagicMock()
        mock_limit_result.scalars.return_value.all.return_value = [limit]
        
        # Mock: return 10 executions (at limit)
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 10
        
        mock_db.execute = AsyncMock(side_effect=[mock_limit_result, mock_count_result])
        
        # Mock add to set the id on the violation (simulating DB insert)
        def set_violation_id(obj):
            if hasattr(obj, 'id') and obj.id is None:
                obj.id = uuid4()
        mock_db.add = MagicMock(side_effect=set_violation_id)
        mock_db.flush = AsyncMock()
        
        result = await service.check_rate_limit(
            tool_id=mock_tool.id,
            user_id=mock_user.id,
            agent_name="test-agent",
        )
        
        assert result.allowed is False
        assert result.violation_id is not None
        # Verify violation was added to db
        mock_db.add.assert_called()
        added_obj = mock_db.add.call_args[0][0]
        assert isinstance(added_obj, RateLimitViolation)
        assert added_obj.rate_limit_id == limit.id
        assert added_obj.current_count == 10
        assert added_obj.limit_count == 10


# =============================================================================
# Integration with ToolExecutionService
# =============================================================================

class TestToolExecutionIntegration:
    """Tests for rate limit integration with tool execution."""

    @pytest.mark.asyncio
    async def test_execution_blocked_by_rate_limit(self, mock_db, mock_tool):
        """Test that tool execution is blocked when rate limited."""
        from app.services.tool_execution_service import ToolExecutionService
        
        # This would require more complex mocking of the full execution flow
        # For now, we test the service directly
        pass  # TODO: Add full integration test


# =============================================================================
# Summary and Listing Tests
# =============================================================================

class TestRateLimitSummary:
    """Tests for rate limit summary functionality."""

    @pytest.mark.asyncio
    async def test_get_rate_limit_summary(self, mock_db, mock_tool, mock_user):
        """Test getting rate limit summary."""
        service = RateLimitService(mock_db)
        
        limit1 = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.TOOL,
            tool_id=mock_tool.id,
            max_executions=100,
            period=RateLimitPeriod.HOUR,
            is_active=True,
            name="Hourly Tool Limit",
        )
        
        limit2 = ToolRateLimit(
            id=uuid4(),
            scope=RateLimitScope.USER,
            user_id=mock_user.id,
            max_executions=500,
            period=RateLimitPeriod.DAY,
            is_active=True,
            name="Daily User Limit",
        )
        
        # Mock: return both limits - use MagicMock for results since scalars().all() is sync
        mock_limit_result = MagicMock()
        mock_limit_result.scalars.return_value.all.return_value = [limit1, limit2]
        
        # Mock: return execution counts
        mock_count1 = MagicMock()
        mock_count1.scalar.return_value = 25
        mock_count2 = MagicMock()
        mock_count2.scalar.return_value = 100
        
        mock_db.execute = AsyncMock(side_effect=[
            mock_limit_result,
            mock_count1,
            mock_count2,
        ])
        
        summary = await service.get_rate_limit_summary(
            tool_id=mock_tool.id,
            user_id=mock_user.id,
        )
        
        assert len(summary.limits) == 2
        assert summary.total_remaining == 75  # min(100-25, 500-100) = 75
        assert summary.most_restrictive is not None
        assert summary.most_restrictive["remaining"] == 75


# ============================================================================
# HTTP Endpoint Rate Limits
# ============================================================================


class TestPasswordChangeRateLimit:
    """PUT /users/me has a rate limit to prevent brute-force password changes."""

    def test_users_me_has_rate_limit(self):
        """The PUT /users/me endpoint has a rate limit decorator."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "api", "endpoints", "users.py").read_text()
        assert '@limiter.limit(' in src
        # SGA3-L5: rate limit reduced from 10/minute to 3/minute
        assert "3/minute" in src


class TestLogoutRateLimit:
    """POST /auth/logout has a rate limit to prevent abuse."""

    def test_logout_has_rate_limit(self):
        """auth.py logout endpoint has a rate limit decorator."""
        from tests.helpers.paths import backend_file

        src = backend_file("app", "api", "endpoints", "auth.py").read_text()

        # Find the logout function and verify it has a rate limit
        logout_idx = src.find("async def logout")
        assert logout_idx > 0, "logout function not found"

        # The limiter decorator should appear before the function
        preceding = src[max(0, logout_idx - 300):logout_idx]
        assert "@limiter.limit" in preceding, "logout missing rate limit decorator"
