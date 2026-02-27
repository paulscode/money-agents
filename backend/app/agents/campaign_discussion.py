"""Campaign Discussion Agent - discuss campaign progress with AI assistant.

This agent enables intelligent conversations about campaigns, providing
context-aware assistance with full access to campaign data.
"""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.prompt_injection_guard import get_security_preamble
from app.models import Tool, ConversationType, Campaign
from app.services.llm_service import LLMMessage, StreamChunk
from app.services.campaign_context_service import CampaignContextService, CampaignContext

logger = logging.getLogger(__name__)


class CampaignDiscussionAgent(BaseAgent):
    """
    Agent for discussing campaign progress, strategy, and details.
    
    Capabilities:
    - Analyze campaign progress and identify issues
    - Explain stream/task status and blockers
    - Help with input decisions
    - Provide strategic recommendations
    - Answer questions about campaign data
    
    Future capabilities (Phase 2):
    - Provide user inputs via <campaign_action> tags
    - Add notes and decisions
    - Adjust priorities
    """
    
    name = "campaign_discussion"
    description = "Discusses campaign progress and provides assistance"
    default_temperature = 0.7
    default_max_tokens = 4000
    
    # Use fast tier for responsiveness in discussion
    model_tier = "fast"
    
    # CampaignDiscussion does not make <tool_call> tags — uses <campaign_action> only
    TOOL_ALLOWLIST: list[str] | None = []
    
    def get_system_prompt(
        self,
        tools: List[Tool],
        campaign_context: Optional[CampaignContext] = None,
    ) -> str:
        """Build the system prompt for campaign discussion."""
        
        # Build context section
        context_section = ""
        if campaign_context:
            context_service = CampaignContextService.__new__(CampaignContextService)
            context_section = context_service.format_context_for_prompt(campaign_context)
        
        security_preamble = get_security_preamble("<campaign_action>")

        return f"""You are a Campaign Discussion Assistant for Money Agents, an AI-powered system for automated money-making campaigns.

{security_preamble}

{context_section}

## Your Role

You help users understand and manage their campaigns through natural conversation. You have access to all campaign data including progress, streams, tasks, inputs, and metrics.

## Your Communication Style

- **Be conversational**: Chat naturally, not like a formal report
- **Be concise**: Give direct answers without unnecessary padding
- **Be helpful**: Anticipate follow-up questions and provide actionable insights
- **Be honest**: If something is going wrong, say so clearly
- **Use data**: Reference specific numbers and statuses from the campaign

## Your Capabilities

1. **Answer Questions**
   - Explain campaign status and progress
   - Describe what each stream/task does
   - Clarify why something is blocked
   - Interpret metrics and budget usage

2. **Provide Analysis**
   - Identify bottlenecks and blockers
   - Assess progress against goals
   - Spot potential issues early
   - Suggest optimizations

3. **Give Recommendations**
   - Suggest input values when asked
   - Recommend priorities
   - Advise on strategy adjustments
   - Help with decision-making

4. **Context Awareness**
   - You can see the campaign's current state
   - You know about all streams and their tasks
   - You can see pending inputs needed from the user
   - You understand budget and timeline constraints

## Taking Actions

You can take DIRECT ACTIONS on the campaign using special XML tags. When you include these tags in your response, the system will parse them and present them to the user for confirmation before executing.

### Available Actions

**1. Provide User Input** - Fill in a required input value:
```xml
<campaign_action type="provide_input" key="brand_guidelines">
Your suggested value here - can be multiple lines
</campaign_action>
```

**2. Update Campaign Status** - Pause, resume, or cancel the campaign:
```xml
<campaign_action type="update_status" new_status="paused">
Reason for the status change
</campaign_action>
```
Valid statuses: pending, active, paused, completed, failed, cancelled

**3. Add a Note** - Record a decision, observation, or milestone:
```xml
<campaign_action type="add_note" category="decision">
The note content to record
</campaign_action>
```
Categories: decision, observation, milestone, concern

**4. Prioritize a Stream** - Bump a stream to highest priority:
```xml
<campaign_action type="prioritize_stream" stream_name="content_production">
Reason for prioritizing this stream
</campaign_action>
```

**5. Skip a Task** - Mark a task as skipped (no longer needed):
```xml
<campaign_action type="skip_task" task_id="uuid-here" reason="No longer relevant">
Additional explanation if needed
</campaign_action>
```

### Action Guidelines

1. **Only use actions when the user asks** - Don't proactively add actions unless the user wants you to make changes
2. **Explain what the action will do** - Before the action tag, tell the user what you're about to do
3. **Ask for confirmation if uncertain** - If you're not sure about a value, ask first rather than guessing
4. **One action at a time** - For major changes, use one action per response so the user can approve each
5. **Multiple inputs are OK** - If filling multiple inputs at once, you can include multiple provide_input actions

### When NOT to use actions

- Just explaining what something is (no action needed)
- Giving advice without explicit request to make changes
- When the user is just asking questions
- When you'd need information you don't have

## Guidelines

1. **Focus on THIS campaign** - All your answers should be about the current campaign context provided above.

2. **Be specific** - When discussing progress, reference actual task names, stream names, and numbers.

3. **Acknowledge limitations** - If you don't have information about something, say so.

4. **Encourage progress** - Help users feel informed and in control, not overwhelmed.

## Example Interactions

**User**: "How's the campaign going?"
**You**: "Your campaign is 31% complete with 4 of 13 tasks done. The research stream is nearly finished (2/3 tasks), but content production is waiting on your brand guidelines input. Budget looks healthy at $12 of $100 spent."

**User**: "Why is content production blocked?"
**You**: "It needs the brand_guidelines input from you before it can start. This tells the system what visual style, colors, and tone to use for your content. Would you like help deciding what to put there?"

**User**: "Yes, please fill in the brand guidelines for me"
**You**: "Based on your proposal targeting home décor enthusiasts aged 25-45, I'll set your brand guidelines:

<campaign_action type="provide_input" key="brand_guidelines">
**Visual Style:** Minimalist watercolor aesthetic with soft, organic shapes
**Color Palette:** Earth tones (sage green, terracotta, dusty rose) with cream backgrounds
**Mood:** Calm, sophisticated, nature-inspired
**Avoid:** Neon colors, harsh edges, busy patterns
</campaign_action>

Click 'Apply' if this looks good, or let me know if you'd like to adjust anything!"

Remember: You're a helpful assistant for campaign management. Help the user understand their campaign and make good decisions.
"""

    async def think_with_tools_stream(
        self,
        user_message: str,
        context: AgentContext,
        conversation_history: Optional[List[LLMMessage]] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream response with campaign context.
        
        Overrides base to include campaign context in system prompt.
        Uses smart context analysis in Phase 3 for optimized token usage.
        """
        # Get available tools (for future use)
        tools = await self.get_available_tools(context.db)
        
        # Get campaign context from AgentContext extra data
        campaign_context = context.extra.get("campaign_context")
        
        # If we have a related_id (campaign_id) but no context, build it
        if not campaign_context and context.related_id:
            logger.info(f"Building smart campaign context for {context.related_id}")
            context_service = CampaignContextService(context.db)
            
            # Try to use smart context with query analysis (Phase 3)
            try:
                campaign_context = await context_service.build_smart_context(
                    campaign_id=context.related_id,
                    user_message=user_message,
                    model=model or "default",
                )
                if campaign_context.meta.compression_applied:
                    logger.info(
                        f"Smart context applied compression: "
                        f"{campaign_context.meta.total_tokens} tokens "
                        f"(tier1={campaign_context.meta.tier1_tokens}, "
                        f"tier2={campaign_context.meta.tier2_tokens})"
                    )
            except Exception as e:
                logger.warning(f"Smart context failed, falling back to basic: {e}")
                campaign_context = await context_service.build_full_context(
                    campaign_id=context.related_id,
                    include_tier2=True,
                    include_tier3=False,
                )
        
        # Build system prompt with campaign context
        system_prompt = self.get_system_prompt(tools, campaign_context=campaign_context)
        
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
    
    async def execute(self, context: AgentContext, **kwargs) -> AgentResult:
        """
        Execute a discussion action.
        
        Supported kwargs:
            action: "respond" (default)
            user_message: str - The user's message
        """
        action = kwargs.get("action", "respond")
        
        if action == "respond":
            user_message = kwargs.get("user_message", "")
            if not user_message:
                return AgentResult(success=False, message="No user message provided")
            return await self.respond_to_message(context, user_message)
        else:
            return AgentResult(success=False, message=f"Unknown action: {action}")
    
    async def respond_to_message(
        self,
        context: AgentContext,
        user_message: str,
        save_to_conversation: bool = True,
    ) -> AgentResult:
        """
        Respond to a user message in the campaign discussion.
        
        Args:
            context: Agent context with db session and conversation_id
            user_message: The user's message
            save_to_conversation: Whether to save the response
            
        Returns:
            AgentResult with the response
        """
        try:
            # Get conversation history
            history = []
            if context.conversation_id:
                history = await self.get_conversation_history(context, limit=30)
            
            # Get campaign context
            campaign_context = context.extra.get("campaign_context")
            if not campaign_context and context.related_id:
                context_service = CampaignContextService(context.db)
                campaign_context = await context_service.build_full_context(
                    campaign_id=context.related_id,
                    include_tier2=True,
                )
                context.extra["campaign_context"] = campaign_context
            
            # Generate response
            tools = await self.get_available_tools(context.db)
            system_prompt = self.get_system_prompt(tools, campaign_context=campaign_context)
            
            messages = [LLMMessage(role="system", content=system_prompt)]
            messages.extend(history)
            messages.append(LLMMessage(role="user", content=user_message))
            
            response = await self.think(messages)
            
            # Save to conversation
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


# Singleton instance
campaign_discussion_agent = CampaignDiscussionAgent()
