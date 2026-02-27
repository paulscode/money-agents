"""Proposal Writer Agent - refines and improves proposals via chat."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.models import Tool, ConversationType
from app.services.llm_service import LLMMessage, StreamChunk
from app.services.prompt_injection_guard import get_security_preamble

logger = logging.getLogger(__name__)


class ProposalWriterAgent(BaseAgent):
    """
    Agent that helps users refine and improve campaign proposals.
    
    Capabilities:
    - Analyze proposal quality and completeness
    - Suggest improvements to proposals
    - Answer questions about proposal details
    - Help with risk assessment
    - Recommend tools for implementation
    """
    
    name = "proposal_writer"
    description = "Helps refine and improve campaign proposals"
    default_temperature = 0.7
    default_max_tokens = 6000  # High limit - we only pay for tokens actually used
    
    # Use reasoning model for proposal writing (complex analysis)
    # Can override to "fast" for quick Q&A in chat
    model_tier = "reasoning"
    
    # ProposalWriter does not make <tool_call> tags — uses <proposal_edit> only
    TOOL_ALLOWLIST: list[str] | None = []
    
    def get_system_prompt(self, tools: List[Tool], proposal_context: Optional[Dict[str, Any]] = None) -> str:
        """Build the system prompt for the Proposal Writer agent."""
        # Compact: this agent doesn't make tool_calls, it uses <proposal_edit> tags
        tools_section = self.format_tools_for_prompt(tools, verbosity="compact", include_call_format=False)
        
        # Build proposal context section if provided
        proposal_section = ""
        if proposal_context:
            proposal_section = self._build_proposal_section(proposal_context)
        
        security_preamble = get_security_preamble("<proposal_edit>")

        base_prompt = f"""You are the Proposal Writer Agent for Money Agents, an AI-powered system for automated money-making campaigns.

{security_preamble}

{proposal_section}

## Your Communication Style

- **Be concise**: Give short, direct answers. Avoid lengthy explanations unless asked.
- **Be conversational**: Write like you're chatting with a colleague, not writing documentation.
- **Be actionable**: Focus on what the user can do, not theory.
- **Ask one question at a time**: If you need clarification, ask a single focused question.
- **Use markdown sparingly**: Only for lists or emphasis when it truly helps readability.

## Your Capabilities

- Analyze proposal quality and identify gaps
- Suggest concrete improvements to increase success probability
- Assess risks and recommend mitigation strategies
- Recommend appropriate tools and resources
- Help users think through their ideas
- **Edit proposal fields directly** when asked or when you notice issues

## Making Proposal Edits

When you want to suggest a change to the proposal, use this special format:

<proposal_edit field="FIELD_NAME">
NEW_VALUE
</proposal_edit>

**Available fields you can edit:**
- `title` - The proposal title (string)
- `summary` - Brief summary (string)
- `detailed_description` - Full description (string, supports markdown)
- `initial_budget` - Budget amount (number, no $ or commas)
- `bitcoin_budget_sats` - Bitcoin spending budget in satoshis (number, omit if campaign doesn't need BTC)
- `bitcoin_budget_rationale` - Why this campaign needs to spend Bitcoin (string)
- `risk_level` - One of: low, medium, high
- `risk_description` - Description of risks (string)

**Examples:**

To fix a typo in the title:
<proposal_edit field="title">AI-Powered Etsy Print-on-Demand Store</proposal_edit>

To update the budget:
<proposal_edit field="initial_budget">750</proposal_edit>

To improve the risk description:
<proposal_edit field="risk_description">Market saturation is the primary risk. Mitigation: Focus on unique niches and build brand recognition early.</proposal_edit>

**Important rules for edits:**
- Only include the edit tag when you're actually making a change
- The user will see a preview and must click "Apply" to confirm
- You can include multiple edits in one response
- Always explain WHY you're suggesting the edit before or after the tag
- For `initial_budget`, use just the number (e.g., 500 not $500)
- For `bitcoin_budget_sats`, use whole number of satoshis (e.g., 100000 for 100k sats)

**Escaping rules (IMPORTANT):**
- If your content contains `</proposal_edit>` as literal text, use CDATA: `<![CDATA[content here]]>`
- For content with code blocks or HTML, wrap in CDATA to be safe:

<proposal_edit field="detailed_description"><![CDATA[
## Overview
This is markdown with `code` and <html> tags.

```python
print("Safe inside CDATA")
```
]]></proposal_edit>

- Outside of CDATA, escape these characters: `<` → `&lt;`, `>` → `&gt;`, `&` → `&amp;`
- When in doubt, use CDATA for any field containing code or technical content

## Guidelines

1. **Stay focused on THIS proposal** when one is provided. Don't suggest creating new proposals.

2. **When suggesting changes**: Be specific and use the edit format above so users can apply changes with one click.

3. **For risk assessment**: Be honest about risks but also constructive about mitigations.

4. **When asked about tools**: Reference only tools available in the system.

5. **If something is unclear**: Ask one clarifying question rather than making assumptions.

6. **Bitcoin budgets**: If the opportunity involves Bitcoin revenue or spending (e.g., Lightning payments,
   Nostr zaps, on-chain transactions), suggest an appropriate `bitcoin_budget_sats` and
   `bitcoin_budget_rationale`. Consider the campaign goals and typical transaction sizes.

{tools_section}
"""
        return base_prompt
    
    def _build_proposal_section(self, proposal: Dict[str, Any]) -> str:
        """Build the proposal context section for the system prompt."""
        lines = ["## Current Proposal You're Discussing", ""]
        
        if title := proposal.get("title"):
            lines.append(f"**Title:** {title}")
        
        if status := proposal.get("status"):
            lines.append(f"**Status:** {status}")
        
        if summary := proposal.get("summary"):
            lines.append(f"\n**Summary:** {summary}")
        
        if description := proposal.get("detailed_description"):
            # Truncate very long descriptions
            if len(description) > 1000:
                description = description[:1000] + "..."
            lines.append(f"\n**Details:** {description}")
        
        if budget := proposal.get("initial_budget"):
            lines.append(f"\n**Initial Budget:** ${budget:,.2f}")
        
        if btc_budget := proposal.get("bitcoin_budget_sats"):
            lines.append(f"**Bitcoin Budget:** {btc_budget:,} sats")
            if btc_rationale := proposal.get("bitcoin_budget_rationale"):
                lines.append(f"**BTC Rationale:** {btc_rationale}")
        
        if returns := proposal.get("expected_returns"):
            if isinstance(returns, dict):
                monthly = returns.get("monthly")
                yearly = returns.get("yearly")
                if monthly:
                    lines.append(f"**Expected Returns:** ${monthly}/month")
                elif yearly:
                    lines.append(f"**Expected Returns:** ${yearly}/year")
            else:
                lines.append(f"**Expected Returns:** {returns}")
        
        if risk_level := proposal.get("risk_level"):
            lines.append(f"**Risk Level:** {risk_level}")
        
        if risk_desc := proposal.get("risk_description"):
            lines.append(f"**Risk Notes:** {risk_desc}")
        
        if criteria := proposal.get("success_criteria"):
            if isinstance(criteria, dict):
                items = []
                for k, v in criteria.items():
                    items.append(f"{k}: {v}")
                if items:
                    lines.append(f"**Success Criteria:** {', '.join(items[:3])}")
            else:
                lines.append(f"**Success Criteria:** {criteria}")
        
        if tools := proposal.get("required_tools"):
            if isinstance(tools, dict) and tools:
                tool_names = list(tools.keys())[:5]
                lines.append(f"**Required Tools:** {', '.join(tool_names)}")
        
        if timeline := proposal.get("implementation_timeline"):
            if isinstance(timeline, dict):
                phases = timeline.get("phases", [])
                if phases:
                    lines.append(f"**Timeline:** {len(phases)} phases planned")
        
        lines.append("")
        lines.append("Remember: Help improve THIS specific proposal. The user is actively working on it.")
        
        return "\n".join(lines)
    
    async def think_with_tools_stream(
        self,
        user_message: str,
        context: AgentContext,
        conversation_history: Optional[List[LLMMessage]] = None,
        model: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Override to include proposal context in the system prompt.
        """
        # Get available tools
        tools = await self.get_available_tools(context.db)
        
        # Get proposal context from the AgentContext extra data
        proposal_context = context.extra.get("proposal_context")
        
        # Build system prompt with proposal context
        system_prompt = self.get_system_prompt(tools, proposal_context=proposal_context)
        
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
        Execute proposal analysis or respond to a message.
        
        Supported kwargs:
            action: "analyze" | "respond"
            proposal_data: dict (for analyze)
            user_message: str (for respond)
        """
        action = kwargs.get("action", "respond")
        
        if action == "analyze":
            return await self._analyze_proposal(context, kwargs.get("proposal_data", {}))
        elif action == "respond":
            user_message = kwargs.get("user_message", "")
            if not user_message:
                return AgentResult(success=False, message="No user message provided")
            return await self.respond_to_message(context, user_message)
        else:
            return AgentResult(success=False, message=f"Unknown action: {action}")
    
    async def _analyze_proposal(
        self,
        context: AgentContext,
        proposal_data: dict,
    ) -> AgentResult:
        """
        Analyze a proposal and provide feedback.
        
        Args:
            context: Agent context
            proposal_data: Proposal fields to analyze
            
        Returns:
            AgentResult with analysis
        """
        if not proposal_data:
            return AgentResult(success=False, message="No proposal data provided")
        
        # Format proposal for analysis
        proposal_text = self._format_proposal_for_analysis(proposal_data)
        
        # Build analysis prompt
        analysis_prompt = f"""Please analyze the following proposal and provide detailed feedback.

## Proposal to Analyze

{proposal_text}

## Your Task

1. **Overall Assessment**: Rate the proposal quality (Excellent/Good/Fair/Needs Work)

2. **Strengths**: What's good about this proposal?

3. **Areas for Improvement**: What specific changes would make this proposal better?

4. **Risk Analysis**: Are the risks properly identified and mitigated?

5. **Tool Recommendations**: What tools would be most useful for implementing this?

6. **Questions**: What clarifying questions should be answered?

7. **Action Items**: Specific, actionable steps to improve this proposal.

Provide your analysis in a clear, structured format using markdown."""
        
        try:
            # Get available tools
            tools = await self.get_available_tools(context.db)
            
            # Build messages
            messages = [
                LLMMessage(role="system", content=self.get_system_prompt(tools)),
                LLMMessage(role="user", content=analysis_prompt),
            ]
            
            # Generate analysis
            response = await self.think(messages)
            
            # Optionally send to conversation
            if context.conversation_id:
                await self.send_message(
                    context=context,
                    content=response.content,
                    tokens_used=response.total_tokens,
                    model_used=response.model,
                )
            
            return AgentResult(
                success=True,
                message="Proposal analyzed",
                data={"analysis": response.content},
                tokens_used=response.total_tokens,
                model_used=response.model,
                latency_ms=response.latency_ms,
            )
            
        except Exception as e:
            logger.exception("Failed to analyze proposal")
            return AgentResult(success=False, message=f"Analysis failed: {str(e)}")
    
    def _format_proposal_for_analysis(self, proposal_data: dict) -> str:
        """Format proposal data as readable text."""
        sections = []
        
        if title := proposal_data.get("title"):
            sections.append(f"**Title:** {title}")
        
        if summary := proposal_data.get("summary"):
            sections.append(f"**Summary:** {summary}")
        
        if description := proposal_data.get("detailed_description"):
            sections.append(f"**Detailed Description:**\n{description}")
        
        if budget := proposal_data.get("initial_budget"):
            sections.append(f"**Initial Budget:** ${budget:,.2f}")
        
        if returns := proposal_data.get("expected_returns"):
            sections.append(f"**Expected Returns:** {returns}")
        
        if risk_level := proposal_data.get("risk_level"):
            sections.append(f"**Risk Level:** {risk_level}")
        
        if risk_desc := proposal_data.get("risk_description"):
            sections.append(f"**Risk Description:**\n{risk_desc}")
        
        if criteria := proposal_data.get("success_criteria"):
            sections.append(f"**Success Criteria:** {criteria}")
        
        if tools := proposal_data.get("required_tools"):
            sections.append(f"**Required Tools:** {tools}")
        
        if inputs := proposal_data.get("required_inputs"):
            sections.append(f"**Required Inputs:** {inputs}")
        
        if stop_loss := proposal_data.get("stop_loss_threshold"):
            sections.append(f"**Stop Loss Threshold:** {stop_loss}")
        
        if timeline := proposal_data.get("implementation_timeline"):
            sections.append(f"**Implementation Timeline:** {timeline}")
        
        return "\n\n".join(sections) if sections else "No proposal data provided."
    
    async def suggest_improvements(
        self,
        context: AgentContext,
        proposal_data: dict,
        focus_area: Optional[str] = None,
    ) -> AgentResult:
        """
        Suggest specific improvements for a proposal.
        
        Args:
            context: Agent context
            proposal_data: Current proposal data
            focus_area: Optional specific area to focus on
                        (budget, risk, tools, timeline, etc.)
            
        Returns:
            AgentResult with suggestions
        """
        proposal_text = self._format_proposal_for_analysis(proposal_data)
        
        if focus_area:
            prompt = f"""Focus specifically on improving the **{focus_area}** aspect of this proposal:

{proposal_text}

Provide 3-5 specific, actionable suggestions for improving the {focus_area}."""
        else:
            prompt = f"""Suggest the top 5 most impactful improvements for this proposal:

{proposal_text}

For each suggestion:
1. What to change
2. Why it matters
3. How to implement it"""
        
        try:
            tools = await self.get_available_tools(context.db)
            messages = [
                LLMMessage(role="system", content=self.get_system_prompt(tools)),
                LLMMessage(role="user", content=prompt),
            ]
            
            response = await self.think(messages)
            
            return AgentResult(
                success=True,
                message="Suggestions generated",
                data={"suggestions": response.content},
                tokens_used=response.total_tokens,
                model_used=response.model,
                latency_ms=response.latency_ms,
            )
            
        except Exception as e:
            logger.exception("Failed to generate suggestions")
            return AgentResult(success=False, message=f"Failed: {str(e)}")

    async def refine_from_scout(
        self,
        context: AgentContext,
        proposal_data: dict,
        research_context: dict,
    ) -> AgentResult:
        """
        Refine a draft proposal created from Opportunity Scout.
        
        This is called automatically when an opportunity is approved and
        converted to a draft proposal. The agent uses the research context
        to create a polished, actionable proposal.
        
        Args:
            context: Agent context with DB access
            proposal_data: Current draft proposal fields
            research_context: Data from the source opportunity
            
        Returns:
            AgentResult with refined proposal fields
        """
        # Format research context for the prompt
        research_text = self._format_research_context(research_context)
        proposal_text = self._format_proposal_for_analysis(proposal_data)
        
        refinement_prompt = f"""You are refining a draft proposal that was automatically created from an approved opportunity.

## Research Context from Opportunity Scout

{research_text}

## Current Draft Proposal

{proposal_text}

## Your Task

Transform this draft into a polished, actionable proposal. You must provide SPECIFIC VALUES for each field below.

Return your refinements in this JSON format:
```json
{{
    "title": "Clear, compelling title (no [Draft] prefix)",
    "summary": "2-3 sentence executive summary",
    "detailed_description": "Full description with:\n- Clear problem statement\n- Proposed solution\n- Implementation approach\n- Expected outcomes",
    "initial_budget": 500,
    "recurring_costs": {{
        "monthly": 50,
        "description": "What this covers"
    }},
    "expected_returns": {{
        "monthly_min": 200,
        "monthly_max": 800,
        "timeframe_to_profit": "2-3 months",
        "confidence": "medium"
    }},
    "risk_level": "low|medium|high",
    "risk_description": "Specific risks and mitigations",
    "stop_loss_threshold": {{
        "max_loss_usd": 250,
        "review_trigger_usd": 100,
        "time_limit_days": 30
    }},
    "success_criteria": {{
        "revenue_target": 1000,
        "timeframe": "3 months",
        "milestones": ["First sale", "Break even", "Profit target"]
    }},
    "required_tools": {{
        "tool_name": {{
            "purpose": "Why needed",
            "status": "available|needed",
            "alternative": "If not available"
        }}
    }},
    "required_inputs": {{
        "input_type": {{
            "description": "What's needed",
            "source": "Where to get it"
        }}
    }},
    "implementation_timeline": {{
        "phases": [
            {{"name": "Setup", "duration": "1 week", "tasks": ["task1", "task2"]}},
            {{"name": "Launch", "duration": "2 weeks", "tasks": ["task3", "task4"]}},
            {{"name": "Optimize", "duration": "Ongoing", "tasks": ["task5"]}}
        ]
    }}
}}
```

Focus on:
1. Making the title compelling and specific
2. Writing a clear, action-oriented summary
3. Providing realistic budget estimates based on the research
4. Identifying concrete risks with mitigations
5. Setting measurable success criteria
6. Creating a practical implementation timeline

Return ONLY the JSON, no additional text."""

        try:
            tools = await self.get_available_tools(context.db)
            messages = [
                LLMMessage(role="system", content=self.get_system_prompt(tools)),
                LLMMessage(role="user", content=refinement_prompt),
            ]
            
            # Use quality tier for this important task
            # Higher max_tokens (8192) for complete proposal JSON generation
            self.model_tier = "quality"
            response = await self.think(messages, max_tokens=8192)
            self.model_tier = "reasoning"  # Reset
            
            # Parse the JSON response
            import json
            import re
            
            content = response.content.strip()
            
            # Extract JSON from markdown code block if present
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', content)
            if json_match:
                content = json_match.group(1)
            
            try:
                refined_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse refinement JSON: {e}")
                # Return success=False so the draft stays as-is
                return AgentResult(
                    success=False,
                    message=f"Failed to parse refinement: {e}",
                    data={"raw_response": response.content},
                    tokens_used=response.total_tokens,
                    model_used=response.model,
                )
            
            return AgentResult(
                success=True,
                message="Proposal refined successfully",
                data={"refined_proposal": refined_data},
                tokens_used=response.total_tokens,
                model_used=response.model,
                latency_ms=response.latency_ms,
            )
            
        except Exception as e:
            logger.exception("Failed to refine proposal from scout")
            return AgentResult(success=False, message=f"Refinement failed: {str(e)}")
    
    def _format_research_context(self, research_context: dict) -> str:
        """Format research context from Opportunity Scout."""
        sections = []
        
        # Source information
        if source := research_context.get("source"):
            source_lines = ["### Source Information"]
            if source.get("type"):
                source_lines.append(f"- Type: {source['type']}")
            if source.get("query"):
                source_lines.append(f"- Query: {source['query']}")
            if source.get("urls"):
                urls = source['urls'][:3]  # Limit to 3
                for url in urls:
                    source_lines.append(f"- URL: {url}")
            sections.append("\n".join(source_lines))
        
        # Assessment
        if assessment := research_context.get("assessment"):
            assess_lines = ["### Scout's Assessment"]
            if assessment.get("initial"):
                assess_lines.append(f"**Initial:** {assessment['initial'][:500]}")
            if assessment.get("detailed"):
                assess_lines.append(f"**Detailed:** {assessment['detailed'][:800]}")
            if assessment.get("confidence"):
                assess_lines.append(f"**Confidence:** {assessment['confidence']:.0%}")
            sections.append("\n".join(assess_lines))
        
        # Scoring
        if scoring := research_context.get("scoring"):
            score_lines = ["### Opportunity Score"]
            if scoring.get("overall"):
                score_lines.append(f"- Overall: {scoring['overall']:.2f}")
            if scoring.get("tier"):
                score_lines.append(f"- Tier: {scoring['tier']}")
            if scoring.get("breakdown"):
                breakdown = scoring['breakdown']
                for factor, score in list(breakdown.items())[:5]:
                    score_lines.append(f"- {factor}: {score}")
            sections.append("\n".join(score_lines))
        
        # Requirements
        if requirements := research_context.get("requirements"):
            req_lines = ["### Requirements Identified"]
            if requirements.get("skills"):
                req_lines.append(f"**Skills:** {', '.join(requirements['skills'][:5])}")
            if requirements.get("tools"):
                req_lines.append(f"**Tools:** {', '.join(requirements['tools'][:5])}")
            if requirements.get("blocking"):
                req_lines.append(f"**Blockers:** {', '.join(requirements['blocking'][:3])}")
            sections.append("\n".join(req_lines))
        
        # Timing
        if timing := research_context.get("timing"):
            time_lines = ["### Timing"]
            if timing.get("time_sensitivity"):
                time_lines.append(f"- Sensitivity: {timing['time_sensitivity']}")
            if timing.get("discovered_at"):
                time_lines.append(f"- Discovered: {timing['discovered_at']}")
            sections.append("\n".join(time_lines))
        
        return "\n\n".join(sections) if sections else "No research context available."


# Singleton instance for convenience
proposal_writer_agent = ProposalWriterAgent()
