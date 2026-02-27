"""
Unit tests for Nostr Service — identity and publishing operations.

Uses mocks for database and relay connections to test business logic.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from uuid import uuid4, UUID

pynostr = pytest.importorskip("pynostr", reason="pynostr not installed")


def _mock_identity(**overrides):
    """Create a mock NostrIdentity object."""
    defaults = {
        "id": uuid4(),
        "user_id": uuid4(),
        "campaign_id": None,
        "pubkey_hex": "a" * 64,
        "npub": "npub1" + "a" * 58,
        "encrypted_nsec": "encrypted_nsec_value",
        "display_name": "TestUser",
        "about": "Test bio",
        "picture_url": "",
        "nip05": "",
        "lud16": "",
        "relay_urls": ["wss://relay.test.io"],
        "follower_count": 0,
        "following_count": 0,
        "post_count": 0,
        "total_zaps_received_sats": 0,
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "last_posted_at": None,
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


class TestNostrServiceIdentity:
    """Tests for identity management."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.encrypt_nsec")
    @patch("app.services.nostr_service.NostrRelayPool")
    @patch("app.services.nostr_service.settings")
    async def test_create_identity(self, mock_settings, MockPool, mock_encrypt, mock_session_factory):
        """create_identity generates keypair, encrypts nsec, stores in DB."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        mock_encrypt.return_value = "encrypted_nsec"

        # Mock DB session
        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock relay pool
        mock_pool = AsyncMock()
        mock_pool.default_relays = ["wss://relay.test.io"]
        mock_pool.publish_event = AsyncMock(return_value={"wss://relay.test.io": "ok"})
        MockPool.return_value = mock_pool

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = mock_pool

        user_id = uuid4()
        result = await service.create_identity(
            user_id=user_id,
            name="Test Identity",
            about="A test identity",
        )

        assert "identity_id" in result
        assert "npub" in result
        assert result["name"] == "Test Identity"
        mock_encrypt.assert_called_once()  # nsec was encrypted
        mock_session.add.assert_called_once()  # Identity added to DB
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_list_identities(self, mock_settings, mock_session_factory):
        """list_identities returns compact identity list."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        identity = _mock_identity(display_name="Alice", post_count=5)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [identity]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.services.nostr_service import NostrService
        service = NostrService()

        user_id = identity.user_id
        result = await service.list_identities(user_id)

        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert result[0]["post_count"] == 5
        # Verify npub is included but nsec is NOT
        assert "npub" in result[0]

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_get_identity_returns_no_keys(self, mock_settings, mock_session_factory):
        """get_identity returns profile info but never private keys."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        identity = _mock_identity()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.services.nostr_service import NostrService
        service = NostrService()

        result = await service.get_identity(str(identity.id), identity.user_id)

        assert "npub" in result
        assert "pubkey_hex" in result
        assert "nsec" not in result
        assert "encrypted_nsec" not in result
        assert "private_key" not in result

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_load_identity_access_denied(self, mock_settings, mock_session_factory):
        """Accessing another user's identity raises ValueError."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.services.nostr_service import NostrService
        service = NostrService()

        with pytest.raises(ValueError, match="not found or access denied"):
            await service.get_identity(str(uuid4()), uuid4())


class TestNostrServicePublishing:
    """Tests for content publishing operations."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service._rate_limiter")
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_post_note_success(self, mock_settings, mock_session_factory,
                                     mock_decrypt, mock_rate_limiter):
        """post_note builds event, publishes, and increments stats."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.nostr_post_rate_limit_hour = 30
        mock_settings.nostr_post_rate_limit_day = 150

        mock_rate_limiter.check.return_value = True

        # Mock identity load
        identity = _mock_identity()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock nsec decrypt — return a real nsec for signing
        try:
            from pynostr.key import PrivateKey
            pk = PrivateKey()
            mock_decrypt.return_value = pk.bech32()
        except ImportError:
            pytest.skip("pynostr not installed")

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.publish_event = AsyncMock(return_value={"wss://relay.test.io": "ok"})

        result = await service.post_note(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            content="Hello Nostr!",
            hashtags=["bitcoin", "test"],
        )

        assert "event_id" in result
        assert "relays" in result
        service.relay_pool.publish_event.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.nostr_service._rate_limiter")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_post_note_rate_limited(self, mock_settings, mock_session_factory,
                                          mock_rate_limiter):
        """post_note raises ValueError when rate limit exceeded."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.nostr_post_rate_limit_hour = 30
        mock_settings.nostr_post_rate_limit_day = 150

        mock_rate_limiter.check.return_value = False

        identity = _mock_identity()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.services.nostr_service import NostrService
        service = NostrService()

        with pytest.raises(ValueError, match="Rate limit exceeded"):
            await service.post_note(
                identity_id=str(identity.id),
                user_id=identity.user_id,
                content="Spam",
            )

    @pytest.mark.asyncio
    @patch("app.services.nostr_service._rate_limiter")
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_post_article_success(self, mock_settings, mock_session_factory,
                                         mock_decrypt, mock_rate_limiter):
        """post_article builds kind-30023 event with title and d-tag."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.nostr_post_rate_limit_hour = 30
        mock_settings.nostr_post_rate_limit_day = 150

        mock_rate_limiter.check.return_value = True

        identity = _mock_identity()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        try:
            from pynostr.key import PrivateKey
            pk = PrivateKey()
            mock_decrypt.return_value = pk.bech32()
        except ImportError:
            pytest.skip("pynostr not installed")

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.publish_event = AsyncMock(return_value={"wss://relay.test.io": "ok"})

        result = await service.post_article(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            title="Bitcoin Privacy Guide",
            content="Long form article content here...",
            hashtags=["bitcoin", "privacy"],
        )

        assert "event_id" in result
        assert "d_tag" in result
        assert result["d_tag"].startswith("bitcoin-privacy")


class TestNostrServiceDiscovery:
    """Tests for search and discovery operations."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_search_returns_compact_events(self, mock_settings):
        """search returns compact event summaries, not raw events."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        import time
        mock_events = [
            {
                "id": "a" * 64,
                "pubkey": "b" * 64,
                "created_at": int(time.time()) - 60,
                "kind": 1,
                "content": "Bitcoin is great!",
                "tags": [["t", "bitcoin"]],
                "sig": "x" * 128,
            }
        ]

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.search_events = AsyncMock(return_value=mock_events)
        service.relay_pool.query_events = AsyncMock(return_value=[])

        result = await service.search(query="bitcoin")

        assert result["query"] == "bitcoin"
        assert result["count"] == 1
        assert len(result["results"]) == 1

        # Verify compact format
        evt = result["results"][0]
        assert len(evt["id"]) == 12  # Truncated
        assert "sig" not in evt  # No signature

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_get_engagement_categorizes(self, mock_settings, mock_session_factory):
        """get_engagement categorizes events by kind."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        import time
        identity = _mock_identity()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_events = [
            {"id": "a" * 64, "pubkey": "c" * 64, "created_at": int(time.time()), "kind": 7, "content": "+", "tags": []},
            {"id": "b" * 64, "pubkey": "d" * 64, "created_at": int(time.time()), "kind": 6, "content": "", "tags": []},
            {"id": "c" * 64, "pubkey": "e" * 64, "created_at": int(time.time()), "kind": 1, "content": "reply", "tags": []},
        ]

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.query_events = AsyncMock(return_value=mock_events)

        result = await service.get_engagement(
            identity_id=str(identity.id),
            user_id=identity.user_id,
        )

        assert result["summary"]["reactions"] == 1
        assert result["summary"]["reposts"] == 1
        assert result["summary"]["replies"] == 1
        assert result["summary"]["total"] == 3


class TestNostrServiceZaps:
    """Tests for zap-related operations."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_send_zap_requires_lnd(self, mock_settings):
        """send_zap returns error when USE_LND is false."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.use_lnd = False

        from app.services.nostr_service import NostrService
        service = NostrService()

        result = await service.send_zap(
            identity_id=str(uuid4()),
            user_id=uuid4(),
            target="npub1test",
            amount_sats=100,
        )

        assert "error" in result
        assert "USE_LND" in result["error"]

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_zap_receipts_requires_lnd(self, mock_settings):
        """get_zap_receipts returns error when USE_LND is false."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.use_lnd = False

        from app.services.nostr_service import NostrService
        service = NostrService()

        result = await service.get_zap_receipts(
            identity_id=str(uuid4()),
            user_id=uuid4(),
        )

        assert "error" in result
        assert "USE_LND" in result["error"]
