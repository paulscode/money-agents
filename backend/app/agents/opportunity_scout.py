"""Opportunity Scout Agent - discovers money-making opportunities with adaptive learning."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.prompt_injection_guard import (
    get_security_preamble,
    sanitize_external_content,
    wrap_external_content,
)
from app.models import (
    Tool, 
    Proposal,
    ProposalStatus,
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    TimeSensitivity,
    EffortLevel,
    DiscoveryStrategy,
    StrategyStatus,
    StrategyOutcome,
    AgentInsight,
    InsightType,
    MemorySummary,
    SummaryType,
    UserScoutSettings,
    ScoringRubric,
    UserIdea,
    IdeaStatus,
    StrategicContextEntry,
    StrategicContextCategory,
)
from app.services.llm_service import LLMMessage, StreamChunk, llm_service
from app.services.ideas_service import IdeasService
from app.services.strategic_context_service import StrategicContextService
from app.services.opportunity_service import opportunity_service

logger = logging.getLogger(__name__)


class OpportunityScoutAgent(BaseAgent):
    """
    Agent that discovers money-making opportunities through adaptive strategies.
    
    Key capabilities:
    - Self-plans discovery strategies using quality LLM
    - Executes web searches and analyzes results
    - Scores and ranks opportunities
    - Learns from user feedback to improve over time
    - Maintains compressed long-term memory
    """
    
    name = "opportunity_scout"
    description = "Discovers and evaluates money-making opportunities"
    default_temperature = 0.7
    default_max_tokens = 6000  # High limit - we only pay for tokens actually used
    
    # Use quality tier for planning, reasoning for analysis, fast for filtering
    model_tier = "quality"  # Default for strategic planning
    
    # No tool calls via <tool_call> tags — searches Serper directly via HTTP
    TOOL_ALLOWLIST: list[str] | None = []
    
    def _effective_tier(self, tier: str) -> str:
        """Resolve effective LLM tier, upgrading to quality when Ollama is primary.
        
        Ollama models are inherently less capable than cloud models, so we always
        use the best available local model rather than distinguishing tiers.
        """
        from app.core.config import settings
        primary = settings.llm_provider_priority_list[0] if settings.llm_provider_priority_list else ""
        if primary == "ollama":
            return "quality"
        return tier
    
    # ==========================================================================
    # Planning Phase - Agent creates its own strategy
    # ==========================================================================
    
    async def create_strategic_plan(
        self,
        context: AgentContext,
        force_new: bool = False,
        user_id: Optional[UUID] = None,
    ) -> AgentResult:
        """
        Have the agent create or update its strategic plan for discovering opportunities.
        
        Uses quality LLM to think deeply about:
        - What strategies to pursue
        - What search queries to use
        - How to evaluate results
        - What success looks like
        
        Now includes user's strategic context (distilled from their ideas).
        """
        db = context.db
        
        # Get existing context
        tools = await self.get_available_tools(db)
        active_strategies = await self._get_active_strategies(db)
        recent_insights = await self._get_recent_insights(db, limit=10)
        memory_summary = await self._get_latest_memory_summary(db)
        strategy_stats = await self._get_strategy_statistics(db)
        
        # Get user's strategic context (ideas distilled into insights)
        strategic_context = await self._get_strategic_context_for_planning(db, user_id)
        
        # Build planning prompt
        prompt = self._build_planning_prompt(
            tools=tools,
            active_strategies=active_strategies,
            recent_insights=recent_insights,
            memory_summary=memory_summary,
            strategy_stats=strategy_stats,
            force_new=force_new,
            strategic_context=strategic_context,
        )
        
        messages = [
            LLMMessage(role="system", content=self._get_planning_system_prompt()),
            LLMMessage(role="user", content=prompt),
        ]
        
        # Use quality tier for strategic planning
        response = await self.think(messages, model="quality", max_tokens=6000)
        
        # Parse the plan and create/update strategies
        strategies_created = await self._parse_and_save_strategies(db, response.content)
        
        return AgentResult(
            success=True,
            message=f"Strategic plan created with {len(strategies_created)} strategies",
            data={
                "plan": response.content,
                "strategies_created": [s.name for s in strategies_created],
                "strategic_context_used": bool(strategic_context),
            },
            tokens_used=response.total_tokens,
            model_used=response.model,
            latency_ms=response.latency_ms,
        )
    
    def _get_planning_system_prompt(self) -> str:
        """System prompt for strategic planning."""
        preamble = get_security_preamble("none")
        return preamble + """You are the Opportunity Scout Agent's strategic planner. Your role is to create 
effective strategies for discovering money-making opportunities.

## Your Planning Principles

1. **Diversify approaches**: Don't put all eggs in one basket. Use multiple search angles.
2. **Learn from history**: If a strategy hasn't worked, deprioritize it. If one excels, expand it.
3. **Match capabilities**: Only pursue opportunities we can actually execute with available tools.
4. **Time awareness**: Consider market timing, trends, and seasonality.
5. **Risk balance**: Mix low-risk quick wins with higher-risk bigger opportunities.

## Strategy Output Format

Return your plan as JSON with this structure:

```json
{
  "strategic_summary": "Brief overview of the plan",
  "strategies": [
    {
      "name": "Strategy name",
      "description": "What this strategy does and why",
      "strategy_type": "search|monitor|analyze|combine",
      "search_queries": ["query 1", "query 2"],
      "source_types": ["web_search", "news"],
      "filters": {
        "min_revenue_potential": 1000,
        "max_competition": "medium"
      },
      "schedule": "daily|weekly|on_demand",
      "expected_success_rate": 0.1,
      "rationale": "Why this strategy should work"
    }
  ],
  "experiments": [
    {
      "name": "Experimental approach name",
      "hypothesis": "What we're testing",
      "success_criteria": "How we'll know it worked"
    }
  ],
  "focus_areas": ["area1", "area2"],
  "avoid_areas": ["area1", "area2"]
}
```

Be specific with search queries - they should be actual queries that would find opportunities.
Include 3-5 main strategies and 1-2 experiments."""

    def _build_planning_prompt(
        self,
        tools: List[Tool],
        active_strategies: List[DiscoveryStrategy],
        recent_insights: List[AgentInsight],
        memory_summary: Optional[MemorySummary],
        strategy_stats: Dict[str, Any],
        force_new: bool,
        strategic_context: str = "",
    ) -> str:
        """Build the user prompt for planning."""
        
        # Format tools
        tool_names = [f"- {t.name}: {t.description[:100]}" for t in tools[:15]]
        tools_text = "\n".join(tool_names) if tool_names else "No tools currently available"
        
        # Format active strategies
        if active_strategies:
            strategies_text = "\n".join([
                f"- {s.name} (executed {s.times_executed}x, "
                f"found {s.opportunities_found}, approved {s.opportunities_approved}, "
                f"effectiveness: {s.effectiveness_score or 'N/A'})"
                for s in active_strategies
            ])
        else:
            strategies_text = "No active strategies yet - this is a fresh start."
        
        # Format insights
        if recent_insights:
            insights_text = "\n".join([
                f"- [{i.insight_type.value}] {i.title} (confidence: {i.confidence:.0%})"
                for i in recent_insights
            ])
        else:
            insights_text = "No insights recorded yet."
        
        # Format memory summary
        if memory_summary:
            memory_text = f"""
Last summary ({memory_summary.summary_type.value}, {memory_summary.period_start.date()} to {memory_summary.period_end.date()}):
{memory_summary.executive_summary[:500]}

Focus areas: {', '.join(memory_summary.focus_areas or [])}
Avoid areas: {', '.join(memory_summary.avoid_areas or [])}
"""
        else:
            memory_text = "No historical memory yet - starting fresh."
        
        # Format stats
        stats_text = f"""
Total strategies: {strategy_stats.get('total', 0)}
Active strategies: {strategy_stats.get('active', 0)}
Total opportunities found: {strategy_stats.get('opportunities_found', 0)}
Approval rate: {strategy_stats.get('approval_rate', 0):.0%}
"""
        
        # Format strategic context section
        if strategic_context:
            strategic_context_text = f"""
## User's Strategic Context (from their ideas)
{strategic_context}

Use this context to personalize strategies to the user's interests, capabilities, and goals.
"""
        else:
            strategic_context_text = ""
        
        context = "NEW PLAN REQUESTED - Create fresh strategies." if force_new else "Review and update existing strategies if needed."
        
        return f"""## Current Context
{context}

## Available Tools
{tools_text}

## Current Strategies
{strategies_text}

## Recent Insights
{insights_text}

## Historical Memory
{memory_text}

## Performance Statistics
{stats_text}
{strategic_context_text}
## Today's Date
{utc_now().strftime('%Y-%m-%d')}

Based on this context, create a strategic plan for discovering money-making opportunities.
Consider what's worked, what hasn't, and what new approaches might yield results.
{f'Pay special attention to the user strategic context above - prioritize opportunities that align with their stated interests and capabilities.' if strategic_context else ''}"""

    def _extract_json_from_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Extract JSON object from LLM response, handling markdown code blocks."""
        import re
        
        original_content = content  # Save for debugging
        
        try:
            # Try to find JSON in code blocks first
            if "```json" in content:
                json_start = content.find("```json") + 7
                json_end = content.find("```", json_start)
                if json_end > json_start:
                    content = content[json_start:json_end].strip()
            elif "```" in content:
                # Generic code block
                json_start = content.find("```") + 3
                json_end = content.find("```", json_start)
                if json_end > json_start:
                    content = content[json_start:json_end].strip()
            
            # Now find the JSON object
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                logger.warning("No JSON found in response")
                logger.debug(f"Raw content: {original_content[:500]}")
                return None
            
            json_str = content[json_start:json_end]
            
            # Try to parse, if it fails try to fix common issues
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                # Try fixing common issues
                # 1. Remove control characters
                json_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', json_str)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
                
                # 2. Fix trailing commas before ] or }
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
                
                # 3. Fix "or" text in value positions (e.g. "tool" or "opportunity")
                json_str = re.sub(r'"([^"]+)"\s+or\s+"([^"]+)"', r'"\1"', json_str)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    pass
                
                # 4. Try escaping unescaped newlines in strings
                # This is a last resort
                json_str = re.sub(r'(?<!\\)\n', r'\\n', json_str)
                return json.loads(json_str)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Problematic JSON (first 500 chars): {json_str[:500] if 'json_str' in dir() else content[:500]}")
            return None
        except Exception as e:
            logger.error(f"Error extracting JSON: {e}")
            return None

    async def _parse_and_save_strategies(
        self,
        db: AsyncSession,
        plan_content: str,
    ) -> List[DiscoveryStrategy]:
        """Parse the LLM's plan and save strategies to database."""
        strategies_created = []
        
        try:
            # Extract JSON from response - handle markdown code blocks
            content = plan_content
            
            # Try to find JSON in code blocks first
            if "```json" in content:
                json_start = content.find("```json") + 7
                json_end = content.find("```", json_start)
                if json_end > json_start:
                    content = content[json_start:json_end].strip()
            elif "```" in content:
                # Generic code block
                json_start = content.find("```") + 3
                json_end = content.find("```", json_start)
                if json_end > json_start:
                    content = content[json_start:json_end].strip()
            
            # Now find the JSON object
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                logger.warning("No JSON found in planning response")
                return strategies_created
            
            json_str = content[json_start:json_end]
            
            # Try to parse, if it fails try to fix common issues
            try:
                plan = json.loads(json_str)
            except json.JSONDecodeError:
                # Try fixing common issues: control characters in strings
                import re
                # Remove control characters but preserve newlines in a safe way
                json_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', json_str)
                plan = json.loads(json_str)
            
            for strategy_data in plan.get("strategies", []):
                strategy = DiscoveryStrategy(
                    name=strategy_data.get("name", "Unnamed Strategy"),
                    description=strategy_data.get("description", ""),
                    strategy_type=strategy_data.get("strategy_type", "search"),
                    search_queries=strategy_data.get("search_queries", []),
                    source_types=strategy_data.get("source_types", ["web_search"]),
                    filters=strategy_data.get("filters", {}),
                    schedule=strategy_data.get("schedule", "on_demand"),
                    status=StrategyStatus.ACTIVE,
                    created_by="agent",
                    agent_notes=strategy_data.get("rationale", ""),
                )
                db.add(strategy)
                strategies_created.append(strategy)
            
            await db.commit()
            
            # Refresh to get IDs
            for strategy in strategies_created:
                await db.refresh(strategy)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse planning JSON: {e}")
        except Exception as e:
            logger.error(f"Error saving strategies: {e}")
            await db.rollback()
        
        return strategies_created

    # ==========================================================================
    # Discovery Phase - Execute strategies to find opportunities
    # ==========================================================================
    
    async def run_discovery(
        self,
        context: AgentContext,
        strategy_id: Optional[UUID] = None,
        max_opportunities: int = 10,
    ) -> AgentResult:
        """
        Execute discovery strategies to find new opportunities.
        
        Args:
            context: Agent context with db session
            strategy_id: Specific strategy to run, or None for all active
            max_opportunities: Maximum opportunities to create this run
        """
        db = context.db
        opportunities_created = []
        outcomes_recorded = []
        bootstrap_tokens = 0  # Track tokens used for bootstrap if needed
        
        # Get strategies to run
        if strategy_id:
            strategy = await db.get(DiscoveryStrategy, strategy_id)
            strategies = [strategy] if strategy else []
        else:
            strategies = await self._get_active_strategies(db)
        
        # Auto-bootstrap: Create initial strategies if none exist
        if not strategies:
            logger.info("No strategies found - bootstrapping with initial strategic plan")
            plan_result = await self.create_strategic_plan(context)
            bootstrap_tokens = plan_result.tokens_used or 0
            if plan_result.success:
                strategies = await self._get_active_strategies(db)
                logger.info(f"Created {len(strategies)} initial strategies")
            else:
                return AgentResult(
                    success=False,
                    message="Failed to create initial strategic plan.",
                    data={"opportunities_created": 0},
                    tokens_used=bootstrap_tokens,
                    model_used=plan_result.model_used,
                )
        
        # Final check - if still no strategies after bootstrap, we can't proceed
        if not strategies:
            return AgentResult(
                success=False,
                message="Strategic plan created but no strategies were saved.",
                data={"opportunities_created": 0},
                tokens_used=bootstrap_tokens,
            )
        
        # Get available tools for context
        tools = await self.get_available_tools(db)
        tool_slugs = {t.slug for t in tools}
        
        total_tokens = bootstrap_tokens  # Include bootstrap tokens in total
        
        for strategy in strategies:
            if len(opportunities_created) >= max_opportunities:
                break
            
            try:
                # Execute the strategy
                result = await self._execute_strategy(
                    db=db,
                    strategy=strategy,
                    tools=tools,
                    tool_slugs=tool_slugs,
                    max_results=max_opportunities - len(opportunities_created),
                )
                
                opportunities_created.extend(result.get("opportunities", []))
                total_tokens += result.get("tokens_used", 0)
                
                # Record outcome
                outcome = StrategyOutcome(
                    strategy_id=strategy.id,
                    execution_context={
                        "date": utc_now().isoformat(),
                        "max_results": max_opportunities,
                    },
                    queries_run=result.get("queries_run", []),
                    results_count=result.get("raw_results_count", 0),
                    opportunities_discovered=len(result.get("opportunities", [])),
                    quality_assessment=result.get("quality_assessment"),
                )
                db.add(outcome)
                outcomes_recorded.append(outcome)
                
                # Update strategy stats
                strategy.times_executed += 1
                strategy.last_executed = utc_now()
                strategy.opportunities_found += len(result.get("opportunities", []))
                
            except Exception as e:
                logger.error(f"Strategy {strategy.name} failed: {e}")
                continue
        
        # Run Bitcoin acquisition search (only when LND is enabled)
        from app.core.config import settings
        if settings.use_lnd:
            try:
                logger.info("Running Bitcoin acquisition search phase...")
                btc_result = await self._run_bitcoin_acquisition_search(
                    db=db,
                    tools=tools,
                    tool_slugs=tool_slugs,
                )
                btc_opps = btc_result.get("opportunities", [])
                opportunities_created.extend(btc_opps)
                total_tokens += btc_result.get("tokens_used", 0)
                if btc_opps:
                    logger.info(f"Bitcoin acquisition search found {len(btc_opps)} opportunities")
            except Exception as e:
                logger.error(f"Bitcoin acquisition search failed: {e}")

        # Run creative exploration (the "wacky" phase)
        # This runs after strategies to avoid duplicating their findings
        creative_slots = max(2, max_opportunities - len(opportunities_created))
        if creative_slots > 0:
            try:
                logger.info("Running creative exploration phase...")
                creative_result = await self._run_creative_exploration(
                    db=db,
                    tools=tools,
                    tool_slugs=tool_slugs,
                )
                creative_opps = creative_result.get("opportunities", [])
                opportunities_created.extend(creative_opps)
                total_tokens += creative_result.get("tokens_used", 0)
                
                if creative_opps:
                    logger.info(f"Creative exploration found {len(creative_opps)} opportunities")
            except Exception as e:
                logger.error(f"Creative exploration failed: {e}")
        
        # Run Bitcoin creative exploration (only when LND is enabled)
        if settings.use_lnd:
            try:
                logger.info("Running Bitcoin creative exploration phase...")
                btc_creative_result = await self._run_bitcoin_creative_exploration(
                    db=db,
                    tools=tools,
                    tool_slugs=tool_slugs,
                )
                btc_creative_opps = btc_creative_result.get("opportunities", [])
                opportunities_created.extend(btc_creative_opps)
                total_tokens += btc_creative_result.get("tokens_used", 0)
                if btc_creative_opps:
                    logger.info(f"Bitcoin creative exploration found {len(btc_creative_opps)} opportunities")
            except Exception as e:
                logger.error(f"Bitcoin creative exploration failed: {e}")

        await db.commit()
        
        return AgentResult(
            success=True,
            message=f"Discovery complete: {len(opportunities_created)} opportunities found",
            data={
                "opportunities_created": len(opportunities_created),
                "opportunities_found": len(opportunities_created),  # Alias for agent_tasks.py
                "strategies_run": len(strategies),
                "opportunity_ids": [str(o.id) for o in opportunities_created],
                "bootstrapped": bootstrap_tokens > 0,
            },
            tokens_used=total_tokens,
        )

    async def _execute_strategy(
        self,
        db: AsyncSession,
        strategy: DiscoveryStrategy,
        tools: List[Tool],
        tool_slugs: set,
        max_results: int,
    ) -> Dict[str, Any]:
        """Execute a single discovery strategy."""
        
        results = {
            "opportunities": [],
            "queries_run": [],
            "raw_results_count": 0,
            "tokens_used": 0,
            "quality_assessment": None,
        }
        
        # For now, focus on web search strategies
        if "web_search" not in strategy.source_types:
            return results
        
        # Execute each search query
        for query in strategy.search_queries[:5]:  # Limit queries per run
            results["queries_run"].append(query)
            
            # Use Serper tool for web search
            search_results = await self._execute_web_search(query)
            if not search_results:
                continue
            
            results["raw_results_count"] += len(search_results)
            
            # Analyze results with fast LLM for initial filtering
            filtered = await self._filter_search_results(
                query=query,
                results=search_results,
                strategy=strategy,
            )
            results["tokens_used"] += filtered.get("tokens_used", 0)
            
            # Create opportunities from promising results
            for signal in filtered.get("promising", [])[:max_results]:
                opportunity = await self._create_opportunity_from_signal(
                    db=db,
                    signal=signal,
                    strategy=strategy,
                    query=query,
                    tool_slugs=tool_slugs,
                )
                if opportunity:
                    results["opportunities"].append(opportunity)
        
        return results

    async def _execute_web_search(self, query: str) -> List[Dict[str, Any]]:
        """Execute a web search using Serper API (or Serper Clone)."""
        from app.core.config import settings
        import httpx
        
        if not settings.serper_api_key:
            logger.warning("Serper API key not configured")
            return []
        
        try:
            # Get base URL and SSL settings (Serper Clone uses self-signed certs)
            base_url = settings.serper_base_url
            verify_ssl = settings.serper_verify_ssl
            
            async with httpx.AsyncClient(verify=verify_ssl, timeout=30.0) as client:
                response = await client.post(
                    f"{base_url}/search",
                    headers={
                        "X-API-KEY": settings.serper_api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": query, "num": 10},
                )
                response.raise_for_status()
                data = response.json()
                
                # Combine organic results and news if available
                results = data.get("organic", [])
                if "news" in data:
                    results.extend(data["news"])
                
                return results
                
        except Exception as e:
            logger.error(f"Web search failed for '{query}': {e}")
            return []

    async def _filter_search_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        strategy: DiscoveryStrategy,
    ) -> Dict[str, Any]:
        """Use fast LLM to filter search results for promising signals."""
        
        # Format results for LLM — sanitize external search content
        raw_results_text = "\n\n".join([
            f"[{i+1}] {r.get('title', 'No title')}\n"
            f"URL: {r.get('link', 'No URL')}\n"
            f"Snippet: {r.get('snippet', 'No snippet')}"
            for i, r in enumerate(results[:10])
        ])
        sanitized_results, _det = sanitize_external_content(
            raw_results_text, source="web_search",
        )
        results_text = wrap_external_content(sanitized_results, source="web_search")
        
        filter_prompt = f"""Analyze these search results for MONEY-MAKING opportunity signals.

Your task is to identify results that describe specific, actionable ways to earn revenue or profit.
A "money-making opportunity" means a concrete way to generate income — NOT general industry news,
trend reports, career advice, educational content, or interesting-but-unprofitable information.

Search Query: {query}
Strategy Focus: {strategy.description}

Results:
{results_text}

## ACCEPT criteria (must meet ALL):
- Describes a specific way to earn money, not just an interesting topic
- Has a clear revenue mechanism (sell, charge, earn, arbitrage, monetize)
- Is actionable by a small team or individual with software/AI tools
- Is current and not obviously outdated

## REJECT criteria (reject if ANY apply):
- General news or trend articles without a specific monetization angle
- Job listings, hiring announcements, or employment opportunities
- Courses, certifications, or "learn to earn" content
- Vague "the market is growing" without a concrete way to capture value
- Requires large capital investment (>$10k) or physical infrastructure
- Press releases or company announcements without an actionable angle
- Lists of tips or advice without a specific implementable opportunity
- Academic research or white papers
- Product reviews or comparisons (unless revealing an arbitrage/gap)

For each result that passes the ACCEPT criteria, extract:
1. The money-making signal (specifically HOW money is made)
2. Opportunity type (arbitrage, content, service, product, automation, affiliate, investment, other)
3. Estimated revenue potential (low: <$500/mo, medium: $500-5k/mo, high: >$5k/mo)
4. Time sensitivity (immediate/short/medium/evergreen)

Return JSON:
```json
{{
  "promising": [
    {{
      "result_index": 1,
      "signal": "Clear description of how money is made",
      "opportunity_type": "content",
      "revenue_potential": "medium",
      "time_sensitivity": "evergreen",
      "title": "Generated title for this opportunity",
      "source_url": "the URL",
      "raw_snippet": "the snippet"
    }}
  ],
  "rejected_count": 5,
  "quality_notes": "Brief assessment of result quality"
}}
```

Most search results are NOT money-making opportunities. It is normal and expected to reject
the majority of results. When in doubt, reject."""

        messages = [
            LLMMessage(role="user", content=filter_prompt),
        ]
        
        # Use fast tier for filtering (upgraded to quality for Ollama)
        response = await self.think(messages, model=self._effective_tier("fast"), max_tokens=6000)
        
        try:
            # Extract JSON from response (may be wrapped in markdown code blocks)
            content = response.content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
                content = json_match
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0].strip()
                content = json_match
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end > 0:
                parsed = json.loads(content[json_start:json_end])
                parsed["tokens_used"] = response.total_tokens
                return parsed
        except json.JSONDecodeError:
            pass
        
        return {"promising": [], "rejected_count": len(results), "tokens_used": response.total_tokens}

    # ==========================================================================
    # Creative Exploration - Think outside the box
    # ==========================================================================

    def _get_creative_exploration_prompt(self) -> str:
        """Get system prompt for creative 'what if' exploration."""
        return """You are the Opportunity Scout Agent in creative exploration mode.

## Your Mission

Take a step back from conventional opportunity hunting and engage in divergent thinking.
Your goal is to imagine money-making opportunities that aren't obvious, or creative angles
on existing trends that others might miss.

## Creative Thinking Angles

- **Contrarian**: What's everyone ignoring that actually has potential?
- **Intersection**: Where do two unrelated trends/niches collide in interesting ways?
- **Underserved**: Who's being poorly served by current solutions?
- **Emerging Behavior**: What new behaviors are people developing that create opportunities?
- **Arbitrage**: Where are there gaps between perceived value and actual effort?
- **Meta-Opportunity**: What opportunities exist because everyone else is chasing the obvious ones?
- **Weird Niche**: What oddly specific thing has a passionate community willing to pay?

## Examples of Creative Thinking

- "Everyone's making faceless YouTube channels about finance... but what about faceless channels for niche hobbies like competitive yo-yo?"
- "AI art is saturated, but what about AI-generated custom crossword puzzles for corporate team building?"
- "People sell Notion templates... what about templates for obscure software only certain professionals use?"

## Output Format

Generate exactly 2 creative opportunity angles, then propose a search query for each:

```json
{
  "creative_angles": [
    {
      "angle": "A creative opportunity idea that's a bit unusual or unexpected",
      "reasoning": "Why this might actually work despite being unconventional",
      "target_audience": "Who would pay for this",
      "search_query": "A search query to explore if anything like this exists or to find related signals"
    },
    {
      "angle": "Another creative opportunity idea",
      "reasoning": "Why this might work",
      "target_audience": "Who would pay",
      "search_query": "Search query to explore"
    }
  ]
}
```

## Guidelines

- Be genuinely creative, not just "another AI tool for X"
- Think about combinations, contrarian angles, and underserved niches
- The weirder/more specific, often the better (less competition)
- Consider low-effort, high-margin opportunities
- It's OK if the idea seems unconventional - that's the point!"""

    def _build_creative_exploration_prompt(
        self,
        recent_opportunities: List[str],
        active_strategies: List[str],
    ) -> str:
        """Build user prompt for creative exploration."""
        opps_list = "\n".join(f"- {o}" for o in recent_opportunities[:10]) if recent_opportunities else "None yet"
        strategies_list = "\n".join(f"- {s}" for s in active_strategies) if active_strategies else "General discovery"
        
        return f"""## Current Context

**Opportunities we've already found (avoid similar):**
{opps_list}

**Our current search strategies:**
{strategies_list}

## Today's Date
{utc_now().strftime('%Y-%m-%d')}

---

Now, engage your creative thinking mode!

Think about what everyone ELSE is missing. What weird, specific, or contrarian opportunities
might be hiding in plain sight? What would make someone say "huh, I never thought of that"?

Generate 2 creative opportunity angles with search queries to explore them."""

    async def _run_creative_exploration(
        self,
        db: AsyncSession,
        tools: List[Tool],
        tool_slugs: set,
    ) -> Dict[str, Any]:
        """Run creative exploration to find unconventional opportunities."""
        results = {
            "opportunities": [],
            "tokens_used": 0,
            "creative_angles_explored": 0,
        }
        
        # Get recent opportunity titles to avoid repetition
        result = await db.execute(
            select(Opportunity.title)
            .order_by(Opportunity.discovered_at.desc())
            .limit(20)
        )
        recent_opps = [row[0] for row in result.fetchall()]
        
        # Get active strategy names for context
        result = await db.execute(
            select(DiscoveryStrategy.name)
            .where(DiscoveryStrategy.status == StrategyStatus.ACTIVE)
        )
        active_strategies = [row[0] for row in result.fetchall()]
        
        system_prompt = self._get_creative_exploration_prompt()
        user_prompt = self._build_creative_exploration_prompt(recent_opps, active_strategies)
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        # Use higher temperature for more creative output
        response = await llm_service.generate(
            messages=messages,
            model="quality",
            temperature=0.9,  # High temperature for creativity
            max_tokens=6000,
        )
        
        results["tokens_used"] += response.total_tokens
        
        # Parse creative angles
        try:
            content = response.content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
                content = json_match
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0].strip()
                content = json_match
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end > 0:
                parsed = json.loads(content[json_start:json_end])
            else:
                parsed = {}
        except json.JSONDecodeError:
            parsed = {}
        
        creative_angles = parsed.get("creative_angles", [])
        if not creative_angles:
            logger.info("Creative exploration: No structured angles generated")
            return results
        
        results["creative_angles_explored"] = len(creative_angles)
        
        # Execute searches for creative angles
        for angle in creative_angles[:2]:
            query = angle.get("search_query", "")
            if not query:
                continue
            
            logger.info(f"Creative exploration search: {query}")
            
            try:
                search_results = await self._execute_web_search(query)
                if not search_results:
                    continue
                
                # Use LLM to analyze results with the creative context
                analysis = await self._analyze_creative_results(
                    query=query,
                    results=search_results,
                    creative_angle=angle,
                )
                results["tokens_used"] += analysis.get("tokens_used", 0)
                
                # Create opportunities from promising signals
                for signal in analysis.get("promising", [])[:3]:
                    # Add the creative context to the signal
                    signal["creative_angle"] = angle.get("angle", "")
                    opportunity = await self._create_opportunity_from_creative_signal(
                        db=db,
                        signal=signal,
                        query=query,
                        tool_slugs=tool_slugs,
                    )
                    if opportunity:
                        results["opportunities"].append(opportunity)
                        
            except Exception as e:
                logger.warning(f"Creative search failed for '{query}': {e}")
                continue
        
        return results

    async def _analyze_creative_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        creative_angle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze search results through the lens of a creative angle."""
        
        raw_results_text = "\n\n".join([
            f"[{i+1}] {r.get('title', 'No title')}\n"
            f"URL: {r.get('link', 'No URL')}\n"
            f"Snippet: {r.get('snippet', 'No snippet')}"
            for i, r in enumerate(results[:10])
        ])
        sanitized_results, _det = sanitize_external_content(
            raw_results_text, source="web_search",
        )
        results_text = wrap_external_content(sanitized_results, source="web_search")
        
        prompt = f"""Analyze these search results through the lens of our creative angle,
looking specifically for MONEY-MAKING opportunities.

A "money-making opportunity" means a concrete way to generate income — NOT general industry news,
trend reports, or interesting-but-unprofitable information.

## Creative Angle We're Exploring
**Idea:** {creative_angle.get('angle', 'Unknown')}
**Reasoning:** {creative_angle.get('reasoning', 'Unknown')}
**Target Audience:** {creative_angle.get('target_audience', 'Unknown')}

## Search Query
{query}

## Results
{results_text}

## Your Task

Look for signals that this creative angle represents a viable way to EARN MONEY:
- Is there evidence people pay for something in this space?
- Are there examples of revenue being generated?
- What specific revenue mechanism could we use (sell, charge, license, monetize)?
- What's the competition like?

## REJECT if:
- The result is just news/trends without a revenue mechanism
- It requires large capital, physical infrastructure, or specialized credentials
- It's a job listing, course, or general advice article
- There's no clear path from "interesting idea" to "generating income"

Return JSON:
```json
{{
  "promising": [
    {{
      "signal": "Specifically HOW money can be made from this angle",
      "opportunity_type": "content/product/service/affiliate/arbitrage/other",
      "revenue_potential": "low/medium/high",
      "time_sensitivity": "immediate/short/medium/evergreen",
      "title": "A catchy title for this opportunity",
      "source_url": "the URL",
      "raw_snippet": "the snippet"
    }}
  ],
  "validation_notes": "Overall assessment of whether the creative angle can generate revenue",
  "competition_level": "none/low/medium/high"
}}
```

Most results will NOT represent viable money-making opportunities. When in doubt, reject."""

        messages = [
            LLMMessage(role="user", content=prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model=self._effective_tier("fast"),
            max_tokens=6000,
        )
        
        try:
            content = response.content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
                content = json_match
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0].strip()
                content = json_match
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end > 0:
                parsed = json.loads(content[json_start:json_end])
                parsed["tokens_used"] = response.total_tokens
                return parsed
        except json.JSONDecodeError:
            pass
        
        return {"promising": [], "tokens_used": response.total_tokens}

    async def _create_opportunity_from_creative_signal(
        self,
        db: AsyncSession,
        signal: Dict[str, Any],
        query: str,
        tool_slugs: set,
    ) -> Optional[Opportunity]:
        """Create an Opportunity record from a creative exploration signal."""
        
        # Map string values to enums
        type_map = {
            "arbitrage": OpportunityType.ARBITRAGE,
            "content": OpportunityType.CONTENT,
            "service": OpportunityType.SERVICE,
            "product": OpportunityType.PRODUCT,
            "automation": OpportunityType.AUTOMATION,
            "affiliate": OpportunityType.AFFILIATE,
            "investment": OpportunityType.INVESTMENT,
        }
        
        sensitivity_map = {
            "immediate": TimeSensitivity.IMMEDIATE,
            "short": TimeSensitivity.SHORT,
            "medium": TimeSensitivity.MEDIUM,
            "evergreen": TimeSensitivity.EVERGREEN,
        }
        
        # Include creative angle in the summary
        creative_context = signal.get("creative_angle", "")
        summary = signal.get("signal", "")
        if creative_context:
            summary = f"[Creative angle: {creative_context}]\n\n{summary}"
        
        title = signal.get("title", "Untitled Creative Opportunity")
        source_urls = [signal.get("source_url")] if signal.get("source_url") else []

        # Dedup check — skip if a similar opportunity already exists
        existing = await opportunity_service.find_duplicate_opportunity(
            db, title=title, source_urls=source_urls,
        )
        if existing:
            logger.info(
                "Skipping duplicate creative opportunity: '%s' matches existing '%s' (%s)",
                title, existing.title, existing.id,
            )
            return None

        opportunity = Opportunity(
            title=title,
            summary=summary,
            opportunity_type=type_map.get(signal.get("opportunity_type", ""), OpportunityType.OTHER),
            status=OpportunityStatus.DISCOVERED,
            discovery_strategy_id=None,  # No specific strategy - creative exploration
            source_type="creative_exploration",
            source_query=query,
            source_urls=source_urls,
            raw_signal=signal.get("raw_snippet", ""),
            time_sensitivity=sensitivity_map.get(signal.get("time_sensitivity", ""), None),
            initial_assessment=f"Found via creative exploration: {signal.get('signal', '')}",
        )
        
        db.add(opportunity)
        await db.flush()
        
        logger.info(f"Created creative opportunity: {opportunity.title}")
        return opportunity

    # ==========================================================================
    # Bitcoin Acquisition Search - Find ways to acquire BTC
    # ==========================================================================

    _BITCOIN_SEARCH_QUERIES = [
        "earn bitcoin online freelancing bounties 2025",
        "bitcoin cashback rewards programs earn sats",
        "bitcoin mining profitability small scale home 2025",
        "get paid in bitcoin remote jobs",
        "bitcoin affiliate programs highest paying sats",
        "Lightning Network earn sats routing fees 2025",
        "bitcoin micro-tasks earn satoshis",
        "selling digital products for bitcoin sats",
    ]

    def _get_bitcoin_filter_prompt(self) -> str:
        """System prompt for filtering Bitcoin acquisition search results."""
        return """You are the Opportunity Scout Agent focused on **Bitcoin acquisition**.

## Your Mission

Analyze search results to find CONCRETE ways to acquire Bitcoin (BTC/sats).
We already run a Lightning Network node, so we have Bitcoin infrastructure in place.

## ACCEPT signals that describe:
- Ways to earn Bitcoin directly (freelancing, bounties, tasks paid in BTC)
- Bitcoin cashback or rewards programs with favorable rates
- Lightning Network earning opportunities (routing, liquidity provision, paid services)
- Selling products/services for Bitcoin with a clear mechanism
- Bitcoin affiliate or referral programs that pay in sats
- Small-scale mining or hash-rate rental that's currently profitable
- Arbitrage between Bitcoin and fiat or between exchanges

## REJECT signals that are:
- General Bitcoin price speculation or investment advice
- News articles about Bitcoin without an actionable earning mechanism
- Scams, Ponzi schemes, or "guaranteed returns" offers
- Opportunities requiring large capital (>$1000 upfront)
- Outdated information (pre-2024 guides that may no longer apply)
- Ads or sponsored content disguised as articles
- Opportunities only available in specific countries with no remote option

## Output Format
```json
{
  "promising": [
    {
      "signal": "How specifically you can acquire BTC through this opportunity",
      "opportunity_type": "service/product/affiliate/arbitrage/automation/other",
      "revenue_potential": "low/medium/high",
      "time_sensitivity": "immediate/short/medium/evergreen",
      "title": "Concise descriptive title - Bitcoin focused",
      "source_url": "the URL",
      "raw_snippet": "the relevant snippet",
      "btc_acquisition_method": "earn/cashback/mining/routing/selling/affiliate/arbitrage"
    }
  ]
}
```

Be selective. Most results will NOT be actionable Bitcoin acquisition opportunities."""

    async def _run_bitcoin_acquisition_search(
        self,
        db: AsyncSession,
        tools: List[Tool],
        tool_slugs: set,
    ) -> Dict[str, Any]:
        """Search for concrete ways to acquire Bitcoin."""
        import random

        results: Dict[str, Any] = {
            "opportunities": [],
            "tokens_used": 0,
            "queries_run": [],
        }

        # Pick 3 random queries each run for variety
        queries = random.sample(
            self._BITCOIN_SEARCH_QUERIES,
            min(3, len(self._BITCOIN_SEARCH_QUERIES)),
        )

        # Get recent BTC opportunity titles to avoid repetition
        db_result = await db.execute(
            select(Opportunity.title)
            .where(Opportunity.source_type == "bitcoin_search")
            .order_by(Opportunity.discovered_at.desc())
            .limit(20)
        )
        recent_btc_titles = [row[0] for row in db_result.fetchall()]

        system_prompt = self._get_bitcoin_filter_prompt()

        for query in queries:
            results["queries_run"].append(query)
            search_results = await self._execute_web_search(query)
            if not search_results:
                continue

            raw_results_text = "\n\n".join([
                f"[{i+1}] {r.get('title', 'No title')}\n"
                f"URL: {r.get('link', 'No URL')}\n"
                f"Snippet: {r.get('snippet', 'No snippet')}"
                for i, r in enumerate(search_results[:10])
            ])
            sanitized_results, _det = sanitize_external_content(
                raw_results_text, source="web_search",
            )
            results_text = wrap_external_content(sanitized_results, source="web_search")

            avoid_text = "\n".join(f"- {t}" for t in recent_btc_titles) if recent_btc_titles else "None yet"

            user_prompt = (
                f"## Search Query\n{query}\n\n"
                f"## Search Results\n{results_text}\n\n"
                f"## Already Found (avoid duplicates)\n{avoid_text}\n\n"
                "Analyze these results and extract actionable Bitcoin acquisition opportunities."
            )

            messages = [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]

            response = await llm_service.generate(
                messages=messages,
                model=self._effective_tier("fast"),
                max_tokens=4000,
            )
            results["tokens_used"] += response.total_tokens

            # Parse response
            try:
                content = response.content
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                json_start = content.find("{")
                json_end = content.rfind("}") + 1
                if json_start != -1 and json_end > 0:
                    parsed = json.loads(content[json_start:json_end])
                else:
                    parsed = {}
            except json.JSONDecodeError:
                parsed = {}

            for signal in parsed.get("promising", [])[:3]:
                opportunity = await self._create_opportunity_from_bitcoin_signal(
                    db=db,
                    signal=signal,
                    query=query,
                    tool_slugs=tool_slugs,
                )
                if opportunity:
                    results["opportunities"].append(opportunity)

        return results

    async def _create_opportunity_from_bitcoin_signal(
        self,
        db: AsyncSession,
        signal: Dict[str, Any],
        query: str,
        tool_slugs: set,
    ) -> Optional[Opportunity]:
        """Create an Opportunity from a Bitcoin acquisition signal."""
        type_map = {
            "arbitrage": OpportunityType.ARBITRAGE,
            "service": OpportunityType.SERVICE,
            "product": OpportunityType.PRODUCT,
            "automation": OpportunityType.AUTOMATION,
            "affiliate": OpportunityType.AFFILIATE,
            "investment": OpportunityType.INVESTMENT,
        }
        sensitivity_map = {
            "immediate": TimeSensitivity.IMMEDIATE,
            "short": TimeSensitivity.SHORT,
            "medium": TimeSensitivity.MEDIUM,
            "evergreen": TimeSensitivity.EVERGREEN,
        }

        btc_method = signal.get("btc_acquisition_method", "")
        title = signal.get("title", "Untitled Bitcoin Opportunity")
        source_urls = [signal.get("source_url")] if signal.get("source_url") else []

        # Dedup check
        existing = await opportunity_service.find_duplicate_opportunity(
            db, title=title, source_urls=source_urls,
        )
        if existing:
            logger.info(
                "Skipping duplicate BTC opportunity: '%s' matches existing '%s' (%s)",
                title, existing.title, existing.id,
            )
            return None

        summary = signal.get("signal", "")
        if btc_method:
            summary = f"[BTC acquisition: {btc_method}]\n\n{summary}"

        opportunity = Opportunity(
            title=title,
            summary=summary,
            opportunity_type=type_map.get(signal.get("opportunity_type", ""), OpportunityType.OTHER),
            status=OpportunityStatus.DISCOVERED,
            discovery_strategy_id=None,
            source_type="bitcoin_search",
            source_query=query,
            source_urls=source_urls,
            raw_signal=signal.get("raw_snippet", ""),
            time_sensitivity=sensitivity_map.get(signal.get("time_sensitivity", ""), None),
            initial_assessment=f"Bitcoin acquisition opportunity: {summary}",
        )

        db.add(opportunity)
        await db.flush()
        logger.info(f"Created Bitcoin acquisition opportunity: {opportunity.title}")
        return opportunity

    # ==========================================================================
    # Bitcoin Creative Exploration - Creative angles for BTC acquisition
    # ==========================================================================

    def _get_bitcoin_creative_prompt(self) -> str:
        """System prompt for creative Bitcoin acquisition exploration."""
        return """You are the Opportunity Scout Agent in **Bitcoin creative exploration** mode.

## Your Mission

Think creatively about unusual, unconventional, or overlooked ways to acquire Bitcoin.
We operate a Lightning Network node, so we have LN infrastructure already.

## Creative Thinking Angles for Bitcoin

- **Lightning Services**: What services could we run on our LN node to earn routing fees or payments?
- **Bitcoin-Native Products**: What digital products can ONLY be sold for Bitcoin (leveraging our LN setup)?
- **Circular Economy**: How can we create earn→spend→earn loops in the Bitcoin economy?
- **Micro-Monetization**: What extremely small services (pay-per-use via Lightning) have no competition?
- **Contrarian BTC**: What Bitcoin earning methods is everyone ignoring?
- **Infrastructure Play**: How can our LN node itself generate sats beyond simple routing?
- **Emerging Bitcoin Protocols**: Ordinals, Runes, Nostr zaps — what earning angles exist?
- **Cross-Ecosystem Arbitrage**: Where do BTC and fiat economies meet with pricing gaps?

## Examples of Creative Bitcoin Thinking

- "Run a Lightning-powered API that charges per-call in sats for a useful micro-service"
- "Provide inbound liquidity leasing to new Lightning nodes at premium rates"
- "Create a Nostr-based paid content feed where subscribers pay in zaps"
- "Offer Bitcoin paywall for AI-generated niche content (pay 100 sats to read)"

## Output Format

Generate exactly 2 creative Bitcoin acquisition angles with search queries:

```json
{
  "creative_angles": [
    {
      "angle": "A creative way to acquire Bitcoin that most people haven't considered",
      "reasoning": "Why this could actually work — be specific about the BTC flow",
      "btc_mechanism": "How exactly sats flow to us (earn/route/sell/mine/arbitrage)",
      "search_query": "Search query to explore if this is viable"
    },
    {
      "angle": "Another creative Bitcoin acquisition idea",
      "reasoning": "Why it works",
      "btc_mechanism": "How sats flow to us",
      "search_query": "Search query"
    }
  ]
}
```

## Guidelines

- Every angle MUST result in acquiring Bitcoin/sats — not fiat
- Leverage our existing Lightning Network node where possible
- Prefer low-effort, automated, or passive approaches
- The more specific and niche, the better (less competition)
- Consider what's unique about Bitcoin's properties (censorship-resistant, micropayments, global)"""

    def _build_bitcoin_creative_prompt(
        self,
        recent_btc_opportunities: List[str],
        recent_general_opportunities: List[str],
    ) -> str:
        """Build user prompt for Bitcoin creative exploration."""
        btc_list = "\n".join(f"- {o}" for o in recent_btc_opportunities[:10]) if recent_btc_opportunities else "None yet"
        general_list = "\n".join(f"- {o}" for o in recent_general_opportunities[:5]) if recent_general_opportunities else "None"

        return f"""## Context

**Bitcoin opportunities we've already found (avoid similar):**
{btc_list}

**General opportunities for reference (different angle needed):**
{general_list}

## Today's Date
{utc_now().strftime('%Y-%m-%d')}

---

Think about what unique Bitcoin acquisition angles everyone is missing.
Remember: we have a working Lightning Network node. What can we DO with it to earn sats?

Generate 2 creative Bitcoin acquisition angles with search queries."""

    async def _run_bitcoin_creative_exploration(
        self,
        db: AsyncSession,
        tools: List[Tool],
        tool_slugs: set,
    ) -> Dict[str, Any]:
        """Run creative exploration focused on Bitcoin acquisition."""
        results: Dict[str, Any] = {
            "opportunities": [],
            "tokens_used": 0,
            "creative_angles_explored": 0,
        }

        # Get recent BTC opportunity titles
        db_result = await db.execute(
            select(Opportunity.title)
            .where(Opportunity.source_type.in_(["bitcoin_search", "bitcoin_creative"]))
            .order_by(Opportunity.discovered_at.desc())
            .limit(20)
        )
        recent_btc = [row[0] for row in db_result.fetchall()]

        # Get recent general opportunity titles for context
        db_result = await db.execute(
            select(Opportunity.title)
            .where(Opportunity.source_type.notin_(["bitcoin_search", "bitcoin_creative"]))
            .order_by(Opportunity.discovered_at.desc())
            .limit(10)
        )
        recent_general = [row[0] for row in db_result.fetchall()]

        system_prompt = self._get_bitcoin_creative_prompt()
        user_prompt = self._build_bitcoin_creative_prompt(recent_btc, recent_general)

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        response = await llm_service.generate(
            messages=messages,
            model=self._effective_tier("quality"),
            temperature=0.9,
            max_tokens=6000,
        )
        results["tokens_used"] += response.total_tokens

        # Parse creative angles
        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start != -1 and json_end > 0:
                parsed = json.loads(content[json_start:json_end])
            else:
                parsed = {}
        except json.JSONDecodeError:
            parsed = {}

        creative_angles = parsed.get("creative_angles", [])
        if not creative_angles:
            logger.info("Bitcoin creative exploration: No structured angles generated")
            return results

        results["creative_angles_explored"] = len(creative_angles)

        # Execute searches for each creative BTC angle
        for angle in creative_angles[:2]:
            query = angle.get("search_query", "")
            if not query:
                continue

            logger.info(f"Bitcoin creative search: {query}")

            try:
                search_results = await self._execute_web_search(query)
                if not search_results:
                    continue

                # Analyze results through the Bitcoin creative lens
                analysis = await self._analyze_bitcoin_creative_results(
                    query=query,
                    results=search_results,
                    creative_angle=angle,
                )
                results["tokens_used"] += analysis.get("tokens_used", 0)

                for signal in analysis.get("promising", [])[:3]:
                    signal["creative_angle"] = angle.get("angle", "")
                    signal["btc_mechanism"] = angle.get("btc_mechanism", "")
                    opportunity = await self._create_opportunity_from_bitcoin_creative_signal(
                        db=db,
                        signal=signal,
                        query=query,
                        tool_slugs=tool_slugs,
                    )
                    if opportunity:
                        results["opportunities"].append(opportunity)

            except Exception as e:
                logger.warning(f"Bitcoin creative search failed for '{query}': {e}")
                continue

        return results

    async def _analyze_bitcoin_creative_results(
        self,
        query: str,
        results: List[Dict[str, Any]],
        creative_angle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze search results for Bitcoin creative acquisition angles."""
        raw_results_text = "\n\n".join([
            f"[{i+1}] {r.get('title', 'No title')}\n"
            f"URL: {r.get('link', 'No URL')}\n"
            f"Snippet: {r.get('snippet', 'No snippet')}"
            for i, r in enumerate(results[:10])
        ])
        sanitized_results, _det = sanitize_external_content(
            raw_results_text, source="web_search",
        )
        results_text = wrap_external_content(sanitized_results, source="web_search")

        prompt = f"""Analyze these search results for our Bitcoin acquisition creative angle.

## Creative Angle
**Idea:** {creative_angle.get('angle', '')}
**Reasoning:** {creative_angle.get('reasoning', '')}
**BTC Mechanism:** {creative_angle.get('btc_mechanism', '')}

## Search Query
{query}

## Results
{results_text}

## Your Task

Find evidence that this creative angle can generate Bitcoin/sats:
- Is anyone already doing this? How much are they earning?
- What infrastructure or setup is needed beyond our LN node?
- How quickly could we start earning sats from this?
- What's the realistic earning potential in sats/day or sats/month?

## ACCEPT if:
- There's a clear, concrete path to acquiring BTC/sats
- It leverages our existing Lightning Network node
- It can be started with minimal additional investment
- There's evidence of demand or existing market

## REJECT if:
- It's just Bitcoin news or price analysis
- It requires large capital or specialized hardware we don't have
- It's a scam, Ponzi, or unrealistic "guaranteed returns"
- It only works in specific geographic regions
- The opportunity is so saturated it's no longer viable

Return JSON:
```json
{{
  "promising": [
    {{
      "signal": "HOW specifically this generates Bitcoin/sats for us",
      "opportunity_type": "service/product/affiliate/arbitrage/automation/other",
      "revenue_potential": "low/medium/high",
      "time_sensitivity": "immediate/short/medium/evergreen",
      "title": "Descriptive title - Bitcoin acquisition via [method]",
      "source_url": "the URL",
      "raw_snippet": "the snippet"
    }}
  ],
  "validation_notes": "Can this creative angle realistically generate sats?"
}}
```

Be very selective. Only include results with a clear BTC acquisition path."""

        messages = [LLMMessage(role="user", content=prompt)]

        response = await llm_service.generate(
            messages=messages,
            model=self._effective_tier("fast"),
            max_tokens=4000,
        )

        try:
            content = response.content
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start != -1 and json_end > 0:
                parsed = json.loads(content[json_start:json_end])
                parsed["tokens_used"] = response.total_tokens
                return parsed
        except json.JSONDecodeError:
            pass

        return {"promising": [], "tokens_used": response.total_tokens}

    async def _create_opportunity_from_bitcoin_creative_signal(
        self,
        db: AsyncSession,
        signal: Dict[str, Any],
        query: str,
        tool_slugs: set,
    ) -> Optional[Opportunity]:
        """Create an Opportunity from a Bitcoin creative exploration signal."""
        type_map = {
            "arbitrage": OpportunityType.ARBITRAGE,
            "service": OpportunityType.SERVICE,
            "product": OpportunityType.PRODUCT,
            "automation": OpportunityType.AUTOMATION,
            "affiliate": OpportunityType.AFFILIATE,
            "investment": OpportunityType.INVESTMENT,
        }
        sensitivity_map = {
            "immediate": TimeSensitivity.IMMEDIATE,
            "short": TimeSensitivity.SHORT,
            "medium": TimeSensitivity.MEDIUM,
            "evergreen": TimeSensitivity.EVERGREEN,
        }

        creative_context = signal.get("creative_angle", "")
        btc_mechanism = signal.get("btc_mechanism", "")
        summary = signal.get("signal", "")
        if creative_context:
            summary = f"[BTC creative: {creative_context}]\n"
        if btc_mechanism:
            summary += f"[Mechanism: {btc_mechanism}]\n\n"
        summary += signal.get("signal", "")

        title = signal.get("title", "Untitled Bitcoin Creative Opportunity")
        source_urls = [signal.get("source_url")] if signal.get("source_url") else []

        # Dedup check
        existing = await opportunity_service.find_duplicate_opportunity(
            db, title=title, source_urls=source_urls,
        )
        if existing:
            logger.info(
                "Skipping duplicate BTC creative opportunity: '%s' matches existing '%s' (%s)",
                title, existing.title, existing.id,
            )
            return None

        opportunity = Opportunity(
            title=title,
            summary=summary,
            opportunity_type=type_map.get(signal.get("opportunity_type", ""), OpportunityType.OTHER),
            status=OpportunityStatus.DISCOVERED,
            discovery_strategy_id=None,
            source_type="bitcoin_creative",
            source_query=query,
            source_urls=source_urls,
            raw_signal=signal.get("raw_snippet", ""),
            time_sensitivity=sensitivity_map.get(signal.get("time_sensitivity", ""), None),
            initial_assessment=f"Bitcoin creative exploration: {signal.get('signal', '')}",
        )

        db.add(opportunity)
        await db.flush()
        logger.info(f"Created Bitcoin creative opportunity: {opportunity.title}")
        return opportunity

    async def _create_opportunity_from_signal(
        self,
        db: AsyncSession,
        signal: Dict[str, Any],
        strategy: DiscoveryStrategy,
        query: str,
        tool_slugs: set,
    ) -> Optional[Opportunity]:
        """Create an Opportunity record from a promising signal."""
        
        # Map string values to enums
        type_map = {
            "arbitrage": OpportunityType.ARBITRAGE,
            "content": OpportunityType.CONTENT,
            "service": OpportunityType.SERVICE,
            "product": OpportunityType.PRODUCT,
            "automation": OpportunityType.AUTOMATION,
            "affiliate": OpportunityType.AFFILIATE,
            "investment": OpportunityType.INVESTMENT,
        }
        
        sensitivity_map = {
            "immediate": TimeSensitivity.IMMEDIATE,
            "short": TimeSensitivity.SHORT,
            "medium": TimeSensitivity.MEDIUM,
            "evergreen": TimeSensitivity.EVERGREEN,
        }
        
        title = signal.get("title", "Untitled Opportunity")
        source_urls = [signal.get("source_url")] if signal.get("source_url") else []

        # Dedup check — skip if a similar opportunity already exists
        existing = await opportunity_service.find_duplicate_opportunity(
            db, title=title, source_urls=source_urls,
        )
        if existing:
            logger.info(
                "Skipping duplicate opportunity: '%s' matches existing '%s' (%s)",
                title, existing.title, existing.id,
            )
            return None

        opportunity = Opportunity(
            title=title,
            summary=signal.get("signal", ""),
            opportunity_type=type_map.get(signal.get("opportunity_type", ""), OpportunityType.OTHER),
            status=OpportunityStatus.DISCOVERED,
            discovery_strategy_id=strategy.id,
            source_type="web_search",
            source_query=query,
            source_urls=source_urls,
            raw_signal=signal.get("raw_snippet", ""),
            time_sensitivity=sensitivity_map.get(signal.get("time_sensitivity", ""), None),
            initial_assessment=signal.get("signal", ""),
        )
        
        db.add(opportunity)
        await db.flush()  # Get the ID
        
        return opportunity

    # ==========================================================================
    # Evaluation Phase - Score and rank opportunities
    # ==========================================================================
    
    async def evaluate_opportunities(
        self,
        context: AgentContext,
        opportunity_ids: Optional[List[UUID]] = None,
    ) -> AgentResult:
        """
        Evaluate and score discovered opportunities.
        
        Uses reasoning LLM to deeply analyze each opportunity and assign scores.
        """
        db = context.db
        
        # Get opportunities to evaluate
        query = select(Opportunity).where(
            Opportunity.status.in_([OpportunityStatus.DISCOVERED, OpportunityStatus.RESEARCHING])
        )
        if opportunity_ids:
            query = query.where(Opportunity.id.in_(opportunity_ids))
        
        result = await db.execute(query)
        opportunities = list(result.scalars().all())
        
        if not opportunities:
            return AgentResult(
                success=True,
                message="No opportunities to evaluate",
                data={"evaluated": 0},
            )
        
        # Get rubric
        rubric = await self._get_active_rubric(db)
        tools = await self.get_available_tools(db)
        tool_slugs = {t.slug for t in tools}
        
        total_tokens = 0
        evaluated = []
        
        for opp in opportunities:
            try:
                eval_result = await self._evaluate_single_opportunity(
                    db=db,
                    opportunity=opp,
                    rubric=rubric,
                    tool_slugs=tool_slugs,
                )
                total_tokens += eval_result.get("tokens_used", 0)
                evaluated.append(opp.id)
            except Exception as e:
                logger.error(f"Failed to evaluate opportunity {opp.id}: {e}")
        
        await db.commit()
        
        # Re-rank all evaluated opportunities
        await self._rank_opportunities(db)
        await db.commit()  # Commit ranking changes
        
        return AgentResult(
            success=True,
            message=f"Evaluated {len(evaluated)} opportunities",
            data={
                "evaluated": len(evaluated),
                "opportunity_ids": [str(oid) for oid in evaluated],
            },
            tokens_used=total_tokens,
        )

    async def _evaluate_single_opportunity(
        self,
        db: AsyncSession,
        opportunity: Opportunity,
        rubric: Optional[ScoringRubric],
        tool_slugs: set,
    ) -> Dict[str, Any]:
        """Deeply evaluate a single opportunity."""
        
        # Build evaluation prompt
        rubric_text = ""
        if rubric:
            factors = rubric.factors
            rubric_text = "Score each factor 0-1:\n" + "\n".join([
                f"- {name}: {f.get('description', '')} (weight: {f.get('weight', 0.1)})"
                for name, f in factors.items()
            ])
        else:
            rubric_text = """Score each factor 0-1:
- market_validation: Evidence people pay for this (weight: 0.25)
- competition_level: How crowded is the space, lower is better (weight: 0.15)
- time_to_revenue: How quickly can we monetize (weight: 0.20)
- tool_alignment: Do we have the tools needed (weight: 0.15)
- effort_reward_ratio: Expected return vs effort (weight: 0.15)
- risk_level: What could go wrong, lower is better (weight: 0.10)"""

        eval_prompt = f"""Evaluate this opportunity in detail.

## Opportunity
Title: {opportunity.title}
Type: {opportunity.opportunity_type.value}
Summary: {opportunity.summary}
Source: {opportunity.source_urls[0] if opportunity.source_urls else 'Unknown'}
Initial Assessment: {opportunity.initial_assessment or 'None'}

## Available Tools
{', '.join(list(tool_slugs)[:20])}

## Scoring Rubric
{rubric_text}

Provide a thorough analysis and scoring:

```json
{{
  "detailed_analysis": "2-3 paragraphs of analysis",
  "score_breakdown": {{
    "market_validation": 0.7,
    "competition_level": 0.5,
    "time_to_revenue": 0.8,
    "tool_alignment": 0.9,
    "effort_reward_ratio": 0.6,
    "risk_level": 0.4
  }},
  "overall_score": 0.65,
  "confidence_score": 0.8,
  "estimated_effort": "moderate",
  "estimated_revenue_potential": {{
    "min": 500,
    "max": 5000,
    "timeframe": "monthly",
    "recurring": true
  }},
  "estimated_cost": {{
    "upfront": 200,
    "ongoing": 50,
    "currency": "USD"
  }},
  "estimated_revenue_sats": null,
  "estimated_cost_sats": null,
  "required_tools": ["tool-slug-1", "tool-slug-2"],
  "required_skills": ["skill1", "skill2"],
  "blocking_requirements": [],
  "ranking_factors": {{
    "strengths": ["strength1", "strength2"],
    "weaknesses": ["weakness1"],
    "unique_angle": "What makes this different"
  }},
  "recommendation": "approve|research_more|reject",
  "recommendation_reason": "Why this recommendation"
}}
```

IMPORTANT: Always provide `estimated_cost` with upfront and ongoing amounts. If this is a Bitcoin/cryptocurrency/Lightning/Nostr opportunity, also fill in `estimated_revenue_sats` and `estimated_cost_sats` with satoshi amounts."""

        messages = [
            LLMMessage(role="user", content=eval_prompt),
        ]
        
        # Use reasoning tier for evaluation (upgraded to quality for Ollama)
        response = await self.think(messages, model=self._effective_tier("reasoning"), max_tokens=6000)
        
        try:
            # Extract JSON from response (may be wrapped in markdown code blocks)
            content = response.content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
                content = json_match
            elif "```" in content:
                # Try extracting from generic code blocks
                json_match = content.split("```")[1].split("```")[0].strip()
                content = json_match
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end > 0:
                eval_data = json.loads(content[json_start:json_end])
                
                # Update opportunity with evaluation
                opportunity.detailed_analysis = eval_data.get("detailed_analysis", "")
                opportunity.score_breakdown = eval_data.get("score_breakdown", {})
                opportunity.overall_score = eval_data.get("overall_score", 0.5)
                opportunity.confidence_score = eval_data.get("confidence_score", 0.5)
                opportunity.ranking_factors = eval_data.get("ranking_factors", {})
                opportunity.required_tools = eval_data.get("required_tools", [])
                opportunity.required_skills = eval_data.get("required_skills", [])
                opportunity.estimated_cost = eval_data.get("estimated_cost")
                opportunity.blocking_requirements = eval_data.get("blocking_requirements", [])
                opportunity.estimated_revenue_potential = eval_data.get("estimated_revenue_potential")
                
                # Store sats estimates in the revenue/cost dicts if provided
                if eval_data.get("estimated_revenue_sats") is not None:
                    if opportunity.estimated_revenue_potential is None:
                        opportunity.estimated_revenue_potential = {}
                    opportunity.estimated_revenue_potential["sats"] = eval_data["estimated_revenue_sats"]
                if eval_data.get("estimated_cost_sats") is not None:
                    if opportunity.estimated_cost is None:
                        opportunity.estimated_cost = {}
                    opportunity.estimated_cost["sats"] = eval_data["estimated_cost_sats"]
                
                # Map effort level
                effort_map = {
                    "minimal": EffortLevel.MINIMAL,
                    "moderate": EffortLevel.MODERATE,
                    "significant": EffortLevel.SIGNIFICANT,
                    "major": EffortLevel.MAJOR,
                }
                opportunity.estimated_effort = effort_map.get(
                    eval_data.get("estimated_effort", ""), 
                    EffortLevel.MODERATE
                )
                
                # IMPORTANT: Refresh from DB and only update status if still in evaluatable state
                # This prevents overwriting user decisions made while evaluation was running
                await db.refresh(opportunity)
                if opportunity.status in [OpportunityStatus.DISCOVERED, OpportunityStatus.RESEARCHING]:
                    # Auto-dismiss low-scoring opportunities (below 0.3)
                    if opportunity.overall_score is not None and opportunity.overall_score < 0.3:
                        opportunity.status = OpportunityStatus.DISMISSED
                        opportunity.agent_notes = (opportunity.agent_notes or "") + (
                            f"\nAuto-dismissed: score {opportunity.overall_score:.2f} below 0.3 threshold"
                        )
                        logger.info(
                            f"Auto-dismissed opportunity {opportunity.id} "
                            f"(score {opportunity.overall_score:.2f} < 0.3)"
                        )
                    else:
                        opportunity.status = OpportunityStatus.EVALUATED
                else:
                    logger.info(
                        f"Opportunity {opportunity.id} status changed to {opportunity.status} "
                        "during evaluation, not overwriting"
                    )
                
                return {"tokens_used": response.total_tokens, "eval_data": eval_data}
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse evaluation JSON: {e}")
        
        return {"tokens_used": response.total_tokens}

    async def _rank_opportunities(self, db: AsyncSession) -> None:
        """Rank all evaluated opportunities by score and assign tiers."""
        
        # Get all evaluated/presented opportunities
        query = select(Opportunity).where(
            Opportunity.status.in_([OpportunityStatus.EVALUATED, OpportunityStatus.PRESENTED]),
            Opportunity.overall_score.isnot(None),
        ).order_by(
            Opportunity.overall_score.desc(),
            Opportunity.time_sensitivity.asc(),  # More urgent first among same score
        )
        
        result = await db.execute(query)
        opportunities = list(result.scalars().all())
        
        for rank, opp in enumerate(opportunities, 1):
            opp.rank_position = rank
            
            # Assign tier based on score
            score = opp.overall_score or 0
            if score >= 0.8:
                opp.ranking_tier = RankingTier.TOP_PICK
            elif score >= 0.6:
                opp.ranking_tier = RankingTier.PROMISING
            elif score >= 0.4:
                opp.ranking_tier = RankingTier.MAYBE
            else:
                opp.ranking_tier = RankingTier.UNLIKELY

    # ==========================================================================
    # Learning Phase - Extract insights and update strategies
    # ==========================================================================
    
    async def reflect_and_learn(
        self,
        context: AgentContext,
        deep_reflection: bool = False,
    ) -> AgentResult:
        """
        Reflect on recent outcomes and extract insights.
        Also evolves discovery strategies based on performance.
        
        Args:
            context: Agent context
            deep_reflection: If True, use quality LLM for thorough analysis
        """
        db = context.db
        
        # Get recent outcomes with user feedback
        recent_outcomes = await self._get_outcomes_with_feedback(db, days=7)
        
        # Get current strategies with their performance stats
        strategies = await self._get_active_strategies(db)
        strategies_text = "\n".join([
            f"- **{s.name}** (executed {s.times_executed}x, found {s.opportunities_found} opps, "
            f"approved {s.opportunities_approved})\n"
            f"  Current queries: {s.search_queries}\n"
            f"  Effectiveness: {s.effectiveness_score or 'Not measured'}"
            for s in strategies
        ]) if strategies else "No active strategies"
        
        if not recent_outcomes and not strategies:
            return AgentResult(
                success=True,
                message="No recent outcomes to learn from",
                data={"insights_created": 0},
            )
        
        # Build reflection prompt with strategy evolution
        outcomes_text = "\n".join([
            f"- Strategy: {o.strategy.name if o.strategy else 'Unknown'}\n"
            f"  Queries run: {o.queries_run}\n"
            f"  Found: {o.opportunities_discovered}, User decision: {o.user_decision or 'pending'}\n"
            f"  User feedback: {o.user_feedback or 'none'}"
            for o in recent_outcomes[:20]
        ]) if recent_outcomes else "No recent outcomes yet"
        
        # Conditionally build Bitcoin learning section
        from app.core.config import settings as app_settings
        bitcoin_section = ""
        if app_settings.use_lnd:
            # Get recent BTC opportunity stats
            btc_result = await db.execute(
                select(
                    Opportunity.source_type,
                    Opportunity.title,
                    Opportunity.status,
                    Opportunity.overall_score,
                    Opportunity.user_decision,
                )
                .where(Opportunity.source_type.in_(["bitcoin_search", "bitcoin_creative"]))
                .order_by(Opportunity.discovered_at.desc())
                .limit(20)
            )
            btc_rows = btc_result.fetchall()
            if btc_rows:
                btc_opps_text = "\n".join([
                    f"- [{row[0]}] {row[1]} — status: {row[2]}, "
                    f"score: {row[3] or 'unscored'}, decision: {row[4] or 'pending'}"
                    for row in btc_rows
                ])
            else:
                btc_opps_text = "No Bitcoin opportunities found yet"

            bitcoin_section = f"""

## Bitcoin Acquisition Focus
We actively search for opportunities to acquire Bitcoin/sats. We run a Lightning Network node.

**Recent Bitcoin Opportunities:**
{btc_opps_text}

**Bitcoin-Specific Learning Goals:**
4. Which Bitcoin acquisition methods (earn/cashback/routing/selling/mining/affiliate/arbitrage)
   are producing approved opportunities?
5. What Bitcoin-related search queries are finding actionable results vs noise?
6. Are there Bitcoin earning patterns the user prefers (passive vs active, Lightning-specific vs general)?
7. How can we improve our Bitcoin search queries and creative angles?

Include a `bitcoin_learnings` section in your response with:
- Which BTC acquisition methods are working
- Suggested improvements to Bitcoin search queries
- New Bitcoin creative angles to try based on what resonated"""

        reflection_prompt = f"""Analyze these recent discovery outcomes and extract learnings.
Also evaluate and evolve our discovery strategies based on what's working.

## Recent Outcomes (last 7 days)
{outcomes_text}

## Current Discovery Strategies & Performance
{strategies_text}
{bitcoin_section}

## Your Task
1. Extract insights about what types of opportunities resonate with the user
2. Identify patterns in approved vs dismissed opportunities  
3. **Evolve strategy search queries** - improve underperforming strategies by updating their queries

For strategy evolution, consider:
- Which search terms are finding valuable opportunities?
- Which terms are generating noise or irrelevant results?
- What new angles or keywords might work better?
- Are there seasonal/trending terms to incorporate?

Respond in this exact JSON format:
```json
{{
  "insights": [
    {{
      "type": "principle|pattern|anti_pattern|hypothesis",
      "title": "Short insight title",
      "description": "Detailed explanation",
      "confidence": 0.7,
      "domains": ["relevant", "domains"],
      "evidence": ["outcome references"]
    }}
  ],
  "strategy_evolutions": [
    {{
      "strategy_name": "Exact name of strategy to update",
      "new_queries": ["updated query 1", "updated query 2", "updated query 3"],
      "reason": "Why these queries will perform better",
      "change_type": "refine|expand|pivot"
    }}
  ],
  "bitcoin_learnings": {{
    "effective_methods": ["Methods that are finding good BTC opportunities"],
    "query_improvements": ["Improved Bitcoin search queries to try"],
    "creative_angles": ["New Bitcoin creative angles based on patterns"],
    "overall_btc_assessment": "How well are we finding Bitcoin acquisition opportunities?"
  }},
  "overall_assessment": "Brief summary of learning"
}}
```

Guidelines for strategy_evolutions:
- Only include strategies that need updating (don't change what's working)
- change_type "refine" = small tweaks to existing queries
- change_type "expand" = add new angles while keeping core queries  
- change_type "pivot" = major change in direction due to poor performance
- Provide 3-5 specific, actionable search queries per strategy
- Queries should be concrete (e.g., "AI writing tools for YouTubers 2024" not "AI tools")"""

        messages = [
            LLMMessage(role="user", content=reflection_prompt),
        ]
        
        model = "quality" if deep_reflection else "reasoning"
        response = await self.think(messages, model=self._effective_tier(model), max_tokens=6000)
        
        insights_created = []
        
        try:
            # Extract JSON from response (may be wrapped in markdown code blocks)
            content = response.content
            if "```json" in content:
                json_match = content.split("```json")[1].split("```")[0].strip()
                content = json_match
            elif "```" in content:
                json_match = content.split("```")[1].split("```")[0].strip()
                content = json_match
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start != -1 and json_end > 0:
                learning = json.loads(content[json_start:json_end])
                
                # Create insights
                for insight_data in learning.get("insights", []):
                    type_map = {
                        "principle": InsightType.PRINCIPLE,
                        "pattern": InsightType.PATTERN,
                        "anti_pattern": InsightType.ANTI_PATTERN,
                        "hypothesis": InsightType.HYPOTHESIS,
                    }
                    
                    insight = AgentInsight(
                        insight_type=type_map.get(insight_data.get("type", ""), InsightType.HYPOTHESIS),
                        title=insight_data.get("title", ""),
                        description=insight_data.get("description", ""),
                        confidence=insight_data.get("confidence", 0.5),
                        domains=insight_data.get("domains", []),
                        evidence=insight_data.get("evidence", []),
                    )
                    db.add(insight)
                    insights_created.append(insight)
                
                # Apply strategy evolutions
                strategies_evolved = await self._apply_strategy_evolutions(
                    db, 
                    learning.get("strategy_evolutions", []),
                    strategies
                )
                
                await db.commit()
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse reflection JSON: {e}")
            strategies_evolved = 0
        
        return AgentResult(
            success=True,
            message=f"Reflection complete: {len(insights_created)} insights, {strategies_evolved} strategies evolved",
            data={
                "insights_created": len(insights_created),
                "strategies_evolved": strategies_evolved,
                "reflection": response.content,
            },
            tokens_used=response.total_tokens,
            model_used=response.model,
        )

    async def _apply_strategy_evolutions(
        self,
        db: AsyncSession,
        evolutions: List[Dict[str, Any]],
        strategies: List[DiscoveryStrategy],
    ) -> int:
        """
        Apply strategy evolutions recommended by the learning phase.
        
        Args:
            db: Database session
            evolutions: List of evolution recommendations from LLM
            strategies: Current active strategies
            
        Returns:
            Number of strategies updated
        """
        if not evolutions:
            return 0
            
        # Build lookup by strategy name
        strategy_map = {s.name.lower(): s for s in strategies}
        updated_count = 0
        
        for evolution in evolutions:
            strategy_name = evolution.get("strategy_name", "").lower()
            new_queries = evolution.get("new_queries", [])
            reason = evolution.get("reason", "")
            change_type = evolution.get("change_type", "refine")
            
            # Find matching strategy
            strategy = strategy_map.get(strategy_name)
            if not strategy:
                # Try partial matching
                for name, strat in strategy_map.items():
                    if strategy_name in name or name in strategy_name:
                        strategy = strat
                        break
            
            if not strategy:
                logger.warning(f"Strategy evolution: could not find strategy '{evolution.get('strategy_name')}'")
                continue
                
            if not new_queries or len(new_queries) < 2:
                logger.warning(f"Strategy evolution: insufficient queries for '{strategy.name}'")
                continue
            
            # Store old queries for logging
            old_queries = strategy.search_queries or []
            
            # Update the strategy
            strategy.search_queries = new_queries
            
            logger.info(
                f"Strategy evolved: '{strategy.name}' ({change_type})\n"
                f"  Old queries: {old_queries}\n"
                f"  New queries: {new_queries}\n"
                f"  Reason: {reason}"
            )
            
            updated_count += 1
        
        return updated_count

    # ==========================================================================
    # Helper Methods
    # ==========================================================================
    
    async def _get_active_strategies(self, db: AsyncSession) -> List[DiscoveryStrategy]:
        """Get all active discovery strategies."""
        query = select(DiscoveryStrategy).where(
            DiscoveryStrategy.status == StrategyStatus.ACTIVE
        ).order_by(DiscoveryStrategy.effectiveness_score.desc().nullslast())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _get_recent_insights(self, db: AsyncSession, limit: int = 10) -> List[AgentInsight]:
        """Get recent agent insights."""
        query = select(AgentInsight).order_by(
            AgentInsight.created_at.desc()
        ).limit(limit)
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _get_latest_memory_summary(self, db: AsyncSession) -> Optional[MemorySummary]:
        """Get the most recent memory summary."""
        query = select(MemorySummary).order_by(
            MemorySummary.created_at.desc()
        ).limit(1)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def _get_strategy_statistics(self, db: AsyncSession) -> Dict[str, Any]:
        """Get aggregate statistics about strategies."""
        # Total strategies
        total_query = select(func.count(DiscoveryStrategy.id))
        total = (await db.execute(total_query)).scalar() or 0
        
        # Active strategies
        active_query = select(func.count(DiscoveryStrategy.id)).where(
            DiscoveryStrategy.status == StrategyStatus.ACTIVE
        )
        active = (await db.execute(active_query)).scalar() or 0
        
        # Total opportunities found
        found_query = select(func.sum(DiscoveryStrategy.opportunities_found))
        found = (await db.execute(found_query)).scalar() or 0
        
        # Total approved
        approved_query = select(func.sum(DiscoveryStrategy.opportunities_approved))
        approved = (await db.execute(approved_query)).scalar() or 0
        
        approval_rate = approved / found if found > 0 else 0
        
        return {
            "total": total,
            "active": active,
            "opportunities_found": found,
            "opportunities_approved": approved,
            "approval_rate": approval_rate,
        }

    async def _get_active_rubric(self, db: AsyncSession) -> Optional[ScoringRubric]:
        """Get the currently active scoring rubric."""
        query = select(ScoringRubric).where(ScoringRubric.is_active == True)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def _get_outcomes_with_feedback(
        self, 
        db: AsyncSession, 
        days: int = 7,
    ) -> List[StrategyOutcome]:
        """Get recent strategy outcomes that have user feedback."""
        from sqlalchemy.orm import selectinload
        since = utc_now() - timedelta(days=days)
        query = select(StrategyOutcome).where(
            StrategyOutcome.executed_at >= since
        ).options(
            selectinload(StrategyOutcome.strategy)  # Eager load strategy relationship
        ).order_by(StrategyOutcome.executed_at.desc())
        result = await db.execute(query)
        return list(result.scalars().all())

    # ==========================================================================
    # Idea Review Phase - Process user ideas
    # ==========================================================================

    async def review_ideas(
        self,
        context: AgentContext,
        limit: int = 20,
    ) -> AgentResult:
        """
        Review new ideas from users and classify them.
        
        For each idea:
        - If tool-related → mark for Tool Scout
        - If opportunity-related → mark for processing and add to strategic context
        
        Returns summary of reviewed ideas.
        """
        db = context.db
        ideas_service = IdeasService(db)
        
        # Get all new ideas (across all users for now)
        new_ideas = await ideas_service.get_all_new_ideas(limit=limit)
        
        if not new_ideas:
            return AgentResult(
                success=True,
                message="No new ideas to review",
                data={"reviewed": 0},
            )
        
        # Build review prompt
        prompt = self._build_idea_review_prompt(new_ideas)
        
        messages = [
            LLMMessage(role="system", content=self._get_idea_review_system_prompt()),
            LLMMessage(role="user", content=prompt),
        ]
        
        # Use reasoning tier for classification (upgraded to quality for Ollama)
        response = await self.think(messages, model=self._effective_tier("reasoning"), max_tokens=6000)
        
        # Parse and apply classifications
        reviewed = await self._apply_idea_classifications(db, new_ideas, response.content)
        
        return AgentResult(
            success=True,
            message=f"Reviewed {len(new_ideas)} ideas: {reviewed['tool']} tool-related, {reviewed['opportunity']} opportunity-related",
            data=reviewed,
            tokens_used=response.total_tokens,
            model_used=response.model,
            latency_ms=response.latency_ms,
        )

    def _get_idea_review_system_prompt(self) -> str:
        """System prompt for idea classification."""
        return """You are the Opportunity Scout Agent reviewing user ideas.

## Your Task
Classify each idea as either:
1. **TOOL**: Related to new tools, APIs, integrations, or technical capabilities we should add
2. **OPPORTUNITY**: Related to business opportunities, strategies, revenue streams, or market insights

## Classification Guidelines

**TOOL ideas include:**
- Suggestions for new tools or integrations (e.g., "use Ollama for text generation")
- Technical improvements to the system
- New APIs or services to integrate
- Automation ideas that require new tools

**OPPORTUNITY ideas include:**
- Business opportunities or revenue streams
- Market insights or trends to explore
- Strategy suggestions for finding opportunities
- Customer or market segment ideas
- Process improvements that could generate value

## Output Format
Return JSON with classifications for each idea:

```json
{
  "classifications": [
    {
      "idea_id": "uuid-of-the-idea",
      "classification": "tool",
      "reasoning": "Brief explanation",
      "distilled_insight": "Concise, optimized version of the idea (1-2 sentences)",
      "category": "capability",
      "keywords": ["keyword1", "keyword2"]
    }
  ]
}
```

IMPORTANT: For the "classification" field, use ONLY one of these exact strings: "tool" or "opportunity" (not both, just one).
For the "category" field when classification is "opportunity", use one of: "capability", "interest", "constraint", "goal", "insight", "preference".

For TOOL classifications, still provide distilled_insight but it won't be added to strategic context.
The category field is only relevant for OPPORTUNITY classifications."""

    def _build_idea_review_prompt(self, ideas: List[UserIdea]) -> str:
        """Build prompt for idea review."""
        ideas_text = "\n\n".join([
            f"**Idea {i+1}**\nID: {idea.id}\nContent: {idea.reformatted_content}"
            for i, idea in enumerate(ideas)
        ])
        
        return f"""Please classify the following {len(ideas)} idea(s):

{ideas_text}

Classify each as either TOOL or OPPORTUNITY and provide a distilled insight."""

    async def _apply_idea_classifications(
        self,
        db: AsyncSession,
        ideas: List[UserIdea],
        response: str,
    ) -> Dict[str, Any]:
        """Parse LLM response and apply classifications to ideas."""
        ideas_service = IdeasService(db)
        context_service = StrategicContextService(db)
        
        # Parse JSON response
        parsed = self._extract_json_from_response(response)
        if not parsed:
            logger.error("Failed to parse idea classification response")
            return {"reviewed": 0, "tool": 0, "opportunity": 0, "errors": 1}
        
        classifications = parsed.get("classifications", [])
        
        # Build lookup for ideas
        idea_map = {str(idea.id): idea for idea in ideas}
        
        results = {"reviewed": 0, "tool": 0, "opportunity": 0, "context_added": 0}
        
        for classification in classifications:
            idea_id = classification.get("idea_id")
            if idea_id not in idea_map:
                continue
            
            idea = idea_map[idea_id]
            class_type = classification.get("classification", "").lower()
            reasoning = classification.get("reasoning", "")
            distilled = classification.get("distilled_insight", "")
            
            try:
                if class_type == "tool":
                    await ideas_service.mark_for_tool_scout(
                        idea_id=idea.id,
                        agent_name=self.name,
                        notes=reasoning,
                    )
                    results["tool"] += 1
                    
                elif class_type == "opportunity":
                    # Mark as opportunity
                    await ideas_service.mark_for_opportunity(
                        idea_id=idea.id,
                        agent_name=self.name,
                        notes=reasoning,
                    )
                    results["opportunity"] += 1
                    
                    # Add to strategic context
                    if distilled:
                        category_str = classification.get("category", "insight")
                        try:
                            category = StrategicContextCategory(category_str)
                        except ValueError:
                            category = StrategicContextCategory.INSIGHT
                        
                        keywords = classification.get("keywords", [])
                        
                        # Check for similar existing entry
                        existing = await context_service.find_similar_entry(
                            user_id=idea.user_id,
                            content=distilled,
                            category=category,
                        )
                        
                        if existing:
                            # Merge with existing
                            await context_service.merge_with_existing(
                                existing_entry_id=existing.id,
                                new_content=distilled,
                                source_idea_id=idea.id,
                            )
                        else:
                            # Create new entry
                            entry = await context_service.add_entry(
                                user_id=idea.user_id,
                                content=distilled,
                                category=category,
                                keywords=keywords,
                                source_idea_id=idea.id,
                            )
                            results["context_added"] += 1
                        
                        # Mark idea as fully processed
                        await ideas_service.mark_as_processed(
                            idea_id=idea.id,
                            distilled_content=distilled,
                        )
                
                results["reviewed"] += 1
                
            except Exception as e:
                logger.error(f"Error classifying idea {idea_id}: {e}")
        
        return results

    async def _get_strategic_context_for_planning(
        self,
        db: AsyncSession,
        user_id: Optional[UUID] = None,
    ) -> str:
        """Get formatted strategic context for use in planning prompts."""
        if not user_id:
            # For now, if no user specified, return empty context
            return ""
        
        context_service = StrategicContextService(db)
        return await context_service.format_context_for_prompt(user_id)

    # ==========================================================================
    # Required BaseAgent Methods
    # ==========================================================================
    
    def get_system_prompt(self, tools: List[Tool]) -> str:
        """Get the system prompt for this agent."""
        # Compact: this agent doesn't make tool_calls, it uses [SEARCH:] format
        tools_section = self.format_tools_for_prompt(tools, verbosity="compact", include_call_format=False)
        
        security_preamble = get_security_preamble("none")

        return f"""You are the Opportunity Scout Agent for Money Agents, an AI-powered system for automated money-making campaigns.

{security_preamble}

## Your Mission
Discover, evaluate, and present money-making opportunities to users. You learn from feedback to improve your discovery strategies over time.

## Your Capabilities
1. **Strategic Planning**: Create and refine discovery strategies
2. **Web Research**: Search the web for opportunity signals
3. **Evaluation**: Score opportunities using a dynamic rubric
4. **Learning**: Extract insights from user feedback

{tools_section}

## Communication Style
- Be analytical and data-driven
- Present findings clearly with evidence
- Acknowledge uncertainty when appropriate
- Learn from mistakes and share what you've learned"""

    async def execute(self, action: str, context: AgentContext) -> AgentResult:
        """Execute an agent action."""
        
        if action == "plan":
            return await self.create_strategic_plan(context)
        elif action == "discover":
            return await self.run_discovery(context)
        elif action == "evaluate":
            return await self.evaluate_opportunities(context)
        elif action == "reflect":
            return await self.reflect_and_learn(context)
        elif action == "review_ideas":
            return await self.review_ideas(context)
        else:
            return AgentResult(
                success=False,
                message=f"Unknown action: {action}",
            )
