"""
Tool Scout Agent - discovers and evaluates AI tools and capabilities.

The Tool Scout maintains a living knowledge base about the AI/tool landscape
and helps identify tools that could be useful for campaigns or expand
opportunities for the Opportunity Scout.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from app.core.datetime_utils import utc_now
from typing import Any, AsyncIterator, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent, AgentContext, AgentResult
from app.services.prompt_injection_guard import (
    get_security_preamble,
    sanitize_external_content,
    wrap_external_content,
)
from app.models import (
    Tool,
    ToolStatus,
    ToolCategory,
    User,
    UserIdea,
    IdeaStatus,
    ConversationType,
)
from app.models.resource import Resource, ResourceCategory
from app.models.tool_scout import (
    ToolKnowledge,
    ToolKnowledgeCategory,
    ToolKnowledgeStatus,
    ToolIdeaEntry,
)
from app.services.llm_service import LLMMessage, StreamChunk, llm_service
from app.services.tool_knowledge_service import ToolKnowledgeService
from app.services.tool_idea_service import ToolIdeaService
from app.services.ideas_service import IdeasService
from app.services.system_info_service import SystemInfoService

logger = logging.getLogger(__name__)


class ToolScoutAgent(BaseAgent):
    """
    Agent that discovers and evaluates AI tools and capabilities.
    
    Key capabilities:
    - Periodic internet searches for new AI tools and developments
    - Maintains living knowledge base about the tool landscape
    - Processes tool ideas from user idea queue
    - Creates Tool records for promising discoveries
    - Assists with tool discussions and implementation
    """
    
    name = "tool_scout"
    description = "Discovers and evaluates AI tools and capabilities"
    default_temperature = 0.7
    default_max_tokens = 6000  # High limit - we only pay for tokens actually used
    
    # Use quality tier for discovery, reasoning for evaluation
    model_tier = "quality"
    
    # No tool calls via <tool_call> tags — searches Serper directly via executor
    TOOL_ALLOWLIST: list[str] | None = []
    
    # ==========================================================================
    # Phase 1: Process Tool Ideas from Queue
    # ==========================================================================
    
    async def process_tool_ideas(
        self,
        context: AgentContext,
        limit: int = 10,
    ) -> AgentResult:
        """
        Process ideas flagged for Tool Scout from the ideas queue.
        
        Distills ideas into optimized form and stores in tool ideas resource.
        """
        db = context.db
        if not db:
            return AgentResult(
                success=False,
                message="Database session required",
            )
        
        # Get ideas flagged for tool scout
        ideas_service = IdeasService(db)
        tool_ideas = await ideas_service.get_ideas_for_tool_scout(limit=limit)
        
        if not tool_ideas:
            return AgentResult(
                success=True,
                message="No tool ideas to process",
                data={"processed": 0},
            )
        
        # Build prompt to distill ideas
        ideas_text = "\n\n".join([
            f"**Idea {i+1}**\nOriginal: {idea.reformatted_content}"
            for i, idea in enumerate(tool_ideas)
        ])
        
        system_prompt = self._get_idea_processing_prompt()
        user_prompt = f"""Process these {len(tool_ideas)} tool idea(s) from users:

{ideas_text}

For each idea, provide a distilled version in JSON format."""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model="reasoning",
            temperature=0.5,
            max_tokens=6000,
        )
        
        # Parse and store results
        processed = await self._apply_idea_processing(db, tool_ideas, response.content)
        
        return AgentResult(
            success=True,
            message=f"Processed {processed['processed']} tool ideas",
            data=processed,
            tokens_used=response.total_tokens,
            cost_usd=response.cost_usd,
        )
    
    def _get_idea_processing_prompt(self) -> str:
        """Get system prompt for idea processing."""
        preamble = get_security_preamble("none")
        return preamble + """

You are the Tool Scout Agent, processing tool ideas from users.

Your task is to distill each idea into an optimized form for long-term reference.

For each idea, extract:
1. **summary** - A clear, concise summary of what tool/capability is wanted (1-2 sentences)
2. **use_case** - What problem it would solve or what it would enable
3. **keywords** - 3-5 keywords for matching with future discoveries
4. **priority** - "low", "medium", or "high" based on potential impact

Return JSON in this format:
```json
{
  "processed_ideas": [
    {
      "idea_index": 1,
      "summary": "...",
      "use_case": "...",
      "keywords": ["keyword1", "keyword2"],
      "priority": "medium"
    }
  ]
}
```"""

    async def _apply_idea_processing(
        self,
        db: AsyncSession,
        ideas: List[UserIdea],
        response: str,
    ) -> Dict[str, Any]:
        """Apply idea processing results."""
        tool_idea_service = ToolIdeaService(db)
        
        parsed = self._extract_json_from_response(response)
        if not parsed:
            logger.error("Failed to parse idea processing response")
            return {"processed": 0, "errors": 1}
        
        processed_ideas = parsed.get("processed_ideas", [])
        results = {"processed": 0, "skipped": 0, "errors": 0}
        
        for item in processed_ideas:
            idx = item.get("idea_index", 0) - 1
            if idx < 0 or idx >= len(ideas):
                results["errors"] += 1
                continue
            
            idea = ideas[idx]
            
            try:
                # Check for similar existing entry
                existing = await tool_idea_service.find_similar_entry(
                    user_id=idea.user_id,
                    summary=item.get("summary", ""),
                    keywords=item.get("keywords", []),
                )
                
                if existing:
                    # Boost existing entry instead of creating duplicate
                    await tool_idea_service.boost_relevance(existing.id, 0.15)
                    # Still mark original idea as processed
                    idea.status = IdeaStatus.PROCESSED.value
                    idea.reviewed_at = utc_now()
                    idea.reviewed_by_agent = self.name
                    results["skipped"] += 1
                else:
                    # Create new entry
                    await tool_idea_service.process_idea_from_queue(
                        idea=idea,
                        distilled_summary=item.get("summary", idea.reformatted_content),
                        use_case=item.get("use_case"),
                        context=None,
                        keywords=item.get("keywords", []),
                        priority=item.get("priority"),
                    )
                    results["processed"] += 1
                    
            except Exception as e:
                logger.error(f"Error processing idea {idea.id}: {e}")
                results["errors"] += 1
        
        await db.commit()
        return results

    # ==========================================================================
    # Phase 2: Discover New Tools
    # ==========================================================================
    
    async def discover_tools(
        self,
        context: AgentContext,
        search_focus: Optional[str] = None,
    ) -> AgentResult:
        """
        Search the internet for new AI tools and developments.
        
        Uses web search to find new tools, then processes and stores findings
        in the knowledge base.
        """
        db = context.db
        if not db:
            return AgentResult(success=False, message="Database session required")
        
        knowledge_service = ToolKnowledgeService(db)
        tool_idea_service = ToolIdeaService(db)
        
        # Get current knowledge and ideas for context
        knowledge_context = await knowledge_service.format_knowledge_for_prompt(limit=20)
        ideas_context = await tool_idea_service.format_for_prompt(limit=15)
        
        # Build discovery prompt
        system_prompt = self._get_discovery_system_prompt()
        user_prompt = self._build_discovery_prompt(
            knowledge_context, 
            ideas_context,
            search_focus,
        )
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        # Let LLM decide what to search for
        response = await llm_service.generate(
            messages=messages,
            model="quality",
            temperature=0.7,
            max_tokens=6000,
        )
        
        tokens_used = response.total_tokens
        total_cost = response.cost_usd or 0
        
        # Extract search queries from response
        searches = self._extract_searches(response.content)
        
        if not searches:
            return AgentResult(
                success=True,
                message="No searches identified",
                data={"searches": 0},
                tokens_used=tokens_used,
                cost_usd=total_cost,
            )
        
        # Execute searches
        search_results = []
        for query in searches[:5]:  # Limit to 5 searches
            try:
                results = await self._execute_search(context, query)
                if results:
                    search_results.append({"query": query, "results": results})
            except Exception as e:
                logger.warning(f"Search failed for '{query}': {e}")
        
        if not search_results:
            return AgentResult(
                success=True,
                message="Searches returned no results",
                data={"searches": len(searches), "results": 0},
                tokens_used=tokens_used,
                cost_usd=total_cost,
            )
        
        # Analyze and store findings
        analysis_result = await self._analyze_search_results(
            context, search_results, knowledge_service
        )
        
        tokens_used += analysis_result.get("tokens_used", 0)
        total_cost += analysis_result.get("cost_usd", 0)
        entries_added = analysis_result.get("entries_added", 0)
        entries_updated = analysis_result.get("entries_updated", 0)
        
        # Run creative exploration as a bonus task
        logger.info("Starting creative exploration phase...")
        try:
            creative_result = await self._run_creative_exploration(
                context, knowledge_service, knowledge_context
            )
            tokens_used += creative_result.get("tokens_used", 0)
            total_cost += creative_result.get("cost_usd", 0)
            creative_entries = creative_result.get("entries_added", 0)
            creative_ideas = creative_result.get("ideas_explored", 0)
            entries_added += creative_entries
            
            logger.info(f"Creative exploration: {creative_ideas} ideas explored, {creative_entries} entries added")
        except Exception as e:
            logger.warning(f"Creative exploration failed (non-fatal): {e}")
            creative_ideas = 0
            creative_entries = 0
        
        return AgentResult(
            success=True,
            message=f"Discovered {entries_added} new knowledge entries ({creative_entries} from creative exploration)",
            data={
                "searches_executed": len(search_results),
                "entries_added": entries_added,
                "entries_updated": entries_updated,
                "creative_ideas_explored": creative_ideas,
                "creative_entries_added": creative_entries,
            },
            tokens_used=tokens_used,
            cost_usd=total_cost,
        )

    def _get_discovery_system_prompt(self) -> str:
        """Get system prompt for discovery phase."""
        # Get system information
        system_info = SystemInfoService.collect()
        system_context = system_info.format_for_prompt()
        
        preamble = get_security_preamble("[SEARCH:]")
        return f"""{preamble}

You are the Tool Scout Agent, an AI researcher focused on discovering valuable AI tools and capabilities.

{system_context}

## Your Mission

You are the eyes and ears of the system, constantly scanning the AI landscape for:
1. **New AI tools and APIs** - LLMs, image generators, voice synthesis, data analysis tools
2. **Automation services** - Workflow automation, web scraping, browser automation
3. **Cost-effective alternatives** - Open-source or cheaper alternatives to expensive tools
4. **Emerging capabilities** - New developments that could unlock new campaign types
5. **Infrastructure tools** - Deployment, monitoring, scheduling tools

## Strategic Focus Areas

Consider these high-value categories:
- **Content Generation**: AI writing, image/video creation, audio synthesis
- **Data & Research**: Web scraping, market research, competitive analysis
- **Communication**: Email automation, SMS, social media APIs
- **Payments & Commerce**: Payment processing, affiliate networks, e-commerce
- **Workflow Automation**: Zapier alternatives, n8n, Make, custom automation
- **Local AI**: Tools that run on local hardware (Ollama, llama.cpp, whisper.cpp)

## System Compatibility

**IMPORTANT:** Prioritize tools compatible with:
- {system_info.os_pretty_name} ({system_info.architecture})
- {system_info.ram_total_gb:.0f}GB RAM available
- {"GPU: " + system_info.gpus[0].name + " - can run local ML models" if system_info.gpus else "No GPU - prefer cloud APIs or CPU-optimized tools"}
- {"Docker available for containerized deployments" if system_info.docker_available else "No Docker - prefer native installations"}

## Search Strategy

To request a web search, use:
[SEARCH: your search query here]

**Tips for effective searches:**
- Use specific, current terms: "best AI tools 2025" or "new AI APIs January 2026"
- Search for specific categories: "open source text to speech API 2025"
- Look for comparisons: "Ollama vs vLLM performance comparison"
- Find alternatives: "free alternatives to [expensive tool]"
- Check for updates: "[tool name] new features 2025"

Request 3-5 focused searches. Even if no new tools result, add findings to the knowledge base."""

    def _build_discovery_prompt(
        self,
        knowledge_context: str,
        ideas_context: str,
        search_focus: Optional[str],
    ) -> str:
        """Build the discovery prompt."""
        focus_section = ""
        if search_focus:
            focus_section = f"\n## Search Focus\n{search_focus}\n"
        
        return f"""## Current Knowledge Base (Top Entries)
{knowledge_context}

## User Tool Ideas (What Users Want)
{ideas_context}
{focus_section}
## Today's Date
{utc_now().strftime('%Y-%m-%d')}

## Your Task

Based on the knowledge base and user ideas above, plan a strategic research session:

**Primary Goals:**
1. **Discover NEW tools** - Find AI tools, APIs, or services not in our knowledge base
2. **Track industry trends** - What's new in AI/automation this week?
3. **Find alternatives** - Cheaper or better options for expensive services
4. **Update stale knowledge** - Verify and refresh old entries

**Research Strategy:**
- Start broad: "new AI tools January 2026" or "best AI APIs 2025"
- Then go specific based on user ideas and knowledge gaps
- Look for practical, deployable tools (not just research papers)
- Check for open-source options that work on Linux

**IMPORTANT:** Even if you don't find tools worth proposing, ALWAYS add interesting 
findings to the knowledge base. Future runs will benefit from this accumulated knowledge.

Request 3-5 focused, strategic searches."""

    def _get_creative_exploration_prompt(self) -> str:
        """Get system prompt for creative 'what if' exploration."""
        return """You are the Tool Scout Agent in creative exploration mode.

## Your Mission

Take a step back from the usual tool hunting and engage in some creative, divergent thinking.
Your goal is to imagine capabilities that don't obviously exist yet, or creative combinations
of existing technologies that could unlock new possibilities.

## Creative Thinking Prompts

Consider these angles:
- **Cross-pollination**: What if we combined capability X with capability Y?
- **Inversion**: What's the opposite of how we usually do something? What if that worked?
- **Extreme scaling**: What if something that's currently slow/expensive became instant/free?
- **Missing pieces**: What's the annoying gap between two things we can already do?
- **Adjacent possible**: What new capability is *almost* achievable with current tech?
- **Serendipity**: What random emerging technology might have unexpected applications?

## Focus Areas for Money-Making Automation

Think about creative tools for:
- Finding opportunities others miss
- Automating tedious research or outreach
- Creating content in novel ways
- Analyzing data for hidden patterns
- Connecting disparate information sources
- Reducing friction in existing workflows

## Output Format

Generate exactly 2 creative "what if" ideas, then propose a search query for each:

```json
{
  "creative_ideas": [
    {
      "idea": "Wouldn't it be cool if we had a tool that could...",
      "reasoning": "Why this would be valuable and what problem it solves",
      "search_query": "A search query to explore if anything like this exists"
    },
    {
      "idea": "Wouldn't it be cool if we had a tool that could...",
      "reasoning": "Why this would be valuable and what problem it solves", 
      "search_query": "A search query to explore if anything like this exists"
    }
  ]
}
```

## Guidelines

- Be genuinely creative, not just "better version of X"
- Think about combinations and novel applications
- It's OK if the idea seems far-fetched - that's the point
- The search might not find anything - that's fine, we're exploring
- If something exists that's close, that's a win!"""

    def _build_creative_exploration_prompt(
        self,
        knowledge_context: str,
        recent_tools: List[str],
    ) -> str:
        """Build user prompt for creative exploration."""
        tools_list = ", ".join(recent_tools[:10]) if recent_tools else "None yet"
        
        return f"""## Current Context

**Tools we already have or are considering:**
{tools_list}

**Recent knowledge base entries (for inspiration, not limitation):**
{knowledge_context[:2000]}

## Today's Date
{utc_now().strftime('%Y-%m-%d')}

---

Now, engage your creative thinking mode!

Forget about what's "practical" for a moment. Dream a little. What capabilities would 
be genuinely exciting if they existed? What would make you say "wow, that's clever"?

Generate 2 creative "wouldn't it be cool if..." ideas with search queries to explore them."""

    async def _run_creative_exploration(
        self,
        context: AgentContext,
        knowledge_service: "ToolKnowledgeService",
        knowledge_context: str,
    ) -> Dict[str, Any]:
        """Run creative exploration phase to generate and search for novel ideas."""
        db = context.db
        if not db:
            return {"tokens_used": 0, "cost_usd": 0, "ideas_explored": 0}
        
        # Get existing tool names for context
        from sqlalchemy import select
        result = await db.execute(
            select(Tool.name).order_by(Tool.created_at.desc()).limit(15)
        )
        recent_tools = [row[0] for row in result.fetchall()]
        
        system_prompt = self._get_creative_exploration_prompt()
        user_prompt = self._build_creative_exploration_prompt(knowledge_context, recent_tools)
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        # Use higher temperature for more creative output
        response = await llm_service.generate(
            messages=messages,
            model="quality",
            temperature=0.9,  # Higher temperature for creativity
            max_tokens=6000,
        )
        
        tokens_used = response.total_tokens
        total_cost = response.cost_usd or 0
        
        # Parse creative ideas
        parsed = self._extract_json_from_response(response.content)
        if not parsed or "creative_ideas" not in parsed:
            logger.info("Creative exploration: No structured ideas generated")
            return {"tokens_used": tokens_used, "cost_usd": total_cost, "ideas_explored": 0}
        
        creative_ideas = parsed.get("creative_ideas", [])
        search_results = []
        
        # Execute searches for creative ideas
        for idea in creative_ideas[:2]:
            query = idea.get("search_query", "")
            if not query:
                continue
                
            logger.info(f"Creative exploration search: {query}")
            try:
                results = await self._execute_search(context, query)
                if results:
                    search_results.append({
                        "query": query,
                        "results": results,
                        "creative_idea": idea.get("idea", ""),
                        "reasoning": idea.get("reasoning", ""),
                    })
            except Exception as e:
                logger.warning(f"Creative search failed for '{query}': {e}")
        
        if not search_results:
            # Still log the creative ideas even if searches returned nothing
            logger.info(f"Creative ideas generated but no search results: {[i.get('idea', '')[:50] for i in creative_ideas]}")
            return {"tokens_used": tokens_used, "cost_usd": total_cost, "ideas_explored": len(creative_ideas)}
        
        # Analyze creative search results with context about the original idea
        analysis_result = await self._analyze_creative_search_results(
            context, search_results, knowledge_service
        )
        
        tokens_used += analysis_result.get("tokens_used", 0)
        total_cost += analysis_result.get("cost_usd", 0)
        
        return {
            "tokens_used": tokens_used,
            "cost_usd": total_cost,
            "ideas_explored": len(creative_ideas),
            "entries_added": analysis_result.get("entries_added", 0),
        }

    async def _analyze_creative_search_results(
        self,
        context: AgentContext,
        search_results: List[Dict[str, Any]],
        knowledge_service: "ToolKnowledgeService",
    ) -> Dict[str, Any]:
        """Analyze search results from creative exploration."""
        # Format results with the creative context
        # Sanitize external search result content before entering LLM prompt
        results_text = ""
        for sr in search_results:
            results_text += f"\n### Creative Idea: {sr.get('creative_idea', 'Unknown')}\n"
            results_text += f"**Why this would be cool:** {sr.get('reasoning', '')}\n"
            results_text += f"**Search:** {sr['query']}\n\n"
            for r in sr['results'][:5]:
                title, _ = sanitize_external_content(
                    r.get('title', 'No title'), source="web_search"
                )
                snippet, _ = sanitize_external_content(
                    r.get('snippet', 'No description'), source="web_search"
                )
                results_text += f"- **{title}**\n"
                results_text += f"  {snippet}\n"
                if r.get('link'):
                    results_text += f"  URL: {r['link']}\n"
        results_text = wrap_external_content(results_text, source="web_search")
        
        system_prompt = """You are analyzing search results from a creative exploration session.

The Tool Scout generated some "what if" ideas and searched to see if anything like them exists.

Your job is to extract any interesting findings - even partial matches or related tools count!
If we searched for "AI that reads minds" and found "AI that analyzes facial expressions", that's
still worth noting as a step in that direction.

For each finding worth keeping, provide:

```json
{
  "findings": [
    {
      "title": "Clear, searchable title",
      "summary": "What it is and how it relates to the creative idea we were exploring",
      "category": "tool|platform|technique|trend|capability",
      "keywords": ["keyword1", "keyword2", "keyword3"],
      "relevance": 0.7,
      "source_url": "https://...",
      "creative_connection": "How this connects to or approximates the creative idea"
    }
  ]
}
```

Be generous in what you capture - creative exploration is about serendipity!"""

        user_prompt = f"""Analyze these creative exploration search results:

{results_text}

Extract anything interesting, even if it's only partially related to the original idea.
The goal is to find unexpected gems or technologies moving in interesting directions."""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model="reasoning",
            temperature=0.5,
            max_tokens=6000,
        )
        
        tokens = response.total_tokens
        cost = response.cost_usd or 0
        
        # Parse and store findings
        parsed = self._extract_json_from_response(response.content)
        if not parsed:
            return {"tokens_used": tokens, "cost_usd": cost, "entries_added": 0}
        
        findings = parsed.get("findings", [])
        added = 0
        
        for finding in findings:
            title = finding.get("title", "")
            if not title:
                continue
            
            # Check for similar existing entry
            existing = await knowledge_service.find_similar(title, threshold=0.85)
            if existing:
                continue
            
            # Add creative connection to the summary if available
            summary = finding.get("summary", "")
            creative_note = finding.get("creative_connection", "")
            if creative_note:
                summary = f"{summary}\n\n*Creative exploration note: {creative_note}*"
            
            await knowledge_service.add_entry(
                title=title,
                summary=summary,
                category=finding.get("category", "capability"),
                keywords=finding.get("keywords", []),
                source_url=finding.get("source_url"),
                relevance_score=finding.get("relevance", 0.6),
            )
            added += 1
        
        if added > 0:
            await context.db.commit()
            logger.info(f"Creative exploration added {added} knowledge entries")
        
        return {"tokens_used": tokens, "cost_usd": cost, "entries_added": added}

    def _extract_searches(self, content: str) -> List[str]:
        """Extract search queries from agent response."""
        pattern = r'\[SEARCH:\s*([^\]]+)\]'
        matches = re.findall(pattern, content)
        return [m.strip() for m in matches if m.strip()]

    async def _execute_search(
        self,
        context: AgentContext,
        query: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Execute a web search using Serper with cost tracking."""
        from app.services.tool_execution_service import ToolExecutor
        from app.models import ToolExecution, ToolExecutionStatus, Tool
        from datetime import datetime
        from sqlalchemy import select
        
        db = context.db
        if not db:
            return None
        
        executor = ToolExecutor()
        
        try:
            # Use Serper search - pass params dict as expected
            result = await executor._execute_serper_search(
                tool=None,  # Not needed for direct search
                params={"query": query, "num": 8},
            )
            
            # Track the search cost
            try:
                # Find the serper tool in the catalog
                tool_result = await db.execute(
                    select(Tool).where(Tool.slug == "serper-web-search")
                )
                tool = tool_result.scalar_one_or_none()
                
                execution = ToolExecution(
                    tool_id=tool.id if tool else None,
                    agent_name="tool_scout",
                    status=ToolExecutionStatus.COMPLETED if result.success else ToolExecutionStatus.FAILED,
                    input_params={"query": query, "num": 8},
                    output_result={"organic_count": len(result.output.get("organic_results", []))} if result.success and result.output else None,
                    error_message=result.error if not result.success else None,
                    started_at=utc_now(),
                    completed_at=utc_now(),
                    cost_units=result.cost_units,
                    cost_details=result.cost_details,
                )
                db.add(execution)
                await db.flush()
            except Exception as track_error:
                # Don't fail the search if tracking fails
                import logging
                logging.getLogger(__name__).warning(f"Failed to track Tool Scout search cost: {track_error}")
            
            if result.success and result.output:
                # Serper returns results under organic_results key
                organic = result.output.get("organic_results", [])
                return organic if organic else None
            return None
        finally:
            await executor.close()

    async def _analyze_search_results(
        self,
        context: AgentContext,
        search_results: List[Dict[str, Any]],
        knowledge_service: ToolKnowledgeService,
    ) -> Dict[str, Any]:
        """Analyze search results and add to knowledge base."""
        # Format results for analysis
        # Sanitize external search result content before entering LLM prompt
        results_text = ""
        for sr in search_results:
            results_text += f"\n### Search: {sr['query']}\n"
            for r in sr['results'][:5]:
                title, _ = sanitize_external_content(
                    r.get('title', 'No title'), source="web_search"
                )
                snippet, _ = sanitize_external_content(
                    r.get('snippet', 'No description'), source="web_search"
                )
                results_text += f"- **{title}**\n"
                results_text += f"  {snippet}\n"
                if r.get('link'):
                    results_text += f"  URL: {r['link']}\n"
        results_text = wrap_external_content(results_text, source="web_search")
        
        system_prompt = """You are the Tool Scout Agent analyzing search results to build a valuable knowledge base.

## Your Mission

Extract and catalog useful information from search results. This knowledge base helps future 
runs make better decisions about which tools to recommend.

## Categories of Information to Extract

- **tool** - Specific AI tool, API, or service (e.g., "Ollama", "Anthropic Claude API")
- **platform** - Broader platforms or ecosystems (e.g., "Hugging Face", "Replicate")
- **technique** - Methods or approaches (e.g., "RAG with vector databases")
- **trend** - Industry trends or directions (e.g., "Local LLMs gaining popularity")
- **limitation** - Known issues or constraints (e.g., "GPT-4 rate limits")
- **integration** - How things connect (e.g., "Langchain supports Ollama")
- **cost** - Pricing information (e.g., "OpenAI API pricing changes")
- **capability** - What's possible (e.g., "Whisper can transcribe real-time audio")

## Output Format

Return ONLY valid JSON with no trailing commas. Each finding must have all required fields:

```json
{
  "findings": [
    {
      "title": "Clear, searchable title",
      "summary": "Detailed 2-3 sentence summary",
      "category": "tool",
      "keywords": ["keyword1", "keyword2"],
      "relevance": 0.8,
      "source_url": "https://example.com"
    }
  ]
}
```

IMPORTANT: Ensure valid JSON syntax - no trailing commas, properly escaped strings.

## Guidelines

- **Be thorough** - If something might be useful later, include it
- **Be specific** - "Ollama supports llama3.2" is better than "Ollama supports models"
- **Include pricing** - Cost information is valuable for tool decisions
- **Note limitations** - Knowing what doesn't work is as valuable as what does
- **Track trends** - Industry direction helps prioritize tool development
- **Score honestly** - 0.5 = marginally useful, 0.7 = good, 0.9+ = highly valuable
- **Limit to top 5** - Return only the 5 most valuable/relevant findings to ensure complete JSON"""

        user_prompt = f"""Analyze these search results and extract valuable findings for our knowledge base.

{results_text}

**Remember:** Build comprehensive knowledge even if no tools are immediately worth proposing. 
Future runs will benefit from this information. Return the top 5 most valuable findings (max 5 to ensure complete response)."""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model="reasoning",
            temperature=0.5,
            max_tokens=6000,
        )
        
        tokens = response.total_tokens
        cost = response.cost_usd or 0
        
        # Parse and store findings
        parsed = self._extract_json_from_response(response.content)
        if not parsed:
            return {"tokens_used": tokens, "cost_usd": cost, "entries_added": 0}
        
        findings = parsed.get("findings", [])
        added = 0
        updated = 0
        
        for finding in findings:
            try:
                # Check for similar existing entry
                existing = await knowledge_service.find_similar_entry(
                    title=finding.get("title", ""),
                    keywords=finding.get("keywords", []),
                )
                
                if existing:
                    # Update existing entry
                    await knowledge_service.validate_entry(
                        existing.id, 
                        boost_relevance=0.1,
                        agent_name=self.name,
                    )
                    updated += 1
                else:
                    # Add new entry
                    category_str = finding.get("category", "tool")
                    try:
                        category = ToolKnowledgeCategory(category_str)
                    except ValueError:
                        category = ToolKnowledgeCategory.TOOL
                    
                    await knowledge_service.add_entry(
                        title=finding.get("title", "Unnamed"),
                        summary=finding.get("summary", ""),
                        category=category,
                        source_url=finding.get("source_url"),
                        source_type="web_search",
                        keywords=finding.get("keywords", []),
                        relevance_score=finding.get("relevance", 0.7),
                    )
                    added += 1
                    
            except Exception as e:
                logger.error(f"Error storing finding: {e}")
        
        return {
            "tokens_used": tokens,
            "cost_usd": cost,
            "entries_added": added,
            "entries_updated": updated,
        }

    # ==========================================================================
    # Phase 3: Evaluate and Create Tool Records
    # ==========================================================================
    
    async def evaluate_for_tool_creation(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """
        Review knowledge base and ideas to identify tools worth creating.
        
        Creates Tool records for promising discoveries.
        """
        db = context.db
        if not db:
            return AgentResult(success=False, message="Database session required")
        
        knowledge_service = ToolKnowledgeService(db)
        tool_idea_service = ToolIdeaService(db)
        
        # Get high-relevance entries
        knowledge_entries = await knowledge_service.get_active_entries(limit=30)
        tool_ideas = await tool_idea_service.get_unaddressed_entries(limit=20)
        
        # Get existing tools with details to avoid duplicates
        result = await db.execute(
            select(Tool.name, Tool.slug, Tool.description, Tool.category)
            .order_by(Tool.created_at.desc())
        )
        existing_tools = [dict(row._mapping) for row in result.all()]
        
        # Fetch available resources so agent can recommend resource requirements
        resources = await self._get_available_resources(db)
        
        system_prompt = self._get_evaluation_system_prompt(resources)
        user_prompt = self._build_evaluation_prompt(
            knowledge_entries, 
            tool_ideas,
            existing_tools,
        )
        
        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model="quality",
            temperature=0.6,
            max_tokens=6000,  # Increased to allow for complete JSON responses
        )
        
        tokens = response.total_tokens
        cost = response.cost_usd or 0
        
        # Log response for debugging
        logger.info(f"Evaluation response length: {len(response.content)} chars")
        if not response.content or len(response.content) < 50:
            logger.warning(f"Short or empty evaluation response: {response.content[:200] if response.content else 'empty'}")
        
        # Parse and create tools
        created = await self._create_tools_from_evaluation(
            db, response.content, context.user_id
        )
        
        return AgentResult(
            success=True,
            message=f"Created {created} tool record(s) for review",
            data={"tools_created": created},
            tokens_used=tokens,
            cost_usd=cost,
        )

    def _get_evaluation_system_prompt(self, resources: Optional[List[Resource]] = None) -> str:
        """Get system prompt for tool evaluation."""
        # Get system information
        system_info = SystemInfoService.collect()
        
        # Format available resources
        resources_section = self._format_resources_for_prompt(resources or [])
        
        return f"""You are the Tool Scout Agent evaluating potential tools for the system.

## System Environment
- **OS:** {system_info.os_pretty_name} ({system_info.architecture})
- **Hardware:** {system_info.cpu_cores} cores, {system_info.ram_total_gb:.0f}GB RAM
- **GPU:** {"Yes - " + system_info.gpus[0].name if system_info.gpus else "No dedicated GPU"}
- **Docker:** {"Available" if system_info.docker_available else "Not available"}

## Available Resources

These are the configured system resources that tools can require:

{resources_section}

## Your Task

Evaluate the knowledge base and user ideas to identify tools worth formally adding to the 
system's tool catalog. Be thorough and professional in your assessments.

**Selection Criteria:**
1. Clear, specific capability not already covered
2. Could enable new campaign types or opportunities
3. Reasonable cost/effort to implement
4. User interest (from tool ideas) OR clear strategic value
5. **Compatible with our system environment**

## Tool Request Format

For each recommended tool, provide a **comprehensive, professional request**:

```json
{{
  "recommendations": [
    {{
      "name": "Professional Tool Name",
      "slug": "professional-tool-name",
      "category": "api|data_source|automation|analysis|communication",
      "description": "A concise 1-3 sentence plain text description of what this tool does and its primary value proposition. No markdown here.",
      "priority": "low|medium|high|critical",
      "implementation_notes": "## Implementation Plan\\n\\n### Prerequisites\\n- System requirements\\n- API keys needed\\n\\n### Installation\\n```bash\\npip install tool-name\\n# or\\ndocker pull tool-image\\n```\\n\\n### Configuration\\nStep-by-step config...\\n\\n### Integration Points\\nHow it connects to our system...",
      "strengths": ["Key strength 1", "Key strength 2", "Key strength 3"],
      "weaknesses": ["Limitation 1", "Limitation 2"],
      "best_use_cases": ["Use case 1 with explanation", "Use case 2 with explanation"],
      "cost_model": "free|freemium|paid|per_api_call|subscription",
      "cost_details": "Pricing breakdown: Free tier includes X, paid tier at $Y/month...",
      "integration_complexity": "trivial|simple|moderate|complex",
      "external_documentation_url": "https://official-docs-url.com",
      "addresses_ideas": [],
      "resource_ids": []
    }}
  ],
  "reasoning": "Overall reasoning for selections and what was considered but rejected."
}}
```

## Field Guidelines

**Plain text fields (NO markdown):**
- **name**: Professional, clear tool name
- **description**: 1-3 sentence summary - displayed as subtitle, keep it brief
- **cost_model**: One of: free, freemium, paid, per_api_call, subscription
- **cost_details**: Brief pricing info (1-2 sentences)
- **integration_complexity**: One of: trivial, simple, moderate, complex

**Markdown-supported fields (USE formatting!):**
- **implementation_notes**: This is the main documentation field! Use full markdown:
  - Headers (##, ###) to organize sections
  - Code blocks with language tags for commands/examples
  - Bullet lists for requirements
  - Include: Prerequisites, Installation, Configuration, Integration Points
- **strengths**: Array of strings - each can use inline markdown if needed
- **weaknesses**: Array of strings - each can use inline markdown if needed  
- **best_use_cases**: Array of strings - each can use inline markdown if needed

**Other fields:**
- **external_documentation_url**: Link to official docs (full URL)

**Be selective but thorough.** Only recommend 0-3 tools, but make each request comprehensive and professional."""

    def _build_evaluation_prompt(
        self,
        knowledge_entries: List[ToolKnowledge],
        tool_ideas: List[ToolIdeaEntry],
        existing_tools: List[Dict[str, Any]],
    ) -> str:
        """Build evaluation prompt."""
        # Format knowledge
        knowledge_text = "\n".join([
            f"- [{e.category}] {e.title}: {e.summary}"
            for e in knowledge_entries[:20]
        ])
        
        # Format ideas with more context
        ideas_text = "\n".join([
            f"- **ID:{e.id}** [{e.priority or 'unset'}] {e.summary}"
            for e in tool_ideas[:15]
        ]) or "No pending tool ideas"
        
        # Format existing tools with names and descriptions for better duplicate detection
        if existing_tools:
            existing_text = "\n".join([
                f"- **{t['name']}** (`{t['slug']}`) [{t['category']}]: {t['description'][:150]}{'...' if len(t['description']) > 150 else ''}"
                for t in existing_tools[:20]
            ])
        else:
            existing_text = "None yet - this would be the first tool!"
        
        return f"""## Knowledge Base Highlights

Recent research findings and tool discoveries:

{knowledge_text}

## User Tool Ideas (Unaddressed)

These are requests from users - high priority if they match your findings:

{ideas_text}

## Existing Tools (Already in System)

**CRITICAL: Do NOT create tools that duplicate or overlap with these existing tools.**
If a tool provides similar functionality, enhances an existing tool, or is a variant of something
already listed, DO NOT recommend it. Look carefully at the descriptions below:

{existing_text}

---

## Analysis Instructions

1. **Check for duplicates FIRST** - Before recommending ANY tool, verify it doesn't overlap with existing tools above
2. **Review the knowledge base** - What promising tools emerged from research?
3. **Match with user needs** - Do any findings address user ideas?
4. **Evaluate fit** - Consider system compatibility and implementation complexity
5. **Draft comprehensive requests** - If a tool is worth adding, write a professional, detailed request

**Duplicate Detection Rules:**
- Same underlying technology = DUPLICATE (e.g., two Ollama-based tools)
- Same primary use case = DUPLICATE (e.g., two "local LLM" tools)
- Subset of existing functionality = DUPLICATE (e.g., "Ollama for docs" when "Ollama LLM" exists)
- If in doubt, DON'T create it

**CRITICAL OUTPUT FORMAT:**
- Return valid JSON only - ensure all strings are properly escaped
- Close ALL code blocks within implementation_notes
- Keep implementation_notes BRIEF (under 500 chars) - detailed docs can be added later
- Limit to MAX 2 recommendations to ensure complete JSON response
- Return an empty recommendations array `[]` if nothing is truly valuable

**Remember:** 
- Quality over quantity - only recommend tools with clear, UNIQUE value
- If nothing NEW is worth adding this round, return an empty recommendations array
- It's better to return 0 recommendations than to create duplicates"""

    async def _create_tools_from_evaluation(
        self,
        db: AsyncSession,
        response: str,
        user_id: Optional[UUID],
    ) -> int:
        """Create tool records from evaluation response."""
        # Log raw response for debugging
        logger.info(f"Raw evaluation first 100 bytes: {response[:100].encode('utf-8')}")
        
        parsed = self._extract_json_from_response(response)
        if not parsed:
            # Log more detail about what went wrong
            has_json_block = "```json" in response
            has_braces = "{" in response or "[" in response
            logger.warning(
                f"No JSON parsed from evaluation response. "
                f"Has ```json: {has_json_block}, Has braces: {has_braces}. "
                f"First 500 chars: {response[:500] if response else 'empty'}"
            )
            return 0
        
        recommendations = parsed.get("recommendations", [])
        logger.info(f"Evaluation returned {len(recommendations)} recommendations")
        
        if not recommendations:
            logger.info("No tool recommendations from evaluation (this is normal if existing tools cover needs)")
            return 0
        
        created = 0
        
        # Get a system user for tool creation if no user provided
        if not user_id:
            result = await db.execute(
                select(User).where(User.role == "admin").limit(1)
            )
            admin = result.scalar_one_or_none()
            if admin:
                user_id = admin.id
            else:
                logger.error("No admin user found for tool creation")
                return 0
        
        # Fetch existing tools for similarity checking
        result = await db.execute(
            select(Tool.name, Tool.slug, Tool.description)
        )
        existing_tools = [dict(row._mapping) for row in result.all()]
        
        for rec in recommendations:
            slug = rec.get("slug", "")
            if not slug:
                continue
            
            # Check if tool already exists by slug
            result = await db.execute(
                select(Tool).where(Tool.slug == slug)
            )
            if result.scalar_one_or_none():
                logger.info(f"Skipping duplicate tool (exact slug match): {slug}")
                continue
            
            # Programmatic similarity check as backstop
            new_name = rec.get("name", slug).lower()
            new_desc = rec.get("description", "").lower()
            
            is_duplicate = False
            for existing in existing_tools:
                existing_name = existing['name'].lower()
                existing_desc = existing['description'].lower()
                existing_slug = existing['slug'].lower()
                
                # Check for name similarity (shared significant words)
                new_words = set(new_name.replace('-', ' ').split())
                existing_words = set(existing_name.replace('-', ' ').split())
                # Remove common filler words
                filler = {'the', 'a', 'an', 'for', 'and', 'or', 'to', 'with', 'of'}
                new_words -= filler
                existing_words -= filler
                
                if new_words and existing_words:
                    overlap = new_words & existing_words
                    # If >50% of words overlap, likely duplicate
                    overlap_ratio = len(overlap) / min(len(new_words), len(existing_words))
                    if overlap_ratio > 0.5 and len(overlap) >= 1:
                        # Check if they share the key technology word
                        tech_words = {'ollama', 'openai', 'claude', 'anthropic', 'llm', 'gpt', 
                                      'whisper', 'stable', 'diffusion', 'midjourney', 'llama'}
                        if overlap & tech_words:
                            logger.warning(
                                f"Skipping likely duplicate: '{rec.get('name')}' similar to '{existing['name']}' "
                                f"(shared tech: {overlap & tech_words})"
                            )
                            is_duplicate = True
                            break
            
            if is_duplicate:
                continue
            
            # Map category
            category_str = rec.get("category", "automation")
            try:
                category = ToolCategory(category_str)
            except ValueError:
                category = ToolCategory.AUTOMATION
            
            # Convert list fields to text (model uses Text, not JSONB for these)
            strengths = rec.get("strengths", [])
            if isinstance(strengths, list):
                strengths = "\n".join(f"- {s}" for s in strengths) if strengths else None
            
            weaknesses = rec.get("weaknesses", [])
            if isinstance(weaknesses, list):
                weaknesses = "\n".join(f"- {w}" for w in weaknesses) if weaknesses else None
            
            best_use_cases = rec.get("best_use_cases", [])
            if isinstance(best_use_cases, list):
                best_use_cases = "\n".join(f"- {u}" for u in best_use_cases) if best_use_cases else None
            
            # cost_details can be string or dict - convert string to dict for JSONB
            cost_details = rec.get("cost_details")
            if isinstance(cost_details, str):
                cost_details = {"details": cost_details}
            
            # Create tool with all available fields
            tool = Tool(
                name=rec.get("name", slug),
                slug=slug,
                category=category,
                description=rec.get("description", ""),
                status=ToolStatus.REQUESTED,
                requester_id=user_id,
                implementation_notes=rec.get("implementation_notes"),
                priority=rec.get("priority"),
                resource_ids=rec.get("resource_ids", []),
                # Detailed fields
                strengths=strengths,
                weaknesses=weaknesses,
                best_use_cases=best_use_cases,
                cost_model=rec.get("cost_model"),
                cost_details=cost_details,
                integration_complexity=rec.get("integration_complexity"),
                external_documentation_url=rec.get("external_documentation_url"),
            )
            db.add(tool)
            created += 1
            
            resource_info = f" with resources: {rec.get('resource_ids')}" if rec.get("resource_ids") else ""
            logger.info(f"Created tool record: {tool.name}{resource_info}")
        
        if created > 0:
            await db.commit()
        
        return created

    # ==========================================================================
    # Tool Discussion Support (like Proposal Writer)
    # ==========================================================================
    
    async def _get_available_resources(self, db: AsyncSession) -> List[Resource]:
        """Fetch all enabled resources from the database."""
        result = await db.execute(
            select(Resource).where(Resource.status == "available").order_by(Resource.name)
        )
        return list(result.scalars().all())
    
    def _format_resources_for_prompt(self, resources: List[Resource]) -> str:
        """Format resources for inclusion in LLM prompt."""
        if not resources:
            return "No resources currently configured."
        
        lines = []
        for r in resources:
            resource_info = f"- **{r.name}** (ID: `{r.id}`)"
            resource_info += f"\n  - Type: {r.resource_type}, Category: {r.category}"
            
            # Add relevant metadata
            if r.resource_metadata:
                if r.resource_type == "gpu" and "memory_mb" in r.resource_metadata:
                    resource_info += f"\n  - VRAM: {r.resource_metadata['memory_mb']}MB"
                elif r.resource_type == "storage" and "path" in r.resource_metadata:
                    resource_info += f"\n  - Path: {r.resource_metadata['path']}"
                elif r.resource_type == "ram" and "total_gb" in r.resource_metadata:
                    resource_info += f"\n  - Total: {r.resource_metadata['total_gb']:.0f}GB"
            
            lines.append(resource_info)
        
        return "\n".join(lines)
    
    def get_system_prompt(
        self, 
        tools: List[Tool], 
        tool_context: Optional[Dict[str, Any]] = None,
        resources: Optional[List[Resource]] = None,
    ) -> str:
        """Build the system prompt for tool discussions."""
        # Standard verbosity: tool scout needs category/type/cost but not full
        # strengths/weaknesses for every tool — those are visible in tool_context
        # when discussing a specific tool.
        tools_section = self.format_tools_for_prompt(
            tools, verbosity="standard", include_call_format=False
        )
        resources_section = self._format_resources_for_prompt(resources or [])
        
        # Get system information
        system_info = SystemInfoService.collect()
        system_section = f"""## Host System Environment

- **OS:** {system_info.os_pretty_name} ({system_info.architecture})
- **CPU:** {system_info.cpu_model} ({system_info.cpu_cores} cores)
- **RAM:** {system_info.ram_total_gb:.0f} GB
- **GPU:** {system_info.gpus[0].name if system_info.gpus else "None (CPU only)"}
- **Docker:** {"Available" if system_info.docker_available else "Not available"}
- **Python:** {system_info.python_version}

## Available Resources

{resources_section}

When creating or updating tools, specify required resources via the `resource_ids` field."""
        
        tool_section = ""
        if tool_context:
            tool_section = self._build_tool_section(tool_context)
        
        # Only include the full implementation guide when actively discussing
        # a tool (tool_context present). Otherwise it wastes ~5K tokens.
        impl_guide = ""
        if tool_context:
            impl_guide = self._get_implementation_guide()
        
        security_preamble = get_security_preamble("<tool_edit>")

        return f"""You are the Tool Scout Agent for Money Agents, an AI-powered system for automated money-making campaigns.

{security_preamble}

{system_section}

{tool_section}

## Your Communication Style

- **Be concise**: Give short, direct answers.
- **Be technical but accessible**: Explain implementation details clearly.
- **Be practical**: Focus on what's needed to get the tool working.
- **Ask clarifying questions**: If implementation details are unclear, ask.

## Your Capabilities

- Discuss tool implementation and integration
- Help plan installation and setup steps
- Troubleshoot integration issues
- Suggest improvements to tool configuration
- **Edit tool fields directly** when asked or needed
- **Assign resource requirements** to tools based on their needs

## Making Tool Edits

When you want to suggest a change to the tool record, use this format:

<tool_edit field="FIELD_NAME">
NEW_VALUE
</tool_edit>

**Available fields:** name, slug, category, description, tags, implementation_notes, blockers, dependencies, estimated_completion_date, usage_instructions, example_code, required_environment_variables, integration_complexity, cost_model, cost_details, resource_ids, strengths, weaknesses, best_use_cases, external_documentation_url, version, priority, status, interface_type, interface_config, input_schema, output_schema, timeout_seconds, available_on_agents, agent_resource_map

**Rules:**
- The user sees a preview and must click "Apply" to confirm
- You can include multiple edits in one response
- Always explain WHY you're suggesting the edit
{impl_guide}
## Available Tools in System
{tools_section}"""

    def _get_implementation_guide(self) -> str:
        """Return the tool implementation guide (REST/CLI/SDK/MCP configs).
        
        Only included in the system prompt when a specific tool is being
        discussed (tool_context is present), saving ~5K tokens otherwise.
        """
        return """
## Custom Tool Implementation Guide

Tools can be configured entirely through database fields — no code changes needed.

**Architecture:** Money Agents runs in Docker. Custom tools run on the **HOST machine**. Use `host.docker.internal` to reach host services from containers.

### REST API (`interface_type: "rest_api"`)

```json
{
  "base_url": "http://host.docker.internal:PORT",
  "endpoint": {"method": "POST", "path": "/api/endpoint", "headers": {"Content-Type": "application/json"}},
  "auth": {"type": "none"},
  "request_mapping": {"prompt": "$.prompt"},
  "response_mapping": {"content": "$.response"}
}
```
Auth types: `none`, `api_key` (needs `env_var`, `header`, `prefix`), `bearer`, `basic`.

### CLI (`interface_type: "cli"`)

```json
{
  "command": "ffmpeg",
  "working_dir": "/tmp/workspace",
  "templates": {"convert": {"args": ["-i", "{{input}}", "-c:v", "libx264", "{{output}}"], "env": {}}}
}
```

### Python SDK (`interface_type: "python_sdk"`)

```json
{
  "module": "openai",
  "class": "OpenAI",
  "init_args": {"api_key": "$OPENAI_API_KEY"},
  "method": "chat.completions.create"
}
```
`$VAR` in init_args resolves from environment. For function-based: use `"function"` instead of `"class"+"method"`.

### MCP (`interface_type: "mcp"`)

```json
{
  "transport": "stdio",
  "server_command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path"],
  "tool_name": "read_file"
}
```
Also supports `"transport": "http"` with `"server_url"`.

### Distributed Execution

- `available_on_agents`: `null` (local only), `["*"]` (all agents), `["hostname1"]` (specific)
- `agent_resource_map`: per-agent resource requirements, e.g., `{"gpu-server-01": ["gpu-0"]}`

### Implementation Steps

1. Set `interface_type` to the appropriate value
2. Build and set `interface_config` with proper structure  
3. Optionally set `resource_ids`, `available_on_agents`, `agent_resource_map`
4. Set `timeout_seconds` if needed (default: 30)
5. Change `status` to `implemented`
"""

    def _build_tool_section(self, tool_context: Dict[str, Any]) -> str:
        """Build the tool context section for the prompt.
        
        Truncates long text fields to prevent context window bloat.
        """
        def _truncate(text: str, max_len: int = 500) -> str:
            """Truncate text with ellipsis indicator."""
            if not text or len(text) <= max_len:
                return text
            return text[:max_len] + f"... [{len(text) - max_len} chars truncated]"
        
        # Format resource_ids if present
        resource_ids = tool_context.get('resource_ids', [])
        resource_str = ', '.join(resource_ids) if resource_ids else 'None assigned'
        
        # Format environment variables if present
        env_vars = tool_context.get('required_environment_variables', {})
        if env_vars:
            env_str = '\n'.join([f"  - `{k}`: {v}" for k, v in env_vars.items()])
        else:
            env_str = 'None specified'
        
        # Format tags
        tags = tool_context.get('tags', [])
        tags_str = ', '.join(tags) if tags else 'None'
        
        # Format dependencies
        deps = tool_context.get('dependencies', [])
        deps_str = ', '.join(deps) if deps else 'None'
        
        # Format interface_config if present
        interface_config = tool_context.get('interface_config')
        if interface_config:
            import json
            interface_config_str = json.dumps(interface_config, indent=2)
        else:
            interface_config_str = 'Not configured'
        
        # Format distributed execution fields
        available_on_agents = tool_context.get('available_on_agents')
        if available_on_agents is None:
            agents_str = 'Local only (not distributed)'
        elif len(available_on_agents) == 0:
            agents_str = 'Disabled (explicitly blocked)'
        elif '*' in available_on_agents:
            agents_str = 'All connected agents'
        else:
            agents_str = ', '.join(available_on_agents)
        
        agent_resource_map = tool_context.get('agent_resource_map')
        if agent_resource_map:
            import json
            resource_map_str = json.dumps(agent_resource_map, indent=2)
        else:
            resource_map_str = 'None (no per-agent resource requirements)'
        
        return f"""## Current Tool Under Discussion

**Basic Information:**
- **ID:** `{tool_context.get('id', 'N/A')}`
- **Name:** {tool_context.get('name', 'Unknown')}
- **Slug:** `{tool_context.get('slug', 'unknown')}`
- **Status:** {tool_context.get('status', 'unknown')}
- **Category:** {tool_context.get('category', 'unknown')}
- **Priority:** {tool_context.get('priority', 'unset')}
- **Tags:** {tags_str}
- **Integration Complexity:** {tool_context.get('integration_complexity', 'Not set')}

**Description:**
{tool_context.get('description', 'No description')}

**Dynamic Execution Interface (KEY FOR IMPLEMENTATION):**
- **Interface Type:** {tool_context.get('interface_type', 'Not set')}
- **Timeout:** {tool_context.get('timeout_seconds', 30)} seconds
- **Interface Config:**
```json
{interface_config_str}
```

**Dependencies (other tools in catalog):**
{deps_str}

**Resource Requirements:**
- Resource IDs: {resource_str}

**Distributed Execution:**
- **Available On Agents:** {agents_str}
- **Per-Agent Resource Map:**
```json
{resource_map_str}
```

**Required Environment Variables:**
{env_str}

**Cost Information:**
- Cost Model: {tool_context.get('cost_model', 'Not specified')}
- Cost Details: {tool_context.get('cost_details', 'Not specified')}

**Implementation Notes:**
{_truncate(tool_context.get('implementation_notes', 'None yet'), 800)}

**Blockers:**
{tool_context.get('blockers', 'None')}

**Usage Instructions:**
{_truncate(tool_context.get('usage_instructions', 'Not documented yet'), 800)}

**Example Code:**
```
{_truncate(tool_context.get('example_code', 'No example code yet'), 500)}
```

**Strengths:** {_truncate(tool_context.get('strengths', 'Not documented'), 300)}
**Weaknesses:** {_truncate(tool_context.get('weaknesses', 'Not documented'), 300)}
**Best Use Cases:** {_truncate(tool_context.get('best_use_cases', 'Not documented'), 300)}
**External Documentation:** {tool_context.get('external_documentation_url', 'None')}
**Version:** {tool_context.get('version', 'Not set')}
"""

    async def chat(
        self,
        context: AgentContext,
        user_message: str,
        conversation_history: Optional[List[LLMMessage]] = None,
        tool_context: Optional[Dict[str, Any]] = None,
        model_override: Optional[str] = None,
    ) -> AsyncIterator[StreamChunk]:
        """
        Stream a chat response for tool discussions.
        Similar to Proposal Writer's chat functionality.
        
        Args:
            context: Agent context
            user_message: The user's message
            conversation_history: Optional prior messages
            tool_context: Tool being discussed
            model_override: Optional model/tier override (e.g., "fast", "claude:quality")
        """
        db = context.db
        tools = []
        resources = []
        if db:
            result = await db.execute(
                select(Tool).where(Tool.status == ToolStatus.IMPLEMENTED)
            )
            tools = list(result.scalars().all())
            
            # Fetch available resources
            resources = await self._get_available_resources(db)
        
        system_prompt = self.get_system_prompt(tools, tool_context, resources)
        
        messages = [LLMMessage(role="system", content=system_prompt)]
        
        if conversation_history:
            messages.extend(conversation_history)
        
        messages.append(LLMMessage(role="user", content=user_message))
        
        # Use override if provided, otherwise default to reasoning tier
        model = model_override or "reasoning"
        
        async for chunk in llm_service.generate_stream(
            messages=messages,
            model=model,
            temperature=self.default_temperature,
            max_tokens=self.default_max_tokens,
        ):
            yield chunk

    # ==========================================================================
    # Maintenance
    # ==========================================================================
    
    async def run_maintenance(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """Run maintenance on knowledge base and tool ideas."""
        db = context.db
        if not db:
            return AgentResult(success=False, message="Database session required")
        
        knowledge_service = ToolKnowledgeService(db)
        tool_idea_service = ToolIdeaService(db)
        
        knowledge_maintenance = await knowledge_service.run_maintenance()
        idea_maintenance = await tool_idea_service.run_maintenance()
        
        return AgentResult(
            success=True,
            message="Maintenance complete",
            data={
                "knowledge": knowledge_maintenance,
                "ideas": idea_maintenance,
            },
        )

    # ==========================================================================
    # Main Execute Method (Required by BaseAgent)
    # ==========================================================================
    
    async def execute(self, context: AgentContext, **kwargs) -> AgentResult:
        """
        Execute the Tool Scout's main task cycle.
        
        This is the entry point when the agent is triggered by the scheduler.
        It runs through all phases:
        1. Process tool ideas from the queue
        2. Discover new tools via web search
        3. Evaluate discoveries for potential tool creation
        4. Run maintenance on knowledge resources
        
        Args:
            context: Agent execution context with database session
            **kwargs: Optional overrides:
                - skip_ideas: Skip idea processing phase
                - skip_discovery: Skip discovery phase
                - skip_evaluation: Skip evaluation phase
                - skip_maintenance: Skip maintenance phase
                - discovery_focus: Specific focus for discovery
        
        Returns:
            AgentResult with combined results from all phases
        """
        db = context.db
        if not db:
            return AgentResult(
                success=False,
                message="Database session required",
            )
        
        results = {
            "phases_completed": [],
            "ideas_processed": 0,
            "discoveries": 0,
            "tools_created": 0,
            "maintenance": {},
        }
        total_tokens = 0
        
        # Phase 1: Process tool ideas
        if not kwargs.get("skip_ideas", False):
            try:
                logger.info("Tool Scout: Phase 1 - Processing tool ideas")
                idea_result = await self.process_tool_ideas(context)
                if idea_result.success:
                    results["phases_completed"].append("ideas")
                    results["ideas_processed"] = idea_result.data.get("processed", 0) if idea_result.data else 0
                    total_tokens += idea_result.tokens_used
            except Exception as e:
                logger.error(f"Error in idea processing phase: {e}")
        
        # Phase 2: Discover new tools
        if not kwargs.get("skip_discovery", False):
            try:
                logger.info("Tool Scout: Phase 2 - Discovering tools")
                discovery_focus = kwargs.get("discovery_focus")
                discover_result = await self.discover_tools(context, search_focus=discovery_focus)
                if discover_result.success:
                    results["phases_completed"].append("discovery")
                    results["discoveries"] = discover_result.data.get("new_entries", 0) if discover_result.data else 0
                    total_tokens += discover_result.tokens_used
            except Exception as e:
                logger.error(f"Error in discovery phase: {e}")
        
        # Phase 3: Evaluate for tool creation
        if not kwargs.get("skip_evaluation", False):
            try:
                logger.info("Tool Scout: Phase 3 - Evaluating for tool creation")
                eval_result = await self.evaluate_for_tool_creation(context)
                if eval_result.success:
                    results["phases_completed"].append("evaluation")
                    results["tools_created"] = eval_result.data.get("tools_created", 0) if eval_result.data else 0
                    total_tokens += eval_result.tokens_used
            except Exception as e:
                logger.error(f"Error in evaluation phase: {e}")
        
        # Phase 4: Maintenance
        if not kwargs.get("skip_maintenance", False):
            try:
                logger.info("Tool Scout: Phase 4 - Running maintenance")
                maint_result = await self.run_maintenance(context)
                if maint_result.success:
                    results["phases_completed"].append("maintenance")
                    results["maintenance"] = maint_result.data or {}
            except Exception as e:
                logger.error(f"Error in maintenance phase: {e}")
        
        success = len(results["phases_completed"]) > 0
        message = f"Tool Scout completed {len(results['phases_completed'])} phases: {', '.join(results['phases_completed'])}"
        
        return AgentResult(
            success=success,
            message=message,
            data=results,
            tokens_used=total_tokens,
        )

    # ==========================================================================
    # Helpers
    # ==========================================================================
    
    def _extract_json_from_response(self, content: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from LLM response, handling common formatting issues."""
        import re
        
        try:
            # Try to import json5 for lenient parsing (handles trailing commas, etc.)
            try:
                import json5
                use_json5 = True
            except ImportError:
                logger.warning("json5 not available, using standard json parser")
                use_json5 = False
            
            # Try to find JSON in code blocks
            if "```json" in content:
                block_start = content.find("```json")
                json_start = block_start + 7
                # Find the closing ``` that ends the JSON code block
                # This is tricky because the JSON may contain ``` inside markdown strings
                # Look for ``` that appears on a new line after the JSON opening brace
                json_end = -1
                
                # Strategy: Find the LAST ``` in the content that appears after JSON closes
                # First, try to find ``` that appears on its own line (not inside a string)
                search_pos = json_start
                brace_depth = 0
                in_string = False
                escape_next = False
                found_first_brace = False
                
                for i, char in enumerate(content[json_start:], json_start):
                    if escape_next:
                        escape_next = False
                        continue
                    if char == '\\' and in_string:
                        escape_next = True
                        continue
                    if char == '"' and not escape_next:
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if char == '{':
                        brace_depth += 1
                        found_first_brace = True
                    elif char == '}':
                        brace_depth -= 1
                        if brace_depth == 0 and found_first_brace:
                            # Found the end of the JSON object
                            # Now look for ``` after this position
                            remaining = content[i+1:]
                            close_tick = remaining.find("```")
                            if close_tick != -1:
                                json_end = i + 1 + close_tick
                            else:
                                json_end = len(content)
                            break
                
                if json_end == -1:
                    # Fallback: use end of content
                    json_end = len(content)
                    logger.info(f"Could not find JSON end, using end of content: {json_end}")
                    
                logger.info(f"Found ```json block: start={json_start}, end={json_end}, content_len={len(content)}")
                if json_end > json_start:
                    # Skip any "json" word and newlines after ```json
                    extracted = content[json_start:json_end]
                    logger.info(f"Extracted {len(extracted)} chars from code block")
                    # Remove leading "json" if present (in case of ```json\njson format)
                    if extracted.lstrip().startswith('json'):
                        extracted = extracted.lstrip()[4:]
                    content = extracted.strip()
                    logger.info(f"After strip ({len(content)} chars): {repr(content[:80])}")
            elif "```" in content:
                json_start = content.find("```") + 3
                json_end = content.find("```", json_start)
                if json_end > json_start:
                    content = content[json_start:json_end].strip()
            
            # Handle case where response starts with key without outer braces
            # e.g., "recommendations": [...] instead of {"recommendations": [...]}
            content = content.strip()
            if content.startswith('"') and ':' in content[:50]:
                # Likely missing outer braces - wrap it
                logger.info(f"Wrapping JSON that's missing outer braces. Content starts with: {repr(content[:30])}")
                content = '{' + content + '}'
            else:
                logger.debug(f"Content after strip starts with: {repr(content[:30])}")
            
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start == -1 or json_end == 0:
                # Try to find array
                array_start = content.find('[')
                array_end = content.rfind(']') + 1
                if array_start != -1 and array_end > 0:
                    # Wrap array in object with "recommendations" key
                    json_str = '{"recommendations": ' + content[array_start:array_end] + '}'
                else:
                    logger.warning("No JSON braces found in response")
                    return None
            else:
                json_str = content[json_start:json_end]
            
            # Try json5 first if available (more lenient)
            if use_json5:
                try:
                    result = json5.loads(json_str)
                    logger.info(f"json5 successfully parsed response with {len(json_str)} chars")
                    return result
                except Exception as e:
                    logger.warning(f"json5 failed to parse: {e}, trying standard json")
            
            # Clean up common JSON issues
            # Remove trailing commas before ] or }
            json_str = re.sub(r',(\s*[\]}])', r'\1', json_str)
            # Remove control characters
            json_str = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', json_str)
            
            try:
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                # Try to fix unescaped newlines in strings
                json_str = re.sub(r'(?<!\\)\n', r'\\n', json_str)
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    logger.error(f"Error extracting JSON: {e}")
                    return None
                
        except Exception as e:
            logger.error(f"Error extracting JSON: {e}")
            return None

    # ==========================================================================
    # Learning Phase - Evolve Discovery Strategies
    # ==========================================================================

    async def reflect_and_learn(
        self,
        context: AgentContext,
    ) -> AgentResult:
        """
        Reflect on recent tool discoveries and evolve search strategies.
        
        Similar to Opportunity Scout's learning phase, this analyzes:
        - Which strategies are finding useful tools
        - Which search queries are producing results
        - How to improve underperforming strategies
        """
        db = context.db
        if not db:
            return AgentResult(success=False, message="Database session required")
        
        # Get strategies with performance stats
        strategies = await self._get_active_strategies(db)
        if not strategies:
            return AgentResult(
                success=True,
                message="No strategies to analyze",
                data={"strategies_evolved": 0},
            )
        
        strategies_text = "\n".join([
            f"- **{s.name}** (focus: {s.focus_area})\n"
            f"  Executed: {int(s.times_executed or 0)}x, Knowledge entries: {int(s.knowledge_entries_found or 0)}, "
            f"Tools proposed: {int(s.tools_proposed or 0)}, Approved: {int(s.tools_approved or 0)}\n"
            f"  Current queries: {s.search_queries}\n"
            f"  Effectiveness: {s.effectiveness_score or 'Not measured'}"
            for s in strategies
        ])
        
        # Get recent knowledge entries to understand what's being found
        knowledge_service = ToolKnowledgeService(db)
        recent_entries = await knowledge_service.get_recent_entries(limit=20)
        entries_text = "\n".join([
            f"- [{e.category}] {e.title}: {e.summary[:100]}..."
            for e in recent_entries
        ]) if recent_entries else "No recent entries"
        
        reflection_prompt = f"""Analyze Tool Scout's discovery performance and evolve the strategies.

## Current Strategies & Performance
{strategies_text}

## Recent Knowledge Entries Found
{entries_text}

## Your Task
1. Identify which strategies are performing well vs poorly
2. Analyze what types of tools/knowledge are most valuable
3. **Evolve search queries** for underperforming strategies

Respond in this exact JSON format:
```json
{{
  "analysis": "Brief assessment of overall performance",
  "strategy_evolutions": [
    {{
      "strategy_name": "Exact name of strategy to update",
      "new_queries": ["query 1", "query 2", "query 3", "query 4"],
      "reason": "Why these queries will work better",
      "change_type": "refine|expand|pivot"
    }}
  ]
}}
```

Guidelines:
- Only include strategies that need updating
- Queries should be specific and actionable (e.g., "best ai coding tools 2025" not just "ai tools")
- Consider current trends, new releases, and emerging tech
- change_type: refine=small tweaks, expand=add angles, pivot=major direction change"""

        messages = [
            LLMMessage(role="user", content=reflection_prompt),
        ]
        
        response = await llm_service.generate(
            messages=messages,
            model="quality",
            temperature=0.7,
            max_tokens=6000,
        )
        
        strategies_evolved = 0
        
        try:
            parsed = self._extract_json_from_response(response.content)
            if parsed and "strategy_evolutions" in parsed:
                strategies_evolved = await self._apply_strategy_evolutions(
                    db,
                    parsed["strategy_evolutions"],
                    strategies
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Failed to parse learning response: {e}")
        
        return AgentResult(
            success=True,
            message=f"Learning complete: {strategies_evolved} strategies evolved",
            data={
                "strategies_evolved": strategies_evolved,
                "reflection": response.content,
            },
            tokens_used=response.total_tokens,
            cost_usd=response.cost_usd or 0,
        )

    async def _get_active_strategies(self, db: AsyncSession) -> List["ToolDiscoveryStrategy"]:
        """Get all active tool discovery strategies."""
        from app.models.tool_scout import ToolDiscoveryStrategy, ToolStrategyStatus
        
        query = select(ToolDiscoveryStrategy).where(
            ToolDiscoveryStrategy.status == ToolStrategyStatus.ACTIVE.value
        ).order_by(ToolDiscoveryStrategy.effectiveness_score.desc().nullslast())
        result = await db.execute(query)
        return list(result.scalars().all())

    async def _apply_strategy_evolutions(
        self,
        db: AsyncSession,
        evolutions: List[Dict[str, Any]],
        strategies: List["ToolDiscoveryStrategy"],
    ) -> int:
        """Apply strategy evolution recommendations."""
        if not evolutions:
            return 0
        
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
                for name, strat in strategy_map.items():
                    if strategy_name in name or name in strategy_name:
                        strategy = strat
                        break
            
            if not strategy:
                logger.warning(f"Strategy evolution: could not find '{evolution.get('strategy_name')}'")
                continue
            
            if not new_queries or len(new_queries) < 2:
                logger.warning(f"Strategy evolution: insufficient queries for '{strategy.name}'")
                continue
            
            old_queries = strategy.search_queries or []
            strategy.search_queries = new_queries
            
            logger.info(
                f"Tool Strategy evolved: '{strategy.name}' ({change_type})\n"
                f"  Old: {old_queries}\n"
                f"  New: {new_queries}\n"
                f"  Reason: {reason}"
            )
            
            updated_count += 1
        
        return updated_count
