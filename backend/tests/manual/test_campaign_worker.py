#!/usr/bin/env python3
"""
Manual test script for distributed campaign worker functionality.

This script tests:
1. Agent connection verification
2. Campaign creation and assignment
3. Model tier and token tracking
4. Cleanup of test data

Run from backend directory:
    source venv/bin/activate
    python test_campaign_worker.py
"""
import asyncio
import sys
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select, delete
from app.core.database import get_session_maker, get_engine
from app.models import User, Proposal, Campaign, Conversation, Message, ProposalStatus, RiskLevel, CampaignStatus
from app.services.broker_service import broker_service


# Test IDs for cleanup
TEST_IDS = {
    "proposal_id": None,
    "campaign_id": None,
    "conversation_id": None,
}


async def get_admin_user():
    """Get an admin user for creating test data."""
    engine = get_engine()
    session_maker = get_session_maker()
    
    async with session_maker() as db:
        result = await db.execute(
            select(User).where(User.role == "admin")
        )
        return result.scalar_one_or_none()


async def check_connected_agents():
    """Check which agents are connected to the broker."""
    print("\n" + "="*60)
    print("STEP 1: Check Connected Agents")
    print("="*60)
    
    agents = broker_service.get_connected_agents()
    
    if not agents:
        print("❌ No agents connected!")
        print("   Make sure the resource-agent is running on the remote machine")
        return False
    
    print(f"✅ {len(agents)} agent(s) connected:\n")
    
    campaign_worker_found = False
    for agent in agents:
        print(f"  Agent: {agent['hostname']}")
        print(f"    Display Name: {agent['display_name']}")
        print(f"    Connected: {agent['connected']}")
        print(f"    Campaign Worker: {agent['is_campaign_worker']}")
        print(f"    Campaign Capacity: {agent.get('campaign_capacity', 0)}")
        print(f"    Held Campaigns: {agent.get('held_campaigns', [])}")
        print()
        
        if agent['is_campaign_worker']:
            campaign_worker_found = True
    
    if not campaign_worker_found:
        print("⚠️  No campaign workers found!")
        print("   Enable campaign_worker in config.yaml on the remote agent")
        return False
    
    return True


async def check_campaign_workers():
    """Check available campaign workers."""
    print("\n" + "="*60)
    print("STEP 2: Check Campaign Workers")
    print("="*60)
    
    workers = broker_service.get_available_campaign_workers()
    
    if not workers:
        print("❌ No available campaign workers!")
        return False
    
    print(f"✅ {len(workers)} campaign worker(s) available:\n")
    
    for w in workers:
        available_slots = w.campaign_capacity - len(w.held_campaigns)
        print(f"  Worker: {w.campaign_worker_id}")
        print(f"    Hostname: {w.hostname}")
        print(f"    Capacity: {w.campaign_capacity}")
        print(f"    Available Slots: {available_slots}")
        print()
    
    return True


async def create_test_campaign():
    """Create a test proposal and campaign."""
    print("\n" + "="*60)
    print("STEP 3: Create Test Campaign")
    print("="*60)
    
    user = await get_admin_user()
    if not user:
        print("❌ No admin user found!")
        return None
    
    print(f"  Using user: {user.username} ({user.email})")
    
    engine = get_engine()
    session_maker = get_session_maker()
    
    async with session_maker() as db:
        # Create test proposal
        proposal_id = uuid4()
        TEST_IDS["proposal_id"] = proposal_id
        
        proposal = Proposal(
            id=proposal_id,
            user_id=user.id,
            title="[TEST] Distributed Campaign Worker Test",
            summary="Testing distributed campaign worker with model tier and token tracking",
            detailed_description="""
This is a test campaign to verify:
1. Campaign assignment to remote workers
2. Model tier handling (should use 'reasoning' tier)
3. Token usage tracking back to main app
4. Provider failover logic

Expected behavior:
- Campaign should be assigned to remote worker
- Worker should use GLM (first in priority) if available
- Token usage should be recorded in message metadata
""",
            status=ProposalStatus.APPROVED,
            initial_budget=100.00,
            risk_level=RiskLevel.LOW,
            risk_description="Test campaign - no real risk",
            stop_loss_threshold={"type": "budget", "value": 50.00},
            success_criteria={"test_completed": True},
            required_tools={},
            required_inputs={},
        )
        db.add(proposal)
        
        # Create conversation for campaign
        conversation_id = uuid4()
        TEST_IDS["conversation_id"] = conversation_id
        
        conversation = Conversation(
            id=conversation_id,
            created_by_user_id=user.id,
            conversation_type="campaign",
            title="[TEST] Campaign Worker Test Conversation",
        )
        db.add(conversation)
        
        # Create campaign
        campaign_id = uuid4()
        TEST_IDS["campaign_id"] = campaign_id
        
        campaign = Campaign(
            id=campaign_id,
            user_id=user.id,
            proposal_id=proposal_id,
            conversation_id=conversation_id,
            status=CampaignStatus.ACTIVE,
            budget_allocated=100.00,
            budget_spent=0.00,
            tasks_total=1,
            tasks_completed=0,
            success_metrics={},
        )
        db.add(campaign)
        
        await db.commit()
        
        print(f"✅ Created test data:")
        print(f"    Proposal ID: {proposal_id}")
        print(f"    Campaign ID: {campaign_id}")
        print(f"    Conversation ID: {conversation_id}")
        
        return campaign_id


async def assign_campaign_to_worker(campaign_id):
    """Assign the test campaign to a remote worker."""
    print("\n" + "="*60)
    print("STEP 4: Assign Campaign to Worker")
    print("="*60)
    
    engine = get_engine()
    session_maker = get_session_maker()
    
    async with session_maker() as db:
        # Prepare campaign data (this is what gets sent to the worker)
        campaign_data = {
            "status": "active",
            "current_phase": "executing",
            "proposal_title": "[TEST] Distributed Campaign Worker Test",
            "proposal_summary": "Testing distributed campaign worker",
            "budget_allocated": 100.00,
            "budget_spent": 0.00,
            "revenue_generated": 0.00,
            "tasks_total": 1,
            "tasks_completed": 0,
            "success_metrics": {},
            "requirements_checklist": [],
            "all_requirements_met": False,
            "conversation_history": [],
            "available_tools": [],
            # These should be set by assign_campaign_to_worker defaults
            # model_tier: "reasoning"
            # max_tokens: 6000
        }
        
        print(f"  Campaign data to send:")
        print(f"    model_tier: {campaign_data.get('model_tier', '(will default to reasoning)')}")
        print(f"    max_tokens: {campaign_data.get('max_tokens', '(will default to 6000)')}")
        print()
        
        worker_id = await broker_service.assign_campaign_to_worker(
            db, campaign_id, campaign_data
        )
        
        if worker_id:
            print(f"✅ Campaign assigned to worker: {worker_id}")
            return worker_id
        else:
            print("❌ No workers available to accept campaign")
            return None


async def wait_for_response(campaign_id, timeout=30):
    """Wait for a response from the worker."""
    print("\n" + "="*60)
    print("STEP 5: Wait for Worker Response")
    print("="*60)
    
    print(f"  Waiting up to {timeout} seconds for worker response...")
    
    engine = get_engine()
    session_maker = get_session_maker()
    
    conversation_id = TEST_IDS.get("conversation_id")
    if not conversation_id:
        print("❌ No conversation ID found")
        return False
    
    for i in range(timeout):
        await asyncio.sleep(1)
        
        async with session_maker() as db:
            result = await db.execute(
                select(Message).where(
                    Message.conversation_id == conversation_id
                ).order_by(Message.created_at.desc())
            )
            messages = result.scalars().all()
            
            if messages:
                print(f"\n✅ Received {len(messages)} message(s):\n")
                for msg in messages:
                    print(f"  Message ID: {msg.id}")
                    print(f"  Sender: {msg.sender_type.value}")
                    print(f"  Tokens Used: {msg.tokens_used}")
                    print(f"  Model Used: {msg.model_used}")
                    print(f"  Metadata: {msg.meta_data}")
                    print(f"  Content (first 200 chars):")
                    print(f"    {msg.content[:200]}..." if len(msg.content) > 200 else f"    {msg.content}")
                    print()
                return True
        
        if i % 5 == 0 and i > 0:
            print(f"  ... still waiting ({i}s)")
    
    print("⚠️  Timeout waiting for response")
    return False


async def cleanup_test_data():
    """Clean up all test data."""
    print("\n" + "="*60)
    print("CLEANUP: Removing Test Data")
    print("="*60)
    
    engine = get_engine()
    session_maker = get_session_maker()
    
    async with session_maker() as db:
        # Delete in order (respecting foreign keys)
        
        if TEST_IDS.get("conversation_id"):
            # Messages will cascade delete
            await db.execute(
                delete(Conversation).where(
                    Conversation.id == TEST_IDS["conversation_id"]
                )
            )
            print(f"  Deleted conversation: {TEST_IDS['conversation_id']}")
        
        if TEST_IDS.get("campaign_id"):
            await db.execute(
                delete(Campaign).where(
                    Campaign.id == TEST_IDS["campaign_id"]
                )
            )
            print(f"  Deleted campaign: {TEST_IDS['campaign_id']}")
        
        if TEST_IDS.get("proposal_id"):
            await db.execute(
                delete(Proposal).where(
                    Proposal.id == TEST_IDS["proposal_id"]
                )
            )
            print(f"  Deleted proposal: {TEST_IDS['proposal_id']}")
        
        await db.commit()
        
    print("\n✅ Cleanup complete")


async def main():
    """Run the full test suite."""
    print("\n" + "="*60)
    print("DISTRIBUTED CAMPAIGN WORKER TEST")
    print("="*60)
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    
    try:
        # Step 1: Check agents
        if not await check_connected_agents():
            print("\n❌ Test aborted: No agents connected")
            return 1
        
        # Step 2: Check campaign workers
        if not await check_campaign_workers():
            print("\n❌ Test aborted: No campaign workers available")
            return 1
        
        # Step 3: Create test campaign
        campaign_id = await create_test_campaign()
        if not campaign_id:
            print("\n❌ Test aborted: Failed to create campaign")
            return 1
        
        # Step 4: Assign to worker
        worker_id = await assign_campaign_to_worker(campaign_id)
        if not worker_id:
            print("\n⚠️  Warning: Campaign not assigned (no available workers)")
            # Continue to cleanup
        else:
            # Step 5: Wait for response
            await wait_for_response(campaign_id, timeout=60)
        
        return 0
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
        
    finally:
        # Always cleanup
        await cleanup_test_data()


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    print(f"\nTest completed with exit code: {exit_code}")
    sys.exit(exit_code)
