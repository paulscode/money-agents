"""Unit tests for CampaignManagerAgent."""
import json
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from app.agents.campaign_manager import CampaignManagerAgent, CampaignPhase
from app.agents.base import AgentContext, AgentResult
from app.models import (
    Campaign,
    CampaignStatus,
    Proposal,
    ProposalStatus,
    RiskLevel,
)


class TestCampaignManagerAgent:
    """Test suite for CampaignManagerAgent."""
    
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return CampaignManagerAgent()
    
    # ==========================================================================
    # Test Agent Configuration
    # ==========================================================================
    
    def test_agent_name(self, agent):
        """Test agent has correct name."""
        assert agent.name == "campaign_manager"
    
    def test_agent_description(self, agent):
        """Test agent has description."""
        assert "campaign" in agent.description.lower()
        assert "execute" in agent.description.lower() or "autonom" in agent.description.lower()
    
    def test_agent_model_tier(self, agent):
        """Test agent uses reasoning tier by default."""
        assert agent.model_tier == "reasoning"
    
    def test_agent_max_tokens(self, agent):
        """Test agent uses high max_tokens limit."""
        assert agent.default_max_tokens >= 4000
    
    # ==========================================================================
    # Test System Prompt Generation
    # ==========================================================================
    
    def test_system_prompt_basic(self, agent):
        """Test basic system prompt generation."""
        prompt = agent.get_system_prompt(tools=[])
        
        assert "Campaign Manager Agent" in prompt
        assert "Money Agents" in prompt
        assert "autonomously" in prompt.lower() or "autonomous" in prompt.lower()
    
    def test_system_prompt_with_tools(self, agent):
        """Test system prompt includes tools."""
        mock_tool = MagicMock()
        mock_tool.name = "web-search"
        mock_tool.slug = "web-search"
        mock_tool.category = MagicMock()
        mock_tool.category.value = "api"
        mock_tool.description = "Search the web"
        mock_tool.usage_instructions = "Call with query"
        mock_tool.cost_model = "per_use"
        mock_tool.strengths = "Fast"
        mock_tool.weaknesses = "Limited"
        mock_tool.best_use_cases = "Research"
        
        prompt = agent.get_system_prompt(tools=[mock_tool])
        
        assert "web-search" in prompt
        assert "Available Tools" in prompt
    
    def test_system_prompt_with_campaign_context(self, agent):
        """Test system prompt includes campaign context."""
        context = {
            "status": "active",
            "current_phase": "executing",
            "budget_allocated": 500.0,
            "budget_spent": 100.0,
            "revenue_generated": 50.0,
            "tasks_total": 10,
            "tasks_completed": 3,
            "success_metrics": {
                "revenue": {"current": 50, "target": 500, "percentage": 10}
            },
            "requirements_checklist": [
                {"item": "API key", "completed": True, "blocking": True},
                {"item": "Budget approval", "completed": False, "blocking": True},
            ],
            "proposal": {
                "title": "Test Campaign",
                "summary": "A test campaign for unit tests",
            }
        }
        
        prompt = agent.get_system_prompt(tools=[], campaign_context=context)
        
        assert "Test Campaign" in prompt
        assert "active" in prompt.lower()
        assert "500" in prompt  # budget
        assert "3/10" in prompt or "3" in prompt  # tasks
    
    def test_system_prompt_user_input_format(self, agent):
        """Test system prompt explains user input request format."""
        prompt = agent.get_system_prompt(tools=[])
        
        assert "user_input_request" in prompt
        assert "confirmation" in prompt.lower()
        assert "credentials" in prompt.lower()
        assert "blocking" in prompt.lower()
    
    def test_system_prompt_campaign_status_format(self, agent):
        """Test system prompt explains campaign status format."""
        prompt = agent.get_system_prompt(tools=[])
        
        assert "campaign_status" in prompt
        assert "Progress" in prompt
        assert "Budget" in prompt
    
    # ==========================================================================
    # Test Campaign Section Builder
    # ==========================================================================
    
    def test_build_campaign_section_minimal(self, agent):
        """Test campaign section with minimal data."""
        context = {
            "status": "initializing",
        }
        
        section = agent._build_campaign_section(context)
        
        assert "Current Campaign" in section
        assert "initializing" in section.lower()
    
    def test_build_campaign_section_with_financials(self, agent):
        """Test campaign section includes financial data."""
        context = {
            "status": "active",
            "budget_allocated": 1000.0,
            "budget_spent": 250.0,
            "revenue_generated": 100.0,
        }
        
        section = agent._build_campaign_section(context)
        
        assert "Budget" in section
        assert "1,000" in section or "1000" in section
        assert "250" in section
        assert "Revenue" in section or "100" in section
    
    def test_build_campaign_section_with_requirements(self, agent):
        """Test campaign section shows requirements status."""
        context = {
            "status": "waiting_for_inputs",
            "requirements_checklist": [
                {"item": "Twitter API credentials", "completed": False, "blocking": True},
                {"item": "Budget approved", "completed": True, "blocking": True},
                {"item": "Content calendar", "completed": False, "blocking": False},
            ],
        }
        
        section = agent._build_campaign_section(context)
        
        assert "Requirements" in section
        assert "1/3" in section or "1" in section  # completed count
        assert "Twitter API" in section or "Pending Blocking" in section
    
    # ==========================================================================
    # Test Success Metrics Initialization
    # ==========================================================================
    
    def test_initialize_success_metrics_from_criteria(self, agent):
        """Test initializing metrics from proposal criteria."""
        proposal = MagicMock()
        proposal.success_criteria = {
            "revenue": 5000,
            "customers": 100,
        }
        proposal.initial_budget = Decimal("500.00")
        
        metrics = agent._initialize_success_metrics(proposal)
        
        assert "revenue" in metrics
        assert metrics["revenue"]["current"] == 0
        assert metrics["revenue"]["target"] == 5000
        assert "customers" in metrics
    
    def test_initialize_success_metrics_with_dict_targets(self, agent):
        """Test metrics with complex dict targets."""
        proposal = MagicMock()
        proposal.success_criteria = {
            "roi": {"target": 2.0, "minimum": 1.0},
        }
        proposal.initial_budget = Decimal("500.00")
        
        metrics = agent._initialize_success_metrics(proposal)
        
        assert "roi" in metrics
        assert metrics["roi"]["target"] == 2.0
    
    def test_initialize_success_metrics_default(self, agent):
        """Test default metrics when none specified."""
        proposal = MagicMock()
        proposal.success_criteria = None
        proposal.initial_budget = Decimal("500.00")
        
        metrics = agent._initialize_success_metrics(proposal)
        
        assert "revenue" in metrics
        assert "roi" in metrics
        assert metrics["revenue"]["target"] == 1000.0  # 2x budget
    
    # ==========================================================================
    # Test Initialization Message Formatting
    # ==========================================================================
    
    def test_format_initialization_message(self, agent):
        """Test initialization message formatting."""
        proposal = MagicMock()
        proposal.title = "AI Content Generator"
        
        campaign = MagicMock()
        campaign.budget_allocated = Decimal("1000.00")
        
        checklist = [
            {"item": "API credentials", "blocking": True, "completed": False},
            {"item": "Budget approval", "blocking": True, "completed": False},
            {"item": "Optional: Analytics setup", "blocking": False, "completed": False},
        ]
        
        message = agent._format_initialization_message(proposal, campaign, checklist)
        
        assert "AI Content Generator" in message
        assert "1,000" in message or "1000" in message
        assert "Blocking Requirements" in message
        assert "API credentials" in message
        assert "Optional Requirements" in message or "Optional" in message
    
    def test_format_initialization_message_ready_to_execute(self, agent):
        """Test message when all blocking requirements met."""
        proposal = MagicMock()
        proposal.title = "Ready Campaign"
        
        campaign = MagicMock()
        campaign.budget_allocated = Decimal("500.00")
        
        checklist = [
            {"item": "Already done", "blocking": True, "completed": True},
            {"item": "Optional thing", "blocking": False, "completed": False},
        ]
        
        message = agent._format_initialization_message(proposal, campaign, checklist)
        
        assert "Ready to Execute" in message or "begin executing" in message
    
    # ==========================================================================
    # Test Threshold Checking
    # ==========================================================================
    
    @pytest_asyncio.fixture
    async def mock_context(self):
        """Create mock context."""
        context = MagicMock(spec=AgentContext)
        context.db = AsyncMock()
        context.user_id = uuid4()
        return context
    
    @pytest.mark.asyncio
    async def test_check_thresholds_budget_warning(self, agent):
        """Test budget warning threshold."""
        campaign = MagicMock()
        campaign.budget_allocated = 100.0
        campaign.budget_spent = 95.0
        campaign.revenue_generated = 0.0
        campaign.success_metrics = {}
        
        result = await agent._check_thresholds(campaign, None)
        
        assert result["action_needed"] == True
        assert result["type"] == "budget_warning"
    
    @pytest.mark.asyncio
    async def test_check_thresholds_stop_loss(self, agent):
        """Test stop-loss threshold."""
        campaign = MagicMock()
        campaign.budget_allocated = 100.0
        campaign.budget_spent = 80.0
        campaign.revenue_generated = 10.0  # Net loss of $70
        campaign.success_metrics = {}
        
        proposal = MagicMock()
        proposal.stop_loss_threshold = {"max_loss": 50}  # Stop at $50 loss
        
        result = await agent._check_thresholds(campaign, proposal)
        
        assert result["action_needed"] == True
        assert result["type"] == "stop_loss"
    
    @pytest.mark.asyncio
    async def test_check_thresholds_success_reached(self, agent):
        """Test success threshold reached."""
        campaign = MagicMock()
        campaign.budget_allocated = 100.0
        campaign.budget_spent = 50.0
        campaign.revenue_generated = 200.0
        campaign.success_metrics = {
            "revenue": {"current": 200, "target": 150, "percentage": 133}
        }
        
        result = await agent._check_thresholds(campaign, None)
        
        assert result["action_needed"] == True
        assert result["type"] == "success_reached"
    
    @pytest.mark.asyncio
    async def test_check_thresholds_no_action(self, agent):
        """Test no action needed when within thresholds."""
        campaign = MagicMock()
        campaign.budget_allocated = 100.0
        campaign.budget_spent = 30.0
        campaign.revenue_generated = 20.0
        campaign.success_metrics = {
            "revenue": {"current": 20, "target": 150, "percentage": 13}
        }
        
        result = await agent._check_thresholds(campaign, None)
        
        assert result["action_needed"] == False
    
    # ==========================================================================
    # Test Campaign Context Builder
    # ==========================================================================
    
    def test_get_campaign_context(self, agent):
        """Test building campaign context dict."""
        campaign = MagicMock()
        campaign.status = MagicMock()
        campaign.status.value = "active"
        campaign.current_phase = "executing"
        campaign.budget_allocated = Decimal("500.00")
        campaign.budget_spent = Decimal("100.00")
        campaign.revenue_generated = Decimal("50.00")
        campaign.tasks_total = 10
        campaign.tasks_completed = 3
        campaign.success_metrics = {"revenue": {"current": 50, "target": 500}}
        campaign.requirements_checklist = [{"item": "Test", "completed": True}]
        campaign.all_requirements_met = True
        
        proposal = MagicMock()
        proposal.title = "Test Proposal"
        proposal.summary = "Test summary"
        proposal.required_tools = {"tool1": {}}
        
        context = agent._get_campaign_context(campaign, proposal)
        
        assert context["status"] == "active"
        assert context["current_phase"] == "executing"
        assert context["budget_allocated"] == 500.0
        assert context["proposal"]["title"] == "Test Proposal"
    
    # ==========================================================================
    # Test Campaign Phases
    # ==========================================================================
    
    def test_campaign_phases_defined(self):
        """Test all campaign phases are defined."""
        assert hasattr(CampaignPhase, "INITIALIZING")
        assert hasattr(CampaignPhase, "REQUIREMENTS_GATHERING")
        assert hasattr(CampaignPhase, "WAITING_FOR_USER")
        assert hasattr(CampaignPhase, "EXECUTING")
        assert hasattr(CampaignPhase, "MONITORING")
        assert hasattr(CampaignPhase, "PAUSED")
        assert hasattr(CampaignPhase, "COMPLETING")
        assert hasattr(CampaignPhase, "TERMINATING")


class TestCampaignManagerIntegration:
    """Integration-style tests for CampaignManagerAgent."""
    
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return CampaignManagerAgent()
    
    @pytest.mark.asyncio
    async def test_initialize_campaign_proposal_not_found(self, agent):
        """Test initialization fails for non-existent proposal."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.initialize_campaign(
            context=context,
            proposal_id=uuid4(),
            user_id=uuid4(),
        )
        
        assert result.success == False
        assert "not found" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_initialize_campaign_not_approved(self, agent):
        """Test initialization fails for non-approved proposal."""
        mock_db = AsyncMock()
        
        mock_proposal = MagicMock()
        mock_proposal.status = ProposalStatus.PENDING
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_proposal
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.initialize_campaign(
            context=context,
            proposal_id=uuid4(),
            user_id=uuid4(),
        )
        
        assert result.success == False
        assert "approved" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_get_campaign_status_not_found(self, agent):
        """Test status check fails for non-existent campaign."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.get_campaign_status(
            context=context,
            campaign_id=uuid4(),
        )
        
        assert result.success == False
        assert "not found" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_pause_campaign_wrong_status(self, agent):
        """Test pause fails for completed campaign."""
        mock_db = AsyncMock()
        
        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.COMPLETED
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.pause_campaign(
            context=context,
            campaign_id=uuid4(),
        )
        
        assert result.success == False
        assert "cannot pause" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_resume_campaign_not_paused(self, agent):
        """Test resume fails for non-paused campaign."""
        mock_db = AsyncMock()
        
        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.ACTIVE
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.resume_campaign(
            context=context,
            campaign_id=uuid4(),
        )
        
        assert result.success == False
        assert "not paused" in result.message.lower()
    
    @pytest.mark.asyncio
    async def test_terminate_campaign_already_done(self, agent):
        """Test terminate fails for already-terminated campaign."""
        mock_db = AsyncMock()
        
        mock_campaign = MagicMock()
        mock_campaign.status = CampaignStatus.TERMINATED
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_campaign
        mock_db.execute.return_value = mock_result
        
        context = AgentContext(db=mock_db)
        
        result = await agent.terminate_campaign(
            context=context,
            campaign_id=uuid4(),
            reason="Test termination",
        )
        
        assert result.success == False
        assert "already" in result.message.lower()


class TestToolCallParsing:
    """Test tool call parsing and execution in Campaign Manager."""
    
    @pytest.fixture
    def agent(self):
        """Create agent instance."""
        return CampaignManagerAgent()
    
    def test_parse_tool_calls_single(self, agent):
        """Test parsing a single tool call from response."""
        content = '''I'll test the tool now.
        
<tool_call name="mock-gpu-imgen">{"prompt": "test image", "model": "default"}</tool_call>

The tool should execute.'''
        
        calls = agent.parse_tool_calls(content)
        
        assert len(calls) == 1
        assert calls[0]["tool_slug"] == "mock-gpu-imgen"
        assert calls[0]["params"]["prompt"] == "test image"
        assert calls[0]["params"]["model"] == "default"
    
    def test_parse_tool_calls_multiple(self, agent):
        """Test parsing multiple tool calls."""
        content = '''Generating all images:

<tool_call name="mock-gpu-imgen">{"prompt": "sunset over ocean"}</tool_call>

<tool_call name="mock-gpu-imgen">{"prompt": "city at night"}</tool_call>

<tool_call name="mock-gpu-imgen">{"prompt": "forest clearing"}</tool_call>

All images queued.'''
        
        calls = agent.parse_tool_calls(content)
        
        assert len(calls) == 3
        assert calls[0]["params"]["prompt"] == "sunset over ocean"
        assert calls[1]["params"]["prompt"] == "city at night"
        assert calls[2]["params"]["prompt"] == "forest clearing"
    
    def test_parse_tool_calls_no_calls(self, agent):
        """Test parsing when no tool calls present."""
        content = "This is just a normal response with no tool calls."
        
        calls = agent.parse_tool_calls(content)
        
        assert len(calls) == 0
    
    def test_has_tool_calls_true(self, agent):
        """Test detecting tool calls in content."""
        content = '<tool_call name="test">{"a": 1}</tool_call>'
        assert agent.has_tool_calls(content) == True
    
    def test_has_tool_calls_false(self, agent):
        """Test detecting no tool calls in content."""
        content = "No tool calls here"
        assert agent.has_tool_calls(content) == False
    
    def test_remove_tool_call_tags(self, agent):
        """Test removing tool call tags from content."""
        content = '''Before tool call.

<tool_call name="test">{"param": "value"}</tool_call>

After tool call.'''
        
        clean = agent.remove_tool_call_tags(content)
        
        assert "<tool_call" not in clean
        assert "Before tool call" in clean
        assert "After tool call" in clean
    
    def test_parse_tool_calls_invalid_json(self, agent):
        """Test handling invalid JSON in tool call.
        
        SA2-25: Tool calls with unparseable JSON params are now
        skipped entirely rather than executed with empty params.
        """
        content = '<tool_call name="test">not valid json</tool_call>'
        
        calls = agent.parse_tool_calls(content)
        
        assert len(calls) == 0  # SA2-25: skipped due to invalid JSON
    
    def test_parse_tool_calls_empty_params(self, agent):
        """Test parsing tool call with empty params."""
        content = '<tool_call name="simple-tool"></tool_call>'
        
        calls = agent.parse_tool_calls(content)
        
        assert len(calls) == 1
        assert calls[0]["tool_slug"] == "simple-tool"
        assert calls[0]["params"] == {}
