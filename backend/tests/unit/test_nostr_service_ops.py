"""
Unit tests for Nostr Service operations — update_profile, react, repost,
follow, unfollow, delete_event, get_feed, get_thread, get_profile, send_zap.

These are the operations NOT covered in test_nostr_service.py.
"""
import json
import time
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

pynostr = pytest.importorskip("pynostr", reason="pynostr not installed")

from pynostr.key import PrivateKey


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
        "following_count": 5,
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


def _setup_service_mocks(mock_settings, mock_session_factory, identity=None):
    """Common setup for service tests — mock settings and DB session."""
    mock_settings.nostr_relay_timeout = 5
    mock_settings.nostr_relay_connect_timeout = 3
    mock_settings.nostr_default_relays = "wss://relay.test.io"
    mock_settings.nostr_post_rate_limit_hour = 30
    mock_settings.nostr_post_rate_limit_day = 150
    mock_settings.nostr_lightning_address = ""

    if identity:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = identity
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)


def _get_service_with_mocked_relay():
    """Create NostrService with mocked relay pool."""
    from app.services.nostr_service import NostrService
    service = NostrService()
    service.relay_pool = AsyncMock()
    service.relay_pool.publish_event = AsyncMock(return_value={"wss://relay.test.io": "ok"})
    service.relay_pool.query_events = AsyncMock(return_value=[])
    return service


class TestUpdateProfile:
    """Tests for update_profile."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_update_name(self, mock_settings, mock_session_factory, mock_decrypt):
        """Updating name returns updated fields."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        result = await service.update_profile(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            name="NewName",
        )

        assert result["status"] == "updated"
        assert "display_name" in result["fields"]

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_update_no_changes(self, mock_settings, mock_session_factory):
        """No changes returns 'no changes' status."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        service = _get_service_with_mocked_relay()

        result = await service.update_profile(
            identity_id=str(identity.id),
            user_id=identity.user_id,
        )

        assert result["status"] == "no changes"

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_update_multiple_fields(self, mock_settings, mock_session_factory, mock_decrypt):
        """Multiple fields can be updated at once."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        result = await service.update_profile(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            name="Bob",
            about="New bio",
            lud16="bob@ln.tips",
        )

        assert result["status"] == "updated"
        assert "display_name" in result["fields"]
        assert "about" in result["fields"]
        assert "lud16" in result["fields"]


class TestReact:
    """Tests for react."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_react_default_plus(self, mock_settings, mock_session_factory, mock_decrypt):
        """Default reaction is '+'."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        result = await service.react(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id="e" * 64,
        )

        assert result["status"] == "reacted"
        assert result["reaction"] == "+"

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_react_custom_emoji(self, mock_settings, mock_session_factory, mock_decrypt):
        """Custom reaction string is supported."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        result = await service.react(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id="e" * 64,
            reaction="🤙",
        )

        assert result["reaction"] == "🤙"

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_react_publishes_event(self, mock_settings, mock_session_factory, mock_decrypt):
        """React publishes a kind-7 event to relays."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        await service.react(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id="e" * 64,
        )

        service.relay_pool.publish_event.assert_called_once()
        published_event = service.relay_pool.publish_event.call_args[0][0]
        assert published_event["kind"] == 7


class TestRepost:
    """Tests for repost."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_repost_success(self, mock_settings, mock_session_factory, mock_decrypt):
        """Repost returns status and truncated event_id."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        event_id = "f" * 64
        result = await service.repost(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id=event_id,
        )

        assert result["status"] == "reposted"
        assert result["event_id"] == event_id[:12]

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_repost_publishes_kind_6(self, mock_settings, mock_session_factory, mock_decrypt):
        """Repost publishes a kind-6 event."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        await service.repost(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id="f" * 64,
        )

        published_event = service.relay_pool.publish_event.call_args[0][0]
        assert published_event["kind"] == 6
        assert published_event["content"] == ""


class TestFollow:
    """Tests for follow/unfollow."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_follow_adds_pubkeys(self, mock_settings, mock_session_factory, mock_decrypt):
        """Follow adds new pubkeys to follow set."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()
        # Mock empty follow list
        service.relay_pool.query_events = AsyncMock(return_value=[])

        new_pubkey = "b" * 64
        result = await service.follow(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            pubkeys=[new_pubkey],
        )

        assert result["added"] == 1
        assert result["following"] == 1

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_follow_deduplicates(self, mock_settings, mock_session_factory, mock_decrypt):
        """Following the same pubkey twice doesn't duplicate."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()
        # Mock existing follow list with the pubkey already present
        existing_pubkey = "c" * 64
        service.relay_pool.query_events = AsyncMock(return_value=[{
            "kind": 3,
            "tags": [["p", existing_pubkey]],
        }])

        result = await service.follow(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            pubkeys=[existing_pubkey],  # Already followed
        )

        assert result["added"] == 0

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_unfollow_removes_pubkeys(self, mock_settings, mock_session_factory, mock_decrypt):
        """Unfollow removes pubkeys from follow set."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()
        # Mock follow list with one entry
        existing_pubkey = "d" * 64
        service.relay_pool.query_events = AsyncMock(return_value=[{
            "kind": 3,
            "tags": [["p", existing_pubkey]],
        }])

        result = await service.unfollow(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            pubkeys=[existing_pubkey],
        )

        assert result["removed"] == 1
        assert result["following"] == 0

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_unfollow_nonexistent_doesnt_crash(self, mock_settings, mock_session_factory, mock_decrypt):
        """Unfollowing someone not followed produces removed=0."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()
        service.relay_pool.query_events = AsyncMock(return_value=[])

        result = await service.unfollow(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            pubkeys=["e" * 64],
        )

        assert result["removed"] == 0


class TestDeleteEvent:
    """Tests for delete_event."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_delete_event_success(self, mock_settings, mock_session_factory, mock_decrypt):
        """delete_event publishes kind-5 deletion request."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        event_ids = ["a" * 64, "b" * 64]
        result = await service.delete_event(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_ids=event_ids,
        )

        assert result["status"] == "deletion_requested"
        assert len(result["event_ids"]) == 2

        # Verify kind 5 event published
        published_event = service.relay_pool.publish_event.call_args[0][0]
        assert published_event["kind"] == 5
        e_tags = [t for t in published_event["tags"] if t[0] == "e"]
        assert len(e_tags) == 2


class TestGetFeed:
    """Tests for get_feed."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_get_feed_empty_follows(self, mock_settings, mock_session_factory):
        """Empty follow list returns empty feed with note."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        service = _get_service_with_mocked_relay()
        service.relay_pool.query_events = AsyncMock(return_value=[])  # No contacts

        result = await service.get_feed(
            identity_id=str(identity.id),
            user_id=identity.user_id,
        )

        assert result["count"] == 0
        assert "Not following anyone" in result.get("note", "")

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_get_feed_with_posts(self, mock_settings, mock_session_factory):
        """Feed returns compact events from followed users."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        service = _get_service_with_mocked_relay()

        followed_pubkey = "f" * 64
        # First call: get follows, second call: get posts, third call: cache names
        service.relay_pool.query_events = AsyncMock(side_effect=[
            # Follow list
            [{"kind": 3, "tags": [["p", followed_pubkey]]}],
            # Posts from followed users
            [{
                "id": "p" * 64,
                "pubkey": followed_pubkey,
                "created_at": int(time.time()) - 300,
                "kind": 1,
                "content": "Hello from followed user",
                "tags": [],
            }],
            # Name cache lookup
            [],
        ])

        result = await service.get_feed(
            identity_id=str(identity.id),
            user_id=identity.user_id,
        )

        assert result["count"] == 1
        assert result["results"][0]["text"] == "Hello from followed user"


class TestGetThread:
    """Tests for get_thread."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_thread_with_replies(self, mock_settings):
        """Thread returns root event and replies."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        root_event = {
            "id": "r" * 64,
            "pubkey": "a" * 64,
            "created_at": int(time.time()) - 600,
            "kind": 1,
            "content": "Original post",
            "tags": [],
        }
        reply_event = {
            "id": "q" * 64,
            "pubkey": "b" * 64,
            "created_at": int(time.time()) - 300,
            "kind": 1,
            "content": "A reply",
            "tags": [["e", "r" * 64]],
        }

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()

        # First call: root event, second call: replies, third: name cache
        service.relay_pool.query_events = AsyncMock(side_effect=[
            [root_event],
            [reply_event],
            [],  # name cache
        ])

        result = await service.get_thread(event_id="r" * 64)

        assert result["reply_count"] == 1
        assert result["root"]["text"] == "Original post"
        assert len(result["replies"]) == 1

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_thread_missing_root(self, mock_settings):
        """Thread with deleted/missing root returns no root."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()

        service.relay_pool.query_events = AsyncMock(side_effect=[
            [],  # No root
            [],  # No replies
            [],  # Name cache
        ])

        result = await service.get_thread(event_id="x" * 64)

        assert result["reply_count"] == 0
        assert "root" not in result


class TestGetProfile:
    """Tests for get_profile."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_profile_with_metadata(self, mock_settings):
        """Profile returns parsed kind-0 metadata."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()

        pubkey = "d" * 64
        metadata = {
            "name": "Satoshi",
            "about": "Bitcoin creator",
            "picture": "https://example.com/pic.png",
            "nip05": "satoshi@example.com",
            "lud16": "satoshi@ln.tips",
        }

        service.relay_pool.query_events = AsyncMock(side_effect=[
            # Kind-0 metadata
            [{"content": json.dumps(metadata), "pubkey": pubkey}],
            # Contacts
            [{"kind": 3, "tags": [["p", "x" * 64], ["p", "y" * 64]]}],
        ])

        result = await service.get_profile(pubkey_or_npub=pubkey)

        assert result["name"] == "Satoshi"
        assert result["about"] == "Bitcoin creator"
        assert result["lud16"] == "satoshi@ln.tips"
        assert result["following"] == 2

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_profile_with_posts(self, mock_settings):
        """Profile can include recent posts."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()

        pubkey = "d" * 64

        service.relay_pool.query_events = AsyncMock(side_effect=[
            # Metadata
            [{"content": json.dumps({"name": "Alice"}), "pubkey": pubkey}],
            # Contacts
            [],
            # Posts
            [{
                "id": "p" * 64,
                "pubkey": pubkey,
                "created_at": int(time.time()),
                "kind": 1,
                "content": "My post",
                "tags": [],
            }],
        ])

        result = await service.get_profile(pubkey_or_npub=pubkey, include_posts=True)

        assert "recent_posts" in result
        assert len(result["recent_posts"]) == 1

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_get_profile_no_metadata(self, mock_settings):
        """Profile with no kind-0 returns just pubkey stub."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()

        service.relay_pool.query_events = AsyncMock(return_value=[])

        result = await service.get_profile(pubkey_or_npub="d" * 64)

        assert "pubkey" in result
        # Name fields should not be set
        assert "name" not in result


class TestSendZapHappyPath:
    """Tests for send_zap happy path (with mocked LND)."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_send_zap_no_lightning_address(self, mock_settings, mock_session_factory, mock_decrypt):
        """Zap fails when target has no lud16."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.use_lnd = True
        mock_settings.nostr_lightning_address = ""

        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        # Profile has no lud16
        service.relay_pool.query_events = AsyncMock(return_value=[
            {"content": json.dumps({"name": "NoLN"}), "pubkey": "b" * 64},
        ])

        result = await service.send_zap(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            target="b" * 64,
            amount_sats=100,
        )

        assert "error" in result
        assert "Lightning address" in result["error"]


class TestReply:
    """Tests for reply (delegates to post_note)."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service._rate_limiter")
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_reply_is_rate_limited(self, mock_settings, mock_session_factory, mock_decrypt, mock_rl):
        """Reply goes through rate limiting (delegates to post_note)."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)
        mock_rl.check.return_value = False  # Rate limited

        from app.services.nostr_service import NostrService
        service = NostrService()

        with pytest.raises(ValueError, match="Rate limit exceeded"):
            await service.reply(
                identity_id=str(identity.id),
                user_id=identity.user_id,
                event_id="e" * 64,
                content="reply",
            )

    @pytest.mark.asyncio
    @patch("app.services.nostr_service._rate_limiter")
    @patch("app.services.nostr_service.decrypt_nsec")
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.settings")
    async def test_reply_adds_event_tag(self, mock_settings, mock_session_factory, mock_decrypt, mock_rl):
        """Reply includes e-tag referencing the parent event."""
        identity = _mock_identity()
        _setup_service_mocks(mock_settings, mock_session_factory, identity)
        mock_rl.check.return_value = True

        pk = PrivateKey()
        mock_decrypt.return_value = pk.bech32()

        service = _get_service_with_mocked_relay()

        result = await service.reply(
            identity_id=str(identity.id),
            user_id=identity.user_id,
            event_id="e" * 64,
            content="Great point!",
        )

        assert "event_id" in result
        # Verify the published event has e-tag
        published_event = service.relay_pool.publish_event.call_args[0][0]
        e_tags = [t for t in published_event["tags"] if t[0] == "e"]
        assert len(e_tags) == 1
        assert e_tags[0][1] == "e" * 64


class TestGetFollowList:
    """Tests for _get_follow_list helper."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_empty_follow_list(self, mock_settings):
        """No contacts event returns empty list."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.query_events = AsyncMock(return_value=[])

        result = await service._get_follow_list("a" * 64)
        assert result == []

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.settings")
    async def test_follow_list_extracts_p_tags(self, mock_settings):
        """Follow list extracted from kind-3 p-tags."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.query_events = AsyncMock(return_value=[{
            "kind": 3,
            "tags": [["p", "x" * 64], ["p", "y" * 64], ["e", "z" * 64]],
        }])

        result = await service._get_follow_list("a" * 64)
        assert len(result) == 2  # Only p-tags, not e-tags
        assert "x" * 64 in result
        assert "y" * 64 in result


class TestLightningAddressDefault:
    """Tests for default Lightning Address in create_identity."""

    @pytest.mark.asyncio
    @patch("app.services.nostr_service.async_session_factory")
    @patch("app.services.nostr_service.encrypt_nsec")
    @patch("app.services.nostr_service.settings")
    async def test_default_lud16_from_config(self, mock_settings, mock_encrypt, mock_session_factory):
        """When no lud16 provided, uses NOSTR_LIGHTNING_ADDRESS config."""
        mock_settings.nostr_relay_timeout = 5
        mock_settings.nostr_relay_connect_timeout = 3
        mock_settings.nostr_default_relays = "wss://relay.test.io"
        mock_settings.nostr_lightning_address = "user@getalby.com"
        mock_encrypt.return_value = "encrypted"

        mock_session = AsyncMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        from app.services.nostr_service import NostrService
        service = NostrService()
        service.relay_pool = AsyncMock()
        service.relay_pool.default_relays = ["wss://relay.test.io"]
        service.relay_pool.publish_event = AsyncMock(return_value={})

        result = await service.create_identity(
            user_id=uuid4(),
            name="Test",
            # No lud16 provided
        )

        # The identity should have been created with the default lud16
        add_call = mock_session.add.call_args[0][0]
        assert add_call.lud16 == "user@getalby.com"
