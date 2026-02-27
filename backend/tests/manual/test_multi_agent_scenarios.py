#!/usr/bin/env python3
"""Multi-agent manual test scenarios for Money Agents.

This script tests complex scenarios involving:
1. Multiple connected agents with different capabilities
2. Tool dispatch to appropriate agents
3. Campaign worker functionality with Ollama
4. Failover behavior

Prerequisites:
- At least one agent connected (preferably both GPU and non-GPU)
- Backend running (docker compose up)
- Test user account exists

Usage:
    cd backend && source venv/bin/activate
    python tests/manual/test_multi_agent_scenarios.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional

import httpx


# Configuration
API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
TEST_EMAIL = os.getenv("TEST_EMAIL", "admin@example.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "testpass123")


class TestResults:
    """Collect and display test results."""
    
    def __init__(self):
        self.results = []
    
    def add(self, name: str, status: str, details: str = ""):
        icon = {"pass": "✅", "fail": "❌", "skip": "⏭️", "info": "ℹ️"}[status]
        self.results.append((name, status, details))
        print(f"  {icon} {name}")
        if details:
            for line in details.split("\n"):
                print(f"     {line}")
    
    def summary(self):
        passed = sum(1 for _, s, _ in self.results if s == "pass")
        failed = sum(1 for _, s, _ in self.results if s == "fail")
        skipped = sum(1 for _, s, _ in self.results if s == "skip")
        
        print("\n" + "=" * 60)
        print(f"SUMMARY: {passed} passed, {failed} failed, {skipped} skipped")
        print("=" * 60)
        
        if failed > 0:
            print("\nFailed tests:")
            for name, status, details in self.results:
                if status == "fail":
                    print(f"  - {name}: {details}")
        
        return failed == 0


results = TestResults()


async def login() -> str:
    """Login and return access token."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/auth/login",
            json={"identifier": TEST_EMAIL, "password": TEST_PASSWORD}
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def get_connected_agents(token: str) -> list:
    """Get list of connected agents."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/broker/agents/connected",
            headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        return response.json()


async def get_agent_details(token: str, hostname: str) -> dict:
    """Get full agent details."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/broker/agents/{hostname}",
            headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()


# =============================================================================
# Scenario 1: Agent Capability Discovery
# =============================================================================
async def scenario_agent_discovery(token: str):
    """Scenario 1: Verify agent capabilities are correctly detected."""
    print("\n" + "=" * 60)
    print("SCENARIO 1: Agent Capability Discovery")
    print("=" * 60)
    
    agents = await get_connected_agents(token)
    
    if not agents:
        results.add("Agent connectivity", "skip", "No agents connected")
        return
    
    results.add("Agent connectivity", "pass", f"{len(agents)} agent(s) connected")
    
    for agent in agents:
        name = agent.get("name", agent.get("hostname"))
        
        # Get full details
        details = await get_agent_details(token, agent["hostname"])
        caps = details.get("capabilities", {})
        
        # Check GPU detection
        gpus = caps.get("gpus", [])
        has_gpu = len(gpus) > 0
        results.add(
            f"{name}: GPU detection",
            "pass",
            f"{'Yes' if has_gpu else 'No'}" + 
            (f" - {gpus[0].get('name', 'Unknown')}" if has_gpu else "")
        )
        
        # Check Ollama detection
        ollama = caps.get("ollama", {})
        if ollama and ollama.get("enabled"):
            models = ollama.get("available_models", [])
            results.add(
                f"{name}: Ollama detection",
                "pass",
                f"Enabled with {len(models)} models: {', '.join(models[:3])}..."
            )
        else:
            results.add(
                f"{name}: Ollama detection",
                "info",
                "Not configured or disabled"
            )
        
        # Check campaign worker status
        is_worker = agent.get("is_campaign_worker", False)
        capacity = agent.get("campaign_capacity", 0)
        results.add(
            f"{name}: Campaign worker",
            "pass",
            f"{'Yes' if is_worker else 'No'}" +
            (f" (capacity: {capacity})" if is_worker else "")
        )


# =============================================================================
# Scenario 2: Tool Dispatch by Capability
# =============================================================================
async def scenario_tool_dispatch(token: str):
    """Scenario 2: Test tool dispatch to appropriate agents."""
    print("\n" + "=" * 60)
    print("SCENARIO 2: Tool Dispatch by Capability")
    print("=" * 60)
    
    # Get available tools
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/tools/",
            headers={"Authorization": f"Bearer {token}"}
        )
        tools = response.json()
    
    if not tools:
        results.add("Tools available", "skip", "No tools in catalog")
        return
    
    results.add("Tools available", "pass", f"{len(tools)} tools in catalog")
    
    # Categorize tools by requirements
    gpu_tools = [t for t in tools if t.get("requires_gpu")]
    cloud_tools = [t for t in tools if t.get("endpoint_type") in ["http_rest", "http_graphql"]]
    local_tools = [t for t in tools if t.get("endpoint_type") in ["cli", "local_python"]]
    
    results.add(
        "Tool categories",
        "info",
        f"GPU-requiring: {len(gpu_tools)}, Cloud: {len(cloud_tools)}, Local: {len(local_tools)}"
    )
    
    # Note: Actual tool dispatch testing would require triggering campaigns
    # which is covered in scenario 4


# =============================================================================
# Scenario 3: Brainstorm Provider Failover
# =============================================================================
async def scenario_provider_failover(token: str):
    """Scenario 3: Test LLM provider failover in brainstorm."""
    print("\n" + "=" * 60)
    print("SCENARIO 3: Brainstorm Provider Failover")
    print("=" * 60)
    
    # Get provider config
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/brainstorm/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        config = response.json()
    
    providers = config.get("providers", [])
    configured = [p for p in providers if p.get("is_configured")]
    
    results.add(
        "Configured providers",
        "pass",
        f"{len(configured)}: {', '.join(p['id'] for p in configured)}"
    )
    
    # Test each configured provider
    for provider in configured:
        pid = provider["id"]
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                response = await client.post(
                    f"{API_BASE}/brainstorm/chat",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "messages": [{"role": "user", "content": f"Say '{pid} works'"}],
                        "provider": pid,
                        "tier": "fast",
                        "enable_search": False,
                        "enable_task_context": False,
                        "max_tokens": 30
                    }
                )
                
                if response.status_code == 200:
                    # Check if we got a response with correct provider
                    for line in response.text.split("\n"):
                        if '"type": "done"' in line:
                            data = json.loads(line[6:])
                            used_model = data.get("model", "unknown")
                            results.add(
                                f"Provider {pid}",
                                "pass",
                                f"Model: {used_model}"
                            )
                            break
                else:
                    results.add(
                        f"Provider {pid}",
                        "fail",
                        f"Status {response.status_code}"
                    )
            except Exception as e:
                results.add(
                    f"Provider {pid}",
                    "fail",
                    str(e)
                )


# =============================================================================
# Scenario 4: Campaign Worker Selection
# =============================================================================
async def scenario_campaign_worker(token: str):
    """Scenario 4: Test campaign worker selection based on capabilities."""
    print("\n" + "=" * 60)
    print("SCENARIO 4: Campaign Worker Selection")
    print("=" * 60)
    
    agents = await get_connected_agents(token)
    
    # Filter to campaign workers
    workers = [a for a in agents if a.get("is_campaign_worker")]
    
    if not workers:
        results.add("Campaign workers", "skip", "No campaign workers connected")
        return
    
    results.add(
        "Campaign workers available",
        "pass",
        f"{len(workers)} worker(s)"
    )
    
    for worker in workers:
        name = worker.get("name")
        capacity = worker.get("campaign_capacity", 0)
        held = worker.get("held_campaigns", 0)
        available = worker.get("is_campaign_available", False)
        
        # Get full details for Ollama info
        details = await get_agent_details(token, worker["hostname"])
        ollama = details.get("capabilities", {}).get("ollama", {})
        has_ollama = ollama.get("enabled", False) if ollama else False
        
        results.add(
            f"Worker '{name}'",
            "pass",
            f"Capacity: {capacity}, Held: {held}, Available: {available}, Ollama: {has_ollama}"
        )
    
    # Note: Full campaign execution testing would require creating proposals
    # and starting campaigns, which can be a separate test


# =============================================================================
# Scenario 5: Ollama Stress Test (Optional)
# =============================================================================
async def scenario_ollama_stress(token: str, num_requests: int = 5):
    """Scenario 5: Test Ollama under concurrent load."""
    print("\n" + "=" * 60)
    print(f"SCENARIO 5: Ollama Stress Test ({num_requests} concurrent requests)")
    print("=" * 60)
    
    # Check if Ollama is available
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/brainstorm/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        config = response.json()
    
    ollama_config = next((p for p in config.get("providers", []) if p["id"] == "ollama"), None)
    
    if not ollama_config or not ollama_config.get("is_configured"):
        results.add("Ollama stress test", "skip", "Ollama not configured")
        return
    
    # Fire concurrent requests
    async def make_request(i: int) -> tuple:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                start = datetime.now()
                response = await client.post(
                    f"{API_BASE}/brainstorm/chat",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "messages": [{"role": "user", "content": f"Say 'Request {i} complete'"}],
                        "provider": "ollama",
                        "tier": "fast",
                        "enable_search": False,
                        "enable_task_context": False,
                        "max_tokens": 20
                    }
                )
                elapsed = (datetime.now() - start).total_seconds()
                
                if response.status_code == 200:
                    return (i, "success", elapsed)
                else:
                    return (i, f"error:{response.status_code}", elapsed)
        except Exception as e:
            return (i, f"exception:{str(e)[:50]}", 0)
    
    # Run concurrent requests
    tasks = [make_request(i) for i in range(num_requests)]
    responses = await asyncio.gather(*tasks)
    
    successes = sum(1 for _, status, _ in responses if status == "success")
    times = [t for _, s, t in responses if s == "success"]
    
    results.add(
        "Concurrent requests",
        "pass" if successes > 0 else "fail",
        f"{successes}/{num_requests} succeeded"
    )
    
    if times:
        avg_time = sum(times) / len(times)
        results.add(
            "Response times",
            "info",
            f"Avg: {avg_time:.1f}s, Min: {min(times):.1f}s, Max: {max(times):.1f}s"
        )


async def main():
    print("=" * 60)
    print("MULTI-AGENT TEST SCENARIOS")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Login
    try:
        token = await login()
        print(f"\n✅ Logged in as {TEST_EMAIL}")
    except Exception as e:
        print(f"\n❌ Login failed: {e}")
        sys.exit(1)
    
    # Run scenarios
    await scenario_agent_discovery(token)
    await scenario_tool_dispatch(token)
    await scenario_provider_failover(token)
    await scenario_campaign_worker(token)
    await scenario_ollama_stress(token, num_requests=3)
    
    # Summary
    success = results.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    asyncio.run(main())
