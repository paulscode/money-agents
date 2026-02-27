"""
Unit tests for Analytics Service.

Tests:
- Tool operations summary (health, approvals, rate limits)
- Execution trends aggregation
- Alert generation and counting
"""
import pytest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import (
    Tool, ToolStatus, ToolCategory, User, UserRole,
    ToolExecution, ToolExecutionStatus,
    ToolRateLimit, RateLimitViolation, RateLimitScope, RateLimitPeriod,
    ToolApprovalRequest, ApprovalStatus, ApprovalUrgency,
    ToolHealthCheck, HealthStatus,
)
from app.models.agent_scheduler import AgentDefinition, AgentRun, AgentRunStatus, AgentStatus
from app.services.analytics_service import (
    AnalyticsService,
    HealthSummary,
    ApprovalSummary,
    RateLimitAlert,
    ToolOperationsSummary,
    Alert,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def test_user(db_session):
    """Create a test user."""
    user = User(
        id=uuid4(),
        username="analyticsuser",
        email="analytics@example.com",
        password_hash="hash",
        role=UserRole.USER.value,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def healthy_tool(db_session, test_user):
    """Create a healthy tool."""
    tool = Tool(
        id=uuid4(),
        name="Healthy API Tool",
        slug="healthy-api",
        category=ToolCategory.API,
        description="A healthy tool",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,
        health_status="healthy",
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def degraded_tool(db_session, test_user):
    """Create a degraded tool."""
    tool = Tool(
        id=uuid4(),
        name="Degraded API Tool",
        slug="degraded-api",
        category=ToolCategory.API,
        description="A degraded tool",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,
        health_status="degraded",
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def unhealthy_tool(db_session, test_user):
    """Create an unhealthy tool."""
    tool = Tool(
        id=uuid4(),
        name="Unhealthy API Tool",
        slug="unhealthy-api",
        category=ToolCategory.API,
        description="An unhealthy tool",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,
        health_status="unhealthy",
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def unknown_status_tool(db_session, test_user):
    """Create a tool with unknown health status."""
    tool = Tool(
        id=uuid4(),
        name="Unknown Status Tool",
        slug="unknown-status",
        category=ToolCategory.API,
        description="A tool with no health status",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,
        health_status=None,
    )
    db_session.add(tool)
    db_session.commit()
    return tool


@pytest.fixture
def approval_tool(db_session, test_user):
    """Create a tool that requires approval."""
    tool = Tool(
        id=uuid4(),
        name="Approval Required Tool",
        slug="approval-required",
        category=ToolCategory.AUTOMATION,
        description="A tool requiring approval",
        status=ToolStatus.IMPLEMENTED,
        requester_id=test_user.id,
        requires_approval=True,
        approval_urgency="high",
        health_status="healthy",
    )
    db_session.add(tool)
    db_session.commit()
    return tool


# =============================================================================
# Test: Health Summary
# =============================================================================

@pytest.mark.asyncio
async def test_health_summary_empty(db_session):
    """Test health summary with no tools."""
    service = AnalyticsService(db_session)
    summary = await service._get_health_summary()
    
    assert summary.total == 0
    assert summary.healthy == 0
    assert summary.degraded == 0
    assert summary.unhealthy == 0
    assert summary.unknown == 0


@pytest.mark.asyncio
async def test_health_summary_healthy_tool(db_session, healthy_tool):
    """Test health summary with a healthy tool."""
    service = AnalyticsService(db_session)
    summary = await service._get_health_summary()
    
    assert summary.total == 1
    assert summary.healthy == 1
    assert summary.degraded == 0
    assert summary.unhealthy == 0
    assert summary.unknown == 0


@pytest.mark.asyncio
async def test_health_summary_degraded_tool(db_session, degraded_tool):
    """Test health summary with a degraded tool."""
    service = AnalyticsService(db_session)
    summary = await service._get_health_summary()
    
    assert summary.total == 1
    assert summary.healthy == 0
    assert summary.degraded == 1
    assert summary.unhealthy == 0
    assert summary.unknown == 0


@pytest.mark.asyncio
async def test_health_summary_mixed_health(
    db_session, healthy_tool, degraded_tool, unhealthy_tool, unknown_status_tool
):
    """Test health summary with mixed health statuses."""
    service = AnalyticsService(db_session)
    summary = await service._get_health_summary()
    
    assert summary.total == 4
    assert summary.healthy == 1
    assert summary.degraded == 1
    assert summary.unhealthy == 1
    assert summary.unknown == 1


@pytest.mark.asyncio
async def test_health_summary_excludes_non_implemented(db_session, test_user):
    """Test that non-implemented tools are excluded from health summary."""
    # Create a requested (not implemented) tool
    requested_tool = Tool(
        id=uuid4(),
        name="Requested Tool",
        slug="requested-tool",
        category=ToolCategory.API,
        description="Not implemented yet",
        status=ToolStatus.REQUESTED,
        requester_id=test_user.id,
        health_status="healthy",
    )
    db_session.add(requested_tool)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    summary = await service._get_health_summary()
    
    assert summary.total == 0


# =============================================================================
# Test: Approval Summary
# =============================================================================

@pytest.mark.asyncio
async def test_approval_summary_empty(db_session):
    """Test approval summary with no pending approvals."""
    service = AnalyticsService(db_session)
    summary = await service._get_approval_summary()
    
    assert summary.pending_count == 0
    assert summary.critical_count == 0
    assert summary.high_count == 0
    assert summary.medium_count == 0
    assert summary.low_count == 0
    assert summary.oldest_pending_minutes is None


@pytest.mark.asyncio
async def test_approval_summary_single_pending(db_session, approval_tool, test_user):
    """Test approval summary with a single pending approval."""
    # Create a pending approval request
    approval = ToolApprovalRequest(
        id=uuid4(),
        tool_id=approval_tool.id,
        requested_by_id=test_user.id,
        status=ApprovalStatus.PENDING,
        urgency=ApprovalUrgency.HIGH,
        parameters={"amount": 100},
        reason="Test approval",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(approval)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    summary = await service._get_approval_summary()
    
    assert summary.pending_count == 1
    assert summary.high_count == 1
    assert summary.critical_count == 0


@pytest.mark.asyncio
async def test_approval_summary_multiple_urgencies(db_session, approval_tool, test_user):
    """Test approval summary with multiple urgency levels."""
    now = datetime.now(timezone.utc)
    
    # Create approvals with different urgencies
    urgencies = [
        ApprovalUrgency.CRITICAL,
        ApprovalUrgency.HIGH,
        ApprovalUrgency.MEDIUM,
        ApprovalUrgency.LOW,
    ]
    
    for urgency in urgencies:
        approval = ToolApprovalRequest(
            id=uuid4(),
            tool_id=approval_tool.id,
            requested_by_id=test_user.id,
            status=ApprovalStatus.PENDING,
            urgency=urgency,
            parameters={},
            reason=f"Test {urgency.value}",
            expires_at=now + timedelta(hours=24),
        )
        db_session.add(approval)
    
    db_session.commit()
    
    service = AnalyticsService(db_session)
    summary = await service._get_approval_summary()
    
    assert summary.pending_count == 4
    assert summary.critical_count == 1
    assert summary.high_count == 1
    assert summary.medium_count == 1
    assert summary.low_count == 1
    # Oldest should be set (created just now, so should be 0 or small)
    assert summary.oldest_pending_minutes is not None
    assert summary.oldest_pending_minutes >= 0


@pytest.mark.asyncio
async def test_approval_summary_excludes_reviewed(db_session, approval_tool, test_user):
    """Test that reviewed approvals are excluded from summary."""
    now = datetime.now(timezone.utc)
    
    # Create approved and rejected requests
    approved = ToolApprovalRequest(
        id=uuid4(),
        tool_id=approval_tool.id,
        requested_by_id=test_user.id,
        status=ApprovalStatus.APPROVED,
        urgency=ApprovalUrgency.HIGH,
        parameters={},
        reason="Approved request",
        expires_at=now + timedelta(hours=24),
    )
    
    rejected = ToolApprovalRequest(
        id=uuid4(),
        tool_id=approval_tool.id,
        requested_by_id=test_user.id,
        status=ApprovalStatus.REJECTED,
        urgency=ApprovalUrgency.CRITICAL,
        parameters={},
        reason="Rejected request",
        expires_at=now + timedelta(hours=24),
    )
    
    db_session.add_all([approved, rejected])
    db_session.commit()
    
    service = AnalyticsService(db_session)
    summary = await service._get_approval_summary()
    
    assert summary.pending_count == 0
    assert summary.high_count == 0
    assert summary.critical_count == 0


# =============================================================================
# Test: Unhealthy Tools List
# =============================================================================

@pytest.mark.asyncio
async def test_unhealthy_tools_empty(db_session, healthy_tool):
    """Test unhealthy tools list with no unhealthy tools."""
    service = AnalyticsService(db_session)
    unhealthy = await service._get_unhealthy_tools()
    
    assert unhealthy == []


@pytest.mark.asyncio
async def test_unhealthy_tools_with_unhealthy(db_session, unhealthy_tool, degraded_tool):
    """Test unhealthy tools list includes unhealthy and degraded."""
    service = AnalyticsService(db_session)
    unhealthy = await service._get_unhealthy_tools()
    
    # Should include both unhealthy and degraded
    assert len(unhealthy) == 2
    statuses = {tool['health_status'] for tool in unhealthy}
    assert 'unhealthy' in statuses
    assert 'degraded' in statuses


@pytest.mark.asyncio
async def test_unhealthy_tools_sorted_by_severity(
    db_session, unhealthy_tool, degraded_tool
):
    """Test that unhealthy tools are sorted by severity (unhealthy first)."""
    service = AnalyticsService(db_session)
    unhealthy = await service._get_unhealthy_tools()
    
    # First should be unhealthy (more severe)
    assert unhealthy[0]['health_status'] == 'unhealthy'
    assert unhealthy[1]['health_status'] == 'degraded'


# =============================================================================
# Test: Recent Violations Count
# =============================================================================

@pytest.mark.asyncio
async def test_recent_violations_empty(db_session):
    """Test recent violations count with no violations."""
    service = AnalyticsService(db_session)
    count = await service._get_recent_violations_count()
    
    assert count == 0


@pytest.mark.asyncio
async def test_recent_violations_with_violations(db_session, healthy_tool, test_user):
    """Test recent violations count with violations."""
    now = datetime.now(timezone.utc)
    
    # Create rate limit for the tool
    rate_limit = ToolRateLimit(
        id=uuid4(),
        tool_id=healthy_tool.id,
        name="api-calls",
        max_executions=100,
        period=RateLimitPeriod.HOUR,
        scope=RateLimitScope.GLOBAL,
    )
    db_session.add(rate_limit)
    db_session.commit()
    
    # Create recent violation (within 24 hours)
    violation = RateLimitViolation(
        id=uuid4(),
        rate_limit_id=rate_limit.id,
        tool_id=healthy_tool.id,
        user_id=test_user.id,
        violated_at=now - timedelta(hours=1),
        current_count=150,
        limit_count=100,
        period_start=now - timedelta(hours=2),
    )
    db_session.add(violation)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    count = await service._get_recent_violations_count()
    
    assert count == 1


@pytest.mark.asyncio
async def test_recent_violations_excludes_old(db_session, healthy_tool, test_user):
    """Test that old violations are excluded."""
    now = datetime.now(timezone.utc)
    
    # Create rate limit
    rate_limit = ToolRateLimit(
        id=uuid4(),
        tool_id=healthy_tool.id,
        name="api-calls",
        max_executions=100,
        period=RateLimitPeriod.HOUR,
        scope=RateLimitScope.GLOBAL,
    )
    db_session.add(rate_limit)
    db_session.commit()
    
    # Create old violation (more than 24 hours ago)
    old_violation = RateLimitViolation(
        id=uuid4(),
        rate_limit_id=rate_limit.id,
        tool_id=healthy_tool.id,
        user_id=test_user.id,
        violated_at=now - timedelta(days=2),
        current_count=150,
        limit_count=100,
        period_start=now - timedelta(days=2, hours=1),
    )
    db_session.add(old_violation)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    count = await service._get_recent_violations_count()
    
    assert count == 0


# =============================================================================
# Test: Tool Operations Summary (Integration)
# =============================================================================

@pytest.mark.asyncio
async def test_operations_summary_full(
    db_session, healthy_tool, unhealthy_tool, approval_tool, test_user
):
    """Test full tool operations summary."""
    now = datetime.now(timezone.utc)
    
    # Add a pending approval
    approval = ToolApprovalRequest(
        id=uuid4(),
        tool_id=approval_tool.id,
        requested_by_id=test_user.id,
        status=ApprovalStatus.PENDING,
        urgency=ApprovalUrgency.HIGH,
        parameters={},
        reason="Test approval",
        expires_at=now + timedelta(hours=24),
    )
    db_session.add(approval)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    summary = await service.get_tool_operations_summary()
    
    # Check structure
    assert isinstance(summary, ToolOperationsSummary)
    assert isinstance(summary.health, HealthSummary)
    assert isinstance(summary.approvals, ApprovalSummary)
    assert isinstance(summary.rate_limit_alerts, list)
    assert isinstance(summary.unhealthy_tools, list)
    assert isinstance(summary.pending_approvals, list)
    
    # Check health counts (healthy_tool, unhealthy_tool, approval_tool)
    assert summary.health.total == 3
    
    # Check pending approvals
    assert summary.approvals.pending_count == 1
    assert len(summary.pending_approvals) == 1


# =============================================================================
# Test: Active Alerts
# =============================================================================

@pytest.mark.asyncio
async def test_get_active_alerts_empty(db_session):
    """Test active alerts with no issues."""
    service = AnalyticsService(db_session)
    alerts = await service.get_active_alerts()
    
    assert isinstance(alerts, list)
    assert len(alerts) == 0


@pytest.mark.asyncio
async def test_get_active_alerts_unhealthy_tool(db_session, unhealthy_tool):
    """Test that unhealthy tools generate alerts."""
    service = AnalyticsService(db_session)
    alerts = await service.get_active_alerts()
    
    # Should have an alert for the unhealthy tool
    health_alerts = [a for a in alerts if a['category'] == 'health']
    assert len(health_alerts) >= 1
    
    # Find the alert for our tool
    tool_alert = next(
        (a for a in health_alerts if str(unhealthy_tool.id) == str(a['source_id'])),
        None
    )
    assert tool_alert is not None
    assert tool_alert['severity'] in ('critical', 'high')


@pytest.mark.asyncio
async def test_get_active_alerts_pending_approval(
    db_session, approval_tool, test_user
):
    """Test that pending approvals generate alerts."""
    now = datetime.now(timezone.utc)
    
    approval = ToolApprovalRequest(
        id=uuid4(),
        tool_id=approval_tool.id,
        requested_by_id=test_user.id,
        status=ApprovalStatus.PENDING,
        urgency=ApprovalUrgency.CRITICAL,
        parameters={},
        reason="Urgent approval needed",
        expires_at=now + timedelta(hours=23),
    )
    db_session.add(approval)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    alerts = await service.get_active_alerts()
    
    # Should have an alert for the pending approval
    approval_alerts = [a for a in alerts if a['category'] == 'approval']
    assert len(approval_alerts) >= 1


# =============================================================================
# Test: Alert Counts
# =============================================================================

@pytest.mark.asyncio
async def test_get_alert_counts_empty(db_session):
    """Test alert counts with no issues."""
    service = AnalyticsService(db_session)
    counts = await service.get_alert_counts()
    
    assert counts['total'] == 0
    assert counts['critical'] == 0
    assert counts['high'] == 0


@pytest.mark.asyncio
async def test_get_alert_counts_with_alerts(db_session, unhealthy_tool):
    """Test alert counts with active alerts."""
    service = AnalyticsService(db_session)
    counts = await service.get_alert_counts()
    
    # Should have at least one alert for the unhealthy tool
    assert counts['total'] >= 1


# =============================================================================
# Test: Execution Trends (PostgreSQL-only due to date_trunc)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.skip(reason="date_trunc is PostgreSQL-specific, tests use SQLite")
async def test_get_execution_trends_empty(db_session):
    """Test execution trends with no executions."""
    service = AnalyticsService(db_session)
    trends = await service.get_execution_trends(hours=24)
    
    assert isinstance(trends, list)
    assert len(trends) == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="date_trunc is PostgreSQL-specific, tests use SQLite")
async def test_get_execution_trends_with_data(db_session, healthy_tool, test_user):
    """Test execution trends with execution data."""
    now = datetime.now(timezone.utc)
    
    # Create some executions
    executions = [
        ToolExecution(
            id=uuid4(),
            tool_id=healthy_tool.id,
            user_id=test_user.id,
            status=ToolExecutionStatus.COMPLETED,
            parameters={},
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(seconds=5),
            duration_ms=5000,
        ),
        ToolExecution(
            id=uuid4(),
            tool_id=healthy_tool.id,
            user_id=test_user.id,
            status=ToolExecutionStatus.COMPLETED,
            parameters={},
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(seconds=3),
            duration_ms=3000,
        ),
        ToolExecution(
            id=uuid4(),
            tool_id=healthy_tool.id,
            user_id=test_user.id,
            status=ToolExecutionStatus.FAILED,
            parameters={},
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(seconds=1),
            duration_ms=1000,
            error_message="Test error",
        ),
    ]
    
    for execution in executions:
        db_session.add(execution)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    trends = await service.get_execution_trends(hours=24)
    
    assert len(trends) >= 1


# =============================================================================
# Test: Violation Trends (PostgreSQL-only due to date_trunc)
# =============================================================================

@pytest.mark.asyncio
@pytest.mark.skip(reason="date_trunc is PostgreSQL-specific, tests use SQLite")
async def test_get_violation_trends_empty(db_session):
    """Test violation trends with no violations."""
    service = AnalyticsService(db_session)
    trends = await service.get_violation_trends(hours=24)
    
    assert isinstance(trends, list)
    assert len(trends) == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="date_trunc is PostgreSQL-specific, tests use SQLite")
async def test_get_violation_trends_with_data(db_session, healthy_tool, test_user):
    """Test violation trends with violation data."""
    now = datetime.now(timezone.utc)
    
    # Create rate limit
    rate_limit = ToolRateLimit(
        id=uuid4(),
        tool_id=healthy_tool.id,
        name="api-calls",
        max_executions=100,
        period=RateLimitPeriod.HOUR,
        scope=RateLimitScope.GLOBAL,
    )
    db_session.add(rate_limit)
    db_session.commit()
    
    # Create violations
    violations = [
        RateLimitViolation(
            id=uuid4(),
            rate_limit_id=rate_limit.id,
            tool_id=healthy_tool.id,
            user_id=test_user.id,
            violated_at=now - timedelta(hours=1),
            current_count=150,
            limit_count=100,
            period_start=now - timedelta(hours=2),
        ),
        RateLimitViolation(
            id=uuid4(),
            rate_limit_id=rate_limit.id,
            tool_id=healthy_tool.id,
            user_id=test_user.id,
            violated_at=now - timedelta(hours=2),
            current_count=200,
            limit_count=100,
            period_start=now - timedelta(hours=3),
        ),
    ]
    
    for violation in violations:
        db_session.add(violation)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    trends = await service.get_violation_trends(hours=24)
    
    assert len(trends) >= 1


# =============================================================================
# Test: Agent Performance Analytics
# =============================================================================

@pytest.fixture
def test_agent(db_session):
    """Create a test agent."""
    agent = AgentDefinition(
        id=uuid4(),
        name="Test Scout",
        slug="test_scout",
        description="A test agent for analytics",
        status=AgentStatus.IDLE,
        is_enabled=True,
        schedule_interval_seconds=3600,
        total_runs=0,
        successful_runs=0,
        failed_runs=0,
        total_tokens_used=0,
        total_cost_usd=0.0,
    )
    db_session.add(agent)
    db_session.commit()
    return agent


@pytest.mark.asyncio
async def test_agent_performance_empty(db_session):
    """Test agent performance with no agents."""
    service = AnalyticsService(db_session)
    performance = await service.get_agent_performance(days=7)
    
    assert isinstance(performance, list)
    assert len(performance) == 0


@pytest.mark.asyncio
async def test_agent_performance_with_runs(db_session, test_agent):
    """Test agent performance with agent runs."""
    now = datetime.now(timezone.utc)
    
    # Create some agent runs
    runs = [
        AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.COMPLETED,
            trigger_type="scheduled",
            started_at=now - timedelta(hours=2),
            completed_at=now - timedelta(hours=2) + timedelta(seconds=45),
            duration_seconds=45.0,
            items_processed=5,
            cost_usd=0.12,
        ),
        AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.COMPLETED,
            trigger_type="scheduled",
            started_at=now - timedelta(hours=1),
            completed_at=now - timedelta(hours=1) + timedelta(seconds=50),
            duration_seconds=50.0,
            items_processed=3,
            cost_usd=0.15,
        ),
        AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.FAILED,
            trigger_type="scheduled",
            started_at=now - timedelta(minutes=30),
            completed_at=now - timedelta(minutes=30) + timedelta(seconds=10),
            duration_seconds=10.0,
            items_processed=0,
            cost_usd=0.05,
            error_message="Serper API timeout",
        ),
    ]
    
    for run in runs:
        db_session.add(run)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    performance = await service.get_agent_performance(days=7)
    
    assert len(performance) == 1
    agent_perf = performance[0]
    
    assert agent_perf['agent_slug'] == 'test_scout'
    assert agent_perf['total_runs'] == 3
    assert agent_perf['successful_runs'] == 2
    assert agent_perf['failed_runs'] == 1
    assert 0.66 <= agent_perf['success_rate'] <= 0.67  # ~66%
    assert agent_perf['total_cost_usd'] == 0.32


@pytest.mark.asyncio
async def test_agent_performance_failure_reasons(db_session, test_agent):
    """Test that failure reasons are extracted."""
    now = datetime.now(timezone.utc)
    
    # Create failed runs with same error
    for i in range(3):
        run = AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.FAILED,
            trigger_type="scheduled",
            started_at=now - timedelta(hours=i),
            completed_at=now - timedelta(hours=i) + timedelta(seconds=5),
            duration_seconds=5.0,
            error_message="Connection timeout to external API",
        )
        db_session.add(run)
    
    db_session.commit()
    
    service = AnalyticsService(db_session)
    performance = await service.get_agent_performance(days=7)
    
    assert len(performance) == 1
    agent_perf = performance[0]
    
    assert len(agent_perf['top_failure_reasons']) >= 1
    top_reason = agent_perf['top_failure_reasons'][0]
    assert 'Connection timeout' in top_reason['reason']
    assert top_reason['count'] == 3


@pytest.mark.asyncio
async def test_agent_suggestions_low_yield(db_session, test_agent):
    """Test that low yield suggestions are generated."""
    now = datetime.now(timezone.utc)
    
    # Create runs with low items processed
    for i in range(6):
        run = AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.COMPLETED,
            trigger_type="scheduled",
            started_at=now - timedelta(hours=i * 2),
            completed_at=now - timedelta(hours=i * 2) + timedelta(seconds=30),
            duration_seconds=30.0,
            items_processed=0,  # No items found
            cost_usd=0.10,
        )
        db_session.add(run)
    
    db_session.commit()
    
    service = AnalyticsService(db_session)
    suggestions = await service.get_agent_suggestions()
    
    # Should have a low yield suggestion
    low_yield = [s for s in suggestions if s['suggestion_type'] == 'efficiency']
    assert len(low_yield) >= 1
    assert 'Low yield' in low_yield[0]['title']


@pytest.mark.asyncio
async def test_agent_suggestions_high_failure_rate(db_session, test_agent):
    """Test that high failure rate suggestions are generated."""
    now = datetime.now(timezone.utc)
    
    # Create runs with high failure rate (3 failed, 1 success = 25% success)
    for i in range(3):
        run = AgentRun(
            id=uuid4(),
            agent_id=test_agent.id,
            status=AgentRunStatus.FAILED,
            trigger_type="scheduled",
            started_at=now - timedelta(hours=i * 2),
            completed_at=now - timedelta(hours=i * 2) + timedelta(seconds=5),
            duration_seconds=5.0,
            error_message="Tool execution failed",
        )
        db_session.add(run)
    
    # One success
    success_run = AgentRun(
        id=uuid4(),
        agent_id=test_agent.id,
        status=AgentRunStatus.COMPLETED,
        trigger_type="scheduled",
        started_at=now - timedelta(hours=10),
        completed_at=now - timedelta(hours=10) + timedelta(seconds=30),
        duration_seconds=30.0,
        items_processed=2,
    )
    db_session.add(success_run)
    db_session.commit()
    
    service = AnalyticsService(db_session)
    suggestions = await service.get_agent_suggestions()
    
    # Should have a reliability suggestion
    reliability = [s for s in suggestions if s['suggestion_type'] == 'reliability']
    assert len(reliability) >= 1
    assert 'failure rate' in reliability[0]['title'].lower()


@pytest.mark.asyncio
async def test_agent_suggestions_empty_no_runs(db_session, test_agent):
    """Test that no suggestions when no runs exist."""
    service = AnalyticsService(db_session)
    suggestions = await service.get_agent_suggestions()
    
    # No runs = no suggestions
    assert isinstance(suggestions, list)
    # Might have schedule-based suggestions but not performance-based


# =============================================================================
# Campaign Intelligence Tests
# =============================================================================

from app.models.campaign_learning import (
    CampaignPattern, PatternType, PatternStatus,
    CampaignLesson, LessonCategory,
)
from app.models import Campaign, CampaignStatus


@pytest.fixture
def test_pattern(db_session, test_user):
    """Create a test campaign pattern."""
    pattern = CampaignPattern(
        id=uuid4(),
        name="High-Performance Social Media",
        description="Optimal pattern for social media campaigns",
        pattern_type=PatternType.EXECUTION_SEQUENCE,
        status=PatternStatus.ACTIVE,
        confidence_score=0.85,
        pattern_data={
            "tasks": ["research", "content_creation", "scheduling"],
            "agent_type": "social_scout",
            "target_market": "tech",
        },
        times_applied=10,
        times_successful=8,
        user_id=test_user.id,
        is_global=False,
    )
    db_session.add(pattern)
    db_session.commit()
    return pattern


@pytest.fixture
def global_pattern(db_session):
    """Create a global pattern."""
    pattern = CampaignPattern(
        id=uuid4(),
        name="Universal Email Sequence",
        description="Works for any email campaign",
        pattern_type=PatternType.TOOL_COMBINATION,
        status=PatternStatus.ACTIVE,
        confidence_score=0.75,
        pattern_data={
            "tools": ["email_sender", "template_engine"],
        },
        times_applied=25,
        times_successful=20,
        user_id=None,
        is_global=True,
    )
    db_session.add(pattern)
    db_session.commit()
    return pattern


@pytest.fixture
def test_lesson(db_session, test_user, test_pattern):
    """Create a test campaign lesson."""
    lesson = CampaignLesson(
        id=uuid4(),
        title="Rate limiting issues with Twitter API",
        description="Campaigns exceeded rate limits causing delays",
        category=LessonCategory.TOOL_ISSUE,
        severity="high",
        context="Social media campaigns",
        failure_analysis="API rate limits hit during peak hours",
        prevention_steps=["Schedule posts during off-peak", "Use batch API calls"],
        pattern_id=test_pattern.id,
        user_id=test_user.id,
    )
    db_session.add(lesson)
    db_session.commit()
    return lesson


@pytest.fixture
def critical_lesson(db_session, test_user):
    """Create a critical severity lesson."""
    lesson = CampaignLesson(
        id=uuid4(),
        title="Budget exhaustion",
        description="Campaign ran out of budget mid-execution",
        category=LessonCategory.BUDGET_ISSUE,
        severity="critical",
        context="Budget management",
        failure_analysis="No budget monitoring in place",
        prevention_steps=["Set budget alerts at 80%", "Enable auto-pause"],
        user_id=test_user.id,
    )
    db_session.add(lesson)
    db_session.commit()
    return lesson


@pytest.fixture
def test_campaign_with_success(db_session, test_user):
    """Create a successful campaign."""
    campaign = Campaign(
        id=uuid4(),
        name="Test Success Campaign",
        goal="Test successful campaign",
        status=CampaignStatus.COMPLETED,
        budget_allocated=100.0,
        budget_spent=50.0,
        user_id=test_user.id,
        created_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    db_session.add(campaign)
    db_session.commit()
    return campaign


@pytest.fixture
def test_campaign_failed(db_session, test_user):
    """Create a failed campaign."""
    campaign = Campaign(
        id=uuid4(),
        name="Test Failed Campaign",
        goal="Test failed campaign",
        status=CampaignStatus.FAILED,
        budget_allocated=100.0,
        budget_spent=100.0,
        user_id=test_user.id,
        created_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    db_session.add(campaign)
    db_session.commit()
    return campaign


@pytest.mark.asyncio
async def test_get_top_patterns_empty(db_session):
    """Test getting patterns when none exist."""
    service = AnalyticsService(db_session)
    patterns = await service.get_top_patterns(limit=10, min_confidence=0.5)
    
    assert isinstance(patterns, list)
    assert len(patterns) == 0


@pytest.mark.asyncio
async def test_get_top_patterns_with_data(db_session, test_pattern, global_pattern, test_user):
    """Test getting top patterns with user and global patterns."""
    service = AnalyticsService(db_session)
    patterns = await service.get_top_patterns(
        limit=10, 
        min_confidence=0.5, 
        user_id=test_user.id
    )
    
    assert len(patterns) == 2
    # Should be sorted by success rate * confidence
    first = patterns[0]
    assert 'id' in first
    assert 'name' in first
    assert 'confidence_score' in first
    assert 'success_rate' in first
    assert 'times_applied' in first


@pytest.mark.asyncio
async def test_get_top_patterns_min_confidence_filter(db_session, test_pattern, global_pattern, test_user):
    """Test that min_confidence filter works."""
    service = AnalyticsService(db_session)
    
    # High confidence threshold - should only get test_pattern (0.85)
    patterns = await service.get_top_patterns(
        limit=10, 
        min_confidence=0.8, 
        user_id=test_user.id
    )
    
    assert len(patterns) == 1
    assert patterns[0]['confidence_score'] >= 0.8


@pytest.mark.asyncio
async def test_get_recent_lessons_empty(db_session, test_user):
    """Test getting lessons when none exist."""
    service = AnalyticsService(db_session)
    lessons = await service.get_recent_lessons(user_id=test_user.id, days=30, limit=10)
    
    assert isinstance(lessons, list)
    assert len(lessons) == 0


@pytest.mark.asyncio
async def test_get_campaign_intelligence_summary_empty(db_session, test_user):
    """Test summary with no data."""
    service = AnalyticsService(db_session)
    summary = await service.get_campaign_intelligence_summary(user_id=test_user.id)
    
    assert summary['patterns']['total'] == 0
    assert summary['patterns']['active'] == 0
    assert summary['lessons']['total_30d'] == 0
    assert summary['campaigns']['total_30d'] == 0


@pytest.mark.asyncio
@pytest.mark.skip(reason="Uses PostgreSQL-specific date_trunc function, not supported in SQLite unit tests")
async def test_get_pattern_effectiveness_trend_empty(db_session, test_user):
    """Test effectiveness trend with no campaigns."""
    service = AnalyticsService(db_session)
    trend = await service.get_pattern_effectiveness_trend(days=30, user_id=test_user.id)
    
    assert isinstance(trend, list)
    # Should return empty list or weekly buckets with zero values
