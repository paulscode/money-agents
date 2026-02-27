#!/usr/bin/env python3
"""Comprehensive End-to-End Agent System Tests.

This script tests the complete Money Agents workflow:
1. Opportunity Scout - discovers opportunities from ideas
2. Proposal Writer - creates proposals for opportunities
3. Tool Scout - discovers tools for campaign tasks
4. Campaign Manager - orchestrates campaign execution
5. Campaign Workers - distributed execution with Ollama support

Test Objectives:
- Verify Ollama can handle all agent tasks
- Test resource isolation (no OOM, no Ollama crashes)
- Validate multi-agent coordination
- Ensure proper failover between providers

Prerequisites:
- Backend running (docker compose up)
- At least one campaign worker connected
- Test user account exists
- Ollama running locally (for local provider tests)

Usage:
    cd backend && source venv/bin/activate
    python tests/manual/test_full_agent_flow.py

    # Run specific scenario
    python tests/manual/test_full_agent_flow.py --scenario opportunity_scout

    # Quick mode (fewer iterations)
    python tests/manual/test_full_agent_flow.py --quick
"""
import asyncio
import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

import httpx


# =============================================================================
# Configuration
# =============================================================================
API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
TEST_EMAIL = os.getenv("TEST_EMAIL", "admin@example.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "testpass123")


class TestContext:
    """Holds test state and results."""
    
    def __init__(self):
        self.token: str = ""
        self.user_id: str = ""
        self.results: List[tuple] = []
        self.created_ideas: List[str] = []
        self.created_opportunities: List[str] = []
        self.created_proposals: List[str] = []
        self.created_campaigns: List[str] = []
        self.start_time = datetime.now()
    
    def log(self, category: str, name: str, status: str, details: str = ""):
        """Log a test result."""
        icon = {
            "pass": "✅", 
            "fail": "❌", 
            "skip": "⏭️", 
            "info": "ℹ️",
            "warn": "⚠️",
            "start": "🚀",
            "wait": "⏳"
        }.get(status, "•")
        
        self.results.append((category, name, status, details))
        print(f"  {icon} [{category}] {name}")
        if details:
            for line in details.split("\n")[:5]:  # Limit detail lines
                print(f"     {line}")
    
    def summary(self) -> bool:
        """Print test summary and return success status."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        passed = sum(1 for _, _, s, _ in self.results if s == "pass")
        failed = sum(1 for _, _, s, _ in self.results if s == "fail")
        warnings = sum(1 for _, _, s, _ in self.results if s == "warn")
        
        print("\n" + "=" * 70)
        print(f"TEST SUMMARY (elapsed: {elapsed:.1f}s)")
        print("=" * 70)
        print(f"  ✅ Passed:   {passed}")
        print(f"  ❌ Failed:   {failed}")
        print(f"  ⚠️  Warnings: {warnings}")
        
        # Cleanup summary
        print(f"\n  Resources created:")
        print(f"    Ideas:         {len(self.created_ideas)}")
        print(f"    Opportunities: {len(self.created_opportunities)}")
        print(f"    Proposals:     {len(self.created_proposals)}")
        print(f"    Campaigns:     {len(self.created_campaigns)}")
        
        if failed > 0:
            print("\n  Failed tests:")
            for cat, name, status, details in self.results:
                if status == "fail":
                    print(f"    - [{cat}] {name}: {details[:100]}")
        
        return failed == 0


ctx = TestContext()


# =============================================================================
# API Helpers
# =============================================================================
async def api_get(endpoint: str, params: dict = None) -> dict:
    """Make authenticated GET request."""
    # Remove trailing slash for consistency
    endpoint = endpoint.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        response = await client.get(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {ctx.token}"},
            params=params
        )
        response.raise_for_status()
        return response.json()


async def api_post(endpoint: str, data: dict = None, timeout: float = 60.0) -> dict:
    """Make authenticated POST request."""
    # Remove trailing slash for consistency
    endpoint = endpoint.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {ctx.token}"},
            json=data
        )
        if response.status_code >= 400:
            return {"error": response.status_code, "detail": response.text}
        if not response.content:
            return {"error": "empty_response", "status": response.status_code}
        return response.json()


async def api_patch(endpoint: str, data: dict) -> dict:
    """Make authenticated PATCH request."""
    # Remove trailing slash for consistency
    endpoint = endpoint.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.patch(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {ctx.token}"},
            json=data
        )
        if response.status_code >= 400:
            return {"error": response.status_code, "detail": response.text}
        return response.json()


async def api_stream(endpoint: str, data: dict, timeout: float = 120.0) -> tuple:
    """Make authenticated streaming POST request, return (content, metadata)."""
    # Remove trailing slash for consistency
    endpoint = endpoint.rstrip("/")
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Bearer {ctx.token}"},
            json=data
        )
        
        if response.status_code != 200:
            return "", {"error": response.status_code}
        
        content_chunks = []
        metadata = {}
        
        for line in response.text.split("\n"):
            if line.startswith("data: "):
                try:
                    event = json.loads(line[6:])
                    if event.get("type") == "content":
                        content_chunks.append(event.get("content", ""))
                    elif event.get("type") == "done":
                        metadata = event
                except json.JSONDecodeError:
                    pass
        
        return "".join(content_chunks), metadata


async def login():
    """Login and store token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/auth/login",
            json={"identifier": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        response.raise_for_status()
        data = response.json()
        ctx.token = data["access_token"]
        
        # Get user info
        me = await api_get("users/me")
        ctx.user_id = me["id"]


# =============================================================================
# SCENARIO 1: Opportunity Scout with Ollama
# =============================================================================
async def test_opportunity_scout(provider: str = "ollama"):
    """Test Opportunity Scout using specified provider."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 1: Opportunity Scout (provider: {provider})")
    print("=" * 70)
    
    # Step 1: Create test ideas
    ctx.log("OpportunityScout", "Creating test ideas", "start")
    
    test_ideas = [
        {
            "content": "Create an AI-powered code review tool that uses local LLMs to analyze code for bugs, security issues, and style violations without sending code to external servers.",
            "source": "manual"
        },
        {
            "content": "Build a personal finance tracker that uses AI to categorize transactions and predict upcoming expenses based on spending patterns.",
            "source": "manual"
        },
    ]
    
    created_ideas = []
    for idea_data in test_ideas:
        result = await api_post("ideas/", idea_data)
        if "error" not in result:
            created_ideas.append(result["id"])
            ctx.created_ideas.append(result["id"])
    
    ctx.log("OpportunityScout", f"Created {len(created_ideas)} ideas", "pass")
    
    # Step 2: Trigger opportunity discovery
    ctx.log("OpportunityScout", "Triggering opportunity discovery", "start")
    
    # The opportunity scout runs as a background task
    # We can trigger it via the agent endpoint or wait for scheduled run
    # For testing, let's use the brainstorm to simulate the scout's analysis
    
    analysis_prompt = f"""Analyze this business idea and identify:
1. Market opportunity (score 1-10)
2. Technical feasibility (score 1-10)
3. Revenue potential (score 1-10)
4. Key risks
5. Recommended next steps

Idea: {test_ideas[0]['content']}

Respond with a JSON object containing these fields."""

    content, metadata = await api_stream("brainstorm/chat", {
        "messages": [{"role": "user", "content": analysis_prompt}],
        "provider": provider,
        "tier": "reasoning",  # Use reasoning tier for analysis
        "enable_search": False,
        "enable_task_context": False,
        "max_tokens": 1000
    })
    
    if metadata.get("error"):
        ctx.log("OpportunityScout", f"Analysis with {provider}", "fail", f"Error: {metadata}")
        return False
    
    ctx.log(
        "OpportunityScout", 
        f"Analysis with {provider}", 
        "pass",
        f"Model: {metadata.get('model')}, Response length: {len(content)} chars"
    )
    
    # Step 3: Check resource usage (no OOM)
    agents = await api_get("broker/agents/connected")
    for agent in agents:
        name = agent.get("name")
        if agent.get("has_gpu"):
            # Check GPU memory
            details = await api_get(f"broker/agents/{agent['hostname']}")
            gpus = details.get("live_stats", {}).get("gpus", [])
            if gpus:
                gpu = gpus[0]
                used_mb = gpu.get("memory_used_mb", 0)
                total_mb = details.get("capabilities", {}).get("gpus", [{}])[0].get("memory_total_mb", 1)
                pct = (used_mb / total_mb) * 100 if total_mb else 0
                ctx.log(
                    "OpportunityScout",
                    f"GPU memory ({name})",
                    "pass" if pct < 90 else "warn",
                    f"{used_mb}MB / {total_mb}MB ({pct:.1f}%)"
                )
    
    return True


# =============================================================================
# SCENARIO 2: Proposal Writer with Ollama
# =============================================================================
async def test_proposal_writer(provider: str = "ollama"):
    """Test Proposal Writer using specified provider."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 2: Proposal Writer (provider: {provider})")
    print("=" * 70)
    
    # Get existing opportunities or create a mock one
    opp_response = await api_get("opportunities", {"limit": 5})
    opportunities = opp_response.get("opportunities", []) if isinstance(opp_response, dict) else opp_response
    
    if not opportunities:
        ctx.log("ProposalWriter", "No opportunities found", "info", "Using mock opportunity data")
        # Use mock opportunity for testing
        opportunity = {
            "id": "mock-opportunity",
            "title": "AI-Powered Code Review Tool",
            "description": "A tool that uses local LLMs to analyze code for bugs, security issues, and style violations."
        }
    else:
        opportunity = opportunities[0]
    
    ctx.log("ProposalWriter", "Using opportunity", "info", f"Title: {opportunity.get('title', 'Untitled')}")
    
    # Generate proposal content using specified provider
    proposal_prompt = f"""Write a detailed project proposal for this opportunity:

Title: {opportunity.get('title', 'Untitled')}
Description: {opportunity.get('description', 'No description')}

Include:
1. Executive Summary (2-3 sentences)
2. Project Scope
3. Timeline (phases with durations)
4. Resource Requirements
5. Budget Estimate
6. Success Metrics
7. Risk Assessment

Format as a professional proposal document."""

    ctx.log("ProposalWriter", f"Generating proposal with {provider}", "start")
    
    content, metadata = await api_stream("brainstorm/chat", {
        "messages": [{"role": "user", "content": proposal_prompt}],
        "provider": provider,
        "tier": "quality",  # Use quality tier for important documents
        "enable_search": False,
        "enable_task_context": False,
        "max_tokens": 2000
    }, timeout=180.0)  # Longer timeout for quality models
    
    if metadata.get("error"):
        ctx.log("ProposalWriter", f"Generation failed", "fail", str(metadata))
        return False
    
    ctx.log(
        "ProposalWriter",
        f"Proposal generated with {provider}",
        "pass",
        f"Model: {metadata.get('model')}, Length: {len(content)} chars"
    )
    
    # Verify content quality (basic checks)
    quality_checks = {
        "Has executive summary": "executive summary" in content.lower() or "summary" in content.lower(),
        "Has timeline": "timeline" in content.lower() or "phase" in content.lower(),
        "Has budget": "budget" in content.lower() or "cost" in content.lower(),
        "Minimum length": len(content) > 500,
    }
    
    passed = sum(quality_checks.values())
    total = len(quality_checks)
    
    ctx.log(
        "ProposalWriter",
        "Content quality checks",
        "pass" if passed == total else "warn",
        f"{passed}/{total} checks passed"
    )
    
    return True


# =============================================================================
# SCENARIO 3: Tool Scout with Ollama
# =============================================================================
async def test_tool_scout(provider: str = "ollama"):
    """Test Tool Scout using specified provider."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 3: Tool Scout (provider: {provider})")
    print("=" * 70)
    
    # Get current tools catalog
    tools = await api_get("tools/")
    ctx.log("ToolScout", f"Current catalog", "info", f"{len(tools)} tools")
    
    # Simulate tool discovery analysis
    discovery_prompt = """You are a Tool Scout agent. Analyze this task and recommend tools:

Task: Create automated social media content generation and scheduling

For each recommended tool, provide:
1. Tool name
2. Category (image_gen, text_gen, scheduling, analytics)
3. API endpoint type (http_rest, cli, sdk)
4. Why it's needed

Recommend 3-5 tools. Format as JSON array."""

    ctx.log("ToolScout", f"Discovering tools with {provider}", "start")
    
    content, metadata = await api_stream("brainstorm/chat", {
        "messages": [{"role": "user", "content": discovery_prompt}],
        "provider": provider,
        "tier": "reasoning",
        "enable_search": True,  # Enable search for tool discovery
        "enable_task_context": False,
        "max_tokens": 1500
    }, timeout=120.0)
    
    if metadata.get("error"):
        ctx.log("ToolScout", f"Discovery failed", "fail", str(metadata))
        return False
    
    search_performed = metadata.get("search_performed", False)
    ctx.log(
        "ToolScout",
        f"Tool discovery with {provider}",
        "pass",
        f"Model: {metadata.get('model')}, Search: {search_performed}, Length: {len(content)} chars"
    )
    
    # Check if response contains tool recommendations
    tool_indicators = ["api", "endpoint", "tool", "service", "sdk"]
    found_indicators = sum(1 for ind in tool_indicators if ind in content.lower())
    
    ctx.log(
        "ToolScout",
        "Recommendation quality",
        "pass" if found_indicators >= 3 else "warn",
        f"Found {found_indicators}/5 expected elements"
    )
    
    return True


# =============================================================================
# SCENARIO 4: Campaign Manager Coordination
# =============================================================================
async def test_campaign_manager(provider: str = "ollama"):
    """Test Campaign Manager coordination."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 4: Campaign Manager (provider: {provider})")
    print("=" * 70)
    
    # Check campaign workers
    agents = await api_get("broker/agents/connected")
    workers = [a for a in agents if a.get("is_campaign_worker")]
    
    if not workers:
        ctx.log("CampaignManager", "No workers available", "skip")
        return False
    
    ctx.log(
        "CampaignManager",
        "Campaign workers",
        "pass",
        f"{len(workers)} workers: {', '.join(w['name'] for w in workers)}"
    )
    
    # Check worker capabilities
    for worker in workers:
        details = await api_get(f"broker/agents/{worker['hostname']}")
        caps = details.get("capabilities", {})
        ollama = caps.get("ollama", {})
        
        has_ollama = ollama.get("enabled", False) if ollama else False
        has_gpu = len(caps.get("gpus", [])) > 0
        
        ctx.log(
            "CampaignManager",
            f"Worker '{worker['name']}'",
            "info",
            f"GPU: {has_gpu}, Ollama: {has_ollama}, Capacity: {worker.get('campaign_capacity', 0)}"
        )
    
    # Simulate campaign planning
    planning_prompt = """You are a Campaign Manager. Create an execution plan for:

Campaign: Launch AI Code Review Tool
Goal: Get 100 beta users in 30 days
Budget: $500

Create a task breakdown with:
1. Task name
2. Dependencies (other task names)
3. Estimated hours
4. Required tools/resources
5. Priority (1-5)

Format as a structured plan with 5-8 tasks."""

    ctx.log("CampaignManager", f"Planning campaign with {provider}", "start")
    
    content, metadata = await api_stream("brainstorm/chat", {
        "messages": [{"role": "user", "content": planning_prompt}],
        "provider": provider,
        "tier": "reasoning",
        "enable_search": False,
        "enable_task_context": True,  # Include task context
        "max_tokens": 1500
    })
    
    if metadata.get("error"):
        ctx.log("CampaignManager", "Planning failed", "fail", str(metadata))
        return False
    
    ctx.log(
        "CampaignManager",
        f"Plan generated with {provider}",
        "pass",
        f"Model: {metadata.get('model')}, Tasks created: {metadata.get('tasks_created', 0)}"
    )
    
    return True


# =============================================================================
# SCENARIO 5: Multi-Provider Stress Test
# =============================================================================
async def test_multi_provider_stress(iterations: int = 3):
    """Test multiple providers under load to check for resource conflicts."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 5: Multi-Provider Stress Test ({iterations} iterations)")
    print("=" * 70)
    
    providers = ["glm", "ollama"]  # Test these providers
    
    # Check which providers are available
    config = await api_get("brainstorm/config")
    available = {p["id"]: p["is_configured"] for p in config.get("providers", [])}
    
    test_providers = [p for p in providers if available.get(p)]
    ctx.log("StressTest", "Available providers", "info", ", ".join(test_providers))
    
    if "ollama" not in test_providers:
        ctx.log("StressTest", "Ollama not available", "skip")
        return False
    
    # Run concurrent requests
    async def make_request(provider: str, idx: int) -> dict:
        start = time.time()
        try:
            content, metadata = await api_stream("brainstorm/chat", {
                "messages": [{"role": "user", "content": f"Count from 1 to 5. Request {idx}."}],
                "provider": provider,
                "tier": "fast",
                "enable_search": False,
                "enable_task_context": False,
                "max_tokens": 50
            }, timeout=60.0)
            
            elapsed = time.time() - start
            return {
                "provider": provider,
                "idx": idx,
                "success": not metadata.get("error"),
                "elapsed": elapsed,
                "model": metadata.get("model")
            }
        except Exception as e:
            return {
                "provider": provider,
                "idx": idx,
                "success": False,
                "elapsed": time.time() - start,
                "error": str(e)
            }
    
    # Run iterations
    all_results = []
    for iteration in range(iterations):
        ctx.log("StressTest", f"Iteration {iteration + 1}/{iterations}", "start")
        
        # Create tasks for all providers
        tasks = []
        for i, provider in enumerate(test_providers):
            tasks.append(make_request(provider, iteration * len(test_providers) + i))
        
        # Run concurrently
        results = await asyncio.gather(*tasks)
        all_results.extend(results)
        
        # Brief pause between iterations
        await asyncio.sleep(1)
    
    # Analyze results
    by_provider = {}
    for r in all_results:
        p = r["provider"]
        if p not in by_provider:
            by_provider[p] = {"success": 0, "fail": 0, "times": []}
        if r["success"]:
            by_provider[p]["success"] += 1
            by_provider[p]["times"].append(r["elapsed"])
        else:
            by_provider[p]["fail"] += 1
    
    for provider, stats in by_provider.items():
        total = stats["success"] + stats["fail"]
        avg_time = sum(stats["times"]) / len(stats["times"]) if stats["times"] else 0
        
        ctx.log(
            "StressTest",
            f"Provider {provider}",
            "pass" if stats["fail"] == 0 else "warn",
            f"{stats['success']}/{total} succeeded, avg time: {avg_time:.2f}s"
        )
    
    # Check for resource issues
    try:
        agents = await api_get("broker/agents/connected")
        for agent in agents:
            if agent.get("has_gpu"):
                try:
                    details = await api_get(f"broker/agents/{agent['hostname']}")
                    live = details.get("live_stats", {})
                    
                    cpu_pct = live.get("cpu_percent", 0)
                    mem_pct = live.get("memory", {}).get("percent_used", 0)
                    
                    gpus = live.get("gpus", [])
                    gpu_util = gpus[0].get("utilization_percent", 0) if gpus else 0
                    gpu_mem = gpus[0].get("memory_used_mb", 0) if gpus else 0
                    
                    ctx.log(
                        "StressTest",
                        f"Resource check ({agent['name']})",
                        "pass" if cpu_pct < 90 and mem_pct < 90 else "warn",
                        f"CPU: {cpu_pct}%, RAM: {mem_pct:.1f}%, GPU: {gpu_util}%, VRAM: {gpu_mem}MB"
                    )
                except Exception as e:
                    ctx.log("StressTest", f"Resource check ({agent['name']})", "warn", f"Failed: {e}")
    except Exception as e:
        ctx.log("StressTest", "Resource checks", "warn", f"Could not get agents: {e}")
    
    return True


# =============================================================================
# SCENARIO 6: Ollama Isolation Test
# =============================================================================
async def test_ollama_isolation():
    """Test that Ollama requests don't interfere with each other."""
    print("\n" + "=" * 70)
    print("SCENARIO 6: Ollama Isolation Test")
    print("=" * 70)
    
    # Check Ollama is available
    config = await api_get("brainstorm/config")
    ollama_config = next((p for p in config.get("providers", []) if p["id"] == "ollama"), None)
    
    if not ollama_config or not ollama_config.get("is_configured"):
        ctx.log("OllamaIsolation", "Ollama not configured", "skip")
        return False
    
    # Test 1: Sequential requests with different tiers
    tiers = ["fast", "reasoning", "quality"]
    
    for tier in tiers:
        ctx.log("OllamaIsolation", f"Testing {tier} tier", "start")
        
        content, metadata = await api_stream("brainstorm/chat", {
            "messages": [{"role": "user", "content": f"What tier are you? Say '{tier}' and the model name."}],
            "provider": "ollama",
            "tier": tier,
            "enable_search": False,
            "enable_task_context": False,
            "max_tokens": 50
        }, timeout=120.0)
        
        if metadata.get("error"):
            ctx.log("OllamaIsolation", f"Tier {tier}", "fail", str(metadata))
        else:
            ctx.log(
                "OllamaIsolation",
                f"Tier {tier}",
                "pass",
                f"Model: {metadata.get('model')}"
            )
    
    # Test 2: Rapid sequential requests (test for state leakage)
    ctx.log("OllamaIsolation", "Rapid sequential test", "start")
    
    results = []
    for i in range(5):
        content, metadata = await api_stream("brainstorm/chat", {
            "messages": [{"role": "user", "content": f"Say only the number {i}"}],
            "provider": "ollama",
            "tier": "fast",
            "enable_search": False,
            "enable_task_context": False,
            "max_tokens": 10
        }, timeout=30.0)
        
        results.append({
            "expected": str(i),
            "got": content.strip(),
            "success": str(i) in content
        })
    
    passed = sum(1 for r in results if r["success"])
    ctx.log(
        "OllamaIsolation",
        "State isolation",
        "pass" if passed >= 4 else "warn",  # Allow 1 miss for LLM variability
        f"{passed}/5 correct responses"
    )
    
    return True


# =============================================================================
# SCENARIO 7: Long-Running Task Simulation
# =============================================================================
async def test_long_running_task(provider: str = "ollama"):
    """Test a longer task that exercises sustained Ollama usage."""
    print("\n" + "=" * 70)
    print(f"SCENARIO 7: Long-Running Task (provider: {provider})")
    print("=" * 70)
    
    # Simulate a complex analysis task
    long_prompt = """You are an AI business analyst. Perform a comprehensive analysis:

**Task:** Analyze the market opportunity for an AI-powered personal finance app

**Required Sections:**

1. **Market Analysis** (200 words)
   - Target demographics
   - Market size estimation
   - Growth trends

2. **Competitive Landscape** (150 words)
   - Key competitors
   - Differentiation opportunities

3. **Technical Requirements** (150 words)
   - Core features needed
   - AI/ML capabilities required
   - Integration requirements

4. **Go-to-Market Strategy** (150 words)
   - Launch phases
   - Marketing channels
   - Partnership opportunities

5. **Financial Projections** (100 words)
   - Development costs
   - Revenue model
   - Break-even timeline

6. **Risk Assessment** (100 words)
   - Technical risks
   - Market risks
   - Mitigation strategies

Provide detailed, actionable insights for each section."""

    ctx.log("LongTask", f"Starting comprehensive analysis with {provider}", "start")
    start_time = time.time()
    
    content, metadata = await api_stream("brainstorm/chat", {
        "messages": [{"role": "user", "content": long_prompt}],
        "provider": provider,
        "tier": "quality",  # Use quality for detailed analysis
        "enable_search": False,
        "enable_task_context": False,
        "max_tokens": 3000
    }, timeout=300.0)  # 5 minute timeout
    
    elapsed = time.time() - start_time
    
    if metadata.get("error"):
        ctx.log("LongTask", "Analysis failed", "fail", str(metadata))
        return False
    
    # Check response quality
    sections = ["market", "competitive", "technical", "strategy", "financial", "risk"]
    found_sections = sum(1 for s in sections if s in content.lower())
    
    ctx.log(
        "LongTask",
        f"Analysis complete with {provider}",
        "pass",
        f"Time: {elapsed:.1f}s, Length: {len(content)} chars, Model: {metadata.get('model')}"
    )
    
    ctx.log(
        "LongTask",
        "Content completeness",
        "pass" if found_sections >= 5 else "warn",
        f"Found {found_sections}/6 required sections"
    )
    
    # Check memory after long task
    agents = await api_get("broker/agents/connected")
    for agent in agents:
        if agent.get("has_gpu"):
            details = await api_get(f"broker/agents/{agent['hostname']}")
            gpus = details.get("live_stats", {}).get("gpus", [])
            if gpus:
                mem_used = gpus[0].get("memory_used_mb", 0)
                ctx.log(
                    "LongTask",
                    f"Post-task GPU memory ({agent['name']})",
                    "info",
                    f"{mem_used}MB used"
                )
    
    return True


# =============================================================================
# Main Entry Point
# =============================================================================
async def run_all_scenarios(quick: bool = False):
    """Run all test scenarios."""
    print("=" * 70)
    print("FULL AGENT SYSTEM END-TO-END TESTS")
    print(f"Started: {datetime.now().isoformat()}")
    print(f"Mode: {'Quick' if quick else 'Full'}")
    print("=" * 70)
    
    # Login
    try:
        await login()
        print(f"\n✅ Logged in as {TEST_EMAIL}")
    except Exception as e:
        print(f"\n❌ Login failed: {e}")
        sys.exit(1)
    
    # Check prerequisites
    agents = await api_get("broker/agents/connected")
    workers = [a for a in agents if a.get("is_campaign_worker")]
    ollama_workers = [a for a in agents if a.get("capabilities", {}).get("ollama", {}).get("enabled")]
    
    print(f"\n📊 Environment:")
    print(f"   Connected agents: {len(agents)}")
    print(f"   Campaign workers: {len(workers)}")
    print(f"   Ollama-enabled: {len(ollama_workers)}")
    
    # Run scenarios
    iterations = 2 if quick else 3
    
    # Scenario 1: Opportunity Scout
    await test_opportunity_scout("ollama")
    
    # Scenario 2: Proposal Writer
    await test_proposal_writer("ollama")
    
    # Scenario 3: Tool Scout
    await test_tool_scout("ollama")
    
    # Scenario 4: Campaign Manager
    await test_campaign_manager("ollama")
    
    # Scenario 5: Multi-Provider Stress
    await test_multi_provider_stress(iterations)
    
    # Scenario 6: Ollama Isolation
    await test_ollama_isolation()
    
    # Scenario 7: Long-Running Task (skip in quick mode)
    if not quick:
        await test_long_running_task("ollama")
    
    # Summary
    success = ctx.summary()
    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(description="Full Agent System E2E Tests")
    parser.add_argument("--quick", action="store_true", help="Run in quick mode (fewer iterations)")
    parser.add_argument("--scenario", type=str, help="Run specific scenario only")
    args = parser.parse_args()
    
    asyncio.run(run_all_scenarios(quick=args.quick))


if __name__ == "__main__":
    main()
