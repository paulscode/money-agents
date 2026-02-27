"""Unit tests for TaskContextService."""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.task_context_service import (
    TaskContextService,
    TASK_MANAGEMENT_PROMPT,
    get_brainstorm_task_prompt,
    TASK_CREATE_PATTERN,
    TASK_COMPLETE_PATTERN,
    TASK_DEFER_PATTERN,
)
from app.models.task import Task, TaskType, TaskStatus


class TestTaskContextService:
    """Tests for TaskContextService methods."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        """Create a TaskContextService instance."""
        return TaskContextService(mock_db)
    
    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid4()
    
    # =========================================================================
    # Pattern Tests
    # =========================================================================
    
    def test_task_create_pattern_basic(self):
        """Test basic task creation pattern matching."""
        text = "[TASK: Review proposal draft]"
        matches = TASK_CREATE_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0] == "Review proposal draft"
    
    def test_task_create_pattern_with_description(self):
        """Test task creation pattern with description."""
        text = "[TASK: Review proposal | Check for typos and clarity]"
        matches = TASK_CREATE_PATTERN.findall(text)
        assert len(matches) == 1
        assert "Review proposal" in matches[0]
    
    def test_task_create_pattern_with_due_and_value(self):
        """Test task creation pattern with due date and value."""
        text = "[TASK: Review proposal | due:2d | value:$500]"
        matches = TASK_CREATE_PATTERN.findall(text)
        assert len(matches) == 1
        assert "due:2d" in matches[0]
        assert "value:$500" in matches[0]
    
    def test_task_create_pattern_multiple(self):
        """Test multiple task creation patterns."""
        text = """
        [TASK: First task]
        Some other text
        [TASK: Second task | with description]
        """
        matches = TASK_CREATE_PATTERN.findall(text)
        assert len(matches) == 2
    
    def test_task_complete_pattern_basic(self):
        """Test basic task completion pattern."""
        text = "[TASK_COMPLETE: 12345678-1234-1234-1234-123456789012]"
        matches = TASK_COMPLETE_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][0] == "12345678-1234-1234-1234-123456789012"
    
    def test_task_complete_pattern_with_notes(self):
        """Test task completion pattern with notes."""
        text = "[TASK_COMPLETE: 12345678-1234-1234-1234-123456789012, All items checked]"
        matches = TASK_COMPLETE_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][0] == "12345678-1234-1234-1234-123456789012"
        assert matches[0][1] == "All items checked"
    
    def test_task_defer_pattern_days(self):
        """Test task deferral pattern with days."""
        text = "[TASK_DEFER: 12345678-1234-1234-1234-123456789012, 3 days]"
        matches = TASK_DEFER_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][0] == "12345678-1234-1234-1234-123456789012"
        assert matches[0][1] == "3"
        assert matches[0][2].lower().startswith("day")
    
    def test_task_defer_pattern_week(self):
        """Test task deferral pattern with weeks."""
        text = "[TASK_DEFER: 12345678-1234-1234-1234-123456789012, 1 week]"
        matches = TASK_DEFER_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][1] == "1"
        assert matches[0][2].lower().startswith("week")
    
    def test_task_defer_pattern_hours(self):
        """Test task deferral pattern with hours."""
        text = "[TASK_DEFER: 12345678-1234-1234-1234-123456789012, 4 hours]"
        matches = TASK_DEFER_PATTERN.findall(text)
        assert len(matches) == 1
        assert matches[0][1] == "4"
        assert matches[0][2].lower().startswith("hour")
    
    # =========================================================================
    # Extract Task Creation Tests
    # =========================================================================
    
    def test_extract_task_creation_basic(self, service):
        """Test extracting basic task creation."""
        text = "[TASK: Review proposal draft]"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Review proposal draft"
    
    def test_extract_task_creation_with_description(self, service):
        """Test extracting task with description."""
        text = "[TASK: Review proposal | Check for typos and clarity]"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Review proposal"
        assert tasks[0]["description"] == "Check for typos and clarity"
    
    def test_extract_task_creation_with_due(self, service):
        """Test extracting task with due date."""
        text = "[TASK: Review proposal | due:2d]"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Review proposal"
        assert tasks[0]["due"] == "2d"
    
    def test_extract_task_creation_with_value(self, service):
        """Test extracting task with value."""
        text = "[TASK: Review proposal | value:$500]"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Review proposal"
        assert tasks[0]["value"] == 500.0
    
    def test_extract_task_creation_full(self, service):
        """Test extracting task with all fields."""
        text = "[TASK: Review proposal | Check clarity | due:2d | value:$1,500]"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 1
        assert tasks[0]["title"] == "Review proposal"
        assert tasks[0]["description"] == "Check clarity"
        assert tasks[0]["due"] == "2d"
        assert tasks[0]["value"] == 1500.0
    
    def test_extract_task_creation_no_tasks(self, service):
        """Test extracting from text with no tasks."""
        text = "Just some regular text without any task markers."
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 0
    
    # =========================================================================
    # Extract Task Completion Tests
    # =========================================================================
    
    def test_extract_task_completions_basic(self, service):
        """Test extracting basic task completion."""
        task_id = "12345678-1234-1234-1234-123456789012"
        text = f"[TASK_COMPLETE: {task_id}]"
        completions = service.extract_task_completions(text)
        assert len(completions) == 1
        assert completions[0]["task_id"] == task_id
        assert completions[0]["notes"] is None
    
    def test_extract_task_completions_with_notes(self, service):
        """Test extracting task completion with notes."""
        task_id = "12345678-1234-1234-1234-123456789012"
        text = f"[TASK_COMPLETE: {task_id}, All items checked off]"
        completions = service.extract_task_completions(text)
        assert len(completions) == 1
        assert completions[0]["task_id"] == task_id
        assert completions[0]["notes"] == "All items checked off"
    
    # =========================================================================
    # Extract Task Deferral Tests
    # =========================================================================
    
    def test_extract_task_deferrals_days(self, service):
        """Test extracting task deferral in days."""
        task_id = "12345678-1234-1234-1234-123456789012"
        text = f"[TASK_DEFER: {task_id}, 3 days]"
        deferrals = service.extract_task_deferrals(text)
        assert len(deferrals) == 1
        assert deferrals[0]["task_id"] == task_id
        # Check that defer_until is about 3 days from now
        defer_dt = datetime.fromisoformat(deferrals[0]["defer_until"])
        expected = datetime.now(timezone.utc) + timedelta(days=3)
        assert abs((defer_dt - expected).total_seconds()) < 60  # Within 1 minute
    
    def test_extract_task_deferrals_weeks(self, service):
        """Test extracting task deferral in weeks."""
        task_id = "12345678-1234-1234-1234-123456789012"
        text = f"[TASK_DEFER: {task_id}, 2 weeks]"
        deferrals = service.extract_task_deferrals(text)
        assert len(deferrals) == 1
        defer_dt = datetime.fromisoformat(deferrals[0]["defer_until"])
        expected = datetime.now(timezone.utc) + timedelta(weeks=2)
        assert abs((defer_dt - expected).total_seconds()) < 60
    
    def test_extract_task_deferrals_hours(self, service):
        """Test extracting task deferral in hours."""
        task_id = "12345678-1234-1234-1234-123456789012"
        text = f"[TASK_DEFER: {task_id}, 4 hours]"
        deferrals = service.extract_task_deferrals(text)
        assert len(deferrals) == 1
        defer_dt = datetime.fromisoformat(deferrals[0]["defer_until"])
        expected = datetime.now(timezone.utc) + timedelta(hours=4)
        assert abs((defer_dt - expected).total_seconds()) < 60
    
    # =========================================================================
    # Clean Task Tags Tests
    # =========================================================================
    
    def test_clean_task_tags_all(self, service):
        """Test cleaning all task tags from text."""
        text = """
        Here is some text [TASK: Create something]
        and more text [TASK_COMPLETE: 12345678-1234-1234-1234-123456789012]
        final text [TASK_DEFER: 12345678-1234-1234-1234-123456789012, 2 days]
        """
        cleaned = service.clean_task_tags(text)
        assert "[TASK:" not in cleaned
        assert "[TASK_COMPLETE:" not in cleaned
        assert "[TASK_DEFER:" not in cleaned
        assert "Here is some text" in cleaned
        assert "and more text" in cleaned
        assert "final text" in cleaned
    
    # =========================================================================
    # Prompt Helper Tests
    # =========================================================================
    
    def test_get_brainstorm_task_prompt_with_context(self):
        """Test getting prompt with task context."""
        task_context = "## Current Tasks\n1. Review proposal"
        prompt = get_brainstorm_task_prompt(task_context)
        assert TASK_MANAGEMENT_PROMPT in prompt
        assert task_context in prompt
    
    def test_get_brainstorm_task_prompt_no_context(self):
        """Test getting prompt with no task context."""
        prompt = get_brainstorm_task_prompt("")
        assert TASK_MANAGEMENT_PROMPT in prompt
        assert "no active tasks" in prompt.lower()
    
    def test_task_management_prompt_content(self):
        """Test that task management prompt has required elements."""
        assert "[TASK:" in TASK_MANAGEMENT_PROMPT
        assert "[TASK_COMPLETE:" in TASK_MANAGEMENT_PROMPT
        assert "[TASK_DEFER:" in TASK_MANAGEMENT_PROMPT
        assert "Creating Tasks" in TASK_MANAGEMENT_PROMPT


class TestTaskContextServiceAsync:
    """Async tests for TaskContextService that require mocked DB."""
    
    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = AsyncMock()
        return db
    
    @pytest.fixture
    def service(self, mock_db):
        """Create a TaskContextService instance."""
        return TaskContextService(mock_db)
    
    @pytest.fixture
    def user_id(self):
        """Test user ID."""
        return uuid4()
    
    @pytest.mark.asyncio
    async def test_get_task_context_for_prompt_no_tasks(self, service, mock_db, user_id):
        """Test getting context when user has no tasks."""
        # Mock empty result for tasks query
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        context = await service.get_task_context_for_prompt(user_id)
        assert context == ""
    
    @pytest.mark.asyncio
    async def test_get_task_summary(self, service, mock_db, user_id):
        """Test getting task summary."""
        # Mock the count queries with proper return values
        mock_status_result = MagicMock()
        mock_status_result.fetchall.return_value = [
            (TaskStatus.READY.value, 5),
            (TaskStatus.IN_PROGRESS.value, 2),
            (TaskStatus.BLOCKED.value, 1),
        ]
        
        mock_overdue_result = MagicMock()
        mock_overdue_result.scalar.return_value = 0  # overdue count
        
        mock_value_result = MagicMock()
        mock_value_result.scalar.return_value = 10000  # total value
        
        # Setup execute to return different results for different queries
        mock_db.execute = AsyncMock(side_effect=[
            mock_status_result,
            mock_overdue_result,
            mock_value_result,
        ])
        
        summary = await service.get_task_summary(user_id)
        
        assert summary["active"] == 7  # 5 ready + 2 in progress
        assert summary["blocked"] == 1
        assert summary["total_value"] == 10000


class TestTaskActionParsing:
    """Tests for parsing task actions from LLM responses."""
    
    def test_complex_llm_response(self):
        """Test parsing a complex LLM response with multiple actions."""
        service = TaskContextService(AsyncMock())
        
        response = """
        I've reviewed your request and have a few suggestions:
        
        1. [TASK: Set up meeting with stakeholders | Discuss Q2 planning | due:3d]
        2. [TASK: Prepare presentation slides | value:$200]
        
        I've also noticed task 12345678-1234-1234-1234-123456789012 appears to be complete.
        [TASK_COMPLETE: 12345678-1234-1234-1234-123456789012, All requirements met]
        
        The low-priority review can wait:
        [TASK_DEFER: 87654321-4321-4321-4321-210987654321, 1 week]
        
        Let me know if you'd like me to adjust any of these!
        """
        
        creations = service.extract_task_creation(response)
        assert len(creations) == 2
        assert creations[0]["title"] == "Set up meeting with stakeholders"
        assert creations[0]["due"] == "3d"
        assert creations[1]["title"] == "Prepare presentation slides"
        assert creations[1]["value"] == 200.0
        
        completions = service.extract_task_completions(response)
        assert len(completions) == 1
        assert completions[0]["notes"] == "All requirements met"
        
        deferrals = service.extract_task_deferrals(response)
        assert len(deferrals) == 1
    
    def test_edge_case_nested_brackets(self):
        """Test handling of text with nested or partial brackets."""
        service = TaskContextService(AsyncMock())
        
        # This shouldn't match - no proper closing bracket
        text = "[TASK: Incomplete task"
        tasks = service.extract_task_creation(text)
        assert len(tasks) == 0
    
    def test_case_insensitivity(self):
        """Test that patterns are case insensitive."""
        service = TaskContextService(AsyncMock())
        
        text1 = "[task: lowercase task]"
        text2 = "[TASK: UPPERCASE TASK]"
        text3 = "[Task: Mixed Case Task]"
        
        assert len(service.extract_task_creation(text1)) == 1
        assert len(service.extract_task_creation(text2)) == 1
        assert len(service.extract_task_creation(text3)) == 1
