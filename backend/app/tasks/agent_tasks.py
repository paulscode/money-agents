"""Celery tasks for agent execution and scheduling.

These tasks run in Celery workers and handle:
- Periodic agent execution (Opportunity Scout)
- Event-driven agent execution (Proposal Writer on opportunity approval)
- Agent health checks and budget monitoring
"""
import asyncio
import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from app.core.celery_app import celery_app

from app.core.datetime_utils import utc_now, ensure_utc
from app.core.database import get_db_context
from app.services.agent_scheduler_service import agent_scheduler_service, calculate_cost
from app.services.opportunity_service import opportunity_service
from app.models.agent_scheduler import AgentStatus, AgentRunStatus

logger = logging.getLogger(__name__)


async def _cleanup_db_pool():
    """Clean up the database pool for the current event loop."""
    from app.core.database import _engines, _session_makers, _get_loop_id
    loop_id = _get_loop_id()
    if loop_id in _engines:
        engine = _engines.pop(loop_id)
        await engine.dispose()
        logger.debug(f"Disposed engine for loop {loop_id}")
    if loop_id in _session_makers:
        del _session_makers[loop_id]


def run_async(coro):
    """Run an async coroutine in Celery (which is sync)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Clean up database connections before closing loop
        loop.run_until_complete(_cleanup_db_pool())
        loop.close()


# =============================================================================
# Opportunity Scout Tasks
# =============================================================================

@celery_app.task(bind=True, name="app.tasks.agent_tasks.run_opportunity_scout")
def run_opportunity_scout(self, force: bool = False):
    """
    Run the Opportunity Scout agent.
    
    This task is scheduled to run periodically (default: every 6 hours).
    It searches for new money-making opportunities and ranks them.
    
    Args:
        force: If True, run even if not due (for manual triggers)
    """
    return run_async(_run_opportunity_scout_async(force))


async def _run_opportunity_scout_async(force: bool = False):
    """Async implementation of Opportunity Scout run."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="opportunity_scout")
        if not agent:
            logger.error("Opportunity Scout agent not found in database")
            return {"error": "Agent not found"}
        
        # Check if agent can run
        if not agent.is_enabled and not force:
            logger.info("Opportunity Scout is disabled, skipping")
            return {"skipped": True, "reason": "Agent disabled"}
        
        if agent.status == AgentStatus.RUNNING and not force:
            logger.info("Opportunity Scout is already running, skipping")
            return {"skipped": True, "reason": "Already running"}
        
        # Check if due to run
        if not force and agent.next_run_at and utc_now() < ensure_utc(agent.next_run_at):
            logger.debug(f"Opportunity Scout not due until {agent.next_run_at}")
            return {"skipped": True, "reason": "Not due yet"}
        
        # Check backlog size: skip if too many unreviewed opportunities
        if not force:
            settings = await opportunity_service.get_any_user_settings(db)
            max_backlog = settings.max_backlog_size if settings else 200
            if max_backlog > 0:
                unreviewed = await opportunity_service.count_unreviewed_opportunities(db)
                if unreviewed >= max_backlog:
                    logger.info(
                        f"Opportunity Scout skipped: {unreviewed} unreviewed opportunities "
                        f">= max backlog size {max_backlog}"
                    )
                    return {
                        "skipped": True,
                        "reason": "Backlog full",
                        "unreviewed": unreviewed,
                        "max_backlog_size": max_backlog,
                    }
        
        # Create and start run
        run = await agent_scheduler_service.create_run(
            db,
            slug="opportunity_scout",
            trigger_type="scheduled" if not force else "manual",
            trigger_reason="Periodic discovery run" if not force else "Manual trigger",
            force=force,
        )
        
        if not run:
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            # Import here to avoid circular imports
            from app.agents.opportunity_scout import OpportunityScoutAgent
            from app.agents.base import AgentContext
            
            # Create agent instance
            scout = OpportunityScoutAgent()
            context = AgentContext(db=db)
            
            # Execute discovery phases
            logger.info("Starting Opportunity Scout discovery run")
            
            # Phase 1 & 2: Execute discovery
            result = await scout.execute("discover", context)
            
            # Calculate cost from result
            tokens = result.tokens_used if result else 0
            model = result.model_used if result else None
            cost = 0.0
            if model and tokens > 0:
                # Rough estimate: 70% input, 30% output tokens
                cost = calculate_cost(model, int(tokens * 0.7), int(tokens * 0.3))
            
            items_found = 0
            items_evaluated = 0
            if result and result.data:
                items_found = result.data.get("opportunities_found", 0)
            
            # Phase 3 & 4: Evaluate and rank discovered opportunities
            logger.info("Running evaluation phase to score and rank opportunities...")
            try:
                eval_result = await scout.execute("evaluate", context)
                if eval_result and eval_result.data:
                    items_evaluated = eval_result.data.get("evaluated", 0)
                    logger.info(f"Evaluation complete: {items_evaluated} opportunities scored")
                    # Add evaluation tokens to total
                    tokens += eval_result.tokens_used or 0
            except Exception as eval_error:
                logger.warning(f"Evaluation phase failed (non-fatal): {eval_error}")
            
            # Phase 5: Learning - reflect and evolve strategies
            logger.info("Running learning phase to evolve strategies...")
            try:
                learn_result = await scout.reflect_and_learn(context, deep_reflection=False)
                if learn_result and learn_result.data:
                    insights = learn_result.data.get("insights_created", 0)
                    evolved = learn_result.data.get("strategies_evolved", 0)
                    logger.info(f"Learning complete: {insights} insights, {evolved} strategies evolved")
                    # Add learning tokens to total
                    tokens += learn_result.tokens_used or 0
            except Exception as learn_error:
                logger.warning(f"Learning phase failed (non-fatal): {learn_error}")
            
            # Complete the run
            run_metadata = result.data if result else {}
            run_metadata["opportunities_evaluated"] = items_evaluated
            
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=items_found,
                items_created=items_found,
                tokens_used=tokens,
                cost_usd=cost,
                model_used=model,
                metadata=run_metadata,
            )
            
            logger.info(f"Opportunity Scout completed: found {items_found}, evaluated {items_evaluated} opportunities")
            return {
                "success": True,
                "run_id": str(run.id),
                "opportunities_found": items_found,
                "opportunities_evaluated": items_evaluated,
                "tokens_used": tokens,
                "cost_usd": cost,
            }
            
        except Exception as e:
            logger.exception(f"Opportunity Scout failed: {e}")
            await agent_scheduler_service.fail_run(
                db,
                run.id,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            return {"error": str(e), "run_id": str(run.id)}


@celery_app.task(bind=True, name="app.tasks.agent_tasks.review_user_ideas")
def review_user_ideas(self, force: bool = False):
    """
    Review new user ideas and classify them.
    
    This task runs periodically (default: every 15 minutes) and:
    1. Gets all new (unreviewed) ideas
    2. Classifies them as tool-related or opportunity-related
    3. For opportunity ideas, distills them into strategic context
    
    Args:
        force: If True, run even if not due (for manual triggers)
    """
    return run_async(_review_user_ideas_async(force))


async def _review_user_ideas_async(force: bool = False):
    """Async implementation of idea review."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="opportunity_scout")
        if not agent:
            logger.error("Opportunity Scout agent not found in database")
            return {"error": "Agent not found"}
        
        # Check if agent can run (use Scout since ideas feed into Scout)
        if not agent.is_enabled and not force:
            logger.info("Opportunity Scout is disabled, skipping idea review")
            return {"skipped": True, "reason": "Agent disabled"}
        
        # Check for new ideas
        from app.services.ideas_service import IdeasService
        ideas_service = IdeasService(db)
        
        # Quick check if there are any new ideas
        from app.models import UserIdea, IdeaStatus
        from sqlalchemy import select, func
        result = await db.execute(
            select(func.count(UserIdea.id)).where(UserIdea.status == IdeaStatus.NEW.value)
        )
        new_count = result.scalar() or 0
        
        if new_count == 0:
            logger.debug("No new ideas to review")
            return {"skipped": True, "reason": "No new ideas"}
        
        # Create and start run (track under opportunity_scout since it does the review)
        run = await agent_scheduler_service.create_run(
            db,
            slug="opportunity_scout",
            trigger_type="scheduled" if not force else "manual",
            trigger_reason=f"Idea review - {new_count} new ideas",
        )
        
        if not run:
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            # Import here to avoid circular imports
            from app.agents.opportunity_scout import OpportunityScoutAgent
            from app.agents.base import AgentContext
            
            # Create agent instance
            scout = OpportunityScoutAgent()
            context = AgentContext(db=db)
            
            # Execute idea review
            logger.info(f"Starting idea review for {new_count} ideas")
            result = await scout.review_ideas(context, limit=20)
            
            # Calculate cost from result
            tokens = result.tokens_used if result else 0
            model = result.model_used if result else None
            cost = 0.0
            if model and tokens > 0:
                cost = calculate_cost(model, int(tokens * 0.7), int(tokens * 0.3))
            
            items_processed = 0
            if result and result.data:
                items_processed = result.data.get("reviewed", 0)
            
            # Complete the run
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=items_processed,
                items_created=result.data.get("context_added", 0) if result and result.data else 0,
                tokens_used=tokens,
                cost_usd=cost,
                model_used=model,
                metadata=result.data if result else None,
            )
            
            logger.info(f"Idea review completed: {items_processed} reviewed")
            return {
                "success": True,
                "run_id": str(run.id),
                "reviewed": items_processed,
                "tokens_used": tokens,
                "cost_usd": cost,
            }
            
        except Exception as e:
            logger.exception(f"Idea review failed: {e}")
            if run:
                await agent_scheduler_service.fail_run(
                    db,
                    run.id,
                    error_message=str(e),
                    error_traceback=traceback.format_exc(),
                )
                return {"error": str(e), "run_id": str(run.id)}
            return {"error": str(e)}


# =============================================================================
# Proposal Writer Tasks
# =============================================================================

@celery_app.task(bind=True, name="app.tasks.agent_tasks.check_approved_opportunities")
def check_approved_opportunities(self):
    """
    Check for approved opportunities that need proposals.
    
    This task runs frequently (default: every 5 minutes) and:
    1. Looks for opportunities with status=APPROVED and no proposal_id
    2. Creates a proposal draft for each
    3. Optionally triggers Proposal Writer to refine
    """
    return run_async(_check_approved_opportunities_async())


async def _check_approved_opportunities_async():
    """Async implementation of checking approved opportunities."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="proposal_writer")
        if not agent or not agent.is_enabled:
            logger.info("Proposal Writer is disabled, skipping")
            return {"skipped": True, "reason": "Agent disabled"}
        
        if agent.status == AgentStatus.RUNNING:
            logger.info("Proposal Writer is already running, skipping")
            return {"skipped": True, "reason": "Already running"}
        
        # Check for pending events
        events = await agent_scheduler_service.get_pending_events(
            db,
            agent_slug="proposal_writer",
            limit=5,
        )
        
        # Create a run record for this scheduled check
        run = await agent_scheduler_service.create_run(
            db,
            slug="proposal_writer",
            trigger_type="scheduled",
            trigger_reason=f"Scheduled check - {len(events)} pending events",
        )
        
        if not run:
            logger.error("Failed to create run for Proposal Writer")
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            if not events:
                logger.debug("No pending events for Proposal Writer")
                # Even if no events, check for stuck proposals and opportunities
                stuck_count = await _check_stuck_draft_proposals(db)
                orphan_count = await _check_stuck_approved_opportunities(db)
                
                # Complete with 0 items - this is a successful "no work to do" run
                await agent_scheduler_service.complete_run(
                    db,
                    run.id,
                    items_processed=0,
                    items_created=0,
                    tokens_used=0,
                    cost_usd=0.0,
                    metadata={
                        "message": "No pending events",
                        "stuck_drafts_recovered": stuck_count,
                        "orphan_opportunities_recovered": orphan_count,
                    },
                )
                return {
                    "processed": 0, 
                    "stuck_drafts_recovered": stuck_count, 
                    "orphan_opportunities_recovered": orphan_count,
                    "run_id": str(run.id),
                }
            
            # Process each event
            processed = 0
            total_tokens = 0
            total_cost = 0.0
            
            for event in events:
                if event.event_type == "opportunity.approved":
                    try:
                        result = await _process_approved_opportunity(db, event)
                        if result.get("success"):
                            processed += 1
                            total_tokens += result.get("tokens_used", 0)
                            total_cost += result.get("cost_usd", 0.0)
                    except Exception as e:
                        logger.exception(f"Failed to process event {event.id}: {e}")
            
            # Also check for stuck DRAFT_FROM_SCOUT proposals
            stuck_count = await _check_stuck_draft_proposals(db)
            
            # Also check for stuck approved opportunities (marked processed but no proposal created)
            orphan_count = await _check_stuck_approved_opportunities(db)
            
            # Complete the run
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=len(events),
                items_created=processed,
                tokens_used=total_tokens,
                cost_usd=total_cost,
                metadata={
                    "events_found": len(events), 
                    "proposals_created": processed, 
                    "stuck_drafts_recovered": stuck_count,
                    "orphan_opportunities_recovered": orphan_count,
                },
            )
            
            return {"processed": processed, "run_id": str(run.id)}
            
        except Exception as e:
            logger.exception(f"Proposal Writer check failed: {e}")
            await agent_scheduler_service.fail_run(
                db,
                run.id,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            return {"error": str(e), "run_id": str(run.id)}


async def _check_stuck_draft_proposals(db) -> int:
    """
    Check for proposals stuck in DRAFT_FROM_SCOUT status and re-trigger refinement.
    
    This recovers proposals that were created but whose refinement task failed
    to execute (e.g., Celery worker was down, Redis connection lost, etc.).
    
    Only re-triggers for proposals older than 5 minutes to avoid double-processing
    proposals that are currently being refined.
    
    Returns:
        Number of stuck proposals that were re-triggered for refinement.
    """
    from app.models import Proposal, ProposalStatus
    from sqlalchemy import select
    from datetime import timedelta
    
    # Only consider proposals stuck for more than 5 minutes
    cutoff = utc_now() - timedelta(minutes=5)
    
    result = await db.execute(
        select(Proposal)
        .where(Proposal.status == ProposalStatus.DRAFT_FROM_SCOUT)
        .where(Proposal.created_at < cutoff)
        .limit(5)  # Process up to 5 at a time to avoid overwhelming the system
    )
    stuck_proposals = result.scalars().all()
    
    recovered = 0
    for proposal in stuck_proposals:
        logger.info(f"Found stuck DRAFT_FROM_SCOUT proposal {proposal.id}, re-triggering refinement")
        try:
            run_proposal_writer.delay(str(proposal.id))
            recovered += 1
        except Exception as e:
            logger.error(f"Failed to re-trigger refinement for proposal {proposal.id}: {e}")
    
    if recovered > 0:
        logger.info(f"Re-triggered refinement for {recovered} stuck proposals")
    
    return recovered


async def _check_stuck_approved_opportunities(db) -> int:
    """
    Check for approved opportunities that have processed events but no proposal created.
    
    This recovers opportunities that were approved and had their events processed,
    but the proposal creation step failed (e.g., database error, race condition, etc.).
    
    For each such opportunity, we create a fake event and process it to create the proposal.
    
    Returns:
        Number of orphaned opportunities that were recovered.
    """
    from app.models import Opportunity, AgentEvent
    from app.models.opportunity import OpportunityStatus
    from sqlalchemy import select, and_
    from datetime import timedelta
    
    # Find approved opportunities without proposals, older than 5 minutes
    cutoff = utc_now() - timedelta(minutes=5)
    
    result = await db.execute(
        select(Opportunity)
        .where(
            and_(
                Opportunity.status == OpportunityStatus.APPROVED,
                Opportunity.proposal_id.is_(None),
                Opportunity.updated_at < cutoff,
            )
        )
        .limit(5)  # Process up to 5 at a time
    )
    stuck_opportunities = result.scalars().all()
    
    recovered = 0
    for opp in stuck_opportunities:
        logger.info(f"Found stuck approved opportunity {opp.id} without proposal, creating proposal")
        try:
            # Create a synthetic event to process
            # First check if there's an existing event we can reuse
            event_result = await db.execute(
                select(AgentEvent)
                .where(
                    and_(
                        AgentEvent.source_id == opp.id,
                        AgentEvent.event_type == "opportunity.approved",
                    )
                )
                .limit(1)
            )
            existing_event = event_result.scalar_one_or_none()
            
            if existing_event:
                # Reset the event to unprocessed and re-process it
                existing_event.is_processed = False
                existing_event.processed_at = None
                await db.commit()
                
                # Process it
                result = await _process_approved_opportunity(db, existing_event)
                if result.get("success"):
                    recovered += 1
                    logger.info(f"Successfully recovered opportunity {opp.id}")
                else:
                    logger.warning(f"Failed to recover opportunity {opp.id}: {result}")
            else:
                # Create a new event if none exists
                logger.warning(f"No event found for approved opportunity {opp.id}, creating one")
                new_event = AgentEvent(
                    agent_slug="proposal_writer",
                    event_type="opportunity.approved",
                    source_type="opportunity",
                    source_id=opp.id,
                    event_data={"recovered": True},
                )
                db.add(new_event)
                await db.flush()
                
                result = await _process_approved_opportunity(db, new_event)
                if result.get("success"):
                    recovered += 1
                    logger.info(f"Successfully created proposal for orphan opportunity {opp.id}")
                else:
                    logger.warning(f"Failed to create proposal for opportunity {opp.id}: {result}")
        except Exception as e:
            logger.exception(f"Failed to recover opportunity {opp.id}: {e}")
    
    if recovered > 0:
        logger.info(f"Recovered {recovered} orphaned approved opportunities")
    
    return recovered


async def _process_approved_opportunity(db, event):
    """
    Process a single approved opportunity event.
    
    Creates a draft proposal from the opportunity data with:
    - Status: DRAFT_FROM_SCOUT (awaiting refinement)
    - Research context: Populated from opportunity data
    - Link: source_opportunity_id → opportunity.id
    """
    from app.models import Opportunity, Proposal, ProposalStatus, RiskLevel
    from app.models.opportunity import OpportunityType, EffortLevel
    
    opportunity_id = event.source_id
    opportunity = await db.get(Opportunity, opportunity_id)
    
    if not opportunity:
        logger.warning(f"Opportunity {opportunity_id} not found")
        await agent_scheduler_service.mark_event_processed(db, event.id)
        return {"success": False, "reason": "Opportunity not found"}
    
    if opportunity.proposal_id:
        logger.info(f"Opportunity {opportunity_id} already has proposal {opportunity.proposal_id}")
        await agent_scheduler_service.mark_event_processed(db, event.id)
        return {"success": True, "reason": "Already has proposal"}
    
    # Map opportunity data to proposal fields
    risk_mapping = {
        0.8: RiskLevel.LOW,
        0.6: RiskLevel.MEDIUM,
        0.0: RiskLevel.HIGH,
    }
    risk_level = RiskLevel.MEDIUM  # Default
    if opportunity.overall_score:
        for threshold, level in sorted(risk_mapping.items(), reverse=True):
            if opportunity.overall_score >= threshold:
                risk_level = level
                break
    
    # Build research context from opportunity data
    research_context = {
        "source": {
            "type": opportunity.source_type,
            "query": opportunity.source_query,
            "urls": opportunity.source_urls or [],
            "raw_signal": opportunity.raw_signal,
        },
        "assessment": {
            "initial": opportunity.initial_assessment,
            "detailed": opportunity.detailed_analysis,
            "confidence": opportunity.confidence_score,
        },
        "scoring": {
            "overall": opportunity.overall_score,
            "breakdown": opportunity.score_breakdown,
            "tier": opportunity.ranking_tier.value if opportunity.ranking_tier else None,
            "factors": opportunity.ranking_factors,
        },
        "timing": {
            "discovered_at": opportunity.discovered_at.isoformat() if opportunity.discovered_at else None,
            "time_sensitivity": opportunity.time_sensitivity.value if opportunity.time_sensitivity else None,
        },
        "requirements": {
            "skills": opportunity.required_skills or [],
            "tools": opportunity.required_tools or [],
            "blocking": opportunity.blocking_requirements or [],
        },
    }
    
    # Build initial budget from opportunity's estimated cost
    initial_budget = 0.0
    if opportunity.estimated_cost:
        initial_budget = float(opportunity.estimated_cost.get("upfront", 0) or 0)
    if initial_budget <= 0:
        initial_budget = 100.0  # Default minimum budget
    
    # Extract expected returns
    expected_returns = opportunity.estimated_revenue_potential or {
        "min": 0,
        "max": 0,
        "timeframe": "unknown",
        "recurring": False,
    }
    
    # Determine Bitcoin budget for crypto/Bitcoin-related opportunities
    bitcoin_budget_sats = None
    bitcoin_budget_rationale = None
    if _is_bitcoin_opportunity(opportunity):
        # Use sats cost estimate from eval if available
        if opportunity.estimated_cost and opportunity.estimated_cost.get("sats"):
            bitcoin_budget_sats = int(opportunity.estimated_cost["sats"])
            bitcoin_budget_rationale = "Auto-estimated from opportunity cost analysis (sats)"
        else:
            # Conservative default for Bitcoin opportunities: 100k sats
            bitcoin_budget_sats = 100000
            bitcoin_budget_rationale = "Default Bitcoin budget for crypto opportunity — adjust via Proposal Writer"
    
    # Extract required tools and inputs
    required_tools = {}
    if opportunity.required_tools:
        for i, tool in enumerate(opportunity.required_tools):
            required_tools[f"tool_{i+1}"] = {"name": tool, "status": "needed"}
    
    required_inputs = {}
    if opportunity.required_skills:
        for i, skill in enumerate(opportunity.required_skills):
            required_inputs[f"skill_{i+1}"] = {"name": skill, "type": "skill"}
    
    # Create draft proposal
    # We need a user_id - get from the event data or use the opportunity's context
    user_id = event.event_data.get("user_id") if event.event_data else None
    if not user_id:
        # Fallback: get the first admin user
        from app.models import User, UserRole
        from sqlalchemy import select
        result = await db.execute(
            select(User).where(User.role == UserRole.ADMIN.value).limit(1)
        )
        admin_user = result.scalar_one_or_none()
        if admin_user:
            user_id = admin_user.id
        else:
            logger.error("No admin user found to assign proposal to")
            await agent_scheduler_service.mark_event_processed(db, event.id)
            return {"success": False, "reason": "No user found for proposal"}
    
    proposal = Proposal(
        user_id=user_id,
        title=f"[Draft] {opportunity.title}",
        summary=opportunity.summary,
        detailed_description=opportunity.detailed_analysis or opportunity.initial_assessment or opportunity.summary,
        status=ProposalStatus.DRAFT_FROM_SCOUT,
        initial_budget=initial_budget,
        bitcoin_budget_sats=bitcoin_budget_sats,
        bitcoin_budget_rationale=bitcoin_budget_rationale,
        recurring_costs=opportunity.estimated_cost,
        expected_returns=expected_returns,
        risk_level=risk_level,
        risk_description=f"Auto-assessed from opportunity score: {opportunity.overall_score:.2f}" if opportunity.overall_score else "Risk not assessed",
        stop_loss_threshold={"max_loss_usd": initial_budget * 0.5, "review_trigger": initial_budget * 0.3},
        success_criteria={"revenue_target": expected_returns.get("min", 0), "timeframe": expected_returns.get("timeframe", "unknown")},
        required_tools=required_tools,
        required_inputs=required_inputs,
        source=f"opportunity_scout:{opportunity.id}",
        source_opportunity_id=opportunity.id,
        research_context=research_context,
        tags={"auto_generated": True, "opportunity_type": opportunity.opportunity_type.value if opportunity.opportunity_type else "other"},
    )
    
    db.add(proposal)
    await db.flush()
    
    # Update opportunity with proposal link
    opportunity.proposal_id = proposal.id
    
    await db.commit()
    
    logger.info(f"Created draft proposal {proposal.id} from opportunity {opportunity_id}")
    
    await agent_scheduler_service.mark_event_processed(db, event.id)
    
    # Trigger Proposal Writer to refine the draft asynchronously
    run_proposal_writer.delay(str(proposal.id))
    
    return {
        "success": True,
        "opportunity_id": str(opportunity_id),
        "proposal_id": str(proposal.id),
    }


def _is_bitcoin_opportunity(opportunity) -> bool:
    """Check if an opportunity involves Bitcoin/Lightning/crypto based on its content."""
    bitcoin_keywords = {'bitcoin', 'btc', 'lightning', 'satoshi', 'sats', 'nostr', 'zap',
                        'cryptocurrency', 'crypto', 'ln ', 'lnurl', 'bolt11', 'on-chain',
                        'onchain', 'wallet', 'mempool'}
    text = f"{opportunity.title} {opportunity.summary} {opportunity.initial_assessment or ''}".lower()
    return any(kw in text for kw in bitcoin_keywords)


@celery_app.task(bind=True, name="app.tasks.agent_tasks.run_proposal_writer")
def run_proposal_writer(self, proposal_id: str):
    """
    Run the Proposal Writer agent to refine a draft proposal.
    
    This is triggered after a proposal is auto-created from an approved
    opportunity. The agent uses the research_context to refine the proposal.
    
    Args:
        proposal_id: UUID of the proposal to refine
    """
    return run_async(_run_proposal_writer_async(proposal_id))


async def _run_proposal_writer_async(proposal_id: str):
    """Async implementation of Proposal Writer run."""
    from app.models import Proposal, ProposalStatus
    from uuid import UUID as PyUUID
    
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="proposal_writer")
        if not agent:
            logger.error("Proposal Writer agent not found in database")
            return {"error": "Agent not found"}
        
        if not agent.is_enabled:
            logger.info("Proposal Writer is disabled")
            return {"skipped": True, "reason": "Agent disabled"}
        
        # Get the proposal
        proposal_uuid = PyUUID(proposal_id) if isinstance(proposal_id, str) else proposal_id
        proposal = await db.get(Proposal, proposal_uuid)
        
        if not proposal:
            logger.error(f"Proposal {proposal_id} not found")
            return {"error": "Proposal not found"}
        
        if proposal.status != ProposalStatus.DRAFT_FROM_SCOUT:
            logger.info(f"Proposal {proposal_id} is not a draft (status={proposal.status})")
            return {"skipped": True, "reason": f"Not a draft: {proposal.status}"}
        
        # Create and start run
        run = await agent_scheduler_service.create_run(
            db,
            slug="proposal_writer",
            trigger_type="event",
            trigger_reason=f"Refining draft proposal {proposal_id}",
        )
        
        if not run:
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            from app.agents.proposal_writer import ProposalWriterAgent
            from app.agents.base import AgentContext
            
            # Build proposal data dict from model
            proposal_data = {
                "title": proposal.title,
                "summary": proposal.summary,
                "detailed_description": proposal.detailed_description,
                "initial_budget": float(proposal.initial_budget),
                "recurring_costs": proposal.recurring_costs,
                "expected_returns": proposal.expected_returns,
                "risk_level": proposal.risk_level.value if proposal.risk_level else None,
                "risk_description": proposal.risk_description,
                "stop_loss_threshold": proposal.stop_loss_threshold,
                "success_criteria": proposal.success_criteria,
                "required_tools": proposal.required_tools,
                "required_inputs": proposal.required_inputs,
                "implementation_timeline": proposal.implementation_timeline,
            }
            
            research_context = proposal.research_context or {}
            
            # Create agent and context
            writer = ProposalWriterAgent()
            context = AgentContext(db=db)
            
            # Run refinement
            logger.info(f"Starting Proposal Writer refinement for proposal {proposal_id}")
            result = await writer.refine_from_scout(
                context=context,
                proposal_data=proposal_data,
                research_context=research_context,
            )
            
            if result.success and result.data:
                refined = result.data.get("refined_proposal", {})
                
                # Update proposal with refined data
                if refined.get("title"):
                    proposal.title = refined["title"]
                if refined.get("summary"):
                    proposal.summary = refined["summary"]
                if refined.get("detailed_description"):
                    proposal.detailed_description = refined["detailed_description"]
                if refined.get("initial_budget"):
                    proposal.initial_budget = float(refined["initial_budget"])
                if refined.get("recurring_costs"):
                    proposal.recurring_costs = refined["recurring_costs"]
                if refined.get("expected_returns"):
                    proposal.expected_returns = refined["expected_returns"]
                if refined.get("risk_level"):
                    from app.models import RiskLevel
                    try:
                        proposal.risk_level = RiskLevel(refined["risk_level"])
                    except ValueError:
                        pass
                if refined.get("risk_description"):
                    proposal.risk_description = refined["risk_description"]
                if refined.get("stop_loss_threshold"):
                    proposal.stop_loss_threshold = refined["stop_loss_threshold"]
                if refined.get("success_criteria"):
                    proposal.success_criteria = refined["success_criteria"]
                if refined.get("required_tools"):
                    proposal.required_tools = refined["required_tools"]
                if refined.get("required_inputs"):
                    proposal.required_inputs = refined["required_inputs"]
                if refined.get("implementation_timeline"):
                    proposal.implementation_timeline = refined["implementation_timeline"]
                
                # Update status to PENDING (ready for user review)
                proposal.status = ProposalStatus.PENDING
                
                await db.commit()
                
                logger.info(f"Proposal {proposal_id} refined and updated to PENDING status")
            else:
                logger.warning(f"Refinement did not produce data: {result.message}")
            
            # Calculate cost
            tokens = result.tokens_used or 0
            model = result.model_used
            cost = 0.0
            if model and tokens > 0:
                cost = calculate_cost(model, int(tokens * 0.7), int(tokens * 0.3))
            
            # Complete the run
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=1,
                items_created=1 if result.success else 0,
                tokens_used=tokens,
                cost_usd=cost,
                model_used=model,
                metadata={
                    "proposal_id": proposal_id,
                    "refined": result.success,
                },
            )
            
            return {
                "success": result.success,
                "run_id": str(run.id),
                "proposal_id": proposal_id,
                "tokens_used": tokens,
                "cost_usd": cost,
            }
            
        except Exception as e:
            logger.exception(f"Proposal Writer failed: {e}")
            await agent_scheduler_service.fail_run(
                db,
                run.id,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            return {"error": str(e), "run_id": str(run.id)}


# =============================================================================
# Tool Scout Tasks
# =============================================================================

@celery_app.task(bind=True, name="app.tasks.agent_tasks.run_tool_scout")
def run_tool_scout(self, force: bool = False):
    """
    Run the Tool Scout agent for discovery.
    
    This task is scheduled to run periodically (default: every 12 hours).
    It searches for new AI tools and updates the knowledge base.
    
    Args:
        force: If True, run even if not due (for manual triggers)
    """
    return run_async(_run_tool_scout_async(force))


async def _run_tool_scout_async(force: bool = False):
    """Async implementation of Tool Scout run."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="tool_scout")
        if not agent:
            logger.error("Tool Scout agent not found in database")
            return {"error": "Agent not found"}
        
        if not agent.is_enabled and not force:
            logger.info("Tool Scout is disabled, skipping")
            return {"skipped": True, "reason": "Agent disabled"}
        
        if agent.status == AgentStatus.RUNNING and not force:
            logger.info("Tool Scout is already running, skipping")
            return {"skipped": True, "reason": "Already running"}
        
        if not force and agent.next_run_at and utc_now() < ensure_utc(agent.next_run_at):
            logger.debug(f"Tool Scout not due until {agent.next_run_at}")
            return {"skipped": True, "reason": "Not due yet"}
        
        # Create and start run
        run = await agent_scheduler_service.create_run(
            db,
            slug="tool_scout",
            trigger_type="scheduled" if not force else "manual",
            trigger_reason="Scheduled tool discovery" if not force else "Manual trigger",
            force=force,
        )
        
        if not run:
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            from app.agents.tool_scout import ToolScoutAgent
            from app.agents.base import AgentContext
            
            scout = ToolScoutAgent()
            context = AgentContext(db=db)
            
            logger.info("Starting Tool Scout discovery")
            
            total_tokens = 0
            total_cost = 0.0
            total_entries = 0
            
            # Phase 1: Process tool ideas from queue
            ideas_result = await scout.process_tool_ideas(context)
            total_tokens += ideas_result.tokens_used or 0
            total_cost += ideas_result.cost_usd or 0
            ideas_processed = ideas_result.data.get("processed", 0) if ideas_result.data else 0
            
            # Phase 2: Discover new tools
            discover_result = await scout.discover_tools(context)
            total_tokens += discover_result.tokens_used or 0
            total_cost += discover_result.cost_usd or 0
            total_entries += discover_result.data.get("entries_added", 0) if discover_result.data else 0
            
            # Phase 3: Evaluate for tool creation
            tools_created = 0
            eval_result = await scout.evaluate_for_tool_creation(context)
            total_tokens += eval_result.tokens_used or 0
            total_cost += eval_result.cost_usd or 0
            tools_created = eval_result.data.get("tools_created", 0) if eval_result.data else 0
            
            # Phase 4: Run maintenance
            await scout.run_maintenance(context)
            
            # Phase 5: Learning - reflect and evolve strategies
            logger.info("Running Tool Scout learning phase...")
            strategies_evolved = 0
            try:
                learn_result = await scout.reflect_and_learn(context)
                if learn_result and learn_result.data:
                    strategies_evolved = learn_result.data.get("strategies_evolved", 0)
                    logger.info(f"Tool Scout learning complete: {strategies_evolved} strategies evolved")
                    total_tokens += learn_result.tokens_used or 0
                    total_cost += learn_result.cost_usd or 0
            except Exception as learn_error:
                logger.warning(f"Tool Scout learning phase failed (non-fatal): {learn_error}")
            
            model_used = "glm-4-flash"  # Tool Scout uses quality tier
            
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=ideas_processed + total_entries,
                items_created=tools_created,
                tokens_used=total_tokens,
                cost_usd=total_cost,
                model_used=model_used,
                metadata={
                    "ideas_processed": ideas_processed,
                    "knowledge_entries_added": total_entries,
                    "tools_created": tools_created,
                    "strategies_evolved": strategies_evolved,
                },
            )
            
            logger.info(f"Tool Scout completed: {ideas_processed} ideas processed, {total_entries} entries added, {tools_created} tools created, {strategies_evolved} strategies evolved")
            
            return {
                "success": True,
                "run_id": str(run.id),
                "ideas_processed": ideas_processed,
                "entries_added": total_entries,
                "tools_created": tools_created,
                "tokens_used": total_tokens,
                "cost_usd": total_cost,
            }
            
        except Exception as e:
            logger.exception(f"Tool Scout failed: {e}")
            await agent_scheduler_service.fail_run(
                db,
                run.id,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            return {"error": str(e), "run_id": str(run.id)}


@celery_app.task(bind=True, name="app.tasks.agent_tasks.process_tool_ideas")
def process_tool_ideas(self, force: bool = False):
    """
    Process tool ideas from the queue.
    
    This task runs more frequently (default: every 30 minutes) to pick up
    tool ideas from the ideas queue and store them in the tool ideas resource.
    
    Args:
        force: If True, run even if not due
    """
    return run_async(_process_tool_ideas_async(force))


async def _process_tool_ideas_async(force: bool = False):
    """Async implementation of tool idea processing."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="tool_scout")
        if not agent:
            logger.error("Tool Scout agent not found in database")
            return {"error": "Agent not found"}
        
        if not agent.is_enabled and not force:
            logger.info("Tool Scout is disabled, skipping idea processing")
            return {"skipped": True, "reason": "Agent disabled"}
        
        # Check for tool ideas in queue
        from app.models import UserIdea, IdeaStatus
        from sqlalchemy import select, func
        result = await db.execute(
            select(func.count(UserIdea.id)).where(UserIdea.status == IdeaStatus.TOOL.value)
        )
        tool_count = result.scalar() or 0
        
        if tool_count == 0:
            logger.debug("No tool ideas to process")
            return {"skipped": True, "reason": "No tool ideas"}
        
        run = await agent_scheduler_service.create_run(
            db,
            slug="tool_scout",
            trigger_type="scheduled" if not force else "manual",
            trigger_reason=f"Tool idea processing - {tool_count} ideas",
        )
        
        if not run:
            return {"error": "Could not create run"}
        
        await agent_scheduler_service.start_run(db, run.id)
        
        try:
            from app.agents.tool_scout import ToolScoutAgent
            from app.agents.base import AgentContext
            
            scout = ToolScoutAgent()
            context = AgentContext(db=db)
            
            logger.info(f"Processing {tool_count} tool ideas")
            result = await scout.process_tool_ideas(context, limit=15)
            
            tokens = result.tokens_used or 0
            cost = result.cost_usd or 0
            processed = result.data.get("processed", 0) if result.data else 0
            
            await agent_scheduler_service.complete_run(
                db,
                run.id,
                items_processed=processed,
                items_created=processed,
                tokens_used=tokens,
                cost_usd=cost,
                metadata=result.data,
            )
            
            logger.info(f"Tool idea processing completed: {processed} processed")
            
            return {
                "success": True,
                "run_id": str(run.id),
                "processed": processed,
                "tokens_used": tokens,
                "cost_usd": cost,
            }
            
        except Exception as e:
            logger.exception(f"Tool idea processing failed: {e}")
            await agent_scheduler_service.fail_run(
                db,
                run.id,
                error_message=str(e),
                error_traceback=traceback.format_exc(),
            )
            return {"error": str(e), "run_id": str(run.id)}


# =============================================================================
# Health Check & Monitoring Tasks
# =============================================================================

@celery_app.task(name="app.tasks.agent_tasks.agent_scheduler_dispatcher")
def agent_scheduler_dispatcher():
    """
    Dynamic scheduler that dispatches agent tasks based on database configuration.
    
    This task runs frequently (every 30 seconds) and:
    1. Reads agent configurations from database
    2. Checks which agents are due to run (next_run_at <= now)
    3. Dispatches the appropriate task for each due agent
    4. Respects is_enabled flag and current status
    
    This replaces static Celery beat schedules with database-driven scheduling.
    """
    return run_async(_agent_scheduler_dispatcher_async())


async def _agent_scheduler_dispatcher_async():
    """Async implementation of the scheduler dispatcher."""
    async with get_db_context() as db:
        from sqlalchemy import select
        from app.models.agent_scheduler import AgentDefinition
        
        # Check if agents are globally enabled (admin has acknowledged disclaimer)
        from app.services.disclaimer_service import are_agents_enabled
        if not await are_agents_enabled(db):
            return {
                "checked_at": utc_now().isoformat(),
                "dispatched": [],
                "skipped": [],
                "reason": "agents_globally_disabled",
            }
        
        # Get all enabled agents that are due to run
        now = utc_now()
        result = await db.execute(
            select(AgentDefinition)
            .where(AgentDefinition.is_enabled == True)
            .where(AgentDefinition.status.in_([AgentStatus.IDLE, AgentStatus.ERROR]))
            .where(
                (AgentDefinition.next_run_at == None) | 
                (AgentDefinition.next_run_at <= now)
            )
        )
        due_agents = result.scalars().all()
        
        dispatched = []
        skipped = []
        
        for agent in due_agents:
            # Double-check budget
            budget_info = await agent_scheduler_service.check_budget(db, agent.slug)
            if budget_info.get("exceeded"):
                logger.info(f"Skipping {agent.slug}: budget exceeded")
                skipped.append({"slug": agent.slug, "reason": "budget_exceeded"})
                continue
            
            # Dispatch the appropriate task
            logger.info(f"Dispatching scheduled run for {agent.slug}")
            
            task_dispatched = False
            if agent.slug == "opportunity_scout":
                run_opportunity_scout.delay(force=False)
                task_dispatched = True
            elif agent.slug == "proposal_writer":
                # Proposal writer checks for pending events
                check_approved_opportunities.delay()
                task_dispatched = True
            elif agent.slug == "tool_scout":
                run_tool_scout.delay(force=False)
                task_dispatched = True
            elif agent.slug == "campaign_manager":
                run_campaign_manager.delay(force=False)
                task_dispatched = True
            else:
                logger.warning(f"Unknown agent type: {agent.slug}")
                skipped.append({"slug": agent.slug, "reason": "unknown_type"})
            
            if task_dispatched:
                dispatched.append(agent.slug)
                # Immediately advance next_run_at so the dispatcher won't
                # re-dispatch this agent on the next 30-second tick before
                # the Celery task has had a chance to start and set status
                # to RUNNING.  (Previously only proposal_writer did this,
                # causing opportunity_scout / tool_scout / campaign_manager
                # to be dispatched twice.)
                from datetime import timedelta
                agent.next_run_at = now + timedelta(seconds=agent.schedule_interval_seconds)
                agent.last_run_at = now
        
        # Single commit for all dispatched agents
        if dispatched:
            await db.commit()
        
        return {
            "checked_at": now.isoformat(),
            "dispatched": dispatched,
            "skipped": skipped,
        }


@celery_app.task(name="app.tasks.agent_tasks.agent_health_check")
def agent_health_check():
    """
    Periodic health check for all agents.
    
    This task runs every minute and:
    1. Checks for stuck agents (running too long)
    2. Resets budgets when periods expire
    3. Updates next_run_at if needed
    """
    return run_async(_agent_health_check_async())


async def _agent_health_check_async():
    """Async implementation of agent health check."""
    from app.models import AgentRun
    from sqlalchemy import func, select
    
    async with get_db_context() as db:
        agents = await agent_scheduler_service.get_all_agents(db, include_disabled=True)
        
        results = {
            "checked": len(agents),
            "budget_resets": 0,
            "stuck_detected": 0,
            "stuck_recovered": 0,
            "cost_syncs": 0,
        }
        
        for agent in agents:
            # Check for stuck agents (running for more than 30 minutes)
            if agent.status == AgentStatus.RUNNING:
                if agent.last_run_at:
                    last_run = ensure_utc(agent.last_run_at)
                    running_time = (utc_now() - last_run).total_seconds()
                    if running_time > 1800:  # 30 minutes
                        logger.warning(f"Agent {agent.slug} appears stuck (running for {running_time/60:.1f} minutes) - recovering")
                        results["stuck_detected"] += 1
                        # Auto-recover stuck agent
                        agent.status = AgentStatus.IDLE
                        await db.commit()
                        results["stuck_recovered"] += 1
                        logger.info(f"Agent {agent.slug} recovered from stuck state")
            
            # Check/reset budgets
            budget_info = await agent_scheduler_service.check_budget(db, agent.slug)
            if budget_info.get("was_reset"):
                results["budget_resets"] += 1
            
            # Sync total_cost_usd with actual runs (safety net for any drift)
            cost_result = await db.execute(
                select(func.coalesce(func.sum(AgentRun.cost_usd), 0))
                .where(AgentRun.agent_id == agent.id)
            )
            actual_cost = float(cost_result.scalar() or 0)
            
            # Only sync if there's a significant drift (> $0.01)
            if abs(actual_cost - agent.total_cost_usd) > 0.01:
                logger.warning(
                    f"Agent {agent.slug} cost drift detected: "
                    f"recorded=${agent.total_cost_usd:.4f}, actual=${actual_cost:.4f}, "
                    f"diff=${actual_cost - agent.total_cost_usd:.4f}"
                )
                agent.total_cost_usd = actual_cost
                await db.commit()
                results["cost_syncs"] += 1
        
        return results


@celery_app.task(name="app.tasks.agent_tasks.trigger_agent_manually")
def trigger_agent_manually(agent_slug: str, reason: Optional[str] = None):
    """
    Manually trigger an agent to run immediately.
    
    Args:
        agent_slug: The agent to trigger (opportunity_scout, proposal_writer, etc.)
        reason: Optional reason for the manual trigger
    """
    if agent_slug == "opportunity_scout":
        return run_opportunity_scout.delay(force=True)
    elif agent_slug == "tool_scout":
        return run_tool_scout.delay(force=True)
    elif agent_slug == "campaign_manager":
        return run_campaign_manager.delay(force=True)
    else:
        return {"error": f"Unknown agent: {agent_slug}"}


# =============================================================================
# System Recovery Tasks
# =============================================================================

@celery_app.task(name="app.tasks.agent_tasks.system_startup_recovery")
def system_startup_recovery():
    """
    Recover system state after restart or crash.
    
    This task should be triggered on application startup.
    It cleans up:
    1. Stale job queue entries (running/queued jobs that died)
    2. Stuck agents (in RUNNING status but not actually running)
    3. Orphaned resources (in_use but no active job)
    4. Stale tool executions (stuck in PENDING/RUNNING)
    """
    return run_async(_system_startup_recovery_async())


async def _system_startup_recovery_async():
    """Async implementation of system startup recovery."""
    from sqlalchemy import select, update
    from app.services import job_queue_service
    from app.services import campaign_lease_service
    from app.services import campaign_worker_service
    from app.models import ToolExecution, ToolExecutionStatus
    from app.models.resource import RemoteAgent, RemoteAgentStatus, Resource
    
    results = {
        "job_queue_recovery": {},
        "agent_recovery": {},
        "agent_run_recovery": {},
        "tool_execution_recovery": {},
        "campaign_lease_recovery": {},
        "campaign_worker_recovery": {},
        "remote_agent_recovery": {},
        "timestamp": utc_now().isoformat(),
    }
    
    logger.info("=" * 60)
    logger.info("SYSTEM STARTUP RECOVERY - Beginning recovery process")
    logger.info("=" * 60)
    
    async with get_db_context() as db:
        # 1. Recover job queue (stale running/queued jobs, orphaned resources)
        job_recovery = await job_queue_service.recover_stale_jobs(
            db, default_threshold_minutes=30
        )
        results["job_queue_recovery"] = job_recovery
        
        # 2. Recover stuck agents
        agents = await agent_scheduler_service.get_all_agents(db, include_disabled=True)
        agents_recovered = 0
        for agent in agents:
            if agent.status == AgentStatus.RUNNING:
                # Any agent in RUNNING status after restart is stuck
                logger.warning(f"Recovery: Agent {agent.slug} was stuck in RUNNING status")
                agent.status = AgentStatus.IDLE
                agents_recovered += 1
        
        if agents_recovered > 0:
            await db.commit()
            logger.info(f"Recovery: Reset {agents_recovered} stuck agents to IDLE")
        
        results["agent_recovery"] = {"agents_recovered": agents_recovered}
        
        # 2b. Recover stale agent_runs (stuck in PENDING or RUNNING)
        # These can accumulate if agents are killed mid-run and not properly cleaned up
        from app.models.agent_scheduler import AgentRun, AgentRunStatus
        from datetime import timedelta
        
        # Use a generous threshold - runs shouldn't be "running" for more than 4 hours
        run_threshold_time = utc_now() - timedelta(hours=4)
        
        stale_runs_result = await db.execute(
            select(AgentRun)
            .where(AgentRun.status.in_([AgentRunStatus.PENDING, AgentRunStatus.RUNNING]))
            .where(AgentRun.created_at < run_threshold_time)
        )
        stale_runs = list(stale_runs_result.scalars().all())
        
        runs_recovered = 0
        for run in stale_runs:
            old_status = run.status
            run.status = AgentRunStatus.FAILED
            run.error_message = f"SYSTEM_RECOVERY: Agent run was interrupted (was {old_status.value}, created {run.created_at})"
            run.completed_at = utc_now()
            runs_recovered += 1
            logger.warning(f"Recovery: Failed stale agent run {run.id} (was {old_status.value})")
        
        if runs_recovered > 0:
            await db.commit()
            logger.info(f"Recovery: Failed {runs_recovered} stale agent runs")
        
        results["agent_run_recovery"] = {"runs_recovered": runs_recovered}
        
        # 3. Recover stuck tool executions
        # Find executions stuck in PENDING or RUNNING
        from datetime import timedelta
        
        threshold_time = utc_now() - timedelta(minutes=30)
        
        stale_executions_result = await db.execute(
            select(ToolExecution)
            .where(ToolExecution.status.in_([
                ToolExecutionStatus.PENDING,
                ToolExecutionStatus.RUNNING
            ]))
            .where(ToolExecution.created_at < threshold_time)
        )
        stale_executions = list(stale_executions_result.scalars().all())
        
        executions_recovered = 0
        for execution in stale_executions:
            execution.status = ToolExecutionStatus.FAILED
            execution.error_message = f"SYSTEM_RECOVERY: Execution was interrupted by system restart (created {execution.created_at})"
            execution.completed_at = utc_now()
            executions_recovered += 1
            logger.warning(f"Recovery: Failed stale tool execution {execution.id}")
        
        if executions_recovered > 0:
            await db.commit()
            logger.info(f"Recovery: Failed {executions_recovered} stale tool executions")
        
        results["tool_execution_recovery"] = {"executions_recovered": executions_recovered}
        
        # 4. Recover expired campaign leases
        # After restart, any lease might be stale (worker process died)
        leases_released = await campaign_lease_service.force_release_expired_leases(
            db, set_failover_status=True
        )
        logger.info(f"Recovery: Released {leases_released} expired campaign leases")
        results["campaign_lease_recovery"] = {"leases_released": leases_released}
        
        # 5. Detect and mark offline campaign workers
        # Any worker that was "online" before restart is probably offline now
        # (they need to reconnect and heartbeat)
        offline_workers = await campaign_worker_service.detect_offline_workers(db)
        logger.info(f"Recovery: Marked {len(offline_workers)} campaign workers as offline")
        results["campaign_worker_recovery"] = {
            "workers_marked_offline": len(offline_workers),
            "worker_ids": [w.worker_id for w in offline_workers]
        }
        
        # 6. Register local worker (backend as campaign worker)
        try:
            local_worker = await campaign_worker_service.register_local_worker(db)
            logger.info(f"Recovery: Registered local campaign worker: {local_worker.worker_id}")
            results["campaign_worker_recovery"]["local_worker_registered"] = local_worker.worker_id
        except Exception as e:
            logger.error(f"Recovery: Failed to register local campaign worker: {e}")
            results["campaign_worker_recovery"]["local_worker_error"] = str(e)
        
        # 7. Disable resources belonging to offline remote agents
        # After restart, all remote agents are offline (they need to reconnect)
        # Mark their resources as disabled so they don't show as available
        
        # First, mark all remote agents as offline (they need to reconnect)
        offline_agents_result = await db.execute(
            select(RemoteAgent).where(
                RemoteAgent.status != RemoteAgentStatus.OFFLINE.value
            )
        )
        offline_agents = list(offline_agents_result.scalars().all())
        
        agents_marked_offline = 0
        for agent in offline_agents:
            agent.status = RemoteAgentStatus.OFFLINE.value
            agents_marked_offline += 1
        
        if agents_marked_offline > 0:
            await db.commit()
            logger.info(f"Recovery: Marked {agents_marked_offline} remote agents as offline")
        
        # Now disable all resources that belong to remote agents (they have remote_agent_id set)
        disabled_resources_result = await db.execute(
            update(Resource)
            .where(Resource.remote_agent_id.isnot(None))
            .where(Resource.status == "available")
            .values(status="disabled")
        )
        resources_disabled = disabled_resources_result.rowcount
        await db.commit()
        
        logger.info(f"Recovery: Disabled {resources_disabled} resources belonging to offline remote agents")
        
        results["remote_agent_recovery"] = {
            "agents_marked_offline": agents_marked_offline,
            "resources_disabled": resources_disabled
        }
    
    logger.info("=" * 60)
    logger.info(f"SYSTEM STARTUP RECOVERY - Complete: {results}")
    logger.info("=" * 60)
    
    return results


@celery_app.task(name="app.tasks.agent_tasks.get_system_health")
def get_system_health():
    """
    Get current system health status.
    
    Returns information about job queues, resources, and any anomalies.
    """
    return run_async(_get_system_health_async())


async def _get_system_health_async():
    """Async implementation of system health check."""
    from app.services import job_queue_service
    
    async with get_db_context() as db:
        health = await job_queue_service.get_system_health_status(db)
        
        # Add agent status
        agents = await agent_scheduler_service.get_all_agents(db, include_disabled=True)
        agent_status = {}
        for agent in agents:
            agent_status[agent.slug] = {
                "status": agent.status.value if agent.status else "unknown",
                "is_enabled": agent.is_enabled,
                "last_run": agent.last_run_at.isoformat() if agent.last_run_at else None,
            }
        
        health["agents"] = agent_status
        health["timestamp"] = utc_now().isoformat()
        
        return health


# =============================================================================
# Campaign Manager Tasks (Lease-Based Worker Loop)
# =============================================================================

@celery_app.task(bind=True, name="app.tasks.agent_tasks.run_campaign_manager")
def run_campaign_manager(self, force: bool = False):
    """
    Run the Campaign Manager via the lease-based worker loop.
    
    This task is scheduled to run periodically (default: every 5 minutes).
    It claims available campaigns and processes them using the lease system.
    
    The lease system ensures:
    - Only one worker processes each campaign at a time
    - Campaigns are automatically recovered if a worker fails
    - Horizontal scaling by running multiple workers
    
    Args:
        force: If True, run even if not due (for manual triggers)
    """
    return run_async(_run_campaign_manager_async(force))


async def _run_campaign_manager_async(force: bool = False):
    """Async implementation of Campaign Manager run using lease-based worker loop."""
    async with get_db_context() as db:
        agent = await agent_scheduler_service.get_agent(db, slug="campaign_manager")
        if not agent:
            logger.error("Campaign Manager agent not found in database")
            return {"error": "Agent not found"}
        
        if not agent.is_enabled and not force:
            logger.info("Campaign Manager is disabled, skipping")
            return {"skipped": True, "reason": "Agent disabled"}
        
        # Stacking prevention: skip if already running (unless forced)
        if agent.status == AgentStatus.RUNNING and not force:
            logger.info("Campaign Manager is already running, skipping")
            return {"skipped": True, "reason": "Already running"}
        
        # If agent was in ERROR state from a previous run, clear it so it can
        # resume normal operation (the underlying issue was transient)
        if agent.status == AgentStatus.ERROR and not force:
            logger.info(
                f"Campaign Manager recovering from error state: {agent.status_message}"
            )
            agent.status = AgentStatus.IDLE
            agent.status_message = None
            await db.commit()
        
        if not force and agent.next_run_at and utc_now() < ensure_utc(agent.next_run_at):
            logger.debug(f"Campaign Manager not due until {agent.next_run_at}")
            return {"skipped": True, "reason": "Not due yet"}
        
        # Mark as RUNNING and set next_run_at BEFORE doing work to prevent
        # the dispatcher from re-dispatching while we're still processing
        agent.status = AgentStatus.RUNNING
        agent.status_message = None  # Clear any previous error message
        agent.next_run_at = utc_now() + timedelta(seconds=agent.schedule_interval_seconds)
        await db.commit()
        
        try:
            # Run the lease-based worker loop
            from app.services.campaign_worker import run_campaign_worker_iteration
            
            loop_result = await run_campaign_worker_iteration(db)
            
            # If we did any work, create a run record for tracking
            campaigns_processed = len(loop_result.get("processed", []))
            campaigns_claimed = len(loop_result.get("claimed", []))
            
            # Update timestamps after completion
            agent.last_run_at = utc_now()
            agent.status = AgentStatus.IDLE
            agent.status_message = None  # Clear any previous error message
            await db.commit()
            
            if campaigns_processed > 0 or campaigns_claimed > 0:
                # Create and complete a run record
                run = await agent_scheduler_service.create_run(
                    db,
                    slug="campaign_manager",
                    trigger_type="scheduled" if not force else "manual",
                    trigger_reason=f"Worker loop: {campaigns_claimed} claimed, {campaigns_processed} processed",
                    force=force,
                )
                
                if run:
                    await agent_scheduler_service.start_run(db, run.id)
                    
                    # Sum up tokens from processed campaigns
                    total_tokens = sum(
                        p.get("tokens_used", 0) or 0 
                        for p in loop_result.get("processed", [])
                    )
                    
                    await agent_scheduler_service.complete_run(
                        db,
                        run.id,
                        items_processed=campaigns_processed,
                        items_created=campaigns_claimed,
                        tokens_used=total_tokens,
                        cost_usd=0.0,  # Cost tracking handled separately
                        metadata=loop_result,
                    )
            
            return {
                "success": True,
                "worker_id": loop_result.get("worker_id"),
                "campaigns_claimed": campaigns_claimed,
                "campaigns_processed": campaigns_processed,
                "campaigns_held": len(loop_result.get("processed", [])) + len(loop_result.get("errors", [])),
                "errors": loop_result.get("errors", []),
            }
        except Exception as e:
            # Reset status on failure so the agent can be dispatched again
            logger.error(f"Campaign Manager failed: {e}", exc_info=True)
            agent.status = AgentStatus.ERROR
            agent.status_message = f"Run failed: {str(e)[:200]}"
            await db.commit()
            raise


@celery_app.task(bind=True, name="app.tasks.agent_tasks.campaign_worker_heartbeat")
def campaign_worker_heartbeat(self):
    """
    Send heartbeat to renew campaign leases for this worker.
    
    This task should be scheduled to run more frequently than the lease TTL
    (e.g., every 60 seconds with a 5-minute TTL).
    
    The heartbeat:
    - Renews all campaign leases held by this worker
    - Updates the worker's last_heartbeat_at timestamp
    - Releases campaigns whose leases couldn't be renewed
    """
    return run_async(_campaign_worker_heartbeat_async())


async def _campaign_worker_heartbeat_async():
    """Async implementation of campaign worker heartbeat."""
    async with get_db_context() as db:
        from app.services.campaign_worker import get_worker_instance
        
        worker = get_worker_instance()
        
        # Only send heartbeat if worker has campaigns
        if worker.current_campaign_count == 0:
            return {"skipped": True, "reason": "No campaigns held"}
        
        renewed = await worker.send_heartbeat(db)
        
        return {
            "success": True,
            "worker_id": worker.worker_id,
            "leases_renewed": renewed,
            "campaigns_held": worker.current_campaign_count,
        }


@celery_app.task(bind=True, name="app.tasks.agent_tasks.release_expired_campaign_leases")
def release_expired_campaign_leases(self):
    """
    Release campaign leases that have expired past the grace period.
    
    This is a cleanup task that should run periodically to handle cases
    where workers died without gracefully releasing their leases.
    
    Campaigns with expired leases are set to PAUSED_FAILOVER status
    and become claimable by other workers.
    """
    return run_async(_release_expired_campaign_leases_async())


async def _release_expired_campaign_leases_async():
    """Async implementation of expired lease release."""
    async with get_db_context() as db:
        from app.services.campaign_lease_service import force_release_expired_leases
        
        released = await force_release_expired_leases(db)
        
        if released:
            logger.info(f"Released {released} expired campaign leases")
        
        return {
            "success": True,
            "released_count": released,
        }
@celery_app.task(bind=True, name="app.tasks.agent_tasks.execute_campaign_step")
def execute_campaign_step(self, campaign_id: str):
    """
    Execute a single step for a specific campaign.
    
    This can be triggered when a campaign needs immediate attention,
    such as after user provides required input.
    
    Note: This bypasses the lease system and should only be used for
    campaigns that are already leased by this worker, or for immediate
    user-triggered actions.
    
    Args:
        campaign_id: UUID of the campaign to process
    """
    return run_async(_execute_campaign_step_async(campaign_id))


async def _execute_campaign_step_async(campaign_id: str):
    """Async implementation of single campaign step."""
    from uuid import UUID as PyUUID
    
    async with get_db_context() as db:
        from app.models import Campaign
        from sqlalchemy import select
        
        result = await db.execute(
            select(Campaign).where(Campaign.id == PyUUID(campaign_id))
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return {"error": f"Campaign {campaign_id} not found"}
        
        from app.agents.campaign_manager import CampaignManagerAgent
        from app.agents.base import AgentContext
        
        manager = CampaignManagerAgent()
        context = AgentContext(db=db, user_id=campaign.user_id)
        
        result = await manager.execute_campaign_step(
            context=context,
            campaign_id=PyUUID(campaign_id),
        )
        
        await db.commit()
        
        return {
            "success": result.success,
            "message": result.message,
            "data": result.data,
            "tokens_used": result.tokens_used,
        }


@celery_app.task(bind=True, name="app.tasks.agent_tasks.initialize_campaign_from_proposal")
def initialize_campaign_from_proposal(self, proposal_id: str, user_id: str):
    """
    Initialize a new campaign from an approved proposal.
    
    This is triggered when a proposal is approved and ready for execution.
    
    Args:
        proposal_id: UUID of the approved proposal
        user_id: UUID of the user who owns the proposal
    """
    return run_async(_initialize_campaign_from_proposal_async(proposal_id, user_id))


async def _initialize_campaign_from_proposal_async(proposal_id: str, user_id: str):
    """Async implementation of campaign initialization."""
    from uuid import UUID as PyUUID
    
    async with get_db_context() as db:
        from app.agents.campaign_manager import CampaignManagerAgent
        from app.agents.base import AgentContext
        
        manager = CampaignManagerAgent()
        context = AgentContext(db=db, user_id=PyUUID(user_id))
        
        result = await manager.initialize_campaign(
            context=context,
            proposal_id=PyUUID(proposal_id),
            user_id=PyUUID(user_id),
        )
        
        await db.commit()
        
        return {
            "success": result.success,
            "message": result.message,
            "data": result.data,
        }
