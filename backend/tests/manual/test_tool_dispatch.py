"""
Manual integration test for Cross-Worker Tool Dispatch.

This test verifies the full tool dispatch flow:
1. Campaign worker sends tool_dispatch request
2. Broker routes to best available resource agent (or local execution)
3. Result is routed back to campaign worker

Prerequisites:
- Backend running: docker-compose up backend
- At least one resource agent connected (or test with local fallback)
- Valid auth token

Usage:
    cd backend
    source venv/bin/activate
    python tests/manual/test_tool_dispatch.py
"""
import asyncio
import json
import websockets
import sys
from uuid import uuid4

# Configuration - update these as needed
WS_URL = "ws://localhost:8000/api/v1/broker/agent"
# To get an API key, register a remote agent in the admin UI
# or create one via: python -c "from app.services.broker_service import BrokerService; print(BrokerService.generate_api_key())"
API_KEY = "YOUR_AGENT_API_KEY"  # Replace with actual key

# Test tool slugs
TEST_TOOLS = [
    "web_search",  # Should be available on most hosts
    "text_generation",  # Typically local-only
]


async def simulate_campaign_worker():
    """
    Simulate a campaign worker that:
    1. Connects to the broker
    2. Registers as a campaign worker
    3. Requests tool dispatch
    4. Waits for result
    """
    print("=" * 60)
    print("Tool Dispatch Integration Test")
    print("=" * 60)
    
    ws_url = f"{WS_URL}?api_key={API_KEY}"
    
    try:
        async with websockets.connect(ws_url) as ws:
            print("✓ WebSocket connected")
            
            # Step 1: Register as campaign worker
            worker_id = f"test-worker-{uuid4().hex[:8]}"
            register_msg = {
                "type": "register",
                "data": {
                    "hostname": "test-dispatch-host",
                    "capabilities": {
                        "max_concurrent_jobs": 2,
                    },
                    # Campaign worker registration
                    "is_campaign_worker": True,
                    "campaign_worker_id": worker_id,
                    "campaign_capacity": 1,
                }
            }
            
            await ws.send(json.dumps(register_msg))
            print(f"→ Sent registration (worker_id: {worker_id})")
            
            # Wait for registration confirmation
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(response)
            
            if data.get("type") == "registered":
                print(f"✓ Registered: {data.get('data', {}).get('message')}")
            else:
                print(f"✗ Unexpected response: {data}")
                return
            
            # Step 2: Test tool dispatch
            for tool_slug in TEST_TOOLS:
                print(f"\n--- Testing tool: {tool_slug} ---")
                
                exec_id = str(uuid4())
                campaign_id = str(uuid4())
                
                dispatch_msg = {
                    "type": "tool_dispatch",
                    "data": {
                        "execution_id": exec_id,
                        "worker_id": worker_id,
                        "campaign_id": campaign_id,
                        "tool_slug": tool_slug,
                        "params": {
                            "query": "test query for tool dispatch",
                        }
                    }
                }
                
                await ws.send(json.dumps(dispatch_msg))
                print(f"→ Sent tool_dispatch (exec_id: {exec_id[:8]}...)")
                
                # Wait for result
                try:
                    result_msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    result_data = json.loads(result_msg)
                    
                    if result_data.get("type") == "tool_result":
                        result = result_data.get("data", {}).get("result", {})
                        success = result.get("success", False)
                        
                        if success:
                            print(f"✓ Tool execution succeeded")
                            print(f"  Executed by: {result.get('executed_by', 'unknown')}")
                            output = result.get("output", {})
                            if isinstance(output, dict):
                                print(f"  Output keys: {list(output.keys())}")
                            else:
                                print(f"  Output: {str(output)[:100]}...")
                        else:
                            error = result.get("error", "Unknown error")
                            print(f"✗ Tool execution failed: {error}")
                            if result.get("timed_out"):
                                print("  (Timed out)")
                            if result.get("agent_disconnected"):
                                print("  (Agent disconnected)")
                    else:
                        print(f"? Unexpected message type: {result_data.get('type')}")
                        print(f"  Full message: {json.dumps(result_data, indent=2)[:500]}")
                        
                except asyncio.TimeoutError:
                    print(f"✗ Timeout waiting for result (30s)")
            
            print("\n" + "=" * 60)
            print("Test complete")
            print("=" * 60)
            
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"✗ WebSocket connection failed: {e}")
        print("  Check that the API key is valid and the backend is running")
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()


async def test_local_tool_dispatch():
    """
    Test tool dispatch using local execution only.
    
    This tests the broker's local fallback when no remote agents are available.
    Uses HTTP API instead of WebSocket for simpler testing.
    """
    import httpx
    
    print("=" * 60)
    print("Local Tool Dispatch Test (HTTP)")
    print("=" * 60)
    
    # This would require an HTTP endpoint for tool dispatch
    # Currently the system only supports WebSocket dispatch from campaign workers
    print("Note: Local dispatch testing requires WebSocket connection")
    print("Use the campaign worker simulation above")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print(__doc__)
        sys.exit(0)
    
    if API_KEY == "YOUR_AGENT_API_KEY":
        print("ERROR: Please update API_KEY in this script")
        print("Generate one with:")
        print('  python -c "from app.services.broker_service import BrokerService; print(BrokerService.generate_api_key())"')
        sys.exit(1)
    
    asyncio.run(simulate_campaign_worker())
