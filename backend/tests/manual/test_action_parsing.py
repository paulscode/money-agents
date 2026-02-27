"""Manual tests for Campaign Action Parsing."""
import asyncio
import sys

from app.services.campaign_action_service import CampaignActionService, ActionType


class MockDB:
    pass


def test_action_parsing():
    print('=== Action Parsing Tests ===')
    print()

    # Parse response only needs the service, not actual DB calls
    service = CampaignActionService(MockDB())

    # Test 1: Parse provide_input action
    print('Test 1: Parse provide_input action')
    response1 = '''
I'll help you with the content approval. Let me provide that input now:

<campaign_action type="provide_input" key="content_approval">Approved - looks great!</campaign_action>

That should unblock the content production stream.
'''
    result1 = service.parse_response(response1)
    print(f'  Found {len(result1.actions)} actions')
    if len(result1.actions) == 1:
        action = result1.actions[0]
        print(f'  Type: {action.action_type.value}')
        print(f'  Key attr: {action.attributes.get("key")}')
        print(f'  Content: {action.content}')
        assert action.action_type == ActionType.PROVIDE_INPUT
        assert action.attributes.get('key') == 'content_approval'
        assert action.content == 'Approved - looks great!'
        print('  Clean content length:', len(result1.clean_content))
        print('  ✓ Parsed correctly')
    else:
        print('  ✗ Wrong number of actions')
        return False

    # Test 2: Parse update_status action
    print()
    print('Test 2: Parse update_status action')
    response2 = '''
Let me pause this campaign:

<campaign_action type="update_status" new_status="paused">Waiting for stakeholder approval</campaign_action>
'''
    result2 = service.parse_response(response2)
    print(f'  Found {len(result2.actions)} actions')
    if len(result2.actions) == 1:
        action = result2.actions[0]
        print(f'  Type: {action.action_type.value}')
        print(f'  New Status: {action.attributes.get("new_status")}')
        print(f'  Content: {action.content}')
        assert action.action_type == ActionType.UPDATE_STATUS
        print('  ✓ Parsed correctly')
    else:
        print('  ✗ Wrong number of actions')
        return False

    # Test 3: Parse add_note action
    print()
    print('Test 3: Parse add_note action')
    response3 = '''
I'll add a note to track this:

<campaign_action type="add_note" category="client_feedback">Client mentioned they want more emphasis on ROI metrics in the final deliverable</campaign_action>
'''
    result3 = service.parse_response(response3)
    print(f'  Found {len(result3.actions)} actions')
    if len(result3.actions) == 1:
        action = result3.actions[0]
        print(f'  Type: {action.action_type.value}')
        print(f'  Category: {action.attributes.get("category")}')
        print(f'  Content: {action.content[:50]}...')
        assert action.action_type == ActionType.ADD_NOTE
        print('  ✓ Parsed correctly')
    else:
        print('  ✗ Wrong number of actions')
        return False

    # Test 4: Parse multiple actions
    print()
    print('Test 4: Parse multiple actions in one response')
    response4 = '''
Let me help with all of these:

<campaign_action type="provide_input" key="brand_guidelines">Use blue and white colors only</campaign_action>

And I'll add a note for tracking:

<campaign_action type="add_note" category="design">Client prefers minimalist design</campaign_action>

I'll also prioritize this stream:

<campaign_action type="prioritize_stream" stream_name="content_production">High priority due to deadline</campaign_action>
'''
    result4 = service.parse_response(response4)
    print(f'  Found {len(result4.actions)} actions')
    if len(result4.actions) == 3:
        print(f'  Action 1: {result4.actions[0].action_type.value}')
        print(f'  Action 2: {result4.actions[1].action_type.value}')
        print(f'  Action 3: {result4.actions[2].action_type.value}')
        print('  ✓ All actions parsed correctly')
    else:
        print('  ✗ Wrong number of actions')
        return False

    # Test 5: Response with no actions
    print()
    print('Test 5: Response with no actions')
    response5 = 'The campaign is going well. No actions needed right now.'
    result5 = service.parse_response(response5)
    print(f'  Found {len(result5.actions)} actions')
    assert len(result5.actions) == 0
    print('  ✓ Correctly found no actions')

    # Test 6: Skip task action
    print()
    print('Test 6: Parse skip_task action')
    response6 = '''
<campaign_action type="skip_task" task_id="task-789" reason="Not relevant">Skipping because it's out of scope</campaign_action>
'''
    result6 = service.parse_response(response6)
    print(f'  Found {len(result6.actions)} actions')
    if len(result6.actions) == 1:
        action = result6.actions[0]
        print(f'  Type: {action.action_type.value}')
        print(f'  Task ID: {action.attributes.get("task_id")}')
        print(f'  Reason attr: {action.attributes.get("reason")}')
        assert action.action_type == ActionType.SKIP_TASK
        print('  ✓ Parsed correctly')
    else:
        print('  ✗ Wrong number of actions')
        return False

    # Test 7: Action preview generation
    print()
    print('Test 7: Action preview generation')
    action_prev = result1.actions[0]
    preview = action_prev.get_preview()
    print(f'  Preview: {preview}')
    assert 'content_approval' in preview
    assert 'Approved' in preview
    print('  ✓ Preview generated correctly')

    # Test 8: to_dict serialization
    print()
    print('Test 8: to_dict serialization')
    action_dict = action_prev.to_dict()
    print(f'  Keys: {list(action_dict.keys())}')
    assert 'action_id' in action_dict
    assert 'action_type' in action_dict
    assert 'content' in action_dict
    assert 'preview' in action_dict
    print('  ✓ Serialization works')

    # Test 9: Clean content removes action tags
    print()
    print('Test 9: Clean content removes action tags')
    clean = result1.clean_content
    assert '<campaign_action' not in clean
    assert 'That should unblock' in clean
    assert "I'll help you" in clean
    print(f'  Clean content: {clean[:60]}...')
    print('  ✓ Action tags removed correctly')

    # Test 10: Parse error handling
    print()
    print('Test 10: Parse error handling (invalid type)')
    response_bad = '<campaign_action type="invalid_type">content</campaign_action>'
    result_bad = service.parse_response(response_bad)
    print(f'  Actions found: {len(result_bad.actions)}')
    print(f'  Parse errors: {len(result_bad.parse_errors)}')
    assert len(result_bad.actions) == 0
    assert len(result_bad.parse_errors) == 1
    print('  ✓ Invalid type handled correctly')

    print()
    print('✓ All action parsing tests passed')
    return True


if __name__ == '__main__':
    success = test_action_parsing()
    sys.exit(0 if success else 1)
