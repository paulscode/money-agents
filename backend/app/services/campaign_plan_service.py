"""Campaign Plan Service for multi-stream execution planning.

This service uses an LLM to analyze a proposal and generate an optimized
execution plan with parallel streams, dependencies, and input requirements.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Campaign, Proposal, TaskStream, CampaignTask, UserInputRequest,
    TaskStreamStatus, TaskStatus, TaskType, InputType, InputPriority, InputStatus
)
from app.services.llm_service import LLMService, LLMMessage
from app.services.prompt_injection_guard import (
    get_security_preamble,
    sanitize_external_content,
    wrap_external_content,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class InputRequirement:
    """Input requirement identified during planning."""
    key: str
    input_type: str
    title: str
    description: str
    priority: str
    options: Optional[List[str]] = None
    default_value: Optional[str] = None


@dataclass
class TaskDefinition:
    """Task definition from execution plan."""
    name: str
    description: str
    task_type: str
    tool_slug: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    llm_prompt: Optional[str] = None
    depends_on_tasks: Optional[List[str]] = None
    depends_on_inputs: Optional[List[str]] = None
    estimated_duration_minutes: int = 5
    is_critical: bool = True


@dataclass
class StreamDefinition:
    """Stream definition from execution plan."""
    name: str
    description: str
    tasks: List[TaskDefinition]
    depends_on_streams: Optional[List[str]] = None
    requires_inputs: Optional[List[str]] = None
    can_run_parallel: bool = False
    max_concurrent: int = 1
    estimated_duration_minutes: int = 60


@dataclass
class ExecutionPlan:
    """Complete execution plan for a campaign."""
    streams: List[StreamDefinition]
    input_requirements: List[InputRequirement]
    estimated_total_duration_minutes: int
    parallelization_factor: float  # 1.0 = sequential, 2.0 = can run 2x faster with parallel


# =============================================================================
# Prompt Templates
# =============================================================================

_PLAN_SECURITY_PREAMBLE = get_security_preamble("none")

PLAN_GENERATION_SYSTEM_PROMPT = _PLAN_SECURITY_PREAMBLE + """

You are an expert campaign execution planner. Your job is to analyze marketing and business proposals and create detailed, parallelized execution plans.

KEY OBJECTIVES:
1. MAXIMIZE PARALLEL EXECUTION - Break work into independent streams that can run simultaneously
2. MINIMIZE BLOCKING - Identify user inputs needed early, batch similar inputs together  
3. SMART DEPENDENCIES - Only create dependencies where truly necessary
4. REALISTIC ESTIMATES - Provide accurate time estimates for each task

STREAM TYPES to consider:
- "research" - Market research, competitor analysis, audience research
- "content_creation" - Copy, images, landing pages
- "platform_setup" - Account creation, API connections, tracking setup
- "asset_preparation" - Creative assets, ad formats
- "campaign_execution" - Actually running/launching campaigns
- "monitoring_optimization" - Performance tracking, A/B tests

TASK TYPES:
- "tool_execution" - Execute a specific tool (provide tool_slug and params)
- "llm_reasoning" - LLM analysis/decision (provide llm_prompt)
- "user_input" - Wait for user input (reference by input key)
- "checkpoint" - Milestone marker
- "parallel_gate" - Wait for multiple parallel tasks to complete

INPUT TYPES:
- "credentials" - API keys, passwords, tokens
- "text" - Free-form text input
- "confirmation" - Yes/no approval
- "selection" - Choose from options
- "file" - File upload
- "budget_approval" - Approve spending
- "content" - User-provided copy, prompts, branding

OUTPUT FORMAT: Return ONLY valid JSON matching this schema:
{
  "streams": [
    {
      "name": "stream_name",
      "description": "What this stream accomplishes",
      "depends_on_streams": ["other_stream_name"],  // optional
      "requires_inputs": ["input_key"],  // optional
      "can_run_parallel": true,  // can tasks in this stream run in parallel?
      "max_concurrent": 2,  // if parallel, how many at once
      "estimated_duration_minutes": 120,
      "tasks": [
        {
          "name": "Task name",
          "description": "What this task does",
          "task_type": "tool_execution",
          "tool_slug": "web_search",  // if tool_execution
          "tool_params": {"query": "..."},  // if tool_execution
          "llm_prompt": "...",  // if llm_reasoning
          "depends_on_tasks": ["Previous task name"],  // optional, within stream
          "depends_on_inputs": ["input_key"],  // optional
          "estimated_duration_minutes": 10,
          "is_critical": true  // false = can skip if it fails
        }
      ]
    }
  ],
  "input_requirements": [
    {
      "key": "unique_key",
      "input_type": "credentials",
      "title": "User-friendly title",
      "description": "Detailed description of what's needed and why",
      "priority": "blocking",  // blocking, high, medium, low
      "options": ["opt1", "opt2"],  // for selection type
      "default_value": "..."  // if available
    }
  ],
  "estimated_total_duration_minutes": 480,
  "parallelization_factor": 1.8
}"""


PLAN_GENERATION_USER_PROMPT = """Analyze this proposal and create an optimized execution plan:

## PROPOSAL DETAILS
Title: {title}
Description: {description}

Strategy: {strategy}

Success Metrics: {success_metrics}

Budget: ${budget}
Timeline: {timeline}

## AVAILABLE TOOLS
{available_tools}

## GUIDELINES
1. Identify ALL user inputs needed upfront - credentials, approvals, content
2. Create PARALLEL streams where possible - research can happen while content is created
3. Put credential/API key inputs as BLOCKING priority - nothing can run without them
4. Group similar tasks into logical streams
5. Be specific about tool usage - use actual tool slugs when appropriate
6. Estimate times realistically - account for API rate limits and processing time

Create the execution plan:"""


# =============================================================================
# Service Class
# =============================================================================

class CampaignPlanService:
    """Service for generating and managing campaign execution plans."""
    
    def __init__(self, db: AsyncSession, llm_service: Optional[LLMService] = None):
        self.db = db
        self.llm_service = llm_service or LLMService()
    
    async def generate_execution_plan(
        self,
        proposal: Proposal,
        available_tools: List[Dict[str, Any]],
        existing_credentials: Optional[List[str]] = None
    ) -> ExecutionPlan:
        """
        Generate an execution plan from a proposal using LLM analysis.
        
        Args:
            proposal: The approved proposal to plan execution for
            available_tools: List of available tools with their capabilities
            existing_credentials: List of credential types already available
            
        Returns:
            ExecutionPlan with streams, tasks, and input requirements
        """
        # Format tool list for prompt
        tools_text = self._format_tools_for_prompt(available_tools)
        
        # Sanitize proposal fields to prevent multi-hop injection
        # (web search → opportunity → proposal → plan generation)
        san_title, _ = sanitize_external_content(
            proposal.title or "Untitled", source="proposal"
        )
        san_description, _ = sanitize_external_content(
            proposal.detailed_description or "No description", source="proposal"
        )
        raw_strategy = json.dumps(proposal.implementation_timeline, indent=2) if proposal.implementation_timeline else "Not specified"
        san_strategy, _ = sanitize_external_content(raw_strategy, source="proposal")
        raw_metrics = json.dumps(proposal.success_criteria, indent=2) if proposal.success_criteria else "Not specified"
        san_metrics, _ = sanitize_external_content(raw_metrics, source="proposal")
        raw_timeline = json.dumps(proposal.implementation_timeline, indent=2) if proposal.implementation_timeline else "Unknown"
        san_timeline, _ = sanitize_external_content(raw_timeline, source="proposal")
        
        # Format proposal details - using correct Proposal model attributes
        user_prompt = PLAN_GENERATION_USER_PROMPT.format(
            title=san_title,
            description=wrap_external_content(san_description, source="proposal"),
            strategy=wrap_external_content(san_strategy, source="proposal"),
            success_metrics=wrap_external_content(san_metrics, source="proposal"),
            budget=proposal.initial_budget or "Not specified",
            timeline=wrap_external_content(san_timeline, source="proposal"),
            available_tools=tools_text
        )
        
        # Call LLM
        messages = [
            LLMMessage(role="system", content=PLAN_GENERATION_SYSTEM_PROMPT),
            LLMMessage(role="user", content=user_prompt)
        ]
        
        response = await self.llm_service.generate(
            messages=messages,
            temperature=0.7,
            max_tokens=8000,
            model="reasoning"  # Use advanced model for complex planning
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
            meta_data={"action": "generate_execution_plan"},
        )
        
        # Parse response
        try:
            plan_data = json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse execution plan JSON: {e}")
            # Try to extract JSON from response
            plan_data = self._extract_json_from_response(response.content)
        
        # Convert to dataclass structure
        return self._parse_plan_data(plan_data, existing_credentials)
    
    async def create_campaign_streams(
        self,
        campaign: Campaign,
        plan: ExecutionPlan
    ) -> List[TaskStream]:
        """
        Create TaskStream and CampaignTask records from an execution plan.
        
        Args:
            campaign: The campaign to create streams for
            plan: The execution plan
            
        Returns:
            List of created TaskStream objects
        """
        created_streams = []
        stream_name_to_id: Dict[str, UUID] = {}
        task_name_to_id: Dict[str, UUID] = {}
        # Track created tasks locally to avoid lazy loading relationships
        stream_tasks: Dict[int, List[CampaignTask]] = {}
        
        # First pass: create all streams and map names to IDs
        for idx, stream_def in enumerate(plan.streams):
            stream = TaskStream(
                id=uuid4(),
                campaign_id=campaign.id,
                name=stream_def.name,
                description=stream_def.description,
                order_index=idx,
                status=TaskStreamStatus.PENDING,
                can_run_parallel=stream_def.can_run_parallel,
                max_concurrent=stream_def.max_concurrent,
                estimated_duration_minutes=stream_def.estimated_duration_minutes,
                tasks_total=len(stream_def.tasks),
                requires_inputs=stream_def.requires_inputs or []
            )
            self.db.add(stream)
            created_streams.append(stream)
            stream_name_to_id[stream_def.name] = stream.id
            stream_tasks[idx] = []
        
        # Second pass: set stream dependencies (now that we have IDs)
        for idx, stream_def in enumerate(plan.streams):
            if stream_def.depends_on_streams:
                created_streams[idx].depends_on_streams = [
                    str(stream_name_to_id[name]) 
                    for name in stream_def.depends_on_streams 
                    if name in stream_name_to_id
                ]
        
        # Third pass: create tasks for each stream
        for stream_idx, stream_def in enumerate(plan.streams):
            stream = created_streams[stream_idx]
            
            for task_idx, task_def in enumerate(stream_def.tasks):
                task = CampaignTask(
                    id=uuid4(),
                    stream_id=stream.id,
                    campaign_id=campaign.id,
                    name=task_def.name,
                    description=task_def.description,
                    order_index=task_idx,
                    task_type=TaskType(task_def.task_type),
                    tool_slug=task_def.tool_slug,
                    tool_params=task_def.tool_params,
                    llm_prompt=task_def.llm_prompt,
                    estimated_duration_minutes=task_def.estimated_duration_minutes,
                    is_critical=task_def.is_critical,
                    depends_on_inputs=task_def.depends_on_inputs or [],
                    status=TaskStatus.PENDING
                )
                self.db.add(task)
                task_name_to_id[f"{stream_def.name}:{task_def.name}"] = task.id
                stream_tasks[stream_idx].append(task)
        
        # Fourth pass: set task dependencies (using local task list, not lazy-loaded relationship)
        for stream_idx, stream_def in enumerate(plan.streams):
            for task in stream_tasks[stream_idx]:
                task_def = next(
                    (t for t in stream_def.tasks if t.name == task.name),
                    None
                )
                if task_def and task_def.depends_on_tasks:
                    task.depends_on_tasks = [
                        str(task_name_to_id.get(f"{stream_def.name}:{dep_name}"))
                        for dep_name in task_def.depends_on_tasks
                        if f"{stream_def.name}:{dep_name}" in task_name_to_id
                    ]
        
        await self.db.flush()
        return created_streams
    
    async def create_input_requests(
        self,
        campaign: Campaign,
        plan: ExecutionPlan
    ) -> List[UserInputRequest]:
        """
        Create UserInputRequest records from an execution plan.
        
        Args:
            campaign: The campaign
            plan: The execution plan
            
        Returns:
            List of created UserInputRequest objects
        """
        created_requests = []
        
        for req_def in plan.input_requirements:
            # Count blocking impact
            blocking_streams = []
            blocking_tasks = []
            
            for stream in plan.streams:
                if req_def.key in (stream.requires_inputs or []):
                    blocking_streams.append(stream.name)
                for task in stream.tasks:
                    if req_def.key in (task.depends_on_inputs or []):
                        blocking_tasks.append(f"{stream.name}:{task.name}")
            
            input_request = UserInputRequest(
                id=uuid4(),
                campaign_id=campaign.id,
                input_key=req_def.key,
                input_type=InputType(req_def.input_type),
                title=req_def.title,
                description=req_def.description,
                options=req_def.options,
                default_value=req_def.default_value,
                priority=InputPriority(req_def.priority),
                blocking_streams=blocking_streams,
                blocking_tasks=blocking_tasks,
                blocking_count=len(blocking_streams) + len(blocking_tasks),
                status=InputStatus.PENDING
            )
            self.db.add(input_request)
            created_requests.append(input_request)
        
        await self.db.flush()
        return created_requests
    
    async def update_stream_readiness(self, campaign_id: UUID) -> int:
        """
        Update status of all streams based on dependencies.
        
        Marks streams as READY when their dependencies are met.
        
        Returns:
            Number of streams marked ready
        """
        # Get all streams for campaign
        result = await self.db.execute(
            select(TaskStream).where(TaskStream.campaign_id == campaign_id)
        )
        streams = result.scalars().all()
        
        # Get all input requests
        result = await self.db.execute(
            select(UserInputRequest).where(
                UserInputRequest.campaign_id == campaign_id,
                UserInputRequest.status == InputStatus.PENDING
            )
        )
        pending_inputs = {req.input_key for req in result.scalars().all()}
        
        # Build stream status map
        stream_status = {str(s.id): s.status for s in streams}
        stream_by_id = {str(s.id): s for s in streams}
        
        ready_count = 0
        
        for stream in streams:
            if stream.status != TaskStreamStatus.PENDING:
                continue
            
            # Check stream dependencies
            deps_met = True
            blocking_reasons = []
            
            for dep_id in (stream.depends_on_streams or []):
                dep_status = stream_status.get(dep_id)
                if dep_status != TaskStreamStatus.COMPLETED:
                    deps_met = False
                    dep_stream = stream_by_id.get(dep_id)
                    if dep_stream:
                        blocking_reasons.append(f"Waiting for stream: {dep_stream.name}")
            
            # Check input dependencies
            for input_key in (stream.requires_inputs or []):
                if input_key in pending_inputs:
                    deps_met = False
                    blocking_reasons.append(f"Waiting for input: {input_key}")
            
            # Update status
            if deps_met:
                stream.status = TaskStreamStatus.READY
                stream.blocking_reasons = []
                ready_count += 1
            else:
                stream.status = TaskStreamStatus.BLOCKED
                stream.blocking_reasons = blocking_reasons
        
        await self.db.flush()
        return ready_count
    
    def _format_tools_for_prompt(self, tools: List[Dict[str, Any]]) -> str:
        """Format tool list for inclusion in prompt."""
        if not tools:
            return "No tools available"
        
        lines = []
        for tool in tools:
            line = f"- {tool.get('slug', 'unknown')}: {tool.get('description', 'No description')}"
            lines.append(line)
        return "\n".join(lines)
    
    def _extract_json_from_response(self, content: str) -> Dict[str, Any]:
        """Try to extract JSON from a response that may have extra text."""
        # Look for JSON block markers
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        elif "```" in content:
            start = content.find("```") + 3
            end = content.find("```", start)
            if end > start:
                content = content[start:end].strip()
        
        # Find JSON object boundaries
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(content[start:end])
            except json.JSONDecodeError:
                pass
        
        # Return minimal valid plan as fallback
        logger.error("Could not parse execution plan, returning minimal plan")
        return {
            "streams": [{
                "name": "main",
                "description": "Main execution stream",
                "tasks": [{
                    "name": "Manual execution",
                    "description": "Execute campaign manually",
                    "task_type": "llm_reasoning",
                    "llm_prompt": "Guide the user through manual campaign execution",
                    "estimated_duration_minutes": 60,
                    "is_critical": True
                }],
                "estimated_duration_minutes": 60
            }],
            "input_requirements": [],
            "estimated_total_duration_minutes": 60,
            "parallelization_factor": 1.0
        }
    
    def _parse_plan_data(
        self,
        data: Dict[str, Any],
        existing_credentials: Optional[List[str]] = None
    ) -> ExecutionPlan:
        """Parse raw plan data into ExecutionPlan dataclass."""
        existing_creds = set(existing_credentials or [])
        
        # Parse input requirements, filtering out ones we already have
        input_requirements = []
        for req_data in data.get("input_requirements", []):
            if req_data.get("key") in existing_creds:
                continue  # Skip credentials we already have
            
            input_requirements.append(InputRequirement(
                key=req_data.get("key", "unknown"),
                input_type=req_data.get("input_type", "text"),
                title=req_data.get("title", "Required Input"),
                description=req_data.get("description", ""),
                priority=req_data.get("priority", "medium"),
                options=req_data.get("options"),
                default_value=req_data.get("default_value")
            ))
        
        # Parse streams
        streams = []
        for stream_data in data.get("streams", []):
            tasks = []
            for task_data in stream_data.get("tasks", []):
                tasks.append(TaskDefinition(
                    name=task_data.get("name", "Unnamed Task"),
                    description=task_data.get("description", ""),
                    task_type=task_data.get("task_type", "llm_reasoning"),
                    tool_slug=task_data.get("tool_slug"),
                    tool_params=task_data.get("tool_params"),
                    llm_prompt=task_data.get("llm_prompt"),
                    depends_on_tasks=task_data.get("depends_on_tasks"),
                    depends_on_inputs=task_data.get("depends_on_inputs"),
                    estimated_duration_minutes=task_data.get("estimated_duration_minutes", 5),
                    is_critical=task_data.get("is_critical", True)
                ))
            
            streams.append(StreamDefinition(
                name=stream_data.get("name", "main"),
                description=stream_data.get("description", ""),
                tasks=tasks,
                depends_on_streams=stream_data.get("depends_on_streams"),
                requires_inputs=stream_data.get("requires_inputs"),
                can_run_parallel=stream_data.get("can_run_parallel", False),
                max_concurrent=stream_data.get("max_concurrent", 1),
                estimated_duration_minutes=stream_data.get("estimated_duration_minutes", 60)
            ))
        
        return ExecutionPlan(
            streams=streams,
            input_requirements=input_requirements,
            estimated_total_duration_minutes=data.get("estimated_total_duration_minutes", 0),
            parallelization_factor=data.get("parallelization_factor", 1.0)
        )


# =============================================================================
# Helper Functions
# =============================================================================

async def get_ready_streams(db: AsyncSession, campaign_id: UUID) -> List[TaskStream]:
    """Get all streams that are ready to execute."""
    result = await db.execute(
        select(TaskStream).where(
            TaskStream.campaign_id == campaign_id,
            TaskStream.status == TaskStreamStatus.READY
        ).order_by(TaskStream.order_index)
    )
    return list(result.scalars().all())


async def get_blocking_inputs(db: AsyncSession, campaign_id: UUID) -> List[UserInputRequest]:
    """Get all blocking input requests for a campaign."""
    result = await db.execute(
        select(UserInputRequest).where(
            UserInputRequest.campaign_id == campaign_id,
            UserInputRequest.status == InputStatus.PENDING,
            UserInputRequest.priority == InputPriority.BLOCKING
        ).order_by(UserInputRequest.blocking_count.desc())
    )
    return list(result.scalars().all())


async def get_campaign_progress(db: AsyncSession, campaign_id: UUID) -> Dict[str, Any]:
    """Get overall campaign progress summary."""
    # Get all streams
    result = await db.execute(
        select(TaskStream).where(TaskStream.campaign_id == campaign_id)
    )
    streams = list(result.scalars().all())
    
    # Calculate totals
    total_tasks = sum(s.tasks_total for s in streams)
    completed_tasks = sum(s.tasks_completed for s in streams)
    failed_tasks = sum(s.tasks_failed for s in streams)
    blocked_tasks = sum(s.tasks_blocked for s in streams)
    
    # Count stream statuses
    stream_counts = {status: 0 for status in TaskStreamStatus}
    for stream in streams:
        stream_counts[stream.status] += 1
    
    return {
        "streams_total": len(streams),
        "streams_completed": stream_counts[TaskStreamStatus.COMPLETED],
        "streams_in_progress": stream_counts[TaskStreamStatus.IN_PROGRESS],
        "streams_ready": stream_counts[TaskStreamStatus.READY],
        "streams_blocked": stream_counts[TaskStreamStatus.BLOCKED],
        "tasks_total": total_tasks,
        "tasks_completed": completed_tasks,
        "tasks_failed": failed_tasks,
        "tasks_blocked": blocked_tasks,
        "progress_pct": (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    }
