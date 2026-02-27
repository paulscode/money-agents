"""
Campaign Processor for Remote Workers.

Handles campaign execution on remote workers, communicating with the
central backend via the broker protocol for state management.

This is the remote equivalent of the backend's CampaignWorkerLoop,
but instead of direct database access, it communicates via WebSocket
messages to the broker.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Set
from dataclasses import dataclass, field

from config import Config
from llm_client import LLMClient, LLMMessage, LLMResponse

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


@dataclass
class CampaignState:
    """Represents a campaign's current state."""
    id: str
    status: str
    current_phase: str
    proposal_title: str
    proposal_summary: str
    budget_allocated: float
    budget_spent: float
    revenue_generated: float
    tasks_total: int
    tasks_completed: int
    success_metrics: Dict[str, Any] = field(default_factory=dict)
    requirements_checklist: List[Dict[str, Any]] = field(default_factory=list)
    all_requirements_met: bool = False
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    available_tools: List[Dict[str, Any]] = field(default_factory=list)
    # Model settings from campaign assignment (defaults match Campaign Manager agent)
    model_tier: str = "reasoning"  # fast, reasoning, quality
    max_tokens: int = 6000  # Standard: always use 6000


class CampaignProcessor:
    """
    Processes campaigns on a remote worker.
    
    Key responsibilities:
    - Claim and manage campaign leases via broker
    - Execute campaign steps using LLM
    - Parse and execute tool calls
    - Handle user input
    - Report progress and events back to broker
    """
    
    # Campaign statuses that can be processed
    PROCESSABLE_STATUSES = {
        "active", "initializing", "requirements_gathering",
        "executing", "monitoring"
    }
    
    def __init__(
        self,
        config: Config,
        send_message_func,
    ):
        """
        Initialize campaign processor.
        
        Args:
            config: Agent configuration with campaign worker settings
            send_message_func: Async function to send messages to broker
        """
        self.config = config
        self._send_message = send_message_func
        
        # LLM client - check for any available provider (new multi-provider support)
        self._llm: Optional[LLMClient] = None
        if config.campaign_worker.enabled and config.campaign_worker.get_available_providers():
            self._llm = LLMClient(config.campaign_worker)
        
        # Campaign tracking
        self._held_campaigns: Dict[str, CampaignState] = {}  # campaign_id -> state
        self._pending_tool_results: Dict[str, asyncio.Future] = {}  # tool_exec_id -> Future
        
        # Worker info
        self.worker_id = f"remote-{config.agent.get_name()}"
        self.max_campaigns = config.campaign_worker.max_campaigns
        
        # Background task handles
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._running = False
    
    @property
    def is_enabled(self) -> bool:
        """Check if campaign worker mode is enabled and configured."""
        return (
            self.config.campaign_worker.enabled and
            self._llm is not None
        )
    
    @property
    def current_campaign_count(self) -> int:
        """Number of campaigns currently held."""
        return len(self._held_campaigns)
    
    @property
    def available_slots(self) -> int:
        """Number of additional campaigns we can accept."""
        return max(0, self.max_campaigns - self.current_campaign_count)
    
    async def start(self):
        """Start the campaign processor."""
        if not self.is_enabled:
            logger.info("Campaign worker mode not enabled, skipping")
            return
        
        self._running = True
        
        # Register as campaign worker with broker
        await self._register_worker()
        
        # Start heartbeat loop
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        logger.info(
            f"Campaign processor started: {self.worker_id}, "
            f"max_campaigns={self.max_campaigns}"
        )
    
    async def stop(self):
        """Stop the campaign processor."""
        self._running = False
        
        # Cancel heartbeat
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        
        # Release all held campaigns
        for campaign_id in list(self._held_campaigns.keys()):
            await self._release_campaign(campaign_id, reason="worker_shutdown")
        
        # Disconnect worker
        await self._send_message({
            "type": "worker_disconnect",
            "data": {
                "worker_id": self.worker_id,
                "graceful": True,
            }
        })
        
        # Close LLM client
        if self._llm:
            await self._llm.close()
        
        logger.info("Campaign processor stopped")
    
    # =========================================================================
    # Broker Communication
    # =========================================================================
    
    async def _register_worker(self):
        """Register as a campaign worker with the broker."""
        await self._send_message({
            "type": "worker_register",
            "data": {
                "worker_id": self.worker_id,
                "worker_type": "remote",
                "max_campaigns": self.max_campaigns,
                "hostname": self.config.agent.get_name(),
                "capabilities": {
                    "campaign_worker": True,
                    "llm_provider": self.config.campaign_worker.llm_provider,
                }
            }
        })
    
    async def _send_heartbeat(self):
        """Send heartbeat to broker with campaign IDs."""
        await self._send_message({
            "type": "worker_heartbeat",
            "data": {
                "worker_id": self.worker_id,
                "campaign_ids": list(self._held_campaigns.keys()),
                "available_slots": self.available_slots,
            }
        })
    
    async def _heartbeat_loop(self):
        """Background loop for periodic heartbeats."""
        interval = self.config.campaign_worker.heartbeat_interval
        
        while self._running:
            try:
                await asyncio.sleep(interval)
                if self._running and self._held_campaigns:
                    await self._send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    async def _release_campaign(
        self,
        campaign_id: str,
        new_status: Optional[str] = None,
        reason: str = "release",
    ):
        """Release a campaign lease."""
        await self._send_message({
            "type": "campaign_release",
            "data": {
                "worker_id": self.worker_id,
                "campaign_id": campaign_id,
                "new_status": new_status,
                "reason": reason,
            }
        })
        self._held_campaigns.pop(campaign_id, None)
        logger.info(f"Released campaign {campaign_id} ({reason})")
    
    async def _report_progress(
        self,
        campaign_id: str,
        phase: str,
        message: str,
        data: Optional[Dict] = None,
    ):
        """Report campaign progress to broker."""
        await self._send_message({
            "type": "campaign_progress",
            "data": {
                "worker_id": self.worker_id,
                "campaign_id": campaign_id,
                "phase": phase,
                "message": message,
                "data": data or {},
                "timestamp": utc_now().isoformat(),
            }
        })
    
    async def _request_tool_execution(
        self,
        campaign_id: str,
        tool_slug: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Request tool execution from the broker.
        
        The broker will route this to the appropriate resource host
        and return the result.
        """
        import uuid
        exec_id = str(uuid.uuid4())
        
        # Create future for result
        future = asyncio.get_event_loop().create_future()
        self._pending_tool_results[exec_id] = future
        
        # Send request
        await self._send_message({
            "type": "tool_dispatch",
            "data": {
                "execution_id": exec_id,
                "worker_id": self.worker_id,
                "campaign_id": campaign_id,
                "tool_slug": tool_slug,
                "params": params,
            }
        })
        
        try:
            # Wait for result (with timeout)
            result = await asyncio.wait_for(future, timeout=300)  # 5 min timeout
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": "Tool execution timed out"}
        finally:
            self._pending_tool_results.pop(exec_id, None)
    
    # =========================================================================
    # Message Handlers (called by broker_client)
    # =========================================================================
    
    async def handle_campaign_assigned(self, data: Dict[str, Any]):
        """Handle campaign assignment from broker."""
        campaign_id = data.get("campaign_id")
        campaign_data = data.get("campaign", {})
        
        logger.info(f"Campaign assigned: {campaign_id}")
        
        # Create campaign state
        state = CampaignState(
            id=campaign_id,
            status=campaign_data.get("status", "active"),
            current_phase=campaign_data.get("current_phase", "executing"),
            proposal_title=campaign_data.get("proposal_title", ""),
            proposal_summary=campaign_data.get("proposal_summary", ""),
            budget_allocated=campaign_data.get("budget_allocated", 0),
            budget_spent=campaign_data.get("budget_spent", 0),
            revenue_generated=campaign_data.get("revenue_generated", 0),
            tasks_total=campaign_data.get("tasks_total", 0),
            tasks_completed=campaign_data.get("tasks_completed", 0),
            success_metrics=campaign_data.get("success_metrics", {}),
            requirements_checklist=campaign_data.get("requirements_checklist", []),
            all_requirements_met=campaign_data.get("all_requirements_met", False),
            conversation_history=campaign_data.get("conversation_history", []),
            available_tools=campaign_data.get("available_tools", []),
            # Model settings from campaign assignment (honor Campaign Manager config)
            model_tier=campaign_data.get("model_tier", "reasoning"),
            max_tokens=campaign_data.get("max_tokens", 6000),
        )
        
        self._held_campaigns[campaign_id] = state
        
        # Acknowledge and start processing
        await self._send_message({
            "type": "campaign_accepted",
            "data": {
                "worker_id": self.worker_id,
                "campaign_id": campaign_id,
            }
        })
        
        # Start processing in background
        asyncio.create_task(self._process_campaign(campaign_id))
    
    async def handle_campaign_revoked(self, data: Dict[str, Any]):
        """Handle campaign revocation from broker."""
        campaign_id = data.get("campaign_id")
        reason = data.get("reason", "revoked")
        
        logger.warning(f"Campaign revoked: {campaign_id} ({reason})")
        self._held_campaigns.pop(campaign_id, None)
    
    async def handle_user_input(self, data: Dict[str, Any]):
        """Handle user input for a campaign."""
        campaign_id = data.get("campaign_id")
        user_message = data.get("message", "")
        
        state = self._held_campaigns.get(campaign_id)
        if not state:
            logger.warning(f"User input for unknown campaign: {campaign_id}")
            return
        
        logger.info(f"User input for campaign {campaign_id}: {user_message[:50]}...")
        
        # Add to conversation history
        state.conversation_history.append({
            "role": "user",
            "content": user_message,
        })
        
        # Process the input
        await self._process_user_input(campaign_id, user_message)
    
    async def handle_tool_result(self, data: Dict[str, Any]):
        """Handle tool execution result from broker."""
        exec_id = data.get("execution_id")
        result = data.get("result", {})
        
        future = self._pending_tool_results.get(exec_id)
        if future and not future.done():
            future.set_result(result)
    
    async def handle_campaign_state_update(self, data: Dict[str, Any]):
        """Handle campaign state update from broker."""
        campaign_id = data.get("campaign_id")
        updates = data.get("updates", {})
        
        state = self._held_campaigns.get(campaign_id)
        if state:
            # Update relevant fields
            for key, value in updates.items():
                if hasattr(state, key):
                    setattr(state, key, value)
    
    # =========================================================================
    # Campaign Processing Logic
    # =========================================================================
    
    async def _process_campaign(self, campaign_id: str):
        """Process a campaign step."""
        state = self._held_campaigns.get(campaign_id)
        if not state:
            return
        
        # Check if processable
        if state.status.lower() not in self.PROCESSABLE_STATUSES:
            await self._release_campaign(
                campaign_id,
                reason=f"status_{state.status}",
            )
            return
        
        # Check requirements
        if not state.all_requirements_met:
            await self._report_progress(
                campaign_id,
                phase="requirements_gathering",
                message="Waiting for requirements to be met",
            )
            return
        
        # Determine next action using LLM
        try:
            result = await self._determine_next_action(state)
            
            if result.get("type") == "execute_tool":
                await self._execute_tool_action(campaign_id, result)
            elif result.get("type") == "request_input":
                await self._request_user_input_action(campaign_id, result)
            elif result.get("type") == "complete":
                await self._complete_campaign(campaign_id)
            elif result.get("type") == "wait":
                await self._report_progress(
                    campaign_id,
                    phase=state.current_phase,
                    message=result.get("reason", "Waiting"),
                )
            else:
                await self._report_progress(
                    campaign_id,
                    phase=state.current_phase,
                    message="No action required at this time",
                )
                
        except Exception as e:
            logger.exception(f"Error processing campaign {campaign_id}: {e}")
            await self._report_progress(
                campaign_id,
                phase="error",
                message=f"Processing error: {str(e)}",
            )
    
    async def _process_user_input(self, campaign_id: str, user_message: str):
        """Process user input for a campaign."""
        state = self._held_campaigns.get(campaign_id)
        if not state:
            return
        
        # Build messages for LLM
        messages = self._build_llm_messages(state, user_message)
        
        try:
            # Use model_tier and max_tokens from campaign state
            response = await self._llm.chat(
                messages,
                model_tier=state.model_tier,
                max_tokens=state.max_tokens,
            )
            
            # Add response to history
            state.conversation_history.append({
                "role": "assistant",
                "content": response.content,
            })
            
            # Parse response for requirements completion
            completed = self._parse_requirement_completions(response.content)
            
            # Parse for tool calls
            tool_calls = self._parse_tool_calls(response.content)
            
            # Execute any tool calls
            tool_results = []
            for call in tool_calls:
                result = await self._request_tool_execution(
                    campaign_id,
                    call["tool_slug"],
                    call["params"],
                )
                tool_results.append({
                    "tool_slug": call["tool_slug"],
                    "result": result,
                })
            
            # Report the response with full token tracking
            await self._send_message({
                "type": "campaign_response",
                "data": {
                    "worker_id": self.worker_id,
                    "campaign_id": campaign_id,
                    "content": response.content,
                    "requirements_completed": completed,
                    "tool_results": tool_results,
                    # Token tracking
                    "tokens_used": response.total_tokens,
                    "prompt_tokens": response.prompt_tokens,
                    "completion_tokens": response.completion_tokens,
                    # Model tracking  
                    "model_used": response.model,
                    "provider_used": response.provider,
                    "model_tier": state.model_tier,
                    # Latency
                    "latency_ms": response.latency_ms,
                }
            })
            
        except Exception as e:
            logger.exception(f"Error processing user input: {e}")
            await self._send_message({
                "type": "campaign_error",
                "data": {
                    "worker_id": self.worker_id,
                    "campaign_id": campaign_id,
                    "error": str(e),
                }
            })
    
    async def _determine_next_action(self, state: CampaignState) -> Dict[str, Any]:
        """Use LLM to determine next action for campaign."""
        # Build context prompt
        prompt = f"""Analyze the current campaign state and determine the next action.

## Campaign: {state.proposal_title}

**Status:** {state.status}
**Phase:** {state.current_phase}
**Budget:** ${state.budget_spent:.2f} / ${state.budget_allocated:.2f}
**Tasks:** {state.tasks_completed}/{state.tasks_total}

## Recent Conversation
{self._format_conversation(state.conversation_history[-5:])}

## Available Tools
{self._format_tools(state.available_tools[:10])}

## Your Task

Determine the next action. Respond with ONE of:

1. **Execute Tool:**
<action type="execute_tool">
{{"tool_slug": "tool-name", "params": {{"key": "value"}}}}
</action>

2. **Request User Input:**
<action type="request_input">
{{"message": "What you need from the user", "priority": "blocking|high|medium|low"}}
</action>

3. **Complete Campaign:**
<action type="complete">
{{"reason": "Why the campaign is complete"}}
</action>

4. **Wait:**
<action type="wait">
{{"reason": "Why we should wait"}}
</action>

Analyze the situation and respond with the appropriate action tag.
"""
        
        messages = [
            LLMMessage(
                role="system",
                content="You are the Campaign Manager Agent. Analyze campaign state and determine the next action."
            ),
            LLMMessage(role="user", content=prompt),
        ]
        
        # Use fast tier for action determination (simple analysis)
        response = await self._llm.chat(messages, model_tier="fast", temperature=0.3)
        
        # Parse action from response
        return self._parse_action(response.content)
    
    async def _execute_tool_action(self, campaign_id: str, action: Dict[str, Any]):
        """Execute a tool action."""
        tool_slug = action.get("tool_slug")
        params = action.get("params", {})
        
        logger.info(f"Executing tool {tool_slug} for campaign {campaign_id}")
        
        result = await self._request_tool_execution(campaign_id, tool_slug, params)
        
        # Report result
        await self._report_progress(
            campaign_id,
            phase="executing",
            message=f"Executed tool: {tool_slug}",
            data={"tool_result": result},
        )
    
    async def _request_user_input_action(self, campaign_id: str, action: Dict[str, Any]):
        """Request user input."""
        message = action.get("message", "")
        priority = action.get("priority", "medium")
        
        await self._send_message({
            "type": "campaign_user_input_request",
            "data": {
                "worker_id": self.worker_id,
                "campaign_id": campaign_id,
                "message": message,
                "priority": priority,
            }
        })
    
    async def _complete_campaign(self, campaign_id: str):
        """Complete a campaign."""
        await self._release_campaign(
            campaign_id,
            new_status="completed",
            reason="campaign_complete",
        )
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _build_llm_messages(
        self,
        state: CampaignState,
        user_message: Optional[str] = None,
    ) -> List[LLMMessage]:
        """Build LLM messages from campaign state."""
        messages = [
            LLMMessage(
                role="system",
                content=self._build_system_prompt(state),
            ),
        ]
        
        # Add conversation history
        for msg in state.conversation_history[-10:]:
            messages.append(LLMMessage(
                role=msg["role"],
                content=msg["content"],
            ))
        
        # Add current user message if provided
        if user_message:
            messages.append(LLMMessage(role="user", content=user_message))
        
        return messages
    
    def _build_system_prompt(self, state: CampaignState) -> str:
        """Build system prompt for campaign."""
        # Sanitize user-supplied metadata to prevent prompt injection (GAP-14)
        safe_title = re.sub(r'<[^>]+>', '', state.proposal_title or 'Untitled')
        safe_summary = re.sub(r'<[^>]+>', '', state.proposal_summary or '')
        
        return f"""You are the Campaign Manager Agent for Money Agents.

## Current Campaign: {safe_title}

**Summary:** {safe_summary}
**Budget:** ${state.budget_spent:.2f} / ${state.budget_allocated:.2f}
**Tasks:** {state.tasks_completed}/{state.tasks_total}

## Your Role
Execute this campaign autonomously, making decisions within budget/scope.
Request user input when needed. Report progress transparently.

## Available Tools
{self._format_tools(state.available_tools[:10])}

## Tool Calls
To execute a tool:
<tool_call name="tool-slug">{{"param": "value"}}</tool_call>

## Handling Tool Errors
When a tool returns an error, check the error type:
- **RATE_LIMIT_EXCEEDED**: Wait before retrying (check retry_after), or use an alternative.
- **RESOURCE_UNAVAILABLE**: Resource offline. Wait or proceed with other tasks.
- **QUEUE_TIMEOUT / RESOURCE_BUSY**: Resource busy. Try again later.
- **APPROVAL_REQUIRED**: Tool needs human approval. Request input from user to approve.

## Marking Requirements Complete
<requirement_completed>EXACT_REQUIREMENT_TEXT</requirement_completed>
"""
    
    def _format_conversation(self, messages: List[Dict]) -> str:
        """Format conversation messages for prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "user").upper()
            content = msg.get("content", "")[:200]
            lines.append(f"[{role}]: {content}")
        return "\n".join(lines) if lines else "(No recent messages)"
    
    def _format_tools(self, tools: List[Dict]) -> str:
        """Format tools for prompt."""
        if not tools:
            return "(No tools available)"
        
        # Map interface types to human-readable names
        interface_labels = {
            "rest_api": "REST API",
            "cli": "CLI",
            "python_sdk": "Python SDK",
            "mcp": "MCP",
        }
        
        lines = []
        for tool in tools:
            slug = tool.get("slug", "unknown")
            name = tool.get("name", slug)
            desc = tool.get("description", "")[:100]
            interface_type = tool.get("interface_type", "rest_api")
            label = interface_labels.get(interface_type, interface_type)
            lines.append(f"- **{slug}** [{label}]: {name} - {desc}")
        return "\n".join(lines)
    
    def _parse_action(self, content: str) -> Dict[str, Any]:
        """Parse action from LLM response."""
        import json
        
        # Look for <action type="...">...</action>
        pattern = r'<action\s+type="(\w+)">\s*({.*?})\s*</action>'
        match = re.search(pattern, content, re.DOTALL)
        
        if match:
            action_type = match.group(1)
            try:
                params = json.loads(match.group(2))
                return {"type": action_type, **params}
            except json.JSONDecodeError:
                pass
        
        return {"type": "wait", "reason": "Could not determine action"}
    
    def _parse_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Parse tool calls from response."""
        import json
        
        pattern = r'<tool_call\s+name="([^"]+)">\s*({.*?})\s*</tool_call>'
        calls = []
        
        for match in re.finditer(pattern, content, re.DOTALL):
            tool_slug = match.group(1)
            try:
                params = json.loads(match.group(2))
                calls.append({"tool_slug": tool_slug, "params": params})
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in tool call: {match.group(2)[:100]}")
        
        return calls
    
    def _parse_requirement_completions(self, content: str) -> List[str]:
        """Parse requirement completions from response."""
        pattern = r'<requirement_completed>([^<]+)</requirement_completed>'
        return re.findall(pattern, content)
