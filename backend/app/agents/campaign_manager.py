"""
Campaign Manager Agent - executes approved campaigns autonomously.

The Campaign Manager is responsible for:
- Initializing campaigns from approved proposals
- Generating requirements checklists
- Collecting user inputs (accounts, credentials, budget confirmation)
- Executing campaign tasks using available tools
- Monitoring progress, budget, and success thresholds
- Requesting user input when needed
- Making autonomous decisions within defined scope
- Handling errors gracefully and escalating when necessary
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.prompt_injection_guard import get_security_preamble
from app.models import (
    Tool,
    Campaign,
    CampaignStatus,
    Proposal,
    ProposalStatus,
    Conversation,
    ConversationType,
    Message,
    SenderType,
    # Multi-stream models
    TaskStream,
    CampaignTask,
    UserInputRequest,
    TaskStreamStatus,
    TaskStatus,
    InputStatus,
    InputPriority,
)
from app.services.llm_service import LLMMessage, StreamChunk, llm_service
from app.services.campaign_plan_service import CampaignPlanService
from app.services.stream_executor_service import (
    StreamExecutorService, 
    get_stream_execution_summary,
    provide_user_input
)
from app.services.campaign_progress_service import campaign_progress_service
from app.services.campaign_learning_service import (
    CampaignLearningService,
    RevisionTrigger,
    LessonCategory,
)
from app.services.task_generation_service import TaskGenerationService

logger = logging.getLogger(__name__)


# Campaign phases for tracking progress
class CampaignPhase:
    """Campaign execution phases."""
    INITIALIZING = "initializing"
    REQUIREMENTS_GATHERING = "requirements_gathering"
    WAITING_FOR_USER = "waiting_for_user"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    PAUSED = "paused"
    COMPLETING = "completing"
    TERMINATING = "terminating"


class CampaignManagerAgent(BaseAgent):
    """
    Agent that executes approved campaigns autonomously.
    
    Key capabilities:
    - Initialize campaigns from approved proposals
    - Generate and track requirements checklists
    - Request and collect user inputs
    - Execute tasks using available tools
    - Monitor budget, progress, and success metrics
    - Make autonomous decisions within scope
    - Escalate issues to users when needed
    """
    
    name = "campaign_manager"
    description = "Executes approved campaigns autonomously"
    default_temperature = 0.7
    default_max_tokens = 6000  # High limit - we only pay for tokens actually used
    
    # Use reasoning tier for decision-making, fast for status updates
    model_tier = "reasoning"
    
    # Campaign Manager needs access to content-generation and research tools.
    # Financial tools (lnd-lightning) and social tools (nostr) require explicit
    # campaign-level enablement — kept out of default allowlist for safety.
    # See: internal_docs/PROMPT_INJECTION_AUDIT.md (PI-01, PI-02)
    TOOL_ALLOWLIST = [
        # Research
        "serper-web-search",
        # Document parsing
        "docling-parser",
        # Media generation (GPU)
        "acestep-music", "zimage-generation", "qwen3-tts-voice",
        "ltx-video-generation", "seedvr2-upscaler", "audiosr-enhance",
        "realesrgan-cpu-upscaler",
        # Composition
        "media-toolkit",
        # Sandbox (isolated code execution)
        "dev-sandbox",
        # LLM tools
        "zai-glm-47", "anthropic-claude-sonnet-45", "openai-gpt-52",
    ]
    
    # ==========================================================================
    # System Prompt
    # ==========================================================================
    
    def get_system_prompt(
        self, 
        tools: List[Tool],
        campaign_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build the system prompt for the Campaign Manager agent."""
        # Standard: needs tool details for task execution, but not full strengths/weaknesses
        tools_section = self.format_tools_for_prompt(tools, verbosity="standard")
        
        # Build campaign context section if provided
        campaign_section = ""
        if campaign_context:
            campaign_section = self._build_campaign_section(campaign_context)
        
        security_preamble = get_security_preamble("<tool_call>, <user_input_request>")

        return f"""You are the Campaign Manager Agent for Money Agents, an AI-powered system for automated money-making campaigns.

{security_preamble}

{campaign_section}

## Your Role

You execute approved campaign proposals autonomously, making decisions within defined parameters while keeping users informed and requesting input when needed.

## Your Communication Style

- **Be professional**: You're managing real money and campaigns - be clear and precise.
- **Be proactive**: Anticipate issues and communicate them early.
- **Be autonomous**: Make decisions within your scope without excessive hand-holding.
- **Be transparent**: Report progress, issues, and decisions clearly.
- **Ask when needed**: If something is outside your scope or unclear, ask the user.

## Your Capabilities

1. **Initialize Campaigns**
   - Parse proposal requirements
   - Generate requirements checklist
   - Identify what user inputs are needed
   - Set up tracking metrics

2. **Collect Requirements**
   - Request user inputs (accounts, credentials, confirmations)
   - Validate provided inputs
   - Track requirement completion

3. **Execute Tasks**
   - Use available tools to perform campaign tasks
   - Follow the implementation timeline
   - Make autonomous decisions within budget/scope
   - Handle errors gracefully

4. **Monitor Progress**
   - Track budget spent vs allocated
   - Monitor success metrics
   - Detect threshold violations (stop-loss, success)
   - Report anomalies

5. **Communicate**
   - Provide status updates
   - Request user input when needed
   - Escalate issues appropriately
   - Celebrate milestones

## User Input Requests

When you need something from the user, use this format:

<user_input_request type="INPUT_TYPE" priority="PRIORITY" deadline="DEADLINE">
REQUEST_DESCRIPTION
</user_input_request>

**Input types:**
- `confirmation` - Yes/no approval
- `text` - Free-form text input
- `credentials` - Account credentials or API keys
- `selection` - Choose from options
- `file` - File upload needed
- `budget_approval` - Approve budget allocation

**Priority levels:**
- `blocking` - Campaign cannot continue without this
- `high` - Needed soon for optimal execution
- `medium` - Would improve campaign but not critical
- `low` - Nice to have

**Examples:**

<user_input_request type="credentials" priority="blocking" deadline="before_start">
Please provide your Twitter API credentials (API Key and API Secret) to enable automated posting.
</user_input_request>

<user_input_request type="confirmation" priority="high" deadline="24h">
The campaign has spent $150 of $500 budget in 2 days. Should we continue at this pace?
</user_input_request>

## Campaign Status Updates

Provide status updates in this format:

<campaign_status phase="PHASE">
**Progress:** X/Y tasks completed
**Budget:** $spent of $allocated (percentage%)
**Metrics:** key_metric: value
**Next Actions:** What you're doing next
</campaign_status>

## Decision Making

You can make autonomous decisions for:
- Task execution within approved budget
- Minor timeline adjustments (< 20% deviation)
- Tool selection from approved list
- Retry strategies for failed operations
- Optimization within defined parameters

You should escalate to user for:
- Budget overruns or significant underspend
- Major timeline changes
- Tool failures affecting campaign viability
- Decisions outside defined scope
- Ethical or legal concerns
- Success or stop-loss threshold crossings

## Handling Tool Errors

When tool execution fails, check the error type:

- **RATE_LIMIT_EXCEEDED**: Tool has hit usage limit. Wait before retrying (check retry_after), or use an alternative tool with similar capabilities.
- **RESOURCE_UNAVAILABLE**: The resource (GPU, storage, etc.) is offline. Either wait for it to come back online, or proceed with tasks that don't need that resource.
- **QUEUE_TIMEOUT**: Resource is busy with other work. Try again later or continue with other tasks.
- **RESOURCE_BUSY**: Resource is currently in use. Wait and retry.
- **APPROVAL_REQUIRED**: Tool requires human approval before execution. Inform the user that approval is needed and wait for confirmation before retrying.

For rate limits, consider:
1. Batching requests to be more efficient
2. Using alternative tools with similar capabilities
3. Informing the user if rate limits significantly impact the campaign

For approval-required tools:
1. Explain to the user what the tool will do and why it needs approval
2. Request they create an approval request via the UI
3. Wait for the approval notification before proceeding

## Requirements Checklist Generation

When initializing a campaign, analyze the proposal and generate a checklist:

<requirements_checklist>
[
  {{"item": "Requirement description", "type": "user_input|verification|setup", "blocking": true|false}},
  ...
]
</requirements_checklist>

## Marking Requirements Complete

When user input satisfies a requirement, you MUST output the exact requirement text in this tag:

<requirement_completed>EXACT_ITEM_TEXT_FROM_CHECKLIST</requirement_completed>

IMPORTANT: The text inside the tag must EXACTLY match the "item" field from the requirements checklist.

Example - if the checklist has:
{{"item": "Provide valid platform_account credentials", "type": "user_input", "blocking": true}}

And the user provides their platform account, you output:
<requirement_completed>Provide valid platform_account credentials</requirement_completed>

You can mark multiple requirements complete in one response by using multiple tags.

{tools_section}
"""

    def _build_campaign_section(self, campaign: Dict[str, Any]) -> str:
        """Build the campaign context section for the system prompt."""
        from app.services.prompt_injection_guard import sanitize_external_content
        
        lines = ["## Current Campaign", ""]
        
        if status := campaign.get("status"):
            lines.append(f"**Status:** {status}")
        
        if phase := campaign.get("current_phase"):
            lines.append(f"**Phase:** {phase}")
        
        # Proposal info — sanitize user-supplied title/summary (GAP-14)
        if proposal := campaign.get("proposal"):
            san_title, _ = sanitize_external_content(
                proposal.get('title', 'Untitled'), source="campaign_manager_context"
            )
            lines.append(f"\n### Proposal: {san_title}")
            if summary := proposal.get("summary"):
                san_summary, _ = sanitize_external_content(
                    summary, source="campaign_manager_context"
                )
                lines.append(f"{san_summary}")
        
        # Financial tracking
        allocated = campaign.get("budget_allocated", 0)
        spent = campaign.get("budget_spent", 0)
        revenue = campaign.get("revenue_generated", 0)
        if allocated:
            pct = (spent / allocated * 100) if allocated else 0
            lines.append(f"\n**Budget:** ${spent:,.2f} / ${allocated:,.2f} ({pct:.1f}% used)")
        if revenue:
            lines.append(f"**Revenue Generated:** ${revenue:,.2f}")
        
        # Bitcoin budget tracking
        btc_budget = campaign.get("bitcoin_budget_sats")
        btc_spent = campaign.get("bitcoin_spent_sats", 0)
        btc_received = campaign.get("bitcoin_received_sats", 0)
        if btc_budget:
            btc_pct = (btc_spent / btc_budget * 100) if btc_budget else 0
            btc_remaining = btc_budget - btc_spent
            lines.append(f"\n**Bitcoin Budget:** {btc_spent:,} / {btc_budget:,} sats ({btc_pct:.1f}% used, {btc_remaining:,} remaining)")
            if btc_received:
                lines.append(f"**Bitcoin Received:** {btc_received:,} sats")
        elif btc_spent or btc_received:
            # Campaign has BTC activity but no formal budget
            if btc_spent:
                lines.append(f"\n**Bitcoin Spent:** {btc_spent:,} sats (no budget set — all spends require approval)")
            if btc_received:
                lines.append(f"**Bitcoin Received:** {btc_received:,} sats")
        
        # Progress
        total = campaign.get("tasks_total", 0)
        completed = campaign.get("tasks_completed", 0)
        if total > 0:
            lines.append(f"**Tasks:** {completed}/{total} completed")
        
        # Metrics
        if metrics := campaign.get("success_metrics"):
            lines.append("\n**Success Metrics:**")
            for key, value in metrics.items():
                if isinstance(value, dict):
                    current = value.get("current", 0)
                    target = value.get("target", 0)
                    pct = value.get("percentage", (current / target * 100) if target else 0)
                    lines.append(f"  - {key}: {current} / {target} ({pct:.1f}%)")
                else:
                    lines.append(f"  - {key}: {value}")
        
        # Requirements
        if checklist := campaign.get("requirements_checklist"):
            completed_reqs = sum(1 for r in checklist if r.get("completed"))
            total_reqs = len(checklist)
            lines.append(f"\n**Requirements:** {completed_reqs}/{total_reqs} met")
            
            # Show pending blocking requirements
            pending_blocking = [r for r in checklist if not r.get("completed") and r.get("blocking")]
            if pending_blocking:
                lines.append("**Pending Blocking Requirements:**")
                for req in pending_blocking[:5]:
                    lines.append(f"  - ⚠️ {req.get('item', 'Unknown')}")
        
        lines.append("")
        return "\n".join(lines)
    
    # ==========================================================================
    # Campaign Initialization
    # ==========================================================================
    
    async def initialize_campaign(
        self,
        context: AgentContext,
        proposal_id: UUID,
        user_id: UUID,
    ) -> AgentResult:
        """
        Initialize a new campaign from an approved proposal.
        
        1. Validates proposal is approved
        2. Creates campaign record
        3. Generates requirements checklist
        4. Sets up initial metrics
        """
        db = context.db
        
        # Fetch proposal
        result = await db.execute(
            select(Proposal).where(Proposal.id == proposal_id)
        )
        proposal = result.scalar_one_or_none()
        
        if not proposal:
            return AgentResult(
                success=False,
                message=f"Proposal {proposal_id} not found",
            )
        
        if proposal.status != ProposalStatus.APPROVED:
            return AgentResult(
                success=False,
                message=f"Proposal must be approved to start campaign (current: {proposal.status.value})",
            )
        
        # Generate requirements checklist using LLM
        checklist = await self._generate_requirements_checklist(db, proposal)
        
        # Initialize success metrics from proposal
        success_metrics = self._initialize_success_metrics(proposal)
        
        # Create campaign
        campaign = Campaign(
            proposal_id=proposal.id,
            user_id=user_id,
            status=CampaignStatus.INITIALIZING,
            budget_allocated=float(proposal.initial_budget),
            bitcoin_budget_sats=proposal.bitcoin_budget_sats,  # Bitcoin budget from proposal
            budget_spent=0.0,
            revenue_generated=0.0,
            success_metrics=success_metrics,
            tasks_total=0,
            tasks_completed=0,
            current_phase=CampaignPhase.INITIALIZING,
            requirements_checklist=checklist,
            all_requirements_met=False,
        )
        
        db.add(campaign)
        await db.flush()  # Get the campaign ID
        
        # =====================================================================
        # Generate Multi-Stream Execution Plan
        # =====================================================================
        try:
            # Get available tools for planning
            tools = await self.get_available_tools(db)
            tool_list = [
                {"slug": t.slug, "description": t.description, "name": t.name}
                for t in tools
            ]
            
            # Generate execution plan using LLM
            plan_service = CampaignPlanService(db)
            execution_plan = await plan_service.generate_execution_plan(
                proposal=proposal,
                available_tools=tool_list,
                existing_credentials=None  # TODO: Check for existing credentials
            )
            
            # Store the raw execution plan on the campaign
            campaign.execution_plan = {
                "estimated_duration_minutes": execution_plan.estimated_total_duration_minutes,
                "parallelization_factor": execution_plan.parallelization_factor,
                "streams_count": len(execution_plan.streams),
                "inputs_required": len(execution_plan.input_requirements)
            }
            
            # Create TaskStream and CampaignTask records
            streams = await plan_service.create_campaign_streams(campaign, execution_plan)
            
            # Create UserInputRequest records
            input_requests = await plan_service.create_input_requests(campaign, execution_plan)
            
            # Generate tasks for each input request
            task_gen_service = TaskGenerationService(db)
            for input_req in input_requests:
                await task_gen_service.create_task_for_campaign_input(
                    user_id=user_id,
                    campaign=campaign,
                    input_request=input_req,
                )
            
            # Update campaign task counts (use tasks_total to avoid lazy loading)
            campaign.tasks_total = sum(s.tasks_total for s in streams)
            
            # Update stream readiness based on dependencies
            ready_count = await plan_service.update_stream_readiness(campaign.id)
            
            logger.info(
                f"Campaign {campaign.id}: Generated execution plan with "
                f"{len(streams)} streams, {campaign.tasks_total} tasks, "
                f"{len(input_requests)} input requests, {ready_count} streams ready"
            )
            
        except Exception as e:
            logger.error(f"Failed to generate execution plan: {e}", exc_info=True)
            # Continue without streams - fall back to legacy behavior
            campaign.execution_plan = {"error": str(e), "fallback": True}
        
        # =====================================================================
        
        # Create conversation for this campaign
        conversation = Conversation(
            created_by_user_id=user_id,
            conversation_type=ConversationType.CAMPAIGN,
            related_id=campaign.id,
            title=f"Campaign: {proposal.title}",
            is_active=True,
        )
        db.add(conversation)
        await db.flush()
        
        # Send initialization message
        init_message = self._format_initialization_message(proposal, campaign, checklist)
        message = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.AGENT,
            sender_id=None,
            content=init_message,
            meta_data={"agent_name": self.name},
        )
        db.add(message)
        
        # Update campaign phase based on requirements
        blocking_requirements = [r for r in checklist if r.get("blocking") and not r.get("completed")]
        if blocking_requirements:
            campaign.status = CampaignStatus.WAITING_FOR_INPUTS
            campaign.current_phase = CampaignPhase.REQUIREMENTS_GATHERING
        else:
            campaign.status = CampaignStatus.ACTIVE
            campaign.current_phase = CampaignPhase.EXECUTING
        
        await db.commit()
        
        return AgentResult(
            success=True,
            message=f"Campaign initialized from proposal '{proposal.title}'",
            data={
                "campaign_id": str(campaign.id),
                "conversation_id": str(conversation.id),
                "status": campaign.status.value,
                "requirements_count": len(checklist),
                "blocking_requirements": len(blocking_requirements),
            },
        )
    
    async def _generate_requirements_checklist(
        self,
        db: AsyncSession,
        proposal: Proposal,
    ) -> List[Dict[str, Any]]:
        """Generate a requirements checklist for the campaign."""
        
        # Build prompt
        prompt = f"""Analyze this proposal and generate a requirements checklist for campaign execution.

## Proposal

**Title:** {proposal.title}

**Summary:** {proposal.summary}

**Description:** {proposal.detailed_description[:2000] if proposal.detailed_description else 'N/A'}

**Budget:** ${float(proposal.initial_budget):,.2f}

**Required Tools:** {json.dumps(proposal.required_tools, indent=2) if proposal.required_tools else 'None specified'}

**Required Inputs:** {json.dumps(proposal.required_inputs, indent=2) if proposal.required_inputs else 'None specified'}

**Implementation Timeline:** {json.dumps(proposal.implementation_timeline, indent=2) if proposal.implementation_timeline else 'None specified'}

## Task

Generate a checklist of requirements needed before campaign execution can begin.

Return ONLY a JSON array:
```json
[
  {{"item": "Requirement description", "type": "user_input", "blocking": true}},
  {{"item": "Another requirement", "type": "verification", "blocking": false}},
  ...
]
```

**Types:**
- `user_input` - Requires user to provide something
- `verification` - Requires verification or confirmation
- `setup` - System setup or configuration
- `approval` - Requires explicit approval

**Blocking:** true if campaign cannot start without this requirement being met.

Be specific and actionable. Include requirements for:
1. Account/credential needs
2. Budget confirmations
3. Tool access verification
4. Content/asset needs
5. External service setup
"""

        messages = [
            LLMMessage(role="system", content="You are a requirements analyst. Generate precise, actionable requirements checklists."),
            LLMMessage(role="user", content=prompt),
        ]
        
        try:
            response = await self.think(messages, model="fast", max_tokens=6000)
            
            # Parse JSON from response
            import json5
            content = response.content
            
            # Extract JSON array from response
            json_match = None
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end > start:
                    json_match = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                if end > start:
                    json_match = content[start:end].strip()
            elif content.strip().startswith("["):
                json_match = content.strip()
            
            if json_match:
                checklist = json5.loads(json_match)
                if isinstance(checklist, list):
                    # Add completed: false to each item
                    for item in checklist:
                        item["completed"] = False
                        item["completed_at"] = None
                    return checklist
            
            logger.warning(f"Could not parse checklist from LLM response: {content[:200]}")
            
        except Exception as e:
            logger.exception(f"Failed to generate requirements checklist: {e}")
        
        # Fallback: generate basic checklist from proposal inputs
        checklist = []
        
        if proposal.required_inputs:
            for key, value in proposal.required_inputs.items():
                checklist.append({
                    "item": f"Provide {key}: {value}" if isinstance(value, str) else f"Provide {key}",
                    "type": "user_input",
                    "blocking": True,
                    "completed": False,
                    "completed_at": None,
                })
        
        if proposal.required_tools:
            checklist.append({
                "item": "Verify all required tools are available",
                "type": "verification",
                "blocking": True,
                "completed": False,
                "completed_at": None,
            })
        
        # Always add budget confirmation
        checklist.append({
            "item": f"Confirm budget allocation of ${float(proposal.initial_budget):,.2f}",
            "type": "approval",
            "blocking": True,
            "completed": False,
            "completed_at": None,
        })
        
        return checklist
    
    def _initialize_success_metrics(self, proposal: Proposal) -> Dict[str, Any]:
        """Initialize success metrics from proposal criteria."""
        metrics = {}
        
        if proposal.success_criteria:
            for key, target in proposal.success_criteria.items():
                if isinstance(target, (int, float)):
                    metrics[key] = {
                        "current": 0,
                        "target": target,
                        "percentage": 0,
                    }
                elif isinstance(target, dict):
                    metrics[key] = {
                        "current": 0,
                        "target": target.get("target", target.get("value", 0)),
                        "percentage": 0,
                        **target,
                    }
                else:
                    metrics[key] = {
                        "current": 0,
                        "target": str(target),
                        "percentage": 0,
                    }
        
        # Add default metrics if none specified
        if not metrics:
            metrics = {
                "revenue": {"current": 0, "target": float(proposal.initial_budget) * 2, "percentage": 0},
                "roi": {"current": 0, "target": 1.0, "percentage": 0},
            }
        
        return metrics
    
    def _format_initialization_message(
        self,
        proposal: Proposal,
        campaign: Campaign,
        checklist: List[Dict[str, Any]],
    ) -> str:
        """Format the initialization message for the campaign conversation."""
        blocking = [r for r in checklist if r.get("blocking")]
        non_blocking = [r for r in checklist if not r.get("blocking")]
        
        lines = [
            f"# Campaign Initialized: {proposal.title}",
            "",
            f"I've set up this campaign based on your approved proposal.",
            "",
            "## Budget",
            f"- **Allocated:** ${float(campaign.budget_allocated):,.2f}",
            "",
            "## Requirements Checklist",
            "",
        ]
        
        if blocking:
            lines.append("**⚠️ Blocking Requirements** (must be completed before execution):")
            for i, req in enumerate(blocking, 1):
                lines.append(f"{i}. {req['item']}")
            lines.append("")
        
        if non_blocking:
            lines.append("**Optional Requirements:**")
            for i, req in enumerate(non_blocking, 1):
                lines.append(f"{i}. {req['item']}")
            lines.append("")
        
        if blocking:
            lines.extend([
                "## Next Steps",
                "",
                "Please provide the blocking requirements above so I can begin executing the campaign.",
                "Reply to this conversation with the information needed, and I'll update the checklist.",
            ])
        else:
            lines.extend([
                "## Status: Ready to Execute",
                "",
                "All blocking requirements are met. I'll begin executing the campaign tasks.",
            ])
        
        return "\n".join(lines)
    
    # ==========================================================================
    # Requirements Collection
    # ==========================================================================
    
    async def process_user_input(
        self,
        context: AgentContext,
        campaign_id: UUID,
        user_message: str,
    ) -> AgentResult:
        """
        Process user input for a campaign and update requirements.
        
        Analyzes user message to determine what requirements it satisfies.
        """
        db = context.db
        
        # Fetch campaign
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        # Get or create conversation for this campaign
        result = await db.execute(
            select(Conversation).where(
                Conversation.related_id == campaign_id,
                Conversation.conversation_type == ConversationType.CAMPAIGN
            )
        )
        conversation = result.scalar_one_or_none()
        
        if not conversation:
            # Create conversation if it doesn't exist
            conversation = Conversation(
                created_by_user_id=context.user_id,
                conversation_type=ConversationType.CAMPAIGN,
                related_id=campaign_id,
                title=f"Campaign: {campaign_id}",
                is_active=True,
            )
            db.add(conversation)
            await db.flush()
        
        # Save user message to conversation
        user_msg = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.USER,
            sender_id=context.user_id,
            content=user_message,
        )
        db.add(user_msg)
        
        # Fetch proposal for context
        result = await db.execute(
            select(Proposal).where(Proposal.id == campaign.proposal_id)
        )
        proposal = result.scalar_one_or_none()
        
        # Get available tools
        tools = await self.get_available_tools(db)
        
        # Build context for LLM
        checklist = campaign.requirements_checklist or []
        pending = [r for r in checklist if not r.get("completed")]
        
        prompt = f"""The user has provided input for the campaign. Analyze it to determine:
1. Which requirements from the checklist this satisfies
2. Whether any follow-up is needed
3. What to tell the user

## Pending Requirements
{json.dumps(pending, indent=2)}

## User Input
{user_message}

## Your Task

Respond with:
1. A brief acknowledgment to the user
2. Which requirements (if any) this input satisfies - reference by exact "item" text
3. Any follow-up questions or next steps

Use this format for requirement completions:
<requirement_completed>EXACT_ITEM_TEXT</requirement_completed>

For example:
<requirement_completed>Confirm budget allocation of $500.00</requirement_completed>
"""

        messages = [
            LLMMessage(
                role="system", 
                content=self.get_system_prompt(tools, campaign_context=self._get_campaign_context(campaign, proposal))
            ),
            LLMMessage(role="user", content=prompt),
        ]
        
        response = await self.think(messages)
        
        # Parse completed requirements
        import re
        completed_items = re.findall(
            r'<requirement_completed>(.*?)</requirement_completed>',
            response.content,
            re.DOTALL
        )
        
        # Update checklist
        updates_made = 0
        for item_text in completed_items:
            item_text = item_text.strip()
            for req in checklist:
                if req.get("item", "").strip() == item_text and not req.get("completed"):
                    req["completed"] = True
                    req["completed_at"] = utc_now().isoformat()
                    updates_made += 1
                    logger.info(f"Marked requirement complete: {item_text}")
        
        if updates_made > 0:
            # Force SQLAlchemy to detect the change to JSONB field
            from sqlalchemy.orm.attributes import flag_modified
            campaign.requirements_checklist = list(checklist)
            flag_modified(campaign, "requirements_checklist")
            
            # Check if all blocking requirements met
            blocking_pending = [r for r in checklist if r.get("blocking") and not r.get("completed")]
            if not blocking_pending:
                campaign.all_requirements_met = True
                campaign.status = CampaignStatus.ACTIVE
                campaign.current_phase = CampaignPhase.EXECUTING
                campaign.start_date = utc_now()
            
            campaign.last_activity_at = utc_now()
        
        await db.commit()
        
        # Parse and execute any tool calls in the response
        tool_results = []
        if self.has_tool_calls(response.content):
            tool_calls = self.parse_tool_calls(response.content)
            if tool_calls:
                logger.info(f"Executing {len(tool_calls)} tool call(s) from campaign response")
                tool_results = await self.execute_tool_calls(context, tool_calls)
                
                # Log tool results
                for result in tool_results:
                    if result.get("success"):
                        logger.info(f"Tool {result['tool_slug']} executed successfully")
                    else:
                        logger.warning(f"Tool {result['tool_slug']} failed: {result.get('error')}")
        
        # Clean response content (remove requirement tags and tool call tags)
        clean_content = re.sub(
            r'<requirement_completed>.*?</requirement_completed>',
            '',
            response.content,
            flags=re.DOTALL
        ).strip()
        clean_content = self.remove_tool_call_tags(clean_content)
        
        # Append tool results to the response if any were executed
        if tool_results:
            from app.services.prompt_injection_guard import sanitize_external_content
            tool_results_summary = "\n\n---\n**Tool Execution Results:**\n"
            for result in tool_results:
                if result.get("success"):
                    output = result.get("output", {})
                    output_str = json.dumps(output) if isinstance(output, dict) else str(output)
                    if len(output_str) > 500:
                        output_str = output_str[:500] + "..."
                    # Sanitize tool output before storing in message
                    sanitized_output, _det = sanitize_external_content(
                        output_str, source=f"tool:{result['tool_slug']}",
                    )
                    tool_results_summary += f"- ✅ **{result['tool_slug']}**: {sanitized_output}\n"
                else:
                    tool_results_summary += f"- ❌ **{result['tool_slug']}**: {result.get('error')}\n"
            clean_content += tool_results_summary
        
        # Save agent response to conversation
        agent_msg = Message(
            conversation_id=conversation.id,
            sender_type=SenderType.AGENT,
            sender_id=None,
            content=clean_content,
            meta_data={
                "agent_name": self.name,
                "requirements_completed": updates_made,
                "tool_executions": [r["tool_slug"] for r in tool_results] if tool_results else [],
            },
            tokens_used=response.total_tokens,
            model_used=response.model,
        )
        db.add(agent_msg)
        await db.commit()
        
        return AgentResult(
            success=True,
            message="User input processed",
            data={
                "content": clean_content,
                "requirements_completed": updates_made,
                "all_requirements_met": campaign.all_requirements_met,
                "campaign_status": campaign.status.value,
                "tool_executions": tool_results,
            },
            tokens_used=response.total_tokens,
            model_used=response.model,
            latency_ms=response.latency_ms,
        )
    
    def _get_campaign_context(
        self,
        campaign: Campaign,
        proposal: Optional[Proposal] = None,
    ) -> Dict[str, Any]:
        """Build campaign context dict for prompts."""
        context = {
            "status": campaign.status.value,
            "current_phase": campaign.current_phase,
            "budget_allocated": float(campaign.budget_allocated),
            "budget_spent": float(campaign.budget_spent),
            "revenue_generated": float(campaign.revenue_generated),
            "tasks_total": campaign.tasks_total,
            "tasks_completed": campaign.tasks_completed,
            "success_metrics": campaign.success_metrics,
            "requirements_checklist": campaign.requirements_checklist,
            "all_requirements_met": campaign.all_requirements_met,
        }
        
        if proposal:
            context["proposal"] = {
                "title": proposal.title,
                "summary": proposal.summary,
                "required_tools": proposal.required_tools,
            }
        
        return context
    
    # ==========================================================================
    # Campaign Execution
    # ==========================================================================
    
    async def execute_campaign_step(
        self,
        context: AgentContext,
        campaign_id: UUID,
    ) -> AgentResult:
        """
        Execute the next step in an active campaign.
        
        Called periodically by the scheduler to advance campaigns.
        Uses multi-stream execution when available.
        """
        db = context.db
        
        # Fetch campaign
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        # Check campaign status - exclude non-active campaigns
        if campaign.status not in [
            CampaignStatus.ACTIVE, 
            CampaignStatus.INITIALIZING,
            CampaignStatus.EXECUTING,
            CampaignStatus.REQUIREMENTS_GATHERING,
            CampaignStatus.WAITING_FOR_INPUTS
        ]:
            return AgentResult(
                success=True,
                message=f"Campaign is {campaign.status.value}, no action needed",
                data={"status": campaign.status.value},
            )
        
        # =====================================================================
        # MULTI-STREAM EXECUTION (New Path)
        # =====================================================================
        if campaign.execution_plan and not campaign.execution_plan.get("fallback"):
            return await self._execute_streams(context, campaign)
        
        # =====================================================================
        # LEGACY EXECUTION (Fallback Path)
        # =====================================================================
        
        # Check if all requirements are met but status hasn't been updated
        if campaign.all_requirements_met and campaign.status == CampaignStatus.WAITING_FOR_INPUTS:
            campaign.status = CampaignStatus.ACTIVE
            campaign.current_phase = CampaignPhase.EXECUTING
            campaign.start_date = campaign.start_date or utc_now()
            await db.commit()
            logger.info(f"Campaign {campaign.id} activated - all requirements met")
        
        # Check requirements
        if not campaign.all_requirements_met:
            return AgentResult(
                success=True,
                message="Waiting for requirements to be met",
                data={"status": "waiting_for_inputs"},
            )
        
        # Fetch proposal for context
        result = await db.execute(
            select(Proposal).where(Proposal.id == campaign.proposal_id)
        )
        proposal = result.scalar_one_or_none()
        
        # Get available tools
        tools = await self.get_available_tools(db)
        
        # Check thresholds
        threshold_check = await self._check_thresholds(campaign, proposal)
        if threshold_check["action_needed"]:
            return await self._handle_threshold_violation(context, campaign, threshold_check)
        
        # Determine next action
        next_action = await self._determine_next_action(db, campaign, proposal, tools)
        
        if next_action["type"] == "execute_tool":
            return await self._execute_tool_action(context, campaign, next_action)
        elif next_action["type"] == "request_input":
            return await self._request_user_input(context, campaign, next_action)
        elif next_action["type"] == "complete":
            return await self._complete_campaign(context, campaign)
        elif next_action["type"] == "wait":
            return AgentResult(
                success=True,
                message=next_action.get("reason", "Waiting"),
                data={"status": "waiting", "reason": next_action.get("reason")},
            )
        else:
            return AgentResult(
                success=True,
                message="No action required at this time",
                data={"status": campaign.status.value},
            )
    
    async def _execute_streams(
        self,
        context: AgentContext,
        campaign: Campaign,
    ) -> AgentResult:
        """
        Execute campaign using multi-stream architecture.
        
        Runs ready streams in parallel, tracks progress, and handles
        blocking inputs.
        """
        db = context.db
        
        # Get execution summary
        summary = await get_stream_execution_summary(db, campaign.id)
        
        # =====================================================================
        # PHASE 5: Check for warnings and generate proactive suggestions
        # =====================================================================
        try:
            learning_service = CampaignLearningService(db)
            suggestions = await learning_service.generate_suggestions(campaign, summary)
            
            # Auto-apply suggestions that are safe to apply
            for suggestion in suggestions:
                if suggestion.can_auto_apply:
                    await learning_service.auto_apply_suggestion(suggestion)
        except Exception as e:
            logger.warning(f"Learning service error for campaign {campaign.id}: {e}")
        # =====================================================================
        
        # Check for blocking inputs
        if summary["blocking_inputs"]:
            campaign.status = CampaignStatus.WAITING_FOR_INPUTS
            await db.flush()
            
            blocking_list = ", ".join(
                f"{inp['title']} ({inp['blocking_count']} tasks blocked)"
                for inp in summary["blocking_inputs"][:3]
            )
            
            return AgentResult(
                success=True,
                message=f"Waiting for user inputs: {blocking_list}",
                data={
                    "status": "waiting_for_inputs",
                    "blocking_inputs": summary["blocking_inputs"],
                    "progress_pct": summary["overall_progress_pct"],
                },
            )
        
        # Check if we have ready streams to execute
        if summary["ready_streams"] == 0:
            if summary["completed_streams"] == summary["total_streams"]:
                # All streams completed!
                return await self._complete_campaign(context, campaign)
            
            if summary["blocked_streams"] > 0:
                campaign.status = CampaignStatus.WAITING_FOR_INPUTS
                await db.flush()
                
                # =========================================================
                # PHASE 5: Analyze if plan revision would help
                # =========================================================
                try:
                    recommendation = await learning_service.analyze_for_revision(
                        campaign=campaign,
                        trigger=RevisionTrigger.STREAM_BLOCKED,
                        trigger_details=f"All {summary['blocked_streams']} streams are blocked"
                    )
                    if recommendation:
                        # Create revision record (but don't auto-apply without user approval)
                        await learning_service.create_revision(
                            campaign=campaign,
                            recommendation=recommendation,
                            initiated_by="agent",
                            approved_by_user=False
                        )
                        logger.info(f"Campaign {campaign.id}: Plan revision recommended")
                except Exception as e:
                    logger.warning(f"Plan revision analysis failed: {e}")
                # =========================================================
                
                return AgentResult(
                    success=True,
                    message="All streams blocked, waiting for dependencies",
                    data={
                        "status": "blocked",
                        "streams": summary["streams"],
                        "progress_pct": summary["overall_progress_pct"],
                    },
                )
            
            return AgentResult(
                success=True,
                message="No streams ready to execute",
                data={"progress_pct": summary["overall_progress_pct"]},
            )
        
        # Execute ready streams
        campaign.status = CampaignStatus.EXECUTING
        campaign.current_phase = CampaignPhase.EXECUTING
        await db.flush()
        
        # Use stream executor
        executor = StreamExecutorService(db)
        
        # Execute up to 3 streams in parallel
        max_parallel = 3 if campaign.streams_parallel_execution else 1
        exec_result = await executor.execute_ready_streams(campaign, max_parallel=max_parallel)
        
        # =====================================================================
        # PHASE 5: Record lessons from failures
        # =====================================================================
        if exec_result.get("failed", 0) > 0:
            try:
                for result in exec_result.get("results", []):
                    if result.get("status") == "failed":
                        await learning_service.record_lesson(
                            campaign_id=campaign.id,
                            title=f"Stream '{result.get('stream_name')}' failed",
                            description=f"Stream failed with error: {result.get('error', 'Unknown')}",
                            category=LessonCategory.FAILURE,
                            trigger_event=f"Stream execution failed",
                            context={
                                "stream_name": result.get("stream_name"),
                                "tasks_completed": result.get("tasks_completed", 0),
                                "tasks_failed": result.get("tasks_failed", 0),
                            },
                            prevention_steps=[
                                "Review task dependencies before execution",
                                "Check tool availability",
                                "Validate input data",
                            ],
                            impact_severity="medium",
                        )
            except Exception as e:
                logger.warning(f"Failed to record lesson: {e}")
        # =====================================================================
        
        # Update campaign progress
        new_summary = await get_stream_execution_summary(db, campaign.id)
        campaign.tasks_completed = new_summary["completed_tasks"]
        campaign.last_activity_at = utc_now()
        
        await db.commit()
        
        # Emit real-time progress update via WebSocket
        try:
            await campaign_progress_service.emit_overall_progress(
                campaign_id=str(campaign.id),
                total_tasks=new_summary["total_tasks"],
                completed_tasks=new_summary["completed_tasks"],
                failed_tasks=new_summary.get("failed_tasks", 0),
                overall_progress_pct=new_summary["overall_progress_pct"],
                budget_spent=campaign.budget_spent,
                revenue_generated=campaign.revenue_generated,
            )
            
            # Emit stream-level progress for each stream
            for stream_info in new_summary.get("streams", []):
                await campaign_progress_service.emit_stream_progress(
                    campaign_id=str(campaign.id),
                    stream_id=str(stream_info.get("stream_id", "")),
                    stream_name=stream_info.get("stream_name", ""),
                    tasks_total=stream_info.get("total_tasks", 0),
                    tasks_completed=stream_info.get("completed_tasks", 0),
                    tasks_failed=stream_info.get("failed_tasks", 0),
                    progress_pct=stream_info.get("progress_pct", 0),
                    status=stream_info.get("status", "unknown"),
                )
        except Exception as e:
            logger.warning(f"Failed to emit WebSocket progress: {e}")
        
        return AgentResult(
            success=True,
            message=f"Executed {exec_result['executed']} streams: "
                    f"{exec_result['completed']} completed, "
                    f"{exec_result['blocked']} blocked, "
                    f"{exec_result['failed']} failed",
            data={
                "status": campaign.status.value,
                "execution_result": exec_result,
                "progress_pct": new_summary["overall_progress_pct"],
                "streams": new_summary["streams"],
            },
        )
    
    async def _check_thresholds(
        self,
        campaign: Campaign,
        proposal: Optional[Proposal],
    ) -> Dict[str, Any]:
        """Check budget and success thresholds."""
        result = {
            "action_needed": False,
            "type": None,
            "reason": None,
        }
        
        # Check budget (if 90%+ spent)
        if campaign.budget_allocated > 0:
            budget_pct = campaign.budget_spent / campaign.budget_allocated
            if budget_pct >= 0.9:
                result["action_needed"] = True
                result["type"] = "budget_warning"
                result["reason"] = f"Budget {budget_pct:.0%} spent"
        
        # Check stop-loss from proposal
        if proposal and proposal.stop_loss_threshold:
            stop_loss = proposal.stop_loss_threshold
            
            # Check max loss threshold
            if "max_loss" in stop_loss:
                max_loss = stop_loss["max_loss"]
                net = campaign.revenue_generated - campaign.budget_spent
                if net < -max_loss:
                    result["action_needed"] = True
                    result["type"] = "stop_loss"
                    result["reason"] = f"Net loss ${abs(net):.2f} exceeds stop-loss ${max_loss:.2f}"
        
        # Check success metrics
        for metric_name, metric in campaign.success_metrics.items():
            if isinstance(metric, dict):
                current = metric.get("current", 0)
                target = metric.get("target", 0)
                if isinstance(target, (int, float)) and target > 0:
                    if current >= target:
                        result["action_needed"] = True
                        result["type"] = "success_reached"
                        result["reason"] = f"Success metric '{metric_name}' reached target"
                        break
        
        return result
    
    async def _handle_threshold_violation(
        self,
        context: AgentContext,
        campaign: Campaign,
        threshold_check: Dict[str, Any],
    ) -> AgentResult:
        """Handle a threshold violation."""
        db = context.db
        
        if threshold_check["type"] == "success_reached":
            # Mark campaign as completed
            campaign.status = CampaignStatus.COMPLETED
            campaign.current_phase = CampaignPhase.COMPLETING
            campaign.end_date = utc_now()
            await db.commit()
            
            return AgentResult(
                success=True,
                message=f"Campaign completed! {threshold_check['reason']}",
                data={
                    "status": "completed",
                    "reason": threshold_check["reason"],
                },
            )
        
        elif threshold_check["type"] == "stop_loss":
            # Pause campaign and request user decision
            campaign.status = CampaignStatus.PAUSED
            campaign.current_phase = CampaignPhase.PAUSED
            await db.commit()
            
            return AgentResult(
                success=True,
                message=f"Campaign paused: {threshold_check['reason']}",
                data={
                    "status": "paused",
                    "reason": threshold_check["reason"],
                    "requires_user_decision": True,
                },
            )
        
        elif threshold_check["type"] == "budget_warning":
            # Continue but flag
            return AgentResult(
                success=True,
                message=f"Warning: {threshold_check['reason']}",
                data={
                    "status": campaign.status.value,
                    "warning": threshold_check["reason"],
                },
            )
        
        return AgentResult(success=True, message="Threshold check complete")
    
    async def _determine_next_action(
        self,
        db: AsyncSession,
        campaign: Campaign,
        proposal: Optional[Proposal],
        tools: List[Tool],
    ) -> Dict[str, Any]:
        """Use LLM to determine the next campaign action."""
        
        # Build prompt with comprehensive context
        timeline = proposal.implementation_timeline if proposal else {}
        required_tools = proposal.required_tools if proposal else {}
        success_metrics = campaign.success_metrics or {}
        
        # Get recent conversation messages for context
        conversation_context = ""
        from app.models import Conversation, Message, ConversationType, SenderType
        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.related_id == campaign.id,
                Conversation.conversation_type == ConversationType.CAMPAIGN
            )
        )
        conversation = conv_result.scalar_one_or_none()
        if conversation:
            msg_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.created_at.desc())
                .limit(10)
            )
            recent_messages = msg_result.scalars().all()
            if recent_messages:
                conversation_context = "\n## Recent Conversation History\n"
                for msg in reversed(recent_messages):
                    role = "User" if msg.sender_type == SenderType.USER else "Campaign Manager"
                    # Truncate long messages
                    content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
                    conversation_context += f"**{role}:** {content}\n\n"
        
        # Build tool reference for required tools
        tools_reference = ""
        for tool in tools:
            if tool.slug in (required_tools or {}):
                tools_reference += f"\n### {tool.name} (`{tool.slug}`)\n"
                if tool.usage_instructions:
                    tools_reference += f"**Usage:** {tool.usage_instructions[:500]}\n"
                if tool.input_schema:
                    tools_reference += f"**Required Parameters:** {json.dumps(tool.input_schema)}\n"
        
        prompt = f"""Analyze this campaign and determine the next action.

## Campaign Overview
**Title:** {proposal.title if proposal else 'Unknown'}
**Summary:** {proposal.summary if proposal else 'No summary'}

## Campaign Status
- **Phase:** {campaign.current_phase}
- **Tasks Completed:** {campaign.tasks_completed}/{campaign.tasks_total}
- **Budget Used:** ${campaign.budget_spent:.2f} of ${campaign.budget_allocated:.2f}
- **Revenue:** ${campaign.revenue_generated:.2f}

## Success Metrics
{json.dumps(success_metrics, indent=2)}

## Implementation Timeline
{json.dumps(timeline, indent=2) if timeline else 'No specific timeline'}

## Required Tools
{json.dumps(required_tools, indent=2) if required_tools else 'No specific tools'}
{tools_reference}
{conversation_context}

## Your Task

Based on the conversation history and campaign status, determine the next action.
- If the user provided specific prompts/inputs, use them in the tool parameters.
- If tasks need execution, execute them with the correct parameters.
- If more information is needed, request it from the user.

Respond with ONE of these JSON formats:

To execute a tool:
```json
{{"type": "execute_tool", "tool_slug": "tool-name", "params": {{"param1": "value1"}}, "reason": "Why"}}
```

To request user input:
```json
{{"type": "request_input", "input_type": "text|confirmation|file", "question": "What to ask", "reason": "Why needed"}}
```

To mark campaign complete:
```json
{{"type": "complete", "reason": "Why campaign is done"}}
```

To wait:
```json
{{"type": "wait", "reason": "Why waiting"}}
```
"""

        messages = [
            LLMMessage(role="system", content="You are a campaign execution planner. Determine the next best action."),
            LLMMessage(role="user", content=prompt),
        ]
        
        try:
            response = await self.think(messages, model="fast", max_tokens=6000)
            
            # Parse JSON from response
            import json5
            content = response.content
            
            # Extract JSON
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                if end > start:
                    return json5.loads(content[start:end].strip())
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                if end > start:
                    return json5.loads(content[start:end].strip())
            elif "{" in content:
                # Try to extract JSON object
                start = content.find("{")
                end = content.rfind("}") + 1
                if end > start:
                    return json5.loads(content[start:end])
            
        except Exception as e:
            logger.exception(f"Failed to determine next action: {e}")
        
        return {"type": "wait", "reason": "Unable to determine next action"}
    
    async def _execute_tool_action(
        self,
        context: AgentContext,
        campaign: Campaign,
        action: Dict[str, Any],
    ) -> AgentResult:
        """Execute a tool action for the campaign."""
        tool_slug = action.get("tool_slug")
        params = action.get("params", {})
        
        if not tool_slug:
            return AgentResult(success=False, message="No tool specified")
        
        # Execute tool
        tool_calls = [{"tool_slug": tool_slug, "params": params}]
        results = await self.execute_tool_calls(context, tool_calls)
        
        if results and results[0].get("success"):
            # Update campaign progress
            campaign.tasks_completed += 1
            campaign.last_activity_at = utc_now()
            await context.db.commit()
            
            # Emit task completion via WebSocket
            try:
                await campaign_progress_service.emit_task_completed(
                    campaign_id=str(campaign.id),
                    stream_id="manual",  # Tool executed outside of stream context
                    task_id="tool_execution",
                    task_name=tool_slug,
                    result=results[0].get("output", ""),
                )
            except Exception as e:
                logger.warning(f"Failed to emit task completion: {e}")
            
            return AgentResult(
                success=True,
                message=f"Executed tool: {tool_slug}",
                data={
                    "tool_slug": tool_slug,
                    "result": results[0].get("output"),
                    "tasks_completed": campaign.tasks_completed,
                },
            )
        else:
            error = results[0].get("error") if results else "Unknown error"
            
            # Emit task failure via WebSocket
            try:
                await campaign_progress_service.emit_task_failed(
                    campaign_id=str(campaign.id),
                    stream_id="manual",
                    task_id="tool_execution",
                    task_name=tool_slug,
                    error=str(error),
                )
            except Exception as e:
                logger.warning(f"Failed to emit task failure: {e}")
            
            return AgentResult(
                success=False,
                message=f"Tool execution failed: {error}",
                data={"tool_slug": tool_slug, "error": error},
            )
    
    async def _request_user_input(
        self,
        context: AgentContext,
        campaign: Campaign,
        action: Dict[str, Any],
    ) -> AgentResult:
        """Create a user input request."""
        old_status = campaign.status.value
        # This would typically create a notification or update conversation
        campaign.status = CampaignStatus.WAITING_FOR_INPUTS
        campaign.current_phase = CampaignPhase.WAITING_FOR_USER
        await context.db.commit()
        
        # Emit status change and input required events
        try:
            await campaign_progress_service.emit_status_change(
                campaign_id=str(campaign.id),
                old_status=old_status,
                new_status=campaign.status.value,
            )
            await campaign_progress_service.emit_input_required(
                campaign_id=str(campaign.id),
                input_key=action.get("input_type", "user_input"),
                title=action.get("question", "Input required"),
                description=action.get("reason", ""),
                input_type="text",
                blocking_task_count=1,
            )
        except Exception as e:
            logger.warning(f"Failed to emit input required event: {e}")
        
        return AgentResult(
            success=True,
            message="User input requested",
            data={
                "input_type": action.get("input_type"),
                "question": action.get("question"),
                "reason": action.get("reason"),
            },
        )
    
    async def _complete_campaign(
        self,
        context: AgentContext,
        campaign: Campaign,
    ) -> AgentResult:
        """Mark campaign as completed and trigger learning."""
        old_status = campaign.status.value
        campaign.status = CampaignStatus.COMPLETED
        campaign.current_phase = CampaignPhase.COMPLETING
        campaign.end_date = utc_now()
        
        # =====================================================================
        # PHASE 5: Trigger Pattern Discovery from successful campaign
        # =====================================================================
        try:
            learning_service = CampaignLearningService(context.db)
            patterns = await learning_service.discover_patterns_from_campaign(campaign.id)
            logger.info(f"Campaign {campaign.id}: Discovered {len(patterns)} patterns")
        except Exception as e:
            logger.warning(f"Pattern discovery failed for campaign {campaign.id}: {e}")
        # =====================================================================
        
        await context.db.commit()
        
        # Emit completion status via WebSocket
        try:
            await campaign_progress_service.emit_status_change(
                campaign_id=str(campaign.id),
                old_status=old_status,
                new_status=campaign.status.value,
            )
        except Exception as e:
            logger.warning(f"Failed to emit completion status: {e}")
        
        return AgentResult(
            success=True,
            message="Campaign completed",
            data={
                "status": "completed",
                "final_spend": float(campaign.budget_spent),
                "final_revenue": float(campaign.revenue_generated),
                "tasks_completed": campaign.tasks_completed,
            },
        )
    
    # ==========================================================================
    # Chat Interface
    # ==========================================================================
    
    async def chat(
        self,
        context: AgentContext,
        user_message: str,
        campaign_context: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        """
        Chat with the user about a campaign.
        
        Used for the WebSocket streaming interface.
        """
        db = context.db
        
        # Get available tools
        tools = await self.get_available_tools(db)
        
        # Build system prompt
        system_prompt = self.get_system_prompt(tools, campaign_context=campaign_context)
        
        # Get conversation history if we have a conversation
        history: List[LLMMessage] = []
        if context.conversation_id:
            history = await self._get_conversation_history(db, context.conversation_id)
        
        # Build messages
        messages = [LLMMessage(role="system", content=system_prompt)]
        messages.extend(history)
        messages.append(LLMMessage(role="user", content=user_message))
        
        # Generate response
        response = await self.think(messages)
        
        # Save messages to conversation if we have one
        if context.conversation_id:
            # Save user message
            user_msg = Message(
                conversation_id=context.conversation_id,
                sender_type=SenderType.USER,
                sender_id=context.user_id,
                content=user_message,
            )
            db.add(user_msg)
            
            # Save agent response
            agent_msg = Message(
                conversation_id=context.conversation_id,
                sender_type=SenderType.AGENT,
                sender_id=None,
                content=response.content,
                tokens_used=response.total_tokens,
                model_used=response.model,
                meta_data={"agent_name": self.name},
            )
            db.add(agent_msg)
            await db.commit()
        
        return AgentResult(
            success=True,
            message="Response generated",
            data={"content": response.content},
            tokens_used=response.total_tokens,
            model_used=response.model,
            latency_ms=response.latency_ms,
        )
    
    async def chat_stream(
        self,
        context: AgentContext,
        user_message: str,
        campaign_context: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream a chat response for a campaign.
        
        Used for the WebSocket streaming interface.
        """
        db = context.db
        
        # Get available tools
        tools = await self.get_available_tools(db)
        
        # Build system prompt
        system_prompt = self.get_system_prompt(tools, campaign_context=campaign_context)
        
        # Get conversation history if we have a conversation
        history: List[LLMMessage] = []
        if context.conversation_id:
            history = await self._get_conversation_history(db, context.conversation_id)
        
        # Build messages
        messages = [LLMMessage(role="system", content=system_prompt)]
        messages.extend(history)
        messages.append(LLMMessage(role="user", content=user_message))
        
        # Stream response
        async for chunk in self.think_stream(messages):
            yield chunk
    
    async def _get_conversation_history(
        self,
        db: AsyncSession,
        conversation_id: UUID,
        limit: int = 20,
    ) -> List[LLMMessage]:
        """Get recent conversation history."""
        from sqlalchemy import desc
        
        result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(desc(Message.created_at))
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()  # Oldest first
        
        history = []
        for msg in messages:
            if msg.sender_type == SenderType.USER:
                history.append(LLMMessage(role="user", content=msg.content))
            elif msg.sender_type == SenderType.AGENT:
                history.append(LLMMessage(role="assistant", content=msg.content))
        
        return history
    
    # ==========================================================================
    # Campaign Status Updates
    # ==========================================================================
    
    async def get_campaign_status(
        self,
        context: AgentContext,
        campaign_id: UUID,
    ) -> AgentResult:
        """Get current campaign status with AI-generated summary."""
        db = context.db
        
        # Fetch campaign
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        # Fetch proposal
        result = await db.execute(
            select(Proposal).where(Proposal.id == campaign.proposal_id)
        )
        proposal = result.scalar_one_or_none()
        
        # Build status summary
        status_data = {
            "id": str(campaign.id),
            "status": campaign.status.value,
            "phase": campaign.current_phase,
            "budget_allocated": float(campaign.budget_allocated),
            "budget_spent": float(campaign.budget_spent),
            "budget_remaining": float(campaign.budget_allocated - campaign.budget_spent),
            "revenue_generated": float(campaign.revenue_generated),
            "net_profit": float(campaign.revenue_generated - campaign.budget_spent),
            "tasks_total": campaign.tasks_total,
            "tasks_completed": campaign.tasks_completed,
            "success_metrics": campaign.success_metrics,
            "requirements_met": campaign.all_requirements_met,
            "start_date": campaign.start_date.isoformat() if campaign.start_date else None,
            "last_activity": campaign.last_activity_at.isoformat() if campaign.last_activity_at else None,
        }
        
        if proposal:
            status_data["proposal_title"] = proposal.title
        
        return AgentResult(
            success=True,
            message=f"Campaign status: {campaign.status.value}",
            data=status_data,
        )
    
    async def pause_campaign(
        self,
        context: AgentContext,
        campaign_id: UUID,
        reason: Optional[str] = None,
    ) -> AgentResult:
        """Pause an active campaign."""
        db = context.db
        
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        old_status = campaign.status.value
        
        if campaign.status not in [CampaignStatus.ACTIVE, CampaignStatus.WAITING_FOR_INPUTS]:
            return AgentResult(
                success=False,
                message=f"Cannot pause campaign in {campaign.status.value} status",
            )
        
        campaign.status = CampaignStatus.PAUSED
        campaign.current_phase = CampaignPhase.PAUSED
        campaign.last_activity_at = utc_now()
        await db.commit()
        
        # Emit WebSocket event for real-time updates
        await campaign_progress_service.emit_status_change(
            campaign_id=campaign_id,
            old_status=old_status,
            new_status="paused",
            reason=reason
        )
        
        return AgentResult(
            success=True,
            message=f"Campaign paused{f': {reason}' if reason else ''}",
            data={"status": "paused"},
        )
    
    async def resume_campaign(
        self,
        context: AgentContext,
        campaign_id: UUID,
    ) -> AgentResult:
        """Resume a paused campaign."""
        db = context.db
        
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        if campaign.status != CampaignStatus.PAUSED:
            return AgentResult(
                success=False,
                message=f"Campaign is not paused (status: {campaign.status.value})",
            )
        
        # Determine appropriate status
        if campaign.all_requirements_met:
            campaign.status = CampaignStatus.ACTIVE
            campaign.current_phase = CampaignPhase.EXECUTING
        else:
            campaign.status = CampaignStatus.WAITING_FOR_INPUTS
            campaign.current_phase = CampaignPhase.REQUIREMENTS_GATHERING
        
        campaign.last_activity_at = utc_now()
        await db.commit()
        
        # Emit WebSocket event for real-time updates
        await campaign_progress_service.emit_status_change(
            campaign_id=campaign_id,
            old_status="paused",
            new_status=campaign.status.value,
        )
        
        return AgentResult(
            success=True,
            message=f"Campaign resumed ({campaign.status.value})",
            data={"status": campaign.status.value},
        )
    
    async def terminate_campaign(
        self,
        context: AgentContext,
        campaign_id: UUID,
        reason: str,
    ) -> AgentResult:
        """Terminate a campaign early."""
        db = context.db
        
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            return AgentResult(success=False, message="Campaign not found")
        
        old_status = campaign.status.value
        
        if campaign.status in [CampaignStatus.COMPLETED, CampaignStatus.TERMINATED]:
            return AgentResult(
                success=False,
                message=f"Campaign already {campaign.status.value}",
            )
        
        campaign.status = CampaignStatus.TERMINATED
        campaign.current_phase = CampaignPhase.TERMINATING
        campaign.end_date = utc_now()
        campaign.last_activity_at = utc_now()
        await db.commit()
        
        # Emit WebSocket event for real-time updates
        await campaign_progress_service.emit_status_change(
            campaign_id=campaign_id,
            old_status=old_status,
            new_status="terminated",
            reason=reason
        )
        
        return AgentResult(
            success=True,
            message=f"Campaign terminated: {reason}",
            data={
                "status": "terminated",
                "reason": reason,
                "final_spend": float(campaign.budget_spent),
                "final_revenue": float(campaign.revenue_generated),
            },
        )
    
    # ==========================================================================
    # Main Execute Method (required by BaseAgent)
    # ==========================================================================
    
    async def execute(self, context: AgentContext, **kwargs) -> AgentResult:
        """
        Execute the Campaign Manager's main task.
        
        Supported kwargs:
            action: "initialize" | "step" | "status" | "pause" | "resume" | "terminate" | "chat"
            campaign_id: UUID of campaign (for step/status/pause/resume/terminate)
            proposal_id: UUID of proposal (for initialize)
            user_id: UUID of user (for initialize)
            user_message: str (for chat)
            reason: str (for terminate)
        """
        action = kwargs.get("action", "step")
        
        if action == "initialize":
            proposal_id = kwargs.get("proposal_id")
            user_id = kwargs.get("user_id")
            if not proposal_id or not user_id:
                return AgentResult(success=False, message="proposal_id and user_id required for initialize")
            return await self.initialize_campaign(context, proposal_id, user_id)
        
        elif action == "step":
            campaign_id = kwargs.get("campaign_id")
            if not campaign_id:
                return AgentResult(success=False, message="campaign_id required for step")
            return await self.execute_campaign_step(context, campaign_id)
        
        elif action == "status":
            campaign_id = kwargs.get("campaign_id")
            if not campaign_id:
                return AgentResult(success=False, message="campaign_id required for status")
            return await self.get_campaign_status(context, campaign_id)
        
        elif action == "pause":
            campaign_id = kwargs.get("campaign_id")
            reason = kwargs.get("reason")
            if not campaign_id:
                return AgentResult(success=False, message="campaign_id required for pause")
            return await self.pause_campaign(context, campaign_id, reason)
        
        elif action == "resume":
            campaign_id = kwargs.get("campaign_id")
            if not campaign_id:
                return AgentResult(success=False, message="campaign_id required for resume")
            return await self.resume_campaign(context, campaign_id)
        
        elif action == "terminate":
            campaign_id = kwargs.get("campaign_id")
            reason = kwargs.get("reason", "Manual termination")
            if not campaign_id:
                return AgentResult(success=False, message="campaign_id required for terminate")
            return await self.terminate_campaign(context, campaign_id, reason)
        
        elif action == "chat":
            user_message = kwargs.get("user_message")
            if not user_message:
                return AgentResult(success=False, message="user_message required for chat")
            campaign_context = kwargs.get("campaign_context")
            return await self.chat(context, user_message, campaign_context)
        
        elif action == "process_input":
            campaign_id = kwargs.get("campaign_id")
            user_message = kwargs.get("user_message")
            if not campaign_id or not user_message:
                return AgentResult(success=False, message="campaign_id and user_message required for process_input")
            return await self.process_user_input(context, campaign_id, user_message)
        
        else:
            return AgentResult(success=False, message=f"Unknown action: {action}")


# Singleton instance
campaign_manager_agent = CampaignManagerAgent()
