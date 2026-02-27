"""Tests for ProposalWriterAgent.

Covers pure-function helpers that don't require LLM calls:
- _build_proposal_section
- _format_proposal_for_analysis
- _format_research_context
- get_system_prompt structure
- execute dispatch (action routing)
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.agents.proposal_writer import ProposalWriterAgent
from app.agents.base import AgentContext, AgentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent():
    return ProposalWriterAgent()


@pytest.fixture
def sample_proposal():
    return {
        "title": "AI-Powered Etsy Store",
        "status": "draft",
        "summary": "Launch a print-on-demand store using AI designs",
        "detailed_description": "Use DALL-E to generate unique designs and sell on Etsy.",
        "initial_budget": 500.0,
        "bitcoin_budget_sats": 100000,
        "bitcoin_budget_rationale": "For Lightning payments to suppliers",
        "expected_returns": {"monthly": 800},
        "risk_level": "medium",
        "risk_description": "Market saturation risk",
        "success_criteria": {"revenue_target": 1000, "timeframe": "3 months"},
        "required_tools": {"image-generator": {"purpose": "design creation"}},
        "implementation_timeline": {"phases": [{"name": "Setup"}, {"name": "Launch"}]},
    }


@pytest.fixture
def sample_research_context():
    return {
        "source": {
            "type": "web_search",
            "query": "print on demand etsy 2024",
            "urls": ["https://example.com/etsy-guide"],
        },
        "assessment": {
            "initial": "Promising opportunity with proven revenue model",
            "detailed": "The Etsy POD market shows consistent growth.",
            "confidence": 0.82,
        },
        "scoring": {
            "overall": 0.75,
            "tier": "A",
            "breakdown": {"market_validation": 0.8, "competition": 0.6},
        },
        "requirements": {
            "skills": ["design", "marketing"],
            "tools": ["image-generator", "etsy-api"],
            "blocking": ["API key needed"],
        },
        "timing": {
            "time_sensitivity": "evergreen",
            "discovered_at": "2024-01-15",
        },
    }


# ---------------------------------------------------------------------------
# _build_proposal_section
# ---------------------------------------------------------------------------

class TestBuildProposalSection:

    def test_includes_title_and_status(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "AI-Powered Etsy Store" in section
        assert "draft" in section

    def test_includes_budget_info(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "$500" in section
        assert "100,000 sats" in section
        assert "Lightning payments" in section

    def test_includes_risk_and_returns(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "medium" in section
        assert "Market saturation" in section

    def test_includes_timeline_phase_count(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "2 phases" in section

    def test_truncates_long_description(self, agent):
        proposal = {"detailed_description": "x" * 2000}
        section = agent._build_proposal_section(proposal)
        assert "..." in section

    def test_handles_empty_proposal(self, agent):
        section = agent._build_proposal_section({})
        # Should not crash; header should still exist
        assert "Current Proposal" in section

    def test_includes_tools(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "image-generator" in section

    def test_includes_success_criteria(self, agent, sample_proposal):
        section = agent._build_proposal_section(sample_proposal)
        assert "revenue_target" in section


# ---------------------------------------------------------------------------
# _format_proposal_for_analysis
# ---------------------------------------------------------------------------

class TestFormatProposalForAnalysis:

    def test_formats_all_fields(self, agent, sample_proposal):
        text = agent._format_proposal_for_analysis(sample_proposal)
        assert "AI-Powered Etsy Store" in text
        assert "$500" in text
        assert "medium" in text
        assert "Market saturation" in text

    def test_empty_data_returns_fallback(self, agent):
        text = agent._format_proposal_for_analysis({})
        assert "No proposal data" in text

    def test_includes_stop_loss_if_present(self, agent):
        text = agent._format_proposal_for_analysis({"stop_loss_threshold": "$250"})
        assert "$250" in text

    def test_includes_timeline_if_present(self, agent):
        text = agent._format_proposal_for_analysis({"implementation_timeline": "3 months"})
        assert "3 months" in text


# ---------------------------------------------------------------------------
# _format_research_context
# ---------------------------------------------------------------------------

class TestFormatResearchContext:

    def test_includes_source_info(self, agent, sample_research_context):
        text = agent._format_research_context(sample_research_context)
        assert "web_search" in text
        assert "print on demand etsy 2024" in text
        assert "example.com" in text

    def test_includes_assessment(self, agent, sample_research_context):
        text = agent._format_research_context(sample_research_context)
        assert "Promising opportunity" in text
        assert "82%" in text

    def test_includes_scoring(self, agent, sample_research_context):
        text = agent._format_research_context(sample_research_context)
        assert "0.75" in text
        assert "Tier" in text

    def test_includes_requirements(self, agent, sample_research_context):
        text = agent._format_research_context(sample_research_context)
        assert "design" in text
        assert "marketing" in text
        assert "API key needed" in text

    def test_includes_timing(self, agent, sample_research_context):
        text = agent._format_research_context(sample_research_context)
        assert "evergreen" in text

    def test_empty_context(self, agent):
        text = agent._format_research_context({})
        assert "No research context" in text


# ---------------------------------------------------------------------------
# get_system_prompt
# ---------------------------------------------------------------------------

class TestGetSystemPrompt:

    def test_prompt_contains_role(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        assert "Proposal Writer" in prompt

    def test_prompt_contains_edit_instructions(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        assert "<proposal_edit" in prompt
        assert "title" in prompt

    def test_prompt_includes_proposal_context(self, agent, sample_proposal):
        prompt = agent.get_system_prompt(tools=[], proposal_context=sample_proposal)
        assert "AI-Powered Etsy Store" in prompt
        assert "Current Proposal" in prompt

    def test_prompt_without_context(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        assert "Current Proposal" not in prompt


# ---------------------------------------------------------------------------
# execute (action routing)
# ---------------------------------------------------------------------------

class TestExecuteDispatch:

    @pytest.mark.asyncio
    async def test_unknown_action(self, agent):
        ctx = AgentContext(db=AsyncMock())
        result = await agent.execute(ctx, action="nonexistent")
        assert result.success is False
        assert "Unknown action" in result.message

    @pytest.mark.asyncio
    async def test_respond_without_message(self, agent):
        ctx = AgentContext(db=AsyncMock())
        result = await agent.execute(ctx, action="respond", user_message="")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_analyze_without_data(self, agent):
        ctx = AgentContext(db=AsyncMock())
        result = await agent.execute(ctx, action="analyze", proposal_data={})
        assert result.success is False


# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------

class TestAgentMetadata:

    def test_name(self, agent):
        assert agent.name == "proposal_writer"

    def test_model_tier(self, agent):
        assert agent.model_tier == "reasoning"

    def test_tool_allowlist_empty(self, agent):
        """ProposalWriter doesn't make tool_calls."""
        assert agent.TOOL_ALLOWLIST == []
