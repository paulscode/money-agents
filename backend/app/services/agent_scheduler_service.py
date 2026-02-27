"""Agent scheduler service for managing agent execution and budgets."""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.datetime_utils import utc_now, ensure_utc
from app.services.usage_service import calculate_cost  # noqa: E402 – single source of truth
from app.models.agent_scheduler import (
    AgentDefinition,
    AgentRun,
    AgentEvent,
    AgentStatus,
    AgentRunStatus,
    BudgetPeriod,
)

logger = logging.getLogger(__name__)


class AgentSchedulerService:
    """Service for managing agent execution and scheduling."""
    
    # =========================================================================
    # Agent Definition CRUD
    # =========================================================================
    
    async def get_agent(
        self,
        db: AsyncSession,
        agent_id: Optional[UUID] = None,
        slug: Optional[str] = None,
    ) -> Optional[AgentDefinition]:
        """Get an agent by ID or slug."""
        if agent_id:
            return await db.get(AgentDefinition, agent_id)
        elif slug:
            result = await db.execute(
                select(AgentDefinition).where(AgentDefinition.slug == slug)
            )
            return result.scalar_one_or_none()
        return None
    
    async def get_all_agents(
        self,
        db: AsyncSession,
        include_disabled: bool = False,
    ) -> List[AgentDefinition]:
        """Get all agent definitions."""
        query = select(AgentDefinition)
        if not include_disabled:
            query = query.where(AgentDefinition.is_enabled == True)
        query = query.order_by(AgentDefinition.name)
        
        result = await db.execute(query)
        return list(result.scalars().all())
    
    async def update_agent(
        self,
        db: AsyncSession,
        agent_id: UUID,
        **kwargs,
    ) -> Optional[AgentDefinition]:
        """Update an agent's configuration."""
        agent = await self.get_agent(db, agent_id=agent_id)
        if not agent:
            return None
        
        # Track if schedule changed
        schedule_changed = 'schedule_interval_seconds' in kwargs and \
            kwargs['schedule_interval_seconds'] != agent.schedule_interval_seconds
        
        for key, value in kwargs.items():
            if hasattr(agent, key):
                setattr(agent, key, value)
        
        # If schedule interval changed, recalculate next_run_at
        if schedule_changed and agent.is_enabled:
            # Calculate from last run, or from now if never ran
            base_time = ensure_utc(agent.last_run_at) or utc_now()
            agent.next_run_at = base_time + timedelta(seconds=agent.schedule_interval_seconds)
            # If next_run_at is in the past, set it to run soon
            if agent.next_run_at < utc_now():
                agent.next_run_at = utc_now() + timedelta(seconds=30)
        
        # If agent is being enabled and has no next_run_at, schedule it
        if 'is_enabled' in kwargs and kwargs['is_enabled'] and not agent.next_run_at:
            agent.next_run_at = utc_now() + timedelta(seconds=30)
        
        await db.commit()
        await db.refresh(agent)
        return agent
    
    # =========================================================================
    # Agent Status Management
    # =========================================================================
    
    async def pause_agent(
        self,
        db: AsyncSession,
        slug: str,
        reason: Optional[str] = None,
    ) -> Optional[AgentDefinition]:
        """Pause an agent."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return None
        
        agent.status = AgentStatus.PAUSED
        agent.status_message = reason or "Paused by user"
        agent.is_enabled = False
        
        await db.commit()
        await db.refresh(agent)
        
        logger.info(f"Paused agent {slug}: {reason}")
        return agent
    
    async def resume_agent(
        self,
        db: AsyncSession,
        slug: str,
    ) -> Optional[AgentDefinition]:
        """Resume a paused agent."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return None
        
        agent.status = AgentStatus.IDLE
        agent.status_message = None
        agent.is_enabled = True
        
        # Calculate next run time
        agent.next_run_at = utc_now() + timedelta(seconds=agent.schedule_interval_seconds)
        
        await db.commit()
        await db.refresh(agent)
        
        logger.info(f"Resumed agent {slug}")
        return agent
    
    async def set_agent_status(
        self,
        db: AsyncSession,
        slug: str,
        status: AgentStatus,
        message: Optional[str] = None,
    ) -> Optional[AgentDefinition]:
        """Set an agent's status."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return None
        
        agent.status = status
        agent.status_message = message
        
        await db.commit()
        await db.refresh(agent)
        return agent
    
    # =========================================================================
    # Budget Management
    # =========================================================================
    
    async def check_budget(
        self,
        db: AsyncSession,
        slug: str,
    ) -> Dict[str, Any]:
        """Check an agent's budget status."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return {"error": "Agent not found"}
        
        # Check if budget needs reset
        await self._maybe_reset_budget(db, agent)
        
        budget_info = {
            "has_limit": agent.budget_limit is not None,
            "limit": agent.budget_limit,
            "used": agent.budget_used,
            "period": agent.budget_period.value,
            "reset_at": agent.budget_reset_at.isoformat() if agent.budget_reset_at else None,
            "remaining": (agent.budget_limit - agent.budget_used) if agent.budget_limit else None,
            "percentage_used": (agent.budget_used / agent.budget_limit * 100) if agent.budget_limit else 0,
            "warning_threshold": agent.budget_warning_threshold,
            "is_warning": False,
            "is_exceeded": False,
        }
        
        if agent.budget_limit:
            budget_info["is_warning"] = budget_info["percentage_used"] >= (agent.budget_warning_threshold * 100)
            budget_info["is_exceeded"] = agent.budget_used >= agent.budget_limit
        
        return budget_info
    
    async def update_budget(
        self,
        db: AsyncSession,
        slug: str,
        limit: Optional[float] = None,
        period: Optional[BudgetPeriod] = None,
        warning_threshold: Optional[float] = None,
    ) -> Optional[AgentDefinition]:
        """Update an agent's budget settings."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return None
        
        if limit is not None:
            agent.budget_limit = limit
        if period is not None:
            agent.budget_period = period
            # Reset budget when period changes
            agent.budget_used = 0.0
            agent.budget_reset_at = self._get_next_reset_time(period)
        if warning_threshold is not None:
            agent.budget_warning_threshold = warning_threshold
        
        await db.commit()
        await db.refresh(agent)
        return agent
    
    async def record_cost(
        self,
        db: AsyncSession,
        slug: str,
        cost: float,
        tokens: int,
    ) -> Dict[str, Any]:
        """Record cost for an agent run and check budget."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return {"error": "Agent not found"}
        
        # Check if budget needs reset
        await self._maybe_reset_budget(db, agent)
        
        # Update usage
        agent.budget_used += cost
        agent.total_cost_usd += cost
        agent.total_tokens_used += tokens
        
        result = {
            "cost_recorded": cost,
            "tokens_recorded": tokens,
            "total_used": agent.budget_used,
            "budget_exceeded": False,
            "budget_warning": False,
        }
        
        # Check budget limits
        if agent.budget_limit:
            result["budget_warning"] = agent.budget_used >= (agent.budget_limit * agent.budget_warning_threshold)
            result["budget_exceeded"] = agent.budget_used >= agent.budget_limit
            
            if result["budget_exceeded"]:
                agent.status = AgentStatus.BUDGET_EXCEEDED
                agent.status_message = f"Budget limit of ${agent.budget_limit:.2f}/{agent.budget_period.value} exceeded"
                agent.is_enabled = False
                logger.warning(f"Agent {slug} budget exceeded: ${agent.budget_used:.4f} / ${agent.budget_limit:.2f}")
            elif result["budget_warning"]:
                logger.warning(f"Agent {slug} approaching budget limit: ${agent.budget_used:.4f} / ${agent.budget_limit:.2f}")
        
        await db.commit()
        await db.refresh(agent)
        
        return result
    
    async def _maybe_reset_budget(self, db: AsyncSession, agent: AgentDefinition) -> bool:
        """Reset budget if period has elapsed."""
        if not agent.budget_reset_at:
            agent.budget_reset_at = self._get_next_reset_time(agent.budget_period)
            await db.commit()
            return False
        
        budget_reset = ensure_utc(agent.budget_reset_at)
        if utc_now() >= budget_reset:
            old_used = agent.budget_used
            agent.budget_used = 0.0
            agent.budget_reset_at = self._get_next_reset_time(agent.budget_period)
            
            # If agent was paused due to budget, resume it
            if agent.status == AgentStatus.BUDGET_EXCEEDED:
                agent.status = AgentStatus.IDLE
                agent.status_message = None
                agent.is_enabled = True
            
            await db.commit()
            logger.info(f"Reset budget for agent {agent.slug}: ${old_used:.4f} → $0.00")
            return True
        
        return False
    
    def _get_next_reset_time(self, period: BudgetPeriod) -> datetime:
        """Calculate the next budget reset time."""
        now = utc_now()
        
        if period == BudgetPeriod.HOURLY:
            return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        elif period == BudgetPeriod.DAILY:
            return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        elif period == BudgetPeriod.WEEKLY:
            days_until_monday = (7 - now.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7
            return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=days_until_monday)
        elif period == BudgetPeriod.MONTHLY:
            if now.month == 12:
                return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        
        return now + timedelta(days=1)
    
    # =========================================================================
    # Agent Run Management
    # =========================================================================
    
    async def create_run(
        self,
        db: AsyncSession,
        slug: str,
        trigger_type: str = "scheduled",
        trigger_reason: Optional[str] = None,
        campaign_id: Optional[UUID] = None,
        force: bool = False,
    ) -> Optional[AgentRun]:
        """Create a new agent run record."""
        agent = await self.get_agent(db, slug=slug)
        if not agent:
            return None
        
        # Check if agent can run
        if not agent.is_enabled and not force:
            logger.warning(f"Cannot create run for disabled agent {slug}")
            return None
        
        if agent.status == AgentStatus.RUNNING and not force:
            logger.warning(f"Agent {slug} is already running")
            return None
        
        # Check budget before starting
        budget_info = await self.check_budget(db, slug)
        if budget_info.get("is_exceeded"):
            logger.warning(f"Cannot run agent {slug}: budget exceeded")
            return None
        
        run = AgentRun(
            agent_id=agent.id,
            status=AgentRunStatus.PENDING,
            trigger_type=trigger_type,
            trigger_reason=trigger_reason,
            campaign_id=campaign_id,
        )
        
        db.add(run)
        await db.commit()
        await db.refresh(run)
        
        return run
    
    async def start_run(
        self,
        db: AsyncSession,
        run_id: UUID,
    ) -> Optional[AgentRun]:
        """
        Mark a run as started.
        
        Includes stacking prevention: checks if agent already has running runs.
        """
        run = await db.get(AgentRun, run_id)
        if not run:
            return None
        
        # STACKING PREVENTION: Check if this agent already has running runs
        existing_running = await db.execute(
            select(AgentRun)
            .where(AgentRun.agent_id == run.agent_id)
            .where(AgentRun.status == AgentRunStatus.RUNNING)
            .where(AgentRun.id != run_id)  # Exclude this run
        )
        existing_runs = list(existing_running.scalars().all())
        
        if existing_runs:
            # Reject the new run to prevent duplicate execution
            agent = await db.get(AgentDefinition, run.agent_id)
            agent_name = agent.slug if agent else str(run.agent_id)
            logger.warning(
                f"STACKING PREVENTED: Rejecting run {run_id} for agent '{agent_name}' "
                f"because {len(existing_runs)} other run(s) are already RUNNING: "
                f"{[str(r.id) for r in existing_runs]}"
            )
            run.status = AgentRunStatus.COMPLETED
            run.completed_at = utc_now()
            run.run_metadata = {"skipped": True, "reason": "stacking_prevented"}
            await db.commit()
            await db.refresh(run)
            return None
        
        run.status = AgentRunStatus.RUNNING
        run.started_at = utc_now()
        
        # Update agent status
        agent = await db.get(AgentDefinition, run.agent_id)
        if agent:
            agent.status = AgentStatus.RUNNING
            agent.last_run_at = utc_now()
        
        await db.commit()
        await db.refresh(run)
        
        return run
    
    async def complete_run(
        self,
        db: AsyncSession,
        run_id: UUID,
        items_processed: int = 0,
        items_created: int = 0,
        items_updated: int = 0,
        tokens_used: int = 0,
        cost_usd: float = 0.0,
        model_used: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentRun]:
        """Mark a run as completed successfully."""
        run = await db.get(AgentRun, run_id)
        if not run:
            return None
        
        run.status = AgentRunStatus.COMPLETED
        run.completed_at = utc_now()
        started = ensure_utc(run.started_at)
        run.duration_seconds = (run.completed_at - started).total_seconds() if started else 0
        run.items_processed = items_processed
        run.items_created = items_created
        run.items_updated = items_updated
        run.tokens_used = tokens_used
        run.cost_usd = cost_usd
        run.model_used = model_used
        run.run_metadata = metadata
        
        # -----------------------------------------------------------------
        # Track to llm_usage (single source of truth for cost reporting)
        # -----------------------------------------------------------------
        if tokens_used > 0 and model_used:
            from app.services.llm_usage_service import llm_usage_service, LLMUsageSource
            
            # Estimate prompt/completion split (agents are mostly output)
            prompt_tokens = int(tokens_used * 0.3)
            completion_tokens = tokens_used - prompt_tokens
            
            # Determine provider from model name
            provider = "unknown"
            model_lower = model_used.lower()
            if "claude" in model_lower:
                provider = "anthropic"
            elif "gpt" in model_lower or "o1" in model_lower:
                provider = "openai"
            elif "glm" in model_lower:
                provider = "zhipu"
            elif ":" in model_lower:
                provider = "ollama"
            
            try:
                    await llm_usage_service.track(
                    db=db,
                    source=LLMUsageSource.AGENT_TASK,
                    provider=provider,
                    model=model_used,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    agent_run_id=run_id,
                    campaign_id=run.campaign_id,
                    cost_usd=cost_usd if cost_usd > 0 else None,
                    latency_ms=int(run.duration_seconds * 1000) if run.duration_seconds else None,
                    meta_data={"agent_id": str(run.agent_id)},
                )
            except Exception as e:
                logger.warning(f"Failed to track LLM usage for run {run_id}: {e}")
        
        # Update agent
        agent = await db.get(AgentDefinition, run.agent_id)
        if agent:
            agent.status = AgentStatus.IDLE
            agent.status_message = None  # Clear any previous error message
            agent.total_runs += 1
            agent.successful_runs += 1
            agent.next_run_at = utc_now() + timedelta(seconds=agent.schedule_interval_seconds)
            
            # Record cost directly on this agent object to avoid session issues
            # (calling record_cost would do a separate query and potentially create
            # a different object instance, leading to stale data on commit)
            if cost_usd > 0:
                # Check if budget needs reset first
                await self._maybe_reset_budget(db, agent)
                
                # Update usage on the same object
                agent.budget_used += cost_usd
                agent.total_cost_usd += cost_usd
                agent.total_tokens_used += tokens_used
                
                # Check budget limits
                if agent.budget_limit:
                    if agent.budget_used >= agent.budget_limit:
                        agent.status = AgentStatus.BUDGET_EXCEEDED
                        agent.status_message = f"Budget limit of ${agent.budget_limit:.2f}/{agent.budget_period.value} exceeded"
                        agent.is_enabled = False
                        logger.warning(f"Agent {agent.slug} budget exceeded: ${agent.budget_used:.4f} / ${agent.budget_limit:.2f}")
                    elif agent.budget_used >= (agent.budget_limit * agent.budget_warning_threshold):
                        logger.warning(f"Agent {agent.slug} approaching budget limit: ${agent.budget_used:.4f} / ${agent.budget_limit:.2f}")
        
        await db.commit()
        await db.refresh(run)
        
        logger.info(f"Completed run {run_id}: processed={items_processed}, created={items_created}, tokens={tokens_used}")
        return run
    
    async def fail_run(
        self,
        db: AsyncSession,
        run_id: UUID,
        error_message: str,
        error_traceback: Optional[str] = None,
    ) -> Optional[AgentRun]:
        """Mark a run as failed."""
        run = await db.get(AgentRun, run_id)
        if not run:
            return None
        
        run.status = AgentRunStatus.FAILED
        run.completed_at = utc_now()
        started = ensure_utc(run.started_at)
        run.duration_seconds = (run.completed_at - started).total_seconds() if started else 0
        run.error_message = error_message
        run.error_traceback = error_traceback
        
        # Update agent
        agent = await db.get(AgentDefinition, run.agent_id)
        if agent:
            agent.status = AgentStatus.ERROR
            agent.status_message = error_message
            agent.total_runs += 1
            agent.failed_runs += 1
            # Still schedule next run despite error
            agent.next_run_at = utc_now() + timedelta(seconds=agent.schedule_interval_seconds)
        
        await db.commit()
        await db.refresh(run)
        
        logger.error(f"Failed run {run_id}: {error_message}")
        return run
    
    async def get_recent_runs(
        self,
        db: AsyncSession,
        slug: Optional[str] = None,
        limit: int = 20,
    ) -> List[AgentRun]:
        """Get recent agent runs."""
        query = select(AgentRun).options(selectinload(AgentRun.agent))
        
        if slug:
            agent = await self.get_agent(db, slug=slug)
            if agent:
                query = query.where(AgentRun.agent_id == agent.id)
        
        query = query.order_by(AgentRun.created_at.desc()).limit(limit)
        
        result = await db.execute(query)
        return list(result.scalars().all())
    
    # =========================================================================
    # Event Management
    # =========================================================================
    
    async def create_event(
        self,
        db: AsyncSession,
        event_type: str,
        source_type: str,
        source_id: UUID,
        target_agent_slug: str,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> AgentEvent:
        """Create an event to trigger an agent."""
        event = AgentEvent(
            event_type=event_type,
            source_type=source_type,
            source_id=source_id,
            target_agent_slug=target_agent_slug,
            event_data=event_data or {},
        )
        
        db.add(event)
        await db.commit()
        await db.refresh(event)
        
        logger.info(f"Created event {event_type} → {target_agent_slug} for {source_type}:{source_id}")
        return event
    
    async def get_pending_events(
        self,
        db: AsyncSession,
        agent_slug: str,
        limit: int = 10,
    ) -> List[AgentEvent]:
        """Get unprocessed events for an agent."""
        result = await db.execute(
            select(AgentEvent)
            .where(AgentEvent.target_agent_slug == agent_slug)
            .where(AgentEvent.is_processed == False)
            .order_by(AgentEvent.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def mark_event_processed(
        self,
        db: AsyncSession,
        event_id: UUID,
        run_id: Optional[UUID] = None,
    ) -> Optional[AgentEvent]:
        """Mark an event as processed."""
        event = await db.get(AgentEvent, event_id)
        if not event:
            return None
        
        event.is_processed = True
        event.processed_at = utc_now()
        event.processed_by_run_id = run_id
        
        await db.commit()
        await db.refresh(event)
        
        return event
    
    # =========================================================================
    # Scheduling Helpers
    # =========================================================================
    
    async def get_agents_due_for_run(
        self,
        db: AsyncSession,
    ) -> List[AgentDefinition]:
        """Get all agents that are due to run."""
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
        
        return list(result.scalars().all())


# Singleton instance
agent_scheduler_service = AgentSchedulerService()
