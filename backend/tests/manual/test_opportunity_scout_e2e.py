#!/usr/bin/env python
"""
Manual End-to-End Test Script for Opportunity Scout Agent.

This script tests the full flow with real LLM calls and database operations.
Run with: python -m tests.manual.test_opportunity_scout_e2e

Requirements:
- Database must be running and migrated
- LLM API keys must be configured
- Serper API key must be configured (for web search)

Note: These tests use the real database and make real API calls.
They are marked as 'e2e' and skipped by default.
Run with: pytest -m e2e tests/manual/
"""
import asyncio
import json
import logging
import pytest
import sys
from datetime import datetime
from typing import Optional

# Mark as e2e tests - skip by default
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skip(reason="Manual E2E tests require real database and API keys - run with pytest -m e2e")
]

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent.parent.parent))

from sqlalchemy import select
from app.core.database import get_session_maker
from app.agents import opportunity_scout_agent, AgentContext
from app.services.opportunity_service import opportunity_service
from app.models import (
    Opportunity,
    OpportunityStatus,
    RankingTier,
    DiscoveryStrategy,
    StrategyStatus,
    AgentInsight,
)


class Colors:
    """ANSI color codes for pretty output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.ENDC}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")


def print_error(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.ENDC}")


def print_info(text: str):
    print(f"{Colors.CYAN}ℹ {text}{Colors.ENDC}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.ENDC}")


async def test_strategic_planning(skip_if_exists: bool = True):
    """Test Phase 1: Strategic Planning."""
    print_header("Phase 1: Strategic Planning")
    
    async with get_session_maker()() as db:
        # Check for existing strategies
        result = await db.execute(
            select(DiscoveryStrategy).where(DiscoveryStrategy.status == StrategyStatus.ACTIVE)
        )
        existing = list(result.scalars().all())
        
        if existing and skip_if_exists:
            print_info(f"Found {len(existing)} existing strategies, skipping planning")
            for s in existing:
                print(f"  - {s.name}: {s.description[:50]}...")
            return True
        
        print_info("Creating strategic plan...")
        
        context = AgentContext(db=db)
        result = await opportunity_scout_agent.create_strategic_plan(
            context=context,
            force_new=not skip_if_exists,
        )
        
        if result.success:
            print_success(f"Strategic plan created!")
            print_info(f"Tokens used: {result.tokens_used}")
            print_info(f"Model: {result.model_used}")
            print_info(f"Strategies created: {result.data.get('strategies_created', [])}")
            
            # Print the plan
            if result.data.get("plan"):
                print(f"\n{Colors.BLUE}Plan Summary:{Colors.ENDC}")
                plan_text = result.data["plan"][:500]
                print(f"{plan_text}...")
            
            return True
        else:
            print_error(f"Planning failed: {result.message}")
            return False


async def test_discovery():
    """Test Phase 2: Discovery."""
    print_header("Phase 2: Discovery")
    
    async with get_session_maker()() as db:
        # Check for existing strategies
        result = await db.execute(
            select(DiscoveryStrategy).where(DiscoveryStrategy.status == StrategyStatus.ACTIVE)
        )
        strategies = list(result.scalars().all())
        
        if not strategies:
            print_error("No active strategies found. Run planning first.")
            return False
        
        print_info(f"Running discovery with {len(strategies)} strategies...")
        
        context = AgentContext(db=db)
        result = await opportunity_scout_agent.run_discovery(
            context=context,
            max_opportunities=5,  # Limit for testing
        )
        
        if result.success:
            print_success(f"Discovery complete!")
            print_info(f"Opportunities created: {result.data.get('opportunities_created', 0)}")
            print_info(f"Strategies run: {result.data.get('strategies_run', 0)}")
            print_info(f"Tokens used: {result.tokens_used}")
            
            # Show created opportunities
            opp_ids = result.data.get("opportunity_ids", [])
            if opp_ids:
                print(f"\n{Colors.BLUE}Created Opportunities:{Colors.ENDC}")
                for opp_id in opp_ids[:5]:
                    opp = await db.get(Opportunity, opp_id)
                    if opp:
                        print(f"  - {opp.title}")
                        print(f"    Type: {opp.opportunity_type.value}")
                        print(f"    Status: {opp.status.value}")
            
            return True
        else:
            print_error(f"Discovery failed: {result.message}")
            return False


async def test_evaluation():
    """Test Phase 3: Evaluation."""
    print_header("Phase 3: Evaluation")
    
    async with get_session_maker()() as db:
        # Get opportunities to evaluate
        result = await db.execute(
            select(Opportunity).where(
                Opportunity.status.in_([
                    OpportunityStatus.DISCOVERED,
                    OpportunityStatus.RESEARCHING,
                ])
            ).limit(3)  # Limit for testing
        )
        opportunities = list(result.scalars().all())
        
        if not opportunities:
            print_warning("No opportunities to evaluate.")
            return True
        
        print_info(f"Evaluating {len(opportunities)} opportunities...")
        
        context = AgentContext(db=db)
        result = await opportunity_scout_agent.evaluate_opportunities(
            context=context,
            opportunity_ids=[o.id for o in opportunities],
        )
        
        if result.success:
            print_success(f"Evaluation complete!")
            print_info(f"Evaluated: {result.data.get('evaluated', 0)}")
            print_info(f"Tokens used: {result.tokens_used}")
            
            # Show rankings
            print(f"\n{Colors.BLUE}Rankings After Evaluation:{Colors.ENDC}")
            
            # Refresh and show
            for opp in opportunities:
                await db.refresh(opp)
                tier_color = {
                    RankingTier.TOP_PICK: Colors.GREEN,
                    RankingTier.PROMISING: Colors.CYAN,
                    RankingTier.MAYBE: Colors.YELLOW,
                    RankingTier.UNLIKELY: Colors.RED,
                }.get(opp.ranking_tier, Colors.ENDC)
                
                print(f"  #{opp.rank_position or '?'} {opp.title}")
                print(f"     Score: {opp.overall_score or 0:.2f}")
                print(f"     Tier: {tier_color}{opp.ranking_tier.value if opp.ranking_tier else 'N/A'}{Colors.ENDC}")
            
            return True
        else:
            print_error(f"Evaluation failed: {result.message}")
            return False


async def test_user_decision():
    """Test Phase 4: User Decisions."""
    print_header("Phase 4: User Decisions (Simulated)")
    
    async with get_session_maker()() as db:
        # Get top-ranked opportunity
        result = await db.execute(
            select(Opportunity).where(
                Opportunity.status == OpportunityStatus.EVALUATED,
            ).order_by(
                Opportunity.overall_score.desc().nullslast()
            ).limit(1)
        )
        opportunity = result.scalar_one_or_none()
        
        if not opportunity:
            print_warning("No evaluated opportunities to decide on.")
            return True
        
        print_info(f"Simulating approval of: {opportunity.title}")
        
        approved = await opportunity_service.approve_opportunity(
            db=db,
            opportunity_id=opportunity.id,
            user_notes="Automated test approval",
        )
        
        if approved:
            print_success(f"Opportunity approved!")
            print_info(f"Status: {approved.status.value}")
            print_info(f"Decision timestamp: {approved.decision_made_at}")
            return True
        else:
            print_error("Approval failed")
            return False


async def test_learning():
    """Test Phase 5: Learning & Reflection."""
    print_header("Phase 5: Learning & Reflection")
    
    async with get_session_maker()() as db:
        print_info("Running reflection...")
        
        context = AgentContext(db=db)
        result = await opportunity_scout_agent.reflect_and_learn(
            context=context,
            deep_reflection=False,  # Use reasoning tier, not quality
        )
        
        if result.success:
            print_success(f"Reflection complete!")
            print_info(f"Insights created: {result.data.get('insights_created', 0)}")
            print_info(f"Tokens used: {result.tokens_used}")
            
            # Show insights
            insights_result = await db.execute(
                select(AgentInsight).order_by(AgentInsight.created_at.desc()).limit(3)
            )
            insights = list(insights_result.scalars().all())
            
            if insights:
                print(f"\n{Colors.BLUE}Recent Insights:{Colors.ENDC}")
                for insight in insights:
                    print(f"  - [{insight.insight_type.value}] {insight.title}")
                    print(f"    Confidence: {insight.confidence:.0%}")
            
            return True
        else:
            print_error(f"Reflection failed: {result.message}")
            return False


async def test_hopper_status():
    """Test hopper status."""
    print_header("Hopper Status Check")
    
    async with get_session_maker()() as db:
        status = await opportunity_service.get_hopper_status(db)
        
        print(f"  Max Capacity: {status['max_capacity']}")
        print(f"  Active Proposals: {status['active_proposals']}")
        print(f"  Pending Approvals: {status['pending_approvals']}")
        print(f"  Available Slots: {status['available_slots']}")
        
        status_color = {
            "available": Colors.GREEN,
            "warning": Colors.YELLOW,
            "full": Colors.RED,
        }.get(status['status'], Colors.ENDC)
        
        print(f"  Status: {status_color}{status['status'].upper()}{Colors.ENDC}")
        
        return True


async def test_statistics():
    """Test statistics."""
    print_header("Scout Statistics")
    
    async with get_session_maker()() as db:
        stats = await opportunity_service.get_scout_statistics(db, days=30)
        
        print(f"  Period: Last {stats['period_days']} days")
        print(f"\n  {Colors.BLUE}Opportunities:{Colors.ENDC}")
        print(f"    Total: {stats['opportunities']['total']}")
        print(f"    Approved: {stats['opportunities']['approved']}")
        print(f"    Dismissed: {stats['opportunities']['dismissed']}")
        print(f"    Approval Rate: {stats['opportunities']['approval_rate']:.0%}")
        print(f"    Avg Score: {stats['opportunities']['avg_score']:.2f}")
        
        print(f"\n  {Colors.BLUE}Strategies:{Colors.ENDC}")
        print(f"    Total: {stats['strategies']['total']}")
        print(f"    Active: {stats['strategies']['active']}")
        print(f"    Avg Effectiveness: {stats['strategies']['avg_effectiveness']:.2f}")
        
        print(f"\n  Discovery Runs: {stats['discovery_runs']}")
        print(f"  Insights: {stats['insights_count']}")
        
        return True


async def run_all_tests(skip_llm: bool = False):
    """Run all tests in sequence."""
    print(f"\n{Colors.BOLD}Opportunity Scout End-to-End Test{Colors.ENDC}")
    print(f"Started: {datetime.now().isoformat()}")
    
    results = {}
    
    # Phase 1: Planning
    if not skip_llm:
        results["planning"] = await test_strategic_planning()
    else:
        print_warning("Skipping planning (--skip-llm)")
        results["planning"] = True
    
    # Phase 2: Discovery
    if not skip_llm and results.get("planning"):
        results["discovery"] = await test_discovery()
    else:
        print_warning("Skipping discovery")
        results["discovery"] = True
    
    # Phase 3: Evaluation
    if not skip_llm and results.get("discovery"):
        results["evaluation"] = await test_evaluation()
    else:
        print_warning("Skipping evaluation")
        results["evaluation"] = True
    
    # Phase 4: User Decisions
    results["user_decision"] = await test_user_decision()
    
    # Phase 5: Learning
    if not skip_llm:
        results["learning"] = await test_learning()
    else:
        print_warning("Skipping learning")
        results["learning"] = True
    
    # Hopper Status
    results["hopper"] = await test_hopper_status()
    
    # Statistics
    results["statistics"] = await test_statistics()
    
    # Summary
    print_header("Test Summary")
    
    all_passed = all(results.values())
    
    for test_name, passed in results.items():
        status = f"{Colors.GREEN}PASS{Colors.ENDC}" if passed else f"{Colors.RED}FAIL{Colors.ENDC}"
        print(f"  {test_name}: {status}")
    
    print()
    if all_passed:
        print_success("All tests passed!")
    else:
        print_error("Some tests failed.")
    
    return all_passed


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Test Opportunity Scout Agent")
    parser.add_argument("--skip-llm", action="store_true", help="Skip tests that require LLM calls")
    parser.add_argument("--phase", choices=["plan", "discover", "evaluate", "decide", "learn", "all"], 
                       default="all", help="Run specific phase")
    args = parser.parse_args()
    
    if args.phase == "all":
        asyncio.run(run_all_tests(skip_llm=args.skip_llm))
    elif args.phase == "plan":
        asyncio.run(test_strategic_planning(skip_if_exists=False))
    elif args.phase == "discover":
        asyncio.run(test_discovery())
    elif args.phase == "evaluate":
        asyncio.run(test_evaluation())
    elif args.phase == "decide":
        asyncio.run(test_user_decision())
    elif args.phase == "learn":
        asyncio.run(test_learning())
