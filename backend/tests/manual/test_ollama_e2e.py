#!/usr/bin/env python3
"""Manual test script for Ollama integration.

This script tests the full Ollama integration across:
1. Local backend OllamaProvider
2. Brainstorm API with Ollama
3. Remote agent Ollama capabilities
4. Provider failover behavior

Usage:
    # Activate backend venv first
    cd backend && source venv/bin/activate
    
    # Run all tests
    python tests/manual/test_ollama_e2e.py
    
    # Run specific test
    python tests/manual/test_ollama_e2e.py --test provider_config
    
    # Skip slow tests
    python tests/manual/test_ollama_e2e.py --skip-slow
"""
import asyncio
import argparse
import json
import os
import sys
from datetime import datetime

import httpx


# Configuration
API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
TEST_EMAIL = os.getenv("TEST_EMAIL", "admin@example.com")
TEST_PASSWORD = os.getenv("TEST_PASSWORD", "testpass123")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


class TestResults:
    """Collect and display test results."""
    
    def __init__(self):
        self.passed = []
        self.failed = []
        self.skipped = []
    
    def add_pass(self, name: str, details: str = ""):
        self.passed.append((name, details))
        print(f"  ✅ {name}")
        if details:
            print(f"     {details}")
    
    def add_fail(self, name: str, error: str):
        self.failed.append((name, error))
        print(f"  ❌ {name}")
        print(f"     Error: {error}")
    
    def add_skip(self, name: str, reason: str):
        self.skipped.append((name, reason))
        print(f"  ⏭️  {name} (skipped: {reason})")
    
    def summary(self):
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)
        print(f"  Passed:  {len(self.passed)}")
        print(f"  Failed:  {len(self.failed)}")
        print(f"  Skipped: {len(self.skipped)}")
        
        if self.failed:
            print("\nFailed tests:")
            for name, error in self.failed:
                print(f"  - {name}: {error}")
        
        return len(self.failed) == 0


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


async def test_ollama_server_health():
    """Test 1: Check if Ollama server is running locally."""
    print("\n📋 Test 1: Ollama Server Health")
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            
            if response.status_code == 200:
                data = response.json()
                models = [m["name"] for m in data.get("models", [])]
                results.add_pass(
                    "Ollama server responding",
                    f"Available models: {', '.join(models[:5])}{'...' if len(models) > 5 else ''}"
                )
                return models
            else:
                results.add_fail("Ollama server check", f"Status {response.status_code}")
                return []
    except Exception as e:
        results.add_fail("Ollama server check", str(e))
        return []


async def test_provider_config(token: str):
    """Test 2: Check brainstorm config includes Ollama."""
    print("\n📋 Test 2: Provider Configuration")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/brainstorm/config",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if response.status_code != 200:
            results.add_fail("Get config", f"Status {response.status_code}")
            return
        
        data = response.json()
        providers = {p["id"]: p for p in data.get("providers", [])}
        
        # Check Ollama is in providers
        if "ollama" in providers:
            ollama = providers["ollama"]
            results.add_pass(
                "Ollama in providers",
                f"Configured: {ollama['is_configured']}, Models: {ollama['models']}"
            )
        else:
            results.add_fail("Ollama in providers", "Ollama not found in provider list")
        
        # Check default provider
        default = data.get("default_provider")
        results.add_pass(
            "Default provider",
            f"Default: {default}"
        )


async def test_brainstorm_with_ollama(token: str, tier: str = "fast"):
    """Test 3: Run brainstorm chat with Ollama."""
    print(f"\n📋 Test 3: Brainstorm with Ollama ({tier} tier)")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{API_BASE}/brainstorm/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [{"role": "user", "content": "Say 'Ollama test successful' and nothing else."}],
                "provider": "ollama",
                "tier": tier,
                "enable_search": False,
                "enable_task_context": False,
                "temperature": 0.1,
                "max_tokens": 50
            }
        )
        
        if response.status_code != 200:
            results.add_fail(f"Brainstorm {tier}", f"Status {response.status_code}: {response.text}")
            return
        
        # Parse SSE response
        content_chunks = []
        done_data = None
        
        for line in response.text.split("\n"):
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if data.get("type") == "content":
                        content_chunks.append(data.get("content", ""))
                    elif data.get("type") == "done":
                        done_data = data
                except json.JSONDecodeError:
                    pass
        
        full_content = "".join(content_chunks)
        
        if done_data:
            results.add_pass(
                f"Brainstorm {tier}",
                f"Model: {done_data.get('model')}, Response: {full_content[:100]}..."
            )
        else:
            results.add_fail(f"Brainstorm {tier}", "No done event received")


async def test_all_ollama_tiers(token: str):
    """Test 3b: Test all Ollama tiers (fast, reasoning, quality)."""
    for tier in ["fast", "reasoning", "quality"]:
        await test_brainstorm_with_ollama(token, tier)


async def test_provider_failover(token: str):
    """Test 4: Test default provider priority."""
    print("\n📋 Test 4: Provider Failover")
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # Test with no provider specified (should use default)
        response = await client.post(
            f"{API_BASE}/brainstorm/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [{"role": "user", "content": "Say OK"}],
                "tier": "fast",
                "enable_search": False,
                "enable_task_context": False,
                "max_tokens": 20
            }
        )
        
        if response.status_code != 200:
            results.add_fail("Default provider", f"Status {response.status_code}")
            return
        
        # Find the done event
        for line in response.text.split("\n"):
            if line.startswith("data: ") and '"type": "done"' in line:
                data = json.loads(line[6:])
                results.add_pass(
                    "Default provider",
                    f"Used: {data.get('provider')} ({data.get('model')})"
                )
                return
        
        results.add_fail("Default provider", "No done event")


async def test_remote_agent_ollama(token: str):
    """Test 5: Check remote agents report Ollama capabilities."""
    print("\n📋 Test 5: Remote Agent Ollama Capabilities")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{API_BASE}/broker/agents",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        if response.status_code != 200:
            results.add_fail("Get agents", f"Status {response.status_code}")
            return
        
        agents = response.json()
        
        if not agents:
            results.add_skip("Remote agent Ollama", "No agents connected")
            return
        
        for agent in agents:
            name = agent.get("name", agent.get("hostname"))
            ollama = agent.get("capabilities", {}).get("ollama")
            
            if ollama:
                results.add_pass(
                    f"Agent '{name}' Ollama",
                    f"Enabled: {ollama.get('enabled')}, Models: {ollama.get('available_models', [])[:3]}..."
                )
            else:
                results.add_pass(
                    f"Agent '{name}' Ollama",
                    "Not configured (expected if not enabled)"
                )


async def test_ollama_pricing():
    """Test 6: Verify Ollama models have free pricing."""
    print("\n📋 Test 6: Ollama Pricing (should be $0)")
    
    try:
        from app.services.usage_service import get_model_pricing
        from app.services.llm_service import calculate_cost
        
        test_models = ["mistral:7b", "qwen2.5:14b", "llama3:8b"]
        
        for model in test_models:
            input_price, output_price = get_model_pricing(model)
            cost = calculate_cost(model, prompt_tokens=1000, completion_tokens=500)
            
            if input_price == 0.0 and output_price == 0.0 and cost == 0.0:
                results.add_pass(f"Pricing {model}", "Free ($0)")
            else:
                results.add_fail(f"Pricing {model}", f"Expected free, got ${cost}")
                
    except ImportError as e:
        results.add_skip("Ollama pricing", f"Import error: {e}")


async def test_concurrent_limiting():
    """Test 7: Test Ollama concurrent request limiting."""
    print("\n📋 Test 7: Concurrent Request Limiting")
    
    try:
        from app.services.llm_service import OllamaProvider, LLMMessage, LLMProviderUnavailable
        
        provider = OllamaProvider(
            base_url=OLLAMA_BASE_URL,
            enabled=True,
            model_tiers={"fast": "mistral:7b"},
            context_lengths={"fast": 8192},
            max_concurrent=1,
        )
        
        # Simulate at max capacity
        provider._current_requests = 1
        
        try:
            await provider.generate(
                "mistral:7b",
                [LLMMessage(role="user", content="test")],
                0.7,
                10
            )
            results.add_fail("Concurrent limit", "Should have raised error")
        except LLMProviderUnavailable as e:
            if "max concurrent" in str(e):
                results.add_pass("Concurrent limit", "Correctly rejects when at capacity")
            else:
                results.add_fail("Concurrent limit", f"Wrong error: {e}")
                
    except ImportError as e:
        results.add_skip("Concurrent limit", f"Import error: {e}")


async def run_all_tests(skip_slow: bool = False):
    """Run all tests."""
    print("=" * 60)
    print("OLLAMA INTEGRATION E2E TESTS")
    print(f"Started: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # Test 1: Ollama server health (no auth needed)
    models = await test_ollama_server_health()
    
    if not models:
        print("\n⚠️  Ollama not available - some tests will be skipped")
    
    # Login for authenticated tests
    try:
        token = await login()
        print(f"\n✅ Logged in as {TEST_EMAIL}")
    except Exception as e:
        print(f"\n❌ Login failed: {e}")
        results.summary()
        return
    
    # Test 2: Provider config
    await test_provider_config(token)
    
    # Test 3: Brainstorm with Ollama
    if models:
        if skip_slow:
            await test_brainstorm_with_ollama(token, "fast")
        else:
            await test_all_ollama_tiers(token)
    else:
        results.add_skip("Brainstorm Ollama", "Ollama not available")
    
    # Test 4: Provider failover
    await test_provider_failover(token)
    
    # Test 5: Remote agent capabilities
    await test_remote_agent_ollama(token)
    
    # Test 6: Pricing
    await test_ollama_pricing()
    
    # Test 7: Concurrent limiting
    if models:
        await test_concurrent_limiting()
    else:
        results.add_skip("Concurrent limit", "Ollama not available")
    
    # Summary
    success = results.summary()
    sys.exit(0 if success else 1)


def main():
    parser = argparse.ArgumentParser(description="Ollama E2E Tests")
    parser.add_argument("--skip-slow", action="store_true", help="Skip slow tests")
    parser.add_argument("--test", type=str, help="Run specific test")
    args = parser.parse_args()
    
    asyncio.run(run_all_tests(skip_slow=args.skip_slow))


if __name__ == "__main__":
    main()
