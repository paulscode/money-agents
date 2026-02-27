"""
Unit tests for Nostr relay pool communication.

Tests NostrRelayPool publish_event, query_events, search_events
with mocked WebSocket connections.
"""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.nostr_service import NostrRelayPool


@pytest.fixture
def pool():
    """Create relay pool with test relays."""
    return NostrRelayPool(
        default_relays=["wss://relay1.test", "wss://relay2.test"],
        timeout=2.0,
        connect_timeout=1.0,
    )


def _make_ws_cm(recv_values=None, recv_side_effect=None):
    """Build an async context manager that yields a mock websocket.

    Args:
        recv_values: List of raw strings to return from recv() in order.
        recv_side_effect: Side effect for recv (e.g. asyncio.TimeoutError).
    """
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock()
    if recv_values is not None:
        mock_ws.recv = AsyncMock(side_effect=recv_values)
    elif recv_side_effect is not None:
        mock_ws.recv = AsyncMock(side_effect=recv_side_effect)
    else:
        mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_ws


class TestPublishEvent:
    """Tests for publish_event."""

    @pytest.mark.asyncio
    async def test_publish_sends_to_all_relays(self, pool):
        """Event is sent to all configured relays."""
        event = {"id": "abc", "content": "test", "kind": 1}
        connections = {}

        def mock_connect(url, **kwargs):
            cm, ws = _make_ws_cm(recv_values=[
                json.dumps(["OK", "abc", True]),
            ])
            connections[url] = ws
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            results = await pool.publish_event(event)

        assert len(connections) == 2
        assert "wss://relay1.test" in connections
        assert "wss://relay2.test" in connections

    @pytest.mark.asyncio
    async def test_publish_ok_response(self, pool):
        """Relay accepting event produces 'ok' result."""
        event = {"id": "abc", "content": "test"}

        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["OK", "abc", True]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            results = await pool.publish_event(event)

        assert results["wss://relay1.test"] == "ok"
        assert results["wss://relay2.test"] == "ok"

    @pytest.mark.asyncio
    async def test_publish_rejection(self, pool):
        """Relay rejecting event produces error message."""
        event = {"id": "abc", "content": "test"}

        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["OK", "abc", False, "rate-limited"]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            results = await pool.publish_event(event)

        assert results["wss://relay1.test"] == "rate-limited"

    @pytest.mark.asyncio
    async def test_publish_timeout_assumes_success(self, pool):
        """Timeout waiting for OK assumes event was sent."""
        event = {"id": "abc", "content": "test"}

        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_side_effect=asyncio.TimeoutError)
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            results = await pool.publish_event(event)

        assert results["wss://relay1.test"] == "ok"

    @pytest.mark.asyncio
    async def test_publish_connection_error(self, pool):
        """Connection failure produces error result."""
        event = {"id": "abc", "content": "test"}

        with patch("websockets.connect", side_effect=ConnectionRefusedError("Down")):
            results = await pool.publish_event(event)

        assert "error" in results.get("wss://relay1.test", "")

    @pytest.mark.asyncio
    async def test_publish_custom_relays(self, pool):
        """Custom relays override defaults."""
        event = {"id": "abc", "content": "test"}
        custom_relays = ["wss://custom.relay"]
        connections = []

        def mock_connect(url, **kwargs):
            connections.append(url)
            cm, _ = _make_ws_cm(recv_side_effect=asyncio.TimeoutError)
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            await pool.publish_event(event, relays=custom_relays)

        assert connections == ["wss://custom.relay"]

    @pytest.mark.asyncio
    async def test_publish_send_called_with_event_msg(self, pool):
        """The EVENT message is sent over the websocket."""
        event = {"id": "abc", "content": "test", "kind": 1}
        ws_instances = []

        def mock_connect(url, **kwargs):
            cm, ws = _make_ws_cm(recv_values=[
                json.dumps(["OK", "abc", True]),
            ])
            ws_instances.append(ws)
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            await pool.publish_event(event)

        # Each ws should have had send called with ["EVENT", event_json]
        for ws in ws_instances:
            ws.send.assert_called_once()
            sent = json.loads(ws.send.call_args[0][0])
            assert sent[0] == "EVENT"
            assert sent[1] == event


class TestQueryEvents:
    """Tests for query_events."""

    @pytest.mark.asyncio
    async def test_query_returns_events(self, pool):
        """Events from relays are returned."""
        test_event = {
            "id": "e" * 64,
            "pubkey": "p" * 64,
            "created_at": 1700000000,
            "kind": 1,
            "content": "Hello",
            "tags": [],
            "sig": "s" * 128,
        }

        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["EVENT", "sub1", test_event]),
                json.dumps(["EOSE", "sub1"]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert len(events) >= 1
        assert events[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_query_deduplicates_by_id(self, pool):
        """Same event from multiple relays is returned only once."""
        test_event = {
            "id": "d" * 64,
            "pubkey": "p" * 64,
            "created_at": 1700000000,
            "kind": 1,
            "content": "Duplicate",
            "tags": [],
            "sig": "s" * 128,
        }

        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["EVENT", "sub1", test_event]),
                json.dumps(["EOSE", "sub1"]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_query_sorted_by_created_at_desc(self, pool):
        """Events are sorted newest first."""
        events_data = [
            {"id": "a" * 64, "pubkey": "p" * 64, "created_at": 1000, "kind": 1,
             "content": "old", "tags": [], "sig": "s" * 128},
            {"id": "b" * 64, "pubkey": "p" * 64, "created_at": 2000, "kind": 1,
             "content": "new", "tags": [], "sig": "s" * 128},
        ]

        def mock_connect(url, **kwargs):
            if "relay1" in url:
                cm, _ = _make_ws_cm(recv_values=[
                    json.dumps(["EVENT", "sub1", events_data[0]]),
                    json.dumps(["EOSE", "sub1"]),
                ])
            else:
                cm, _ = _make_ws_cm(recv_values=[
                    json.dumps(["EVENT", "sub1", events_data[1]]),
                    json.dumps(["EOSE", "sub1"]),
                ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert len(events) == 2
        assert events[0]["created_at"] > events[1]["created_at"]

    @pytest.mark.asyncio
    async def test_query_respects_limit(self, pool):
        """Query respects the max result limit."""
        large_events = [
            {"id": f"{i:064d}", "pubkey": "p" * 64, "created_at": 1000 + i,
             "kind": 1, "content": f"event {i}", "tags": [], "sig": "s" * 128}
            for i in range(25)
        ]

        def mock_connect(url, **kwargs):
            responses = [json.dumps(["EVENT", "sub1", e]) for e in large_events]
            responses.append(json.dumps(["EOSE", "sub1"]))
            cm, _ = _make_ws_cm(recv_values=responses)
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]}, limit=5)

        assert len(events) <= 5

    @pytest.mark.asyncio
    async def test_query_timeout_returns_partial(self, pool):
        """Timeout returns whatever events were collected."""
        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["EVENT", "sub1", {
                    "id": "a" * 64, "pubkey": "p" * 64, "created_at": 1000,
                    "kind": 1, "content": "partial", "tags": [], "sig": "s" * 128,
                }]),
                # Next recv times out
                asyncio.TimeoutError,
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_query_relay_failure_graceful(self, pool):
        """One relay failing doesn't crash the whole query."""
        test_event = {
            "id": "e" * 64, "pubkey": "p" * 64, "created_at": 1000,
            "kind": 1, "content": "test", "tags": [], "sig": "s" * 128,
        }

        def mock_connect(url, **kwargs):
            if "relay1" in url:
                raise ConnectionRefusedError("Down")
            cm, _ = _make_ws_cm(recv_values=[
                json.dumps(["EVENT", "sub1", test_event]),
                json.dumps(["EOSE", "sub1"]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_query_malformed_response_ignored(self, pool):
        """Malformed JSON responses are silently ignored."""
        def mock_connect(url, **kwargs):
            cm, _ = _make_ws_cm(recv_values=[
                "not valid json",
                json.dumps(["EOSE", "sub1"]),
            ])
            return cm

        with patch("websockets.connect", side_effect=mock_connect):
            events = await pool.query_events({"kinds": [1]})

        assert isinstance(events, list)


class TestSearchEvents:
    """Tests for search_events (NIP-50)."""

    @pytest.mark.asyncio
    async def test_search_uses_nip50_relays(self, pool):
        """Search targets NIP-50 supporting relays (nostr.band)."""
        pool.default_relays = [
            "wss://relay.damus.io",
            "wss://relay.nostr.band",
            "wss://nos.lol",
        ]
        pool.query_events = AsyncMock(return_value=[])

        await pool.search_events("bitcoin", limit=5)

        call_args = pool.query_events.call_args
        relays_used = call_args.kwargs.get("relays") or call_args[1].get("relays", [])
        assert any("nostr.band" in r for r in relays_used)

    @pytest.mark.asyncio
    async def test_search_filter_includes_search_field(self, pool):
        """Search filter includes NIP-50 'search' field."""
        pool.query_events = AsyncMock(return_value=[])

        await pool.search_events("bitcoin privacy", kinds=[1])

        call_args = pool.query_events.call_args
        filters = call_args[0][0]
        assert filters["search"] == "bitcoin privacy"

    @pytest.mark.asyncio
    async def test_search_with_kind_filter(self, pool):
        """Search can filter by event kinds."""
        pool.query_events = AsyncMock(return_value=[])

        await pool.search_events("bitcoin", kinds=[1, 30023])

        call_args = pool.query_events.call_args
        filters = call_args[0][0]
        assert filters["kinds"] == [1, 30023]

    @pytest.mark.asyncio
    async def test_search_fallback_when_no_nip50_relay(self):
        """Falls back to first relay when no NIP-50 relay configured."""
        pool = NostrRelayPool(
            default_relays=["wss://relay.damus.io", "wss://nos.lol"],
            timeout=2.0,
            connect_timeout=1.0,
        )
        pool.query_events = AsyncMock(return_value=[])

        await pool.search_events("test")

        call_args = pool.query_events.call_args
        relays_used = call_args.kwargs.get("relays") or call_args[1].get("relays", [])
        assert len(relays_used) >= 1


class TestRelayPoolConfig:
    """Tests for relay pool configuration."""

    @patch("app.services.nostr_service.settings")
    def test_default_relays_parsed_from_settings(self, mock_settings):
        """Default relays are parsed from comma-separated config."""
        mock_settings.nostr_default_relays = "wss://r1.io, wss://r2.io, wss://r3.io"
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3

        pool = NostrRelayPool()
        assert pool.default_relays == ["wss://r1.io", "wss://r2.io", "wss://r3.io"]

    def test_custom_timeout(self):
        """Custom timeout is respected."""
        pool = NostrRelayPool(timeout=10.0, connect_timeout=5.0)
        assert pool.timeout == 10.0
        assert pool.connect_timeout == 5.0

    def test_custom_relays_override(self):
        """Custom relay list overrides defaults."""
        pool = NostrRelayPool(default_relays=["wss://custom.relay"])
        assert pool.default_relays == ["wss://custom.relay"]
