"""Unit tests for TaskGenerationService."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.task_generation_service import TaskGenerationService
from app.models.task import Task, TaskType, TaskStatus
from app.models import (
    Campaign,
    CampaignStatus,
    UserInputRequest,
    InputStatus,
    InputType,
    InputPriority,
    UserIdea,
    IdeaStatus,
)


@pytest.fixture
def sample_user_id():
    """Sample user ID."""
    return uuid4()


@pytest.fixture
def sample_campaign(sample_user_id):
    """Create a sample campaign."""
    campaign = MagicMock(spec=Campaign)
    campaign.id = uuid4()
    campaign.user_id = sample_user_id
    # Campaign has a proposal relationship, not proposal_title directly
    campaign.proposal = MagicMock()
    campaign.proposal.title = "Test Campaign"
    campaign.budget_allocated = 1000.0
    campaign.status = CampaignStatus.ACTIVE.value
    return campaign


@pytest.fixture
def sample_input_request(sample_campaign):
    """Create a sample input request."""
    input_req = MagicMock(spec=UserInputRequest)
    input_req.id = uuid4()
    input_req.campaign_id = sample_campaign.id
    input_req.input_key = "api_key"
    input_req.input_type = InputType.TEXT
    input_req.title = "API Key"
    input_req.description = "Please provide your API key"
    input_req.status = InputStatus.PENDING
    input_req.blocking_count = 2
    input_req.priority = InputPriority.BLOCKING
    return input_req


@pytest.fixture
def sample_idea(sample_user_id):
    """Create a sample idea."""
    idea = MagicMock(spec=UserIdea)
    idea.id = uuid4()
    idea.user_id = sample_user_id
    idea.original_content = "Build a podcast transcription tool"
    idea.reformatted_content = "Create a tool that transcribes podcasts"
    idea.source = "brainstorm"
    idea.status = IdeaStatus.PROCESSED.value
    return idea


class TestCreateTaskForCampaignInput:
    """Tests for creating campaign input tasks."""
    
    @pytest.mark.asyncio
    async def test_skip_non_pending_input(
        self, sample_user_id, sample_campaign, sample_input_request
    ):
        """Should skip task creation for non-pending inputs."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        # Set input to already provided
        sample_input_request.status = InputStatus.PROVIDED
        
        result = await service.create_task_for_campaign_input(
            user_id=sample_user_id,
            campaign=sample_campaign,
            input_request=sample_input_request,
        )
        
        assert result is None
        db.add.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_create_new_task(
        self, sample_user_id, sample_campaign, sample_input_request
    ):
        """Should create a new task when none exists."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        # Mock no existing task found (scalars().first() pattern)
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await service.create_task_for_campaign_input(
            user_id=sample_user_id,
            campaign=sample_campaign,
            input_request=sample_input_request,
        )
        
        # Verify task was added to session
        db.add.assert_called_once()
        added_task = db.add.call_args[0][0]
        
        assert isinstance(added_task, Task)
        assert added_task.user_id == sample_user_id
        assert added_task.title == f"Provide input: {sample_input_request.title}"
        assert added_task.task_type == TaskType.CAMPAIGN_ACTION.value
        assert added_task.status == TaskStatus.READY.value
        assert added_task.source_type == "campaign_input"
        assert added_task.source_id == sample_input_request.id
        # 10% of 1000 budget for blocking input
        assert added_task.estimated_value == 100.0
    
    @pytest.mark.asyncio
    async def test_return_existing_task(
        self, sample_user_id, sample_campaign, sample_input_request
    ):
        """Should return existing task instead of creating duplicate."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        existing_task = MagicMock(spec=Task)
        existing_task.id = uuid4()
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = existing_task
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await service.create_task_for_campaign_input(
            user_id=sample_user_id,
            campaign=sample_campaign,
            input_request=sample_input_request,
        )
        
        assert result == existing_task
        db.add.assert_not_called()


class TestCreateTaskForProcessedIdea:
    """Tests for creating tasks from processed ideas."""
    
    @pytest.mark.asyncio
    async def test_create_new_idea_task(
        self, sample_user_id, sample_idea
    ):
        """Should create a new task for processed idea."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        recommended_action = "Validate market for podcast transcription service"
        next_steps = "1. Research competitors\n2. Survey potential users"
        
        result = await service.create_task_for_processed_idea(
            user_id=sample_user_id,
            idea=sample_idea,
            recommended_action=recommended_action,
            next_steps=next_steps,
            estimated_value=500.0,
        )
        
        db.add.assert_called_once()
        added_task = db.add.call_args[0][0]
        
        assert isinstance(added_task, Task)
        assert added_task.user_id == sample_user_id
        assert added_task.title == recommended_action
        assert added_task.task_type == TaskType.IDEA_ACTION.value
        assert added_task.source_type == "idea"
        assert added_task.source_id == sample_idea.id
        assert added_task.estimated_value == 500.0
        assert "Original idea:" in added_task.description
        assert "Recommended next steps:" in added_task.description
    
    @pytest.mark.asyncio
    async def test_return_existing_task(
        self, sample_user_id, sample_idea
    ):
        """Should return existing task instead of creating duplicate."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        existing_task = MagicMock(spec=Task)
        existing_task.id = uuid4()
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = existing_task
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await service.create_task_for_processed_idea(
            user_id=sample_user_id,
            idea=sample_idea,
            recommended_action="Validate market",
        )
        
        assert result == existing_task
        db.add.assert_not_called()


class TestFollowUpTasks:
    """Tests for follow-up task creation."""
    
    @pytest.mark.asyncio
    async def test_create_follow_up_task(self, sample_user_id):
        """Should create a deferred follow-up task."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        follow_up_date = datetime.utcnow() + timedelta(days=7)
        
        result = await service.create_follow_up_task(
            user_id=sample_user_id,
            title="Check campaign performance",
            description="Review metrics after one week",
            follow_up_date=follow_up_date,
            source_type="campaign",
            source_id=uuid4(),
        )
        
        db.add.assert_called_once()
        added_task = db.add.call_args[0][0]
        
        assert isinstance(added_task, Task)
        assert added_task.task_type == TaskType.FOLLOW_UP.value
        assert added_task.status == TaskStatus.DEFERRED.value
        assert added_task.deferred_until == follow_up_date
        assert added_task.due_date == follow_up_date

class TestTaskSourceManagement:
    """Tests for task completion/cancellation by source."""
    
    @pytest.mark.asyncio
    async def test_complete_task_for_source(self, sample_user_id):
        """Should complete task when source action is done."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        existing_task = MagicMock(spec=Task)
        existing_task.id = uuid4()
        existing_task.status = TaskStatus.READY.value
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = existing_task
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        source_id = uuid4()
        
        result = await service.complete_task_for_source(
            user_id=sample_user_id,
            source_type="campaign_input",
            source_id=source_id,
            completion_notes="Input provided successfully",
            actual_value=150.0,
        )
        
        assert result is True
        assert existing_task.status == TaskStatus.COMPLETED.value
        assert existing_task.completion_notes == "Input provided successfully"
        assert existing_task.actual_value == 150.0
    
    @pytest.mark.asyncio
    async def test_cancel_task_for_source(self, sample_user_id):
        """Should cancel task when source is resolved externally."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        existing_task = MagicMock(spec=Task)
        existing_task.id = uuid4()
        existing_task.status = TaskStatus.READY.value
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = existing_task
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await service.cancel_task_for_source(
            user_id=sample_user_id,
            source_type="opportunity",
            source_id=uuid4(),
            reason="Opportunity dismissed",
        )
        
        assert result is True
        assert existing_task.status == TaskStatus.CANCELLED.value
        assert existing_task.completion_notes == "Opportunity dismissed"
    
    @pytest.mark.asyncio
    async def test_no_task_to_complete(self, sample_user_id):
        """Should return False when no task exists to complete."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        result = await service.complete_task_for_source(
            user_id=sample_user_id,
            source_type="campaign_input",
            source_id=uuid4(),
        )
        
        assert result is False


class TestActivateDeferredTasks:
    """Tests for activating deferred tasks."""
    
    @pytest.mark.asyncio
    async def test_activate_due_tasks(self, sample_user_id):
        """Should activate tasks past their deferred_until date."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        past_due_task = MagicMock(spec=Task)
        past_due_task.id = uuid4()
        past_due_task.status = TaskStatus.DEFERRED.value
        past_due_task.deferred_until = datetime.utcnow() - timedelta(hours=1)
        
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [past_due_task]
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        count = await service.activate_deferred_tasks(sample_user_id)
        
        assert count == 1
        assert past_due_task.status == TaskStatus.READY.value
        assert past_due_task.deferred_until is None
    
    @pytest.mark.asyncio
    async def test_no_tasks_to_activate(self, sample_user_id):
        """Should return 0 when no tasks need activation."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        db.execute.return_value = mock_result
        
        count = await service.activate_deferred_tasks(sample_user_id)
        
        assert count == 0


class TestDescriptionFormatting:
    """Tests for task description formatting."""
    
    def test_campaign_input_description_with_blocking(self, sample_campaign, sample_input_request):
        """Should include blocking count in description."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        description = service._format_campaign_input_description(
            sample_campaign, sample_input_request
        )
        
        assert "Test Campaign" in description
        assert "API Key" in description
        assert "blocking 2 items" in description
        assert f"/campaigns/{sample_campaign.id}" in description
    
    def test_idea_task_description_with_next_steps(self, sample_idea):
        """Should include next steps in idea task description."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        next_steps = "1. Research\n2. Validate"
        
        description = service._format_idea_task_description(
            sample_idea, next_steps
        )
        
        assert "Original idea:" in description
        assert sample_idea.reformatted_content in description
        assert "Recommended next steps:" in description
        assert next_steps in description
        assert f"/ideas/{sample_idea.id}" in description
    
    def test_opportunity_review_description(self):
        """Should format opportunity review task correctly."""
        db = AsyncMock()
        service = TaskGenerationService(db)
        
        description = service._format_opportunity_review_description(
            count=10,
            total_value=5000.0
        )
        
        assert "10 opportunities" in description
        assert "$5,000" in description
        assert "20 minutes" in description  # 10 * 2
        assert "/scout" in description
