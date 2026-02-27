"""Tests for the Task Service - CRUD, priority, and state management."""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from app.models.task import TaskType, TaskStatus, Task
from app.services.task_service import TaskService, TaskSortBy


class TestTaskService:
    """Tests for TaskService."""
    
    @pytest.mark.asyncio
    async def test_create_task(self, db_session, test_user):
        """Test creating a new task."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Test task",
            description="A test task description",
            task_type=TaskType.PERSONAL,
        )
        
        assert task.id is not None
        assert task.user_id == test_user.id
        assert task.title == "Test task"
        assert task.description == "A test task description"
        assert task.task_type == TaskType.PERSONAL.value
        assert task.status == TaskStatus.CREATED.value
        assert 0 <= task.priority_score <= 100
    
    @pytest.mark.asyncio
    async def test_create_task_with_due_date(self, db_session, test_user):
        """Test creating a task with due date affects priority."""
        service = TaskService(db_session)
        
        # Task due today should have high priority
        today_task = await service.create_task(
            user_id=test_user.id,
            title="Due today",
            due_date=datetime.utcnow() + timedelta(hours=6),
        )
        
        # Task due next week should have lower priority
        later_task = await service.create_task(
            user_id=test_user.id,
            title="Due next week",
            due_date=datetime.utcnow() + timedelta(days=7),
        )
        
        assert today_task.priority_score > later_task.priority_score
    
    @pytest.mark.asyncio
    async def test_create_task_with_value(self, db_session, test_user):
        """Test creating a task with estimated value affects priority."""
        service = TaskService(db_session)
        
        # High value task
        high_value = await service.create_task(
            user_id=test_user.id,
            title="High value task",
            estimated_value=1000.0,
        )
        
        # Low value task
        low_value = await service.create_task(
            user_id=test_user.id,
            title="Low value task",
            estimated_value=10.0,
        )
        
        assert high_value.priority_score > low_value.priority_score
    
    @pytest.mark.asyncio
    async def test_get_task(self, db_session, test_user):
        """Test retrieving a task by ID."""
        service = TaskService(db_session)
        
        created = await service.create_task(
            user_id=test_user.id,
            title="Test task",
        )
        
        fetched = await service.get_task(created.id, test_user.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.title == "Test task"
    
    @pytest.mark.asyncio
    async def test_get_task_wrong_user(self, db_session, test_user):
        """Test that tasks are isolated by user."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Test task",
        )
        
        # Should return None for different user
        other_user_id = uuid4()
        fetched = await service.get_task(task.id, other_user_id)
        assert fetched is None
    
    @pytest.mark.asyncio
    async def test_update_task(self, db_session, test_user):
        """Test updating task properties."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Original title",
        )
        
        updated = await service.update_task(
            task_id=task.id,
            user_id=test_user.id,
            title="Updated title",
            description="New description",
            estimated_value=500.0,
        )
        
        assert updated is not None
        assert updated.title == "Updated title"
        assert updated.description == "New description"
        assert updated.estimated_value == 500.0
    
    @pytest.mark.asyncio
    async def test_delete_task(self, db_session, test_user):
        """Test deleting a task."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="To be deleted",
        )
        
        deleted = await service.delete_task(task.id, test_user.id)
        assert deleted is True
        
        # Should not exist anymore
        fetched = await service.get_task(task.id, test_user.id)
        assert fetched is None
    
    @pytest.mark.asyncio
    async def test_complete_task(self, db_session, test_user):
        """Test completing a task."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Task to complete",
        )
        
        completed = await service.complete_task(
            task_id=task.id,
            user_id=test_user.id,
            completion_notes="Done!",
            actual_value=100.0,
        )
        
        assert completed is not None
        assert completed.status == TaskStatus.COMPLETED.value
        assert completed.completed_at is not None
        assert completed.completion_notes == "Done!"
        assert completed.actual_value == 100.0
    
    @pytest.mark.asyncio
    async def test_defer_task(self, db_session, test_user):
        """Test deferring a task."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Task to defer",
        )
        
        defer_until = datetime.utcnow() + timedelta(days=7)
        deferred = await service.defer_task(
            task_id=task.id,
            user_id=test_user.id,
            defer_until=defer_until,
        )
        
        assert deferred is not None
        assert deferred.status == TaskStatus.DEFERRED.value
        assert deferred.deferred_until is not None
    
    @pytest.mark.asyncio
    async def test_block_task(self, db_session, test_user):
        """Test blocking a task."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Task to block",
        )
        
        blocked = await service.block_task(
            task_id=task.id,
            user_id=test_user.id,
            blocked_by="Waiting for API key",
        )
        
        assert blocked is not None
        assert blocked.status == TaskStatus.BLOCKED.value
        assert blocked.blocked_by == "Waiting for API key"


class TestTaskQueries:
    """Tests for task query methods."""
    
    @pytest.mark.asyncio
    async def test_get_tasks_default(self, db_session, test_user):
        """Test default task listing (active only, sorted by priority)."""
        service = TaskService(db_session)
        
        # Create several tasks
        for i in range(5):
            await service.create_task(
                user_id=test_user.id,
                title=f"Task {i}",
                estimated_value=i * 100.0,
            )
        
        tasks = await service.get_tasks(test_user.id)
        assert len(tasks) == 5
        
        # Should be sorted by priority (descending)
        for i in range(1, len(tasks)):
            assert tasks[i-1].priority_score >= tasks[i].priority_score
    
    @pytest.mark.asyncio
    async def test_get_tasks_filter_by_status(self, db_session, test_user):
        """Test filtering tasks by status."""
        service = TaskService(db_session)
        
        # Create tasks with different statuses
        task1 = await service.create_task(user_id=test_user.id, title="Active")
        task2 = await service.create_task(user_id=test_user.id, title="To complete")
        await service.complete_task(task2.id, test_user.id)
        
        # Should only get non-completed by default
        tasks = await service.get_tasks(test_user.id)
        assert len(tasks) == 1
        assert tasks[0].title == "Active"
        
        # Include completed
        all_tasks = await service.get_tasks(test_user.id, include_completed=True)
        assert len(all_tasks) == 2
    
    @pytest.mark.asyncio
    async def test_get_tasks_filter_by_type(self, db_session, test_user):
        """Test filtering tasks by type."""
        service = TaskService(db_session)
        
        await service.create_task(
            user_id=test_user.id,
            title="Personal task",
            task_type=TaskType.PERSONAL,
        )
        await service.create_task(
            user_id=test_user.id,
            title="Campaign task",
            task_type=TaskType.CAMPAIGN_ACTION,
        )
        
        personal = await service.get_tasks(
            test_user.id, 
            task_types=[TaskType.PERSONAL],
        )
        assert len(personal) == 1
        assert personal[0].title == "Personal task"
    
    @pytest.mark.asyncio
    async def test_get_tasks_sort_by_due_date(self, db_session, test_user):
        """Test sorting tasks by due date."""
        service = TaskService(db_session)
        
        # Create tasks with different due dates
        await service.create_task(
            user_id=test_user.id,
            title="Due later",
            due_date=datetime.utcnow() + timedelta(days=7),
        )
        await service.create_task(
            user_id=test_user.id,
            title="Due soon",
            due_date=datetime.utcnow() + timedelta(days=1),
        )
        await service.create_task(
            user_id=test_user.id,
            title="No due date",
        )
        
        tasks = await service.get_tasks(
            test_user.id, 
            sort_by=TaskSortBy.DUE_DATE,
        )
        
        # Due soon should be first
        assert tasks[0].title == "Due soon"
        assert tasks[1].title == "Due later"
        # No due date should be last
        assert tasks[2].title == "No due date"
    
    @pytest.mark.asyncio
    async def test_get_actionable_tasks(self, db_session, test_user):
        """Test getting actionable tasks."""
        service = TaskService(db_session)
        
        # Create actionable task
        await service.create_task(
            user_id=test_user.id,
            title="Ready to work",
        )
        
        # Create blocked task
        blocked = await service.create_task(
            user_id=test_user.id,
            title="Blocked task",
        )
        await service.block_task(blocked.id, test_user.id, "Waiting")
        
        actionable = await service.get_actionable_tasks(test_user.id)
        assert len(actionable) == 1
        assert actionable[0].title == "Ready to work"
    
    @pytest.mark.asyncio
    async def test_get_overdue_tasks(self, db_session, test_user):
        """Test getting overdue tasks."""
        service = TaskService(db_session)
        
        # Create overdue task
        await service.create_task(
            user_id=test_user.id,
            title="Overdue",
            due_date=datetime.utcnow() - timedelta(days=1),
        )
        
        # Create future task
        await service.create_task(
            user_id=test_user.id,
            title="Future",
            due_date=datetime.utcnow() + timedelta(days=1),
        )
        
        overdue = await service.get_overdue_tasks(test_user.id)
        assert len(overdue) == 1
        assert overdue[0].title == "Overdue"
    
    @pytest.mark.asyncio
    async def test_get_due_soon(self, db_session, test_user):
        """Test getting tasks due soon."""
        service = TaskService(db_session)
        
        # Create task due in 12 hours
        await service.create_task(
            user_id=test_user.id,
            title="Due soon",
            due_date=datetime.utcnow() + timedelta(hours=12),
        )
        
        # Create task due in 3 days
        await service.create_task(
            user_id=test_user.id,
            title="Due later",
            due_date=datetime.utcnow() + timedelta(days=3),
        )
        
        due_soon = await service.get_due_soon(test_user.id, hours=24)
        assert len(due_soon) == 1
        assert due_soon[0].title == "Due soon"
    
    @pytest.mark.asyncio
    async def test_get_tasks_by_source(self, db_session, test_user):
        """Test getting tasks by source."""
        service = TaskService(db_session)
        
        campaign_id = uuid4()
        
        await service.create_campaign_task(
            user_id=test_user.id,
            campaign_id=campaign_id,
            title="Campaign action",
        )
        
        await service.create_task(
            user_id=test_user.id,
            title="Regular task",
        )
        
        campaign_tasks = await service.get_tasks_by_source(
            test_user.id,
            source_type="campaign",
            source_id=campaign_id,
        )
        
        assert len(campaign_tasks) == 1
        assert campaign_tasks[0].title == "Campaign action"
    
    @pytest.mark.asyncio
    async def test_get_task_counts(self, db_session, test_user):
        """Test getting task counts."""
        service = TaskService(db_session)
        
        # Create various tasks
        await service.create_task(user_id=test_user.id, title="Task 1")
        await service.create_task(user_id=test_user.id, title="Task 2")
        
        task3 = await service.create_task(user_id=test_user.id, title="Task 3")
        await service.complete_task(task3.id, test_user.id)
        
        task4 = await service.create_task(
            user_id=test_user.id,
            title="Overdue",
            due_date=datetime.utcnow() - timedelta(days=1),
        )
        
        counts = await service.get_task_counts(test_user.id)
        
        assert counts[TaskStatus.CREATED.value] == 3  # Including overdue
        assert counts[TaskStatus.COMPLETED.value] == 1
        assert counts["active"] == 3
        assert counts["overdue"] == 1


class TestPriorityCalculation:
    """Tests for priority score calculation."""
    
    @pytest.mark.asyncio
    async def test_overdue_task_high_priority(self, db_session, test_user):
        """Test that overdue tasks have very high priority."""
        service = TaskService(db_session)
        
        overdue = await service.create_task(
            user_id=test_user.id,
            title="Overdue task",
            due_date=datetime.utcnow() - timedelta(days=1),
        )
        
        # Overdue should have high urgency score (above median 50)
        assert overdue.priority_score >= 60
    
    @pytest.mark.asyncio
    async def test_blocked_task_low_priority(self, db_session, test_user):
        """Test that blocked tasks have lower priority."""
        service = TaskService(db_session)
        
        task = await service.create_task(
            user_id=test_user.id,
            title="Task to block",
        )
        original_priority = task.priority_score
        
        blocked = await service.block_task(
            task.id, 
            test_user.id, 
            "Waiting for dependencies",
        )
        
        assert blocked.priority_score < original_priority
    
    @pytest.mark.asyncio
    async def test_high_value_task_priority(self, db_session, test_user):
        """Test that high-value tasks have higher priority."""
        service = TaskService(db_session)
        
        high_value = await service.create_task(
            user_id=test_user.id,
            title="$10k opportunity",
            estimated_value=10000.0,
        )
        
        low_value = await service.create_task(
            user_id=test_user.id,
            title="$10 task",
            estimated_value=10.0,
        )
        
        assert high_value.priority_score > low_value.priority_score
    
    @pytest.mark.asyncio
    async def test_roi_affects_priority(self, db_session, test_user):
        """Test that value/effort ratio affects priority."""
        service = TaskService(db_session)
        
        # High ROI: $1000 for 30 minutes
        high_roi = await service.create_task(
            user_id=test_user.id,
            title="High ROI",
            estimated_value=1000.0,
            estimated_effort_minutes=30,
        )
        
        # Low ROI: $100 for 8 hours
        low_roi = await service.create_task(
            user_id=test_user.id,
            title="Low ROI",
            estimated_value=100.0,
            estimated_effort_minutes=480,
        )
        
        assert high_roi.priority_score > low_roi.priority_score
    
    @pytest.mark.asyncio
    async def test_recalculate_priorities(self, db_session, test_user):
        """Test bulk priority recalculation."""
        service = TaskService(db_session)
        
        # Create tasks
        for i in range(5):
            await service.create_task(
                user_id=test_user.id,
                title=f"Task {i}",
            )
        
        # Recalculate should return count of updated tasks
        # (may be 0 if priorities haven't changed significantly)
        count = await service.recalculate_priorities(test_user.id)
        assert isinstance(count, int)
        assert count >= 0


class TestAutomaticTaskCreation:
    """Tests for automatic task creation from sources."""
    
    @pytest.mark.asyncio
    async def test_create_campaign_task(self, db_session, test_user):
        """Test creating a task from a campaign."""
        service = TaskService(db_session)
        campaign_id = uuid4()
        
        task = await service.create_campaign_task(
            user_id=test_user.id,
            campaign_id=campaign_id,
            title="Review campaign results",
            estimated_value=500.0,
            context={"step": "review"},
        )
        
        assert task.task_type == TaskType.CAMPAIGN_ACTION.value
        assert task.source_type == "campaign"
        assert task.source_id == campaign_id
        assert task.source_context == {"step": "review"}
    
    @pytest.mark.asyncio
    async def test_create_opportunity_task(self, db_session, test_user):
        """Test creating a task from an opportunity."""
        service = TaskService(db_session)
        opportunity_id = uuid4()
        
        task = await service.create_opportunity_task(
            user_id=test_user.id,
            opportunity_id=opportunity_id,
            title="Research opportunity further",
            estimated_value=2000.0,
        )
        
        assert task.task_type == TaskType.FOLLOW_UP.value
        assert task.source_type == "opportunity"
        assert task.source_id == opportunity_id
    
    @pytest.mark.asyncio
    async def test_create_idea_task(self, db_session, test_user):
        """Test creating a task from an idea."""
        service = TaskService(db_session)
        idea_id = uuid4()
        
        task = await service.create_idea_task(
            user_id=test_user.id,
            idea_id=idea_id,
            title="Explore idea further",
        )
        
        assert task.task_type == TaskType.IDEA_ACTION.value
        assert task.source_type == "idea"
        assert task.source_id == idea_id
    
    @pytest.mark.asyncio
    async def test_create_review_task(self, db_session, test_user):
        """Test creating a review task."""
        service = TaskService(db_session)
        
        task = await service.create_review_task(
            user_id=test_user.id,
            title="Review proposal",
            source_type="proposal",
            source_id=uuid4(),
        )
        
        assert task.task_type == TaskType.REVIEW_REQUIRED.value
