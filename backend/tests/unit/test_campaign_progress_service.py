"""Tests for CampaignProgressService (WebSocket pub/sub).

Covers:
- subscribe / unsubscribe lifecycle
- emit to subscribers
- failed connection cleanup
- typed emit helpers (status_change, stream_progress, etc.)
- get_subscriber_count / get_total_connections
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.services.campaign_progress_service import (
    CampaignProgressService,
    CampaignSubscription,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ws(send_ok=True):
    """Create a mock WebSocket that supports send_json."""
    ws = AsyncMock()
    if not send_ok:
        ws.send_json.side_effect = Exception("connection closed")
    return ws


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

class TestSubscribeUnsubscribe:

    @pytest.mark.asyncio
    async def test_subscribe_returns_subscription(self):
        svc = CampaignProgressService()
        ws = _mock_ws()
        user_id = uuid4()
        campaign_id = uuid4()

        sub = await svc.subscribe(ws, user_id, campaign_id)
        assert isinstance(sub, CampaignSubscription)
        assert sub.user_id == user_id
        assert sub.campaign_id == campaign_id

    @pytest.mark.asyncio
    async def test_subscribe_increments_count(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()

        await svc.subscribe(_mock_ws(), uuid4(), campaign_id)
        await svc.subscribe(_mock_ws(), uuid4(), campaign_id)

        assert svc.get_subscriber_count(campaign_id) == 2

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_connection(self):
        svc = CampaignProgressService()
        ws = _mock_ws()
        campaign_id = uuid4()

        await svc.subscribe(ws, uuid4(), campaign_id)
        assert svc.get_total_connections() == 1

        await svc.unsubscribe(ws)
        assert svc.get_total_connections() == 0
        assert svc.get_subscriber_count(campaign_id) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_unknown_ws_is_noop(self):
        svc = CampaignProgressService()
        ws = _mock_ws()
        await svc.unsubscribe(ws)  # should not raise
        assert svc.get_total_connections() == 0

    @pytest.mark.asyncio
    async def test_multiple_campaigns(self):
        svc = CampaignProgressService()
        c1, c2 = uuid4(), uuid4()

        await svc.subscribe(_mock_ws(), uuid4(), c1)
        await svc.subscribe(_mock_ws(), uuid4(), c2)

        assert svc.get_subscriber_count(c1) == 1
        assert svc.get_subscriber_count(c2) == 1
        assert svc.get_total_connections() == 2


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

class TestEmit:

    @pytest.mark.asyncio
    async def test_emit_sends_to_all_subscribers(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws1, ws2 = _mock_ws(), _mock_ws()

        await svc.subscribe(ws1, uuid4(), campaign_id)
        await svc.subscribe(ws2, uuid4(), campaign_id)

        count = await svc.emit(campaign_id, "test_event", {"foo": "bar"})
        assert count == 2
        assert ws1.send_json.call_count == 1
        assert ws2.send_json.call_count == 1

        # Check message structure
        msg = ws1.send_json.call_args[0][0]
        assert msg["type"] == "test_event"
        assert msg["campaign_id"] == str(campaign_id)
        assert msg["data"]["foo"] == "bar"
        assert "timestamp" in msg

    @pytest.mark.asyncio
    async def test_emit_returns_zero_for_no_subscribers(self):
        svc = CampaignProgressService()
        count = await svc.emit(uuid4(), "test_event", {})
        assert count == 0

    @pytest.mark.asyncio
    async def test_emit_filters_by_user_id(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        target_user = uuid4()
        other_user = uuid4()

        ws_target = _mock_ws()
        ws_other = _mock_ws()

        await svc.subscribe(ws_target, target_user, campaign_id)
        await svc.subscribe(ws_other, other_user, campaign_id)

        count = await svc.emit(campaign_id, "private", {"x": 1}, user_id=target_user)
        assert count == 1
        assert ws_target.send_json.call_count == 1
        assert ws_other.send_json.call_count == 0

    @pytest.mark.asyncio
    async def test_emit_cleans_up_failed_connections(self):
        """Failed sends trigger cleanup — bad subscriber is removed."""
        svc = CampaignProgressService()
        campaign_id = uuid4()

        ws_good = _mock_ws()
        ws_bad = _mock_ws(send_ok=False)

        await svc.subscribe(ws_good, uuid4(), campaign_id)
        await svc.subscribe(ws_bad, uuid4(), campaign_id)

        count = await svc.emit(campaign_id, "test", {"x": 1})
        # Only the good connection was notified
        assert count == 1
        assert ws_good.send_json.call_count == 1
        # Bad subscriber should have been cleaned up
        assert svc.get_subscriber_count(campaign_id) == 1


# ---------------------------------------------------------------------------
# Typed emit helpers
# ---------------------------------------------------------------------------

class TestTypedEmitHelpers:

    @pytest.mark.asyncio
    async def test_emit_status_change(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_status_change(campaign_id, "active", "paused", reason="User request")
        assert count == 1
        msg = ws.send_json.call_args[0][0]
        assert msg["type"] == "campaign_status"
        assert msg["data"]["old_status"] == "active"
        assert msg["data"]["new_status"] == "paused"
        assert msg["data"]["reason"] == "User request"

    @pytest.mark.asyncio
    async def test_emit_stream_progress(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_stream_progress(
            campaign_id,
            stream_id="s1",
            stream_name="Research",
            tasks_total=10,
            tasks_completed=3,
            tasks_failed=1,
            status="in_progress",
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["progress_pct"] == 30.0
        assert data["tasks_failed"] == 1

    @pytest.mark.asyncio
    async def test_emit_task_completed(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_task_completed(
            campaign_id, stream_id="s1", task_id="t1", task_name="Search", result_summary="Found 5 results"
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["task_name"] == "Search"
        assert data["result_summary"] == "Found 5 results"

    @pytest.mark.asyncio
    async def test_emit_task_failed(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_task_failed(
            campaign_id, stream_id="s1", task_id="t1", task_name="Search", error="Timeout"
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["error"] == "Timeout"

    @pytest.mark.asyncio
    async def test_emit_input_required(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_input_required(
            campaign_id, input_key="api_key", input_type="credentials",
            title="API Key", priority="blocking", blocking_count=3,
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["input_key"] == "api_key"
        assert data["blocking_count"] == 3

    @pytest.mark.asyncio
    async def test_emit_overall_progress(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_overall_progress(
            campaign_id,
            overall_progress_pct=45.678,
            total_tasks=20,
            completed_tasks=9,
            budget_spent=50.0,
            revenue_generated=30.0,
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["overall_progress_pct"] == 45.7  # rounded
        assert data["completed_tasks"] == 9

    @pytest.mark.asyncio
    async def test_emit_budget_warning(self):
        svc = CampaignProgressService()
        campaign_id = uuid4()
        ws = _mock_ws()
        await svc.subscribe(ws, uuid4(), campaign_id)

        count = await svc.emit_budget_warning(
            campaign_id,
            threshold_label="75%",
            severity="warning",
            spent_sats=75000,
            budget_sats=100000,
            remaining_sats=25000,
            percent_used=75.0,
        )
        assert count == 1
        data = ws.send_json.call_args[0][0]["data"]
        assert data["severity"] == "warning"
        assert data["percent_used"] == 75.0
