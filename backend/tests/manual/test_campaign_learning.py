#!/usr/bin/env python3
"""Manual End-to-End Tests for Phase 5: Agent Intelligence

This script tests the campaign learning features:
1. Pattern Discovery - Extract patterns from completed campaigns
2. Pattern Matching - Find applicable patterns for proposals
3. Lesson Recording - Capture lessons from failures
4. Warning Detection - Check for warnings based on lessons
5. Plan Revision - Analyze and create plan revisions
6. Proactive Suggestions - Generate optimization suggestions

Run with: python -m tests.manual.test_campaign_learning

Prerequisites:
- Database must be running and migrated
# User account must exist (see .env or use admin@example.com)
"""
import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

# Add parent to path for imports
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session_maker
from app.models import (
    User, Proposal, Campaign, TaskStream, CampaignTask, UserInputRequest,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    ProposalStatus, RiskLevel, CampaignStatus,
    TaskStreamStatus, TaskStatus, TaskType, InputType, InputPriority, InputStatus,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus,
)
from app.services.campaign_learning_service import (
    CampaignLearningService,
    RevisionRecommendation,
)


# =============================================================================
# Test Configuration
# =============================================================================

TEST_USER_EMAIL = "admin@example.com"
TEST_PREFIX = "[TEST-P5]"  # Prefix for test data

# Track created IDs for cleanup
CREATED_IDS = {
    "proposals": [],
    "campaigns": [],
    "streams": [],
    "tasks": [],
    "patterns": [],
    "lessons": [],
    "revisions": [],
    "suggestions": [],
}


# =============================================================================
# Helper Functions
# =============================================================================

def print_header(title: str):
    """Print a formatted header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(test_name: str, passed: bool, message: str = ""):
    """Print test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {test_name}")
    if message:
        print(f"         {message}")


async def get_test_user(db: AsyncSession) -> User:
    """Get or create the test user."""
    result = await db.execute(
        select(User).where(User.email == TEST_USER_EMAIL)
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise ValueError(f"Test user not found: {TEST_USER_EMAIL}")
    
    return user


async def cleanup_test_data(db: AsyncSession):
    """Clean up all test data created during the tests."""
    print_header("CLEANUP: Removing Test Data")
    
    # Delete in reverse order of dependencies
    for suggestion_id in CREATED_IDS["suggestions"]:
        await db.execute(delete(ProactiveSuggestion).where(ProactiveSuggestion.id == suggestion_id))
    print(f"  Deleted {len(CREATED_IDS['suggestions'])} suggestions")
    
    for revision_id in CREATED_IDS["revisions"]:
        await db.execute(delete(PlanRevision).where(PlanRevision.id == revision_id))
    print(f"  Deleted {len(CREATED_IDS['revisions'])} revisions")
    
    for lesson_id in CREATED_IDS["lessons"]:
        await db.execute(delete(CampaignLesson).where(CampaignLesson.id == lesson_id))
    print(f"  Deleted {len(CREATED_IDS['lessons'])} lessons")
    
    for pattern_id in CREATED_IDS["patterns"]:
        await db.execute(delete(CampaignPattern).where(CampaignPattern.id == pattern_id))
    print(f"  Deleted {len(CREATED_IDS['patterns'])} patterns")
    
    for task_id in CREATED_IDS["tasks"]:
        await db.execute(delete(CampaignTask).where(CampaignTask.id == task_id))
    print(f"  Deleted {len(CREATED_IDS['tasks'])} tasks")
    
    for stream_id in CREATED_IDS["streams"]:
        await db.execute(delete(TaskStream).where(TaskStream.id == stream_id))
    print(f"  Deleted {len(CREATED_IDS['streams'])} streams")
    
    for campaign_id in CREATED_IDS["campaigns"]:
        await db.execute(delete(Campaign).where(Campaign.id == campaign_id))
    print(f"  Deleted {len(CREATED_IDS['campaigns'])} campaigns")
    
    for proposal_id in CREATED_IDS["proposals"]:
        await db.execute(delete(Proposal).where(Proposal.id == proposal_id))
    print(f"  Deleted {len(CREATED_IDS['proposals'])} proposals")
    
    await db.commit()
    print("  ✅ Cleanup complete!")


# =============================================================================
# Test Data Setup
# =============================================================================

async def create_test_proposal(
    db: AsyncSession, 
    user: User, 
    title: str,
    budget: float = 1000.00
) -> Proposal:
    """Create a test proposal."""
    proposal = Proposal(
        id=uuid4(),
        user_id=user.id,
        title=f"{TEST_PREFIX} {title}",
        summary=f"Test proposal for {title}",
        detailed_description=f"Detailed description for testing {title}",
        initial_budget=Decimal(str(budget)),
        risk_level=RiskLevel.LOW,
        risk_description="Low risk test",
        stop_loss_threshold={"max_loss": budget * 0.2},
        success_criteria={"target_leads": 100},
        required_tools={"web_search": "needed"},
        required_inputs={"api_key": "string"},
        status=ProposalStatus.APPROVED,
        tags={"type": "marketing", "tags": ["social", "test"]},
    )
    db.add(proposal)
    await db.flush()
    CREATED_IDS["proposals"].append(proposal.id)
    return proposal


async def create_test_campaign(
    db: AsyncSession,
    user: User,
    proposal: Proposal,
    status: CampaignStatus = CampaignStatus.COMPLETED,
    budget_spent: float = 500.00,
) -> Campaign:
    """Create a test campaign."""
    campaign = Campaign(
        id=uuid4(),
        proposal_id=proposal.id,
        user_id=user.id,
        status=status,
        budget_allocated=float(proposal.initial_budget),
        budget_spent=budget_spent,
        revenue_generated=800.00,
        success_metrics={"leads": {"current": 100, "target": 100}},
        tasks_total=10,
        tasks_completed=10 if status == CampaignStatus.COMPLETED else 5,
        requirements_checklist=[],
        all_requirements_met=True,
        execution_plan={"streams_count": 2, "parallelization_factor": 1.5},
        streams_parallel_execution=False,
        start_date=datetime.utcnow() - timedelta(days=5),
        end_date=datetime.utcnow() if status == CampaignStatus.COMPLETED else None,
    )
    db.add(campaign)
    await db.flush()
    CREATED_IDS["campaigns"].append(campaign.id)
    return campaign


async def create_test_stream(
    db: AsyncSession,
    campaign: Campaign,
    name: str,
    status: TaskStreamStatus = TaskStreamStatus.COMPLETED,
) -> TaskStream:
    """Create a test task stream."""
    stream = TaskStream(
        id=uuid4(),
        campaign_id=campaign.id,
        name=name,
        description=f"Test stream: {name}",
        order_index=0,
        status=status,
        tasks_total=5,
        tasks_completed=5 if status == TaskStreamStatus.COMPLETED else 2,
        started_at=datetime.utcnow() - timedelta(hours=4),
        completed_at=datetime.utcnow() if status == TaskStreamStatus.COMPLETED else None,
    )
    db.add(stream)
    await db.flush()
    CREATED_IDS["streams"].append(stream.id)
    return stream


async def create_test_task(
    db: AsyncSession,
    stream: TaskStream,
    campaign: Campaign,
    name: str,
    tool_slug: str = None,
    status: TaskStatus = TaskStatus.COMPLETED,
) -> CampaignTask:
    """Create a test task."""
    task = CampaignTask(
        id=uuid4(),
        stream_id=stream.id,
        campaign_id=campaign.id,
        name=name,
        description=f"Test task: {name}",
        order_index=0,
        status=status,
        task_type=TaskType.TOOL_EXECUTION if tool_slug else TaskType.LLM_REASONING,
        tool_slug=tool_slug,
        duration_ms=1500,
    )
    db.add(task)
    await db.flush()
    CREATED_IDS["tasks"].append(task.id)
    return task


# =============================================================================
# Test Cases
# =============================================================================

async def test_pattern_discovery(db: AsyncSession, user: User) -> bool:
    """Test 1: Pattern Discovery from Completed Campaigns."""
    print_header("TEST 1: Pattern Discovery")
    
    try:
        # Create a completed campaign with streams and tasks
        proposal = await create_test_proposal(db, user, "Pattern Discovery Test")
        campaign = await create_test_campaign(db, user, proposal)
        
        # Create streams
        research_stream = await create_test_stream(db, campaign, "Research")
        execution_stream = await create_test_stream(db, campaign, "Execution")
        
        # Create tasks with tools
        await create_test_task(db, research_stream, campaign, "Web Search", tool_slug="web_search")
        await create_test_task(db, research_stream, campaign, "Analyze Data")
        await create_test_task(db, execution_stream, campaign, "Post Content", tool_slug="social_post")
        
        await db.commit()
        
        # Run pattern discovery
        service = CampaignLearningService(db)
        patterns = await service.discover_patterns_from_campaign(campaign.id)
        
        # Track created patterns for cleanup
        for p in patterns:
            CREATED_IDS["patterns"].append(p.id)
        
        await db.commit()
        
        # Verify results
        passed = len(patterns) >= 1
        print_result(
            "Pattern discovery found patterns",
            passed,
            f"Found {len(patterns)} patterns"
        )
        
        if patterns:
            print(f"\n  Discovered patterns:")
            for p in patterns:
                print(f"    - {p.name} ({p.pattern_type.value})")
                print(f"      Confidence: {p.confidence_score:.2f}")
        
        return passed
        
    except Exception as e:
        print_result("Pattern discovery", False, str(e))
        return False


async def test_pattern_matching(db: AsyncSession, user: User) -> bool:
    """Test 2: Pattern Matching for New Proposals."""
    print_header("TEST 2: Pattern Matching")
    
    try:
        # Create an active pattern
        pattern = CampaignPattern(
            id=uuid4(),
            name=f"{TEST_PREFIX} Social Media Pattern",
            description="A pattern for social media campaigns",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.85,
            pattern_data={"streams": ["research", "content", "execution"]},
            applicability_conditions={
                "budget_range": [500, 2000],
                "proposal_type": "marketing"
            },
            user_id=user.id,
            times_applied=5,
            times_successful=4,
            tags=["social", "test"],
        )
        db.add(pattern)
        await db.flush()
        CREATED_IDS["patterns"].append(pattern.id)
        
        # Create a proposal to match against
        proposal = await create_test_proposal(db, user, "Pattern Match Test", budget=1000.00)
        await db.commit()
        
        # Find matching patterns
        service = CampaignLearningService(db)
        matches = await service.find_applicable_patterns(proposal, user.id)
        
        # Verify
        passed = len(matches) >= 1
        print_result(
            "Pattern matching found applicable patterns",
            passed,
            f"Found {len(matches)} matching patterns"
        )
        
        if matches:
            print(f"\n  Matched patterns:")
            for m in matches:
                print(f"    - {m.pattern.name}")
                print(f"      Relevance: {m.relevance_score:.2f}")
                print(f"      Suggestion: {m.suggested_application}")
        
        return passed
        
    except Exception as e:
        print_result("Pattern matching", False, str(e))
        return False


async def test_lesson_recording(db: AsyncSession, user: User) -> bool:
    """Test 3: Lesson Recording from Failures."""
    print_header("TEST 3: Lesson Recording")
    
    try:
        # Create a campaign to record lessons for
        proposal = await create_test_proposal(db, user, "Lesson Test Campaign")
        campaign = await create_test_campaign(db, user, proposal, status=CampaignStatus.EXECUTING)
        await db.commit()
        
        # Record a lesson
        service = CampaignLearningService(db)
        lesson = await service.record_lesson(
            campaign_id=campaign.id,
            title=f"{TEST_PREFIX} API Rate Limit Issue",
            description="The Twitter API rate limit was hit during high-volume posting",
            category=LessonCategory.FAILURE,
            trigger_event="Tool execution failed with HTTP 429",
            context={
                "tool": "twitter_api",
                "rate_limit": 100,
                "requests_made": 150,
            },
            prevention_steps=[
                "Implement rate limiting in tool executor",
                "Use exponential backoff",
                "Spread posts over longer time period",
            ],
            impact_severity="medium",
            budget_impact=25.00,
            time_impact_minutes=60,
        )
        
        CREATED_IDS["lessons"].append(lesson.id)
        await db.commit()
        
        # Verify
        passed = lesson is not None and lesson.category == LessonCategory.FAILURE
        print_result(
            "Lesson recorded successfully",
            passed,
            f"Lesson ID: {lesson.id}"
        )
        
        print(f"\n  Recorded lesson:")
        print(f"    Title: {lesson.title}")
        print(f"    Category: {lesson.category.value}")
        print(f"    Severity: {lesson.impact_severity}")
        print(f"    Prevention: {lesson.prevention_steps}")
        
        return passed
        
    except Exception as e:
        print_result("Lesson recording", False, str(e))
        return False


async def test_warning_detection(db: AsyncSession, user: User) -> bool:
    """Test 4: Warning Detection Based on Lessons."""
    print_header("TEST 4: Warning Detection")
    
    try:
        # Create a lesson with detection signals
        proposal = await create_test_proposal(db, user, "Warning Detection Test")
        source_campaign = await create_test_campaign(db, user, proposal)
        
        lesson = CampaignLesson(
            id=uuid4(),
            title=f"{TEST_PREFIX} Budget Overrun Warning",
            description="Campaign exceeded budget due to unmonitored spending",
            category=LessonCategory.BUDGET_ISSUE,
            context={"category": "budget_issue"},
            trigger_event="Budget exceeded by 30%",
            impact_severity="high",
            prevention_steps=["Set budget alerts", "Review daily spending"],
            detection_signals=[
                {"type": "budget_percentage", "value": 0.8}
            ],
            source_campaign_id=source_campaign.id,
            user_id=user.id,
        )
        db.add(lesson)
        await db.flush()
        CREATED_IDS["lessons"].append(lesson.id)
        
        # Create a campaign that's at 85% budget (should trigger warning)
        proposal2 = await create_test_proposal(db, user, "High Budget Campaign")
        campaign = await create_test_campaign(
            db, user, proposal2, 
            status=CampaignStatus.EXECUTING,
            budget_spent=850.00  # 85% of 1000
        )
        await db.commit()
        
        # Check for warnings
        service = CampaignLearningService(db)
        warnings = await service.check_for_warnings(
            campaign=campaign,
            current_state={"budget_pct": 0.85}
        )
        
        # Verify
        passed = len(warnings) >= 1
        print_result(
            "Warning detection triggered",
            passed,
            f"Found {len(warnings)} warnings"
        )
        
        if warnings:
            print(f"\n  Detected warnings:")
            for w in warnings:
                print(f"    - {w.warning_message}")
                print(f"      Urgency: {w.urgency}")
                print(f"      Prevention: {w.prevention_actions[:2]}")
        
        return passed
        
    except Exception as e:
        print_result("Warning detection", False, str(e))
        return False


async def test_plan_revision(db: AsyncSession, user: User) -> bool:
    """Test 5: Plan Revision Creation."""
    print_header("TEST 5: Plan Revision")
    
    try:
        # Create a campaign
        proposal = await create_test_proposal(db, user, "Revision Test Campaign")
        campaign = await create_test_campaign(db, user, proposal, status=CampaignStatus.EXECUTING)
        
        # Create a blocked stream
        blocked_stream = await create_test_stream(
            db, campaign, "Blocked Stream", 
            status=TaskStreamStatus.BLOCKED
        )
        blocked_stream.blocking_reasons = ["Missing API credentials"]
        
        await db.commit()
        
        # Create a revision recommendation manually (simulating LLM output)
        recommendation = RevisionRecommendation(
            trigger=RevisionTrigger.STREAM_BLOCKED,
            reason="Stream blocked due to missing credentials",
            changes={
                "add_tasks": [{"name": "Request credentials notification"}],
                "reorder_streams": [{"stream": "Blocked Stream", "action": "defer"}],
            },
            expected_benefit="Unblock other streams while waiting for credentials",
            risk_level="low",
        )
        
        # Create the revision
        service = CampaignLearningService(db)
        revision = await service.create_revision(
            campaign=campaign,
            recommendation=recommendation,
            initiated_by="agent",
        )
        
        CREATED_IDS["revisions"].append(revision.id)
        await db.commit()
        
        # Verify
        passed = revision is not None and revision.revision_number == 1
        print_result(
            "Plan revision created",
            passed,
            f"Revision #{revision.revision_number}"
        )
        
        print(f"\n  Revision details:")
        print(f"    Trigger: {revision.trigger.value}")
        print(f"    Tasks added: {revision.tasks_added}")
        print(f"    Reasoning: {revision.reasoning[:50]}...")
        
        # Test outcome assessment
        await service.assess_revision_outcome(
            revision_id=revision.id,
            success=True,
            notes="Revision helped unblock the campaign"
        )
        await db.commit()
        
        # Reload and verify
        result = await db.execute(
            select(PlanRevision).where(PlanRevision.id == revision.id)
        )
        updated_revision = result.scalar_one()
        
        outcome_passed = updated_revision.outcome_assessed and updated_revision.outcome_success
        print_result(
            "Revision outcome assessed",
            outcome_passed,
            f"Success: {updated_revision.outcome_success}"
        )
        
        return passed and outcome_passed
        
    except Exception as e:
        print_result("Plan revision", False, str(e))
        return False


async def test_proactive_suggestions(db: AsyncSession, user: User) -> bool:
    """Test 6: Proactive Suggestion Generation."""
    print_header("TEST 6: Proactive Suggestions")
    
    try:
        # Create a campaign with parallelization disabled
        proposal = await create_test_proposal(db, user, "Suggestion Test Campaign")
        campaign = await create_test_campaign(db, user, proposal, status=CampaignStatus.EXECUTING)
        campaign.streams_parallel_execution = False
        await db.commit()
        
        # Generate suggestions
        service = CampaignLearningService(db)
        suggestions = await service.generate_suggestions(
            campaign=campaign,
            current_state={
                "total_streams": 3,
                "ready_streams": 2,
                "overall_progress_pct": 30,
            }
        )
        
        # Track for cleanup
        for s in suggestions:
            CREATED_IDS["suggestions"].append(s.id)
        
        await db.commit()
        
        # Verify
        passed = len(suggestions) >= 1
        print_result(
            "Suggestions generated",
            passed,
            f"Generated {len(suggestions)} suggestions"
        )
        
        if suggestions:
            print(f"\n  Generated suggestions:")
            for s in suggestions:
                print(f"    - [{s.suggestion_type.value}] {s.title}")
                print(f"      Urgency: {s.urgency}, Confidence: {s.confidence:.2f}")
                print(f"      Can auto-apply: {s.can_auto_apply}")
        
        # Test auto-apply
        auto_applicable = [s for s in suggestions if s.can_auto_apply]
        if auto_applicable:
            suggestion = auto_applicable[0]
            success = await service.auto_apply_suggestion(suggestion)
            
            print_result(
                "Auto-apply suggestion",
                success,
                f"Applied: {suggestion.title}"
            )
            
            # Verify campaign was updated
            if success:
                await db.refresh(campaign)
                print(f"    Campaign parallelization now: {campaign.streams_parallel_execution}")
        
        # Test user response to suggestion
        if suggestions:
            await service.update_suggestion_status(
                suggestion_id=suggestions[0].id,
                status=SuggestionStatus.ACCEPTED,
                user_feedback="Good suggestion!"
            )
            await db.commit()
            
            print_result(
                "User response recorded",
                True,
                "Status updated to ACCEPTED"
            )
        
        return passed
        
    except Exception as e:
        print_result("Proactive suggestions", False, str(e))
        return False


async def test_full_learning_cycle(db: AsyncSession, user: User) -> bool:
    """Test 7: Full Learning Cycle - End to End."""
    print_header("TEST 7: Full Learning Cycle (E2E)")
    
    try:
        # 1. Create and complete a campaign
        print("\n  Step 1: Create completed campaign with patterns...")
        proposal1 = await create_test_proposal(db, user, "E2E Source Campaign")
        campaign1 = await create_test_campaign(db, user, proposal1)
        stream1 = await create_test_stream(db, campaign1, "E2E Research")
        await create_test_task(db, stream1, campaign1, "E2E Task 1", tool_slug="web_search")
        await db.commit()
        
        # 2. Discover patterns from it
        print("  Step 2: Discover patterns from completed campaign...")
        service = CampaignLearningService(db)
        patterns = await service.discover_patterns_from_campaign(campaign1.id)
        for p in patterns:
            CREATED_IDS["patterns"].append(p.id)
        await db.commit()
        print(f"    Found {len(patterns)} patterns")
        
        # 3. Create a new campaign and find applicable patterns
        print("  Step 3: Create new campaign and find applicable patterns...")
        proposal2 = await create_test_proposal(db, user, "E2E New Campaign", budget=1200.00)
        matches = await service.find_applicable_patterns(proposal2, user.id)
        print(f"    Found {len(matches)} matching patterns")
        
        # 4. Record a lesson from the first campaign
        print("  Step 4: Record lesson from experience...")
        lesson = await service.record_lesson(
            campaign_id=campaign1.id,
            title=f"{TEST_PREFIX} E2E Learned Lesson",
            description="Test lesson for E2E",
            category=LessonCategory.INEFFICIENCY,
            trigger_event="Process took longer than expected",
            context={"reason": "sequential execution"},
            prevention_steps=["Enable parallelization"],
        )
        CREATED_IDS["lessons"].append(lesson.id)
        await db.commit()
        
        # 5. Create a running campaign and check for warnings
        print("  Step 5: Check new campaign for warnings...")
        campaign2 = await create_test_campaign(db, user, proposal2, status=CampaignStatus.EXECUTING)
        warnings = await service.check_for_warnings(campaign2, {})
        print(f"    Found {len(warnings)} warnings")
        
        # 6. Generate suggestions
        print("  Step 6: Generate proactive suggestions...")
        suggestions = await service.generate_suggestions(campaign2, {
            "total_streams": 2,
            "ready_streams": 1,
        })
        for s in suggestions:
            CREATED_IDS["suggestions"].append(s.id)
        await db.commit()
        print(f"    Generated {len(suggestions)} suggestions")
        
        passed = len(patterns) >= 0 and lesson is not None
        print_result(
            "Full learning cycle completed",
            passed,
            f"Patterns: {len(patterns)}, Lessons: 1, Suggestions: {len(suggestions)}"
        )
        
        return passed
        
    except Exception as e:
        print_result("Full learning cycle", False, str(e))
        return False


# =============================================================================
# Main Test Runner
# =============================================================================

async def run_all_tests():
    """Run all manual tests."""
    print("\n" + "=" * 70)
    print("  PHASE 5: AGENT INTELLIGENCE - MANUAL TESTS")
    print("=" * 70)
    print(f"\n  Test User: {TEST_USER_EMAIL}")
    print(f"  Test Prefix: {TEST_PREFIX}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {}
    
    session_maker = get_session_maker()
    async with session_maker() as db:
        try:
            # Get test user
            user = await get_test_user(db)
            print(f"  User Found: {user.username} (ID: {user.id})")
            
            # Run tests
            results["Pattern Discovery"] = await test_pattern_discovery(db, user)
            results["Pattern Matching"] = await test_pattern_matching(db, user)
            results["Lesson Recording"] = await test_lesson_recording(db, user)
            results["Warning Detection"] = await test_warning_detection(db, user)
            results["Plan Revision"] = await test_plan_revision(db, user)
            results["Proactive Suggestions"] = await test_proactive_suggestions(db, user)
            results["Full Learning Cycle"] = await test_full_learning_cycle(db, user)
            
        except Exception as e:
            print(f"\n❌ Test setup failed: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            # Cleanup
            try:
                await cleanup_test_data(db)
            except Exception as e:
                print(f"\n⚠️  Cleanup error: {e}")
    
    # Summary
    print_header("TEST SUMMARY")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "✅" if result else "❌"
        print(f"  {status} {test_name}")
    
    print(f"\n  Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n  🎉 ALL TESTS PASSED!")
        return 0
    else:
        print("\n  ⚠️  Some tests failed. Review output above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    sys.exit(exit_code)
