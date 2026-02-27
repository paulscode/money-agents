"""Tests for CampaignPlanService.

Covers:
- _parse_plan_data (pure function, JSON → dataclass)
- _extract_json_from_response (robust JSON extraction from LLM output)
- _format_tools_for_prompt
- create_campaign_streams (DB record creation)
- create_input_requests
- update_stream_readiness
"""
import pytest
import pytest_asyncio
from uuid import uuid4

from app.models import (
    Campaign, Proposal, TaskStream, CampaignTask, UserInputRequest, User,
    TaskStreamStatus, TaskStatus, TaskType, InputStatus, InputPriority,
    InputType, CampaignStatus, ProposalStatus,
)
from app.services.campaign_plan_service import (
    CampaignPlanService,
    ExecutionPlan,
    StreamDefinition,
    TaskDefinition,
    InputRequirement,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def user(db_session):
    from app.core.security import get_password_hash
    u = User(
        username="plan_user",
        email="plan@test.com",
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
        title="Plan Test Proposal",
        summary="A test",
        detailed_description="Detailed",
        initial_budget=500.0,
        status=ProposalStatus.APPROVED,
        user_id=user.id,
        risk_level="medium",
        risk_description="Moderate risk",
        stop_loss_threshold={"max_loss": 250},
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
        budget_allocated=500.0,
        success_metrics={"target": "test"},
        requirements_checklist={},
    )
    db_session.add(c)
    await db_session.flush()
    return c


@pytest_asyncio.fixture
async def service(db_session):
    return CampaignPlanService(db_session)


def _make_plan(
    streams=None,
    input_requirements=None,
    duration=60,
    parallelization=1.0,
):
    """Helper to build an ExecutionPlan quickly."""
    return ExecutionPlan(
        streams=streams or [],
        input_requirements=input_requirements or [],
        estimated_total_duration_minutes=duration,
        parallelization_factor=parallelization,
    )


# ---------------------------------------------------------------------------
# _parse_plan_data
# ---------------------------------------------------------------------------

class TestParsePlanData:

    def test_minimal_plan(self, db_session):
        """Minimal valid plan data should parse without errors."""
        svc = CampaignPlanService(db_session)
        data = {
            "streams": [
                {
                    "name": "main",
                    "description": "Main stream",
                    "tasks": [
                        {
                            "name": "Task 1",
                            "description": "Do something",
                            "task_type": "llm_reasoning",
                        }
                    ],
                }
            ],
            "input_requirements": [],
            "estimated_total_duration_minutes": 30,
            "parallelization_factor": 1.0,
        }
        plan = svc._parse_plan_data(data)
        assert len(plan.streams) == 1
        assert plan.streams[0].name == "main"
        assert len(plan.streams[0].tasks) == 1
        assert plan.estimated_total_duration_minutes == 30

    def test_filters_existing_credentials(self, db_session):
        """Input requirements for existing credentials should be skipped."""
        svc = CampaignPlanService(db_session)
        data = {
            "streams": [],
            "input_requirements": [
                {"key": "serper_key", "input_type": "credentials", "title": "Serper", "description": "x"},
                {"key": "notion_key", "input_type": "credentials", "title": "Notion", "description": "y"},
            ],
            "estimated_total_duration_minutes": 10,
            "parallelization_factor": 1.0,
        }
        plan = svc._parse_plan_data(data, existing_credentials=["serper_key"])
        assert len(plan.input_requirements) == 1
        assert plan.input_requirements[0].key == "notion_key"

    def test_complex_plan_with_deps(self, db_session):
        """Plan with stream and task dependencies should parse correctly."""
        svc = CampaignPlanService(db_session)
        data = {
            "streams": [
                {
                    "name": "research",
                    "description": "Research",
                    "tasks": [
                        {"name": "Search", "description": "x", "task_type": "tool_execution", "tool_slug": "web_search"},
                    ],
                    "can_run_parallel": True,
                    "max_concurrent": 2,
                },
                {
                    "name": "content",
                    "description": "Content",
                    "depends_on_streams": ["research"],
                    "requires_inputs": ["brand_name"],
                    "tasks": [
                        {
                            "name": "Write",
                            "description": "x",
                            "task_type": "llm_reasoning",
                            "depends_on_tasks": ["Search"],
                            "depends_on_inputs": ["brand_name"],
                        },
                    ],
                },
            ],
            "input_requirements": [
                {"key": "brand_name", "input_type": "text", "title": "Brand", "description": "Brand name", "priority": "blocking"},
            ],
            "estimated_total_duration_minutes": 120,
            "parallelization_factor": 1.5,
        }
        plan = svc._parse_plan_data(data)
        assert len(plan.streams) == 2
        assert plan.streams[1].depends_on_streams == ["research"]
        assert plan.streams[0].can_run_parallel is True
        assert plan.parallelization_factor == 1.5

    def test_defaults_for_missing_fields(self, db_session):
        """Missing optional fields should get sensible defaults."""
        svc = CampaignPlanService(db_session)
        data = {
            "streams": [{"tasks": [{}]}],
        }
        plan = svc._parse_plan_data(data)
        s = plan.streams[0]
        assert s.name == "main"  # default
        assert s.estimated_duration_minutes == 60
        t = s.tasks[0]
        assert t.name == "Unnamed Task"
        assert t.task_type == "llm_reasoning"
        assert t.is_critical is True


# ---------------------------------------------------------------------------
# _extract_json_from_response
# ---------------------------------------------------------------------------

class TestExtractJsonFromResponse:

    def test_plain_json(self, db_session):
        svc = CampaignPlanService(db_session)
        raw = '{"streams": [], "input_requirements": []}'
        result = svc._extract_json_from_response(raw)
        assert result["streams"] == []

    def test_json_in_code_block(self, db_session):
        svc = CampaignPlanService(db_session)
        raw = 'Here is the plan:\n```json\n{"streams": [{"name": "a"}]}\n```\nHope it helps.'
        result = svc._extract_json_from_response(raw)
        assert result["streams"][0]["name"] == "a"

    def test_json_in_generic_code_block(self, db_session):
        svc = CampaignPlanService(db_session)
        raw = '```\n{"key": "val"}\n```'
        result = svc._extract_json_from_response(raw)
        assert result["key"] == "val"

    def test_fallback_on_garbage(self, db_session):
        """Unparseable text should return a minimal fallback plan."""
        svc = CampaignPlanService(db_session)
        result = svc._extract_json_from_response("totally invalid stuff")
        assert "streams" in result
        assert len(result["streams"]) == 1


# ---------------------------------------------------------------------------
# _format_tools_for_prompt
# ---------------------------------------------------------------------------

class TestFormatToolsForPrompt:

    def test_no_tools(self, db_session):
        svc = CampaignPlanService(db_session)
        assert svc._format_tools_for_prompt([]) == "No tools available"

    def test_formats_tools(self, db_session):
        svc = CampaignPlanService(db_session)
        tools = [
            {"slug": "web_search", "description": "Search the web"},
            {"slug": "email", "description": "Send emails"},
        ]
        text = svc._format_tools_for_prompt(tools)
        assert "web_search" in text
        assert "email" in text


# ---------------------------------------------------------------------------
# create_campaign_streams (DB integration)
# ---------------------------------------------------------------------------

class TestCreateCampaignStreams:

    @pytest.mark.asyncio
    async def test_creates_streams_and_tasks(self, db_session, campaign, service):
        """Should create TaskStream + CampaignTask records."""
        plan = _make_plan(
            streams=[
                StreamDefinition(
                    name="research",
                    description="Research stream",
                    tasks=[
                        TaskDefinition(name="Search", description="Search web", task_type="tool_execution", tool_slug="web_search"),
                        TaskDefinition(name="Summarize", description="Summarize", task_type="llm_reasoning"),
                    ],
                ),
                StreamDefinition(
                    name="content",
                    description="Content stream",
                    depends_on_streams=["research"],
                    tasks=[
                        TaskDefinition(name="Write", description="Write copy", task_type="llm_reasoning"),
                    ],
                ),
            ]
        )
        streams = await service.create_campaign_streams(campaign, plan)
        assert len(streams) == 2
        assert streams[0].name == "research"
        assert streams[0].tasks_total == 2
        assert streams[1].depends_on_streams is not None

    @pytest.mark.asyncio
    async def test_stream_order(self, db_session, campaign, service):
        plan = _make_plan(
            streams=[
                StreamDefinition(name="A", description="a", tasks=[]),
                StreamDefinition(name="B", description="b", tasks=[]),
                StreamDefinition(name="C", description="c", tasks=[]),
            ]
        )
        streams = await service.create_campaign_streams(campaign, plan)
        assert [s.order_index for s in streams] == [0, 1, 2]


# ---------------------------------------------------------------------------
# create_input_requests
# ---------------------------------------------------------------------------

class TestCreateInputRequests:

    @pytest.mark.asyncio
    async def test_creates_input_request(self, db_session, campaign, service):
        plan = _make_plan(
            input_requirements=[
                InputRequirement(
                    key="api_key",
                    input_type="credentials",
                    title="API Key",
                    description="Enter API key",
                    priority="blocking",
                ),
            ],
            streams=[
                StreamDefinition(
                    name="s1",
                    description="x",
                    requires_inputs=["api_key"],
                    tasks=[
                        TaskDefinition(name="t1", description="x", task_type="checkpoint", depends_on_inputs=["api_key"]),
                    ],
                ),
            ],
        )
        reqs = await service.create_input_requests(campaign, plan)
        assert len(reqs) == 1
        assert reqs[0].input_key == "api_key"
        assert reqs[0].blocking_count >= 1


# ---------------------------------------------------------------------------
# update_stream_readiness
# ---------------------------------------------------------------------------

class TestUpdateStreamReadiness:

    @pytest.mark.asyncio
    async def test_pending_with_no_deps_becomes_ready(self, db_session, campaign, service):
        """Pending stream with no dependencies should become READY."""
        s = TaskStream(
            campaign_id=campaign.id,
            name="Independent",
            description="x",
            order_index=0,
            status=TaskStreamStatus.PENDING,
            tasks_total=1,
        )
        db_session.add(s)
        await db_session.flush()

        count = await service.update_stream_readiness(campaign.id)
        assert count == 1
        assert s.status == TaskStreamStatus.READY

    @pytest.mark.asyncio
    async def test_pending_with_unmet_dep_stays_blocked(self, db_session, campaign, service):
        """Pending stream with unmet dependency should become BLOCKED."""
        s1 = TaskStream(
            campaign_id=campaign.id,
            name="First",
            description="x",
            order_index=0,
            status=TaskStreamStatus.IN_PROGRESS,
            tasks_total=1,
        )
        db_session.add(s1)
        await db_session.flush()

        s2 = TaskStream(
            campaign_id=campaign.id,
            name="Second",
            description="x",
            order_index=1,
            status=TaskStreamStatus.PENDING,
            tasks_total=1,
            depends_on_streams=[str(s1.id)],
        )
        db_session.add(s2)
        await db_session.flush()

        count = await service.update_stream_readiness(campaign.id)
        assert count == 0
        assert s2.status == TaskStreamStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_pending_with_pending_input_stays_blocked(self, db_session, campaign, service):
        """Stream requiring a pending input should stay blocked."""
        s = TaskStream(
            campaign_id=campaign.id,
            name="NeedsInput",
            description="x",
            order_index=0,
            status=TaskStreamStatus.PENDING,
            tasks_total=1,
            requires_inputs=["brand_name"],
        )
        db_session.add(s)

        req = UserInputRequest(
            campaign_id=campaign.id,
            input_key="brand_name",
            input_type=InputType.TEXT,
            title="Brand",
            description="x",
            priority=InputPriority.BLOCKING,
            status=InputStatus.PENDING,
        )
        db_session.add(req)
        await db_session.flush()

        count = await service.update_stream_readiness(campaign.id)
        assert count == 0
        assert s.status == TaskStreamStatus.BLOCKED
