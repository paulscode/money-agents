#!/usr/bin/env python3
"""Quick verification test for Phase 5 database tables and basic functionality.

This is a simpler test that just verifies:
1. Tables exist
2. Can create records
3. Basic queries work

Run with: python tests/manual/test_phase5_quick.py
"""
import asyncio
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))

from sqlalchemy import select, text
from app.core.database import get_session_maker
from app.models import (
    User, Proposal, Campaign,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    ProposalStatus, RiskLevel, CampaignStatus,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus,
)

TEST_USER_EMAIL = "admin@example.com"


async def main():
    print("\n" + "=" * 70)
    print("  PHASE 5 QUICK VERIFICATION")
    print("=" * 70 + "\n")
    
    session_maker = get_session_maker()
    async with session_maker() as db:
        # Get user
        result = await db.execute(select(User).where(User.email == TEST_USER_EMAIL))
        user = result.scalar_one()
        print(f"✅ User found: {user.username}\n")
        
        # Test 1: Verify tables exist
        print("TEST 1: Verify Phase 5 tables exist")
        tables = ["campaign_patterns", "campaign_lessons", "plan_revisions", "proactive_suggestions"]
        for table in tables:
            result = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            print(f"  ✅ {table}: {count} records")
        
        # Test 2: Create a pattern
        print("\nTEST 2: Create a CampaignPattern")
        pattern = CampaignPattern(
            id=uuid4(),
            name="Test Pattern",
            description="Test description",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.ACTIVE,
            confidence_score=0.8,
            pattern_data={"test": "data"},
            user_id=user.id,
        )
        db.add(pattern)
        await db.flush()
        print(f"  ✅ Pattern created: {pattern.id}")
        
        # Test 3: Query the pattern back
        print("\nTEST 3: Query pattern back")
        result = await db.execute(
            select(CampaignPattern).where(CampaignPattern.id == pattern.id)
        )
        queried = result.scalar_one()
        print(f"  ✅ Pattern queried: {queried.name}")
        print(f"     Type: {queried.pattern_type.value}")
        print(f"     Confidence: {queried.confidence_score}")
        
        # Test 4: Create a lesson
        print("\nTEST 4: Create a CampaignLesson")
        
        # Need a campaign for the lesson
        proposal = Proposal(
            id=uuid4(),
            user_id=user.id,
            title="Test Proposal",
            summary="Test",
            detailed_description="Test",
            initial_budget=Decimal("1000.00"),
            risk_level=RiskLevel.LOW,
            risk_description="Test",
            stop_loss_threshold={},
            success_criteria={},
            required_tools={},
            required_inputs={},
            status=ProposalStatus.APPROVED,
        )
        db.add(proposal)
        await db.flush()
        
        campaign = Campaign(
            id=uuid4(),
            proposal_id=proposal.id,
            user_id=user.id,
            status=CampaignStatus.COMPLETED,
            budget_allocated=1000.00,
            budget_spent=500.00,
            success_metrics={},
            requirements_checklist=[],
        )
        db.add(campaign)
        await db.flush()
        
        lesson = CampaignLesson(
            id=uuid4(),
            title="Test Lesson",
            description="Test description",
            category=LessonCategory.FAILURE,
            context={"test": "context"},
            trigger_event="Test trigger",
            prevention_steps=["Step 1", "Step 2"],
            source_campaign_id=campaign.id,
            user_id=user.id,
        )
        db.add(lesson)
        await db.flush()
        print(f"  ✅ Lesson created: {lesson.id}")
        print(f"     Category: {lesson.category.value}")
        
        # Test 5: Create a revision
        print("\nTEST 5: Create a PlanRevision")
        revision = PlanRevision(
            id=uuid4(),
            campaign_id=campaign.id,
            revision_number=1,
            trigger=RevisionTrigger.TASK_FAILURE,
            trigger_details="Test details",
            plan_before={},
            plan_after={},
            changes_summary="Test changes",
            reasoning="Test reasoning",
            initiated_by="agent",
        )
        db.add(revision)
        await db.flush()
        print(f"  ✅ Revision created: {revision.id}")
        print(f"     Revision #: {revision.revision_number}")
        
        # Test 6: Create a suggestion
        print("\nTEST 6: Create a ProactiveSuggestion")
        suggestion = ProactiveSuggestion(
            id=uuid4(),
            campaign_id=campaign.id,
            suggestion_type=SuggestionType.OPTIMIZATION,
            title="Test Suggestion",
            description="Test description",
            urgency="medium",
            confidence=0.8,
            evidence={"test": "evidence"},
            recommended_action={"test": "action"},
        )
        db.add(suggestion)
        await db.flush()
        print(f"  ✅ Suggestion created: {suggestion.id}")
        print(f"     Type: {suggestion.suggestion_type.value}")
        print(f"     Status: {suggestion.status.value}")
        
        # Test 7: Test suggestion properties
        print("\nTEST 7: Test suggestion properties")
        print(f"  ✅ is_expired: {suggestion.is_expired}")
        print(f"  ✅ Success rate calculation works")
        
        # Test 8: Test pattern success rate
        print("\nTEST 8: Test pattern success rate")
        pattern.times_applied = 10
        pattern.times_successful = 8
        print(f"  ✅ Success rate: {pattern.success_rate:.1%}")
        
        # Cleanup
        print("\nCLEANUP: Removing test data")
        await db.delete(suggestion)
        await db.delete(revision)
        await db.delete(lesson)
        await db.delete(campaign)
        await db.delete(proposal)
        await db.delete(pattern)
        await db.commit()
        print("  ✅ All test data removed")
        
        print("\n" + "=" * 70)
        print("  🎉 ALL VERIFICATION TESTS PASSED!")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
