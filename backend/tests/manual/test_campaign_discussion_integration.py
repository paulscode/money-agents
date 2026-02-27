"""Integration test for Campaign Discussion feature."""
import asyncio
import json
import os
import websockets
import sys

# Set these environment variables before running, or replace with your own values:
# export TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login -H 'Content-Type: application/json' -d '{"identifier":"admin@example.com","password":"YOUR_PASSWORD"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
TOKEN = os.environ.get("TOKEN", "YOUR_JWT_TOKEN_HERE")
CAMPAIGN_ID = os.environ.get("CAMPAIGN_ID", "YOUR_CAMPAIGN_ID_HERE")
WS_URL = f"ws://localhost:8000/api/v1/agents/campaign-discussion/stream?token={TOKEN}"


async def test_campaign_discussion():
    """Test the Campaign Discussion WebSocket endpoint."""
    print("=" * 60)
    print("Campaign Discussion Integration Test")
    print("=" * 60)
    print(f"Campaign ID: {CAMPAIGN_ID}")
    print()
    
    try:
        async with websockets.connect(WS_URL) as ws:
            print("✓ WebSocket connected")
            
            # Wait for auth response first
            auth_response = await asyncio.wait_for(ws.recv(), timeout=10)
            auth_data = json.loads(auth_response)
            if auth_data.get("type") == "auth_result":
                if auth_data.get("success"):
                    print("✓ Authentication successful")
                else:
                    print(f"✗ Auth failed: {auth_data.get('error')}")
                    return
            
            # Test 1: Simple status query
            print("\n--- Test 1: Status Query ---")
            message = {
                "type": "message",  # Required message type
                "content": "How is the campaign going? Give me a quick status update.",
                "campaign_id": CAMPAIGN_ID
            }
            
            await ws.send(json.dumps(message))
            print(f"→ Sent: {message['content'][:50]}...")
            
            # Collect response
            full_response = ""
            actions_found = []
            tokens_used = 0
            
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=60)
                    data = json.loads(response)
                    
                    if data.get("type") == "chunk":
                        chunk = data.get("content", "")
                        full_response += chunk
                        print(chunk, end="", flush=True)
                    
                    elif data.get("type") == "action":
                        actions_found.append(data)
                        print(f"\n[ACTION DETECTED: {data.get('action_type')}]")
                    
                    elif data.get("type") == "done":
                        tokens_used = data.get("total_tokens", 0)
                        print(f"\n\n✓ Response complete ({tokens_used} tokens)")
                        break
                    
                    elif data.get("type") == "error":
                        print(f"\n✗ Error: {data.get('error')}")
                        break
                        
                except asyncio.TimeoutError:
                    print("\n✗ Timeout waiting for response")
                    break
            
            # Summary
            print("\n--- Test 1 Summary ---")
            print(f"Response length: {len(full_response)} chars")
            print(f"Actions found: {len(actions_found)}")
            print(f"Tokens used: {tokens_used}")
            
            # Check for expected content
            status_mentioned = any(word in full_response.lower() for word in ['paused', 'status', 'campaign', 'budget'])
            print(f"Status mentioned: {'✓' if status_mentioned else '✗'}")
            
            if len(full_response) > 50:
                print("✓ Test 1 PASSED")
            else:
                print("✗ Test 1 FAILED - Response too short")
            
            # Test 2: Query about blockers/inputs
            print("\n\n--- Test 2: Blocker Query ---")
            message2 = {
                "type": "message",
                "content": "What inputs are blocking progress? Can you help me fill them in?",
                "campaign_id": CAMPAIGN_ID
            }
            
            await ws.send(json.dumps(message2))
            print(f"→ Sent: {message2['content']}")
            
            full_response2 = ""
            actions_found2 = []
            
            while True:
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=60)
                    data = json.loads(response)
                    
                    if data.get("type") == "chunk":
                        chunk = data.get("content", "")
                        full_response2 += chunk
                        print(chunk, end="", flush=True)
                    
                    elif data.get("type") == "action":
                        actions_found2.append(data)
                        print(f"\n[ACTION: {data.get('action_type')} - {data.get('preview', '')[:50]}]")
                    
                    elif data.get("type") == "done":
                        print(f"\n\n✓ Response complete")
                        break
                    
                    elif data.get("type") == "error":
                        print(f"\n✗ Error: {data.get('error')}")
                        break
                        
                except asyncio.TimeoutError:
                    print("\n✗ Timeout waiting for response")
                    break
            
            # Summary
            print("\n--- Test 2 Summary ---")
            print(f"Response length: {len(full_response2)} chars")
            print(f"Actions found: {len(actions_found2)}")
            
            # Check for expected content
            inputs_mentioned = any(word in full_response2.lower() for word in ['input', 'api key', 'blocking', 'openai', 'printful'])
            print(f"Inputs discussed: {'✓' if inputs_mentioned else '✗'}")
            
            if actions_found2:
                print("✓ Actions proposed (may need to verify in UI)")
            
            if len(full_response2) > 50:
                print("✓ Test 2 PASSED")
            else:
                print("✗ Test 2 FAILED - Response too short")
            
            print("\n" + "=" * 60)
            print("Integration Test Complete")
            print("=" * 60)
            print("\nTo test action confirmation UI:")
            print(f"  1. Open http://localhost:5173/campaigns/{CAMPAIGN_ID}")
            print("  2. Click the 'Discussion' tab")
            print("  3. Ask: 'Help me provide the brand guidelines input'")
            print("  4. Look for action buttons to Apply/Dismiss")
            
    except Exception as e:
        print(f"✗ Connection failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(test_campaign_discussion())
