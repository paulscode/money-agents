"""Integration tests for Agent Management API endpoints.

These tests use the real database to test the full request/response cycle.
Run with: pytest tests/integration/test_agent_management.py -v

Note: These tests require a real PostgreSQL database and are skipped by default.
Run with: pytest -m integration tests/integration/
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.agent_scheduler import (
    AgentDefinition,
    AgentRun,
    AgentStatus,
    AgentRunStatus,
    BudgetPeriod,
)


@pytest.mark.integration
@pytest.mark.skip(reason="Integration tests require real PostgreSQL - run with pytest -m integration")
class TestAgentManagementIntegration:
    """Integration tests for agent management endpoints."""

    @pytest_asyncio.fixture
    async def auth_headers(self, test_admin_user, async_client):
        """Get authentication headers for admin user."""
        response = await async_client.post(
            "/api/v1/auth/login",
            data={
                "username": test_admin_user.email,
                "password": "testpassword123",
            },
        )
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest_asyncio.fixture
    async def user_headers(self, test_user, async_client):
        """Get authentication headers for regular user."""
        response = await async_client.post(
            "/api/v1/auth/login",
            data={
                "username": test_user.email,
                "password": "testpassword123",
            },
        )
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    @pytest_asyncio.fixture
    async def test_agent(self, db_session: AsyncSession):
        """Create a test agent."""
        agent = AgentDefinition(
            name="Integration Test Agent",
            slug="integration_test_agent",
            description="An agent for integration testing",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=True,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_limit=10.0,
            budget_warning_threshold=0.8,
        )
        db_session.add(agent)
        await db_session.commit()
        await db_session.refresh(agent)
        return agent

    # =========================================================================
    # List Agents
    # =========================================================================

    @pytest.mark.asyncio
    async def test_list_agents_authenticated(self, async_client, auth_headers, test_agent):
        """Test listing agents with authentication."""
        response = await async_client.get(
            "/api/v1/agents/scheduler",
            headers=auth_headers,
        )

        assert response.status_code == 200
        agents = response.json()
        assert isinstance(agents, list)
        assert any(a["slug"] == "integration_test_agent" for a in agents)

    @pytest.mark.asyncio
    async def test_list_agents_unauthenticated(self, async_client):
        """Test listing agents without authentication returns 401."""
        response = await async_client.get("/api/v1/agents/scheduler")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_agents_excludes_disabled(self, async_client, auth_headers, db_session):
        """Test that disabled agents are excluded by default."""
        # Create a disabled agent
        disabled_agent = AgentDefinition(
            name="Disabled Agent",
            slug="disabled_agent",
            description="A disabled agent",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=False,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
        db_session.add(disabled_agent)
        await db_session.commit()

        response = await async_client.get(
            "/api/v1/agents/scheduler",
            headers=auth_headers,
        )

        assert response.status_code == 200
        agents = response.json()
        assert not any(a["slug"] == "disabled_agent" for a in agents)

    @pytest.mark.asyncio
    async def test_list_agents_include_disabled(self, async_client, auth_headers, db_session):
        """Test including disabled agents when requested."""
        # Create a disabled agent
        disabled_agent = AgentDefinition(
            name="Disabled Agent 2",
            slug="disabled_agent_2",
            description="Another disabled agent",
            schedule_interval_seconds=3600,
            default_model_tier="fast",
            is_enabled=False,
            status=AgentStatus.IDLE,
            budget_period=BudgetPeriod.DAILY,
            budget_warning_threshold=0.8,
        )
        db_session.add(disabled_agent)
        await db_session.commit()

        response = await async_client.get(
            "/api/v1/agents/scheduler?include_disabled=true",
            headers=auth_headers,
        )

        assert response.status_code == 200
        agents = response.json()
        assert any(a["slug"] == "disabled_agent_2" for a in agents)

    # =========================================================================
    # Get Single Agent
    # =========================================================================

    @pytest.mark.asyncio
    async def test_get_agent_found(self, async_client, auth_headers, test_agent):
        """Test getting a single agent by slug."""
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["slug"] == "integration_test_agent"
        assert agent["name"] == "Integration Test Agent"
        assert agent["schedule_interval_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, async_client, auth_headers):
        """Test getting non-existent agent returns 404."""
        response = await async_client.get(
            "/api/v1/agents/scheduler/nonexistent_agent",
            headers=auth_headers,
        )

        assert response.status_code == 404

    # =========================================================================
    # Update Agent (Admin Only)
    # =========================================================================

    @pytest.mark.asyncio
    async def test_update_agent_admin_only(self, async_client, user_headers, test_agent):
        """Test that updating agent requires admin privileges."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=user_headers,
            json={"schedule_interval_seconds": 7200},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_update_agent_schedule(self, async_client, auth_headers, test_agent):
        """Test updating agent schedule interval."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
            json={"schedule_interval_seconds": 7200},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["schedule_interval_seconds"] == 7200

    @pytest.mark.asyncio
    async def test_update_agent_model_tier(self, async_client, auth_headers, test_agent):
        """Test updating agent model tier."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
            json={"default_model_tier": "reasoning"},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["default_model_tier"] == "reasoning"

    @pytest.mark.asyncio
    async def test_update_agent_config(self, async_client, auth_headers, test_agent):
        """Test updating agent config."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
            json={"config": {"batch_size": 25, "custom_option": True}},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["config"]["batch_size"] == 25
        assert agent["config"]["custom_option"] is True

    # =========================================================================
    # Pause/Resume Agent
    # =========================================================================

    @pytest.mark.asyncio
    async def test_pause_agent(self, async_client, auth_headers, test_agent):
        """Test pausing an agent."""
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{test_agent.slug}/pause",
            headers=auth_headers,
        )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "paused" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_pause_agent_with_reason(self, async_client, auth_headers, test_agent):
        """Test pausing an agent with a reason."""
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{test_agent.slug}/pause?reason=Maintenance%20window",
            headers=auth_headers,
        )

        assert response.status_code == 200
        
        # Verify agent status
        get_response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
        )
        agent = get_response.json()
        assert agent["status"] == "paused"
        assert agent["status_message"] == "Maintenance window"

    @pytest.mark.asyncio
    async def test_resume_agent(self, async_client, auth_headers, test_agent, db_session):
        """Test resuming a paused agent."""
        # First pause the agent
        test_agent.status = AgentStatus.PAUSED
        await db_session.commit()

        response = await async_client.post(
            f"/api/v1/agents/scheduler/{test_agent.slug}/resume",
            headers=auth_headers,
        )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True

        # Verify agent status
        get_response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}",
            headers=auth_headers,
        )
        agent = get_response.json()
        assert agent["status"] == "idle"

    # =========================================================================
    # Trigger Agent
    # =========================================================================

    @pytest.mark.asyncio
    async def test_trigger_agent(self, async_client, auth_headers, test_agent):
        """Test manually triggering an agent run."""
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{test_agent.slug}/trigger",
            headers=auth_headers,
            json={"reason": "Manual test run"},
        )

        assert response.status_code == 200
        result = response.json()
        assert result["success"] is True
        assert "run_id" in result

    @pytest.mark.asyncio
    async def test_trigger_agent_already_running(self, async_client, auth_headers, test_agent, db_session):
        """Test triggering agent that's already running."""
        test_agent.status = AgentStatus.RUNNING
        await db_session.commit()

        response = await async_client.post(
            f"/api/v1/agents/scheduler/{test_agent.slug}/trigger",
            headers=auth_headers,
            json={"reason": "Should fail"},
        )

        # Should return conflict or error
        assert response.status_code in [409, 400]

    # =========================================================================
    # Budget Management
    # =========================================================================

    @pytest.mark.asyncio
    async def test_get_budget(self, async_client, auth_headers, test_agent):
        """Test getting agent budget info."""
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}/budget",
            headers=auth_headers,
        )

        assert response.status_code == 200
        budget = response.json()
        assert budget["agent_slug"] == "integration_test_agent"
        assert budget["budget_limit"] == 10.0
        assert budget["budget_period"] == "daily"
        assert "budget_used" in budget
        assert "budget_remaining" in budget

    @pytest.mark.asyncio
    async def test_update_budget_limit(self, async_client, auth_headers, test_agent):
        """Test updating budget limit."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}/budget",
            headers=auth_headers,
            json={"budget_limit": 25.0},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["budget_limit"] == 25.0

    @pytest.mark.asyncio
    async def test_update_budget_period(self, async_client, auth_headers, test_agent):
        """Test updating budget period."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}/budget",
            headers=auth_headers,
            json={"budget_period": "weekly"},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["budget_period"] == "weekly"

    @pytest.mark.asyncio
    async def test_update_budget_warning_threshold(self, async_client, auth_headers, test_agent):
        """Test updating budget warning threshold."""
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{test_agent.slug}/budget",
            headers=auth_headers,
            json={"warning_threshold": 0.9},
        )

        assert response.status_code == 200
        agent = response.json()
        assert agent["budget_warning_threshold"] == 0.9

    # =========================================================================
    # Run History & Statistics
    # =========================================================================

    @pytest.mark.asyncio
    async def test_get_runs(self, async_client, auth_headers, test_agent, db_session):
        """Test getting run history for an agent."""
        # Create some runs
        for i in range(3):
            run = AgentRun(
                agent_id=test_agent.id,
                trigger_type="scheduled",
                status=AgentRunStatus.COMPLETED,
                items_processed=10 + i,
            )
            db_session.add(run)
        await db_session.commit()

        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}/runs",
            headers=auth_headers,
        )

        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 3

    @pytest.mark.asyncio
    async def test_get_runs_with_limit(self, async_client, auth_headers, test_agent, db_session):
        """Test limiting run history results."""
        # Create 5 runs
        for i in range(5):
            run = AgentRun(
                agent_id=test_agent.id,
                trigger_type="scheduled",
                status=AgentRunStatus.COMPLETED,
            )
            db_session.add(run)
        await db_session.commit()

        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}/runs?limit=2",
            headers=auth_headers,
        )

        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 2

    @pytest.mark.asyncio
    async def test_get_stats(self, async_client, auth_headers, test_agent, db_session):
        """Test getting agent statistics."""
        # Create some runs with varying stats
        from datetime import datetime, timedelta

        for i in range(5):
            run = AgentRun(
                agent_id=test_agent.id,
                trigger_type="scheduled",
                status=AgentRunStatus.COMPLETED,
                duration_seconds=100 + (i * 20),
                items_processed=10 + i,
                tokens_used=500 + (i * 100),
                cost_usd=0.05 + (i * 0.01),
                created_at=datetime.utcnow() - timedelta(days=i),
            )
            db_session.add(run)
        await db_session.commit()

        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        stats = response.json()
        assert stats["agent_slug"] == "integration_test_agent"
        assert stats["total_runs"] == 5
        assert stats["completed_runs"] == 5
        assert stats["failed_runs"] == 0
        assert stats["avg_duration_seconds"] is not None
        assert stats["total_tokens_used"] > 0
        assert stats["total_cost_usd"] > 0

    @pytest.mark.asyncio
    async def test_get_stats_no_runs(self, async_client, auth_headers, test_agent):
        """Test getting stats when no runs exist."""
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{test_agent.slug}/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        stats = response.json()
        assert stats["total_runs"] == 0
        assert stats["avg_duration_seconds"] is None

    # =========================================================================
    # Full Lifecycle Test
    # =========================================================================

    @pytest.mark.asyncio
    async def test_full_agent_lifecycle(self, async_client, auth_headers, test_agent, db_session):
        """Test the complete agent management lifecycle."""
        slug = test_agent.slug

        # 1. List agents - should include our test agent
        response = await async_client.get(
            "/api/v1/agents/scheduler",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert any(a["slug"] == slug for a in response.json())

        # 2. Get agent details
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "idle"

        # 3. Update schedule
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{slug}",
            headers=auth_headers,
            json={"schedule_interval_seconds": 1800},
        )
        assert response.status_code == 200
        assert response.json()["schedule_interval_seconds"] == 1800

        # 4. Update budget
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{slug}/budget",
            headers=auth_headers,
            json={"budget_limit": 50.0, "budget_period": "weekly"},
        )
        assert response.status_code == 200
        assert response.json()["budget_limit"] == 50.0

        # 5. Pause agent
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{slug}/pause",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify paused
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}",
            headers=auth_headers,
        )
        assert response.json()["status"] == "paused"

        # 6. Resume agent
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{slug}/resume",
            headers=auth_headers,
        )
        assert response.status_code == 200

        # Verify resumed
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}",
            headers=auth_headers,
        )
        assert response.json()["status"] == "idle"

        # 7. Trigger manual run
        response = await async_client.post(
            f"/api/v1/agents/scheduler/{slug}/trigger",
            headers=auth_headers,
            json={"reason": "Integration test"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

        # 8. Check runs
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}/runs",
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1

        # 9. Check stats
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}/stats",
            headers=auth_headers,
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_budget_tracking_flow(self, async_client, auth_headers, test_agent, db_session):
        """Test budget tracking with cost recording."""
        slug = test_agent.slug

        # Set a low budget
        response = await async_client.patch(
            f"/api/v1/agents/scheduler/{slug}/budget",
            headers=auth_headers,
            json={"budget_limit": 1.0, "warning_threshold": 0.5},
        )
        assert response.status_code == 200

        # Check initial budget
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}/budget",
            headers=auth_headers,
        )
        assert response.status_code == 200
        budget = response.json()
        assert budget["budget_used"] == 0.0
        assert budget["is_exceeded"] is False
        assert budget["warning_triggered"] is False

        # Record some cost via a run (simulate externally)
        test_agent.budget_used = 0.6
        await db_session.commit()

        # Check budget again - should show warning
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}/budget",
            headers=auth_headers,
        )
        budget = response.json()
        assert budget["budget_used"] == 0.6
        assert budget["warning_triggered"] is True

        # Exceed budget
        test_agent.budget_used = 1.2
        test_agent.status = AgentStatus.BUDGET_EXCEEDED
        await db_session.commit()

        # Check that agent is paused
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}",
            headers=auth_headers,
        )
        agent = response.json()
        assert agent["status"] == "budget_exceeded"

        # Budget info should show exceeded
        response = await async_client.get(
            f"/api/v1/agents/scheduler/{slug}/budget",
            headers=auth_headers,
        )
        budget = response.json()
        assert budget["is_exceeded"] is True
