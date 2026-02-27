"""
Unit tests for Nostr Service helper functions and rate limiter.

Tests context window management, rate limiting, and utility functions
without requiring database or relay connections.
"""
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.nostr_service import (
    _relative_time,
    _truncate,
    _compact_event,
    _RateLimiter,
    _NameCache,
    _resolve_pubkey,
)


class TestRelativeTime:
    """Tests for _relative_time helper."""

    def test_just_now(self):
        assert _relative_time(int(time.time())) == "just now"

    def test_minutes_ago(self):
        result = _relative_time(int(time.time()) - 300)
        assert "m ago" in result

    def test_hours_ago(self):
        result = _relative_time(int(time.time()) - 7200)
        assert "h ago" in result

    def test_days_ago(self):
        result = _relative_time(int(time.time()) - 259200)
        assert "d ago" in result

    def test_old_date(self):
        result = _relative_time(1700000000)  # Nov 2023
        assert "2023" in result


class TestTruncate:
    """Tests for _truncate helper."""

    def test_short_string_unchanged(self):
        assert _truncate("hello", 200) == "hello"

    def test_long_string_truncated(self):
        long = "a" * 300
        result = _truncate(long, 200)
        assert len(result) == 200
        assert result.endswith("...")

    def test_exact_length(self):
        s = "a" * 200
        assert _truncate(s, 200) == s


class TestCompactEvent:
    """Tests for _compact_event — context window protection."""

    def test_basic_event(self):
        raw = {
            "id": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "pubkey": "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
            "created_at": int(time.time()) - 60,
            "kind": 1,
            "content": "Hello Nostr!",
            "tags": [],
            "sig": "x" * 128,
        }
        compact = _compact_event(raw)

        assert compact["id"] == "abcdef123456"  # Truncated to 12 chars
        assert compact["author"] == "1234567890ab..."  # Truncated
        assert "text" in compact
        assert compact["text"] == "Hello Nostr!"
        assert "sig" not in compact  # Signature omitted

    def test_hashtags_extracted(self):
        raw = {
            "id": "a" * 64,
            "pubkey": "b" * 64,
            "created_at": int(time.time()),
            "content": "Bitcoin is great",
            "tags": [["t", "bitcoin"], ["t", "lightning"], ["e", "someid"]],
        }
        compact = _compact_event(raw)
        assert compact["hashtags"] == ["bitcoin", "lightning"]

    def test_long_content_truncated(self):
        raw = {
            "id": "a" * 64,
            "pubkey": "b" * 64,
            "created_at": int(time.time()),
            "content": "x" * 500,
            "tags": [],
        }
        compact = _compact_event(raw)
        assert len(compact["text"]) == 200
        assert compact["text"].endswith("...")

    def test_author_name_from_cache(self):
        """When name is cached, it appears in compact output."""
        _name_cache = _NameCache()
        pubkey = "c" * 64
        _name_cache.set(pubkey, "satoshi")

        raw = {
            "id": "a" * 64,
            "pubkey": pubkey,
            "created_at": int(time.time()),
            "content": "test",
            "tags": [],
        }

        # Patch the module-level cache
        with patch("app.services.nostr_service._name_cache", _name_cache):
            compact = _compact_event(raw)
            assert compact.get("author_name") == "satoshi"


class TestRateLimiter:
    """Tests for the in-memory rate limiter."""

    def _make_memory_limiter(self):
        """Create a rate limiter forced to in-memory mode for deterministic tests."""
        rl = _RateLimiter()
        rl._redis_checked = True
        rl._redis = None
        return rl

    def test_under_limit(self):
        rl = self._make_memory_limiter()
        assert rl.check("id1", 3600, 10) is True

    def test_at_limit(self):
        rl = self._make_memory_limiter()
        for _ in range(10):
            rl.record("id1")
        assert rl.check("id1", 3600, 10) is False

    def test_different_identities_independent(self):
        rl = self._make_memory_limiter()
        for _ in range(10):
            rl.record("id1")
        assert rl.check("id1", 3600, 10) is False
        assert rl.check("id2", 3600, 10) is True

    def test_expired_entries_pruned(self):
        rl = self._make_memory_limiter()
        # Manually add old timestamps
        key = "old_id:1"
        rl._buckets[key] = [time.time() - 3700]  # Over 1 hour ago
        assert rl.check("old_id", 1, 10) is True  # Should be under limit


class TestNameCache:
    """Tests for the profile name cache."""

    def test_set_and_get(self):
        cache = _NameCache()
        cache.set("abc123", "Alice")
        assert cache.get("abc123") == "Alice"

    def test_miss_returns_none(self):
        cache = _NameCache()
        assert cache.get("nonexistent") is None

    def test_expired_entry(self):
        cache = _NameCache()
        cache._cache["old"] = ("OldName", time.time() - 7200)  # 2 hours old
        assert cache.get("old") is None  # TTL is 1 hour

    def test_eviction_on_overflow(self):
        cache = _NameCache()
        cache.MAX_ENTRIES = 10
        for i in range(15):
            cache.set(f"key{i}", f"name{i}")
        # Should have evicted some entries
        assert len(cache._cache) <= 13  # After eviction of oldest quarter


class TestResolvePubkey:
    """Tests for _resolve_pubkey helper."""

    def test_hex_pubkey_passthrough(self):
        hex_pk = "a" * 64
        assert _resolve_pubkey(hex_pk) == hex_pk

    def test_invalid_pubkey_raises(self):
        with pytest.raises(ValueError, match="Invalid pubkey"):
            _resolve_pubkey("tooshort")

    def test_npub_conversion(self):
        """npub1... is converted to hex. This requires pynostr."""
        try:
            from pynostr.key import PrivateKey
            pk = PrivateKey()
            npub = pk.public_key.bech32()
            hex_pk = pk.public_key.hex()

            result = _resolve_pubkey(npub)
            assert result == hex_pk
        except ImportError:
            pytest.skip("pynostr not installed")
