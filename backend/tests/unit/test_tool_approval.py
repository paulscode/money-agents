"""
Unit tests for Tool Approval Service.

Tests:
- Creating approval requests
- Approving/rejecting requests
- Expiration handling
- Request queries
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import (
    Tool, ToolStatus, ToolCategory, User, UserRole,
    ApprovalStatus, ApprovalUrgency, ToolApprovalRequest,
    ToolExecution, ToolExecutionStatus,
)
from app.services.tool_approval_service import (
    ToolApprovalService,
    ApprovalNotFoundError,
    ApprovalExpiredError,
    ApprovalAlreadyReviewedError,
)


@pytest.fixture
def approval_tool(db_session, test_user):
    """Create a tool that requires approval."""
    tool = Tool(
        id=uuid4(),
        name="Financial Transfer Tool",
        slug="financial-transfer",
        category=ToolCategory.AUTOMATION,
        description="Transfer funds between accounts",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,  # Use actual user
        requires_approval=True,
        approval_urgency="high",
        approval_instructions="Review amount, source, and destination carefully.",
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def regular_tool(db_session, test_user):
    """Create a tool that doesn't require approval."""
    tool = Tool(
        id=uuid4(),
        name="Web Search Tool",
        slug="web-search",
        category=ToolCategory.API,
        description="Search the web",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,  # Use actual user
        requires_approval=False,
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def test_user(db_session):
    """Create a test user."""
    user = User(
        id=uuid4(),
        username="testuser",
        email="test@example.com",
        password_hash="hash",
        role=UserRole.USER.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def admin_user(db_session):
    """Create an admin user."""
    user = User(
        id=uuid4(),
        username="adminuser",
        email="admin@example.com",
        password_hash="hash",
        role=UserRole.ADMIN.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


# =============================================================================
# Test: requires_approval check
# =============================================================================

@pytest.mark.asyncio
async def test_requires_approval_true(db_session, approval_tool):
    """Test checking a tool that requires approval."""
    service = ToolApprovalService(db_session)
    result = await service.requires_approval(approval_tool.id)
    assert result is True


@pytest.mark.asyncio
async def test_requires_approval_false(db_session, regular_tool):
    """Test checking a tool that doesn't require approval."""
    service = ToolApprovalService(db_session)
    result = await service.requires_approval(regular_tool.id)
    assert result is False


@pytest.mark.asyncio
async def test_requires_approval_nonexistent(db_session):
    """Test checking a nonexistent tool."""
    service = ToolApprovalService(db_session)
    result = await service.requires_approval(uuid4())
    assert result is False


# =============================================================================
# Test: Creating approval requests
# =============================================================================

@pytest.mark.asyncio
async def test_create_request_basic(db_session, approval_tool, test_user):
    """Test creating a basic approval request."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={"amount": 100, "destination": "account123"},
        reason="Need to transfer funds for project expenses",
    )
    
    assert request.id is not None
    assert request.tool_id == approval_tool.id
    assert request.requested_by_id == test_user.id
    assert request.status == ApprovalStatus.PENDING
    assert request.urgency == ApprovalUrgency.HIGH  # From tool default
    assert request.parameters == {"amount": 100, "destination": "account123"}
    assert request.expires_at is not None


@pytest.mark.asyncio
async def test_create_request_with_urgency_override(db_session, approval_tool, test_user):
    """Test creating request with urgency override."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Urgent transfer needed",
        urgency=ApprovalUrgency.CRITICAL,
    )
    
    assert request.urgency == ApprovalUrgency.CRITICAL


@pytest.mark.asyncio
async def test_create_request_with_all_fields(db_session, approval_tool, test_user):
    """Test creating request with all optional fields."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={"amount": 500},
        reason="Monthly vendor payment",
        urgency=ApprovalUrgency.MEDIUM,
        expected_outcome="Funds transferred to vendor account",
        risk_assessment="Low risk - recurring payment to known vendor",
        estimated_cost=5.00,
        expires_in=timedelta(hours=12),
    )
    
    assert request.expected_outcome == "Funds transferred to vendor account"
    assert request.risk_assessment is not None
    assert request.estimated_cost == 5.00
    # Check expiry is approximately 12 hours from now
    expected_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
    # Make both timezone aware for comparison
    expires_at = request.expires_at
    if expires_at.tzinfo is None:
        from datetime import timezone as tz
        expires_at = expires_at.replace(tzinfo=tz.utc)
    assert abs((expires_at - expected_expiry).total_seconds()) < 60


# =============================================================================
# Test: Approving/Rejecting requests
# =============================================================================

@pytest.mark.asyncio
async def test_approve_request(db_session, approval_tool, test_user, admin_user):
    """Test approving a request."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test approval",
    )
    
    approved = await service.approve(
        request_id=request.id,
        reviewer_id=admin_user.id,
        notes="Looks good, approved.",
    )
    
    assert approved.status == ApprovalStatus.APPROVED
    assert approved.reviewed_by_id == admin_user.id
    assert approved.reviewed_at is not None
    assert approved.review_notes == "Looks good, approved."


@pytest.mark.asyncio
async def test_reject_request(db_session, approval_tool, test_user, admin_user):
    """Test rejecting a request."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test rejection",
    )
    
    rejected = await service.reject(
        request_id=request.id,
        reviewer_id=admin_user.id,
        notes="Amount exceeds limit.",
    )
    
    assert rejected.status == ApprovalStatus.REJECTED
    assert rejected.reviewed_by_id == admin_user.id
    assert rejected.review_notes == "Amount exceeds limit."


@pytest.mark.asyncio
async def test_approve_already_reviewed(db_session, approval_tool, test_user, admin_user):
    """Test approving an already reviewed request."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    
    await service.approve(request.id, admin_user.id)
    
    with pytest.raises(ApprovalAlreadyReviewedError):
        await service.approve(request.id, admin_user.id)


@pytest.mark.asyncio
async def test_reject_already_reviewed(db_session, approval_tool, test_user, admin_user):
    """Test rejecting an already reviewed request."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    
    await service.reject(request.id, admin_user.id)
    
    with pytest.raises(ApprovalAlreadyReviewedError):
        await service.reject(request.id, admin_user.id)


@pytest.mark.asyncio
async def test_approve_not_found(db_session, admin_user):
    """Test approving a nonexistent request."""
    service = ToolApprovalService(db_session)
    
    with pytest.raises(ApprovalNotFoundError):
        await service.approve(uuid4(), admin_user.id)


# =============================================================================
# Test: Cancellation
# =============================================================================

@pytest.mark.asyncio
async def test_cancel_request(db_session, approval_tool, test_user):
    """Test cancelling a request by requester."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test cancellation",
    )
    
    cancelled = await service.cancel(request.id, test_user.id)
    assert cancelled.status == ApprovalStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_by_non_requester(db_session, approval_tool, test_user, admin_user):
    """Test that non-requester cannot cancel."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    
    from app.services.tool_approval_service import ApprovalServiceError
    with pytest.raises(ApprovalServiceError, match="Only the requester"):
        await service.cancel(request.id, admin_user.id)


# =============================================================================
# Test: Expiration
# =============================================================================

@pytest.mark.asyncio
async def test_approve_expired_request(db_session, approval_tool, test_user, admin_user):
    """Test approving an expired request."""
    service = ToolApprovalService(db_session)
    
    # Create request with very short expiry
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test expiry",
        expires_in=timedelta(seconds=-1),  # Already expired
    )
    
    with pytest.raises(ApprovalExpiredError):
        await service.approve(request.id, admin_user.id)


@pytest.mark.asyncio
async def test_is_expired(db_session, approval_tool, test_user):
    """Test is_expired method."""
    service = ToolApprovalService(db_session)
    
    # Create request with past expiry
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
        expires_in=timedelta(seconds=-10),
    )
    
    assert request.is_expired() is True


# =============================================================================
# Test: Queries
# =============================================================================

@pytest.mark.asyncio
async def test_list_pending(db_session, approval_tool, test_user):
    """Test listing pending requests."""
    service = ToolApprovalService(db_session)
    
    # Create several requests with different urgencies
    for urgency in [ApprovalUrgency.LOW, ApprovalUrgency.MEDIUM, ApprovalUrgency.HIGH, ApprovalUrgency.CRITICAL]:
        await service.create_request(
            tool_id=approval_tool.id,
            user_id=test_user.id,
            parameters={},
            reason=f"Test {urgency.value}",
            urgency=urgency,
        )
    
    requests, total = await service.list_pending()
    
    assert total == 4
    assert len(requests) == 4
    # Should be ordered by urgency (CRITICAL first)
    assert requests[0].urgency == ApprovalUrgency.CRITICAL


@pytest.mark.asyncio
async def test_list_pending_with_filter(db_session, approval_tool, test_user):
    """Test listing pending requests with urgency filter."""
    service = ToolApprovalService(db_session)
    
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="High urgency",
        urgency=ApprovalUrgency.HIGH,
    )
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Low urgency",
        urgency=ApprovalUrgency.LOW,
    )
    
    requests, total = await service.list_pending(urgency=ApprovalUrgency.HIGH)
    
    assert total == 1
    assert requests[0].urgency == ApprovalUrgency.HIGH


@pytest.mark.asyncio
async def test_list_by_user(db_session, approval_tool, test_user, admin_user):
    """Test listing requests by user."""
    service = ToolApprovalService(db_session)
    
    # Create requests for test_user
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="User request 1",
    )
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="User request 2",
    )
    
    requests, total = await service.list_by_user(test_user.id)
    
    assert total == 2
    assert all(r.requested_by_id == test_user.id for r in requests)


@pytest.mark.asyncio
async def test_get_pending_count(db_session, approval_tool, test_user):
    """Test getting pending count by urgency."""
    service = ToolApprovalService(db_session)
    
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Critical",
        urgency=ApprovalUrgency.CRITICAL,
    )
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="High 1",
        urgency=ApprovalUrgency.HIGH,
    )
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="High 2",
        urgency=ApprovalUrgency.HIGH,
    )
    
    counts = await service.get_pending_count()
    
    assert counts["critical"] == 1
    assert counts["high"] == 2
    assert counts["medium"] == 0
    assert counts["low"] == 0
    assert counts["total"] == 3


# =============================================================================
# Test: Check has approval
# =============================================================================

@pytest.mark.asyncio
async def test_check_has_approval_approved(db_session, approval_tool, test_user, admin_user):
    """Test checking for existing approval."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    await service.approve(request.id, admin_user.id)
    
    found = await service.check_has_approval(approval_tool.id, test_user.id)
    
    assert found is not None
    assert found.id == request.id


@pytest.mark.asyncio
async def test_check_has_approval_pending(db_session, approval_tool, test_user):
    """Test checking when only pending request exists."""
    service = ToolApprovalService(db_session)
    
    await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    
    # Should not find pending requests
    found = await service.check_has_approval(approval_tool.id, test_user.id)
    assert found is None


@pytest.mark.asyncio
async def test_check_has_approval_already_used(db_session, approval_tool, test_user, admin_user):
    """Test checking when approval was already used."""
    service = ToolApprovalService(db_session)
    
    request = await service.create_request(
        tool_id=approval_tool.id,
        user_id=test_user.id,
        parameters={},
        reason="Test",
    )
    await service.approve(request.id, admin_user.id)
    
    # Create a real execution record
    execution = ToolExecution(
        id=uuid4(),
        tool_id=approval_tool.id,
        status=ToolExecutionStatus.COMPLETED,
        input_params={},
    )
    db_session.add(execution)
    await db_session.commit()
    
    # Link to the real execution
    await service.link_execution(request.id, execution.id)
    
    # Should not find used approvals
    found = await service.check_has_approval(approval_tool.id, test_user.id)
    assert found is None
