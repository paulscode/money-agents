"""Campaign Context Service - builds context for campaign discussions.

This service provides tiered context building for campaign discussions:
- Tier 1 (Core): Always included - status, budget, progress, blockers
- Tier 2 (Detailed): On-demand - full stream/task details, recent executions
- Tier 3 (Historical): Compressed summaries of historical data
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any, Set
from uuid import UUID

from sqlalchemy import select, func, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Campaign,
    Proposal,
    TaskStream,
    CampaignTask,
    UserInputRequest,
    Message,
    Conversation,
    ConversationType,
    TaskStreamStatus,
    TaskStatus,
    InputStatus,
)
from app.services.prompt_injection_guard import sanitize_external_content

logger = logging.getLogger(__name__)


@dataclass
class CampaignContextMeta:
    """Metadata about the context built."""
    tier1_tokens: int = 0
    tier2_tokens: int = 0
    tier3_tokens: int = 0
    total_tokens: int = 0
    compression_applied: bool = False


@dataclass
class CampaignContext:
    """Complete context for campaign discussion."""
    core: Dict[str, Any]
    detailed: Optional[Dict[str, Any]] = None
    historical: Optional[Dict[str, Any]] = None
    meta: CampaignContextMeta = None
    
    def __post_init__(self):
        if self.meta is None:
            self.meta = CampaignContextMeta()


class CampaignContextService:
    """
    Service for building tiered context for campaign discussions.
    
    Context is built in tiers to manage token limits:
    - Tier 1: Always included, ~2000 tokens
    - Tier 2: On-demand based on query relevance, ~4000 tokens
    - Tier 3: Compressed historical summaries, ~2000 tokens
    """
    
    # Approximate tokens per item (for budgeting)
    TOKENS_PER_STREAM_SUMMARY = 50
    TOKENS_PER_TASK = 100
    TOKENS_PER_INPUT = 50
    TOKENS_PER_EXECUTION = 150
    TOKENS_PER_MESSAGE = 100
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def build_core_context(self, campaign_id: UUID) -> Dict[str, Any]:
        """
        Build Tier 1 core context - always included.
        
        Includes:
        - Campaign status, phase, IDs
        - Proposal title and summary
        - Budget summary
        - Progress summary
        - Current blockers
        - Stream status summaries (name + status only)
        - Blocking input requests
        
        Returns:
            Dict with core campaign context
        """
        # Load campaign with proposal
        query = (
            select(Campaign)
            .options(selectinload(Campaign.proposal))
            .where(Campaign.id == campaign_id)
        )
        result = await self.db.execute(query)
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return {"error": f"Campaign {campaign_id} not found"}
        
        proposal = campaign.proposal
        
        # Build core context
        core = {
            "id": str(campaign.id),
            "status": campaign.status.value if campaign.status else "unknown",
            "phase": campaign.current_phase or "unknown",
            "created_at": campaign.created_at.isoformat() if campaign.created_at else None,
            "updated_at": campaign.updated_at.isoformat() if campaign.updated_at else None,
        }
        
        # Proposal summary
        if proposal:
            core["proposal"] = {
                "id": str(proposal.id),
                "title": proposal.title,
                "summary": proposal.summary[:500] if proposal.summary else None,
                "risk_level": proposal.risk_level.value if proposal.risk_level else None,
            }
        
        # Budget tracking
        core["budget"] = {
            "allocated": float(campaign.budget_allocated or 0),
            "spent": float(campaign.budget_spent or 0),
            "remaining": float((campaign.budget_allocated or 0) - (campaign.budget_spent or 0)),
            "percentage_used": round(
                (campaign.budget_spent or 0) / (campaign.budget_allocated or 1) * 100, 1
            ) if campaign.budget_allocated else 0,
        }
        
        # Revenue tracking
        core["revenue"] = {
            "generated": float(campaign.revenue_generated or 0),
            "profit": float((campaign.revenue_generated or 0) - (campaign.budget_spent or 0)),
        }
        
        # Progress tracking
        core["progress"] = {
            "total_tasks": campaign.tasks_total or 0,
            "completed_tasks": campaign.tasks_completed or 0,
            "failed_tasks": 0,  # Will be updated from streams
            "percentage": round(
                (campaign.tasks_completed or 0) / (campaign.tasks_total or 1) * 100, 1
            ) if campaign.tasks_total else 0,
        }
        
        # Success metrics
        if campaign.success_metrics:
            core["success_metrics"] = campaign.success_metrics
        
        # Get stream summaries
        streams_query = (
            select(TaskStream)
            .where(TaskStream.campaign_id == campaign_id)
            .order_by(TaskStream.order_index)
        )
        streams_result = await self.db.execute(streams_query)
        streams = streams_result.scalars().all()
        
        stream_summaries = []
        total_failed = 0
        blockers = []
        
        for stream in streams:
            # Count tasks by status
            tasks_query = select(
                func.count(CampaignTask.id).label('total'),
                func.sum(func.cast(CampaignTask.status == TaskStatus.COMPLETED, Integer)).label('completed'),
                func.sum(func.cast(CampaignTask.status == TaskStatus.FAILED, Integer)).label('failed'),
                func.sum(func.cast(CampaignTask.status == TaskStatus.BLOCKED, Integer)).label('blocked'),
            ).where(CampaignTask.stream_id == stream.id)
            
            # Simpler approach - just count
            tasks_result = await self.db.execute(
                select(CampaignTask).where(CampaignTask.stream_id == stream.id)
            )
            stream_tasks = tasks_result.scalars().all()
            
            completed = sum(1 for t in stream_tasks if t.status == TaskStatus.COMPLETED)
            failed = sum(1 for t in stream_tasks if t.status == TaskStatus.FAILED)
            blocked = sum(1 for t in stream_tasks if t.status == TaskStatus.BLOCKED)
            total = len(stream_tasks)
            
            total_failed += failed
            
            status_text = stream.status.value if stream.status else "unknown"
            
            # Calculate progress
            progress_pct = round(completed / total * 100, 1) if total > 0 else 0
            
            summary = {
                "id": str(stream.id),
                "name": stream.name,
                "status": status_text,
                "tasks": f"{completed}/{total}",
                "progress_pct": progress_pct,
            }
            
            # Add blocking info
            if stream.status == TaskStreamStatus.BLOCKED:
                summary["blocked"] = True
                if blocked > 0:
                    blockers.append(f"Stream '{stream.name}' has {blocked} blocked task(s)")
            
            stream_summaries.append(summary)
        
        core["progress"]["failed_tasks"] = total_failed
        core["streams"] = stream_summaries
        
        # Get blocking input requests
        inputs_query = (
            select(UserInputRequest)
            .where(
                UserInputRequest.campaign_id == campaign_id,
                UserInputRequest.status == InputStatus.PENDING,
            )
            .order_by(UserInputRequest.priority.desc())
        )
        inputs_result = await self.db.execute(inputs_query)
        blocking_inputs = inputs_result.scalars().all()
        
        core["blocking_inputs"] = [
            {
                "key": inp.input_key,
                "title": inp.title,
                "type": inp.input_type.value if inp.input_type else "text",
                "priority": inp.priority.value if inp.priority else "medium",
            }
            for inp in blocking_inputs[:5]  # Limit to top 5 for core context
        ]
        
        if blocking_inputs:
            blockers.append(f"{len(blocking_inputs)} input(s) pending from user")
        
        core["blockers"] = blockers
        
        return core
    
    async def build_detailed_context(
        self,
        campaign_id: UUID,
        include_streams: bool = True,
        include_tasks: bool = True,
        include_inputs: bool = True,
        include_executions: bool = True,
        stream_names: Optional[List[str]] = None,
        max_tasks_per_stream: int = 20,
        max_executions: int = 10,
    ) -> Dict[str, Any]:
        """
        Build Tier 2 detailed context - on-demand based on query.
        
        Includes:
        - Full stream details with task lists
        - Recent execution history
        - All input requests with values
        
        Args:
            campaign_id: The campaign to build context for
            include_streams: Whether to include stream details
            include_tasks: Whether to include task details
            include_inputs: Whether to include input details
            include_executions: Whether to include execution history
            stream_names: Optional filter for specific streams
            max_tasks_per_stream: Limit tasks per stream
            max_executions: Limit execution records
            
        Returns:
            Dict with detailed context
        """
        detailed = {}
        
        # Get full stream and task details
        if include_streams or include_tasks:
            streams_query = (
                select(TaskStream)
                .where(TaskStream.campaign_id == campaign_id)
            )
            if stream_names:
                streams_query = streams_query.where(TaskStream.name.in_(stream_names))
            streams_query = streams_query.order_by(TaskStream.order_index)
            
            streams_result = await self.db.execute(streams_query)
            streams = streams_result.scalars().all()
            
            detailed_streams = []
            for stream in streams:
                stream_data = {
                    "id": str(stream.id),
                    "name": stream.name,
                    "description": stream.description,
                    "status": stream.status.value if stream.status else "unknown",
                    "can_run_parallel": stream.can_run_parallel,
                    "depends_on_streams": stream.depends_on_streams or [],
                    "requires_inputs": stream.requires_inputs or [],
                }
                
                if include_tasks:
                    tasks_query = (
                        select(CampaignTask)
                        .where(CampaignTask.stream_id == stream.id)
                        .order_by(CampaignTask.order_index)
                        .limit(max_tasks_per_stream)
                    )
                    tasks_result = await self.db.execute(tasks_query)
                    tasks = tasks_result.scalars().all()
                    
                    stream_data["tasks"] = [
                        {
                            "id": str(task.id),
                            "name": task.name,
                            "status": task.status.value if task.status else "pending",
                            "task_type": task.task_type.value if task.task_type else "unknown",
                            "tool_slug": task.tool_slug,
                            "is_critical": task.is_critical,
                            "result_summary": self._summarize_result(task.result),
                            "error": task.error_message[:200] if task.error_message else None,
                        }
                        for task in tasks
                    ]
                
                detailed_streams.append(stream_data)
            
            detailed["streams"] = detailed_streams
        
        # Get all input requests with full details
        if include_inputs:
            inputs_query = (
                select(UserInputRequest)
                .where(UserInputRequest.campaign_id == campaign_id)
                .order_by(UserInputRequest.priority.desc(), UserInputRequest.created_at)
            )
            inputs_result = await self.db.execute(inputs_query)
            inputs = inputs_result.scalars().all()
            
            detailed["inputs"] = [
                {
                    "id": str(inp.id),
                    "key": inp.input_key,
                    "title": inp.title,
                    "description": inp.description,
                    "type": inp.input_type.value if inp.input_type else "text",
                    "priority": inp.priority.value if inp.priority else "medium",
                    "status": inp.status.value if inp.status else "pending",
                    "options": inp.options,
                    "default_value": inp.default_value,
                    "provided_value": inp.value,
                    "blocking_tasks_count": inp.blocking_count or 0,
                }
                for inp in inputs
            ]
        
        # TODO: Add execution history when we have execution tracking
        if include_executions:
            detailed["recent_executions"] = []  # Placeholder for future execution history
        
        return detailed
    
    async def build_historical_context(
        self,
        campaign_id: UUID,
        include_conversation_summary: bool = True,
        include_key_decisions: bool = True,
        max_messages: int = 50,
    ) -> Dict[str, Any]:
        """
        Build Tier 3 historical context - compressed summaries.
        
        Includes:
        - Summarized execution history
        - Conversation summary
        - Key decisions and milestones
        
        Note: This is a stub implementation for Phase 1.
        Full compression will be implemented in Phase 3.
        
        Returns:
            Dict with historical context summaries
        """
        historical = {
            "execution_summary": "No execution history available yet.",
            "conversation_summary": None,
            "key_decisions": [],
            "milestones": [],
        }
        
        # Get recent conversation messages for summary
        if include_conversation_summary:
            conv_query = (
                select(Conversation)
                .where(
                    Conversation.conversation_type == ConversationType.CAMPAIGN,
                    Conversation.related_id == campaign_id,
                )
            )
            conv_result = await self.db.execute(conv_query)
            conversation = conv_result.scalar_one_or_none()
            
            if conversation:
                msgs_query = (
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(desc(Message.created_at))
                    .limit(max_messages)
                )
                msgs_result = await self.db.execute(msgs_query)
                messages = msgs_result.scalars().all()
                
                if messages:
                    # For now, just note how many messages exist
                    # Phase 3 will implement actual summarization
                    historical["conversation_summary"] = (
                        f"Discussion history: {len(messages)} messages in conversation."
                    )
        
        return historical
    
    async def build_full_context(
        self,
        campaign_id: UUID,
        include_tier2: bool = False,
        include_tier3: bool = False,
        keywords: Optional[List[str]] = None,
    ) -> CampaignContext:
        """
        Build complete context with all tiers.
        
        Args:
            campaign_id: The campaign to build context for
            include_tier2: Whether to include detailed context
            include_tier3: Whether to include historical context
            keywords: Optional keywords to guide context selection
            
        Returns:
            CampaignContext with all requested tiers
        """
        # Always build core context
        core = await self.build_core_context(campaign_id)
        
        context = CampaignContext(core=core)
        
        # Estimate tier 1 tokens
        context.meta.tier1_tokens = self._estimate_tokens(core)
        
        # Optionally build tier 2
        if include_tier2:
            # If keywords provided, determine which streams to include
            stream_names = None
            if keywords:
                stream_names = self._match_streams_to_keywords(
                    core.get("streams", []), 
                    keywords
                )
            
            detailed = await self.build_detailed_context(
                campaign_id,
                stream_names=stream_names,
            )
            context.detailed = detailed
            context.meta.tier2_tokens = self._estimate_tokens(detailed)
        
        # Optionally build tier 3
        if include_tier3:
            historical = await self.build_historical_context(campaign_id)
            context.historical = historical
            context.meta.tier3_tokens = self._estimate_tokens(historical)
        
        # Calculate total
        context.meta.total_tokens = (
            context.meta.tier1_tokens +
            context.meta.tier2_tokens +
            context.meta.tier3_tokens
        )
        
        return context
    
    def format_context_for_prompt(self, context: CampaignContext) -> str:
        """
        Format the campaign context as a string for the system prompt.
        
        Args:
            context: The CampaignContext object
            
        Returns:
            Formatted string for inclusion in system prompt
        """
        lines = ["## Current Campaign Context", ""]
        core = context.core
        
        # Error case
        if "error" in core:
            return f"## Campaign Context Error\n\n{core['error']}"
        
        # Status and phase
        lines.append(f"**Campaign ID:** {core.get('id', 'unknown')}")
        lines.append(f"**Status:** {core.get('status', 'unknown')}")
        lines.append(f"**Phase:** {core.get('phase', 'unknown')}")
        
        # Proposal
        if proposal := core.get("proposal"):
            san_title, _ = sanitize_external_content(
                proposal.get('title', 'Untitled'), source="campaign_context"
            )
            lines.append(f"\n### Proposal: {san_title}")
            if summary := proposal.get("summary"):
                san_summary, _ = sanitize_external_content(summary, source="campaign_context")
                lines.append(f"{san_summary}")
        
        # Budget
        if budget := core.get("budget"):
            pct = budget.get("percentage_used", 0)
            lines.append(f"\n**Budget:** ${budget.get('spent', 0):,.2f} / ${budget.get('allocated', 0):,.2f} ({pct:.1f}% used)")
            lines.append(f"**Remaining:** ${budget.get('remaining', 0):,.2f}")
        
        # Revenue
        if revenue := core.get("revenue"):
            if revenue.get("generated", 0) > 0:
                lines.append(f"**Revenue:** ${revenue.get('generated', 0):,.2f}")
                lines.append(f"**Profit:** ${revenue.get('profit', 0):,.2f}")
        
        # Progress
        if progress := core.get("progress"):
            lines.append(f"\n**Tasks:** {progress.get('completed_tasks', 0)}/{progress.get('total_tasks', 0)} completed ({progress.get('percentage', 0):.1f}%)")
            if progress.get("failed_tasks", 0) > 0:
                lines.append(f"**Failed:** {progress['failed_tasks']} task(s)")
        
        # Streams summary
        if streams := core.get("streams"):
            lines.append("\n### Streams")
            for stream in streams:
                status_icon = {
                    "pending": "⏳",
                    "ready": "🟢",
                    "in_progress": "🔄",
                    "blocked": "🔴",
                    "completed": "✅",
                    "failed": "❌",
                }.get(stream.get("status", ""), "•")
                lines.append(f"- {status_icon} **{stream.get('name')}**: {stream.get('tasks')} tasks ({stream.get('progress_pct', 0)}%)")
        
        # Blockers
        if blockers := core.get("blockers"):
            lines.append("\n### ⚠️ Current Blockers")
            for blocker in blockers:
                lines.append(f"- {blocker}")
        
        # Blocking inputs
        if blocking_inputs := core.get("blocking_inputs"):
            lines.append("\n### 📝 Pending Inputs Needed")
            for inp in blocking_inputs:
                lines.append(f"- **{inp.get('title', inp.get('key'))}** ({inp.get('type')}, {inp.get('priority')} priority)")
        
        # Detailed context (Tier 2)
        if context.detailed:
            lines.append("\n---\n### Detailed Information")
            
            # Full stream details
            if streams := context.detailed.get("streams"):
                for stream in streams:
                    lines.append(f"\n#### Stream: {stream.get('name')}")
                    lines.append(f"Status: {stream.get('status')} | Parallel: {stream.get('can_run_parallel', False)}")
                    if desc := stream.get("description"):
                        san_desc, _ = sanitize_external_content(desc[:200], source="campaign_context")
                        lines.append(f"_{san_desc}_")
                    
                    if tasks := stream.get("tasks"):
                        # Show active/failed/blocked tasks first, then recent completed
                        # Cap at 10 tasks per stream to prevent unbounded growth
                        priority_tasks = [t for t in tasks if t.get("status") in ("in_progress", "failed", "blocked")]
                        other_tasks = [t for t in tasks if t.get("status") not in ("in_progress", "failed", "blocked")]
                        shown_tasks = priority_tasks + other_tasks[:max(0, 10 - len(priority_tasks))]
                        omitted = len(tasks) - len(shown_tasks)
                        
                        lines.append(f"\nTasks ({len(tasks)} total):")
                        for task in shown_tasks:
                            status_icon = {
                                "pending": "⏳",
                                "in_progress": "🔄",
                                "completed": "✅",
                                "failed": "❌",
                                "blocked": "🔴",
                                "skipped": "⏭️",
                            }.get(task.get("status", ""), "•")
                            task_line = f"  - {status_icon} {task.get('name')}"
                            if task.get("tool_slug"):
                                task_line += f" [tool: {task['tool_slug']}]"
                            if task.get("result_summary"):
                                summary = task['result_summary']
                                if len(summary) > 100:
                                    summary = summary[:100] + "..."
                                san_summary, _ = sanitize_external_content(summary, source="campaign_context")
                                task_line += f" → {san_summary}"
                            if task.get("error"):
                                error = task['error']
                                if len(error) > 100:
                                    error = error[:100] + "..."
                                san_error, _ = sanitize_external_content(error, source="campaign_context")
                                task_line += f" ⚠️ {san_error}"
                            lines.append(task_line)
                        if omitted > 0:
                            lines.append(f"  _...and {omitted} more tasks_")
            
            # Full input details (cap at 15)
            if inputs := context.detailed.get("inputs"):
                lines.append("\n#### All Input Requests")
                shown_inputs = inputs[:15]
                for inp in shown_inputs:
                    status_icon = "✅" if inp.get("status") == "provided" else "⏳"
                    lines.append(f"- {status_icon} **{inp.get('key')}**: {inp.get('title')}")
                    if inp.get("provided_value"):
                        # Truncate long values
                        value = inp['provided_value']
                        if len(value) > 100:
                            value = value[:100] + "..."
                        san_value, _ = sanitize_external_content(value, source="campaign_context")
                        lines.append(f"  Value: `{san_value}`")
                if len(inputs) > 15:
                    lines.append(f"_...and {len(inputs) - 15} more inputs_")
        
        # Historical context (Tier 3) — cap at 500 chars
        if context.historical:
            if summary := context.historical.get("conversation_summary"):
                if len(summary) > 500:
                    summary = summary[:500] + "..."
                lines.append(f"\n---\n### History\n{summary}")
        
        lines.append("")
        return "\n".join(lines)
    
    def _summarize_result(self, result_data: Optional[Dict]) -> Optional[str]:
        """Create a brief summary of task result data."""
        if not result_data:
            return None
        
        # Just return a brief indicator for now
        if isinstance(result_data, dict):
            keys = list(result_data.keys())[:3]
            return f"Result with {len(result_data)} fields: {', '.join(keys)}"
        
        return "Has result"
    
    def _estimate_tokens(self, data: Any) -> int:
        """
        Estimate token count for data structure.
        
        Rough estimate: ~4 characters per token on average.
        """
        import json
        try:
            json_str = json.dumps(data, default=str)
            return len(json_str) // 4
        except Exception:
            return 0
    
    def _match_streams_to_keywords(
        self,
        streams: List[Dict],
        keywords: List[str],
    ) -> Optional[List[str]]:
        """
        Match streams to keywords for context selection.
        
        Returns stream names that match keywords, or None if no specific matches.
        """
        if not keywords or not streams:
            return None
        
        keywords_lower = [k.lower() for k in keywords]
        matched = []
        
        for stream in streams:
            name = stream.get("name", "").lower()
            # Check if any keyword appears in stream name
            if any(kw in name for kw in keywords_lower):
                matched.append(stream.get("name"))
        
        return matched if matched else None
    
    async def build_smart_context(
        self,
        campaign_id: UUID,
        user_message: str,
        conversation_history: Optional[List[Dict]] = None,
        model: str = "default",
    ) -> CampaignContext:
        """
        Build context using smart analysis and compression (Phase 3).
        
        Analyzes the user's message to determine:
        - Query intent (status check, help with inputs, history, etc.)
        - Mentioned entities (streams, tasks, inputs)
        - Context tier requirements
        
        Then builds optimized context within token budget.
        
        Args:
            campaign_id: The campaign to build context for
            user_message: The user's current message
            conversation_history: Optional previous messages
            model: Model name for budget calculation
            
        Returns:
            CampaignContext with smart compression applied
        """
        try:
            from app.services.context_compression_service import (
                ContextAnalyzer,
                TokenCounter,
                ContextCompressor,
                ContextBudgetManager,
            )
        except ImportError:
            # Fall back to basic context if compression service not available
            logger.warning("Context compression service not available, using basic context")
            return await self.build_full_context(campaign_id, include_tier2=True)
        
        # First build core context (always needed)
        core = await self.build_core_context(campaign_id)
        
        if "error" in core:
            return CampaignContext(core=core)
        
        # Analyze user message
        analyzer = ContextAnalyzer(campaign_context=core)
        analysis = analyzer.analyze(user_message)
        
        logger.info(
            f"Query analysis: intent={analysis.intent.value}, "
            f"needs_tier2={analysis.needs_tier2}, needs_tier3={analysis.needs_tier3}, "
            f"streams={analysis.mentioned_streams}, inputs={analysis.mentioned_inputs}"
        )
        
        # Calculate token counts
        core_tokens = TokenCounter.count_tokens(str(core))
        
        # Initialize budget manager
        budget_manager = ContextBudgetManager(model)
        
        # Initialize meta
        meta = CampaignContextMeta(
            tier1_tokens=core_tokens,
        )
        
        # Build detailed context if needed
        detailed = None
        if analysis.needs_tier2:
            # Determine which streams to focus on
            stream_names = list(analysis.mentioned_streams) if analysis.mentioned_streams else None
            
            detailed = await self.build_detailed_context(
                campaign_id,
                include_streams=True,
                include_tasks=True,
                include_inputs=analysis.intent.value == "input_help" or bool(analysis.mentioned_inputs),
                include_executions=analysis.intent.value in ("history", "status"),
                stream_names=stream_names,
                max_tasks_per_stream=10 if stream_names else 5,  # More detail for mentioned streams
                max_executions=10,
            )
            
            # Compress if needed
            compressor = ContextCompressor(self.db)
            available_budget = budget_manager.get_available_budget(
                tier1_tokens=core_tokens,
            )
            
            # Compress tasks if they exist
            if detailed.get("streams"):
                all_tasks = []
                for stream in detailed.get("streams", []):
                    for task in stream.get("tasks", []):
                        task["stream_name"] = stream.get("name")
                        all_tasks.append(task)
                
                if all_tasks:
                    # Identify relevant task IDs
                    relevant_ids: Set[str] = set()
                    if analysis.mentioned_tasks:
                        for task in all_tasks:
                            if task.get("name", "").lower() in analysis.mentioned_tasks:
                                relevant_ids.add(task.get("id", ""))
                    
                    task_result = await compressor.compress_task_details(
                        all_tasks,
                        max_tokens=available_budget["tier2"] // 2,
                        relevant_ids=relevant_ids if relevant_ids else None,
                    )
                    
                    if task_result.compression_ratio < 1.0:
                        detailed["tasks_summary"] = task_result.summary
                        meta.compression_applied = True
            
            meta.tier2_tokens = TokenCounter.count_tokens(str(detailed))
        
        # Build historical context if needed
        historical = None
        if analysis.needs_tier3:
            historical = await self.build_historical_context(campaign_id)
            meta.tier3_tokens = TokenCounter.count_tokens(str(historical))
        
        # Calculate total
        meta.total_tokens = meta.tier1_tokens + meta.tier2_tokens + meta.tier3_tokens
        
        return CampaignContext(
            core=core,
            detailed=detailed,
            historical=historical,
            meta=meta,
        )


# Fix missing import
from sqlalchemy import Integer
