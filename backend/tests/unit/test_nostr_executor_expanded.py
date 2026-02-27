"""
Unit tests for Nostr tool executor — expanded coverage for all 20 actions.

Tests action dispatch, parameter validation, and error handling for actions
not covered in test_nostr_executor.py: react, repost, reply, follow, unfollow,
delete_event, get_feed, get_thread, get_profile, get_engagement,
update_profile, get_zap_receipts, post_article, post_note (happy path).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


def _mock_tool():
    """Create a mock Tool object with created_by_id."""
    tool = MagicMock()
    tool.created_by_id = uuid4()
    tool.slug = "nostr"
    return tool


def _mock_nostr_service():
    """Create a fully mocked NostrService."""
    service = AsyncMock()
    service.create_identity = AsyncMock(return_value={"identity_id": str(uuid4()), "npub": "npub1test"})
    service.list_identities = AsyncMock(return_value=[{"id": str(uuid4()), "name": "Test"}])
    service.get_identity = AsyncMock(return_value={"npub": "npub1test", "name": "Test"})
    service.update_profile = AsyncMock(return_value={"status": "updated", "fields": ["display_name"]})
    service.post_note = AsyncMock(return_value={"event_id": "abc123", "relays": {}})
    service.post_article = AsyncMock(return_value={"event_id": "def456", "d_tag": "my-article"})
    service.react = AsyncMock(return_value={"status": "reacted", "reaction": "+"})
    service.repost = AsyncMock(return_value={"status": "reposted"})
    service.reply = AsyncMock(return_value={"event_id": "ghi789", "relays": {}})
    service.follow = AsyncMock(return_value={"following": 5, "added": 2})
    service.unfollow = AsyncMock(return_value={"following": 3, "removed": 2})
    service.delete_event = AsyncMock(return_value={"status": "deletion_requested"})
    service.search = AsyncMock(return_value={"query": "test", "count": 0, "results": []})
    service.get_feed = AsyncMock(return_value={"count": 0, "results": []})
    service.get_thread = AsyncMock(return_value={"reply_count": 0, "replies": []})
    service.get_profile = AsyncMock(return_value={"name": "Test", "pubkey": "abc..."})
    service.get_engagement = AsyncMock(return_value={"summary": {"total": 0}})
    service.send_zap = AsyncMock(return_value={"status": "zap_sent", "amount_sats": 100})
    service.get_zap_receipts = AsyncMock(return_value={"total_sats": 0, "count": 0})
    return service


class TestNostrExecutorActions:
    """Tests for each action dispatch in _execute_nostr."""

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_list_identities_success(self, mock_settings):
        """list_identities dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {"action": "list_identities"})

        assert result.success is True
        mock_nostr.list_identities.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_identity_success(self, mock_settings):
        """get_identity dispatches with identity_id."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        identity_id = str(uuid4())
        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_identity",
                "identity_id": identity_id,
            })

        assert result.success is True
        mock_nostr.get_identity.assert_called_once_with(identity_id, tool.created_by_id)

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_identity_missing_id(self, mock_settings):
        """get_identity without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_identity"})

        assert result.success is False
        assert "identity_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_update_profile_success(self, mock_settings):
        """update_profile dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "update_profile",
                "identity_id": str(uuid4()),
                "name": "NewName",
                "about": "New bio",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_update_profile_missing_id(self, mock_settings):
        """update_profile without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "update_profile",
                "name": "Name",
            })

        assert result.success is False
        assert "identity_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_post_note_success(self, mock_settings):
        """post_note dispatches correctly with all params."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "post_note",
                "identity_id": str(uuid4()),
                "content": "Hello Nostr!",
                "hashtags": ["bitcoin"],
            })

        assert result.success is True
        mock_nostr.post_note.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_post_article_success(self, mock_settings):
        """post_article dispatches with title, content, summary."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "post_article",
                "identity_id": str(uuid4()),
                "title": "My Article",
                "content": "Article body",
                "summary": "A summary",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_post_article_missing_title(self, mock_settings):
        """post_article without title returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "post_article",
                "identity_id": str(uuid4()),
                "content": "Body",
                # Missing title
            })

        assert result.success is False
        assert "title" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_react_success(self, mock_settings):
        """react dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "react",
                "identity_id": str(uuid4()),
                "event_id": "e" * 64,
                "reaction": "🤙",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_react_missing_event_id(self, mock_settings):
        """react without event_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "react",
                "identity_id": str(uuid4()),
                # Missing event_id
            })

        assert result.success is False
        assert "event_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_repost_success(self, mock_settings):
        """repost dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "repost",
                "identity_id": str(uuid4()),
                "event_id": "e" * 64,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_repost_missing_params(self, mock_settings):
        """repost without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "repost",
                # Missing both identity_id and event_id
            })

        assert result.success is False

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_reply_success(self, mock_settings):
        """reply dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "reply",
                "identity_id": str(uuid4()),
                "event_id": "e" * 64,
                "content": "Great post!",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_reply_missing_content(self, mock_settings):
        """reply without content returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "reply",
                "identity_id": str(uuid4()),
                "event_id": "e" * 64,
                # Missing content
            })

        assert result.success is False

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_follow_success(self, mock_settings):
        """follow dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "follow",
                "identity_id": str(uuid4()),
                "pubkeys": ["a" * 64, "b" * 64],
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_follow_missing_pubkeys(self, mock_settings):
        """follow without pubkeys returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "follow",
                "identity_id": str(uuid4()),
                # Missing pubkeys
            })

        assert result.success is False
        assert "pubkeys" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_unfollow_success(self, mock_settings):
        """unfollow dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "unfollow",
                "identity_id": str(uuid4()),
                "pubkeys": ["a" * 64],
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_delete_event_success(self, mock_settings):
        """delete_event dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "delete_event",
                "identity_id": str(uuid4()),
                "event_ids": ["a" * 64, "b" * 64],
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_delete_event_missing_event_ids(self, mock_settings):
        """delete_event without event_ids returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "delete_event",
                "identity_id": str(uuid4()),
                # Missing event_ids
            })

        assert result.success is False
        assert "event_ids" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_search_success(self, mock_settings):
        """search dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "search",
                "query": "bitcoin privacy",
                "kinds": [1],
                "limit": 5,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_feed_success(self, mock_settings):
        """get_feed dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_feed",
                "identity_id": str(uuid4()),
                "limit": 5,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_feed_missing_identity(self, mock_settings):
        """get_feed without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_feed"})

        assert result.success is False
        assert "identity_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_thread_success(self, mock_settings):
        """get_thread dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_thread",
                "event_id": "e" * 64,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_thread_missing_event_id(self, mock_settings):
        """get_thread without event_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_thread"})

        assert result.success is False
        assert "event_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_profile_success(self, mock_settings):
        """get_profile dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_profile",
                "pubkey_or_npub": "a" * 64,
                "include_posts": True,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_profile_missing_pubkey(self, mock_settings):
        """get_profile without pubkey_or_npub returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_profile"})

        assert result.success is False
        assert "pubkey_or_npub" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_engagement_success(self, mock_settings):
        """get_engagement dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_engagement",
                "identity_id": str(uuid4()),
                "since": 1700000000,
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_engagement_missing_identity(self, mock_settings):
        """get_engagement without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_engagement"})

        assert result.success is False
        assert "identity_id" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_zap_success(self, mock_settings):
        """send_zap dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "send_zap",
                "identity_id": str(uuid4()),
                "target": "a" * 64,
                "amount_sats": 100,
                "comment": "Great post!",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_zap_receipts_success(self, mock_settings):
        """get_zap_receipts dispatches correctly."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "get_zap_receipts",
                "identity_id": str(uuid4()),
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_get_zap_receipts_missing_identity(self, mock_settings):
        """get_zap_receipts without identity_id returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {"action": "get_zap_receipts"})

        assert result.success is False
        assert "identity_id" in result.error


class TestNostrExecutorEdgeCases:
    """Edge case and error handling tests."""

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_exception_handled_gracefully(self, mock_settings):
        """Unexpected exception returns error, doesn't crash."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()
        mock_nostr.search = AsyncMock(side_effect=RuntimeError("Unexpected"))

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "search",
                "query": "test",
            })

        assert result.success is False
        # SA3-M11: Error messages are now generic to avoid leaking internals
        assert "Nostr operation failed" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_action_case_insensitive(self, mock_settings):
        """Action names are case-insensitive."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "LIST_IDENTITIES",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_action_whitespace_trimmed(self, mock_settings):
        """Action names with whitespace are trimmed."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "  list_identities  ",
            })

        assert result.success is True

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_campaign_id_passed_to_create_identity(self, mock_settings):
        """campaign_id from params is passed through to create_identity."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        campaign_id = str(uuid4())
        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "create_identity",
                "name": "CampaignBot",
                "campaign_id": campaign_id,
            })

        assert result.success is True
        mock_nostr.create_identity.assert_called_once()
        _, kwargs = mock_nostr.create_identity.call_args
        assert kwargs["campaign_id"] == campaign_id

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_limit_param_converted_to_int(self, mock_settings):
        """String limit param is converted to int."""
        mock_settings.use_nostr = True
        mock_nostr = _mock_nostr_service()

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "search",
                "query": "test",
                "limit": "5",  # String, not int
            })

        assert result.success is True
        mock_nostr.search.assert_called_once()
        _, kwargs = mock_nostr.search.call_args
        assert kwargs["limit"] == 5
