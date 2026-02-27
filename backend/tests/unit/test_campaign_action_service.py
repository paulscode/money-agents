"""Unit tests for Campaign Action Service."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.campaign_action_service import (
    CampaignActionService,
    CampaignAction,
    ActionType,
    ActionParseResult,
)


class TestActionParsing:
    """Test suite for action parsing from AI responses."""
    
    @pytest.fixture
    def service(self):
        """Create CampaignActionService with mock DB."""
        mock_db = MagicMock()
        return CampaignActionService(mock_db)
    
    def test_parse_provide_input_action(self, service):
        """Test parsing a provide_input action."""
        response = '''
Here's my recommendation for the input:

<campaign_action type="provide_input" key="content_approval">Approved - looks great!</campaign_action>

That should help move things forward.
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.action_type == ActionType.PROVIDE_INPUT
        assert action.attributes.get("key") == "content_approval"
        assert action.content == "Approved - looks great!"
    
    def test_parse_update_status_action(self, service):
        """Test parsing an update_status action."""
        response = '''
<campaign_action type="update_status" new_status="paused">Waiting for stakeholder approval</campaign_action>
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.action_type == ActionType.UPDATE_STATUS
        assert action.attributes.get("new_status") == "paused"
    
    def test_parse_add_note_action(self, service):
        """Test parsing an add_note action."""
        response = '''
<campaign_action type="add_note" category="client_feedback">Client requested faster delivery</campaign_action>
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.action_type == ActionType.ADD_NOTE
        assert action.attributes.get("category") == "client_feedback"
    
    def test_parse_prioritize_stream_action(self, service):
        """Test parsing a prioritize_stream action."""
        response = '''
<campaign_action type="prioritize_stream" stream_name="content_production">Critical path item</campaign_action>
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.action_type == ActionType.PRIORITIZE_STREAM
        assert action.attributes.get("stream_name") == "content_production"
    
    def test_parse_skip_task_action(self, service):
        """Test parsing a skip_task action."""
        response = '''
<campaign_action type="skip_task" task_id="task-123" reason="Out of scope">Not needed for MVP</campaign_action>
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 1
        action = result.actions[0]
        assert action.action_type == ActionType.SKIP_TASK
        assert action.attributes.get("task_id") == "task-123"
    
    def test_parse_multiple_actions(self, service):
        """Test parsing multiple actions from one response."""
        response = '''
Let me help with these items:

<campaign_action type="provide_input" key="brand_colors">Blue and white</campaign_action>

<campaign_action type="add_note" category="design">Client prefers minimalist</campaign_action>

<campaign_action type="prioritize_stream" stream_name="design">High priority</campaign_action>
'''
        result = service.parse_response(response)
        
        assert len(result.actions) == 3
        assert result.actions[0].action_type == ActionType.PROVIDE_INPUT
        assert result.actions[1].action_type == ActionType.ADD_NOTE
        assert result.actions[2].action_type == ActionType.PRIORITIZE_STREAM
    
    def test_parse_no_actions(self, service):
        """Test parsing response with no actions."""
        response = "The campaign is progressing well. No changes needed."
        
        result = service.parse_response(response)
        
        assert len(result.actions) == 0
        assert result.clean_content == response
    
    def test_parse_invalid_action_type(self, service):
        """Test handling of invalid action types."""
        response = '<campaign_action type="invalid_type">content</campaign_action>'
        
        result = service.parse_response(response)
        
        assert len(result.actions) == 0
        assert len(result.parse_errors) == 1
    
    def test_parse_missing_type_attribute(self, service):
        """Test handling of action missing type attribute."""
        response = '<campaign_action key="test">content</campaign_action>'
        
        result = service.parse_response(response)
        
        assert len(result.actions) == 0
        assert len(result.parse_errors) == 1
    
    def test_clean_content_removes_action_tags(self, service):
        """Test that action tags are removed from clean content."""
        response = '''
Before action.

<campaign_action type="provide_input" key="test">value</campaign_action>

After action.
'''
        result = service.parse_response(response)
        
        assert '<campaign_action' not in result.clean_content
        assert '</campaign_action>' not in result.clean_content
        assert 'Before action' in result.clean_content
        assert 'After action' in result.clean_content


class TestCampaignAction:
    """Test suite for CampaignAction dataclass."""
    
    def test_action_to_dict(self):
        """Test serializing action to dictionary."""
        action = CampaignAction(
            action_type=ActionType.PROVIDE_INPUT,
            content="Test value",
            attributes={"key": "test_key"},
            raw_xml="<campaign_action>...</campaign_action>",
            action_id="action_1",
        )
        
        result = action.to_dict()
        
        assert result["action_type"] == "provide_input"
        assert result["content"] == "Test value"
        assert result["attributes"]["key"] == "test_key"
        assert result["action_id"] == "action_1"
        assert "preview" in result
    
    def test_action_preview_provide_input(self):
        """Test preview generation for provide_input action."""
        action = CampaignAction(
            action_type=ActionType.PROVIDE_INPUT,
            content="Approved",
            attributes={"key": "approval_status"},
        )
        
        preview = action.get_preview()
        
        assert "approval_status" in preview
        assert "Approved" in preview
    
    def test_action_preview_update_status(self):
        """Test preview generation for update_status action."""
        action = CampaignAction(
            action_type=ActionType.UPDATE_STATUS,
            content="Need more information",
            attributes={"new_status": "paused"},
        )
        
        preview = action.get_preview()
        
        assert "paused" in preview
    
    def test_action_preview_add_note(self):
        """Test preview generation for add_note action."""
        action = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Important meeting tomorrow",
            attributes={"category": "reminder"},
        )
        
        preview = action.get_preview()
        
        assert "reminder" in preview
        assert "Important" in preview
    
    def test_action_preview_skip_task(self):
        """Test preview generation for skip_task action."""
        action = CampaignAction(
            action_type=ActionType.SKIP_TASK,
            content="Not needed",
            attributes={"task_id": "task-456", "reason": "Out of scope"},
        )
        
        preview = action.get_preview()
        
        assert "task-456" in preview
    
    def test_action_preview_long_content_truncated(self):
        """Test that long content is truncated in preview."""
        long_content = "A" * 200
        action = CampaignAction(
            action_type=ActionType.PROVIDE_INPUT,
            content=long_content,
            attributes={"key": "test"},
        )
        
        preview = action.get_preview()
        
        assert len(preview) < len(long_content) + 50  # Some buffer for prefix
        assert "..." in preview


class TestActionParseResult:
    """Test suite for ActionParseResult dataclass."""
    
    def test_result_structure(self):
        """Test ActionParseResult has correct structure."""
        result = ActionParseResult(
            clean_content="Test content",
            actions=[],
            parse_errors=["Error 1"],
        )
        
        assert result.clean_content == "Test content"
        assert result.actions == []
        assert result.parse_errors == ["Error 1"]
    
    def test_result_with_actions(self):
        """Test ActionParseResult with actions."""
        action = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Note",
            attributes={},
        )
        
        result = ActionParseResult(
            clean_content="Clean",
            actions=[action],
            parse_errors=[],
        )
        
        assert len(result.actions) == 1
        assert result.actions[0].action_type == ActionType.ADD_NOTE


class TestActionExecution:
    """Test suite for action execution (requires mocked database)."""
    
    @pytest.fixture
    def service(self):
        """Create CampaignActionService with mock DB."""
        mock_db = MagicMock()
        return CampaignActionService(mock_db)
    
    @pytest.mark.asyncio
    async def test_execute_action_returns_tuple(self, service):
        """Test that execute_action returns success/message tuple."""
        # This test would require mocking database operations
        # For now, we just verify the method signature exists
        action = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Test note",
            attributes={"category": "test"},
        )
        
        # The actual execution would need database mocks
        assert hasattr(service, 'execute_action')


# ============================================================================
# Security Tests: Anti-Replay & Count Limits
# ============================================================================

class TestAntiReplay:
    """Test that anti-replay protection prevents double-execution of actions."""

    @pytest.fixture(autouse=True)
    def _clear_replay_state(self):
        """Clear both class-level memory set and disable Redis for clean tests."""
        CampaignActionService._memory_executed_ids.clear()
        yield
        CampaignActionService._memory_executed_ids.clear()

    def _make_service_with_owner(self, owner_id):
        """Create a CampaignActionService whose mock DB returns a campaign
        owned by *owner_id* so the GAP-11 ownership check passes."""
        mock_db = AsyncMock()
        mock_campaign = MagicMock()
        mock_campaign.user_id = owner_id
        campaign_result = MagicMock()
        campaign_result.scalar_one_or_none.return_value = mock_campaign
        mock_db.execute.return_value = campaign_result
        service = CampaignActionService(mock_db)
        # Disable Redis in tests so only in-memory anti-replay is used
        service._redis = None
        return service

    @pytest.mark.asyncio
    async def test_replay_blocked_on_second_execution(self):
        """Same action_id cannot be executed twice."""
        action = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Test note",
            attributes={"category": "test"},
            action_id="action_1_replay_test",
        )

        campaign_id = uuid4()
        user_id = uuid4()
        service = self._make_service_with_owner(user_id)

        # Mock the single-action executor to succeed
        with patch.object(service, "execute_action", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (True, "Note added")

            # First execution should succeed
            results1 = await service.execute_actions(
                campaign_id, [action], user_id
            )
            assert results1[0]["success"] is True
            assert results1[0]["message"] == "Note added"

            # Second execution with same action_id should be blocked
            results2 = await service.execute_actions(
                campaign_id, [action], user_id
            )
            assert results2[0]["success"] is False
            assert "replay blocked" in results2[0]["message"].lower()

            # execute_action should only have been called once
            assert mock_exec.call_count == 1

    @pytest.mark.asyncio
    async def test_different_action_ids_not_blocked(self):
        """Different action_ids should each execute independently."""
        action1 = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Note 1",
            attributes={},
            action_id="action_unique_1",
        )
        action2 = CampaignAction(
            action_type=ActionType.ADD_NOTE,
            content="Note 2",
            attributes={},
            action_id="action_unique_2",
        )

        user_id = uuid4()
        service = self._make_service_with_owner(user_id)

        with patch.object(service, "execute_action", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = (True, "Done")

            results = await service.execute_actions(
                uuid4(), [action1, action2], user_id
            )
            assert results[0]["success"] is True
            assert results[1]["success"] is True
            assert mock_exec.call_count == 2


class TestActionCountLimits:
    """Test MAX_ACTIONS_PER_RESPONSE enforcement."""

    @pytest.fixture
    def service(self):
        mock_db = AsyncMock()
        return CampaignActionService(mock_db)

    def test_max_actions_constant_is_reasonable(self, service):
        """MAX_ACTIONS_PER_RESPONSE should be a sane limit."""
        assert 1 <= service.MAX_ACTIONS_PER_RESPONSE <= 20

    def test_excessive_actions_are_truncated(self, service):
        """More than MAX_ACTIONS_PER_RESPONSE actions should be truncated."""
        # Create a response with 15 actions (exceeds default limit of 10)
        actions_xml = ""
        for i in range(15):
            actions_xml += (
                f'<campaign_action type="add_note">Note {i}</campaign_action>\n'
            )

        result = service.parse_response(actions_xml)

        # Should only parse up to MAX_ACTIONS_PER_RESPONSE
        assert len(result.actions) <= service.MAX_ACTIONS_PER_RESPONSE
        # Should have a parse error about truncation
        assert any("too many" in e.lower() for e in result.parse_errors)

    def test_actions_at_limit_are_not_truncated(self, service):
        """Exactly MAX_ACTIONS_PER_RESPONSE actions should all be parsed."""
        actions_xml = ""
        for i in range(service.MAX_ACTIONS_PER_RESPONSE):
            actions_xml += (
                f'<campaign_action type="add_note">Note {i}</campaign_action>\n'
            )

        result = service.parse_response(actions_xml)
        assert len(result.actions) == service.MAX_ACTIONS_PER_RESPONSE
        assert not any("too many" in e.lower() for e in result.parse_errors)
