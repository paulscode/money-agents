"""Unit tests for AgentSchedulerService."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from app.services.agent_scheduler_service import AgentSchedulerService, agent_scheduler_service
from app.models.agent_scheduler import (
    AgentDefinition,
    AgentRun,
    AgentEvent,
    AgentStatus,
    AgentRunStatus,
    BudgetPeriod,
)


class TestAgentSchedulerServiceAgentOperations:
    """Test agent CRUD operations."""
    
    @pytest.fixture
    def service(self):
        return AgentSchedulerService()
    
    @pytest.fixture
    def sample_agent(self, db_session):
        """Create a sample agent for testing."""
        agent = AgentDefinition(
            name="Test Agent",
            slug="test_agent",
            description="A test agent for unit tests",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
        return agent
    
    @pytest.mark.asyncio
    async def test_get_all_agents(self, service, db_session, sample_agent):
        """Test listing all agents."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        agents = await service.get_all_agents(db_session)
        
        assert len(agents) >= 1
        assert any(a.slug == "test_agent" for a in agents)
    
    @pytest.mark.asyncio
    async def test_get_all_agents_exclude_disabled(self, service, db_session, sample_agent):
        """Test that disabled agents are excluded by default."""
        sample_agent.is_enabled = False
        db_session.add(sample_agent)
        await db_session.commit()
        
        agents = await service.get_all_agents(db_session, include_disabled=False)
        
        assert not any(a.slug == "test_agent" for a in agents)
    
    @pytest.mark.asyncio
    async def test_get_all_agents_include_disabled(self, service, db_session, sample_agent):
        """Test including disabled agents when requested."""
        sample_agent.is_enabled = False
        db_session.add(sample_agent)
        await db_session.commit()
        
        agents = await service.get_all_agents(db_session, include_disabled=True)
        
        assert any(a.slug == "test_agent" for a in agents)
    
    @pytest.mark.asyncio
    async def test_get_agent_by_slug(self, service, db_session, sample_agent):
        """Test retrieving agent by slug."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.get_agent(db_session, slug="test_agent")
        
        assert result is not None
        assert result.name == "Test Agent"
        assert result.slug == "test_agent"
    
    @pytest.mark.asyncio
    async def test_get_agent_by_id(self, service, db_session, sample_agent):
        """Test retrieving agent by ID."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        result = await service.get_agent(db_session, agent_id=sample_agent.id)
        
        assert result is not None
        assert result.slug == "test_agent"
    
    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, service, db_session):
        """Test that non-existent agent returns None."""
        result = await service.get_agent(db_session, slug="nonexistent")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_update_agent_schedule(self, service, db_session, sample_agent):
        """Test updating agent schedule interval."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        updated = await service.update_agent(
            db_session, 
            sample_agent.id,
            schedule_interval_seconds=7200
        )
        
        assert updated is not None
        assert updated.schedule_interval_seconds == 7200
    
    @pytest.mark.asyncio
    async def test_update_agent_config(self, service, db_session, sample_agent):
        """Test updating agent config JSONB field."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        updated = await service.update_agent(
            db_session,
            sample_agent.id,
            config={"batch_size": 25, "custom_setting": True}
        )
        
        assert updated is not None
        assert updated.config["batch_size"] == 25
        assert updated.config["custom_setting"] is True
    
    @pytest.mark.asyncio
    async def test_update_agent_model_tier(self, service, db_session, sample_agent):
        """Test updating agent model tier."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        updated = await service.update_agent(
            db_session,
            sample_agent.id,
            default_model_tier="reasoning"
        )
        
        assert updated is not None
        assert updated.default_model_tier == "reasoning"
    
    @pytest.mark.asyncio
    async def test_update_agent_not_found(self, service, db_session):
        """Test updating non-existent agent returns None."""
        result = await service.update_agent(
            db_session,
            uuid4(),
            schedule_interval_seconds=7200
        )
        
        assert result is None


class TestAgentSchedulerServicePauseResume:
    """Test agent pause/resume operations."""
    
    @pytest.fixture
    def service(self):
        return AgentSchedulerService()
    
    @pytest.fixture
    def sample_agent(self):
        return AgentDefinition(
            name="Test Agent",
            slug="test_agent",
            description="A test agent",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
    
    @pytest.mark.asyncio
    async def test_pause_agent(self, service, db_session, sample_agent):
        """Test pausing an agent."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.pause_agent(db_session, "test_agent", reason="Testing pause")
        
        assert result is not None
        assert result.status == AgentStatus.PAUSED
        assert result.status_message == "Testing pause"
    
    @pytest.mark.asyncio
    async def test_pause_agent_no_reason(self, service, db_session, sample_agent):
        """Test pausing an agent without a reason."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.pause_agent(db_session, "test_agent")
        
        assert result is not None
        assert result.status == AgentStatus.PAUSED
        # Service sets a default message of "Paused by user"
        assert result.status_message == "Paused by user"
    
    @pytest.mark.asyncio
    async def test_pause_already_paused(self, service, db_session, sample_agent):
        """Test pausing an already paused agent."""
        sample_agent.status = AgentStatus.PAUSED
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.pause_agent(db_session, "test_agent", reason="New reason")
        
        assert result is not None
        assert result.status == AgentStatus.PAUSED
        assert result.status_message == "New reason"
    
    @pytest.mark.asyncio
    async def test_pause_agent_not_found(self, service, db_session):
        """Test pausing non-existent agent returns None."""
        result = await service.pause_agent(db_session, "nonexistent")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_resume_agent(self, service, db_session, sample_agent):
        """Test resuming a paused agent."""
        sample_agent.status = AgentStatus.PAUSED
        sample_agent.status_message = "Was paused"
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.resume_agent(db_session, "test_agent")
        
        assert result is not None
        assert result.status == AgentStatus.IDLE
        assert result.status_message is None
    
    @pytest.mark.asyncio
    async def test_resume_agent_not_paused(self, service, db_session, sample_agent):
        """Test resuming agent that's not paused."""
        sample_agent.status = AgentStatus.IDLE
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.resume_agent(db_session, "test_agent")
        
        assert result is not None
        assert result.status == AgentStatus.IDLE
    
    @pytest.mark.asyncio
    async def test_resume_agent_not_found(self, service, db_session):
        """Test resuming non-existent agent returns None."""
        result = await service.resume_agent(db_session, "nonexistent")
        
        assert result is None


class TestAgentSchedulerServiceBudget:
    """Test budget tracking operations."""
    
    @pytest.fixture
    def service(self):
        return AgentSchedulerService()
    
    @pytest.fixture
    def sample_agent(self):
        return AgentDefinition(
            name="Test Agent",
            slug="test_agent",
            description="A test agent",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_limit=10.0,
            budget_used=0.0,
            budget_warning_threshold=0.8,
        )
    
    @pytest.mark.asyncio
    async def test_check_budget_no_limit(self, service, db_session, sample_agent):
        """Test budget check with no limit set."""
        sample_agent.budget_limit = None
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.check_budget(db_session, "test_agent")
        
        assert result["limit"] is None
        assert result["is_exceeded"] is False
        assert result["remaining"] is None
    
    @pytest.mark.asyncio
    async def test_check_budget_under_limit(self, service, db_session, sample_agent):
        """Test budget check when under limit."""
        sample_agent.budget_used = 5.0  # 50% of $10 limit
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.check_budget(db_session, "test_agent")
        
        assert result["limit"] == 10.0
        assert result["used"] == 5.0
        assert result["remaining"] == 5.0
        assert result["percentage_used"] == 50.0
        assert result["is_exceeded"] is False
        assert result["is_warning"] is False
    
    @pytest.mark.asyncio
    async def test_check_budget_warning_threshold(self, service, db_session, sample_agent):
        """Test budget warning when at threshold."""
        sample_agent.budget_used = 8.5  # 85% of $10 limit, above 80% threshold
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.check_budget(db_session, "test_agent")
        
        assert result["percentage_used"] == 85.0
        assert result["is_warning"] is True
        assert result["is_exceeded"] is False
    
    @pytest.mark.asyncio
    async def test_check_budget_exceeded(self, service, db_session, sample_agent):
        """Test budget check when exceeded."""
        sample_agent.budget_used = 12.0  # Over $10 limit
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.check_budget(db_session, "test_agent")
        
        assert result["percentage_used"] == 120.0
        assert result["is_exceeded"] is True
        assert result["remaining"] == -2.0  # Negative when over
    
    @pytest.mark.asyncio
    async def test_update_budget_limit(self, service, db_session, sample_agent):
        """Test updating budget limit."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.update_budget(
            db_session,
            "test_agent",
            limit=20.0
        )
        
        assert result is not None
        assert result.budget_limit == 20.0
    
    @pytest.mark.asyncio
    async def test_update_budget_period_resets_used(self, service, db_session, sample_agent):
        """Test that changing period resets budget_used."""
        sample_agent.budget_used = 5.0
        sample_agent.budget_period = BudgetPeriod.DAILY
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.update_budget(
            db_session,
            "test_agent",
            period=BudgetPeriod.WEEKLY
        )
        
        assert result is not None
        assert result.budget_period == BudgetPeriod.WEEKLY
        assert result.budget_used == 0.0  # Reset when period changes
    
    @pytest.mark.asyncio
    async def test_update_budget_warning_threshold(self, service, db_session, sample_agent):
        """Test updating warning threshold."""
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.update_budget(
            db_session,
            "test_agent",
            warning_threshold=0.9
        )
        
        assert result is not None
        assert result.budget_warning_threshold == 0.9
    
    @pytest.mark.asyncio
    async def test_record_cost(self, service, db_session, sample_agent):
        """Test recording cost for an agent."""
        sample_agent.budget_used = 0.0
        sample_agent.total_cost_usd = 0.0
        sample_agent.total_tokens_used = 0
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.record_cost(
            db_session,
            "test_agent",
            cost=1.50,
            tokens=1000
        )
        
        assert result["total_used"] == 1.50
        assert result["budget_exceeded"] is False
        
        # Verify agent was updated
        agent = await service.get_agent(db_session, slug="test_agent")
        assert agent.budget_used == 1.50
        assert agent.total_cost_usd == 1.50
        assert agent.total_tokens_used == 1000
    
    @pytest.mark.asyncio
    async def test_record_cost_exceeds_budget(self, service, db_session, sample_agent):
        """Test recording cost that exceeds budget."""
        sample_agent.budget_used = 9.0  # Already at $9 of $10
        db_session.add(sample_agent)
        await db_session.commit()
        
        result = await service.record_cost(
            db_session,
            "test_agent",
            cost=2.0,  # This pushes us to $11
            tokens=500
        )
        
        assert result["total_used"] == 11.0
        assert result["budget_exceeded"] is True
        
        # Agent should be paused
        agent = await service.get_agent(db_session, slug="test_agent")
        assert agent.status == AgentStatus.BUDGET_EXCEEDED


class TestAgentSchedulerServiceRuns:
    """Test run lifecycle operations."""
    
    @pytest.fixture
    def service(self):
        return AgentSchedulerService()
    
    @pytest.fixture
    def sample_agent(self):
        return AgentDefinition(
            name="Test Agent",
            slug="test_agent",
            description="A test agent",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
    
    @pytest.mark.asyncio
    async def test_create_run(self, service, db_session, sample_agent):
        """Test creating a new agent run."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        run = await service.create_run(
            db_session,
            slug="test_agent",
            trigger_type="scheduled",
            trigger_reason="Scheduled execution"
        )
        
        assert run is not None
        assert run.agent_id == sample_agent.id
        assert run.status == AgentRunStatus.PENDING
        assert run.trigger_type == "scheduled"
    
    @pytest.mark.asyncio
    async def test_start_run(self, service, db_session, sample_agent):
        """Test starting a run."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        run = await service.create_run(
            db_session,
            slug="test_agent",
            trigger_type="manual"
        )
        
        started = await service.start_run(db_session, run.id)
        
        assert started is not None
        assert started.status == AgentRunStatus.RUNNING
        assert started.started_at is not None
    
    @pytest.mark.asyncio
    async def test_complete_run(self, service, db_session, sample_agent):
        """Test completing a run with stats."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        run = await service.create_run(
            db_session,
            slug="test_agent",
            trigger_type="manual"
        )
        await service.start_run(db_session, run.id)
        
        completed = await service.complete_run(
            db_session,
            run.id,
            items_processed=10,
            items_created=3,
            tokens_used=500,
            cost_usd=0.05,
            model_used="gpt-4o-mini",
        )
        
        assert completed is not None
        assert completed.status == AgentRunStatus.COMPLETED
        assert completed.completed_at is not None
        assert completed.items_processed == 10
        assert completed.items_created == 3
        assert completed.tokens_used == 500
        assert completed.cost_usd == 0.05
    
    @pytest.mark.asyncio
    async def test_fail_run(self, service, db_session, sample_agent):
        """Test failing a run with error message."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        run = await service.create_run(
            db_session,
            slug="test_agent",
            trigger_type="manual"
        )
        await service.start_run(db_session, run.id)
        
        failed = await service.fail_run(
            db_session,
            run.id,
            error_message="Connection timeout"
        )
        
        assert failed is not None
        assert failed.status == AgentRunStatus.FAILED
        assert failed.error_message == "Connection timeout"
        assert failed.completed_at is not None
    
    @pytest.mark.asyncio
    async def test_get_recent_runs(self, service, db_session, sample_agent):
        """Test getting recent runs for an agent."""
        db_session.add(sample_agent)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        
        # Create a few runs
        for i in range(3):
            run = await service.create_run(
                db_session,
                slug="test_agent",
                trigger_type="scheduled"
            )
            await service.start_run(db_session, run.id)
            await service.complete_run(db_session, run.id)
        
        runs = await service.get_recent_runs(db_session, slug="test_agent", limit=10)
        
        assert len(runs) == 3
        # Should be ordered by created_at desc
        assert all(r.status == AgentRunStatus.COMPLETED for r in runs)
    
    @pytest.mark.asyncio
    async def test_get_recent_runs_all_agents(self, service, db_session, sample_agent):
        """Test getting recent runs across all agents."""
        db_session.add(sample_agent)
        
        # Create another agent
        agent2 = AgentDefinition(
            name="Test Agent 2",
            slug="test_agent_2",
            description="Another test agent",
            schedule_interval_seconds=1800,
            default_model_tier="reasoning",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
        db_session.add(agent2)
        await db_session.commit()
        await db_session.refresh(sample_agent)
        await db_session.refresh(agent2)
        
        # Create runs for both agents
        run1 = await service.create_run(
            db_session,
            slug="test_agent",
            trigger_type="scheduled"
        )
        await service.start_run(db_session, run1.id)
        await service.complete_run(db_session, run1.id)
        
        run2 = await service.create_run(
            db_session,
            slug="test_agent_2",
            trigger_type="scheduled"
        )
        await service.start_run(db_session, run2.id)
        await service.complete_run(db_session, run2.id)
        
        runs = await service.get_recent_runs(db_session, limit=10)
        
        assert len(runs) == 2


class TestAgentSchedulerServiceBudgetPeriodReset:
    """Test budget period reset time calculations."""
    
    @pytest.fixture
    def service(self):
        return AgentSchedulerService()
    
    def test_get_next_reset_time_hourly(self, service):
        """Test hourly reset calculation."""
        from datetime import timezone
        now = datetime(2026, 1, 29, 14, 30, 0, tzinfo=timezone.utc)
        with patch('app.services.agent_scheduler_service.utc_now', return_value=now):
            next_reset = service._get_next_reset_time(BudgetPeriod.HOURLY)
            
            # Should be next hour
            assert next_reset.hour == 15
            assert next_reset.minute == 0
    
    def test_get_next_reset_time_daily(self, service):
        """Test daily reset calculation."""
        from datetime import timezone
        now = datetime(2026, 1, 29, 14, 30, 0, tzinfo=timezone.utc)
        with patch('app.services.agent_scheduler_service.utc_now', return_value=now):
            next_reset = service._get_next_reset_time(BudgetPeriod.DAILY)
            
            # Should be next day at midnight
            assert next_reset.day == 30
            assert next_reset.hour == 0
    
    def test_get_next_reset_time_weekly(self, service):
        """Test weekly reset calculation."""
        # January 29, 2026 is a Thursday (weekday 3)
        from datetime import timezone
        now = datetime(2026, 1, 29, 14, 30, 0, tzinfo=timezone.utc)
        with patch('app.services.agent_scheduler_service.utc_now', return_value=now):
            next_reset = service._get_next_reset_time(BudgetPeriod.WEEKLY)
            
            # Should be next Monday
            assert next_reset.weekday() == 0  # Monday
    
    def test_get_next_reset_time_monthly(self, service):
        """Test monthly reset calculation."""
        from datetime import timezone
        now = datetime(2026, 1, 29, 14, 30, 0, tzinfo=timezone.utc)
        with patch('app.services.agent_scheduler_service.utc_now', return_value=now):
            next_reset = service._get_next_reset_time(BudgetPeriod.MONTHLY)
            
            # Should be first of next month
            assert next_reset.month == 2
            assert next_reset.day == 1
