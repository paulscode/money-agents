"""Unit tests for task analytics and dashboard endpoints."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.task_service import TaskService
from app.models.task import Task, TaskType, TaskStatus


class TestGetDashboardAnalytics:
    """Tests for TaskService.get_dashboard_analytics method."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()
    
    @pytest.fixture
    def service(self, mock_db):
        """Create a TaskService instance."""
        return TaskService(mock_db)
    
    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid4()
    
    @pytest.mark.asyncio
    async def test_analytics_with_no_completed_tasks(self, service, mock_db, user_id):
        """Test analytics when no tasks have been completed."""
        # Mock completed tasks query - empty
        mock_completed_result = MagicMock()
        mock_completed_scalars = MagicMock()
        mock_completed_scalars.all.return_value = []
        mock_completed_result.scalars.return_value = mock_completed_scalars
        
        # Mock by_type query - empty
        mock_type_result = MagicMock()
        mock_type_result.fetchall.return_value = []
        
        # Mock trend queries - 7 days of zeros
        mock_trend_result = MagicMock()
        mock_trend_result.scalar.return_value = 0
        
        # Mock active value query
        mock_value_result = MagicMock()
        mock_value_result.scalar.return_value = 0
        
        # Setup execute to return appropriate results
        mock_db.execute = AsyncMock(side_effect=[
            mock_completed_result,  # completed tasks
            mock_type_result,       # by type
            mock_trend_result,      # trend day 1
            mock_trend_result,      # trend day 2
            mock_trend_result,      # trend day 3
            mock_trend_result,      # trend day 4
            mock_trend_result,      # trend day 5
            mock_trend_result,      # trend day 6
            mock_trend_result,      # trend day 7
            mock_value_result,      # active value
        ])
        
        analytics = await service.get_dashboard_analytics(user_id, days=30)
        
        assert analytics["period_days"] == 30
        assert analytics["completed_count"] == 0
        assert analytics["value_captured"] == 0
        assert analytics["active_value"] == 0
        assert analytics["avg_completion_hours"] is None
        assert analytics["on_time_rate"] is None
        assert len(analytics["completion_trend"]) == 7
    
    @pytest.mark.asyncio
    async def test_analytics_with_completed_tasks(self, service, mock_db, user_id):
        """Test analytics with completed tasks."""
        # Create mock completed tasks
        now = datetime.utcnow()
        task1 = MagicMock(spec=Task)
        task1.estimated_value = 500.0
        task1.created_at = now - timedelta(hours=48)
        task1.completed_at = now - timedelta(hours=24)
        task1.due_date = now  # Completed on time
        
        task2 = MagicMock(spec=Task)
        task2.estimated_value = 1000.0
        task2.created_at = now - timedelta(hours=72)
        task2.completed_at = now - timedelta(hours=12)
        task2.due_date = now - timedelta(hours=24)  # Late completion
        
        task3 = MagicMock(spec=Task)
        task3.estimated_value = None  # No value
        task3.created_at = now - timedelta(hours=24)
        task3.completed_at = now - timedelta(hours=6)
        task3.due_date = None  # No due date
        
        # Mock completed tasks query
        mock_completed_result = MagicMock()
        mock_completed_scalars = MagicMock()
        mock_completed_scalars.all.return_value = [task1, task2, task3]
        mock_completed_result.scalars.return_value = mock_completed_scalars
        
        # Mock by_type query
        mock_type_result = MagicMock()
        mock_type_result.fetchall.return_value = [
            (TaskType.PERSONAL.value, 2),
            (TaskType.CAMPAIGN_ACTION.value, 1),
        ]
        
        # Mock trend queries
        mock_trend_result = MagicMock()
        mock_trend_result.scalar.return_value = 1
        
        # Mock active value query
        mock_value_result = MagicMock()
        mock_value_result.scalar.return_value = 2500.0
        
        # Setup execute
        mock_db.execute = AsyncMock(side_effect=[
            mock_completed_result,  # completed tasks
            mock_type_result,       # by type
            mock_trend_result,      # trend day 1
            mock_trend_result,      # trend day 2
            mock_trend_result,      # trend day 3
            mock_trend_result,      # trend day 4
            mock_trend_result,      # trend day 5
            mock_trend_result,      # trend day 6
            mock_trend_result,      # trend day 7
            mock_value_result,      # active value
        ])
        
        analytics = await service.get_dashboard_analytics(user_id, days=30)
        
        assert analytics["completed_count"] == 3
        assert analytics["value_captured"] == 1500.0  # 500 + 1000
        assert analytics["active_value"] == 2500.0
        assert analytics["avg_completion_hours"] is not None
        assert analytics["on_time_rate"] == 50.0  # 1 out of 2 with due dates
        assert TaskType.PERSONAL.value in analytics["by_type"]
        assert analytics["by_type"][TaskType.PERSONAL.value] == 2
    
    @pytest.mark.asyncio
    async def test_analytics_different_periods(self, service, mock_db, user_id):
        """Test that period_days is correctly set."""
        # Mock empty results
        mock_empty_result = MagicMock()
        mock_empty_scalars = MagicMock()
        mock_empty_scalars.all.return_value = []
        mock_empty_result.scalars.return_value = mock_empty_scalars
        mock_empty_result.fetchall.return_value = []
        mock_empty_result.scalar.return_value = 0
        
        mock_db.execute = AsyncMock(return_value=mock_empty_result)
        
        # Test 7-day period
        analytics_7 = await service.get_dashboard_analytics(user_id, days=7)
        assert analytics_7["period_days"] == 7
        
        # Test 90-day period
        analytics_90 = await service.get_dashboard_analytics(user_id, days=90)
        assert analytics_90["period_days"] == 90


class TestCompletionTrend:
    """Tests for completion trend calculation."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()
    
    @pytest.fixture
    def service(self, mock_db):
        """Create a TaskService instance."""
        return TaskService(mock_db)
    
    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid4()
    
    @pytest.mark.asyncio
    async def test_trend_has_seven_days(self, service, mock_db, user_id):
        """Test that trend always has 7 data points."""
        # Mock empty results for main queries
        mock_empty_result = MagicMock()
        mock_empty_scalars = MagicMock()
        mock_empty_scalars.all.return_value = []
        mock_empty_result.scalars.return_value = mock_empty_scalars
        mock_empty_result.fetchall.return_value = []
        
        # Mock trend queries with varying counts
        trend_counts = [3, 2, 0, 5, 1, 4, 2]
        call_count = [0]  # Use list to allow mutation in nested function
        
        def mock_execute(*args, **kwargs):
            result = MagicMock()
            result.scalars.return_value = mock_empty_scalars
            result.fetchall.return_value = []
            
            # Completed query and type query return empty
            if call_count[0] < 2:
                call_count[0] += 1
                return result
            
            # Trend queries return the counts
            trend_idx = call_count[0] - 2
            if trend_idx < 7:
                result.scalar.return_value = trend_counts[trend_idx]
            else:
                result.scalar.return_value = 0  # Active value
            
            call_count[0] += 1
            return result
        
        mock_db.execute = AsyncMock(side_effect=mock_execute)
        
        analytics = await service.get_dashboard_analytics(user_id, days=30)
        
        assert len(analytics["completion_trend"]) == 7
        # Trend should be ordered oldest to newest
        for item in analytics["completion_trend"]:
            assert "date" in item
            assert "completed" in item
    
    @pytest.mark.asyncio
    async def test_trend_dates_are_correct(self, service, mock_db, user_id):
        """Test that trend dates are correct and in order."""
        # Mock empty results
        mock_empty_result = MagicMock()
        mock_empty_scalars = MagicMock()
        mock_empty_scalars.all.return_value = []
        mock_empty_result.scalars.return_value = mock_empty_scalars
        mock_empty_result.fetchall.return_value = []
        mock_empty_result.scalar.return_value = 0
        
        mock_db.execute = AsyncMock(return_value=mock_empty_result)
        
        analytics = await service.get_dashboard_analytics(user_id, days=30)
        
        # Dates should be ascending (oldest first)
        dates = [item["date"] for item in analytics["completion_trend"]]
        for i in range(len(dates) - 1):
            assert dates[i] < dates[i + 1], "Dates should be in ascending order"


class TestValueCalculations:
    """Tests for value-related analytics calculations."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        return AsyncMock()
    
    @pytest.fixture
    def service(self, mock_db):
        """Create a TaskService instance."""
        return TaskService(mock_db)
    
    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid4()
    
    @pytest.mark.asyncio
    async def test_value_captured_ignores_none(self, service, mock_db, user_id):
        """Test that None values don't break sum calculation."""
        now = datetime.utcnow()
        
        # Create tasks with mix of None and actual values
        task1 = MagicMock(spec=Task)
        task1.estimated_value = None
        task1.created_at = now - timedelta(hours=24)
        task1.completed_at = now - timedelta(hours=12)
        task1.due_date = None
        
        task2 = MagicMock(spec=Task)
        task2.estimated_value = 1000.0
        task2.created_at = now - timedelta(hours=48)
        task2.completed_at = now - timedelta(hours=24)
        task2.due_date = None
        
        mock_completed_result = MagicMock()
        mock_completed_scalars = MagicMock()
        mock_completed_scalars.all.return_value = [task1, task2]
        mock_completed_result.scalars.return_value = mock_completed_scalars
        
        mock_type_result = MagicMock()
        mock_type_result.fetchall.return_value = []
        
        mock_trend_result = MagicMock()
        mock_trend_result.scalar.return_value = 0
        
        mock_value_result = MagicMock()
        mock_value_result.scalar.return_value = 500.0
        
        mock_db.execute = AsyncMock(side_effect=[
            mock_completed_result,
            mock_type_result,
            mock_trend_result, mock_trend_result, mock_trend_result,
            mock_trend_result, mock_trend_result, mock_trend_result,
            mock_trend_result,
            mock_value_result,
        ])
        
        analytics = await service.get_dashboard_analytics(user_id, days=30)
        
        # Should only count the 1000.0 value, not crash on None
        assert analytics["value_captured"] == 1000.0
        assert analytics["active_value"] == 500.0
