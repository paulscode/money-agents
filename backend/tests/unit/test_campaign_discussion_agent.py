"""Tests for CampaignDiscussionAgent.

Covers:
- get_system_prompt structure and context injection
- execute dispatch (action routing)
- Agent metadata
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.agents.campaign_discussion import CampaignDiscussionAgent
from app.agents.base import AgentContext, AgentResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent():
    return CampaignDiscussionAgent()


# ---------------------------------------------------------------------------
# get_system_prompt
# ---------------------------------------------------------------------------

class TestGetSystemPrompt:

    def test_prompt_contains_role(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        assert "Campaign Discussion" in prompt

    def test_prompt_contains_action_instructions(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        assert "<campaign_action" in prompt
        assert "provide_input" in prompt

    def test_prompt_without_context(self, agent):
        prompt = agent.get_system_prompt(tools=[])
        # Should still be a valid prompt even without campaign context
        assert len(prompt) > 100

    def test_prompt_with_campaign_context(self, agent):
        """When campaign_context is provided, it should be included in the prompt."""
        mock_context = MagicMock()
        # The agent creates a bare CampaignContextService via __new__ and
        # calls format_context_for_prompt on it.  We patch the method itself.
        with patch(
            "app.agents.campaign_discussion.CampaignContextService.format_context_for_prompt",
            return_value="## Campaign: Test Campaign\nStatus: active",
        ):
            prompt = agent.get_system_prompt(tools=[], campaign_context=mock_context)
        assert "Test Campaign" in prompt


# ---------------------------------------------------------------------------
# execute (action routing)
# ---------------------------------------------------------------------------

class TestExecuteDispatch:

    @pytest.mark.asyncio
    async def test_unknown_action_fails(self, agent):
        ctx = AgentContext(db=AsyncMock())
        result = await agent.execute(ctx, action="nonexistent")
        assert result.success is False
        assert "Unknown action" in result.message

    @pytest.mark.asyncio
    async def test_respond_without_message_fails(self, agent):
        ctx = AgentContext(db=AsyncMock())
        result = await agent.execute(ctx, action="respond", user_message="")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_default_action_is_respond(self, agent):
        """When no action is specified, default should be 'respond'."""
        ctx = AgentContext(db=AsyncMock())
        # Without user_message it should fail with a clear message
        result = await agent.execute(ctx)
        assert result.success is False
        assert "No user message" in result.message


# ---------------------------------------------------------------------------
# Agent metadata
# ---------------------------------------------------------------------------

class TestAgentMetadata:

    def test_name(self, agent):
        assert agent.name == "campaign_discussion"

    def test_model_tier(self, agent):
        assert agent.model_tier == "fast"

    def test_tool_allowlist_empty(self, agent):
        """CampaignDiscussion doesn't make tool_calls."""
        assert agent.TOOL_ALLOWLIST == []

    def test_description(self, agent):
        assert "campaign" in agent.description.lower()
