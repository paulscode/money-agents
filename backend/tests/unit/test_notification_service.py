"""Unit tests for NotificationService."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import MagicMock, AsyncMock, patch

from app.services.notification_service import (
    NotificationService,
    RATE_LIMIT_RULES,
    DEFAULT_PRIORITIES,
)
from app.models.notification import (
    Notification,
    NotificationType,
    NotificationPriority,
)


@pytest.fixture
def sample_user_id():
    return uuid4()


def create_mock_db_with_scalar(scalar_value):
    """Create a mock db session that returns the given scalar value."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    
    # Mock the result object that db.execute returns
    mock_result = MagicMock()
    mock_result.scalar.return_value = scalar_value
    
    # db.execute is async, returns the mock_result
    db.execute = AsyncMock(return_value=mock_result)
    
    return db


@pytest.fixture
def mock_db():
    """Create a mock database session with scalar returning 0 (no rate limit)."""
    return create_mock_db_with_scalar(0)


@pytest.fixture
def notification_service(mock_db):
    """Create a NotificationService with mock db."""
    return NotificationService(mock_db)


# ==========================================================================
# Test Create Notification
# ==========================================================================

class TestCreateNotification:
    """Tests for notification creation."""
    
    @pytest.mark.asyncio
    async def test_create_basic_notification(self, mock_db, sample_user_id):
        """Test creating a basic notification."""
        service = NotificationService(mock_db)
        
        notification = await service.create(
            user_id=sample_user_id,
            type=NotificationType.TASK_CREATED,
            title="New Task",
            message="A new task has been created.",
        )
        
        assert notification is not None
        assert notification.user_id == sample_user_id
        assert notification.type == NotificationType.TASK_CREATED
        assert notification.title == "New Task"
        assert notification.priority == NotificationPriority.LOW  # Default for task_created
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_notification_with_link(self, mock_db, sample_user_id):
        """Test creating a notification with link."""
        service = NotificationService(mock_db)
        
        notification = await service.create(
            user_id=sample_user_id,
            type=NotificationType.INPUT_REQUIRED,
            title="Input Required",
            message="Campaign needs your input",
            link="/campaigns/123",
            link_text="View Campaign",
        )
        
        assert notification is not None
        assert notification.link == "/campaigns/123"
        assert notification.link_text == "View Campaign"
    
    @pytest.mark.asyncio
    async def test_create_notification_custom_priority(self, mock_db, sample_user_id):
        """Test creating a notification with custom priority."""
        service = NotificationService(mock_db)
        
        notification = await service.create(
            user_id=sample_user_id,
            type=NotificationType.TASK_CREATED,
            title="Important Task",
            message="A task that needs attention",
            priority=NotificationPriority.HIGH,
        )
        
        assert notification is not None
        assert notification.priority == NotificationPriority.HIGH


# ==========================================================================
# Test Rate Limiting
# ==========================================================================

class TestRateLimiting:
    """Tests for notification rate limiting."""
    
    @pytest.mark.asyncio
    async def test_rate_limited_notification_blocked(self, sample_user_id):
        """Test that rate-limited notification types are blocked."""
        # Create mock db with count above rate limit
        mock_db = create_mock_db_with_scalar(5)  # Above any limit
        service = NotificationService(mock_db)
        
        # Try to create an opportunities notification (rate limited to 1 per hour)
        notification = await service.create(
            user_id=sample_user_id,
            type=NotificationType.OPPORTUNITIES_DISCOVERED,
            title="New Opportunities",
            message="5 new opportunities found",
            source_type="opportunity_batch",
        )
        
        # Should be blocked
        assert notification is None
        mock_db.add.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_rate_limit_skip_flag(self, mock_db, sample_user_id):
        """Test that skip_rate_limit flag bypasses rate limiting."""
        # Even with rate limiting count high, skip_rate_limit bypasses it
        service = NotificationService(mock_db)
        
        notification = await service.create(
            user_id=sample_user_id,
            type=NotificationType.OPPORTUNITIES_DISCOVERED,
            title="New Opportunities",
            message="5 new opportunities found",
            source_type="opportunity_batch",
            skip_rate_limit=True,
        )
        
        # Should NOT be blocked
        assert notification is not None
        mock_db.add.assert_called_once()


# ==========================================================================
# Test Convenience Methods
# ==========================================================================

class TestConvenienceMethods:
    """Tests for convenience notification methods."""
    
    @pytest.mark.asyncio
    async def test_notify_task_created(self, mock_db, sample_user_id):
        """Test notify_task_created method."""
        service = NotificationService(mock_db)
        
        task_id = uuid4()
        notification = await service.notify_task_created(
            user_id=sample_user_id,
            task_id=task_id,
            task_title="Complete API integration",
        )
        
        assert notification is not None
        assert notification.type == NotificationType.TASK_CREATED
        assert notification.source_type == "task"
        assert notification.source_id == task_id
        assert "Complete API integration" in notification.message
    
    @pytest.mark.asyncio
    async def test_notify_input_required(self, mock_db, sample_user_id):
        """Test notify_input_required method."""
        service = NotificationService(mock_db)
        
        campaign_id = uuid4()
        notification = await service.notify_input_required(
            user_id=sample_user_id,
            campaign_id=campaign_id,
            campaign_title="Email Marketing Campaign",
            input_title="API Key",
        )
        
        assert notification is not None
        assert notification.type == NotificationType.INPUT_REQUIRED
        assert notification.priority == NotificationPriority.HIGH
        assert notification.source_type == "campaign"
    
    @pytest.mark.asyncio
    async def test_notify_campaign_completed_with_revenue(self, mock_db, sample_user_id):
        """Test notify_campaign_completed with revenue."""
        service = NotificationService(mock_db)
        
        campaign_id = uuid4()
        notification = await service.notify_campaign_completed(
            user_id=sample_user_id,
            campaign_id=campaign_id,
            campaign_title="Holiday Sale",
            revenue=5000.50,
        )
        
        assert notification is not None
        assert notification.type == NotificationType.CAMPAIGN_COMPLETED
        assert "$5,000.50" in notification.message


# ==========================================================================
# Test Query Methods
# ==========================================================================

class TestQueryMethods:
    """Tests for notification query methods."""
    
    @pytest.mark.asyncio
    async def test_get_unread_count(self, sample_user_id):
        """Test getting unread notification count."""
        mock_db = create_mock_db_with_scalar(5)
        service = NotificationService(mock_db)
        
        count = await service.get_unread_count(sample_user_id)
        
        assert count == 5
    
    @pytest.mark.asyncio
    async def test_get_unread_count_zero(self, sample_user_id):
        """Test getting unread count when there are none."""
        mock_db = create_mock_db_with_scalar(None)  # No results
        service = NotificationService(mock_db)
        
        count = await service.get_unread_count(sample_user_id)
        
        assert count == 0


# ==========================================================================
# Test Update Methods
# ==========================================================================

class TestUpdateMethods:
    """Tests for notification update methods."""
    
    @pytest.mark.asyncio
    async def test_mark_as_read(self, sample_user_id):
        """Test marking a notification as read."""
        notification_id = uuid4()
        
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        service = NotificationService(mock_db)
        success = await service.mark_as_read(notification_id, sample_user_id)
        
        assert success is True
    
    @pytest.mark.asyncio
    async def test_mark_as_read_not_found(self, sample_user_id):
        """Test marking non-existent notification as read."""
        notification_id = uuid4()
        
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        service = NotificationService(mock_db)
        success = await service.mark_as_read(notification_id, sample_user_id)
        
        assert success is False
    
    @pytest.mark.asyncio
    async def test_mark_all_as_read(self, sample_user_id):
        """Test marking all notifications as read."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 10
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        service = NotificationService(mock_db)
        count = await service.mark_all_as_read(sample_user_id)
        
        assert count == 10
    
    @pytest.mark.asyncio
    async def test_dismiss(self, sample_user_id):
        """Test dismissing a notification."""
        notification_id = uuid4()
        
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        service = NotificationService(mock_db)
        success = await service.dismiss(notification_id, sample_user_id)
        
        assert success is True


# ==========================================================================
# Test Default Priorities
# ==========================================================================

class TestDefaultPriorities:
    """Tests for default priority configuration."""
    
    def test_urgent_types_have_high_priority(self):
        """Test that urgent notification types have high/urgent priority."""
        assert DEFAULT_PRIORITIES[NotificationType.AGENT_ERROR] == NotificationPriority.URGENT
        assert DEFAULT_PRIORITIES[NotificationType.TASK_OVERDUE] == NotificationPriority.HIGH
        assert DEFAULT_PRIORITIES[NotificationType.CREDENTIAL_EXPIRING] == NotificationPriority.HIGH
    
    def test_informational_types_have_low_priority(self):
        """Test that informational notifications have low priority."""
        assert DEFAULT_PRIORITIES[NotificationType.TASK_CREATED] == NotificationPriority.LOW
        assert DEFAULT_PRIORITIES[NotificationType.TASK_COMPLETED] == NotificationPriority.LOW
        assert DEFAULT_PRIORITIES[NotificationType.CAMPAIGN_STARTED] == NotificationPriority.LOW
