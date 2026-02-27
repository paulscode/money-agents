"""Tests for LLM cost tracking across all call sites.

Verifies that:
1. LLMUsageService.track() is called from WebSocket endpoints
2. CampaignPlanService tracks costs after generate()
3. CampaignLearningService uses generate() (not chat()) and tracks costs
4. SpendAdvisorAgent.execute() returns cost data in AgentResult
5. StreamChunk final chunk carries usage data
"""
import json
import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4, UUID

from app.services.llm_service import LLMResponse, StreamChunk, LLMMessage
from app.models.llm_usage import LLMUsage, LLMUsageSource


# =============================================================================
# LLMUsageService Tests
# =============================================================================


class TestLLMUsageServiceTrack:
    """Test the LLMUsageService.track() method."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_track_creates_usage_record(self, mock_db):
        """Track creates an LLMUsage record in the database."""
        from app.services.llm_usage_service import LLMUsageService

        service = LLMUsageService()
        user_id = uuid4()

        result = await service.track(
            db=mock_db,
            source=LLMUsageSource.AGENT_CHAT,
            provider="anthropic",
            model="claude-3-haiku",
            prompt_tokens=100,
            completion_tokens=50,
            user_id=user_id,
            latency_ms=500,
            meta_data={"agent": "proposal_writer"},
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_awaited_once()

        added_obj = mock_db.add.call_args[0][0]
        assert isinstance(added_obj, LLMUsage)
        assert added_obj.source == LLMUsageSource.AGENT_CHAT
        assert added_obj.provider == "anthropic"
        assert added_obj.model == "claude-3-haiku"
        assert added_obj.prompt_tokens == 100
        assert added_obj.completion_tokens == 50
        assert added_obj.total_tokens == 150
        assert added_obj.user_id == user_id
        assert added_obj.meta_data == {"agent": "proposal_writer"}

    @pytest.mark.asyncio
    async def test_track_calculates_cost_when_not_provided(self, mock_db):
        """Track auto-calculates cost_usd if not explicitly provided."""
        from app.services.llm_usage_service import LLMUsageService

        service = LLMUsageService()

        result = await service.track(
            db=mock_db,
            source=LLMUsageSource.BRAINSTORM,
            provider="openai",
            model="gpt-4o-mini",
            prompt_tokens=1000,
            completion_tokens=500,
        )

        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.cost_usd is not None
        assert added_obj.cost_usd >= 0

    @pytest.mark.asyncio
    async def test_track_uses_provided_cost(self, mock_db):
        """Track uses explicit cost_usd when provided."""
        from app.services.llm_usage_service import LLMUsageService

        service = LLMUsageService()

        result = await service.track(
            db=mock_db,
            source=LLMUsageSource.CAMPAIGN,
            provider="anthropic",
            model="claude-3-haiku",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.00123,
        )

        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.cost_usd == 0.00123

    @pytest.mark.asyncio
    async def test_track_from_response(self, mock_db):
        """track_from_response extracts fields from LLMResponse."""
        from app.services.llm_usage_service import LLMUsageService

        service = LLMUsageService()

        response = LLMResponse(
            content="test",
            model="claude-3-haiku",
            provider="anthropic",
            latency_ms=300,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            cost_usd=0.001,
        )

        result = await service.track_from_response(
            db=mock_db,
            source=LLMUsageSource.AGENT_TASK,
            response=response,
        )

        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.model == "claude-3-haiku"
        assert added_obj.provider == "anthropic"
        assert added_obj.prompt_tokens == 200
        assert added_obj.completion_tokens == 100
        assert added_obj.cost_usd == 0.001

    @pytest.mark.asyncio
    async def test_track_with_all_foreign_keys(self, mock_db):
        """Track correctly stores all FK references."""
        from app.services.llm_usage_service import LLMUsageService

        service = LLMUsageService()
        user_id = uuid4()
        conv_id = uuid4()
        msg_id = uuid4()
        run_id = uuid4()
        campaign_id = uuid4()

        result = await service.track(
            db=mock_db,
            source=LLMUsageSource.AGENT_CHAT,
            provider="openai",
            model="gpt-4o",
            prompt_tokens=50,
            completion_tokens=25,
            user_id=user_id,
            conversation_id=conv_id,
            message_id=msg_id,
            agent_run_id=run_id,
            campaign_id=campaign_id,
        )

        added_obj = mock_db.add.call_args[0][0]
        assert added_obj.user_id == user_id
        assert added_obj.conversation_id == conv_id
        assert added_obj.message_id == msg_id
        assert added_obj.agent_run_id == run_id
        assert added_obj.campaign_id == campaign_id


# =============================================================================
# CampaignPlanService Cost Tracking Tests
# =============================================================================


class TestCampaignPlanServiceCostTracking:
    """Test that CampaignPlanService tracks LLM costs."""

    @pytest.mark.asyncio
    async def test_generate_execution_plan_tracks_cost(self):
        """generate_execution_plan calls llm_usage_service.track after LLM call."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=LLMResponse(
            content=json.dumps({
                "streams": [{
                    "name": "Setup",
                    "description": "Initial setup",
                    "tasks": [{"name": "Task 1", "description": "Do thing", "tool_slug": None}],
                    "can_run_parallel": False,
                }],
                "input_requirements": [],
            }),
            model="claude-3-sonnet",
            provider="anthropic",
            latency_ms=2000,
            prompt_tokens=500,
            completion_tokens=1000,
            total_tokens=1500,
            cost_usd=0.0075,
        ))

        # Create a mock proposal
        proposal = MagicMock()
        proposal.title = "Test Proposal"
        proposal.detailed_description = "Test description"
        proposal.implementation_timeline = None
        proposal.success_criteria = None
        proposal.initial_budget = 100
        proposal.expected_returns = None

        from app.services.campaign_plan_service import CampaignPlanService

        service = CampaignPlanService(mock_db, mock_llm)

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            plan = await service.generate_execution_plan(
                proposal=proposal,
                available_tools=[],
            )

            # Verify LLM was called
            mock_llm.generate.assert_awaited_once()

            # Verify cost tracking was called
            mock_tracker.track.assert_awaited_once()
            track_kwargs = mock_tracker.track.call_args
            assert track_kwargs.kwargs["source"] == LLMUsageSource.CAMPAIGN
            assert track_kwargs.kwargs["provider"] == "anthropic"
            assert track_kwargs.kwargs["model"] == "claude-3-sonnet"
            assert track_kwargs.kwargs["prompt_tokens"] == 500
            assert track_kwargs.kwargs["completion_tokens"] == 1000
            assert track_kwargs.kwargs["cost_usd"] == 0.0075


# =============================================================================
# CampaignLearningService Tests
# =============================================================================


class TestCampaignLearningServiceFixedBug:
    """Test that CampaignLearningService uses generate() not chat()."""

    @pytest.mark.asyncio
    async def test_analyze_for_revision_uses_generate(self):
        """analyze_for_revision uses llm_service.generate() (not .chat())."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=MagicMock(
            content='```json\n{"should_revise": true, "reason": "test", "changes": {}, "expected_benefit": "better", "risk_level": "low"}\n```',
            provider="anthropic",
            model="claude-3-haiku",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
            latency_ms=200,
        ))
        # Ensure chat() is NOT present / would fail
        mock_llm.chat = MagicMock(side_effect=AttributeError("chat() does not exist"))

        from app.services.campaign_learning_service import CampaignLearningService

        campaign = MagicMock()
        campaign.id = uuid4()
        campaign.status = MagicMock(value="running")
        campaign.budget_allocated = Decimal("100")
        campaign.budget_spent = Decimal("20")

        stream = MagicMock()
        stream.name = "Test Stream"
        stream.status = MagicMock(value="active")
        stream.blocking_reasons = None
        campaign.task_streams = [stream]

        # Mock campaign query
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = campaign
        mock_db.execute.return_value = mock_result

        service = CampaignLearningService(mock_db, mock_llm)

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            result = await service.analyze_for_revision(
                campaign=campaign,
                trigger=MagicMock(value="stream_blocked"),
                trigger_details="Stream is blocked",
            )

            # Verify generate() was called, not chat()
            mock_llm.generate.assert_awaited_once()
            mock_llm.chat.assert_not_called()

            # Verify cost tracking
            mock_tracker.track.assert_awaited_once()
            track_kwargs = mock_tracker.track.call_args
            assert track_kwargs.kwargs["source"] == LLMUsageSource.CAMPAIGN
            assert track_kwargs.kwargs["meta_data"] == {"action": "revision_analysis"}


# =============================================================================
# SpendAdvisor Cost Tracking Tests
# =============================================================================


class TestSpendAdvisorCostTracking:
    """Test that SpendAdvisor returns cost data."""

    @pytest.mark.asyncio
    async def test_execute_returns_cost_in_agent_result(self):
        """execute() returns tokens_used and cost_usd in AgentResult."""
        from app.agents.spend_advisor import SpendAdvisorAgent

        agent = SpendAdvisorAgent()
        mock_db = AsyncMock()
        context = MagicMock()
        context.db = mock_db

        mock_response = LLMResponse(
            content="Approved. The spend is within budget.",
            model="claude-3-haiku",
            provider="anthropic",
            latency_ms=400,
            prompt_tokens=300,
            completion_tokens=100,
            total_tokens=400,
            cost_usd=0.0005,
        )

        with patch.object(agent, "think", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.execute(
                context=context,
                approval_data={"amount": 1000},
                budget_context={"remaining": 5000},
            )

        assert result.success is True
        assert result.tokens_used == 400
        assert result.cost_usd == 0.0005
        assert result.model_used == "claude-3-haiku"
        assert result.latency_ms == 400
        assert "Approved" in result.message

    @pytest.mark.asyncio
    async def test_analyze_spend_returns_content_string(self):
        """analyze_spend() still returns just the text content."""
        from app.agents.spend_advisor import SpendAdvisorAgent

        agent = SpendAdvisorAgent()

        mock_response = LLMResponse(
            content="Analysis: looks good",
            model="claude-3-haiku",
            provider="anthropic",
            latency_ms=300,
            prompt_tokens=200,
            completion_tokens=50,
            total_tokens=250,
            cost_usd=0.0003,
        )

        with patch.object(agent, "think", new_callable=AsyncMock, return_value=mock_response):
            result = await agent.analyze_spend(
                approval_data={"amount": 500},
                budget_context={"remaining": 3000},
            )

        assert isinstance(result, str)
        assert result == "Analysis: looks good"


# =============================================================================
# StreamChunk Final Chunk Tests
# =============================================================================


class TestStreamChunkUsageData:
    """Test that StreamChunk carries usage data on final chunk."""

    def test_final_chunk_has_usage_fields(self):
        """The StreamChunk dataclass supports usage tracking fields."""
        chunk = StreamChunk(
            content="",
            is_final=True,
            model="gpt-4o",
            provider="openai",
            prompt_tokens=500,
            completion_tokens=200,
            total_tokens=700,
            latency_ms=1500,
        )
        assert chunk.is_final is True
        assert chunk.prompt_tokens == 500
        assert chunk.completion_tokens == 200
        assert chunk.total_tokens == 700
        assert chunk.model == "gpt-4o"
        assert chunk.provider == "openai"
        assert chunk.latency_ms == 1500

    def test_non_final_chunk_defaults(self):
        """Non-final chunks have zero token counts."""
        chunk = StreamChunk(content="Hello")
        assert chunk.is_final is False
        assert chunk.prompt_tokens == 0
        assert chunk.completion_tokens == 0
        assert chunk.total_tokens == 0


# =============================================================================
# LLMUsageSource Enum Tests
# =============================================================================


class TestLLMUsageSourceEnum:
    """Test that all required source types exist."""

    def test_agent_chat_source_exists(self):
        """AGENT_CHAT source is available for WebSocket chat tracking."""
        assert LLMUsageSource.AGENT_CHAT == "agent_chat"

    def test_campaign_source_exists(self):
        """CAMPAIGN source is available for campaign service tracking."""
        assert LLMUsageSource.CAMPAIGN == "campaign"

    def test_all_expected_sources(self):
        """All expected source types are defined."""
        expected = {"brainstorm", "agent_chat", "agent_task", "campaign", "tool", "other"}
        actual = {s.value for s in LLMUsageSource}
        assert expected == actual


# =============================================================================
# AgentSchedulerService.complete_run() Tracking Tests
# =============================================================================


class TestCompleteRunTracking:
    """Test that complete_run() tracks to llm_usage table."""

    @pytest.fixture
    def mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.refresh = AsyncMock()
        return db

    @pytest.mark.asyncio
    async def test_complete_run_tracks_llm_usage(self, mock_db):
        """complete_run tracks cost to llm_usage when tokens > 0."""
        from app.services.agent_scheduler_service import AgentSchedulerService

        service = AgentSchedulerService()
        run_id = uuid4()
        agent_id = uuid4()

        # Mock the run object
        mock_run = MagicMock()
        mock_run.id = run_id
        mock_run.agent_id = agent_id
        mock_run.started_at = datetime(2025, 1, 1, 12, 0, 0)
        mock_run.duration_seconds = None
        mock_db.get = AsyncMock(side_effect=[mock_run, None])  # run, then no agent

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            await service.complete_run(
                db=mock_db,
                run_id=run_id,
                tokens_used=500,
                cost_usd=0.005,
                model_used="claude-3-haiku",
            )

            mock_tracker.track.assert_awaited_once()
            kwargs = mock_tracker.track.call_args.kwargs
            assert kwargs["source"] == LLMUsageSource.AGENT_TASK
            assert kwargs["provider"] == "anthropic"
            assert kwargs["model"] == "claude-3-haiku"
            assert kwargs["agent_run_id"] == run_id
            assert kwargs["cost_usd"] == 0.005

    @pytest.mark.asyncio
    async def test_complete_run_skips_tracking_when_no_tokens(self, mock_db):
        """complete_run does NOT track when tokens_used is 0."""
        from app.services.agent_scheduler_service import AgentSchedulerService

        service = AgentSchedulerService()
        run_id = uuid4()

        mock_run = MagicMock()
        mock_run.id = run_id
        mock_run.agent_id = uuid4()
        mock_run.started_at = datetime(2025, 1, 1, 12, 0, 0)
        mock_run.duration_seconds = None
        mock_db.get = AsyncMock(side_effect=[mock_run, None])

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            await service.complete_run(
                db=mock_db,
                run_id=run_id,
                tokens_used=0,
                cost_usd=0.0,
                model_used=None,
            )

            mock_tracker.track.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_run_detects_openai_provider(self, mock_db):
        """complete_run correctly identifies OpenAI provider from model name."""
        from app.services.agent_scheduler_service import AgentSchedulerService

        service = AgentSchedulerService()
        run_id = uuid4()

        mock_run = MagicMock()
        mock_run.id = run_id
        mock_run.agent_id = uuid4()
        mock_run.started_at = datetime(2025, 1, 1, 12, 0, 0)
        mock_run.duration_seconds = None
        mock_db.get = AsyncMock(side_effect=[mock_run, None])

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            await service.complete_run(
                db=mock_db,
                run_id=run_id,
                tokens_used=100,
                cost_usd=0.001,
                model_used="gpt-4o-mini",
            )

            kwargs = mock_tracker.track.call_args.kwargs
            assert kwargs["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_complete_run_detects_zhipu_provider(self, mock_db):
        """complete_run correctly identifies Zhipu provider from model name."""
        from app.services.agent_scheduler_service import AgentSchedulerService

        service = AgentSchedulerService()
        run_id = uuid4()

        mock_run = MagicMock()
        mock_run.id = run_id
        mock_run.agent_id = uuid4()
        mock_run.started_at = datetime(2025, 1, 1, 12, 0, 0)
        mock_run.duration_seconds = None
        mock_db.get = AsyncMock(side_effect=[mock_run, None])

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            await service.complete_run(
                db=mock_db,
                run_id=run_id,
                tokens_used=200,
                cost_usd=0.0,
                model_used="glm-4-flash",
            )

            kwargs = mock_tracker.track.call_args.kwargs
            assert kwargs["provider"] == "zhipu"


# =============================================================================
# UsageService Uses llm_usage Table Tests
# =============================================================================


class TestUsageServiceUsesLLMUsage:
    """Test that UsageService queries llm_usage instead of messages."""

    @pytest.mark.asyncio
    async def test_get_token_usage_by_model_queries_llm_usage(self):
        """_get_token_usage_by_model queries LLMUsage table, not Message."""
        from app.services.usage_service import UsageService
        from app.models.llm_usage import LLMUsage

        service = UsageService()
        mock_db = AsyncMock()

        # Mock the DB result to return model-grouped rows
        mock_row = MagicMock()
        mock_row.model = "claude-3-haiku"
        mock_row.call_count = 10
        mock_row.prompt_tokens = 1000
        mock_row.completion_tokens = 500
        mock_row.total_tokens = 1500
        mock_row.total_cost = 0.005

        mock_result = MagicMock()
        mock_result.all.return_value = [mock_row]
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await service._get_token_usage_by_model(
            db=mock_db,
            start_date=datetime(2025, 1, 1),
            end_date=datetime(2025, 1, 31),
        )

        # Verify one row returned with correct data
        assert len(result) == 1
        assert result[0].model == "claude-3-haiku"
        assert result[0].message_count == 10  # call_count mapped to message_count
        assert result[0].prompt_tokens == 1000
        assert result[0].completion_tokens == 500
        assert result[0].total_tokens == 1500

        # Verify the query was executed (contains LLMUsage reference)
        execute_call = mock_db.execute.call_args[0][0]
        # The query should reference llm_usage table, not messages
        query_str = str(execute_call)
        assert "llm_usage" in query_str


# =============================================================================
# send_message() No Longer Writes cost_usd Tests
# =============================================================================


class TestSendMessageNoCostUsd:
    """Test that send_message no longer stores cost_usd on messages."""

    @pytest.mark.asyncio
    async def test_send_message_does_not_set_cost_usd(self):
        """send_message creates Message without cost_usd field."""
        from app.agents.base import BaseAgent, AgentContext
        from app.models import Message

        # Create a minimal concrete subclass since BaseAgent is abstract
        class _TestAgent(BaseAgent):
            def __init__(self):
                self.name = "test_agent"
                self.slug = "test-agent"
            async def execute(self, context, **kwargs):
                pass
            def get_system_prompt(self, context=None):
                return "test"

        agent = _TestAgent()

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        context = AgentContext(db=mock_db, conversation_id=uuid4())

        await agent.send_message(
            context=context,
            content="Test response",
            tokens_used=100,
            model_used="claude-3-haiku",
            prompt_tokens=30,
            completion_tokens=70,
            cost_usd=0.001,  # Even if passed, should NOT be stored
        )

        mock_db.add.assert_called_once()
        added_msg = mock_db.add.call_args[0][0]
        assert isinstance(added_msg, Message)
        assert added_msg.tokens_used == 100
        assert added_msg.model_used == "claude-3-haiku"
        # cost_usd should NOT be set on the message
        assert added_msg.cost_usd is None or not hasattr(added_msg, 'cost_usd') or added_msg.cost_usd is None


# =============================================================================
# StreamExecutor LLM Tracking Tests
# =============================================================================


class TestStreamExecutorLLMTracking:
    """Test that stream_executor_service tracks LLM calls."""

    @pytest.mark.asyncio
    async def test_execute_llm_task_tracks_usage(self):
        """_execute_llm_task tracks to llm_usage after successful LLM call."""
        from app.services.stream_executor_service import StreamExecutorService

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value=LLMResponse(
            content="Generated analysis",
            model="glm-4-flash",
            provider="zhipu",
            latency_ms=500,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            cost_usd=0.0,
        ))

        service = StreamExecutorService(db=mock_db, llm_service=mock_llm)

        task = MagicMock()
        task.id = uuid4()
        task.campaign_id = uuid4()
        task.llm_prompt = "Analyze this: {{input}}"
        task.result = None

        with patch("app.services.llm_usage_service.llm_usage_service") as mock_tracker:
            mock_tracker.track = AsyncMock()

            result = await service._execute_llm_task(task, {"input": "test data"})

            assert result is True
            mock_tracker.track.assert_awaited_once()
            kwargs = mock_tracker.track.call_args.kwargs
            assert kwargs["source"] == LLMUsageSource.CAMPAIGN
            assert kwargs["provider"] == "zhipu"
            assert kwargs["model"] == "glm-4-flash"
            assert kwargs["campaign_id"] == task.campaign_id


# =============================================================================
# Tool Cost Tracking Tests
# =============================================================================


class TestToolCostPricing:
    """Test that TOOL_PRICING is applied correctly per tool slug."""

    def test_serper_pricing(self):
        """Serper is priced at $0.001 per search."""
        from app.services.usage_service import estimate_tool_cost
        cost = estimate_tool_cost("serper-web-search", 100)
        assert cost == 0.1  # 100 searches × $0.001

    def test_dalle_pricing(self):
        """DALL-E is priced at $0.04 per image."""
        from app.services.usage_service import estimate_tool_cost
        cost = estimate_tool_cost("openai-dalle-3", 3)
        assert cost == 0.12  # 3 images × $0.04

    def test_elevenlabs_pricing(self):
        """ElevenLabs is priced at $0.00003 per character."""
        from app.services.usage_service import estimate_tool_cost
        cost = estimate_tool_cost("elevenlabs-voice-generation", 1000)
        assert cost == 0.03  # 1000 chars × $0.00003

    def test_llm_tool_zero_cost_in_tool_pricing(self):
        """LLM tools have $0 tool pricing (costs tracked in llm_usage)."""
        from app.services.usage_service import TOOL_PRICING
        assert TOOL_PRICING["zai-glm-47"] == 0.0
        assert TOOL_PRICING["anthropic-claude-sonnet-45"] == 0.0
        assert TOOL_PRICING["openai-gpt-52"] == 0.0

    def test_unknown_tool_gets_default_pricing(self):
        """Unknown tools fall back to $0.001/unit."""
        from app.services.usage_service import estimate_tool_cost
        cost = estimate_tool_cost("some-custom-tool", 10)
        assert cost == 0.01  # 10 × $0.001


class TestDalleCostUnits:
    """Test that DALL-E tracks cost_units per image generated."""

    @pytest.mark.asyncio
    async def test_dalle_cost_units_match_image_count(self):
        """DALL-E cost_units reflects actual number of images generated."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        tool = MagicMock()
        tool.slug = "openai-dalle-3"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": [
                {"url": "https://example.com/img1.png", "revised_prompt": "a cat"},
                {"url": "https://example.com/img2.png", "revised_prompt": "a dog"},
            ]
        }

        with patch.object(executor, "_get_client") as mock_client:
            client = AsyncMock()
            client.post = AsyncMock(return_value=mock_response)
            mock_client.return_value = client

            with patch("app.services.tool_execution_service.settings") as mock_settings:
                mock_settings.OPENAI_API_KEY = "test-key"

                result = await executor._execute_dalle(tool, {"prompt": "test", "n": 2})

        assert result.success is True
        assert result.cost_units == 2  # 2 images generated
        assert result.cost_details["images_generated"] == 2


class TestLLMToolNoDoubleCounting:
    """Test that LLM tools don't double-count via both tool_cost and llm_cost."""

    @pytest.mark.asyncio
    async def test_llm_tool_cost_units_zero(self):
        """LLM tool executors set cost_units=0 (costs tracked in llm_usage)."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        tool = MagicMock()
        tool.slug = "zai-glm-47"

        mock_response = LLMResponse(
            content="Generated text",
            model="glm-4-flash",
            provider="zhipu",
            latency_ms=500,
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
            cost_usd=0.0,
        )

        with patch("app.services.llm_service.llm_service") as mock_llm:
            mock_llm.generate = AsyncMock(return_value=mock_response)

            result = await executor._execute_llm(tool, {"prompt": "test"})

        assert result.success is True
        assert result.cost_units == 0  # NOT total_tokens
        assert result.cost_details["prompt_tokens"] == 200
        assert result.cost_details["provider"] == "zhipu"


class TestCustomToolCostDetails:
    """Test that custom tools can use cost_details for pricing."""

    @pytest.mark.asyncio
    async def test_custom_tool_reads_cost_per_execution(self):
        """execute() sets cost_units=1 for custom tools with cost_per_execution."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        tool = MagicMock()
        tool.slug = "my-custom-api"
        tool.interface_type = "rest_api"
        tool.interface_config = {"base_url": "http://example.com", "endpoint": {"path": "/run"}}
        tool.cost_details = {"cost_per_execution": 0.05}
        tool.timeout_seconds = 30

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.cost_units = 0
        mock_result.cost_details = None
        mock_result.duration_ms = 100

        with patch.object(executor, "_execute_dynamic", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute(tool, {"input": "test"})

        assert result.cost_units == 1
        assert result.cost_details["cost_per_execution"] == 0.05

    @pytest.mark.asyncio
    async def test_hardcoded_tool_not_overridden(self):
        """Hardcoded tools with cost_units > 0 are NOT overridden."""
        from app.services.tool_execution_service import ToolExecutor

        executor = ToolExecutor()
        tool = MagicMock()
        tool.slug = "serper-web-search"
        tool.interface_type = None
        tool.interface_config = None
        tool.cost_details = None
        tool.timeout_seconds = 30

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.cost_units = 1  # Already set by _execute_serper
        mock_result.cost_details = {"search_credits": 1}
        mock_result.duration_ms = 200

        with patch.object(executor, "_execute_serper_search", new_callable=AsyncMock, return_value=mock_result):
            # Need to register in EXECUTORS for this to work
            executor.EXECUTORS[tool.slug] = "_execute_serper_search"
            result = await executor.execute(tool, {"query": "test"})

        # cost_units should remain 1 (not overridden)
        assert result.cost_units == 1


# =============================================================================
# Phase 7b: Campaign-level cost attribution
# =============================================================================


class TestToolExecutionCampaignId:
    """Test that ToolExecution model has campaign_id FK."""

    def test_tool_execution_has_campaign_id_column(self):
        """ToolExecution model should have a campaign_id column."""
        from app.models.resource import ToolExecution
        assert hasattr(ToolExecution, 'campaign_id')

    def test_agent_run_has_campaign_id_column(self):
        """AgentRun model should have a campaign_id column."""
        from app.models.agent_scheduler import AgentRun
        assert hasattr(AgentRun, 'campaign_id')


class TestExecuteToolAcceptsCampaignId:
    """Test that execute_tool() accepts and stores campaign_id."""

    @pytest.mark.asyncio
    async def test_execute_tool_signature_has_campaign_id(self):
        """execute_tool() should accept campaign_id parameter."""
        import inspect
        from app.services.tool_execution_service import ToolExecutionService
        sig = inspect.signature(ToolExecutionService.execute_tool)
        assert 'campaign_id' in sig.parameters

    @pytest.mark.asyncio
    async def test_execute_tool_by_slug_passes_campaign_id(self):
        """execute_tool_by_slug() should forward campaign_id via **kwargs."""
        import inspect
        from app.services.tool_execution_service import ToolExecutionService
        # execute_tool_by_slug uses **kwargs, so campaign_id flows through
        sig = inspect.signature(ToolExecutionService.execute_tool_by_slug)
        assert 'kwargs' in [p.kind.name for p in sig.parameters.values()] or \
            any(p.name == 'kwargs' or p.kind == inspect.Parameter.VAR_KEYWORD
                for p in sig.parameters.values())


class TestBaseAgentForwardsCampaignId:
    """Test that BaseAgent.execute_tool_calls forwards context.related_id as campaign_id."""

    @pytest.mark.asyncio
    async def test_execute_tool_calls_passes_related_id(self):
        """execute_tool_calls should pass context.related_id as campaign_id."""
        from app.agents.base import BaseAgent, AgentContext
        from uuid import uuid4

        # Create a concrete subclass to avoid abstract method errors
        class _TestAgent(BaseAgent):
            name = "test_agent"
            async def execute(self, context):
                pass
            def get_system_prompt(self, context):
                return ""

        agent = _TestAgent()

        campaign_id = uuid4()
        context = AgentContext(
            db=MagicMock(),
            conversation_id=uuid4(),
            related_id=campaign_id,
            user_id=uuid4(),
        )

        mock_execution = MagicMock()
        mock_execution.status.value = "completed"
        mock_execution.output_result = {"ok": True}
        mock_execution.duration_ms = 100

        with patch(
            "app.services.tool_execution_service.tool_execution_service.execute_tool_by_slug",
            new_callable=AsyncMock,
            return_value=mock_execution,
        ) as mock_exec:
            await agent.execute_tool_calls(context, [
                {"tool_slug": "serper-web-search", "params": {"query": "test"}}
            ])

            # Verify campaign_id was passed
            mock_exec.assert_called_once()
            call_kwargs = mock_exec.call_args
            assert call_kwargs.kwargs.get("campaign_id") == campaign_id


class TestCreateRunAcceptsCampaignId:
    """Test that create_run() accepts and stores campaign_id."""

    @pytest.mark.asyncio
    async def test_create_run_signature_has_campaign_id(self):
        """create_run() should accept campaign_id parameter."""
        import inspect
        from app.services.agent_scheduler_service import AgentSchedulerService
        sig = inspect.signature(AgentSchedulerService.create_run)
        assert 'campaign_id' in sig.parameters


class TestCompleteRunPropagatesCampaignId:
    """Test that complete_run() passes campaign_id to llm_usage tracking."""

    @pytest.mark.asyncio
    async def test_complete_run_tracks_campaign_id_to_llm_usage(self):
        """complete_run() should propagate run.campaign_id to llm_usage_service.track()."""
        from app.services.agent_scheduler_service import AgentSchedulerService
        from app.models.agent_scheduler import AgentRun, AgentRunStatus, AgentDefinition, AgentStatus
        from uuid import uuid4

        service = AgentSchedulerService()
        run_id = uuid4()
        agent_id = uuid4()
        campaign_id = uuid4()

        mock_run = MagicMock(spec=AgentRun)
        mock_run.id = run_id
        mock_run.agent_id = agent_id
        mock_run.status = AgentRunStatus.RUNNING
        mock_run.started_at = MagicMock()
        mock_run.started_at.tzinfo = None
        mock_run.campaign_id = campaign_id
        mock_run.duration_seconds = 5.0

        mock_agent = MagicMock(spec=AgentDefinition)
        mock_agent.id = agent_id
        mock_agent.status = AgentStatus.RUNNING
        mock_agent.total_runs = 0
        mock_agent.successful_runs = 0
        mock_agent.schedule_interval_seconds = 3600
        mock_agent.budget_limit = None
        mock_agent.budget_used = 0
        mock_agent.total_cost_usd = 0
        mock_agent.total_tokens_used = 0
        mock_agent.budget_period = MagicMock()
        mock_agent.budget_period.value = "daily"
        mock_agent.budget_reset_at = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(side_effect=lambda model, id: {
            run_id: mock_run,
            agent_id: mock_agent,
        }.get(id))

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        with patch("app.services.agent_scheduler_service.utc_now", return_value=now) as mock_utc, \
             patch("app.services.agent_scheduler_service.ensure_utc", return_value=now - timedelta(seconds=5)), \
             patch("app.services.llm_usage_service.llm_usage_service.track", new_callable=AsyncMock) as mock_track:

            await service.complete_run(
                db=mock_db,
                run_id=run_id,
                tokens_used=500,
                cost_usd=0.01,
                model_used="glm-4-flash",
            )

            # Verify campaign_id was passed to llm_usage tracking
            mock_track.assert_called_once()
            call_kwargs = mock_track.call_args
            assert call_kwargs.kwargs.get("campaign_id") == campaign_id


class TestCampaignFinancialsResponse:
    """Test that CampaignFinancials response includes computed API costs."""

    def test_campaign_financials_has_cost_fields(self):
        """CampaignFinancials should have llm_cost, tool_cost, api_cost_total fields."""
        from app.api.endpoints.usage import CampaignFinancials
        fields = CampaignFinancials.model_fields
        assert 'llm_cost' in fields
        assert 'tool_cost' in fields
        assert 'api_cost_total' in fields
