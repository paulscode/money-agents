"""Campaign Learning Service - AI-powered learning from campaign executions.

This service implements Phase 5: Agent Intelligence features:
1. Pattern Discovery: Identify successful execution patterns
2. Lesson Learning: Capture and apply lessons from failures
3. Plan Revision: Enable dynamic plan modifications
4. Proactive Suggestions: Generate optimization suggestions

The goal is to make agents smarter over time by learning from
past campaigns and proactively helping users.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional, List, Dict, Any, Tuple
from uuid import UUID, uuid4

from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Campaign, CampaignTask, TaskStream, Proposal,
    CampaignPattern, CampaignLesson, PlanRevision, ProactiveSuggestion,
    PatternType, PatternStatus, LessonCategory, RevisionTrigger,
    SuggestionType, SuggestionStatus,
    TaskStatus, TaskStreamStatus, CampaignStatus
)
from app.services.llm_service import LLMService, LLMMessage

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class PatternMatch:
    """A pattern that matches the current situation."""
    pattern: CampaignPattern
    relevance_score: float  # 0-1, how relevant is this pattern
    suggested_application: str  # How to apply this pattern


@dataclass
class LessonWarning:
    """A warning based on a past lesson."""
    lesson: CampaignLesson
    warning_message: str
    prevention_actions: List[str]
    urgency: str  # low/medium/high


@dataclass
class RevisionRecommendation:
    """A recommended plan revision."""
    trigger: RevisionTrigger
    reason: str
    changes: Dict[str, Any]  # Proposed changes
    expected_benefit: str
    risk_level: str  # low/medium/high


# =============================================================================
# Campaign Learning Service
# =============================================================================

class CampaignLearningService:
    """
    Service for campaign learning and intelligence.
    
    Enables agents to learn from past campaigns and make
    intelligent suggestions during execution.
    """
    
    def __init__(
        self, 
        db: AsyncSession, 
        llm_service: Optional[LLMService] = None
    ):
        self.db = db
        self.llm_service = llm_service or LLMService()
    
    # =========================================================================
    # Pattern Discovery
    # =========================================================================
    
    async def discover_patterns_from_campaign(
        self,
        campaign_id: UUID,
    ) -> List[CampaignPattern]:
        """
        Analyze a completed campaign and extract successful patterns.
        
        Called after a campaign completes successfully to learn from it.
        """
        # Get campaign with all related data
        result = await self.db.execute(
            select(Campaign)
            .options(
                selectinload(Campaign.task_streams).selectinload(TaskStream.tasks),
                selectinload(Campaign.proposal)
            )
            .where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            logger.warning(f"Campaign {campaign_id} not found for pattern discovery")
            return []
        
        if campaign.status != CampaignStatus.COMPLETED:
            logger.info(f"Campaign {campaign_id} not completed, skipping pattern discovery")
            return []
        
        discovered_patterns = []
        
        # 1. Execution Sequence Patterns
        execution_pattern = await self._discover_execution_pattern(campaign)
        if execution_pattern:
            discovered_patterns.append(execution_pattern)
        
        # 2. Tool Combination Patterns
        tool_patterns = await self._discover_tool_patterns(campaign)
        discovered_patterns.extend(tool_patterns)
        
        # 3. Input Collection Patterns (if inputs were gathered efficiently)
        input_pattern = await self._discover_input_pattern(campaign)
        if input_pattern:
            discovered_patterns.append(input_pattern)
        
        # Save discovered patterns
        for pattern in discovered_patterns:
            self.db.add(pattern)
        
        await self.db.flush()
        
        logger.info(f"Discovered {len(discovered_patterns)} patterns from campaign {campaign_id}")
        return discovered_patterns
    
    async def _discover_execution_pattern(
        self,
        campaign: Campaign
    ) -> Optional[CampaignPattern]:
        """Discover execution sequence patterns from successful streams."""
        
        # Get successful streams
        successful_streams = [
            s for s in campaign.task_streams
            if s.status == TaskStreamStatus.COMPLETED
        ]
        
        if not successful_streams:
            return None
        
        # Build execution sequence data
        sequence_data = {
            "streams": [],
            "total_duration_minutes": 0,
            "parallelization_used": campaign.streams_parallel_execution,
        }
        
        for stream in successful_streams:
            stream_data = {
                "name": stream.name,
                "task_count": stream.tasks_total,
                "tasks": [
                    {
                        "name": t.name,
                        "task_type": t.task_type.value if t.task_type else None,
                        "tool_slug": t.tool_slug,
                        "duration_ms": t.duration_ms,
                    }
                    for t in stream.tasks
                    if t.status == TaskStatus.COMPLETED
                ]
            }
            sequence_data["streams"].append(stream_data)
            
            if stream.started_at and stream.completed_at:
                duration = (stream.completed_at - stream.started_at).total_seconds() / 60
                sequence_data["total_duration_minutes"] += duration
        
        # Create pattern
        pattern = CampaignPattern(
            name=f"Execution Pattern: {campaign.proposal.title[:50]}",
            description=f"Successful execution sequence from campaign for '{campaign.proposal.title}'",
            pattern_type=PatternType.EXECUTION_SEQUENCE,
            status=PatternStatus.EXPERIMENTAL,
            confidence_score=0.6,
            pattern_data=sequence_data,
            applicability_conditions={
                "proposal_type": campaign.proposal.tags.get("type") if campaign.proposal.tags else None,
                "budget_range": [
                    float(campaign.budget_allocated) * 0.5,
                    float(campaign.budget_allocated) * 1.5
                ] if campaign.budget_allocated else None,
            },
            source_campaign_id=campaign.id,
            user_id=campaign.user_id,
            discovered_by_agent="campaign_manager",
            tags=campaign.proposal.tags.get("tags", []) if campaign.proposal.tags else [],
        )
        
        return pattern
    
    async def _discover_tool_patterns(
        self,
        campaign: Campaign
    ) -> List[CampaignPattern]:
        """Discover effective tool combinations."""
        patterns = []
        
        # Group tasks by stream and find tool sequences
        for stream in campaign.task_streams:
            if stream.status != TaskStreamStatus.COMPLETED:
                continue
            
            tools_used = []
            for task in stream.tasks:
                if task.tool_slug and task.status == TaskStatus.COMPLETED:
                    tools_used.append({
                        "tool_slug": task.tool_slug,
                        "success": task.status == TaskStatus.COMPLETED,
                        "duration_ms": task.duration_ms,
                    })
            
            if len(tools_used) >= 2:
                # Found a tool combination pattern
                pattern = CampaignPattern(
                    name=f"Tool Combo: {stream.name}",
                    description=f"Effective tool combination from '{stream.name}' stream",
                    pattern_type=PatternType.TOOL_COMBINATION,
                    status=PatternStatus.EXPERIMENTAL,
                    confidence_score=0.5,
                    pattern_data={
                        "tools": tools_used,
                        "stream_purpose": stream.description,
                        "total_duration_ms": sum(t.get("duration_ms", 0) or 0 for t in tools_used),
                    },
                    applicability_conditions={
                        "stream_type": stream.name.lower().replace(" ", "_"),
                    },
                    source_campaign_id=campaign.id,
                    user_id=campaign.user_id,
                    discovered_by_agent="campaign_manager",
                )
                patterns.append(pattern)
        
        return patterns
    
    async def _discover_input_pattern(
        self,
        campaign: Campaign
    ) -> Optional[CampaignPattern]:
        """Discover input collection patterns."""
        
        # Get input requests that were provided
        from app.models import UserInputRequest, InputStatus
        
        result = await self.db.execute(
            select(UserInputRequest)
            .where(
                UserInputRequest.campaign_id == campaign.id,
                UserInputRequest.status == InputStatus.PROVIDED
            )
            .order_by(UserInputRequest.provided_at)
        )
        inputs = list(result.scalars().all())
        
        if len(inputs) < 2:
            return None
        
        # Analyze input collection efficiency
        input_data = {
            "inputs": [
                {
                    "key": inp.key,
                    "input_type": inp.input_type.value if inp.input_type else None,
                    "priority": inp.priority.value if inp.priority else None,
                    "wait_time_minutes": (
                        (inp.provided_at - inp.created_at).total_seconds() / 60
                        if inp.provided_at and inp.created_at else None
                    ),
                }
                for inp in inputs
            ],
            "total_inputs": len(inputs),
            "blocking_inputs": sum(1 for i in inputs if i.priority and i.priority.value == "blocking"),
        }
        
        # Only create pattern if inputs were handled efficiently (low wait times)
        avg_wait = sum(
            i["wait_time_minutes"] or 0 for i in input_data["inputs"]
        ) / len(input_data["inputs"])
        
        if avg_wait > 60:  # More than 1 hour average wait - not a good pattern
            return None
        
        pattern = CampaignPattern(
            name=f"Input Collection: {campaign.proposal.title[:40]}",
            description="Efficient input collection sequence",
            pattern_type=PatternType.INPUT_COLLECTION,
            status=PatternStatus.EXPERIMENTAL,
            confidence_score=0.55,
            pattern_data=input_data,
            applicability_conditions={
                "input_count_range": [len(inputs) - 2, len(inputs) + 2],
            },
            source_campaign_id=campaign.id,
            user_id=campaign.user_id,
            discovered_by_agent="campaign_manager",
        )
        
        return pattern
    
    async def find_applicable_patterns(
        self,
        proposal: Proposal,
        user_id: UUID,
        limit: int = 5,
    ) -> List[PatternMatch]:
        """
        Find patterns that might be applicable to a new proposal.
        
        Called when initializing a campaign to leverage past learnings.
        """
        # Get active patterns for this user (and global patterns)
        result = await self.db.execute(
            select(CampaignPattern)
            .where(
                CampaignPattern.status == PatternStatus.ACTIVE,
                or_(
                    CampaignPattern.user_id == user_id,
                    CampaignPattern.is_global == True
                )
            )
            .order_by(desc(CampaignPattern.confidence_score))
            .limit(limit * 2)  # Get more to filter
        )
        patterns = list(result.scalars().all())
        
        matches = []
        
        for pattern in patterns:
            relevance = self._calculate_pattern_relevance(pattern, proposal)
            if relevance > 0.3:  # Minimum threshold
                matches.append(PatternMatch(
                    pattern=pattern,
                    relevance_score=relevance,
                    suggested_application=self._generate_application_suggestion(pattern, proposal),
                ))
        
        # Sort by relevance and return top matches
        matches.sort(key=lambda m: m.relevance_score, reverse=True)
        return matches[:limit]
    
    def _calculate_pattern_relevance(
        self,
        pattern: CampaignPattern,
        proposal: Proposal
    ) -> float:
        """Calculate how relevant a pattern is to a proposal."""
        score = 0.0
        conditions = pattern.applicability_conditions or {}
        
        # Check budget range
        if "budget_range" in conditions and conditions["budget_range"]:
            budget_min, budget_max = conditions["budget_range"]
            if budget_min <= float(proposal.initial_budget) <= budget_max:
                score += 0.3
        
        # Check proposal type
        if "proposal_type" in conditions:
            if proposal.tags and proposal.tags.get("type") == conditions["proposal_type"]:
                score += 0.3
        
        # Check tags overlap
        pattern_tags = set(pattern.tags or [])
        proposal_tags = set(proposal.tags.get("tags", []) if proposal.tags else [])
        if pattern_tags and proposal_tags:
            overlap = len(pattern_tags & proposal_tags)
            if overlap > 0:
                score += min(0.2, overlap * 0.1)
        
        # Boost by pattern confidence and success rate
        confidence = pattern.confidence_score or 0.5
        score *= (0.5 + confidence * 0.5)
        times_applied = pattern.times_applied or 0
        if times_applied > 0:
            score *= (0.5 + pattern.success_rate * 0.5)
        
        return min(1.0, score)
    
    def _generate_application_suggestion(
        self,
        pattern: CampaignPattern,
        proposal: Proposal
    ) -> str:
        """Generate a suggestion for how to apply a pattern."""
        if pattern.pattern_type == PatternType.EXECUTION_SEQUENCE:
            streams = pattern.pattern_data.get("streams", [])
            return f"Consider using a similar {len(streams)}-stream execution approach"
        
        elif pattern.pattern_type == PatternType.TOOL_COMBINATION:
            tools = pattern.pattern_data.get("tools", [])
            tool_names = [t.get("tool_slug") for t in tools[:3]]
            return f"This tool combination worked well: {', '.join(tool_names)}"
        
        elif pattern.pattern_type == PatternType.INPUT_COLLECTION:
            total = pattern.pattern_data.get("total_inputs", 0)
            return f"Organize input collection similarly ({total} inputs consolidated)"
        
        return "Apply this pattern to improve execution"
    
    # =========================================================================
    # Lesson Learning
    # =========================================================================
    
    async def record_lesson(
        self,
        campaign_id: UUID,
        title: str,
        description: str,
        category: LessonCategory,
        trigger_event: str,
        context: Dict[str, Any],
        prevention_steps: List[str],
        impact_severity: str = "medium",
        budget_impact: Optional[float] = None,
        time_impact_minutes: Optional[int] = None,
        task_id: Optional[UUID] = None,
    ) -> CampaignLesson:
        """
        Record a lesson learned from a campaign issue.
        
        Called when something goes wrong that should be avoided in future.
        """
        # Get campaign to get user_id
        result = await self.db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            raise ValueError(f"Campaign {campaign_id} not found")
        
        lesson = CampaignLesson(
            title=title,
            description=description,
            category=category,
            context=context,
            trigger_event=trigger_event,
            impact_severity=impact_severity,
            budget_impact=budget_impact,
            time_impact_minutes=time_impact_minutes,
            prevention_steps=prevention_steps,
            detection_signals=[],
            source_campaign_id=campaign_id,
            source_task_id=task_id,
            user_id=campaign.user_id,
        )
        
        self.db.add(lesson)
        await self.db.flush()
        
        logger.info(f"Recorded lesson: {title} for campaign {campaign_id}")
        return lesson
    
    async def check_for_warnings(
        self,
        campaign: Campaign,
        current_state: Dict[str, Any],
    ) -> List[LessonWarning]:
        """
        Check if any learned lessons apply to the current situation.
        
        Called during campaign execution to provide early warnings.
        """
        # Get lessons for this user
        result = await self.db.execute(
            select(CampaignLesson)
            .where(CampaignLesson.user_id == campaign.user_id)
            .order_by(desc(CampaignLesson.times_applied))
            .limit(50)  # Check most frequently applied lessons
        )
        lessons = list(result.scalars().all())
        
        warnings = []
        
        for lesson in lessons:
            warning = self._check_lesson_applies(lesson, campaign, current_state)
            if warning:
                warnings.append(warning)
                # Mark lesson as applied
                lesson.times_applied += 1
        
        await self.db.flush()
        
        return warnings
    
    def _check_lesson_applies(
        self,
        lesson: CampaignLesson,
        campaign: Campaign,
        current_state: Dict[str, Any]
    ) -> Optional[LessonWarning]:
        """Check if a lesson's warning signs are present."""
        
        # Check detection signals
        signals_detected = 0
        for signal in lesson.detection_signals:
            signal_type = signal.get("type")
            signal_value = signal.get("value")
            
            if signal_type == "budget_percentage" and campaign.budget_allocated:
                current_pct = float(campaign.budget_spent) / float(campaign.budget_allocated)
                if current_pct >= signal_value:
                    signals_detected += 1
            
            elif signal_type == "task_failure_rate":
                total = current_state.get("tasks_total", 0)
                failed = current_state.get("tasks_failed", 0)
                if total > 0 and (failed / total) >= signal_value:
                    signals_detected += 1
            
            elif signal_type == "stream_blocked":
                blocked = current_state.get("streams_blocked", 0)
                if blocked >= signal_value:
                    signals_detected += 1
        
        # If detection signals exist and some were detected
        if lesson.detection_signals and signals_detected > 0:
            urgency = "high" if signals_detected >= 2 else "medium"
            return LessonWarning(
                lesson=lesson,
                warning_message=f"Warning: {lesson.title} - {lesson.description[:100]}",
                prevention_actions=lesson.prevention_steps,
                urgency=urgency,
            )
        
        # Check context similarity (simplified)
        context = lesson.context
        if context.get("category") == lesson.category.value:
            # Similar context - provide a gentle warning
            return LessonWarning(
                lesson=lesson,
                warning_message=f"Note: Previous campaign had issue: {lesson.title}",
                prevention_actions=lesson.prevention_steps[:2],
                urgency="low",
            )
        
        return None
    
    # =========================================================================
    # Plan Revision
    # =========================================================================
    
    async def analyze_for_revision(
        self,
        campaign: Campaign,
        trigger: RevisionTrigger,
        trigger_details: str,
    ) -> Optional[RevisionRecommendation]:
        """
        Analyze if a plan revision is recommended.
        
        Called when something happens that might warrant plan changes.
        """
        # Get current state
        result = await self.db.execute(
            select(Campaign)
            .options(
                selectinload(Campaign.task_streams).selectinload(TaskStream.tasks)
            )
            .where(Campaign.id == campaign.id)
        )
        campaign = result.scalar_one()
        
        # Build context for LLM analysis
        context = {
            "trigger": trigger.value,
            "trigger_details": trigger_details,
            "current_status": campaign.status.value,
            "budget_spent_pct": (
                float(campaign.budget_spent) / float(campaign.budget_allocated) * 100
                if campaign.budget_allocated else 0
            ),
            "tasks_completed": campaign.tasks_completed,
            "tasks_total": campaign.tasks_total,
            "streams": [
                {
                    "name": s.name,
                    "status": s.status.value,
                    "tasks_completed": s.tasks_completed,
                    "tasks_total": s.tasks_total,
                    "blocked_reasons": s.blocking_reasons,
                }
                for s in campaign.task_streams
            ],
        }
        
        # Use LLM to analyze if revision is needed
        prompt = self._build_revision_analysis_prompt(context)
        
        messages = [
            LLMMessage(
                role="system",
                content="You are an expert campaign execution advisor. Analyze situations and recommend plan revisions when beneficial."
            ),
            LLMMessage(role="user", content=prompt)
        ]
        
        try:
            response = await self.llm_service.generate(
                messages=messages,
                model="fast",
                max_tokens=1500
            )
            
            # Track LLM usage
            from app.services.llm_usage_service import llm_usage_service
            from app.models.llm_usage import LLMUsageSource
            await llm_usage_service.track(
                db=self.db,
                source=LLMUsageSource.CAMPAIGN,
                provider=response.provider,
                model=response.model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cost_usd=response.cost_usd,
                latency_ms=response.latency_ms,
                meta_data={"action": "revision_analysis"},
            )
            
            recommendation = self._parse_revision_recommendation(response.content, trigger)
            return recommendation
            
        except Exception as e:
            logger.error(f"Failed to analyze for revision: {e}")
            return None
    
    def _build_revision_analysis_prompt(self, context: Dict[str, Any]) -> str:
        """Build prompt for revision analysis."""
        return f"""Analyze this campaign situation and determine if a plan revision is recommended.

## Current Situation

**Trigger:** {context['trigger']}
**Details:** {context['trigger_details']}

**Campaign Status:** {context['current_status']}
**Budget Used:** {context['budget_spent_pct']:.1f}%
**Tasks:** {context['tasks_completed']} / {context['tasks_total']} completed

**Streams:**
{chr(10).join(f"- {s['name']}: {s['status']} ({s['tasks_completed']}/{s['tasks_total']} tasks)" + (f" - Blocked: {s['blocked_reasons']}" if s['blocked_reasons'] else "") for s in context['streams'])}

## Analysis Required

1. Should we revise the execution plan? (yes/no)
2. If yes, what changes do you recommend?
3. What's the expected benefit?
4. What's the risk level of the revision?

Respond in JSON format:
```json
{{
  "should_revise": true/false,
  "reason": "why or why not",
  "changes": {{
    "add_tasks": [...],
    "remove_tasks": [...],
    "reorder_streams": [...],
    "modify_dependencies": [...]
  }},
  "expected_benefit": "description",
  "risk_level": "low/medium/high"
}}
```"""
    
    def _parse_revision_recommendation(
        self,
        response: str,
        trigger: RevisionTrigger
    ) -> Optional[RevisionRecommendation]:
        """Parse LLM response into a revision recommendation."""
        import json5
        
        try:
            # Extract JSON from response
            json_match = None
            if "```json" in response:
                start = response.find("```json") + 7
                end = response.find("```", start)
                if end > start:
                    json_match = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end > start:
                    json_match = response[start:end].strip()
            elif response.strip().startswith("{"):
                json_match = response.strip()
            
            if json_match:
                data = json5.loads(json_match)
                
                if not data.get("should_revise"):
                    return None
                
                return RevisionRecommendation(
                    trigger=trigger,
                    reason=data.get("reason", "Analysis suggests revision would help"),
                    changes=data.get("changes", {}),
                    expected_benefit=data.get("expected_benefit", "Unknown"),
                    risk_level=data.get("risk_level", "medium"),
                )
        
        except Exception as e:
            logger.warning(f"Failed to parse revision recommendation: {e}")
        
        return None
    
    async def create_revision(
        self,
        campaign: Campaign,
        recommendation: RevisionRecommendation,
        initiated_by: str = "agent",
        approved_by_user: bool = False,
    ) -> PlanRevision:
        """
        Create a plan revision record.
        
        Captures the before/after state for learning purposes.
        """
        # Get current revision number
        result = await self.db.execute(
            select(func.max(PlanRevision.revision_number))
            .where(PlanRevision.campaign_id == campaign.id)
        )
        max_rev = result.scalar() or 0
        
        # Capture current plan state
        plan_before = {
            "execution_plan": campaign.execution_plan,
            "tasks_total": campaign.tasks_total,
            "status": campaign.status.value,
        }
        
        # Apply changes (simplified - in real impl would modify actual plan)
        plan_after = {
            **plan_before,
            "revision_changes": recommendation.changes,
        }
        
        # Count changes
        changes = recommendation.changes
        tasks_added = len(changes.get("add_tasks", []))
        tasks_removed = len(changes.get("remove_tasks", []))
        tasks_modified = len(changes.get("modify_dependencies", []))
        streams_added = len([c for c in changes.get("reorder_streams", []) if c.get("action") == "add"])
        streams_removed = len([c for c in changes.get("reorder_streams", []) if c.get("action") == "remove"])
        
        revision = PlanRevision(
            campaign_id=campaign.id,
            revision_number=max_rev + 1,
            trigger=recommendation.trigger,
            trigger_details=recommendation.reason,
            plan_before=plan_before,
            plan_after=plan_after,
            changes_summary=f"Added {tasks_added} tasks, removed {tasks_removed}, modified {tasks_modified}",
            tasks_added=tasks_added,
            tasks_removed=tasks_removed,
            tasks_modified=tasks_modified,
            streams_added=streams_added,
            streams_removed=streams_removed,
            reasoning=recommendation.reason,
            expected_improvement=recommendation.expected_benefit,
            initiated_by=initiated_by,
            approved_by_user=approved_by_user,
        )
        
        self.db.add(revision)
        await self.db.flush()
        
        logger.info(f"Created plan revision {revision.revision_number} for campaign {campaign.id}")
        return revision
    
    async def assess_revision_outcome(
        self,
        revision_id: UUID,
        success: bool,
        notes: str,
    ) -> None:
        """
        Record the outcome of a plan revision.
        
        Called after enough time has passed to evaluate the revision.
        """
        result = await self.db.execute(
            select(PlanRevision).where(PlanRevision.id == revision_id)
        )
        revision = result.scalar_one_or_none()
        
        if not revision:
            logger.warning(f"Revision {revision_id} not found")
            return
        
        revision.outcome_assessed = True
        revision.outcome_success = success
        revision.outcome_notes = notes
        revision.outcome_assessed_at = utc_now()
        
        await self.db.flush()
        
        logger.info(f"Assessed revision {revision_id}: success={success}")
    
    # =========================================================================
    # Proactive Suggestions
    # =========================================================================
    
    async def generate_suggestions(
        self,
        campaign: Campaign,
        current_state: Dict[str, Any],
    ) -> List[ProactiveSuggestion]:
        """
        Generate proactive suggestions for the campaign.
        
        Called periodically during execution to provide optimization ideas.
        """
        suggestions = []
        
        # 1. Check for optimization opportunities
        optimization = await self._check_optimization_opportunity(campaign, current_state)
        if optimization:
            suggestions.append(optimization)
        
        # 2. Check for warnings based on lessons
        warnings = await self.check_for_warnings(campaign, current_state)
        for warning in warnings[:2]:  # Limit to 2 warning suggestions
            suggestion = ProactiveSuggestion(
                campaign_id=campaign.id,
                suggestion_type=SuggestionType.WARNING,
                title=f"Warning: {warning.lesson.title}",
                description=warning.warning_message,
                status=SuggestionStatus.PENDING,
                urgency=warning.urgency,
                confidence=0.7,
                evidence={
                    "lesson_id": str(warning.lesson.id),
                    "category": warning.lesson.category.value,
                },
                based_on_lessons=[str(warning.lesson.id)],
                recommended_action={
                    "type": "prevention",
                    "steps": warning.prevention_actions,
                },
                can_auto_apply=False,
            )
            suggestions.append(suggestion)
        
        # 3. Check for cost savings
        cost_saving = await self._check_cost_saving(campaign, current_state)
        if cost_saving:
            suggestions.append(cost_saving)
        
        # Save suggestions
        for suggestion in suggestions:
            self.db.add(suggestion)
        
        await self.db.flush()
        
        return suggestions
    
    async def _check_optimization_opportunity(
        self,
        campaign: Campaign,
        current_state: Dict[str, Any],
    ) -> Optional[ProactiveSuggestion]:
        """Check for optimization opportunities."""
        
        # Check if some streams could be parallelized
        if not campaign.streams_parallel_execution:
            return ProactiveSuggestion(
                campaign_id=campaign.id,
                suggestion_type=SuggestionType.TIME_SAVING,
                title="Enable Parallel Stream Execution",
                description="Your campaign has multiple independent streams that could run in parallel, potentially reducing execution time significantly.",
                status=SuggestionStatus.PENDING,
                urgency="medium",
                confidence=0.8,
                evidence={
                    "streams_count": current_state.get("total_streams", 0),
                    "independent_streams": current_state.get("ready_streams", 0),
                },
                recommended_action={
                    "type": "enable_parallelization",
                    "field": "streams_parallel_execution",
                    "value": True,
                },
                estimated_benefit="Could reduce execution time by up to 50%",
                can_auto_apply=True,
                auto_apply_conditions={
                    "no_shared_resources": True,
                },
            )
        
        return None
    
    async def _check_cost_saving(
        self,
        campaign: Campaign,
        current_state: Dict[str, Any],
    ) -> Optional[ProactiveSuggestion]:
        """Check for cost saving opportunities."""
        
        # Check budget burn rate
        if campaign.budget_allocated and campaign.start_date:
            days_elapsed = (utc_now() - ensure_utc(campaign.start_date)).days or 1
            daily_burn = float(campaign.budget_spent) / days_elapsed
            
            # Estimate remaining days based on progress
            progress_pct = current_state.get("overall_progress_pct", 0) or 1
            estimated_remaining_days = (100 - progress_pct) / (progress_pct / days_elapsed) if progress_pct > 0 else 30
            
            projected_total = daily_burn * (days_elapsed + estimated_remaining_days)
            
            if projected_total > float(campaign.budget_allocated) * 1.2:
                return ProactiveSuggestion(
                    campaign_id=campaign.id,
                    suggestion_type=SuggestionType.WARNING,
                    title="Budget Overrun Risk",
                    description=f"At current spending rate, campaign may exceed budget by {((projected_total / float(campaign.budget_allocated)) - 1) * 100:.0f}%",
                    status=SuggestionStatus.PENDING,
                    urgency="high",
                    confidence=0.75,
                    evidence={
                        "daily_burn_rate": daily_burn,
                        "projected_total": projected_total,
                        "budget_allocated": float(campaign.budget_allocated),
                    },
                    recommended_action={
                        "type": "review_spending",
                        "suggestions": [
                            "Review task necessity",
                            "Consider cheaper tool alternatives",
                            "Reduce parallel execution to pace spending",
                        ],
                    },
                    can_auto_apply=False,
                )
        
        return None
    
    async def update_suggestion_status(
        self,
        suggestion_id: UUID,
        status: SuggestionStatus,
        user_feedback: Optional[str] = None,
    ) -> None:
        """Update the status of a suggestion after user response."""
        result = await self.db.execute(
            select(ProactiveSuggestion).where(ProactiveSuggestion.id == suggestion_id)
        )
        suggestion = result.scalar_one_or_none()
        
        if not suggestion:
            logger.warning(f"Suggestion {suggestion_id} not found")
            return
        
        suggestion.status = status
        suggestion.user_response_at = utc_now()
        if user_feedback:
            suggestion.user_feedback = user_feedback
        
        await self.db.flush()
        
        logger.info(f"Updated suggestion {suggestion_id} to {status.value}")
    
    async def auto_apply_suggestion(
        self,
        suggestion: ProactiveSuggestion,
    ) -> bool:
        """
        Automatically apply a suggestion if allowed.
        
        Returns True if applied successfully.
        """
        if not suggestion.can_auto_apply:
            return False
        
        action = suggestion.recommended_action
        if not action:
            return False
        
        action_type = action.get("type")
        
        if action_type == "enable_parallelization":
            # Get campaign and update
            result = await self.db.execute(
                select(Campaign).where(Campaign.id == suggestion.campaign_id)
            )
            campaign = result.scalar_one_or_none()
            
            if campaign:
                campaign.streams_parallel_execution = True
                suggestion.status = SuggestionStatus.AUTO_APPLIED
                await self.db.flush()
                
                logger.info(f"Auto-applied suggestion {suggestion.id}: enabled parallelization")
                return True
        
        return False
