"""Seed test data for Campaign Learning / Intelligence tab."""
import asyncio
import sys
from datetime import datetime, timedelta
from uuid import uuid4

# Add the backend directory to the path
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

from sqlalchemy import select
from app.core.database import get_session_maker
from app.models import (
    User, Campaign, Proposal, ProposalStatus, CampaignStatus,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus
)


async def seed_learning_data():
    """Seed learning data for the first user's first campaign."""
    
    session_maker = get_session_maker()
    async with session_maker() as db:
        # Get first user
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        
        if not user:
            print("❌ No users found. Please create a user first.")
            return
        
        print(f"✓ Found user: {user.username}")
        
        # Get first campaign (or create one if none exist)
        result = await db.execute(
            select(Campaign).where(Campaign.user_id == user.id).limit(1)
        )
        campaign = result.scalar_one_or_none()
        
        if campaign:
            campaign_id = campaign.id
            print(f"✓ Found campaign: {campaign_id}")
        else:
            # Need to create a proposal first, then a campaign
            print("⚠ No campaigns found, creating test proposal and campaign...")
            
            proposal = Proposal(
                user_id=user.id,
                title="Test Campaign for Learning Demo",
                summary="A test campaign to demonstrate the Intelligence tab features",
                detailed_description="This proposal was auto-generated to seed learning data for the Intelligence tab demonstration.",
                initial_budget=500.00,
                risk_level="low",
                risk_description="Low risk test campaign",
                stop_loss_threshold={"type": "budget", "value": 100},
                success_criteria={"completion": True},
                required_tools={},
                required_inputs={},
                status=ProposalStatus.APPROVED,
            )
            db.add(proposal)
            await db.flush()
            print(f"  ✓ Created proposal: {proposal.id}")
            
            campaign = Campaign(
                proposal_id=proposal.id,
                user_id=user.id,
                budget_allocated=500.00,
                success_metrics={"completion": True},
                requirements_checklist=[],
                status=CampaignStatus.COMPLETED,
                budget_spent=125.50,
                revenue_generated=0,
                tasks_total=12,
                tasks_completed=12,
            )
            db.add(campaign)
            await db.flush()
            campaign_id = campaign.id
            print(f"  ✓ Created campaign: {campaign_id}")
        
        # =================================================================
        # Seed Patterns
        # =================================================================
        patterns = [
            CampaignPattern(
                user_id=user.id,
                name="Sequential Content Pipeline",
                description="A proven pattern for content creation campaigns: research → outline → draft → review → publish. Each stage validates before proceeding.",
                pattern_type=PatternType.EXECUTION_SEQUENCE,
                status=PatternStatus.ACTIVE,
                confidence_score=0.92,
                pattern_data={
                    "streams": [
                        {"name": "Research", "order": 1, "avg_duration_hours": 2},
                        {"name": "Outline", "order": 2, "avg_duration_hours": 1},
                        {"name": "Draft", "order": 3, "avg_duration_hours": 4},
                        {"name": "Review", "order": 4, "avg_duration_hours": 1},
                        {"name": "Publish", "order": 5, "avg_duration_hours": 0.5},
                    ],
                    "total_duration_hours": 8.5,
                    "success_campaigns": 12,
                },
                applicability_conditions={
                    "campaign_types": ["content", "blog", "article"],
                    "min_budget": 50,
                },
                times_applied=18,
                times_successful=16,
                source_campaign_id=campaign_id,
                is_global=False,
                tags=["content", "sequential", "validated"],
            ),
            CampaignPattern(
                user_id=user.id,
                name="Parallel Data Collection",
                description="Run multiple data gathering tasks simultaneously, then merge results. Reduces total time by 60% compared to sequential approach.",
                pattern_type=PatternType.TOOL_COMBINATION,
                status=PatternStatus.ACTIVE,
                confidence_score=0.87,
                pattern_data={
                    "tools": [
                        {"tool_slug": "web-scraper", "role": "primary_collector"},
                        {"tool_slug": "api-fetcher", "role": "secondary_collector"},
                        {"tool_slug": "data-merger", "role": "aggregator"},
                    ],
                    "parallelization": True,
                    "avg_time_saved_pct": 60,
                },
                applicability_conditions={
                    "requires_tools": ["web-scraper", "data-merger"],
                    "data_sources_min": 2,
                },
                times_applied=8,
                times_successful=7,
                source_campaign_id=campaign_id,
                is_global=True,
                tags=["data", "parallel", "efficiency"],
            ),
            CampaignPattern(
                user_id=user.id,
                name="Upfront Input Batching",
                description="Collect all required user inputs at campaign start rather than interrupting execution. Reduces wait time and context switching.",
                pattern_type=PatternType.INPUT_COLLECTION,
                status=PatternStatus.ACTIVE,
                confidence_score=0.78,
                pattern_data={
                    "total_inputs": 5,
                    "collection_method": "batch_upfront",
                    "avg_wait_reduction_hours": 4,
                    "user_satisfaction_increase": 0.3,
                },
                applicability_conditions={
                    "predictable_inputs": True,
                    "inputs_count_max": 10,
                },
                times_applied=6,
                times_successful=5,
                source_campaign_id=campaign_id,
                is_global=False,
                tags=["inputs", "ux", "efficiency"],
            ),
            CampaignPattern(
                user_id=user.id,
                name="Retry with Exponential Backoff",
                description="When API calls fail, retry with increasing delays (1s, 2s, 4s, 8s). Prevents rate limiting and handles transient failures gracefully.",
                pattern_type=PatternType.ERROR_RECOVERY,
                status=PatternStatus.ACTIVE,
                confidence_score=0.95,
                pattern_data={
                    "max_retries": 4,
                    "initial_delay_seconds": 1,
                    "backoff_multiplier": 2,
                    "success_rate_after_retry": 0.94,
                },
                applicability_conditions={
                    "error_types": ["rate_limit", "timeout", "503"],
                },
                times_applied=45,
                times_successful=42,
                source_campaign_id=campaign_id,
                is_global=True,
                tags=["error-handling", "resilience", "api"],
            ),
        ]
        
        for p in patterns:
            db.add(p)
        print(f"✓ Added {len(patterns)} patterns")
        
        # =================================================================
        # Seed Lessons
        # =================================================================
        lessons = [
            CampaignLesson(
                user_id=user.id,
                title="API Rate Limits Hit During Peak Hours",
                description="External API calls to pricing service failed repeatedly between 9-11 AM EST due to rate limiting. Campaign was blocked for 3 hours.",
                category=LessonCategory.FAILURE,
                context={
                    "api": "pricing-service",
                    "time_of_day": "09:00-11:00 EST",
                    "day_of_week": "Monday",
                    "error_code": 429,
                },
                trigger_event="API returned 429 Too Many Requests",
                impact_severity="high",
                budget_impact=15.50,
                time_impact_minutes=180,
                prevention_steps=[
                    "Schedule API-heavy tasks outside peak hours (before 9 AM or after 6 PM)",
                    "Implement request queuing with rate limit awareness",
                    "Cache responses when possible to reduce API calls",
                ],
                detection_signals=[
                    "Increasing 429 response codes",
                    "Response times > 5 seconds",
                    "Retry count exceeding 3",
                ],
                source_campaign_id=campaign_id,
                times_applied=3,
                tags=["api", "rate-limit", "scheduling"],
            ),
            CampaignLesson(
                user_id=user.id,
                title="Insufficient Budget Buffer for Retries",
                description="Campaign ran out of budget due to unexpected retries. Original estimate didn't account for 15% retry overhead.",
                category=LessonCategory.INEFFICIENCY,
                context={
                    "original_budget": 100,
                    "actual_spent": 118,
                    "retry_percentage": 18,
                },
                trigger_event="Budget exhausted before completion",
                impact_severity="medium",
                budget_impact=18.00,
                time_impact_minutes=60,
                prevention_steps=[
                    "Add 20% buffer to all budget estimates",
                    "Set budget alerts at 70% and 90% thresholds",
                    "Review retry patterns before campaign start",
                ],
                detection_signals=[
                    "Budget consumption rate > 1.1x expected",
                    "Retry rate > 10%",
                ],
                source_campaign_id=campaign_id,
                times_applied=2,
                tags=["budget", "planning", "retries"],
            ),
            CampaignLesson(
                user_id=user.id,
                title="User Input Timeout Caused Stream Cascade Failure",
                description="Waiting 48+ hours for user input caused dependent streams to timeout. Should have set clearer deadlines and fallback options.",
                category=LessonCategory.USER_FRICTION,
                context={
                    "input_type": "content_approval",
                    "wait_time_hours": 52,
                    "affected_streams": 3,
                },
                trigger_event="Input request exceeded 48 hour timeout",
                impact_severity="high",
                budget_impact=0,
                time_impact_minutes=3120,  # 52 hours
                prevention_steps=[
                    "Set clear deadlines with user upfront (24 hour default)",
                    "Provide sensible default values where possible",
                    "Send reminder notifications at 12 and 20 hours",
                    "Have escalation path for blocking inputs",
                ],
                detection_signals=[
                    "Input pending > 12 hours",
                    "Blocked streams count > 1",
                ],
                source_campaign_id=campaign_id,
                times_applied=1,
                tags=["user-input", "timeout", "dependencies"],
            ),
            CampaignLesson(
                user_id=user.id,
                title="Parallel Execution Caused Resource Contention",
                description="Running 5 GPU-intensive tasks in parallel exceeded available VRAM, causing all tasks to fail. Sequential execution would have succeeded.",
                category=LessonCategory.TOOL_ISSUE,
                context={
                    "resource_type": "GPU",
                    "parallel_tasks": 5,
                    "vram_required_gb": 24,
                    "vram_available_gb": 12,
                },
                trigger_event="CUDA out of memory error",
                impact_severity="medium",
                budget_impact=8.00,
                time_impact_minutes=45,
                prevention_steps=[
                    "Check resource requirements before parallelization",
                    "Implement resource-aware task scheduling",
                    "Set max concurrent GPU tasks based on VRAM",
                ],
                detection_signals=[
                    "Memory usage > 80%",
                    "CUDA allocation warnings",
                ],
                source_campaign_id=campaign_id,
                times_applied=1,
                tags=["resources", "gpu", "parallelization"],
            ),
        ]
        
        for lesson in lessons:
            db.add(lesson)
        print(f"✓ Added {len(lessons)} lessons")
        
        # =================================================================
        # Seed Plan Revisions (only if we have a real campaign)
        # =================================================================
        if campaign:
            revisions = [
                PlanRevision(
                    campaign_id=campaign_id,
                    revision_number=1,
                    trigger=RevisionTrigger.EXTERNAL_CHANGE,
                    trigger_details="Required API discovered to be deprecated, need alternative approach",
                    plan_before={
                        "streams": ["Research", "API Integration", "Analysis"],
                        "tasks_total": 8,
                    },
                    plan_after={
                        "streams": ["Research", "Web Scraping", "Analysis"],
                        "tasks_total": 10,
                    },
                    changes_summary="Replaced API Integration stream with Web Scraping approach",
                    tasks_added=4,
                    tasks_removed=2,
                    tasks_modified=1,
                    streams_added=1,
                    streams_removed=1,
                    reasoning="The pricing API was deprecated last month. Web scraping from the public pricing page provides equivalent data with slightly more processing.",
                    expected_improvement="Campaign can proceed without external API dependency",
                    outcome_assessed=True,
                    outcome_success=True,
                    outcome_notes="Web scraping approach worked well, added 30 minutes but avoided API blocker entirely",
                    initiated_by="agent",
                    approved_by_user=True,
                    created_at=datetime.utcnow() - timedelta(days=5),
                    outcome_assessed_at=datetime.utcnow() - timedelta(days=4),
                ),
                PlanRevision(
                    campaign_id=campaign_id,
                    revision_number=2,
                    trigger=RevisionTrigger.BUDGET_CONCERN,
                    trigger_details="Budget consumption 40% higher than projected at midpoint",
                    plan_before={
                        "parallel_streams": True,
                        "quality_tier": "high",
                        "tasks_remaining": 5,
                    },
                    plan_after={
                        "parallel_streams": False,
                        "quality_tier": "standard",
                        "tasks_remaining": 4,
                    },
                    changes_summary="Switched to sequential execution and standard quality to conserve budget",
                    tasks_added=0,
                    tasks_removed=1,
                    tasks_modified=2,
                    streams_added=0,
                    streams_removed=0,
                    reasoning="At current burn rate, budget would be exhausted before completion. Reducing parallelization and quality tier saves ~30% on remaining tasks.",
                    expected_improvement="Stay within budget while completing core objectives",
                    outcome_assessed=True,
                    outcome_success=True,
                    outcome_notes="Finished with 8% budget remaining. Quality was acceptable for use case.",
                    initiated_by="agent",
                    approved_by_user=True,
                    created_at=datetime.utcnow() - timedelta(days=3),
                    outcome_assessed_at=datetime.utcnow() - timedelta(days=1),
                ),
                PlanRevision(
                    campaign_id=campaign_id,
                    revision_number=3,
                    trigger=RevisionTrigger.USER_FEEDBACK,
                    trigger_details="User requested adding social media distribution to scope",
                    plan_before={
                        "streams": ["Content Creation", "Publishing"],
                        "tasks_total": 6,
                    },
                    plan_after={
                        "streams": ["Content Creation", "Publishing", "Social Distribution"],
                        "tasks_total": 9,
                    },
                    changes_summary="Added Social Distribution stream with 3 new tasks",
                    tasks_added=3,
                    tasks_removed=0,
                    tasks_modified=0,
                    streams_added=1,
                    streams_removed=0,
                    reasoning="User wants to maximize content reach. Social distribution can run in parallel with publishing.",
                    expected_improvement="Increase content visibility by ~40% through social channels",
                    outcome_assessed=False,
                    outcome_success=None,
                    outcome_notes=None,
                    initiated_by="user",
                    approved_by_user=True,
                    created_at=datetime.utcnow() - timedelta(hours=12),
                ),
            ]
            
            for rev in revisions:
                db.add(rev)
            print(f"✓ Added {len(revisions)} plan revisions")
        
        # =================================================================
        # Seed Proactive Suggestions
        # =================================================================
        if campaign:
            suggestions = [
                ProactiveSuggestion(
                    campaign_id=campaign_id,
                    suggestion_type=SuggestionType.OPTIMIZATION,
                    title="Enable Parallel Stream Execution",
                    description="Your Research and Data Collection streams have no dependencies between them. Running them in parallel could reduce total execution time by approximately 3 hours.",
                    status=SuggestionStatus.PENDING,
                    urgency="medium",
                    confidence=0.85,
                    evidence={
                        "independent_streams": ["Research", "Data Collection"],
                        "potential_time_savings_hours": 3,
                        "resource_availability": "sufficient",
                    },
                    based_on_patterns=["parallel-execution-pattern-001"],
                    recommended_action={
                        "type": "configuration_change",
                        "field": "parallel_execution",
                        "value": True,
                        "streams": ["Research", "Data Collection"],
                    },
                    estimated_benefit="Save ~3 hours of execution time",
                    estimated_cost=0,
                    can_auto_apply=True,
                    expires_at=datetime.utcnow() + timedelta(hours=24),
                ),
                ProactiveSuggestion(
                    campaign_id=campaign_id,
                    suggestion_type=SuggestionType.WARNING,
                    title="High API Usage Approaching Rate Limit",
                    description="Current API call rate is 45/minute. The pricing service rate limit is 50/minute. Consider throttling to avoid disruption.",
                    status=SuggestionStatus.PENDING,
                    urgency="high",
                    confidence=0.92,
                    evidence={
                        "current_rate": 45,
                        "limit": 50,
                        "time_to_limit_minutes": 8,
                    },
                    based_on_lessons=["api-rate-limit-lesson-001"],
                    recommended_action={
                        "type": "throttle",
                        "target_rate": 30,
                        "reason": "Stay well under rate limit",
                    },
                    estimated_benefit="Avoid 3+ hour delay from rate limiting",
                    can_auto_apply=True,
                    expires_at=datetime.utcnow() + timedelta(hours=2),
                ),
                ProactiveSuggestion(
                    campaign_id=campaign_id,
                    suggestion_type=SuggestionType.OPPORTUNITY,
                    title="Similar Successful Pattern Available",
                    description="A campaign with similar goals completed successfully last week using a 'Content Pipeline' pattern. Applying this pattern could improve your success rate.",
                    status=SuggestionStatus.PENDING,
                    urgency="low",
                    confidence=0.75,
                    evidence={
                        "similar_campaign_id": str(uuid4()),
                        "similarity_score": 0.82,
                        "pattern_success_rate": 0.89,
                    },
                    based_on_patterns=["content-pipeline-pattern"],
                    recommended_action={
                        "type": "apply_pattern",
                        "pattern_name": "Sequential Content Pipeline",
                    },
                    estimated_benefit="Increase success probability by ~15%",
                    can_auto_apply=False,
                    expires_at=datetime.utcnow() + timedelta(days=3),
                ),
                ProactiveSuggestion(
                    campaign_id=campaign_id,
                    suggestion_type=SuggestionType.COST_SAVING,
                    title="Switch to Batch Processing for Cost Savings",
                    description="The current real-time processing approach costs $0.02/item. Batch processing the remaining 200 items would cost $0.005/item, saving approximately $3.",
                    status=SuggestionStatus.ACCEPTED,
                    urgency="low",
                    confidence=0.88,
                    evidence={
                        "current_cost_per_item": 0.02,
                        "batch_cost_per_item": 0.005,
                        "items_remaining": 200,
                        "potential_savings": 3.00,
                    },
                    recommended_action={
                        "type": "switch_processing_mode",
                        "from": "realtime",
                        "to": "batch",
                    },
                    estimated_benefit="Save $3.00 on processing costs",
                    estimated_cost=0,
                    can_auto_apply=True,
                    user_feedback="Good catch, batch processing is fine for this use case",
                ),
                ProactiveSuggestion(
                    campaign_id=campaign_id,
                    suggestion_type=SuggestionType.WARNING,
                    title="Deadline Risk: Stream Behind Schedule",
                    description="The 'Analysis' stream is 40% behind schedule. At current pace, it will complete 6 hours after the campaign deadline.",
                    status=SuggestionStatus.REJECTED,
                    urgency="high",
                    confidence=0.78,
                    evidence={
                        "stream_name": "Analysis",
                        "progress_pct": 30,
                        "expected_pct": 70,
                        "hours_behind": 6,
                    },
                    recommended_action={
                        "type": "add_resources",
                        "suggestion": "Enable parallel analysis workers",
                    },
                    estimated_benefit="Get back on schedule",
                    estimated_cost=5.00,
                    can_auto_apply=False,
                    user_feedback="Deadline is flexible, no need to spend extra",
                ),
            ]
            
            for s in suggestions:
                db.add(s)
            print(f"✓ Added {len(suggestions)} suggestions")
        
        await db.commit()
        print("\n✅ Learning data seeded successfully!")
        print("   Visit the Intelligence tab on any campaign to see the data.")


if __name__ == "__main__":
    asyncio.run(seed_learning_data())
