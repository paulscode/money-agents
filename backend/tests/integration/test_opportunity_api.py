"""Integration tests for Opportunity Scout API endpoints.

Note: These tests require a real PostgreSQL database and are skipped by default.
Run with: pytest -m integration tests/integration/
"""
import pytest
import pytest_asyncio
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from app.main import app
from app.core.database import get_db
from app.models import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    DiscoveryStrategy,
    StrategyStatus,
)


# Mark entire module as integration tests and skip by default
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skip(reason="Integration tests require real PostgreSQL - run with pytest -m integration")
]


@pytest_asyncio.fixture
async def client(db_session):
    """Create test client with database override."""
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
    
    app.dependency_overrides.clear()


class TestOpportunityEndpoints:
    """Test opportunity API endpoints."""
    
    @pytest.mark.asyncio
    async def test_list_opportunities_empty(self, client):
        """Test listing opportunities when none exist."""
        response = await client.get("/api/opportunities")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["opportunities"] == []
        assert "hopper_status" in data
    
    @pytest.mark.asyncio
    async def test_list_opportunities_with_data(self, client, db_session):
        """Test listing opportunities with existing data."""
        # Create test opportunity
        opp = Opportunity(
            title="Test Opportunity",
            summary="A test opportunity",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            overall_score=0.75,
            ranking_tier=RankingTier.PROMISING,
            rank_position=1,
        )
        db_session.add(opp)
        await db_session.commit()
        
        response = await client.get("/api/opportunities")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["opportunities"][0]["title"] == "Test Opportunity"
    
    @pytest.mark.asyncio
    async def test_get_opportunity_detail(self, client, db_session):
        """Test getting single opportunity detail."""
        opp = Opportunity(
            title="Detailed Opportunity",
            summary="Full details",
            opportunity_type=OpportunityType.SERVICE,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            detailed_analysis="This is a detailed analysis...",
            score_breakdown={"market_validation": 0.8, "competition": 0.6},
            overall_score=0.7,
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        response = await client.get(f"/api/opportunities/{opp.id}")
        
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Detailed Opportunity"
        assert data["detailed_analysis"] == "This is a detailed analysis..."
    
    @pytest.mark.asyncio
    async def test_get_opportunity_not_found(self, client):
        """Test getting non-existent opportunity."""
        fake_id = uuid4()
        response = await client.get(f"/api/opportunities/{fake_id}")
        
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_approve_opportunity(self, client, db_session):
        """Test approving an opportunity."""
        opp = Opportunity(
            title="To Approve",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        response = await client.post(
            f"/api/opportunities/{opp.id}/approve",
            json={"notes": "Great opportunity!"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "approved"
        assert data["user_feedback"] == "Great opportunity!"
    
    @pytest.mark.asyncio
    async def test_dismiss_opportunity(self, client, db_session):
        """Test dismissing an opportunity."""
        opp = Opportunity(
            title="To Dismiss",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        response = await client.post(
            f"/api/opportunities/{opp.id}/dismiss",
            json={"notes": "Not relevant"},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "dismissed"
    
    @pytest.mark.asyncio
    async def test_bulk_dismiss(self, client, db_session):
        """Test bulk dismiss endpoint."""
        # Create several opportunities
        for i in range(5):
            opp = Opportunity(
                title=f"Opp {i}",
                summary="Test",
                opportunity_type=OpportunityType.CONTENT,
                status=OpportunityStatus.EVALUATED,
                source_type="test",
                ranking_tier=RankingTier.UNLIKELY if i < 3 else RankingTier.PROMISING,
            )
            db_session.add(opp)
        await db_session.commit()
        
        response = await client.post(
            "/api/opportunities/bulk-dismiss",
            json={
                "tier": "unlikely",
                "reason": "Bulk cleanup",
            },
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["dismissed_count"] == 3
    
    @pytest.mark.asyncio
    async def test_get_opportunities_by_tier(self, client, db_session):
        """Test getting opportunities grouped by tier."""
        # Create opportunities in different tiers
        for tier, count in [
            (RankingTier.TOP_PICK, 2),
            (RankingTier.PROMISING, 3),
            (RankingTier.MAYBE, 1),
        ]:
            for i in range(count):
                opp = Opportunity(
                    title=f"{tier.value} {i}",
                    summary="Test",
                    opportunity_type=OpportunityType.CONTENT,
                    status=OpportunityStatus.EVALUATED,
                    source_type="test",
                    ranking_tier=tier,
                )
                db_session.add(opp)
        await db_session.commit()
        
        response = await client.get("/api/opportunities/by-tier")
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["top_pick"]) == 2
        assert len(data["promising"]) == 3
        assert len(data["maybe"]) == 1
    
    @pytest.mark.asyncio
    async def test_get_hopper_status(self, client):
        """Test getting hopper status."""
        response = await client.get("/api/opportunities/hopper")
        
        assert response.status_code == 200
        data = response.json()
        assert "max_capacity" in data
        assert "available_slots" in data
        assert "status" in data


class TestStrategyEndpoints:
    """Test strategy API endpoints."""
    
    @pytest.mark.asyncio
    async def test_list_strategies(self, client, db_session):
        """Test listing strategies."""
        strategy = DiscoveryStrategy(
            name="Test Strategy",
            description="A test strategy",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
        )
        db_session.add(strategy)
        await db_session.commit()
        
        response = await client.get("/api/opportunities/strategies")
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["strategies"][0]["name"] == "Test Strategy"
    
    @pytest.mark.asyncio
    async def test_pause_strategy(self, client, db_session):
        """Test pausing a strategy."""
        strategy = DiscoveryStrategy(
            name="To Pause",
            description="Test",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
        )
        db_session.add(strategy)
        await db_session.commit()
        await db_session.refresh(strategy)
        
        response = await client.post(f"/api/opportunities/strategies/{strategy.id}/pause")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "paused"


class TestAgentEndpoints:
    """Test agent action endpoints."""
    
    @pytest.mark.asyncio
    async def test_create_strategic_plan(self, client, db_session):
        """Test creating a strategic plan."""
        # Mock the agent's LLM call
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Strategic plan created"
        mock_result.data = {
            "plan": "Test plan content",
            "strategies_created": ["Strategy 1"],
        }
        mock_result.tokens_used = 1000
        mock_result.model_used = "test-model"
        
        with patch(
            "app.api.endpoints.opportunities.opportunity_scout_agent.create_strategic_plan",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await client.post("/api/opportunities/agent/plan")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Strategy 1" in data["strategies_created"]
    
    @pytest.mark.asyncio
    async def test_run_discovery(self, client, db_session):
        """Test running discovery."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Discovery complete"
        mock_result.data = {
            "opportunities_created": 5,
            "strategies_run": 2,
            "opportunity_ids": [str(uuid4()) for _ in range(5)],
        }
        mock_result.tokens_used = 2000
        
        with patch(
            "app.api.endpoints.opportunities.opportunity_scout_agent.run_discovery",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await client.post("/api/opportunities/agent/discover")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["opportunities_created"] == 5
    
    @pytest.mark.asyncio
    async def test_evaluate_opportunities(self, client, db_session):
        """Test evaluating opportunities."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Evaluation complete"
        mock_result.data = {"evaluated": 3}
        mock_result.tokens_used = 1500
        
        with patch(
            "app.api.endpoints.opportunities.opportunity_scout_agent.evaluate_opportunities",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await client.post("/api/opportunities/agent/evaluate")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["evaluated"] == 3
    
    @pytest.mark.asyncio
    async def test_reflect_and_learn(self, client, db_session):
        """Test reflection/learning endpoint."""
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.message = "Reflection complete"
        mock_result.data = {
            "insights_created": 2,
            "reflection": "Learned some things...",
        }
        mock_result.tokens_used = 800
        mock_result.model_used = "test-model"
        
        with patch(
            "app.api.endpoints.opportunities.opportunity_scout_agent.reflect_and_learn",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            response = await client.post("/api/opportunities/agent/reflect")
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["insights_created"] == 2


class TestStatisticsEndpoint:
    """Test statistics endpoint."""
    
    @pytest.mark.asyncio
    async def test_get_statistics(self, client, db_session):
        """Test getting scout statistics."""
        # Create some data
        for i in range(3):
            opp = Opportunity(
                title=f"Opp {i}",
                summary="Test",
                opportunity_type=OpportunityType.CONTENT,
                status=OpportunityStatus.APPROVED if i == 0 else OpportunityStatus.DISMISSED,
                source_type="test",
            )
            db_session.add(opp)
        await db_session.commit()
        
        response = await client.get("/api/opportunities/statistics")
        
        assert response.status_code == 200
        data = response.json()
        assert data["period_days"] == 30
        assert data["opportunities"]["total"] == 3
        assert data["opportunities"]["approved"] == 1
