#!/usr/bin/env python3
"""
Manual test for Campaign WebSocket Progress feature.
Run this script to test WebSocket connectivity and event flow.

Usage:
    python tests/manual/test_websocket_progress.py
"""

import asyncio
import json
import os
import httpx
import websockets
import sys
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000"
EMAIL = os.environ.get("TEST_EMAIL", "admin@example.com")
PASSWORD = os.environ.get("TEST_PASSWORD", "your_password_here")


async def get_auth_token() -> str:
    """Get JWT token via login."""
    async with httpx.AsyncClient() as client:
        # LoginRequest expects JSON with identifier (email or username) and password
        response = await client.post(
            f"{BASE_URL}/api/v1/auth/login",
            json={"identifier": EMAIL, "password": PASSWORD}
        )
        if response.status_code != 200:
            print(f"❌ Login failed: {response.text}")
            sys.exit(1)
        token = response.json()["access_token"]
        print(f"✅ Logged in as {EMAIL}")
        return token


async def get_active_campaign(token: str) -> str:
    """Get an active campaign ID."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{BASE_URL}/api/v1/campaigns/",
            headers={"Authorization": f"Bearer {token}"}
        )
        if response.status_code != 200:
            print(f"❌ Failed to get campaigns: {response.text}")
            sys.exit(1)
        
        campaigns = response.json()
        # Find an active campaign, or use any campaign
        for campaign in campaigns:
            if campaign["status"] in ("active", "running"):
                print(f"✅ Found active campaign: {campaign['id']}")
                return campaign["id"]
        
        if campaigns:
            campaign = campaigns[0]
            print(f"⚠️  No active campaign found, using: {campaign['id']} (status: {campaign['status']})")
            return campaign["id"]
        
        print("❌ No campaigns found")
        sys.exit(1)


async def test_websocket_connection(token: str, campaign_id: str):
    """Test WebSocket connection and message flow."""
    ws_url = f"{WS_URL}/api/v1/campaigns/{campaign_id}/progress?token={token}"
    
    print(f"\n📡 Connecting to WebSocket: {ws_url[:80]}...")
    
    try:
        async with websockets.connect(ws_url) as ws:
            print("✅ WebSocket connected")
            
            # Wait for auth result
            auth_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            auth_data = json.loads(auth_msg)
            print(f"📨 Auth result: {json.dumps(auth_data, indent=2)}")
            
            if not auth_data.get("success"):
                print(f"❌ Auth failed: {auth_data.get('error')}")
                return False
            
            # Wait for initial state
            state_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            state_data = json.loads(state_msg)
            print(f"\n📊 Initial state received:")
            print(f"   Type: {state_data.get('type')}")
            if state_data.get("type") == "initial_state":
                data = state_data.get("data", {})
                print(f"   Status: {data.get('status')}")
                print(f"   Budget: ${data.get('budget_allocated', 0):.2f} allocated, ${data.get('budget_spent', 0):.2f} spent")
                print(f"   Tasks: {data.get('tasks_completed', 0)}/{data.get('tasks_total', 0)} completed")
                print(f"   Progress: {data.get('overall_progress_pct', 0):.1f}%")
                print(f"   Streams: {data.get('total_streams', 0)} total, {data.get('completed_streams', 0)} completed")
            
            # Test ping/pong
            print("\n🏓 Testing ping/pong...")
            await ws.send(json.dumps({"type": "ping"}))
            pong_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            pong_data = json.loads(pong_msg)
            if pong_data.get("type") == "pong":
                print("✅ Ping/pong working")
            else:
                print(f"❌ Unexpected response: {pong_data}")
            
            # Test invalid message
            print("\n🔧 Testing invalid message handling...")
            await ws.send(json.dumps({"type": "invalid_message"}))
            error_msg = await asyncio.wait_for(ws.recv(), timeout=5)
            error_data = json.loads(error_msg)
            if error_data.get("type") == "error":
                print(f"✅ Error handling works: {error_data.get('error')}")
            else:
                print(f"⚠️  Unexpected response: {error_data}")
            
            # Listen for a few seconds for any updates
            print("\n👂 Listening for updates (5 seconds)...")
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(msg)
                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"   [{ts}] Event: {data.get('type')}")
            except asyncio.TimeoutError:
                print("   (No updates received - expected for non-active campaign)")
            
            print("\n✅ WebSocket test completed successfully!")
            return True
            
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"❌ WebSocket connection rejected: {e}")
        return False
    except asyncio.TimeoutError:
        print("❌ Timeout waiting for WebSocket response")
        return False
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        return False


async def main():
    """Run the WebSocket test."""
    print("=" * 60)
    print("Campaign WebSocket Progress Test")
    print("=" * 60)
    
    # Get token
    token = await get_auth_token()
    
    # Get campaign
    campaign_id = await get_active_campaign(token)
    
    # Test WebSocket
    success = await test_websocket_connection(token, campaign_id)
    
    print("\n" + "=" * 60)
    if success:
        print("🎉 All tests passed!")
    else:
        print("❌ Some tests failed")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
