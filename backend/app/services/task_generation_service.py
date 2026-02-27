"""
Task Generation Service - Automatically creates tasks from system events.

This service handles:
- Campaign input requests → tasks
- Opportunity batches needing review → tasks  
- Processed ideas → tasks
- System events (expiring credentials, etc.) → tasks

It also handles task deduplication to avoid creating duplicate tasks.
"""

import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List
from uuid import UUID

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Task,
    Campaign,
    CampaignStatus,
    UserInputRequest,
    InputStatus,
    Opportunity,
    OpportunityStatus,
    UserIdea,
    IdeaStatus,
)
from app.models.task import TaskType, TaskStatus

logger = logging.getLogger(__name__)


class TaskGenerationService:
    """
    Service for automatically generating tasks from system events.
    
    Key design principles:
    1. Deduplication - Don't create duplicate tasks for the same source
    2. Linking - Tasks link back to their source entity
    3. Smart defaults - Estimate value/effort based on context
    4. Batching - Group related items (e.g., multiple opportunities)
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # ==========================================================================
    # Deduplication
    # ==========================================================================
    
    async def find_existing_task(
        self,
        user_id: UUID,
        source_type: str,
        source_id: UUID,
        active_only: bool = True,
    ) -> Optional[Task]:
        """
        Find an existing task for the given source.
        
        Used for deduplication - we don't want to create multiple tasks
        for the same campaign input request, opportunity, etc.
        """
        query = select(Task).where(
            Task.user_id == user_id,
            Task.source_type == source_type,
            Task.source_id == source_id,
        )
        
        if active_only:
            # Only consider non-terminal states
            query = query.where(
                Task.status.notin_([
                    TaskStatus.COMPLETED.value,
                    TaskStatus.CANCELLED.value,
                ])
            )
        
        # Order by created_at descending to get the most recent task
        query = query.order_by(Task.created_at.desc())
        
        result = await self.db.execute(query)
        return result.scalars().first()
    
    async def find_batch_task(
        self,
        user_id: UUID,
        source_type: str,
        task_type: TaskType,
        active_only: bool = True,
    ) -> Optional[Task]:
        """
        Find an existing batch task (like opportunity review).
        
        Batch tasks don't have a specific source_id but represent
        a group of items to process.
        """
        query = select(Task).where(
            Task.user_id == user_id,
            Task.source_type == source_type,
            Task.task_type == task_type.value,
            Task.source_id.is_(None),  # Batch tasks have no specific source
        )
        
        if active_only:
            query = query.where(
                Task.status.notin_([
                    TaskStatus.COMPLETED.value,
                    TaskStatus.CANCELLED.value,
                ])
            )
        
        # Order by created_at descending to get the most recent task
        query = query.order_by(Task.created_at.desc())
        
        result = await self.db.execute(query)
        return result.scalars().first()
    
    # ==========================================================================
    # Campaign Input Tasks
    # ==========================================================================
    
    async def create_task_for_campaign_input(
        self,
        user_id: UUID,
        campaign: Campaign,
        input_request: UserInputRequest,
    ) -> Optional[Task]:
        """
        Create a task for a campaign input request.
        
        Only creates if:
        - No existing active task for this input request
        - Input is still pending
        
        Returns the task (new or existing), or None if not needed.
        """
        # Check if input is still pending
        if input_request.status != InputStatus.PENDING:
            logger.debug(f"Input {input_request.id} is not pending, skipping task creation")
            return None
        
        # Check for existing task
        existing = await self.find_existing_task(
            user_id=user_id,
            source_type="campaign_input",
            source_id=input_request.id,
        )
        if existing:
            logger.debug(f"Task already exists for input {input_request.id}")
            return existing
        
        # Calculate estimated value (% of campaign budget)
        estimated_value = None
        if campaign.budget_allocated:
            # Blocking inputs are worth more
            value_multiplier = 0.1 if input_request.blocking_count > 0 else 0.05
            estimated_value = float(campaign.budget_allocated) * value_multiplier
        
        # Estimate effort based on input type
        effort_estimates = {
            "text": 5,
            "textarea": 15,
            "choice": 2,
            "credentials": 10,
            "file": 15,
        }
        effort_minutes = effort_estimates.get(input_request.input_type.value, 10)
        
        # Create task
        task = Task(
            user_id=user_id,
            title=f"Provide input: {input_request.title}",
            description=self._format_campaign_input_description(campaign, input_request),
            task_type=TaskType.CAMPAIGN_ACTION.value,
            due_date=None,  # Campaign inputs don't have deadlines
            estimated_value=estimated_value,
            estimated_effort_minutes=effort_minutes,
            status=TaskStatus.READY.value,  # Campaign inputs are immediately actionable
            source_type="campaign_input",
            source_id=input_request.id,
            source_context={
                "campaign_id": str(campaign.id),
                "campaign_title": campaign.proposal.title if campaign.proposal else "Unknown Campaign",
                "input_key": input_request.input_key,
                "blocking_count": input_request.blocking_count,
            },
        )
        
        self.db.add(task)
        await self.db.flush()
        
        logger.info(f"Created task {task.id} for campaign input {input_request.id}")
        return task
    
    def _format_campaign_input_description(
        self,
        campaign: Campaign,
        input_request: UserInputRequest,
    ) -> str:
        """Format a helpful description for campaign input task."""
        campaign_title = campaign.proposal.title if campaign.proposal else "Unknown Campaign"
        parts = [
            f"Campaign **{campaign_title}** needs your input:",
            "",
            f"**{input_request.title}**",
        ]
        
        if input_request.description:
            parts.append("")
            parts.append(input_request.description)
        
        if input_request.blocking_count > 0:
            parts.append("")
            parts.append(f"⚠️ This input is blocking {input_request.blocking_count} items from proceeding.")
        
        parts.append("")
        parts.append(f"[Go to Campaign](/campaigns/{campaign.id})")
        
        return "\n".join(parts)
    
    async def create_tasks_for_pending_inputs(
        self,
        user_id: UUID,
    ) -> List[Task]:
        """
        Scan all active campaigns and create tasks for pending inputs.
        
        This is useful for a batch scan (e.g., on login or scheduled).
        """
        # Get all active campaigns with pending inputs
        from app.models import Campaign, CampaignStatus
        
        result = await self.db.execute(
            select(Campaign)
            .options(selectinload(Campaign.proposal))
            .where(
                Campaign.user_id == user_id,
                Campaign.status.in_([
                    CampaignStatus.INITIALIZING.value,
                    CampaignStatus.REQUIREMENTS_GATHERING.value,
                    CampaignStatus.EXECUTING.value,
                    CampaignStatus.MONITORING.value,
                    CampaignStatus.WAITING_FOR_INPUTS.value,
                    CampaignStatus.ACTIVE.value,
                    CampaignStatus.PAUSED.value,
                ])
            )
        )
        campaigns = result.scalars().all()
        
        created_tasks = []
        
        for campaign in campaigns:
            # Get pending inputs for this campaign
            inputs_result = await self.db.execute(
                select(UserInputRequest).where(
                    UserInputRequest.campaign_id == campaign.id,
                    UserInputRequest.status == InputStatus.PENDING,
                )
            )
            pending_inputs = inputs_result.scalars().all()
            
            for input_req in pending_inputs:
                task = await self.create_task_for_campaign_input(
                    user_id=user_id,
                    campaign=campaign,
                    input_request=input_req,
                )
                if task and task.id:  # New task was created
                    created_tasks.append(task)
        
        if created_tasks:
            await self.db.commit()
            logger.info(f"Created {len(created_tasks)} tasks for pending campaign inputs")
        
        return created_tasks
    
    # ==========================================================================
    # Opportunity Review Tasks
    # ==========================================================================
    
    async def create_or_update_opportunity_review_task(
        self,
        user_id: UUID,
        min_batch_size: int = 5,
    ) -> Optional[Task]:
        """
        Create or update a task to review pending opportunities.
        
        We batch opportunities together rather than creating one task per
        opportunity. This task is updated with current counts.
        
        Note: Opportunities are not per-user in the current schema, so
        this counts all pending opportunities.
        
        Args:
            user_id: The user to create task for
            min_batch_size: Minimum opportunities before creating task
            
        Returns:
            Task if created/updated, None if below threshold
        """
        # Count pending opportunities (DISCOVERED or EVALUATED, not yet presented)
        count_result = await self.db.execute(
            select(func.count(Opportunity.id)).where(
                Opportunity.status.in_([
                    OpportunityStatus.DISCOVERED,
                    OpportunityStatus.EVALUATED,
                ])
            )
        )
        pending_count = count_result.scalar() or 0
        
        if pending_count == 0:
            # Close any existing review task
            await self._complete_batch_task_if_empty(
                user_id=user_id,
                source_type="opportunity_batch",
                task_type=TaskType.REVIEW_REQUIRED,
            )
            return None
        
        # For estimated value, we use a simple heuristic based on count
        # (Opportunity.estimated_revenue_potential is JSONB, so we simplify)
        total_value = float(pending_count * 100)  # Assume ~$100 avg potential per opportunity
        
        # Check for existing batch task
        existing = await self.find_batch_task(
            user_id=user_id,
            source_type="opportunity_batch",
            task_type=TaskType.REVIEW_REQUIRED,
        )
        
        if existing:
            # Update existing task with current counts
            existing.title = f"Review {pending_count} new opportunities"
            existing.description = self._format_opportunity_review_description(
                pending_count, total_value
            )
            existing.estimated_value = total_value * 0.1  # 10% conversion estimate
            existing.estimated_effort_minutes = pending_count * 2
            existing.source_context = {
                "pending_count": pending_count,
                "total_value": total_value,
                "last_updated": utc_now().isoformat(),
            }
            existing.updated_at = utc_now()
            
            await self.db.flush()
            logger.debug(f"Updated opportunity review task with {pending_count} opportunities")
            return existing
        
        # Don't create new task below threshold
        if pending_count < min_batch_size:
            logger.debug(f"Only {pending_count} opportunities, below threshold of {min_batch_size}")
            return None
        
        # Create new batch task
        task = Task(
            user_id=user_id,
            title=f"Review {pending_count} new opportunities",
            description=self._format_opportunity_review_description(pending_count, total_value),
            task_type=TaskType.REVIEW_REQUIRED.value,
            estimated_value=total_value * 0.1,
            estimated_effort_minutes=pending_count * 2,
            status=TaskStatus.READY.value,
            source_type="opportunity_batch",
            source_id=None,  # Batch tasks don't have specific source
            source_context={
                "pending_count": pending_count,
                "total_value": total_value,
                "created": utc_now().isoformat(),
            },
        )
        
        self.db.add(task)
        await self.db.flush()
        
        logger.info(f"Created opportunity review task for {pending_count} opportunities")
        return task
    
    def _format_opportunity_review_description(
        self,
        count: int,
        total_value: float,
    ) -> str:
        """Format description for opportunity review task."""
        parts = [
            f"{count} opportunities are waiting for triage.",
            "",
            f"**Potential value:** ${total_value:,.0f}",
            f"**Estimated time:** {count * 2} minutes",
            "",
            "[Start Triage](/scout?filter=new)",
        ]
        return "\n".join(parts)
    
    async def _complete_batch_task_if_empty(
        self,
        user_id: UUID,
        source_type: str,
        task_type: TaskType,
    ) -> bool:
        """
        Mark a batch task as completed if the batch is now empty.
        
        Returns True if a task was completed.
        """
        existing = await self.find_batch_task(
            user_id=user_id,
            source_type=source_type,
            task_type=task_type,
        )
        
        if existing:
            existing.status = TaskStatus.COMPLETED.value
            existing.completed_at = utc_now()
            existing.completion_notes = "Batch empty - all items processed"
            await self.db.flush()
            logger.info(f"Auto-completed empty batch task {existing.id}")
            return True
        
        return False
    
    # ==========================================================================
    # Idea Processing Tasks
    # ==========================================================================
    
    async def create_task_for_processed_idea(
        self,
        user_id: UUID,
        idea: UserIdea,
        recommended_action: str,
        next_steps: Optional[str] = None,
        estimated_value: Optional[float] = None,
    ) -> Optional[Task]:
        """
        Create a task from a processed idea.
        
        Called when Opportunity Scout or another agent processes an idea
        and recommends a next action.
        """
        # Check for existing task
        existing = await self.find_existing_task(
            user_id=user_id,
            source_type="idea",
            source_id=idea.id,
        )
        if existing:
            logger.debug(f"Task already exists for idea {idea.id}")
            return existing
        
        # Create task
        task = Task(
            user_id=user_id,
            title=recommended_action[:255],  # Truncate if needed
            description=self._format_idea_task_description(idea, next_steps),
            task_type=TaskType.IDEA_ACTION.value,
            estimated_value=estimated_value,
            estimated_effort_minutes=30,  # Default estimate for idea follow-ups
            status=TaskStatus.READY.value,
            source_type="idea",
            source_id=idea.id,
            source_context={
                "original_content": idea.original_content[:500],  # Truncate for storage
                "source": idea.source,
            },
        )
        
        self.db.add(task)
        await self.db.flush()
        
        logger.info(f"Created task {task.id} for idea {idea.id}")
        return task
    
    def _format_idea_task_description(
        self,
        idea: UserIdea,
        next_steps: Optional[str],
    ) -> str:
        """Format description for idea-generated task."""
        parts = [
            "**Original idea:**",
            f"> {idea.reformatted_content or idea.original_content}",
            "",
        ]
        
        if next_steps:
            parts.append("**Recommended next steps:**")
            parts.append(next_steps)
            parts.append("")
        
        parts.append(f"[View Idea](/ideas/{idea.id})")
        
        return "\n".join(parts)
    
    # ==========================================================================
    # Follow-up Tasks
    # ==========================================================================
    
    async def create_follow_up_task(
        self,
        user_id: UUID,
        title: str,
        description: str,
        follow_up_date: datetime,
        source_type: Optional[str] = None,
        source_id: Optional[UUID] = None,
        estimated_value: Optional[float] = None,
    ) -> Task:
        """
        Create a scheduled follow-up task.
        
        Follow-up tasks start as deferred and become ready on the follow-up date.
        """
        task = Task(
            user_id=user_id,
            title=title,
            description=description,
            task_type=TaskType.FOLLOW_UP.value,
            due_date=follow_up_date,
            estimated_value=estimated_value,
            estimated_effort_minutes=15,
            status=TaskStatus.DEFERRED.value,
            deferred_until=follow_up_date,
            source_type=source_type,
            source_id=source_id,
        )
        
        self.db.add(task)
        await self.db.flush()
        
        logger.info(f"Created follow-up task {task.id} for {follow_up_date}")
        return task
    
    # ==========================================================================
    # Task Cleanup / Sync
    # ==========================================================================
    
    async def cancel_task_for_source(
        self,
        user_id: UUID,
        source_type: str,
        source_id: UUID,
        reason: str = "Source no longer requires action",
    ) -> bool:
        """
        Cancel an active task when its source is resolved.
        
        Called when:
        - Campaign input is provided
        - Opportunity is processed
        - Idea is dismissed
        """
        existing = await self.find_existing_task(
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
        )
        
        if existing:
            existing.status = TaskStatus.CANCELLED.value
            existing.completion_notes = reason
            await self.db.flush()
            logger.info(f"Cancelled task {existing.id} for {source_type}/{source_id}")
            return True
        
        return False
    
    async def complete_task_for_source(
        self,
        user_id: UUID,
        source_type: str,
        source_id: UUID,
        completion_notes: Optional[str] = None,
        actual_value: Optional[float] = None,
    ) -> bool:
        """
        Mark a task as completed when its source action is done.
        """
        existing = await self.find_existing_task(
            user_id=user_id,
            source_type=source_type,
            source_id=source_id,
        )
        
        if existing:
            existing.status = TaskStatus.COMPLETED.value
            existing.completed_at = utc_now()
            existing.completion_notes = completion_notes
            existing.actual_value = actual_value
            await self.db.flush()
            logger.info(f"Completed task {existing.id} for {source_type}/{source_id}")
            return True
        
        return False
    
    async def activate_deferred_tasks(self, user_id: UUID) -> int:
        """
        Activate deferred tasks that have passed their deferred_until date.
        
        Returns count of activated tasks.
        """
        now = utc_now()
        
        result = await self.db.execute(
            select(Task).where(
                Task.user_id == user_id,
                Task.status == TaskStatus.DEFERRED.value,
                Task.deferred_until <= now,
            )
        )
        deferred_tasks = result.scalars().all()
        
        count = 0
        for task in deferred_tasks:
            task.status = TaskStatus.READY.value
            task.deferred_until = None
            count += 1
        
        if count > 0:
            await self.db.flush()
            logger.info(f"Activated {count} deferred tasks for user {user_id}")
        
        return count
