"""
Spend Advisor Agent — AI assistant for reviewing Bitcoin spend approval requests.

This agent is designed to help users make informed decisions about pending
Bitcoin spend approvals. It has a skeptical bias: it defaults to carefully
verifying all claims and calling out potential concerns.

Uses the "quality" model tier (highest) for maximum analytical capability.
"""
import logging
from typing import AsyncGenerator, Optional
from uuid import UUID

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.models import ConversationType
from app.services.prompt_injection_guard import get_security_preamble, sanitize_external_content

logger = logging.getLogger(__name__)


_SECURITY_PREAMBLE = get_security_preamble("none")

SPEND_ADVISOR_SYSTEM_PROMPT = _SECURITY_PREAMBLE + """

You are the **Bitcoin Spend Advisor** — a meticulous financial review assistant for a campaign management platform that uses Bitcoin (Lightning Network and on-chain) for payments.

## Your Role
You help users decide whether to approve or reject Bitcoin spend requests. You are deliberately cautious and skeptical. Your job is to protect the user's funds by:
- Verifying that claims about the spend are reasonable and well-supported
- Calling out any potential concerns, red flags, or missing context
- Providing clear, fact-based analysis rather than rubber-stamping approvals
- Asking probing questions when the justification seems weak

## Core Principles
1. **Default to skepticism** — Treat each spend as guilty until proven reasonable
2. **Verify amounts** — Cross-reference the spend amount against the campaign budget, historical averages, and the stated purpose
3. **Question vague justifications** — "Miscellaneous expense" or "operational costs" should trigger deeper questioning
4. **Flag unusual patterns** — Rapid sequential spends, round numbers, unfamiliar destinations, spends at unusual times
5. **Context is king** — Always relate the spend back to the campaign's goals and progress
6. **Be direct** — Don't soften your concerns with excessive hedging. State what you see clearly.

## What You Know
You have access to:
- The spend approval request details (amount, destination, justification, trigger reason)
- Budget snapshot (campaign budget, remaining balance, spending history)
- Campaign context (goals, status, progress)
- The decoded invoice or transaction details

## Response Format
When analyzing a spend request, structure your response as:
1. **Summary** — What is being requested, for how much, and why
2. **Budget Impact** — How this affects the remaining budget; percentage consumed
3. **Concerns** — Any red flags or questions (be specific)
4. **Recommendation** — Your honest assessment: approve, reject, or request more info

When the user asks questions, answer directly and concisely.

{context}"""


class SpendAdvisorAgent(BaseAgent):
    """AI advisor for reviewing Bitcoin spend approval requests."""

    name = "spend_advisor"
    description = "Reviews Bitcoin spend approval requests with careful analysis"
    
    # Pure analysis agent — no tool calls allowed
    TOOL_ALLOWLIST: list[str] | None = []
    default_temperature = 0.3  # Low temperature for analytical precision
    default_max_tokens = 6000
    model_tier = "quality"  # Highest model tier — Claude Opus / GPT-4o

    def get_system_prompt(
        self,
        tools: list = None,
        domain_context: Optional[dict] = None,
    ) -> str:
        """Build system prompt with spend approval context."""
        context_section = ""

        if domain_context:
            context_section = "\n## Current Spend Request Context\n"

            # Approval details
            approval = domain_context.get("approval")
            if approval:
                # Sanitize externally-sourced fields (description may come from
                # invoice memos or LLM-generated text)
                san_desc, _ = sanitize_external_content(
                    approval.get('description', 'No description provided'),
                    source="spend_approval"
                )
                san_trigger, _ = sanitize_external_content(
                    approval.get('trigger', 'unknown'),
                    source="spend_approval"
                )
                context_section += f"""
### Spend Request
- **Amount:** {approval.get('amount_sats', 0):,} sats ({approval.get('amount_sats', 0) / 100_000_000:.8f} BTC)
- **Fee Estimate:** {approval.get('fee_estimate_sats', 0):,} sats
- **Trigger:** {san_trigger}
- **Description:** {san_desc}
- **Status:** {approval.get('status', 'unknown')}
"""
                if approval.get('payment_request'):
                    context_section += f"- **Payment Request:** `{approval['payment_request'][:40]}...`\n"
                if approval.get('destination_address'):
                    context_section += f"- **Destination:** `{approval['destination_address']}`\n"

            # Budget context
            budget = domain_context.get("budget_context", {})
            if budget:
                context_section += f"""
### Budget Context
- **Campaign Budget:** {budget.get('campaign_budget_sats', 'Not set')} sats
- **Already Spent:** {budget.get('campaign_spent_sats', 0):,} sats
- **Remaining:** {budget.get('campaign_remaining_sats', 'N/A')} sats
- **Global Safety Limit:** {budget.get('global_limit_sats', 0):,} sats
"""

            # Campaign context
            campaign = domain_context.get("campaign")
            if campaign:
                context_section += f"""
### Campaign Context
- **Campaign:** {campaign.get('title', 'Unknown')} (ID: {campaign.get('id', 'N/A')})
- **Status:** {campaign.get('status', 'unknown')}
- **USD Budget:** ${campaign.get('budget_allocated', 0):,.2f} (spent: ${campaign.get('budget_spent', 0):,.2f})
"""

            # Decoded invoice
            decoded = domain_context.get("decoded_invoice")
            if decoded:
                # Sanitize the description — it's set by the payee (external party)
                san_inv_desc, _ = sanitize_external_content(
                    decoded.get('description', 'None'),
                    source="lightning_invoice"
                )
                context_section += f"""
### Decoded Invoice
- **Destination Node:** `{decoded.get('destination', 'unknown')}`
- **Amount:** {decoded.get('num_satoshis', 0):,} sats
- **Description:** {san_inv_desc}
- **Expiry:** {decoded.get('expiry', 0)} seconds
"""

        return SPEND_ADVISOR_SYSTEM_PROMPT.format(
            context=context_section if context_section else "\nNo specific spend request context loaded yet."
        )

    async def think_with_tools_stream(
        self,
        messages: list,
        tools: list = None,
        context: Optional[AgentContext] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        """Stream a response with spend approval context injected."""
        domain_context = {}
        if context and context.extra:
            domain_context = context.extra.get("spend_advisor_context", {})

        system_prompt = self.get_system_prompt(
            tools=tools, domain_context=domain_context
        )

        # Build message list: system + conversation history + current message
        full_messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (skip system messages from history)
        for msg in messages:
            if msg.get("role") != "system":
                full_messages.append(msg)

        async for chunk in self.think_stream(
            messages=full_messages,
            tools=tools,
            agent_context=context,
            **kwargs,
        ):
            yield chunk

    async def execute(self, context: AgentContext, **kwargs) -> AgentResult:
        """
        Execute a one-shot spend analysis.

        Expected kwargs:
            approval_data: dict — spend approval details
            budget_context: dict — campaign budget summary
            campaign_data: Optional[dict] — campaign info
            decoded_invoice: Optional[dict] — decoded Lightning invoice
        """
        response = await self._analyze_spend_full(
            approval_data=kwargs.get("approval_data", {}),
            budget_context=kwargs.get("budget_context", {}),
            campaign_data=kwargs.get("campaign_data"),
            decoded_invoice=kwargs.get("decoded_invoice"),
        )
        content = response.content if hasattr(response, 'content') else str(response)
        return AgentResult(
            success=True,
            message=content,
            tokens_used=response.total_tokens,
            cost_usd=response.cost_usd,
            model_used=response.model,
            latency_ms=response.latency_ms,
        )

    async def analyze_spend(
        self,
        approval_data: dict,
        budget_context: dict,
        campaign_data: Optional[dict] = None,
        decoded_invoice: Optional[dict] = None,
    ) -> str:
        """
        One-shot analysis of a spend approval request (non-streaming).

        Returns the advisor's analysis text.
        """
        response = await self._analyze_spend_full(
            approval_data=approval_data,
            budget_context=budget_context,
            campaign_data=campaign_data,
            decoded_invoice=decoded_invoice,
        )
        return response.content if hasattr(response, 'content') else str(response)

    async def _analyze_spend_full(
        self,
        approval_data: dict,
        budget_context: dict,
        campaign_data: Optional[dict] = None,
        decoded_invoice: Optional[dict] = None,
    ):
        """
        Internal: run spend analysis and return full LLMResponse.
        """
        domain_context = {
            "approval": approval_data,
            "budget_context": budget_context,
            "campaign": campaign_data,
            "decoded_invoice": decoded_invoice,
        }

        system_prompt = self.get_system_prompt(domain_context=domain_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    "Please analyze this spend request. Provide your assessment "
                    "covering: summary, budget impact, concerns (if any), and "
                    "your recommendation."
                ),
            },
        ]

        return await self.think(messages=messages)


# Singleton
spend_advisor_agent = SpendAdvisorAgent()
