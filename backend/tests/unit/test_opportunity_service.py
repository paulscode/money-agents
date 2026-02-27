"""Unit tests for OpportunityService."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio

from app.services.opportunity_service import OpportunityService, opportunity_service
from app.models import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    TimeSensitivity,
    EffortLevel,
    DiscoveryStrategy,
    StrategyStatus,
    AgentInsight,
    InsightType,
    UserScoutSettings,
    ScoringRubric,
    Proposal,
    ProposalStatus,
)


class TestOpportunityServiceCRUD:
    """Test CRUD operations for opportunities."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_get_opportunity(self, service, db_session, sample_opportunity_data):
        """Test getting a single opportunity by ID."""
        # Create opportunity
        opp = Opportunity(
            title=sample_opportunity_data["title"],
            summary=sample_opportunity_data["summary"],
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.DISCOVERED,
            source_type=sample_opportunity_data["source_type"],
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        # Get it back
        result = await service.get_opportunity(db_session, opp.id)
        
        assert result is not None
        assert result.title == sample_opportunity_data["title"]
    
    @pytest.mark.asyncio
    async def test_get_opportunity_not_found(self, service, db_session):
        """Test getting non-existent opportunity returns None."""
        result = await service.get_opportunity(db_session, uuid4())
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_opportunities_empty(self, service, db_session):
        """Test getting opportunities when none exist."""
        opportunities, total = await service.get_opportunities(db_session)
        
        assert opportunities == []
        assert total == 0
    
    @pytest.mark.asyncio
    async def test_get_opportunities_with_filter(self, service, db_session):
        """Test filtering opportunities by status."""
        # Create opportunities with different statuses
        discovered = Opportunity(
            title="Discovered",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.DISCOVERED,
            source_type="test",
        )
        evaluated = Opportunity(
            title="Evaluated",
            summary="Test",
            opportunity_type=OpportunityType.SERVICE,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
        )
        db_session.add_all([discovered, evaluated])
        await db_session.commit()
        
        # Filter by status
        results, total = await service.get_opportunities(
            db_session,
            status=OpportunityStatus.DISCOVERED,
        )
        
        assert total == 1
        assert results[0].title == "Discovered"
    
    @pytest.mark.asyncio
    async def test_get_opportunities_excludes_dismissed(self, service, db_session):
        """Test that dismissed opportunities are excluded by default."""
        active = Opportunity(
            title="Active",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
        )
        dismissed = Opportunity(
            title="Dismissed",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.DISMISSED,
            source_type="test",
        )
        db_session.add_all([active, dismissed])
        await db_session.commit()
        
        # Without dismissed
        results, total = await service.get_opportunities(db_session)
        assert total == 1
        assert results[0].title == "Active"
        
        # With dismissed
        results, total = await service.get_opportunities(db_session, include_dismissed=True)
        assert total == 2
    
    @pytest.mark.asyncio
    async def test_get_opportunities_by_tier(self, service, db_session):
        """Test grouping opportunities by tier."""
        top = Opportunity(
            title="Top Pick",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            ranking_tier=RankingTier.TOP_PICK,
        )
        promising = Opportunity(
            title="Promising",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            ranking_tier=RankingTier.PROMISING,
        )
        db_session.add_all([top, promising])
        await db_session.commit()
        
        grouped = await service.get_opportunities_by_tier(db_session)
        
        assert len(grouped["top_pick"]) == 1
        assert len(grouped["promising"]) == 1
        assert len(grouped["maybe"]) == 0


class TestUserActions:
    """Test user actions on opportunities."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_approve_opportunity(self, service, db_session):
        """Test approving an opportunity."""
        # Create opportunity and strategy
        strategy = DiscoveryStrategy(
            name="Test Strategy",
            description="Test",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
            opportunities_found=1,
            opportunities_approved=0,
        )
        db_session.add(strategy)
        await db_session.flush()
        
        opp = Opportunity(
            title="Test",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            discovery_strategy_id=strategy.id,
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        # Approve
        result = await service.approve_opportunity(
            db_session,
            opp.id,
            user_notes="Looks good!",
        )
        
        assert result is not None
        assert result.status == OpportunityStatus.APPROVED
        assert result.user_decision == "approved"
        assert result.user_feedback == "Looks good!"
        assert result.decision_made_at is not None
        
        # Check strategy updated
        await db_session.refresh(strategy)
        assert strategy.opportunities_approved == 1
    
    @pytest.mark.asyncio
    async def test_dismiss_opportunity(self, service, db_session):
        """Test dismissing an opportunity."""
        opp = Opportunity(
            title="Test",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
        )
        db_session.add(opp)
        await db_session.commit()
        await db_session.refresh(opp)
        
        result = await service.dismiss_opportunity(
            db_session,
            opp.id,
            reason="Not interesting",
        )
        
        assert result.status == OpportunityStatus.DISMISSED
        assert result.user_decision == "dismissed"
        assert result.user_feedback == "Not interesting"
    
    @pytest.mark.asyncio
    async def test_bulk_dismiss_by_tier(self, service, db_session):
        """Test bulk dismissing by tier."""
        # Create opportunities in different tiers
        maybe = Opportunity(
            title="Maybe 1",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            ranking_tier=RankingTier.MAYBE,
        )
        unlikely = Opportunity(
            title="Unlikely 1",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            ranking_tier=RankingTier.UNLIKELY,
        )
        top = Opportunity(
            title="Top Pick",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            ranking_tier=RankingTier.TOP_PICK,
        )
        db_session.add_all([maybe, unlikely, top])
        await db_session.commit()
        
        # Bulk dismiss UNLIKELY tier
        count = await service.bulk_dismiss(
            db_session,
            tier=RankingTier.UNLIKELY,
            reason="Bulk dismiss unlikely tier",
        )
        
        assert count == 1
        
        # Verify states
        await db_session.refresh(unlikely)
        await db_session.refresh(top)
        
        assert unlikely.status == OpportunityStatus.DISMISSED
        assert top.status == OpportunityStatus.EVALUATED  # Unchanged
    
    @pytest.mark.asyncio
    async def test_bulk_dismiss_below_score(self, service, db_session):
        """Test bulk dismissing by score threshold."""
        high_score = Opportunity(
            title="High Score",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            overall_score=0.8,
        )
        low_score = Opportunity(
            title="Low Score",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            overall_score=0.3,
        )
        no_score = Opportunity(
            title="No Score",
            summary="Test",
            opportunity_type=OpportunityType.CONTENT,
            status=OpportunityStatus.EVALUATED,
            source_type="test",
            overall_score=None,
        )
        db_session.add_all([high_score, low_score, no_score])
        await db_session.commit()
        
        # Bulk dismiss below 0.5
        count = await service.bulk_dismiss(
            db_session,
            below_score=0.5,
        )
        
        # low_score and no_score should be dismissed
        assert count == 2


class TestHopperManagement:
    """Test hopper (capacity) management."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_hopper_status_empty(self, service, db_session):
        """Test hopper status when empty."""
        status = await service.get_hopper_status(db_session)
        
        assert status["max_capacity"] == 10  # Default
        assert status["active_proposals"] == 0
        assert status["pending_approvals"] == 0
        assert status["available_slots"] == 10
        assert status["status"] == "available"
        assert status["can_accept_more"] is True
    
    @pytest.mark.asyncio
    async def test_hopper_status_with_proposals(self, service, db_session):
        """Test hopper status with active proposals."""
        # Create some proposals (need to add Proposal to database)
        # For now, we'll create approved opportunities instead
        for i in range(3):
            opp = Opportunity(
                title=f"Approved {i}",
                summary="Test",
                opportunity_type=OpportunityType.CONTENT,
                status=OpportunityStatus.APPROVED,
                source_type="test",
            )
            db_session.add(opp)
        await db_session.commit()
        
        status = await service.get_hopper_status(db_session)
        
        assert status["pending_approvals"] == 3
        assert status["total_committed"] == 3
        assert status["available_slots"] == 7
    
    @pytest.mark.asyncio
    async def test_hopper_warning_status(self, service, db_session, test_user):
        """Test hopper enters warning status near capacity."""
        # Create settings with low threshold
        settings = UserScoutSettings(
            user_id=test_user.id,
            max_active_proposals=10,
            hopper_warning_threshold=5,
        )
        db_session.add(settings)
        
        # Create 6 approved opportunities (above warning threshold)
        for i in range(6):
            opp = Opportunity(
                title=f"Approved {i}",
                summary="Test",
                opportunity_type=OpportunityType.CONTENT,
                status=OpportunityStatus.APPROVED,
                source_type="test",
            )
            db_session.add(opp)
        await db_session.commit()
        
        status = await service.get_hopper_status(db_session, user_id=test_user.id)
        
        assert status["status"] == "warning"


class TestStrategyManagement:
    """Test strategy management."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_get_strategies(self, service, db_session, sample_strategy_data):
        """Test getting strategies."""
        strategy = DiscoveryStrategy(
            name=sample_strategy_data["name"],
            description=sample_strategy_data["description"],
            strategy_type=sample_strategy_data["strategy_type"],
            status=StrategyStatus.ACTIVE,
        )
        db_session.add(strategy)
        await db_session.commit()
        
        strategies, total = await service.get_strategies(db_session)
        
        assert total == 1
        assert strategies[0].name == sample_strategy_data["name"]
    
    @pytest.mark.asyncio
    async def test_pause_strategy(self, service, db_session):
        """Test pausing a strategy."""
        strategy = DiscoveryStrategy(
            name="Test",
            description="Test",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
        )
        db_session.add(strategy)
        await db_session.commit()
        await db_session.refresh(strategy)
        
        result = await service.pause_strategy(db_session, strategy.id)
        
        assert result.status == StrategyStatus.PAUSED
    
    @pytest.mark.asyncio
    async def test_deprecate_strategy(self, service, db_session):
        """Test deprecating a strategy."""
        strategy = DiscoveryStrategy(
            name="Test",
            description="Test",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
        )
        db_session.add(strategy)
        await db_session.commit()
        await db_session.refresh(strategy)
        
        result = await service.deprecate_strategy(
            db_session,
            strategy.id,
            reason="No longer effective",
        )
        
        assert result.status == StrategyStatus.DEPRECATED
        assert "No longer effective" in result.agent_notes


class TestInsightManagement:
    """Test insight management."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_validate_insight_positive(self, service, db_session):
        """Test validating an insight increases confidence."""
        insight = AgentInsight(
            insight_type=InsightType.HYPOTHESIS,
            title="Test Insight",
            description="Test description",
            confidence=0.5,
        )
        db_session.add(insight)
        await db_session.commit()
        await db_session.refresh(insight)
        
        result = await service.validate_insight(
            db_session,
            insight.id,
            is_validated=True,
            validation_notes="Confirmed this works",
        )
        
        assert result.validated is True
        assert result.confidence == 0.6  # 0.5 + 0.1
        assert result.validation_notes == "Confirmed this works"
    
    @pytest.mark.asyncio
    async def test_validate_insight_negative(self, service, db_session):
        """Test invalidating an insight decreases confidence."""
        insight = AgentInsight(
            insight_type=InsightType.HYPOTHESIS,
            title="Test Insight",
            description="Test description",
            confidence=0.5,
        )
        db_session.add(insight)
        await db_session.commit()
        await db_session.refresh(insight)
        
        result = await service.validate_insight(
            db_session,
            insight.id,
            is_validated=False,
        )
        
        assert result.validated is False
        assert result.confidence == 0.3  # 0.5 - 0.2


class TestUserSettings:
    """Test user settings management."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_get_user_settings_creates_default(self, service, db_session):
        """Test that settings are None when not configured."""
        settings = await service.get_user_settings(db_session)
        assert settings is None
    
    @pytest.mark.asyncio
    async def test_update_user_settings(self, service, db_session, test_user):
        """Test updating user settings."""
        settings = await service.update_user_settings(
            db_session,
            user_id=test_user.id,
            max_active_proposals=15,
            hopper_warning_threshold=12,
            preferred_domains=["content", "saas"],
        )
        
        assert settings.max_active_proposals == 15
        assert settings.hopper_warning_threshold == 12
        assert "content" in settings.preferred_domains


class TestStatistics:
    """Test statistics and analytics."""
    
    @pytest.fixture
    def service(self):
        return OpportunityService()
    
    @pytest.mark.asyncio
    async def test_get_scout_statistics_empty(self, service, db_session):
        """Test statistics when no data exists."""
        stats = await service.get_scout_statistics(db_session)
        
        assert stats["period_days"] == 30
        assert stats["opportunities"]["total"] == 0
        assert stats["strategies"]["total"] == 0
    
    @pytest.mark.asyncio
    async def test_get_scout_statistics_with_data(self, service, db_session):
        """Test statistics with existing data."""
        # Create some opportunities
        for i in range(5):
            status = OpportunityStatus.APPROVED if i < 2 else OpportunityStatus.DISMISSED
            opp = Opportunity(
                title=f"Opp {i}",
                summary="Test",
                opportunity_type=OpportunityType.CONTENT,
                status=status,
                source_type="test",
                overall_score=0.5 + (i * 0.1),
            )
            db_session.add(opp)
        
        # Create a strategy
        strategy = DiscoveryStrategy(
            name="Test",
            description="Test",
            strategy_type="search",
            status=StrategyStatus.ACTIVE,
            effectiveness_score=0.4,
        )
        db_session.add(strategy)
        await db_session.commit()
        
        stats = await service.get_scout_statistics(db_session)
        
        assert stats["opportunities"]["total"] == 5
        assert stats["opportunities"]["approved"] == 2
        assert stats["opportunities"]["dismissed"] == 3
        assert stats["strategies"]["total"] == 1
        assert stats["strategies"]["active"] == 1
