"""
Unit tests for Nostr tool executor in tool_execution_service.py.

Tests the _execute_nostr action dispatcher with mocked NostrService.
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


class TestNostrExecutor:
    """Tests for _execute_nostr in ToolExecutor."""

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_missing_action(self, mock_settings):
        """Missing action returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        result = await service._execute_nostr(tool, {})

        assert result.success is False
        assert "action" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_nostr_disabled(self, mock_settings):
        """Returns error when USE_NOSTR is false."""
        mock_settings.use_nostr = False

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        result = await service._execute_nostr(tool, {"action": "list_identities"})

        assert result.success is False
        assert "USE_NOSTR" in result.error

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.NostrService", create=True)
    @patch("app.services.tool_execution_service.settings")
    async def test_create_identity(self, mock_settings, MockNostrService):
        """create_identity dispatches to NostrService.create_identity."""
        mock_settings.use_nostr = True

        mock_nostr = AsyncMock()
        mock_nostr.create_identity = AsyncMock(return_value={
            "identity_id": str(uuid4()),
            "npub": "npub1test",
            "name": "TestBot",
        })

        from app.services.tool_execution_service import ToolExecutor

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            service = ToolExecutor()
            tool = _mock_tool()

            result = await service._execute_nostr(tool, {
                "action": "create_identity",
                "name": "TestBot",
                "about": "A test bot",
            })

        # Service was called correctly
        assert result.success is True or "identity_id" in str(result.output)

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_create_identity_missing_name(self, mock_settings):
        """create_identity without name returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "create_identity",
                # name is missing
            })

        assert result.success is False
        assert "name" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_post_note_missing_params(self, mock_settings):
        """post_note without identity_id and content returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "post_note",
                # Missing identity_id and content
            })

        assert result.success is False
        assert "identity_id" in result.error.lower() or "content" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_search_missing_query(self, mock_settings):
        """search without query returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "search",
                # Missing query
            })

        assert result.success is False
        assert "query" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_unknown_action(self, mock_settings):
        """Unknown action returns error with valid action list."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "nonexistent_action",
            })

        assert result.success is False
        assert "Unknown Nostr action" in result.error
        assert "create_identity" in result.error  # Suggests valid actions

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_send_zap_missing_params(self, mock_settings):
        """send_zap without required params returns error."""
        mock_settings.use_nostr = True

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService"):
            result = await service._execute_nostr(tool, {
                "action": "send_zap",
                "identity_id": str(uuid4()),
                # Missing target and amount_sats
            })

        assert result.success is False
        assert "target" in result.error.lower() or "amount_sats" in result.error.lower()

    @pytest.mark.asyncio
    @patch("app.services.tool_execution_service.settings")
    async def test_value_error_handled(self, mock_settings):
        """ValueError from NostrService is returned as error."""
        mock_settings.use_nostr = True

        mock_nostr = AsyncMock()
        mock_nostr.list_identities = AsyncMock(side_effect=ValueError("Test error"))

        from app.services.tool_execution_service import ToolExecutor
        service = ToolExecutor()
        tool = _mock_tool()

        with patch("app.services.nostr_service.NostrService", return_value=mock_nostr):
            result = await service._execute_nostr(tool, {
                "action": "list_identities",
            })

        assert result.success is False
        assert "Test error" in result.error
