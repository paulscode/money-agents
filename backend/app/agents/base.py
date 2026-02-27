"""Base agent class with tool discovery and LLM integration."""
from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from app.core.datetime_utils import utc_now
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Tool, ToolStatus, Message, Conversation, SenderType, ConversationType
from app.services.llm_service import LLMMessage, LLMResponse, StreamChunk, llm_service
from app.services.prompt_injection_guard import (
    strip_action_tags,
    injection_monitor,
)

logger = logging.getLogger(__name__)


# Regex patterns for stripping edit tags from conversation history
# These match <proposal_edit field="...">...</proposal_edit> and <tool_edit field="...">...</tool_edit>
# Including multi-line content and CDATA sections
EDIT_TAG_PATTERNS = [
    # proposal_edit tags (with potential CDATA)
    re.compile(
        r'<proposal_edit\s+field="([^"]+)">\s*(?:<!\[CDATA\[[\s\S]*?\]\]>|[\s\S]*?)\s*</proposal_edit>',
        re.MULTILINE
    ),
    # tool_edit tags (with potential CDATA)
    re.compile(
        r'<tool_edit\s+field="([^"]+)">\s*(?:<!\[CDATA\[[\s\S]*?\]\]>|[\s\S]*?)\s*</tool_edit>',
        re.MULTILINE
    ),
]


def strip_edit_tags(content: str, applied_edits: Optional[List[str]] = None) -> str:
    """
    Strip edit suggestion tags from message content for conversation history.
    
    Replaces verbose edit tags with a brief summary to save tokens while
    preserving context about what changes were suggested/applied.
    
    Args:
        content: Message content that may contain edit tags
        applied_edits: List of field names that were applied (from message metadata)
        
    Returns:
        Cleaned content with edit tags replaced by brief summaries
    """
    applied_set = set(applied_edits) if applied_edits else set()
    
    def replace_edit_tag(match: re.Match, tag_type: str) -> str:
        field_name = match.group(1)
        if field_name in applied_set:
            return f"[{tag_type}: {field_name} - APPLIED]"
        else:
            return f"[{tag_type}: {field_name} - suggested]"
    
    result = content
    
    # Replace proposal_edit tags
    result = EDIT_TAG_PATTERNS[0].sub(
        lambda m: replace_edit_tag(m, "Edit"), 
        result
    )
    
    # Replace tool_edit tags
    result = EDIT_TAG_PATTERNS[1].sub(
        lambda m: replace_edit_tag(m, "Edit"), 
        result
    )
    
    # Clean up any excessive whitespace left behind
    result = re.sub(r'\n{3,}', '\n\n', result)
    
    return result.strip()


@dataclass
class AgentContext:
    """Context passed to agent for a task."""
    
    db: AsyncSession
    conversation_id: Optional[UUID] = None
    related_id: Optional[UUID] = None  # proposal_id, campaign_id, etc.
    user_id: Optional[UUID] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result returned from agent execution."""
    
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    tokens_used: int = 0
    cost_usd: Optional[float] = None
    model_used: Optional[str] = None
    latency_ms: int = 0


class BaseAgent(ABC):
    """
    Base class for all Money Agents.
    
    Provides:
    - Tool discovery from the database
    - LLM integration with fallback
    - Message sending to conversations
    - System prompt management
    """
    
    # Subclasses should override these
    name: str = "base_agent"
    description: str = "Base agent class"
    default_model: Optional[str] = None  # None = use fallback chain
    default_temperature: float = 0.7
    default_max_tokens: int = 6000  # High limit - we only pay for tokens actually used
    
    # Model tier selection
    # The LLM service handles tier resolution with provider failover.
    # Tiers: "fast" (cheap/quick), "reasoning" (deep thinking), "quality" (best output)
    # Formats: "fast", "reasoning", "claude:reasoning", "openai:fast"
    # See LLMService.MODEL_TIERS for model mappings
    model_tier: Optional[str] = None
    
    # Tool allowlist — restricts which tools this agent can invoke.
    # None  = no restriction (allow any tool)
    # []    = deny all tools (fail-closed for agents that don't use tools)
    # [...] = only listed slugs are permitted
    # Subclasses that make <tool_call> must set this to a list of permitted slugs.
    # See: internal_docs/PROMPT_INJECTION_AUDIT.md (PI-01)
    TOOL_ALLOWLIST: Optional[List[str]] = None
    
    def __init__(self) -> None:
        self._tools_cache: Optional[List[Tool]] = None
        self._tools_cache_time: Optional[datetime] = None
        self._cache_ttl_seconds: int = 300  # 5 minutes
    
    # -------------------------------------------------------------------------
    # Tool Discovery
    # -------------------------------------------------------------------------
    
    async def get_available_tools(
        self,
        db: AsyncSession,
        category: Optional[str] = None,
        force_refresh: bool = False,
    ) -> List[Tool]:
        """
        Get all implemented tools from the catalog.
        
        Args:
            db: Database session
            category: Optional category filter (api, data_source, automation, analysis, communication)
            force_refresh: Force cache refresh
            
        Returns:
            List of available Tool objects
        """
        now = utc_now()
        
        # Check cache validity
        if (
            not force_refresh
            and self._tools_cache is not None
            and self._tools_cache_time is not None
            and (now - self._tools_cache_time).total_seconds() < self._cache_ttl_seconds
        ):
            tools = self._tools_cache
        else:
            # Fetch from database
            query = select(Tool).where(Tool.status == ToolStatus.IMPLEMENTED)
            result = await db.execute(query)
            tools = list(result.scalars().all())
            
            # Update cache
            self._tools_cache = tools
            self._tools_cache_time = now
            logger.debug(f"Refreshed tools cache: {len(tools)} tools available")
        
        # Filter by category if specified
        if category:
            tools = [t for t in tools if t.category.value == category]
        
        return tools
    
    def format_tools_for_prompt(
        self,
        tools: List[Tool],
        verbosity: str = "full",
        include_call_format: bool = True,
    ) -> str:
        """
        Format tools into a string suitable for inclusion in a system prompt.
        
        Args:
            tools: List of Tool objects
            verbosity: Detail level — "compact", "standard", or "full"
                - compact: name, slug, one-line description (~80 tokens/tool)
                - standard: + category, type, cost (~120 tokens/tool)
                - full: + strengths, weaknesses, best_use_cases (~350 tokens/tool)
            include_call_format: Whether to include <tool_call> format instructions.
                Set False for agents that don't make tool calls.
            
        Returns:
            Formatted string describing available tools
        """
        if not tools:
            return "No tools are currently available."
        
        lines = ["## Available Tools\n"]
        
        if include_call_format:
            lines.append("You can call tools using this format:")
            lines.append("```")
            lines.append('<tool_call name="tool-slug">{"param": "value"}</tool_call>')
            lines.append("```\n")
            lines.append("**Rules for tool calls:**")
            lines.append("- Use the exact tool slug (shown in parentheses)")
            lines.append("- Parameters must be valid JSON")
            lines.append("- You can make multiple tool calls in one response")
            lines.append("- Wait for tool results before continuing if you need them")
            lines.append("- Tool results will be provided in a follow-up message")
            lines.append("- If you see RATE_LIMIT_EXCEEDED error, wait and retry later or use a different tool\n")
        
        for tool in tools:
            if verbosity == "compact":
                desc = (tool.description[:120] + "...") if len(tool.description) > 120 else tool.description
                lines.append(f"- **{tool.name}** (`{tool.slug}`): {desc}")
            else:
                lines.append(f"### {tool.name} (`{tool.slug}`)")
                lines.append(f"**Category:** {tool.category.value}")
                
                # Show interface type if configured
                if tool.interface_type:
                    interface_labels = {
                        "rest_api": "REST API",
                        "cli": "Command Line",
                        "python_sdk": "Python SDK",
                        "mcp": "MCP Server",
                        "internal": "Internal"
                    }
                    lines.append(f"**Type:** {interface_labels.get(tool.interface_type, tool.interface_type)}")
                
                lines.append(f"**Description:** {tool.description}")
                if tool.usage_instructions:
                    usage_preview = tool.usage_instructions.split('\n')[0][:200]
                    lines.append(f"**Usage:** {usage_preview}")
                if tool.cost_model:
                    lines.append(f"**Cost:** {tool.cost_model}")
                
                # Full verbosity adds strengths/weaknesses/best_use_cases
                if verbosity == "full":
                    if tool.strengths:
                        lines.append(f"**Strengths:** {tool.strengths}")
                    if tool.weaknesses:
                        lines.append(f"**Weaknesses:** {tool.weaknesses}")
                    if tool.best_use_cases:
                        lines.append(f"**Best For:** {tool.best_use_cases}")
                
                lines.append("")
        
        return "\n".join(lines)
    
    # -------------------------------------------------------------------------
    # Tool Call Parsing
    # -------------------------------------------------------------------------
    
    def parse_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse tool calls from agent response content.
        
        Looks for: <tool_call name="slug">{"params": "..."}</tool_call>
        
        Includes output validation: tool call params are checked for
        suspicious injection indicators (data boundary markers, canary
        tokens, etc.) and flagged via injection_monitor.
        
        Args:
            content: Agent response text
            
        Returns:
            List of dicts with 'tool_slug' and 'params'
        """
        import re
        import json
        
        tool_calls = []
        pattern = r'<tool_call\s+name=["\']([^"\']+)["\']>([\s\S]*?)</tool_call>'
        
        for match in re.finditer(pattern, content, re.IGNORECASE):
            tool_slug = match.group(1).strip()
            params_str = match.group(2).strip()
            
            # --- Output validation: detect injection indicators ---
            suspicious_indicators = []
            tag_text = match.group(0)
            if "---BEGIN EXTERNAL DATA" in tag_text or "---END EXTERNAL DATA" in tag_text:
                suspicious_indicators.append("data_boundary_marker_in_tool_call")
            if "CNRY-" in tag_text:
                suspicious_indicators.append("canary_token_in_tool_call")
            if re.search(r'ignore\s+(?:all\s+)?(?:previous\s+)?instructions', tag_text, re.IGNORECASE):
                suspicious_indicators.append("injection_phrase_in_tool_call")
            if suspicious_indicators:
                injection_monitor.log_suspicious_output(
                    agent=self.name, indicators=suspicious_indicators,
                )
            
            # Try to parse params as JSON
            try:
                params = json.loads(params_str) if params_str else {}
            except json.JSONDecodeError:
                # SA2-25: Skip tool call if params can't be parsed — don't execute with empty params
                logger.warning(f"Skipping tool call '{tool_slug}': failed to parse params as JSON: {params_str[:100]}")
                continue
            
            tool_calls.append({
                "tool_slug": tool_slug,
                "params": params,
                "original_tag": match.group(0),
            })
        
        # Cap the number of tool calls to prevent abuse
        return tool_calls[:20]
    
    def remove_tool_call_tags(self, content: str) -> str:
        """Remove tool call tags from content for clean display."""
        import re
        return re.sub(
            r'<tool_call\s+name=["\'][^"\']+["\']>[\s\S]*?</tool_call>',
            '',
            content,
            flags=re.IGNORECASE
        ).strip()
    
    def has_tool_calls(self, content: str) -> bool:
        """Check if content contains any tool calls."""
        return '<tool_call' in content.lower()
    
    async def execute_tool_calls(
        self,
        context: AgentContext,
        tool_calls: List[Dict[str, Any]],
        presented_tool_slugs: Optional[set] = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute a list of tool calls and return results.
        
        Enforces three layers of tool access control:
        1. TOOL_ALLOWLIST — per-agent restriction (class attribute)
        2. presented_tool_slugs — only tools the LLM was shown this turn
        3. Risk-tier rate limiting — limits calls per tier per iteration
        
        Args:
            context: Agent context with db session
            tool_calls: List of parsed tool calls
            presented_tool_slugs: Set of slugs that were included in the prompt
            
        Returns:
            List of results with tool_slug, success, output/error
        """
        from app.services.tool_execution_service import tool_execution_service
        from app.services.prompt_injection_guard import (
            get_tool_risk_tier, TOOL_RATE_LIMITS,
        )
        
        results = []
        tier_counts: dict[str, int] = {}  # track calls per risk tier
        
        for call in tool_calls:
            slug = call["tool_slug"]
            
            # --- Security gate: per-agent tool allowlist ---
            if self.TOOL_ALLOWLIST is not None and slug not in self.TOOL_ALLOWLIST:
                injection_monitor.log_blocked_tool(
                    agent=self.name, tool_slug=slug,
                    reason="not in agent TOOL_ALLOWLIST",
                )
                results.append({
                    "tool_slug": slug,
                    "success": False,
                    "error": f"Tool '{slug}' is not permitted for this agent.",
                })
                continue
            
            # --- Security gate: only call tools that were presented ---
            if presented_tool_slugs and slug not in presented_tool_slugs:
                injection_monitor.log_blocked_tool(
                    agent=self.name, tool_slug=slug,
                    reason="tool was not presented in prompt",
                )
                results.append({
                    "tool_slug": slug,
                    "success": False,
                    "error": f"Tool '{slug}' was not presented and cannot be called.",
                })
                continue
            
            # --- Security gate: risk-tier rate limiting ---
            tier = get_tool_risk_tier(slug)
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            limit = TOOL_RATE_LIMITS.get(tier, 3)
            if tier_counts[tier] > limit:
                injection_monitor.log_blocked_tool(
                    agent=self.name, tool_slug=slug,
                    reason=f"rate limit exceeded for {tier}-risk tier "
                           f"({tier_counts[tier]}/{limit})",
                )
                results.append({
                    "tool_slug": slug,
                    "success": False,
                    "error": (
                        f"Rate limit: too many {tier}-risk tool calls in one "
                        f"iteration (max {limit}). Try again in the next turn."
                    ),
                })
                continue
            
            try:
                execution = await tool_execution_service.execute_tool_by_slug(
                    db=context.db,
                    tool_slug=call["tool_slug"],
                    params=call["params"],
                    conversation_id=context.conversation_id,
                    user_id=context.user_id,
                    agent_name=self.name,
                    campaign_id=context.related_id,
                )
                
                if execution.status.value == "completed":
                    results.append({
                        "tool_slug": call["tool_slug"],
                        "success": True,
                        "output": execution.output_result,
                        "duration_ms": execution.duration_ms,
                    })
                else:
                    results.append({
                        "tool_slug": call["tool_slug"],
                        "success": False,
                        "error": execution.error_message,
                    })
            except ValueError as e:
                results.append({
                    "tool_slug": call["tool_slug"],
                    "success": False,
                    "error": str(e),
                })
            except Exception as e:
                logger.exception(f"Error executing tool {call['tool_slug']}")
                results.append({
                    "tool_slug": call["tool_slug"],
                    "success": False,
                    "error": f"Execution error: {str(e)}",
                })
        
        return results
    
    def format_tool_results(self, results: List[Dict[str, Any]]) -> str:
        """
        Format tool execution results as a message to feed back to the agent.
        
        All tool outputs are sanitized via sanitize_external_content() and
        wrapped with data boundary markers to prevent indirect prompt
        injection through tool results (Nostr posts, search results,
        parsed documents, REST API responses, etc.).
        
        Categorizes errors to help agents respond appropriately:
        - RATE_LIMIT_EXCEEDED: Wait and retry later, or use alternative tool
        - RESOURCE_UNAVAILABLE: Resource offline/maintenance, use alternative or wait
        - QUEUE_TIMEOUT: Resource busy, try again later
        - APPROVAL_REQUIRED: Tool needs human approval before execution
        - Other errors: Tool-specific issues to troubleshoot
        
        Args:
            results: List of tool execution results
            
        Returns:
            Formatted string describing results
        """
        import json
        from app.services.prompt_injection_guard import (
            sanitize_external_content, wrap_external_content,
        )
        
        lines = ["Here are the results from your tool calls:\n"]
        
        for result in results:
            slug = result['tool_slug']
            lines.append(f"**Tool: `{slug}`**")
            if result.get("success"):
                output = result.get("output", {})
                if isinstance(output, dict):
                    raw = json.dumps(output, indent=2)
                else:
                    raw = str(output)
                # Sanitize + wrap tool output to prevent indirect injection
                sanitized, _detections = sanitize_external_content(
                    raw, source=f"tool:{slug}",
                )
                wrapped = wrap_external_content(sanitized, source=f"tool:{slug}")
                lines.append(wrapped)
                if result.get("duration_ms"):
                    lines.append(f"*(completed in {result['duration_ms']}ms)*")
            else:
                error = result.get("error", "Unknown error")
                
                # Categorize error types for better agent understanding
                if "RATE_LIMIT_EXCEEDED" in error:
                    lines.append(f"**Error (Rate Limited):** {error}")
                    lines.append("*→ This tool has hit its usage limit. Wait before retrying or use an alternative tool.*")
                elif "RESOURCE_UNAVAILABLE" in error:
                    lines.append(f"**Error (Resource Unavailable):** {error}")
                    lines.append("*→ The required resource is offline or in maintenance. Try again later or use a different approach.*")
                elif "QUEUE_TIMEOUT" in error:
                    lines.append(f"**Error (Queue Timeout):** {error}")
                    lines.append("*→ The resource is busy. The request was queued but timed out waiting. Try again later.*")
                elif "RESOURCE_BUSY" in error:
                    lines.append(f"**Error (Resource Busy):** {error}")
                    lines.append("*→ The resource is currently in use. Wait for it to become available.*")
                elif "APPROVAL_REQUIRED" in error:
                    lines.append(f"**Error (Approval Required):** {error}")
                    lines.append("*→ This tool requires human approval before execution. Request approval from an admin and wait for confirmation before retrying.*")
                else:
                    lines.append(f"**Error:** {error}")
            lines.append("")
        
        return "\n".join(lines)
    
    # -------------------------------------------------------------------------
    # LLM Integration
    # -------------------------------------------------------------------------
    
    @abstractmethod
    def get_system_prompt(self, tools: List[Tool]) -> str:
        """
        Get the system prompt for this agent.
        
        Subclasses must implement this to define their behavior.
        
        Args:
            tools: Available tools to include in prompt
            
        Returns:
            System prompt string
        """
        pass
    
    async def think(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """
        Send messages to the LLM and get a response.
        
        Uses agent defaults if parameters not specified.
        
        Args:
            messages: Conversation history
            model: Override model (None = use fallback chain or model_tier)
            temperature: Override temperature
            max_tokens: Override max tokens
            
        Returns:
            LLM response
        """
        # Resolve model: explicit > model_tier > default_model > fallback chain
        resolved_model = model or self._resolve_model()
        return await llm_service.generate(
            messages=messages,
            model=resolved_model,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens if max_tokens is not None else self.default_max_tokens,
        )

    async def think_stream(
        self,
        messages: List[LLMMessage],
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream messages from the LLM.
        
        Uses agent defaults if parameters not specified.
        
        Args:
            messages: Conversation history
            model: Override model (None = use fallback chain or model_tier)
            temperature: Override temperature
            max_tokens: Override max tokens
            
        Yields:
            StreamChunk objects
        """
        # Resolve model: explicit > model_tier > default_model > fallback chain
        resolved_model = model or self._resolve_model()
        async for chunk in llm_service.generate_stream(
            messages=messages,
            model=resolved_model,
            temperature=temperature if temperature is not None else self.default_temperature,
            max_tokens=max_tokens if max_tokens is not None else self.default_max_tokens,
        ):
            yield chunk
    
    def _resolve_model(self) -> Optional[str]:
        """
        Resolve which model specification to use.
        
        Returns the model_tier (e.g., "fast", "reasoning", "claude:quality")
        or default_model. The LLM service handles actual model selection
        and provider failover.
        """
        return self.model_tier or self.default_model
    
    async def think_with_tools(
        self,
        user_message: str,
        context: AgentContext,
        conversation_history: Optional[List[LLMMessage]] = None,
        model: Optional[str] = None,
    ) -> LLMResponse:
        """
        Think about a user message with tool awareness.
        
        Builds the full message list including system prompt with tools.
        
        Args:
            user_message: The user's message
            context: Agent context with db session
            conversation_history: Optional prior messages
            model: Override model
            
        Returns:
            LLM response
        """
        # Get available tools
        tools = await self.get_available_tools(context.db)
        
        # Build system prompt
        system_prompt = self.get_system_prompt(tools)
        
        # Build message list
        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt)
        ]
        
        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current user message
        messages.append(LLMMessage(role="user", content=user_message))
        
        return await self.think(messages, model=model)

    async def think_with_tools_stream(
        self,
        user_message: str,
        context: AgentContext,
        conversation_history: Optional[List[LLMMessage]] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream a response to a user message with tool awareness.
        
        Builds the full message list including system prompt with tools.
        
        Args:
            user_message: The user's message
            context: Agent context with db session
            conversation_history: Optional prior messages
            model: Override model
            
        Yields:
            StreamChunk objects
        """
        # Get available tools
        tools = await self.get_available_tools(context.db)
        
        # Build system prompt
        system_prompt = self.get_system_prompt(tools)
        
        # Build message list
        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt)
        ]
        
        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current user message
        messages.append(LLMMessage(role="user", content=user_message))
        
        async for chunk in self.think_stream(messages, model=model):
            yield chunk
    
    async def think_and_execute_tools(
        self,
        user_message: str,
        context: AgentContext,
        conversation_history: Optional[List[LLMMessage]] = None,
        model: Optional[str] = None,
        max_tool_iterations: int = 3,
    ) -> LLMResponse:
        """
        Think about a user message, execute any tool calls, and continue until done.
        
        This implements the agent loop:
        1. Generate response
        2. Check for tool calls
        3. If tools called: execute them, feed results back, go to step 1
        4. If no tools: return final response
        
        Args:
            user_message: The user's message
            context: Agent context with db session
            conversation_history: Optional prior messages
            model: Override model
            max_tool_iterations: Maximum number of tool execution rounds
            
        Returns:
            Final LLM response (with any tool call tags removed)
        """
        # Get available tools
        tools = await self.get_available_tools(context.db)
        
        # Track which tool slugs were actually presented to the LLM
        presented_tool_slugs = {t.slug for t in tools} if tools else set()
        
        # Build system prompt
        system_prompt = self.get_system_prompt(tools)
        
        # Build message list
        messages: List[LLMMessage] = [
            LLMMessage(role="system", content=system_prompt)
        ]
        
        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history)
        
        # Add current user message
        messages.append(LLMMessage(role="user", content=user_message))
        
        total_tokens = 0
        final_response = None
        
        for iteration in range(max_tool_iterations + 1):
            # Get response
            response = await self.think(messages, model=model)
            total_tokens += response.total_tokens
            
            # Check for tool calls
            tool_calls = self.parse_tool_calls(response.content)
            
            if not tool_calls or iteration == max_tool_iterations:
                # No tool calls or max iterations reached - return final response
                final_response = LLMResponse(
                    content=self.remove_tool_call_tags(response.content),
                    model=response.model,
                    provider=response.provider,
                    latency_ms=response.latency_ms,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    total_tokens=total_tokens,
                )
                break
            
            # Execute tool calls (with allowlist + presented-tool validation)
            logger.info(f"Agent {self.name} executing {len(tool_calls)} tool calls (iteration {iteration + 1})")
            results = await self.execute_tool_calls(
                context, tool_calls,
                presented_tool_slugs=presented_tool_slugs,
            )
            
            # Add assistant response and tool results to messages
            messages.append(LLMMessage(role="assistant", content=response.content))
            messages.append(LLMMessage(role="user", content=self.format_tool_results(results)))
        
        return final_response
    
    # -------------------------------------------------------------------------
    # Conversation Integration
    # -------------------------------------------------------------------------
    
    async def send_message(
        self,
        context: AgentContext,
        content: str,
        tokens_used: int = 0,
        model_used: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        cost_usd: Optional[float] = None,
    ) -> Message:
        """
        Send a message to the conversation as this agent.
        
        Args:
            context: Agent context with db and conversation_id
            content: Message content
            tokens_used: Number of tokens used (total, for display only)
            model_used: Model that generated the response (for display only)
            prompt_tokens: Number of input tokens (for display only)
            completion_tokens: Number of output tokens (for display only)
            cost_usd: Deprecated — cost tracking is now handled by llm_usage table.
                       Kept for API compatibility but NOT stored on messages.
            
        Returns:
            Created Message object
        """
        if not context.conversation_id:
            raise ValueError("No conversation_id in context")
        
        # Note: cost_usd is NOT stored on messages. The llm_usage table is
        # the single source of truth for all LLM cost tracking.
        message = Message(
            conversation_id=context.conversation_id,
            sender_type=SenderType.AGENT,
            sender_id=None,  # Agents don't have user IDs
            content=content,
            content_format="markdown",
            tokens_used=tokens_used,
            model_used=model_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            meta_data={"agent_name": self.name},
        )
        context.db.add(message)
        await context.db.flush()
        await context.db.refresh(message)
        
        logger.info(
            f"Agent {self.name} sent message",
            extra={"conversation_id": str(context.conversation_id), "message_id": str(message.id)},
        )
        
        return message
    
    async def get_or_create_conversation(
        self,
        context: AgentContext,
        conversation_type: ConversationType,
        title: Optional[str] = None,
    ) -> Conversation:
        """
        Get existing conversation for related entity or create new one.
        
        Args:
            context: Agent context
            conversation_type: Type of conversation
            title: Optional title for new conversation
            
        Returns:
            Conversation object
        """
        # Try to find existing conversation
        if context.related_id:
            query = select(Conversation).where(
                Conversation.conversation_type == conversation_type,
                Conversation.related_id == context.related_id,
            )
            result = await context.db.execute(query)
            existing = result.scalar_one_or_none()
            if existing:
                return existing
        
        # Create new conversation
        # Use a system user ID if no user in context
        user_id = context.user_id
        if not user_id:
            # Find system user
            from app.models import User
            result = await context.db.execute(
                select(User).where(User.username == "system")
            )
            system_user = result.scalar_one_or_none()
            if system_user:
                user_id = system_user.id
            else:
                raise ValueError("No user_id in context and no system user found")
        
        conversation = Conversation(
            created_by_user_id=user_id,
            conversation_type=conversation_type,
            related_id=context.related_id,
            title=title or f"{self.name} conversation",
        )
        context.db.add(conversation)
        await context.db.flush()
        await context.db.refresh(conversation)
        
        # Update context with new conversation ID
        context.conversation_id = conversation.id
        
        return conversation
    
    async def get_conversation_history(
        self,
        context: AgentContext,
        limit: int = 50,
        strip_edits: bool = True,
    ) -> List[LLMMessage]:
        """
        Get conversation history as LLMMessages.
        
        Args:
            context: Agent context with conversation_id
            limit: Maximum messages to retrieve
            strip_edits: If True, strip verbose edit tags to save tokens
            
        Returns:
            List of LLMMessages
        """
        if not context.conversation_id:
            return []
        
        query = (
            select(Message)
            .where(Message.conversation_id == context.conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        result = await context.db.execute(query)
        messages = list(result.scalars().all())
        
        # Reverse to chronological order
        messages.reverse()
        
        # Convert to LLMMessages
        llm_messages = []
        for msg in messages:
            if msg.sender_type == SenderType.USER:
                role = "user"
            elif msg.sender_type == SenderType.AGENT:
                role = "assistant"
            else:
                role = "system"
            
            content = msg.content
            
            # Strip edit tags from agent messages to save tokens
            # The LLM already has access to current field values in the system prompt
            if strip_edits and msg.sender_type == SenderType.AGENT:
                applied_edits = None
                if msg.meta_data and isinstance(msg.meta_data, dict):
                    applied_edits = msg.meta_data.get('applied_edits')
                content = strip_edit_tags(content, applied_edits)
            
            # Strip action tags from ALL messages so stored messages
            # cannot inject tool_call / campaign_action / task tags
            # when replayed to the LLM in future turns.  (PI-09)
            content = strip_action_tags(content)
            
            llm_messages.append(LLMMessage(role=role, content=content))
        
        return llm_messages
    
    # -------------------------------------------------------------------------
    # Main Execution
    # -------------------------------------------------------------------------
    
    @abstractmethod
    async def execute(self, context: AgentContext, **kwargs) -> AgentResult:
        """
        Execute the agent's main task.
        
        Subclasses must implement this.
        
        Args:
            context: Agent execution context
            **kwargs: Task-specific arguments
            
        Returns:
            AgentResult with success status and data
        """
        pass
    
    async def respond_to_message(
        self,
        context: AgentContext,
        user_message: str,
        save_to_conversation: bool = True,
    ) -> AgentResult:
        """
        Respond to a user message in a conversation.
        
        Convenience method for chat-style interactions.
        
        Args:
            context: Agent context
            user_message: The user's message
            save_to_conversation: Whether to save messages to conversation (requires conversation_id)
            
        Returns:
            AgentResult with the response
        """
        try:
            # Get conversation history if we have a conversation
            history = []
            if context.conversation_id:
                history = await self.get_conversation_history(context)
            
            # Generate response
            response = await self.think_with_tools(
                user_message=user_message,
                context=context,
                conversation_history=history,
            )
            
            # Send message to conversation if requested and we have one
            message_id = None
            if save_to_conversation and context.conversation_id:
                message = await self.send_message(
                    context=context,
                    content=response.content,
                    tokens_used=response.total_tokens,
                    model_used=response.model,
                )
                message_id = str(message.id)
            
            return AgentResult(
                success=True,
                message="Response generated",
                data={"message_id": message_id, "content": response.content},
                tokens_used=response.total_tokens,
                model_used=response.model,
                latency_ms=response.latency_ms,
            )
            
        except Exception as e:
            logger.exception(f"Agent {self.name} failed to respond")
            return AgentResult(
                success=False,
                message=f"Failed to respond: {str(e)}",
            )

    async def respond_to_message_stream(
        self,
        context: AgentContext,
        user_message: str,
        model_override: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream a response to a user message in a conversation.
        
        Args:
            context: Agent context
            user_message: The user's message
            model_override: Optional model/tier override (e.g., "fast", "claude:quality")
            
        Yields:
            StreamChunk objects (final chunk includes metadata)
        """
        # Get conversation history if we have a conversation
        history = []
        if context.conversation_id:
            history = await self.get_conversation_history(context)
        
        # Stream response
        async for chunk in self.think_with_tools_stream(
            user_message=user_message,
            context=context,
            conversation_history=history,
            model=model_override,
        ):
            yield chunk
