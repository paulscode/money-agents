"""Tests for StreamExecutorService.

Covers:
- Task dependency checking
- Input substitution
- Stream execution flow and status transitions
- Individual task type execution (checkpoint, input, parallel_gate)
- execute_ready_streams orchestration
- provide_user_input helper
- get_stream_execution_summary helper
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

from app.models import (
    Campaign, Proposal, TaskStream, CampaignTask, UserInputRequest, User,
    TaskStreamStatus, TaskStatus, TaskType, InputStatus, InputPriority,
    InputType, CampaignStatus, ProposalStatus,
)
from app.services.stream_executor_service import (
    StreamExecutorService,
    get_stream_execution_summary,
    provide_user_input,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def user(db_session):
    from app.core.security import get_password_hash
    u = User(
        username="executor_user",
        email="exec@test.com",
        password_hash=get_password_hash("pw123456"),
        role="user",
        is_active=True,
    )
    db_session.add(u)
    await db_session.flush()
    return u


@pytest_asyncio.fixture
async def proposal(db_session, user):
    p = Proposal(
        title="Test Proposal",
        summary="A test",
        detailed_description="Detailed",
        initial_budget=100.0,
        status=ProposalStatus.APPROVED,
        user_id=user.id,
        risk_level="medium",
        risk_description="Moderate risk",
        stop_loss_threshold={"max_loss": 50},
        success_criteria={"target": "test"},
        required_tools={},
        required_inputs={},
    )
    db_session.add(p)
    await db_session.flush()
    return p


@pytest_asyncio.fixture
async def campaign(db_session, user, proposal):
    c = Campaign(
        proposal_id=proposal.id,
        user_id=user.id,
        status=CampaignStatus.ACTIVE,
        budget_allocated=100.0,
        success_metrics={"target": "test"},
        requirements_checklist={},
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def ready_stream(db_session, campaign):
    s = TaskStream(
        campaign_id=campaign.id,
        name="Research",
        description="Research stream",
        order_index=0,
        status=TaskStreamStatus.READY,
        tasks_total=2,
    )
    db_session.add(s)
    await db_session.flush()
    return s


@pytest_asyncio.fixture
async def checkpoint_task(db_session, ready_stream, campaign):
    t = CampaignTask(
        stream_id=ready_stream.id,
        campaign_id=campaign.id,
        name="Checkpoint 1",
        description="A checkpoint",
        order_index=0,
        task_type=TaskType.CHECKPOINT,
        status=TaskStatus.PENDING,
        is_critical=True,
    )
    db_session.add(t)
    await db_session.flush()
    return t


# ---------------------------------------------------------------------------
# _check_task_dependencies
# ---------------------------------------------------------------------------

class TestCheckTaskDependencies:
    """Tests for dependency resolution logic."""

    @pytest.mark.asyncio
    async def test_no_dependencies_met(self, db_session, campaign, ready_stream):
        """Task with no deps should pass."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="NoDep",
            description="x",
            order_index=0,
            task_type=TaskType.CHECKPOINT,
            status=TaskStatus.PENDING,
            is_critical=True,
        )
        met, reason = await svc._check_task_dependencies(task, {}, {})
        assert met is True
        assert reason is None

    @pytest.mark.asyncio
    async def test_task_dependency_blocking(self, db_session, campaign, ready_stream):
        """An unmet task dependency blocks the task."""
        svc = StreamExecutorService(db_session)
        dep_id = str(uuid4())
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="HasDep",
            description="x",
            order_index=1,
            task_type=TaskType.CHECKPOINT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_tasks=[dep_id],
        )
        met, reason = await svc._check_task_dependencies(task, {}, {})
        assert met is False
        assert "dependent task" in reason.lower()

    @pytest.mark.asyncio
    async def test_task_dependency_satisfied(self, db_session, campaign, ready_stream):
        """A completed task dependency lets the task proceed."""
        svc = StreamExecutorService(db_session)
        dep_id = str(uuid4())
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="DepMet",
            description="x",
            order_index=1,
            task_type=TaskType.CHECKPOINT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_tasks=[dep_id],
        )
        met, reason = await svc._check_task_dependencies(
            task, {dep_id: True}, {}
        )
        assert met is True

    @pytest.mark.asyncio
    async def test_input_dependency_blocking(self, db_session, campaign, ready_stream):
        """Missing input blocks the task."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="NeedsInput",
            description="x",
            order_index=0,
            task_type=TaskType.USER_INPUT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_inputs=["api_key"],
        )
        met, reason = await svc._check_task_dependencies(task, {}, {})
        assert met is False
        assert "input" in reason.lower()

    @pytest.mark.asyncio
    async def test_input_dependency_satisfied(self, db_session, campaign, ready_stream):
        """Provided input allows task to proceed."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="HasInput",
            description="x",
            order_index=0,
            task_type=TaskType.USER_INPUT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_inputs=["api_key"],
        )
        met, reason = await svc._check_task_dependencies(
            task, {}, {"api_key": "sk-123"}
        )
        assert met is True


# ---------------------------------------------------------------------------
# execute_stream – checkpoint task
# ---------------------------------------------------------------------------

class TestExecuteStream:

    @pytest.mark.asyncio
    async def test_checkpoint_always_succeeds(self, db_session, campaign, ready_stream, checkpoint_task):
        """Checkpoint tasks should complete immediately."""
        svc = StreamExecutorService(db_session)
        result = await svc.execute_stream(ready_stream)

        assert result["status"] == TaskStreamStatus.COMPLETED.value or result["tasks_completed"] >= 1
        assert result["tasks_failed"] == 0

    @pytest.mark.asyncio
    async def test_stream_marks_in_progress(self, db_session, campaign, ready_stream, checkpoint_task):
        """Stream should be IN_PROGRESS during execution then transition."""
        svc = StreamExecutorService(db_session)
        await svc.execute_stream(ready_stream)
        # After execution the stream should not be READY any more
        assert ready_stream.status != TaskStreamStatus.READY

    @pytest.mark.asyncio
    async def test_blocked_task_stays_blocked(self, db_session, campaign, ready_stream):
        """A task whose deps are not met should be marked BLOCKED."""
        dep_id = str(uuid4())
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="BlockedTask",
            description="x",
            order_index=0,
            task_type=TaskType.CHECKPOINT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_tasks=[dep_id],
        )
        db_session.add(task)
        await db_session.flush()

        svc = StreamExecutorService(db_session)
        result = await svc.execute_stream(ready_stream)
        assert result["tasks_blocked"] >= 1


# ---------------------------------------------------------------------------
# execute_ready_streams
# ---------------------------------------------------------------------------

class TestExecuteReadyStreams:

    @pytest.mark.asyncio
    async def test_no_ready_streams(self, db_session, campaign):
        """When no streams are ready, summary says 0 executed."""
        svc = StreamExecutorService(db_session)
        summary = await svc.execute_ready_streams(campaign)
        assert summary["executed"] == 0

    @pytest.mark.asyncio
    async def test_executes_ready_stream(self, db_session, campaign, ready_stream, checkpoint_task):
        """Ready streams should be picked up and executed."""
        svc = StreamExecutorService(db_session)
        summary = await svc.execute_ready_streams(campaign)
        assert summary["executed"] >= 1

    @pytest.mark.asyncio
    async def test_max_parallel_limits(self, db_session, campaign):
        """max_parallel should cap how many streams are executed."""
        for i in range(5):
            s = TaskStream(
                campaign_id=campaign.id,
                name=f"Stream {i}",
                description="x",
                order_index=i,
                status=TaskStreamStatus.READY,
                tasks_total=0,
            )
            db_session.add(s)
        await db_session.flush()

        svc = StreamExecutorService(db_session)
        summary = await svc.execute_ready_streams(campaign, max_parallel=2)
        assert summary["executed"] == 2


# ---------------------------------------------------------------------------
# _check_input_task / _check_parallel_gate
# ---------------------------------------------------------------------------

class TestInputAndGateTasks:

    @pytest.mark.asyncio
    async def test_input_task_fails_when_missing(self, db_session, campaign, ready_stream):
        """User input task returns False when required input is missing."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="InputTask",
            description="x",
            order_index=0,
            task_type=TaskType.USER_INPUT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_inputs=["brand_name"],
        )
        result = await svc._check_input_task(task, {})
        assert result is False

    @pytest.mark.asyncio
    async def test_input_task_succeeds_when_provided(self, db_session, campaign, ready_stream):
        """User input task returns True when required input exists."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="InputTask",
            description="x",
            order_index=0,
            task_type=TaskType.USER_INPUT,
            status=TaskStatus.PENDING,
            is_critical=True,
            depends_on_inputs=["brand_name"],
        )
        result = await svc._check_input_task(task, {"brand_name": "TestCo"})
        assert result is True

    @pytest.mark.asyncio
    async def test_parallel_gate_no_deps(self, db_session, campaign, ready_stream):
        """Parallel gate with no deps passes."""
        svc = StreamExecutorService(db_session)
        task = CampaignTask(
            stream_id=ready_stream.id,
            campaign_id=campaign.id,
            name="Gate",
            description="x",
            order_index=0,
            task_type=TaskType.PARALLEL_GATE,
            status=TaskStatus.PENDING,
            is_critical=True,
        )
        result = await svc._check_parallel_gate(task)
        assert result is True


# ---------------------------------------------------------------------------
# provide_user_input helper
# ---------------------------------------------------------------------------

class TestProvideUserInput:

    @pytest.mark.asyncio
    async def test_provide_input_returns_none_for_missing(self, db_session, campaign, user):
        """Returns None when input_key doesn't exist."""
        result = await provide_user_input(
            db_session, campaign.id, "nonexistent", "value", user.id
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_provide_input_marks_provided(self, db_session, campaign, user):
        """Providing input updates the request."""
        req = UserInputRequest(
            campaign_id=campaign.id,
            input_key="api_key",
            input_type=InputType.CREDENTIALS,
            title="API Key",
            description="Enter your API key",
            priority=InputPriority.BLOCKING,
            status=InputStatus.PENDING,
        )
        db_session.add(req)
        await db_session.flush()

        result = await provide_user_input(
            db_session, campaign.id, "api_key", "sk-abc", user.id
        )
        assert result is not None
        assert result.status == InputStatus.PROVIDED
        assert result.value == "sk-abc"


# ---------------------------------------------------------------------------
# get_stream_execution_summary helper
# ---------------------------------------------------------------------------

class TestStreamExecutionSummary:

    @pytest.mark.asyncio
    async def test_empty_campaign(self, db_session, campaign):
        """Summary of a campaign with no streams."""
        summary = await get_stream_execution_summary(db_session, campaign.id)
        assert summary["total_streams"] == 0
        assert summary["overall_progress_pct"] == 0

    @pytest.mark.asyncio
    async def test_summary_counts(self, db_session, campaign):
        """Summary should tally stream/task counts correctly."""
        s = TaskStream(
            campaign_id=campaign.id,
            name="S1",
            description="x",
            order_index=0,
            status=TaskStreamStatus.COMPLETED,
            tasks_total=3,
            tasks_completed=3,
        )
        db_session.add(s)
        await db_session.flush()
        # Add tasks so selectinload works
        for i in range(3):
            t = CampaignTask(
                stream_id=s.id,
                campaign_id=campaign.id,
                name=f"T{i}",
                description="x",
                order_index=i,
                task_type=TaskType.CHECKPOINT,
                status=TaskStatus.COMPLETED,
                is_critical=True,
            )
            db_session.add(t)
        await db_session.flush()

        summary = await get_stream_execution_summary(db_session, campaign.id)
        assert summary["total_streams"] == 1
        assert summary["completed_streams"] == 1
        assert summary["total_tasks"] == 3
        assert summary["completed_tasks"] == 3
        assert summary["overall_progress_pct"] == 100.0
