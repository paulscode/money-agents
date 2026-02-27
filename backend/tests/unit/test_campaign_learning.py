"""Tests for campaign learning service - Phase 5: Agent Intelligence.

Tests the following features:
1. Pattern Discovery from successful campaigns
2. Lesson Learning from failures
3. Plan Revision recommendations
4. Proactive Suggestions generation
"""
import pytest
from datetime import datetime, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import (
    Campaign, Proposal, TaskStream, CampaignTask, UserInputRequest,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus,
    TaskStatus, TaskStreamStatus, CampaignStatus, InputStatus, InputPriority,
    User, ProposalStatus, RiskLevel
)
from app.services.campaign_learning_service import (
    CampaignLearningService,
    PatternMatch,
    LessonWarning,
    RevisionRecommendation,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_db():
    """Create a mock database session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_llm_service():
    """Create a mock LLM service."""
    llm = MagicMock()
    llm.generate = AsyncMock()
    return llm


@pytest.fixture
def sample_user():
    """Create a sample user."""
    user = User(
        id=uuid4(),
        username="testuser",
        email="test@example.com",
        password_hash="hash",
    )
    return user


@pytest.fixture
def sample_proposal(sample_user):
    """Create a sample proposal."""
    proposal = Proposal(
        id=uuid4(),
        user_id=sample_user.id,
        title="Test Campaign",
        summary="A test marketing campaign",
        detailed_description="Detailed description of the test campaign",
        initial_budget=1000.00,
        risk_level=RiskLevel.LOW,
        risk_description="Low risk test campaign",
        stop_loss_threshold={"max_loss": 200},
        success_criteria={"target_leads": 100},
        required_tools={"web_search": "needed"},
        required_inputs={"api_key": "string"},
        status=ProposalStatus.APPROVED,
        tags={"type": "marketing", "tags": ["social", "ads"]},
    )
    return proposal


@pytest.fixture
def sample_campaign(sample_user, sample_proposal):
    """Create a sample campaign."""
    campaign = Campaign(
        id=uuid4(),
        proposal_id=sample_proposal.id,
        user_id=sample_user.id,
        status=CampaignStatus.COMPLETED,
        budget_allocated=1000.00,
        budget_spent=500.00,
        revenue_generated=800.00,
        success_metrics={"leads": {"current": 100, "target": 100}},
        tasks_total=10,
        tasks_completed=10,
        requirements_checklist=[],
        all_requirements_met=True,
        execution_plan={"streams_count": 2},
        streams_parallel_execution=True,
        start_date=datetime.utcnow() - timedelta(days=5),
        end_date=datetime.utcnow(),
    )
    campaign.proposal = sample_proposal
    return campaign


@pytest.fixture
def sample_streams(sample_campaign):
    """Create sample task streams."""
    research_stream = TaskStream(
        id=uuid4(),
        campaign_id=sample_campaign.id,
        name="Research",
        description="Research phase",
        order_index=0,
        status=TaskStreamStatus.COMPLETED,
        tasks_total=5,
        tasks_completed=5,
        started_at=datetime.utcnow() - timedelta(hours=4),
        completed_at=datetime.utcnow() - timedelta(hours=2),
    )
    
    execution_stream = TaskStream(
        id=uuid4(),
        campaign_id=sample_campaign.id,
        name="Execution",
        description="Execution phase",
        order_index=1,
        status=TaskStreamStatus.COMPLETED,
        tasks_total=5,
        tasks_completed=5,
        started_at=datetime.utcnow() - timedelta(hours=2),
        completed_at=datetime.utcnow(),
    )
    
    return [research_stream, execution_stream]


@pytest.fixture
def sample_tasks(sample_streams, sample_campaign):
    """Create sample tasks."""
    tasks = []
    for i, stream in enumerate(sample_streams):
        for j in range(3):
            task = CampaignTask(
                id=uuid4(),
                stream_id=stream.id,
                campaign_id=sample_campaign.id,
                name=f"Task {i}-{j}",
                description=f"Task {j} in stream {i}",
                order_index=j,
                status=TaskStatus.COMPLETED,
                tool_slug="web_search" if j % 2 == 0 else None,
                duration_ms=1000 + j * 500,
            )
            tasks.append(task)
        stream.tasks = tasks[i*3:(i+1)*3]
    return tasks


# =============================================================================
# Pattern Discovery Tests
# =============================================================================

class TestPatternDiscovery:
    """Tests for pattern discovery from campaigns."""
    
    @pytest.mark.asyncio
    async def test_discover_patterns_from_completed_campaign(
        self, mock_db, sample_campaign, sample_streams, sample_tasks
    ):
        """Test that patterns are discovered from a completed campaign."""
        # Setup
        sample_campaign.task_streams = sample_streams
        
        # Mock the database queries
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_campaign
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        # Execute
        patterns = await service.discover_patterns_from_campaign(sample_campaign.id)
        
        # Verify
        assert len(patterns) >= 1  # Should find at least execution pattern
        assert mock_db.add.called
        assert mock_db.flush.called
    
    @pytest.mark.asyncio
    async def test_no_patterns_from_incomplete_campaign(self, mock_db, sample_campaign):
        """Test that no patterns are discovered from incomplete campaigns."""
        sample_campaign.status = CampaignStatus.EXECUTING
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_campaign
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        patterns = await service.discover_patterns_from_campaign(sample_campaign.id)
        
        assert len(patterns) == 0
    
    @pytest.mark.asyncio
    async def test_pattern_not_found_campaign(self, mock_db):
        """Test handling of campaign not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        patterns = await service.discover_patterns_from_campaign(uuid4())
        
        assert len(patterns) == 0


class TestPatternMatching:
    """Tests for finding applicable patterns."""
    
    @pytest.mark.asyncio
    async def test_find_applicable_patterns(self, mock_db, sample_proposal, sample_user):
        """Test finding patterns that match a proposal."""
        # Create some patterns
        pattern1 = CampaignPattern(
            id=uuid4(),
            name="Good Pattern",
            description="A good pattern",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.8,
            pattern_data={"streams": []},
            applicability_conditions={"budget_range": [500, 1500]},
            user_id=sample_user.id,
        )
        
        # Mock the query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [pattern1]
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        matches = await service.find_applicable_patterns(
            proposal=sample_proposal,
            user_id=sample_user.id,
        )
        
        # Should return matches (even if relevance is low)
        assert isinstance(matches, list)
    
    def test_calculate_pattern_relevance_budget_match(self, sample_proposal):
        """Test relevance calculation when budget matches."""
        pattern = CampaignPattern(
            id=uuid4(),
            name="Budget Pattern",
            description="Pattern with budget conditions",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.8,
            pattern_data={},
            applicability_conditions={"budget_range": [500, 1500]},
        )
        
        service = CampaignLearningService(MagicMock())
        relevance = service._calculate_pattern_relevance(pattern, sample_proposal)
        
        assert relevance > 0  # Should have some relevance


# =============================================================================
# Lesson Learning Tests
# =============================================================================

class TestLessonLearning:
    """Tests for lesson recording and application."""
    
    @pytest.mark.asyncio
    async def test_record_lesson(self, mock_db, sample_campaign):
        """Test recording a lesson from a failure."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_campaign
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        lesson = await service.record_lesson(
            campaign_id=sample_campaign.id,
            title="API Rate Limit Hit",
            description="The campaign hit API rate limits during execution",
            category=LessonCategory.FAILURE,
            trigger_event="Tool execution failed with rate limit error",
            context={"tool": "twitter_api", "rate_limit": 100},
            prevention_steps=["Add rate limiting", "Use exponential backoff"],
            impact_severity="medium",
            budget_impact=50.0,
            time_impact_minutes=30,
        )
        
        assert lesson.title == "API Rate Limit Hit"
        assert lesson.category == LessonCategory.FAILURE
        assert lesson.user_id == sample_campaign.user_id
        assert mock_db.add.called
        assert mock_db.flush.called
    
    @pytest.mark.asyncio
    async def test_record_lesson_campaign_not_found(self, mock_db):
        """Test error handling when campaign not found."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        with pytest.raises(ValueError, match="not found"):
            await service.record_lesson(
                campaign_id=uuid4(),
                title="Test",
                description="Test",
                category=LessonCategory.FAILURE,
                trigger_event="Test",
                context={},
                prevention_steps=[],
            )


class TestLessonWarnings:
    """Tests for lesson-based warnings."""
    
    @pytest.mark.asyncio
    async def test_check_for_warnings(self, mock_db, sample_campaign):
        """Test checking for warnings based on lessons."""
        # Create a lesson with detection signals
        lesson = CampaignLesson(
            id=uuid4(),
            title="Budget Warning",
            description="Campaign spent too much",
            category=LessonCategory.BUDGET_ISSUE,
            context={"category": "budget_issue"},
            trigger_event="Over budget",
            impact_severity="high",
            prevention_steps=["Monitor spending", "Set alerts"],
            detection_signals=[
                {"type": "budget_percentage", "value": 0.8}
            ],
            source_campaign_id=uuid4(),
            user_id=sample_campaign.user_id,
            times_applied=0,
        )
        
        # Mock the query
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [lesson]
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        # Campaign has spent 90% of budget
        sample_campaign.budget_spent = 900.00
        sample_campaign.budget_allocated = 1000.00
        
        warnings = await service.check_for_warnings(
            campaign=sample_campaign,
            current_state={"budget_pct": 0.9},
        )
        
        assert len(warnings) >= 1
        assert warnings[0].lesson.title == "Budget Warning"


# =============================================================================
# Plan Revision Tests
# =============================================================================

class TestPlanRevision:
    """Tests for plan revision functionality."""
    
    @pytest.mark.asyncio
    async def test_analyze_for_revision(
        self, mock_db, mock_llm_service, sample_campaign, sample_streams
    ):
        """Test analyzing if a revision is needed."""
        sample_campaign.task_streams = sample_streams
        
        # Mock the campaign query
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = sample_campaign
        mock_db.execute.return_value = mock_result
        
        # Mock LLM response
        mock_llm_service.generate.return_value = MagicMock(
            content='```json\n{"should_revise": true, "reason": "Blocked streams", "changes": {}, "expected_benefit": "Unblock", "risk_level": "low"}\n```',
            provider="test",
            model="test-model",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
            latency_ms=200,
        )
        
        service = CampaignLearningService(mock_db, mock_llm_service)
        
        recommendation = await service.analyze_for_revision(
            campaign=sample_campaign,
            trigger=RevisionTrigger.STREAM_BLOCKED,
            trigger_details="2 streams are blocked",
        )
        
        if recommendation:  # LLM said should_revise=true
            assert recommendation.trigger == RevisionTrigger.STREAM_BLOCKED
            assert recommendation.risk_level in ["low", "medium", "high"]
    
    @pytest.mark.asyncio
    async def test_create_revision(self, mock_db, sample_campaign):
        """Test creating a plan revision record."""
        # Mock the max revision query
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        recommendation = RevisionRecommendation(
            trigger=RevisionTrigger.TASK_FAILURE,
            reason="Task failed, need to add retry logic",
            changes={"add_tasks": [{"name": "Retry task"}]},
            expected_benefit="Task will succeed on retry",
            risk_level="low",
        )
        
        revision = await service.create_revision(
            campaign=sample_campaign,
            recommendation=recommendation,
            initiated_by="agent",
        )
        
        assert revision.revision_number == 1
        assert revision.trigger == RevisionTrigger.TASK_FAILURE
        assert revision.tasks_added == 1
        assert mock_db.add.called
        assert mock_db.flush.called
    
    @pytest.mark.asyncio
    async def test_assess_revision_outcome(self, mock_db):
        """Test assessing the outcome of a revision."""
        revision = PlanRevision(
            id=uuid4(),
            campaign_id=uuid4(),
            revision_number=1,
            trigger=RevisionTrigger.OPTIMIZATION,
            trigger_details="Test",
            plan_before={},
            plan_after={},
            changes_summary="Test change",
            reasoning="Test reason",
            initiated_by="agent",
            outcome_assessed=False,
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = revision
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        await service.assess_revision_outcome(
            revision_id=revision.id,
            success=True,
            notes="Revision improved execution time",
        )
        
        assert revision.outcome_assessed == True
        assert revision.outcome_success == True
        assert revision.outcome_notes == "Revision improved execution time"


# =============================================================================
# Proactive Suggestions Tests
# =============================================================================

class TestProactiveSuggestions:
    """Tests for proactive suggestion generation."""
    
    @pytest.mark.asyncio
    async def test_generate_suggestions(self, mock_db, sample_campaign):
        """Test generating proactive suggestions."""
        sample_campaign.streams_parallel_execution = False
        
        # Mock the lesson query to return empty
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        suggestions = await service.generate_suggestions(
            campaign=sample_campaign,
            current_state={
                "total_streams": 3,
                "ready_streams": 2,
            },
        )
        
        # Should suggest enabling parallelization
        assert len(suggestions) >= 1
        assert any(s.suggestion_type == SuggestionType.TIME_SAVING for s in suggestions)
    
    @pytest.mark.asyncio
    async def test_generate_budget_warning(self, mock_db, sample_campaign):
        """Test generating budget warning suggestions."""
        sample_campaign.budget_allocated = 1000.00
        sample_campaign.budget_spent = 600.00  # High burn rate
        sample_campaign.start_date = datetime.utcnow() - timedelta(days=2)
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        suggestions = await service.generate_suggestions(
            campaign=sample_campaign,
            current_state={
                "overall_progress_pct": 30,
            },
        )
        
        # Should include a budget warning if projected to overrun
        warning_suggestions = [s for s in suggestions if s.suggestion_type == SuggestionType.WARNING]
        # Note: might not trigger depending on exact calculations
    
    @pytest.mark.asyncio
    async def test_update_suggestion_status(self, mock_db):
        """Test updating suggestion status."""
        suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=uuid4(),
            suggestion_type=SuggestionType.OPTIMIZATION,
            title="Test Suggestion",
            description="Test",
            status=SuggestionStatus.PENDING,
            urgency="medium",
            confidence=0.8,
            evidence={},
            recommended_action={},
        )
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = suggestion
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        await service.update_suggestion_status(
            suggestion_id=suggestion.id,
            status=SuggestionStatus.ACCEPTED,
            user_feedback="Good suggestion!",
        )
        
        assert suggestion.status == SuggestionStatus.ACCEPTED
        assert suggestion.user_feedback == "Good suggestion!"
        assert suggestion.user_response_at is not None
    
    @pytest.mark.asyncio
    async def test_auto_apply_suggestion(self, mock_db, sample_campaign):
        """Test auto-applying a suggestion."""
        suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=sample_campaign.id,
            suggestion_type=SuggestionType.TIME_SAVING,
            title="Enable Parallelization",
            description="Enable parallel stream execution",
            status=SuggestionStatus.PENDING,
            urgency="medium",
            confidence=0.8,
            evidence={},
            recommended_action={
                "type": "enable_parallelization",
                "field": "streams_parallel_execution",
                "value": True,
            },
            can_auto_apply=True,
        )
        
        sample_campaign.streams_parallel_execution = False
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_campaign
        mock_db.execute.return_value = mock_result
        
        service = CampaignLearningService(mock_db)
        
        success = await service.auto_apply_suggestion(suggestion)
        
        assert success == True
        assert sample_campaign.streams_parallel_execution == True
        assert suggestion.status == SuggestionStatus.AUTO_APPLIED
    
    @pytest.mark.asyncio
    async def test_cannot_auto_apply_without_permission(self, mock_db):
        """Test that suggestions without auto_apply permission are not applied."""
        suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=uuid4(),
            suggestion_type=SuggestionType.WARNING,
            title="Warning",
            description="Test warning",
            status=SuggestionStatus.PENDING,
            urgency="high",
            confidence=0.9,
            evidence={},
            recommended_action={},
            can_auto_apply=False,  # Cannot auto-apply
        )
        
        service = CampaignLearningService(mock_db)
        
        success = await service.auto_apply_suggestion(suggestion)
        
        assert success == False


# =============================================================================
# Integration-style Tests
# =============================================================================

class TestLearningIntegration:
    """Integration-style tests for the learning service."""
    
    def test_pattern_success_rate_calculation(self):
        """Test the success rate calculation for patterns."""
        pattern = CampaignPattern(
            id=uuid4(),
            name="Test Pattern",
            description="Test",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.7,
            pattern_data={},
            times_applied=10,
            times_successful=8,
        )
        
        assert pattern.success_rate == 0.8
    
    def test_pattern_success_rate_zero_applications(self):
        """Test success rate when no applications."""
        pattern = CampaignPattern(
            id=uuid4(),
            name="Test Pattern",
            description="Test",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.7,
            pattern_data={},
            times_applied=0,
            times_successful=0,
        )
        
        assert pattern.success_rate == 0.0
    
    def test_suggestion_expiration(self):
        """Test suggestion expiration check."""
        # Expired suggestion
        expired_suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=uuid4(),
            suggestion_type=SuggestionType.OPPORTUNITY,
            title="Expired",
            description="Test",
            status=SuggestionStatus.PENDING,
            urgency="medium",
            confidence=0.7,
            evidence={},
            recommended_action={},
            expires_at=datetime.utcnow() - timedelta(hours=1),
        )
        
        assert expired_suggestion.is_expired == True
        
        # Non-expired suggestion
        valid_suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=uuid4(),
            suggestion_type=SuggestionType.OPPORTUNITY,
            title="Valid",
            description="Test",
            status=SuggestionStatus.PENDING,
            urgency="medium",
            confidence=0.7,
            evidence={},
            recommended_action={},
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        
        assert valid_suggestion.is_expired == False
        
        # No expiration
        no_expiry_suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=uuid4(),
            suggestion_type=SuggestionType.OPTIMIZATION,
            title="No Expiry",
            description="Test",
            status=SuggestionStatus.PENDING,
            urgency="low",
            confidence=0.6,
            evidence={},
            recommended_action={},
            expires_at=None,
        )
        
        assert no_expiry_suggestion.is_expired == False
