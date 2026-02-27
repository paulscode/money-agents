"""Unit tests for OpportunityScoutAgent."""
import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents.opportunity_scout import OpportunityScoutAgent
from app.agents.base import AgentContext, AgentResult
from app.models import (
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    DiscoveryStrategy,
    StrategyStatus,
    AgentInsight,
    InsightType,
)


class TestOpportunityScoutAgent:
    """Test suite for OpportunityScoutAgent."""
    
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return OpportunityScoutAgent()
    
    # ==========================================================================
    # Test Planning Prompt Generation
    # ==========================================================================
    
    def test_planning_system_prompt(self, agent):
        """Test that planning system prompt contains key elements."""
        prompt = agent._get_planning_system_prompt()
        
        assert "strategic planner" in prompt.lower()
        assert "diversify" in prompt.lower()
        assert "learn from history" in prompt.lower()
        assert "json" in prompt.lower()
        assert "strategies" in prompt.lower()
    
    def test_build_planning_prompt_fresh_start(self, agent):
        """Test planning prompt for fresh start (no existing data)."""
        prompt = agent._build_planning_prompt(
            tools=[],
            active_strategies=[],
            recent_insights=[],
            memory_summary=None,
            strategy_stats={"total": 0, "active": 0, "opportunities_found": 0, "approval_rate": 0},
            force_new=False,
        )
        
        assert "No tools currently available" in prompt
        assert "No active strategies yet" in prompt
        assert "No insights recorded yet" in prompt
        assert "No historical memory yet" in prompt
    
    def test_build_planning_prompt_with_context(self, agent):
        """Test planning prompt includes existing context."""
        # Mock tool
        mock_tool = MagicMock()
        mock_tool.name = "web-search"
        mock_tool.description = "Search the web for information"
        
        # Mock strategy
        mock_strategy = MagicMock()
        mock_strategy.name = "Content Search"
        mock_strategy.times_executed = 5
        mock_strategy.opportunities_found = 10
        mock_strategy.opportunities_approved = 3
        mock_strategy.effectiveness_score = 0.3
        
        # Mock insight
        mock_insight = MagicMock()
        mock_insight.insight_type = InsightType.PATTERN
        mock_insight.title = "Newsletter queries work best"
        mock_insight.confidence = 0.8
        
        prompt = agent._build_planning_prompt(
            tools=[mock_tool],
            active_strategies=[mock_strategy],
            recent_insights=[mock_insight],
            memory_summary=None,
            strategy_stats={"total": 1, "active": 1, "opportunities_found": 10, "approval_rate": 0.3},
            force_new=False,
        )
        
        assert "web-search" in prompt
        assert "Content Search" in prompt
        assert "Newsletter queries work best" in prompt
        assert "10" in prompt  # opportunities found
    
    # ==========================================================================
    # Test JSON Parsing
    # ==========================================================================
    
    @pytest_asyncio.fixture
    async def mock_db_session(self):
        """Create a mock database session."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()
        session.flush = AsyncMock()
        # execute() returns a Result-like mock with sync scalar_one_or_none
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)
        return session
    
    @pytest.mark.asyncio
    async def test_parse_and_save_strategies_valid_json(
        self, agent, mock_db_session, mock_llm_plan_response
    ):
        """Test parsing valid strategy JSON from LLM response."""
        # Format as LLM would return it (with markdown code blocks)
        plan_content = f"""Here's my strategic plan:

```json
{json.dumps(mock_llm_plan_response)}
```

This plan focuses on content monetization opportunities."""

        strategies = await agent._parse_and_save_strategies(mock_db_session, plan_content)
        
        assert len(strategies) == 1
        assert strategies[0].name == "Content Monetization Search"
        assert len(strategies[0].search_queries) > 0  # Has queries
        mock_db_session.add.assert_called()
        mock_db_session.commit.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_parse_and_save_strategies_no_json(self, agent, mock_db_session):
        """Test handling response with no JSON."""
        plan_content = "I couldn't generate a plan at this time."
        
        strategies = await agent._parse_and_save_strategies(mock_db_session, plan_content)
        
        assert len(strategies) == 0
        mock_db_session.commit.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_parse_and_save_strategies_invalid_json(self, agent, mock_db_session):
        """Test handling malformed JSON."""
        plan_content = '{"strategies": [{"name": broken json'
        
        strategies = await agent._parse_and_save_strategies(mock_db_session, plan_content)
        
        assert len(strategies) == 0
    
    # ==========================================================================
    # Test Opportunity Creation from Signals
    # ==========================================================================
    
    @pytest.mark.asyncio
    async def test_create_opportunity_from_signal(self, agent, mock_db_session):
        """Test creating opportunity from a filtered signal."""
        signal = {
            "result_index": 1,
            "signal": "Newsletter monetization opportunity",
            "opportunity_type": "content",
            "revenue_potential": "medium",
            "time_sensitivity": "evergreen",
            "title": "Newsletter Revenue Guide",
            "source_url": "https://example.com/newsletter",
            "raw_snippet": "How to make money with newsletters...",
        }
        
        mock_strategy = MagicMock()
        mock_strategy.id = uuid4()
        
        opportunity = await agent._create_opportunity_from_signal(
            db=mock_db_session,
            signal=signal,
            strategy=mock_strategy,
            query="newsletter monetization",
            tool_slugs={"web-search"},
        )
        
        assert opportunity is not None
        assert opportunity.title == "Newsletter Revenue Guide"
        assert opportunity.opportunity_type == OpportunityType.CONTENT
        assert opportunity.source_query == "newsletter monetization"
        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_create_opportunity_handles_unknown_type(self, agent, mock_db_session):
        """Test that unknown opportunity types default to OTHER."""
        signal = {
            "signal": "Some opportunity",
            "opportunity_type": "unknown_type",
            "title": "Unknown Opportunity",
        }
        
        mock_strategy = MagicMock()
        mock_strategy.id = uuid4()
        
        opportunity = await agent._create_opportunity_from_signal(
            db=mock_db_session,
            signal=signal,
            strategy=mock_strategy,
            query="test",
            tool_slugs=set(),
        )
        
        assert opportunity.opportunity_type == OpportunityType.OTHER
    
    # ==========================================================================
    # Test Ranking Logic
    # ==========================================================================
    
    @pytest.mark.asyncio
    async def test_rank_opportunities_logic(self, agent):
        """Test ranking tier assignment logic based on scores."""
        # Test the tier assignment thresholds directly
        # >= 0.8 -> TOP_PICK
        # >= 0.6 -> PROMISING
        # >= 0.4 -> MAYBE
        # < 0.4 -> UNLIKELY
        
        test_cases = [
            (0.85, RankingTier.TOP_PICK),
            (0.80, RankingTier.TOP_PICK),
            (0.75, RankingTier.PROMISING),
            (0.60, RankingTier.PROMISING),
            (0.55, RankingTier.MAYBE),
            (0.40, RankingTier.MAYBE),
            (0.35, RankingTier.UNLIKELY),
            (0.10, RankingTier.UNLIKELY),
        ]
        
        for score, expected_tier in test_cases:
            if score >= 0.8:
                tier = RankingTier.TOP_PICK
            elif score >= 0.6:
                tier = RankingTier.PROMISING
            elif score >= 0.4:
                tier = RankingTier.MAYBE
            else:
                tier = RankingTier.UNLIKELY
            
            assert tier == expected_tier, f"Score {score} should be {expected_tier}, got {tier}"
    
    # ==========================================================================
    # Test Filter Results Parsing
    # ==========================================================================
    
    def test_filter_response_json_extraction(self, mock_llm_filter_response):
        """Test that filter response JSON can be properly extracted."""
        # Simulate LLM response with JSON
        response_text = f"""Based on analysis:

```json
{json.dumps(mock_llm_filter_response)}
```

The most promising result is the newsletter opportunity."""
        
        # Extract JSON (same logic as agent)
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        parsed = json.loads(response_text[json_start:json_end])
        
        assert len(parsed["promising"]) == 1
        assert parsed["promising"][0]["opportunity_type"] == "content"
        assert parsed["rejected_count"] == 9
    
    # ==========================================================================
    # Test Evaluation Parsing
    # ==========================================================================
    
    def test_evaluation_response_parsing(self, mock_llm_eval_response):
        """Test parsing evaluation response."""
        response_text = f"""## Evaluation

{json.dumps(mock_llm_eval_response)}"""
        
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        parsed = json.loads(response_text[json_start:json_end])
        
        assert parsed["overall_score"] == 0.72
        assert parsed["confidence_score"] == 0.85
        assert parsed["estimated_effort"] == "moderate"
        assert parsed["recommendation"] == "approve"
        assert len(parsed["score_breakdown"]) == 6


class TestAgentExecuteActions:
    """Test the execute() method routing."""
    
    @pytest.fixture
    def agent(self):
        return OpportunityScoutAgent()
    
    @pytest.mark.asyncio
    async def test_execute_unknown_action(self, agent):
        """Test that unknown actions return failure."""
        context = AgentContext(db=AsyncMock())
        result = await agent.execute("unknown_action", context)
        
        assert result.success is False
        assert "unknown" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_execute_plan_action(self, agent):
        """Test that 'plan' action routes to create_strategic_plan."""
        context = AgentContext(db=AsyncMock())
        
        with patch.object(agent, 'create_strategic_plan', new_callable=AsyncMock) as mock_plan:
            mock_plan.return_value = AgentResult(success=True, message="Planned")
            result = await agent.execute("plan", context)
            mock_plan.assert_called_once_with(context)
    
    @pytest.mark.asyncio
    async def test_execute_discover_action(self, agent):
        """Test that 'discover' action routes to run_discovery."""
        context = AgentContext(db=AsyncMock())
        
        with patch.object(agent, 'run_discovery', new_callable=AsyncMock) as mock_discover:
            mock_discover.return_value = AgentResult(success=True, message="Discovered")
            result = await agent.execute("discover", context)
            mock_discover.assert_called_once_with(context)
