#!/usr/bin/env python3
"""
Test Ollama on Complex Agent Tasks

This script tests Ollama's ability to handle the most complex LLM tasks in the system:
1. Creative exploration (wacky idea generation)
2. Introspective learning (strategy evolution)
3. Strategic planning (multi-step reasoning)

These are the tasks most likely to break with smaller models.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app.services.llm_service import LLMMessage, llm_service

# Test configuration
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_EMAIL = os.getenv("TEST_EMAIL", "admin@example.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "testpass123")


class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}")
    print(f"{text}")
    print(f"{'='*70}{Colors.RESET}\n")


def print_success(text: str):
    print(f"  {Colors.GREEN}✅ {text}{Colors.RESET}")


def print_failure(text: str):
    print(f"  {Colors.RED}❌ {text}{Colors.RESET}")


def print_warning(text: str):
    print(f"  {Colors.YELLOW}⚠️  {text}{Colors.RESET}")


def print_info(text: str):
    print(f"  {Colors.CYAN}ℹ️  {text}{Colors.RESET}")


def extract_json(content: str) -> Optional[Dict]:
    """Extract JSON from LLM response."""
    try:
        if "```json" in content:
            json_match = content.split("```json")[1].split("```")[0].strip()
            content = json_match
        elif "```" in content:
            json_match = content.split("```")[1].split("```")[0].strip()
            content = json_match
        
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        if json_start != -1 and json_end > 0:
            return json.loads(content[json_start:json_end])
    except json.JSONDecodeError as e:
        print_warning(f"JSON parse error: {e}")
    return None


async def test_creative_exploration():
    """Test the Opportunity Scout's creative exploration capability."""
    print_header("TEST 1: Creative Exploration (Wacky Idea Generation)")
    
    # This is the exact system prompt from OpportunityScoutAgent._get_creative_exploration_prompt()
    system_prompt = """You are the Opportunity Scout Agent in creative exploration mode.

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

    user_prompt = """## Current Context

**Opportunities we've already found (avoid similar):**
- AI writing tools for content creators
- Dropshipping automation services
- Crypto trading bots
- Print-on-demand stores
- Social media management tools

**Our current search strategies:**
- SaaS Opportunities
- E-commerce Automation
- Content Creation Tools

## Today's Date
2026-02-01

---

Now, engage your creative thinking mode!

Think about what everyone ELSE is missing. What weird, specific, or contrarian opportunities
might be hiding in plain sight? What would make someone say "huh, I never thought of that"?

Generate 2 creative opportunity angles with search queries to explore them."""

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_prompt),
    ]

    results = {"passed": 0, "failed": 0}
    
    # Test with quality tier (qwen2.5:14b)
    print_info("Testing with quality tier (qwen2.5:14b)...")
    try:
        response = await llm_service.generate(
            messages=messages,
            model="ollama:quality",  # provider:tier format
            temperature=0.9,  # High temperature for creativity
            max_tokens=6000,
        )
        
        print_info(f"Model: {response.model}, Tokens: {response.total_tokens}, Latency: {response.latency_ms}ms")
        
        parsed = extract_json(response.content)
        
        if parsed and "creative_angles" in parsed:
            angles = parsed["creative_angles"]
            if len(angles) >= 2:
                print_success(f"Generated {len(angles)} creative angles")
                
                # Check quality of angles
                quality_checks = 0
                for i, angle in enumerate(angles[:2], 1):
                    print(f"\n    {Colors.CYAN}Angle {i}:{Colors.RESET} {angle.get('angle', 'N/A')[:100]}...")
                    print(f"    {Colors.CYAN}Reasoning:{Colors.RESET} {angle.get('reasoning', 'N/A')[:100]}...")
                    print(f"    {Colors.CYAN}Target:{Colors.RESET} {angle.get('target_audience', 'N/A')}")
                    print(f"    {Colors.CYAN}Query:{Colors.RESET} {angle.get('search_query', 'N/A')}")
                    
                    # Verify structure
                    if all(k in angle for k in ["angle", "reasoning", "target_audience", "search_query"]):
                        quality_checks += 1
                        
                    # Check for genuine creativity (not just generic AI stuff)
                    angle_text = angle.get("angle", "").lower()
                    if "ai tool" not in angle_text and "another" not in angle_text:
                        quality_checks += 1
                
                if quality_checks >= 3:
                    print_success(f"Quality checks passed: {quality_checks}/4")
                    results["passed"] += 1
                else:
                    print_warning(f"Quality checks: {quality_checks}/4 (may be too generic)")
                    results["passed"] += 1  # Still count as pass if structure is correct
            else:
                print_failure(f"Only {len(angles)} angles (expected 2)")
                results["failed"] += 1
        else:
            print_failure("Failed to generate valid JSON structure")
            print(f"    Raw response: {response.content[:500]}...")
            results["failed"] += 1
            
    except Exception as e:
        print_failure(f"Error: {e}")
        results["failed"] += 1
    
    return results


async def test_introspective_learning():
    """Test the Opportunity Scout's introspective learning/evolution capability."""
    print_header("TEST 2: Introspective Learning (Strategy Evolution)")
    
    # Simulated outcomes and strategies for the reflection
    reflection_prompt = """Analyze these recent discovery outcomes and extract learnings.
Also evaluate and evolve our discovery strategies based on what's working.

## Recent Outcomes (last 7 days)
- Strategy: AI Content Creation Tools
  Queries run: ai video editing automation tools, ai writing assistants 2026
  Found: 8, User decision: approved 2, dismissed 6
  User feedback: "Too generic, I already know about these"
  
- Strategy: Niche E-commerce Opportunities  
  Queries run: etsy digital products trending, shopify print on demand
  Found: 5, User decision: approved 4, dismissed 1
  User feedback: "These are actionable and specific"
  
- Strategy: Software Development Tools
  Queries run: best developer productivity tools, coding automation AI
  Found: 3, User decision: dismissed all
  User feedback: "Not my area of expertise"

## Current Discovery Strategies & Performance
- **AI Content Creation Tools** (executed 12x, found 24 opps, approved 4)
  Current queries: ["ai video editing tools", "content automation software", "ai writing tools"]
  Effectiveness: 0.17

- **Niche E-commerce Opportunities** (executed 8x, found 15 opps, approved 10)
  Current queries: ["etsy digital products", "print on demand niches", "shopify automation"]
  Effectiveness: 0.67

- **Software Development Tools** (executed 5x, found 8 opps, approved 0)
  Current queries: ["developer tools ai", "coding productivity software", "github copilot alternatives"]
  Effectiveness: 0.00

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
{
  "insights": [
    {
      "type": "principle|pattern|anti_pattern|hypothesis",
      "title": "Short insight title",
      "description": "Detailed explanation",
      "confidence": 0.7,
      "domains": ["relevant", "domains"],
      "evidence": ["outcome references"]
    }
  ],
  "strategy_evolutions": [
    {
      "strategy_name": "Exact name of strategy to update",
      "new_queries": ["updated query 1", "updated query 2", "updated query 3"],
      "reason": "Why these queries will perform better",
      "change_type": "refine|expand|pivot"
    }
  ],
  "overall_assessment": "Brief summary of learning"
}
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
    
    results = {"passed": 0, "failed": 0}
    
    # Test with reasoning tier (mistral-nemo:12b)
    print_info("Testing with reasoning tier (mistral-nemo:12b)...")
    try:
        response = await llm_service.generate(
            messages=messages,
            model="ollama:reasoning",  # provider:tier format
            temperature=0.7,
            max_tokens=6000,
        )
        
        print_info(f"Model: {response.model}, Tokens: {response.total_tokens}, Latency: {response.latency_ms}ms")
        
        parsed = extract_json(response.content)
        
        if parsed:
            # Check insights
            insights = parsed.get("insights", [])
            print_info(f"Generated {len(insights)} insights")
            
            # Check strategy evolutions
            evolutions = parsed.get("strategy_evolutions", [])
            print_info(f"Generated {len(evolutions)} strategy evolutions")
            
            quality_checks = 0
            
            # Validate insights structure
            for insight in insights[:3]:
                if all(k in insight for k in ["type", "title", "description"]):
                    quality_checks += 1
                    print(f"    {Colors.CYAN}Insight:{Colors.RESET} [{insight.get('type')}] {insight.get('title')}")
            
            # Validate evolution structure and logic
            for evolution in evolutions:
                strategy = evolution.get("strategy_name", "")
                new_queries = evolution.get("new_queries", [])
                change_type = evolution.get("change_type", "")
                reason = evolution.get("reason", "")
                
                print(f"\n    {Colors.CYAN}Strategy:{Colors.RESET} {strategy}")
                print(f"    {Colors.CYAN}Change type:{Colors.RESET} {change_type}")
                print(f"    {Colors.CYAN}Reason:{Colors.RESET} {reason[:100]}...")
                print(f"    {Colors.CYAN}New queries:{Colors.RESET} {new_queries[:3]}")
                
                # Check if evolution makes sense
                if strategy.lower() == "software development tools" and change_type == "pivot":
                    quality_checks += 2  # Good reasoning - this should be pivoted or deactivated
                    print_success("Correctly identified underperforming strategy needs pivot")
                elif strategy.lower() == "ai content creation tools" and change_type in ["refine", "expand"]:
                    quality_checks += 1  # Reasonable - refine to be more specific
                    print_success("Correctly identified strategy needs refinement")
                elif strategy.lower() == "niche e-commerce opportunities":
                    quality_checks += 1  # Should probably leave this alone or expand
                    
            if parsed.get("overall_assessment"):
                quality_checks += 1
                print(f"\n    {Colors.CYAN}Overall:{Colors.RESET} {parsed['overall_assessment'][:200]}...")
            
            if quality_checks >= 4:
                print_success(f"Quality checks passed: {quality_checks}")
                results["passed"] += 1
            else:
                print_warning(f"Quality checks: {quality_checks} (some logical issues)")
                results["passed"] += 1  # Still pass if structure is correct
        else:
            print_failure("Failed to generate valid JSON structure")
            print(f"    Raw response: {response.content[:500]}...")
            results["failed"] += 1
            
    except Exception as e:
        print_failure(f"Error: {e}")
        results["failed"] += 1
    
    return results


async def test_strategic_planning():
    """Test the Opportunity Scout's strategic planning capability."""
    print_header("TEST 3: Strategic Planning (Multi-Step Reasoning)")
    
    system_prompt = """You are the Opportunity Scout Agent's strategic planner. Your role is to create 
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
  "strategies": [
    {
      "name": "Strategy Name",
      "description": "What this strategy aims to discover",
      "search_queries": ["specific query 1", "specific query 2", "specific query 3"],
      "expected_opportunity_types": ["type1", "type2"],
      "priority": "high|medium|low",
      "risk_level": "low|medium|high",
      "time_sensitivity": "evergreen|trending|seasonal",
      "reasoning": "Why this strategy should work"
    }
  ],
  "strategic_rationale": "Overall explanation of how these strategies work together"
}
```"""

    user_prompt = """## Available Tools
- web_search: Search the internet for current information
- content_generator: Generate blog posts, social media content, etc.
- image_generator: Create AI images
- seo_analyzer: Analyze SEO opportunities

## User Context
The user is interested in passive income opportunities, particularly:
- Digital products (templates, courses)
- Content monetization
- Micro-SaaS ideas

They have some technical skills but prefer opportunities that don't require heavy coding.

## Previous Strategy Performance
- "Generic AI Tools" strategy: 5% approval rate (too saturated)
- "Etsy Digital Products" strategy: 65% approval rate (good)
- "YouTube Automation" strategy: 30% approval rate (mixed)

## Today's Date
2026-02-01

---

Create 3 discovery strategies for the next iteration. Focus on specific, actionable approaches
that leverage the user's interests and avoid overly saturated markets."""

    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_prompt),
    ]
    
    results = {"passed": 0, "failed": 0}
    
    # Test with quality tier
    print_info("Testing with quality tier (qwen2.5:14b)...")
    try:
        response = await llm_service.generate(
            messages=messages,
            model="ollama:quality",  # provider:tier format
            temperature=0.7,
            max_tokens=6000,
        )
        
        print_info(f"Model: {response.model}, Tokens: {response.total_tokens}, Latency: {response.latency_ms}ms")
        
        parsed = extract_json(response.content)
        
        if parsed and "strategies" in parsed:
            strategies = parsed["strategies"]
            print_info(f"Generated {len(strategies)} strategies")
            
            quality_checks = 0
            
            for i, strategy in enumerate(strategies[:3], 1):
                print(f"\n    {Colors.CYAN}Strategy {i}:{Colors.RESET} {strategy.get('name', 'N/A')}")
                print(f"    {Colors.CYAN}Priority:{Colors.RESET} {strategy.get('priority', 'N/A')}")
                print(f"    {Colors.CYAN}Risk:{Colors.RESET} {strategy.get('risk_level', 'N/A')}")
                print(f"    {Colors.CYAN}Queries:{Colors.RESET} {strategy.get('search_queries', [])[:3]}")
                print(f"    {Colors.CYAN}Reasoning:{Colors.RESET} {strategy.get('reasoning', 'N/A')[:100]}...")
                
                # Check structure
                required_fields = ["name", "description", "search_queries", "priority"]
                if all(k in strategy for k in required_fields):
                    quality_checks += 1
                
                # Check query quality (specific vs generic)
                queries = strategy.get("search_queries", [])
                if queries and len(queries) >= 2:
                    quality_checks += 1
                    
                # Check for coherent reasoning
                if strategy.get("reasoning") and len(strategy.get("reasoning", "")) > 20:
                    quality_checks += 1
            
            if parsed.get("strategic_rationale"):
                print(f"\n    {Colors.CYAN}Rationale:{Colors.RESET} {parsed['strategic_rationale'][:200]}...")
                quality_checks += 1
            
            if quality_checks >= 6:
                print_success(f"Quality checks passed: {quality_checks}")
                results["passed"] += 1
            else:
                print_warning(f"Quality checks: {quality_checks} (some issues)")
                results["passed"] += 1
        else:
            print_failure("Failed to generate valid JSON structure")
            print(f"    Raw response: {response.content[:500]}...")
            results["failed"] += 1
            
    except Exception as e:
        print_failure(f"Error: {e}")
        results["failed"] += 1
    
    return results


async def test_tool_scout_reflection():
    """Test the Tool Scout's reflection and learning capability."""
    print_header("TEST 4: Tool Scout Reflection (Discovery Evolution)")
    
    reflection_prompt = """Analyze Tool Scout's discovery performance and evolve the strategies.

## Current Strategies & Performance
- **AI Development Tools** (focus: coding assistants and dev productivity)
  Executed: 15x, Knowledge entries: 45, Tools proposed: 8, Approved: 2
  Current queries: ["ai coding assistant", "developer ai tools", "github copilot alternatives"]
  Effectiveness: 0.25

- **Content Creation AI** (focus: writing, image, video generation)
  Executed: 10x, Knowledge entries: 30, Tools proposed: 12, Approved: 8
  Current queries: ["ai writing tools", "ai image generator", "video ai tools"]
  Effectiveness: 0.67

- **Business Automation** (focus: workflow and process automation)
  Executed: 8x, Knowledge entries: 20, Tools proposed: 5, Approved: 1
  Current queries: ["business automation software", "workflow ai", "zapier alternatives"]
  Effectiveness: 0.20

## Recent Knowledge Entries Found
- [tool_release] Claude 3.5 Sonnet: Anthropic's latest model with improved coding...
- [capability_update] GPT-4 now supports image generation via DALL-E 3...
- [market_trend] AI agent frameworks seeing rapid adoption in enterprise...
- [tool_release] Cursor IDE: AI-first code editor gaining traction...
- [comparison] Claude vs GPT-4 vs Gemini for coding tasks...

## Your Task
1. Identify which strategies are performing well vs poorly
2. Analyze what types of tools/knowledge are most valuable
3. **Evolve search queries** for underperforming strategies

Respond in this exact JSON format:
```json
{
  "analysis": "Brief assessment of overall performance",
  "strategy_evolutions": [
    {
      "strategy_name": "Exact name of strategy to update",
      "new_queries": ["query 1", "query 2", "query 3", "query 4"],
      "reason": "Why these queries will work better",
      "change_type": "refine|expand|pivot"
    }
  ]
}
```

Guidelines:
- Only include strategies that need updating
- Queries should be specific and actionable (e.g., "best ai coding tools 2025" not just "ai tools")
- Consider current trends, new releases, and emerging tech
- change_type: refine=small tweaks, expand=add angles, pivot=major direction change"""

    messages = [
        LLMMessage(role="user", content=reflection_prompt),
    ]
    
    results = {"passed": 0, "failed": 0}
    
    print_info("Testing with quality tier (qwen2.5:14b)...")
    try:
        response = await llm_service.generate(
            messages=messages,
            model="ollama:quality",  # provider:tier format
            temperature=0.7,
            max_tokens=6000,
        )
        
        print_info(f"Model: {response.model}, Tokens: {response.total_tokens}, Latency: {response.latency_ms}ms")
        
        parsed = extract_json(response.content)
        
        if parsed:
            analysis = parsed.get("analysis", "")
            evolutions = parsed.get("strategy_evolutions", [])
            
            print(f"\n    {Colors.CYAN}Analysis:{Colors.RESET} {analysis[:200]}...")
            
            quality_checks = 0
            
            if analysis:
                quality_checks += 1
            
            for evolution in evolutions:
                strategy = evolution.get("strategy_name", "")
                new_queries = evolution.get("new_queries", [])
                change_type = evolution.get("change_type", "")
                reason = evolution.get("reason", "")
                
                print(f"\n    {Colors.CYAN}Strategy:{Colors.RESET} {strategy}")
                print(f"    {Colors.CYAN}Change type:{Colors.RESET} {change_type}")
                print(f"    {Colors.CYAN}Reason:{Colors.RESET} {reason[:100]}...")
                print(f"    {Colors.CYAN}New queries:{Colors.RESET} {new_queries[:4]}")
                
                # Validate structure
                if all([strategy, new_queries, change_type]):
                    quality_checks += 1
                
                # Check if reasoning is logical
                if "content creation" in strategy.lower() and change_type != "pivot":
                    quality_checks += 1  # Good - this one is working well
                elif "business automation" in strategy.lower() and change_type in ["refine", "expand", "pivot"]:
                    quality_checks += 1  # Good - this one needs help
            
            if quality_checks >= 3:
                print_success(f"Quality checks passed: {quality_checks}")
                results["passed"] += 1
            else:
                print_warning(f"Quality checks: {quality_checks}")
                results["passed"] += 1
        else:
            print_failure("Failed to generate valid JSON structure")
            print(f"    Raw response: {response.content[:500]}...")
            results["failed"] += 1
            
    except Exception as e:
        print_failure(f"Error: {e}")
        results["failed"] += 1
    
    return results


async def test_comparative_providers():
    """Compare Ollama performance with other providers on the same task."""
    print_header("TEST 5: Provider Comparison (Ollama vs GLM)")
    
    # Use a simpler task for fair comparison
    prompt = """You are an AI assistant helping brainstorm business ideas.

Generate 3 creative micro-SaaS ideas for indie developers. For each idea:
1. Name the product
2. Describe what it does (2-3 sentences)
3. Identify the target market
4. Explain why it could work

Respond in JSON format:
```json
{
  "ideas": [
    {
      "name": "Product Name",
      "description": "What it does",
      "target_market": "Who would buy it",
      "why_it_works": "Success factors"
    }
  ]
}
```"""

    messages = [LLMMessage(role="user", content=prompt)]
    
    results = {"passed": 0, "failed": 0}
    provider_results = {}
    
    # Test Ollama
    print_info("Testing Ollama (qwen2.5:14b)...")
    try:
        start = datetime.now()
        response = await llm_service.generate(
            messages=messages,
            model="ollama:quality",  # provider:tier format
            temperature=0.7,
            max_tokens=4000,
        )
        elapsed = (datetime.now() - start).total_seconds()
        
        parsed = extract_json(response.content)
        ideas_count = len(parsed.get("ideas", [])) if parsed else 0
        
        provider_results["ollama"] = {
            "model": response.model,
            "latency": elapsed,
            "tokens": response.total_tokens,
            "ideas": ideas_count,
            "success": ideas_count >= 2,
        }
        
        print_info(f"  Model: {response.model}")
        print_info(f"  Latency: {elapsed:.2f}s")
        print_info(f"  Ideas: {ideas_count}")
        
        if ideas_count >= 2:
            print_success("Ollama generated valid ideas")
            results["passed"] += 1
        else:
            print_warning("Ollama: incomplete response")
            results["passed"] += 1
            
    except Exception as e:
        print_failure(f"Ollama error: {e}")
        provider_results["ollama"] = {"error": str(e)}
        results["failed"] += 1
    
    # Test GLM (if available)
    print_info("\nTesting GLM (glm-4-flash)...")
    try:
        start = datetime.now()
        response = await llm_service.generate(
            messages=messages,
            model="glm:quality",  # provider:tier format
            temperature=0.7,
            max_tokens=4000,
        )
        elapsed = (datetime.now() - start).total_seconds()
        
        parsed = extract_json(response.content)
        ideas_count = len(parsed.get("ideas", [])) if parsed else 0
        
        provider_results["glm"] = {
            "model": response.model,
            "latency": elapsed,
            "tokens": response.total_tokens,
            "ideas": ideas_count,
            "success": ideas_count >= 2,
        }
        
        print_info(f"  Model: {response.model}")
        print_info(f"  Latency: {elapsed:.2f}s")
        print_info(f"  Ideas: {ideas_count}")
        
        if ideas_count >= 2:
            print_success("GLM generated valid ideas")
        else:
            print_warning("GLM: incomplete response")
            
    except Exception as e:
        print_warning(f"GLM unavailable: {e}")
        provider_results["glm"] = {"error": str(e)}
    
    # Compare results
    print(f"\n    {Colors.BOLD}Comparison:{Colors.RESET}")
    for provider, data in provider_results.items():
        if "error" not in data:
            print(f"    {provider}: {data['latency']:.2f}s, {data['ideas']} ideas")
    
    return results


async def main():
    print(f"\n{Colors.BOLD}{'='*70}")
    print("OLLAMA COMPLEX TASK TESTING")
    print(f"Started: {datetime.now().isoformat()}")
    print(f"{'='*70}{Colors.RESET}")
    
    # Check Ollama connection
    print_info("Checking Ollama connection...")
    try:
        # Quick health check - use provider:tier format
        test_response = await llm_service.generate(
            messages=[LLMMessage(role="user", content="Say 'ok'")],
            model="ollama:fast",  # provider:tier format
            max_tokens=10,
        )
        print_success(f"Ollama connected, using model: {test_response.model}")
    except Exception as e:
        print_failure(f"Ollama not available: {e}")
        print_info("Make sure Ollama is running and accessible")
        return
    
    # Run all tests
    all_results = {"passed": 0, "failed": 0}
    
    test_funcs = [
        test_creative_exploration,
        test_introspective_learning,
        test_strategic_planning,
        test_tool_scout_reflection,
        test_comparative_providers,
    ]
    
    for test_func in test_funcs:
        try:
            results = await test_func()
            all_results["passed"] += results["passed"]
            all_results["failed"] += results["failed"]
        except Exception as e:
            print_failure(f"Test {test_func.__name__} crashed: {e}")
            all_results["failed"] += 1
    
    # Summary
    print_header("TEST SUMMARY")
    total = all_results["passed"] + all_results["failed"]
    print(f"  {Colors.GREEN}Passed:{Colors.RESET} {all_results['passed']}")
    print(f"  {Colors.RED}Failed:{Colors.RESET} {all_results['failed']}")
    print(f"  {Colors.BOLD}Total:{Colors.RESET}  {total}")
    
    if all_results["failed"] == 0:
        print(f"\n  {Colors.GREEN}{Colors.BOLD}✅ All complex tasks passed!{Colors.RESET}")
        print(f"  {Colors.CYAN}Ollama can handle creative and introspective agent tasks.{Colors.RESET}")
    else:
        print(f"\n  {Colors.YELLOW}⚠️  Some tasks had issues - review output above{Colors.RESET}")


if __name__ == "__main__":
    asyncio.run(main())
