"""
Analytics Service - Aggregates operational metrics for dashboards.

Provides:
- Tool operations summary (health, rate limits, approvals)
- Tool execution trends (hourly buckets)
- Active alerts requiring attention
- Agent performance metrics
- Resource utilization data
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, func, and_, or_, case, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_utils import utc_now, ensure_utc
from app.models import (
    Tool,
    ToolExecution,
    ToolExecutionStatus,
    ToolRateLimit,
    RateLimitViolation,
    RateLimitScope,
    RateLimitPeriod,
    ToolApprovalRequest,
    ApprovalStatus,
    ApprovalUrgency,
    ToolHealthCheck,
    HealthStatus,
)
from app.models.agent_scheduler import AgentDefinition, AgentRun, AgentRunStatus
from app.models.resource import Resource, JobQueue

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes for Responses
# =============================================================================

@dataclass
class HealthSummary:
    """Summary of tool health statuses."""
    healthy: int = 0
    degraded: int = 0
    unhealthy: int = 0
    unknown: int = 0
    total: int = 0


@dataclass
class ApprovalSummary:
    """Summary of pending approvals."""
    pending_count: int = 0
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    oldest_pending_minutes: Optional[int] = None


@dataclass
class RateLimitAlert:
    """Alert for rate limit approaching threshold."""
    tool_id: UUID
    tool_name: str
    tool_slug: str
    limit_name: str
    current_usage: int
    max_allowed: int
    usage_percent: float
    period: str
    period_resets_at: Optional[datetime] = None


@dataclass
class ToolOperationsSummary:
    """Complete tool operations summary."""
    health: HealthSummary
    approvals: ApprovalSummary
    rate_limit_alerts: List[RateLimitAlert]
    unhealthy_tools: List[Dict[str, Any]]
    recent_violations_count: int
    pending_approvals: List[Dict[str, Any]]


@dataclass
class ExecutionTrendPoint:
    """Single point in execution trend data."""
    hour: str  # ISO format hour
    tool_slug: str
    execution_count: int
    success_count: int
    failure_count: int
    avg_duration_ms: float


@dataclass
class Alert:
    """Active alert requiring attention."""
    id: str  # Unique alert ID
    severity: str  # critical, high, medium, low
    category: str  # health, approval, rate_limit, budget, campaign
    title: str
    message: str
    source_type: str  # tool, agent, campaign, resource
    source_id: Optional[UUID] = None
    source_name: Optional[str] = None
    action_url: Optional[str] = None
    created_at: Optional[datetime] = None


@dataclass
class AgentPerformanceMetrics:
    """Performance metrics for a single agent."""
    agent_id: UUID
    agent_slug: str
    agent_name: str
    total_runs: int
    successful_runs: int
    failed_runs: int
    success_rate: float  # 0.0 to 1.0
    avg_duration_seconds: float
    avg_cost_usd: float
    total_cost_usd: float
    avg_items_processed: float
    # Failure analysis
    top_failure_reasons: List[Dict[str, Any]] = field(default_factory=list)


@dataclass 
class CostTrendPoint:
    """Single point in cost trend data."""
    date: str  # YYYY-MM-DD
    agent_slug: str
    agent_name: str
    total_cost_usd: float
    run_count: int


@dataclass
class AgentSuggestion:
    """AI-generated optimization suggestion for an agent."""
    agent_slug: str
    agent_name: str
    suggestion_type: str  # efficiency, cost, schedule, reliability
    severity: str  # info, warning, recommendation
    title: str
    description: str
    potential_savings: Optional[str] = None  # e.g., "$1.68/week"
    action: Optional[str] = None  # What to do


# =============================================================================
# Analytics Service
# =============================================================================

class AnalyticsService:
    """
    Service for aggregating analytics data across the system.
    
    Focuses on surfacing actionable insights with minimal manual checking.
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # Tool Operations Summary
    # =========================================================================
    
    async def get_tool_operations_summary(self) -> ToolOperationsSummary:
        """
        Get comprehensive tool operations summary.
        
        Includes:
        - Health status counts
        - Pending approvals by urgency
        - Rate limits near threshold
        - Unhealthy tools list
        - Recent violation count
        """
        health = await self._get_health_summary()
        approvals = await self._get_approval_summary()
        rate_limit_alerts = await self._get_rate_limit_alerts()
        unhealthy_tools = await self._get_unhealthy_tools()
        recent_violations = await self._get_recent_violations_count()
        pending_approvals = await self._get_pending_approvals()
        
        return ToolOperationsSummary(
            health=health,
            approvals=approvals,
            rate_limit_alerts=rate_limit_alerts,
            unhealthy_tools=unhealthy_tools,
            recent_violations_count=recent_violations,
            pending_approvals=pending_approvals,
        )
    
    async def _get_health_summary(self) -> HealthSummary:
        """Get health status counts for all tools."""
        result = await self.db.execute(
            select(
                Tool.health_status,
                func.count(Tool.id).label('count')
            )
            .where(Tool.status == 'implemented')  # Only active tools
            .group_by(Tool.health_status)
        )
        
        summary = HealthSummary()
        for row in result:
            status = row.health_status or 'unknown'
            count = row.count
            if status == 'healthy':
                summary.healthy = count
            elif status == 'degraded':
                summary.degraded = count
            elif status == 'unhealthy':
                summary.unhealthy = count
            else:
                summary.unknown = count
        
        summary.total = summary.healthy + summary.degraded + summary.unhealthy + summary.unknown
        return summary
    
    async def _get_approval_summary(self) -> ApprovalSummary:
        """Get pending approval counts by urgency."""
        result = await self.db.execute(
            select(
                ToolApprovalRequest.urgency,
                func.count(ToolApprovalRequest.id).label('count'),
                func.min(ToolApprovalRequest.created_at).label('oldest')
            )
            .where(ToolApprovalRequest.status == ApprovalStatus.PENDING)
            .group_by(ToolApprovalRequest.urgency)
        )
        
        summary = ApprovalSummary()
        oldest_time = None
        
        for row in result:
            urgency = row.urgency
            count = row.count
            
            summary.pending_count += count
            
            if urgency == ApprovalUrgency.CRITICAL:
                summary.critical_count = count
            elif urgency == ApprovalUrgency.HIGH:
                summary.high_count = count
            elif urgency == ApprovalUrgency.MEDIUM:
                summary.medium_count = count
            elif urgency == ApprovalUrgency.LOW:
                summary.low_count = count
            
            if row.oldest:
                if oldest_time is None or row.oldest < oldest_time:
                    oldest_time = row.oldest
        
        if oldest_time:
            now = utc_now()
            oldest_time = ensure_utc(oldest_time)
            delta = now - oldest_time
            summary.oldest_pending_minutes = int(delta.total_seconds() / 60)
        
        return summary
    
    async def _get_rate_limit_alerts(self, threshold: float = 0.8) -> List[RateLimitAlert]:
        """Get rate limits that are at or above the threshold percentage."""
        # Get all active rate limits with tool info
        result = await self.db.execute(
            select(
                ToolRateLimit,
                Tool.name.label('tool_name'),
                Tool.slug.label('tool_slug'),
            )
            .join(Tool, ToolRateLimit.tool_id == Tool.id, isouter=True)
            .where(ToolRateLimit.is_active == True)
        )
        
        alerts = []
        now = utc_now()
        
        for row in result:
            limit = row.ToolRateLimit
            tool_name = row.tool_name or 'Global'
            tool_slug = row.tool_slug or 'global'
            
            # Calculate period start
            period_seconds = self._get_period_seconds(limit.period)
            period_start = now - timedelta(seconds=period_seconds)
            period_end = now
            
            # Count executions in this period
            count_query = select(func.count(ToolExecution.id)).where(
                and_(
                    ToolExecution.created_at >= period_start,
                    ToolExecution.created_at <= period_end,
                )
            )
            
            # Add tool filter if tool-specific
            if limit.tool_id:
                count_query = count_query.where(ToolExecution.tool_id == limit.tool_id)
            
            # Add user filter if user-specific
            if limit.user_id:
                count_query = count_query.where(
                    ToolExecution.triggered_by_user_id == limit.user_id
                )
            
            count_result = await self.db.execute(count_query)
            current_count = count_result.scalar() or 0
            
            # Check if at or above threshold
            usage_percent = current_count / limit.max_executions if limit.max_executions > 0 else 0
            
            if usage_percent >= threshold:
                alerts.append(RateLimitAlert(
                    tool_id=limit.tool_id,
                    tool_name=tool_name,
                    tool_slug=tool_slug,
                    limit_name=limit.name or f"{tool_name} - {limit.period.value}",
                    current_usage=current_count,
                    max_allowed=limit.max_executions,
                    usage_percent=usage_percent,
                    period=limit.period.value,
                    period_resets_at=period_end,
                ))
        
        # Sort by usage percent descending
        alerts.sort(key=lambda a: a.usage_percent, reverse=True)
        return alerts
    
    def _get_period_seconds(self, period: RateLimitPeriod) -> int:
        """Get period duration in seconds."""
        periods = {
            RateLimitPeriod.MINUTE: 60,
            RateLimitPeriod.HOUR: 3600,
            RateLimitPeriod.DAY: 86400,
            RateLimitPeriod.WEEK: 604800,
            RateLimitPeriod.MONTH: 2592000,
        }
        return periods.get(period, 3600)
    
    async def _get_unhealthy_tools(self) -> List[Dict[str, Any]]:
        """Get list of unhealthy or degraded tools."""
        result = await self.db.execute(
            select(
                Tool.id,
                Tool.name,
                Tool.slug,
                Tool.health_status,
                Tool.health_message,
                Tool.last_health_check,
                Tool.health_response_ms,
            )
            .where(
                and_(
                    Tool.status == 'implemented',
                    Tool.health_status.in_(['unhealthy', 'degraded'])
                )
            )
            .order_by(
                case(
                    (Tool.health_status == 'unhealthy', 0),
                    (Tool.health_status == 'degraded', 1),
                    else_=2
                ),
                Tool.last_health_check.desc()
            )
        )
        
        tools = []
        now = utc_now()
        
        for row in result:
            unhealthy_duration = None
            if row.last_health_check:
                last_check = ensure_utc(row.last_health_check)
                unhealthy_duration = int((now - last_check).total_seconds() / 60)
            
            tools.append({
                'id': str(row.id),
                'name': row.name,
                'slug': row.slug,
                'health_status': row.health_status,
                'health_message': row.health_message,
                'last_health_check': row.last_health_check.isoformat() if row.last_health_check else None,
                'response_time_ms': row.health_response_ms,
                'unhealthy_minutes': unhealthy_duration,
            })
        
        return tools
    
    async def _get_recent_violations_count(self, hours: int = 24) -> int:
        """Get count of rate limit violations in the last N hours."""
        since = utc_now() - timedelta(hours=hours)
        
        result = await self.db.execute(
            select(func.count(RateLimitViolation.id))
            .where(RateLimitViolation.violated_at >= since)
        )
        
        return result.scalar() or 0
    
    async def _get_pending_approvals(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get list of pending approvals ordered by urgency and age."""
        result = await self.db.execute(
            select(
                ToolApprovalRequest.id,
                ToolApprovalRequest.urgency,
                ToolApprovalRequest.reason,
                ToolApprovalRequest.estimated_cost,
                ToolApprovalRequest.created_at,
                Tool.id.label('tool_id'),
                Tool.name.label('tool_name'),
                Tool.slug.label('tool_slug'),
            )
            .join(Tool, ToolApprovalRequest.tool_id == Tool.id)
            .where(ToolApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(
                case(
                    (ToolApprovalRequest.urgency == ApprovalUrgency.CRITICAL, 0),
                    (ToolApprovalRequest.urgency == ApprovalUrgency.HIGH, 1),
                    (ToolApprovalRequest.urgency == ApprovalUrgency.MEDIUM, 2),
                    else_=3
                ),
                ToolApprovalRequest.created_at.asc()
            )
            .limit(limit)
        )
        
        approvals = []
        now = utc_now()
        
        for row in result:
            pending_minutes = None
            if row.created_at:
                requested = ensure_utc(row.created_at)
                pending_minutes = int((now - requested).total_seconds() / 60)
            
            approvals.append({
                'id': str(row.id),
                'urgency': row.urgency.value,
                'tool_id': str(row.tool_id),
                'tool_name': row.tool_name,
                'tool_slug': row.tool_slug,
                'reason': row.reason,
                'estimated_cost': row.estimated_cost,
                'created_at': row.created_at.isoformat() if row.created_at else None,
                'pending_minutes': pending_minutes,
            })
        
        return approvals
    
    # =========================================================================
    # Tool Execution Trends
    # =========================================================================
    
    async def get_execution_trends(
        self,
        hours: int = 24,
        tool_ids: Optional[List[UUID]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get tool execution trends in hourly buckets.
        
        Returns execution counts per tool per hour for sparkline charts.
        """
        since = utc_now() - timedelta(hours=hours)
        
        # Create labeled column for date_trunc to use consistently
        hour_col = func.date_trunc('hour', ToolExecution.created_at).label('hour')
        
        # Query executions grouped by hour and tool
        query = select(
            hour_col,
            ToolExecution.tool_id,
            Tool.slug.label('tool_slug'),
            Tool.name.label('tool_name'),
            func.count(ToolExecution.id).label('execution_count'),
            func.sum(
                case((ToolExecution.status == ToolExecutionStatus.COMPLETED, 1), else_=0)
            ).label('success_count'),
            func.sum(
                case((ToolExecution.status.in_([ToolExecutionStatus.FAILED, ToolExecutionStatus.TIMEOUT]), 1), else_=0)
            ).label('failure_count'),
            func.coalesce(func.avg(ToolExecution.duration_ms), 0).label('avg_duration_ms'),
        ).join(
            Tool, ToolExecution.tool_id == Tool.id
        ).where(
            ToolExecution.created_at >= since
        ).group_by(
            hour_col,
            ToolExecution.tool_id,
            Tool.slug,
            Tool.name,
        ).order_by(
            hour_col,
            Tool.slug,
        )
        
        if tool_ids:
            query = query.where(ToolExecution.tool_id.in_(tool_ids))
        
        result = await self.db.execute(query)
        
        trends = []
        for row in result:
            trends.append({
                'hour': row.hour.isoformat() if row.hour else None,
                'tool_id': str(row.tool_id),
                'tool_slug': row.tool_slug,
                'tool_name': row.tool_name,
                'execution_count': row.execution_count or 0,
                'success_count': row.success_count or 0,
                'failure_count': row.failure_count or 0,
                'avg_duration_ms': float(row.avg_duration_ms or 0),
            })
        
        return trends
    
    # =========================================================================
    # Rate Limit Violation Trends
    # =========================================================================
    
    async def get_violation_trends(
        self,
        days: int = 7,
        tool_id: Optional[UUID] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get rate limit violation trends by day and tool.
        """
        since = utc_now() - timedelta(days=days)
        
        # Create labeled column for date_trunc to use consistently
        day_col = func.date_trunc('day', RateLimitViolation.violated_at).label('day')
        
        query = select(
            day_col,
            RateLimitViolation.tool_id,
            Tool.slug.label('tool_slug'),
            Tool.name.label('tool_name'),
            func.count(RateLimitViolation.id).label('violation_count'),
        ).join(
            Tool, RateLimitViolation.tool_id == Tool.id, isouter=True
        ).where(
            RateLimitViolation.violated_at >= since
        ).group_by(
            day_col,
            RateLimitViolation.tool_id,
            Tool.slug,
            Tool.name,
        ).order_by(
            day_col,
        )
        
        if tool_id:
            query = query.where(RateLimitViolation.tool_id == tool_id)
        
        result = await self.db.execute(query)
        
        trends = []
        for row in result:
            trends.append({
                'day': row.day.isoformat() if row.day else None,
                'tool_id': str(row.tool_id) if row.tool_id else None,
                'tool_slug': row.tool_slug or 'global',
                'tool_name': row.tool_name or 'Global',
                'violation_count': row.violation_count or 0,
            })
        
        return trends
    
    # =========================================================================
    # Active Alerts
    # =========================================================================
    
    async def get_active_alerts(self) -> List[Dict[str, Any]]:
        """
        Get all active alerts requiring attention.
        
        Aggregates alerts from multiple sources:
        - Unhealthy tools
        - Pending approvals (especially critical)
        - Rate limits near threshold
        - Agent errors/budget issues
        """
        alerts = []
        now = utc_now()
        
        # 1. Unhealthy tools
        unhealthy = await self._get_unhealthy_tools()
        for tool in unhealthy:
            severity = 'critical' if tool['health_status'] == 'unhealthy' else 'medium'
            alerts.append({
                'id': f"health-{tool['id']}",
                'severity': severity,
                'category': 'health',
                'title': f"Tool {tool['health_status']}: {tool['name']}",
                'message': tool['health_message'] or f"Tool has been {tool['health_status']} for {tool['unhealthy_minutes']} minutes",
                'source_type': 'tool',
                'source_id': tool['id'],
                'source_name': tool['name'],
                'action_url': f"/tools/{tool['slug']}",
                'created_at': tool['last_health_check'],
            })
        
        # 2. Pending approvals (critical and high urgency)
        pending = await self._get_pending_approvals(limit=20)
        for approval in pending:
            if approval['urgency'] in ['critical', 'high']:
                severity = 'critical' if approval['urgency'] == 'critical' else 'high'
                alerts.append({
                    'id': f"approval-{approval['id']}",
                    'severity': severity,
                    'category': 'approval',
                    'title': f"Approval pending: {approval['tool_name']}",
                    'message': f"Waiting {approval['pending_minutes']}m - {approval['reason'][:100]}",
                    'source_type': 'tool',
                    'source_id': approval['tool_id'],
                    'source_name': approval['tool_name'],
                    'action_url': f"/admin/approvals/{approval['id']}",
                    'created_at': approval['created_at'],
                })
        
        # 3. Rate limits near threshold (>90%)
        rate_alerts = await self._get_rate_limit_alerts(threshold=0.9)
        for alert in rate_alerts:
            alerts.append({
                'id': f"ratelimit-{alert.tool_id or 'global'}-{alert.period}",
                'severity': 'high' if alert.usage_percent >= 0.95 else 'medium',
                'category': 'rate_limit',
                'title': f"Rate limit at {alert.usage_percent:.0%}: {alert.tool_name}",
                'message': f"{alert.current_usage}/{alert.max_allowed} ({alert.period})",
                'source_type': 'tool',
                'source_id': str(alert.tool_id) if alert.tool_id else None,
                'source_name': alert.tool_name,
                'action_url': f"/admin/rate-limits",
                'created_at': now.isoformat(),
            })
        
        # 4. Agent errors/budget exceeded
        agent_alerts = await self._get_agent_alerts()
        alerts.extend(agent_alerts)
        
        # Sort by severity
        severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
        alerts.sort(key=lambda a: severity_order.get(a['severity'], 99))
        
        return alerts

    async def get_alert_counts(self) -> Dict[str, int]:
        """Get counts of alerts by severity level."""
        alerts = await self.get_active_alerts()
        
        counts = {
            'total': len(alerts),
            'critical': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
        }
        
        for alert in alerts:
            severity = alert.get('severity', 'low')
            if severity in counts:
                counts[severity] += 1
        
        return counts
    
    async def _get_agent_alerts(self) -> List[Dict[str, Any]]:
        """Get alerts for agent issues."""
        result = await self.db.execute(
            select(AgentDefinition)
            .where(
                or_(
                    AgentDefinition.status == 'error',
                    AgentDefinition.status == 'budget_exceeded',
                    and_(
                        AgentDefinition.budget_limit.isnot(None),
                        AgentDefinition.budget_used >= AgentDefinition.budget_limit * AgentDefinition.budget_warning_threshold
                    )
                )
            )
        )
        
        alerts = []
        for agent in result.scalars():
            if agent.status == 'error':
                alerts.append({
                    'id': f"agent-error-{agent.id}",
                    'severity': 'high',
                    'category': 'agent',
                    'title': f"Agent error: {agent.name}",
                    'message': agent.status_message or "Agent encountered an error",
                    'source_type': 'agent',
                    'source_id': str(agent.id),
                    'source_name': agent.name,
                    'action_url': f"/agents?agent={agent.slug}",
                    'created_at': agent.updated_at.isoformat() if agent.updated_at else None,
                })
            elif agent.status == 'budget_exceeded':
                alerts.append({
                    'id': f"agent-budget-{agent.id}",
                    'severity': 'medium',
                    'category': 'budget',
                    'title': f"Budget exceeded: {agent.name}",
                    'message': f"${agent.budget_used:.2f} / ${agent.budget_limit:.2f}",
                    'source_type': 'agent',
                    'source_id': str(agent.id),
                    'source_name': agent.name,
                    'action_url': f"/agents?agent={agent.slug}",
                    'created_at': agent.updated_at.isoformat() if agent.updated_at else None,
                })
            elif agent.budget_limit and agent.budget_used >= agent.budget_limit * agent.budget_warning_threshold:
                pct = (agent.budget_used / agent.budget_limit) * 100
                alerts.append({
                    'id': f"agent-budget-warning-{agent.id}",
                    'severity': 'low',
                    'category': 'budget',
                    'title': f"Budget at {pct:.0f}%: {agent.name}",
                    'message': f"${agent.budget_used:.2f} / ${agent.budget_limit:.2f}",
                    'source_type': 'agent',
                    'source_id': str(agent.id),
                    'source_name': agent.name,
                    'action_url': f"/agents?agent={agent.slug}",
                    'created_at': agent.updated_at.isoformat() if agent.updated_at else None,
                })
        
        return alerts

    # =========================================================================
    # Agent Performance Analytics
    # =========================================================================

    async def get_agent_performance(
        self, 
        days: int = 7,
        agent_slugs: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get performance metrics for agents over the specified period.
        
        Returns efficiency metrics, success rates, and failure analysis.
        """
        since = utc_now() - timedelta(days=days)
        
        # Base query for agent runs
        query = (
            select(
                AgentDefinition.id.label('agent_id'),
                AgentDefinition.slug.label('agent_slug'),
                AgentDefinition.name.label('agent_name'),
                func.count(AgentRun.id).label('total_runs'),
                func.sum(
                    case((AgentRun.status == AgentRunStatus.COMPLETED, 1), else_=0)
                ).label('successful_runs'),
                func.sum(
                    case((AgentRun.status == AgentRunStatus.FAILED, 1), else_=0)
                ).label('failed_runs'),
                func.coalesce(func.avg(AgentRun.duration_seconds), 0).label('avg_duration'),
                func.coalesce(func.avg(AgentRun.cost_usd), 0).label('avg_cost'),
                func.coalesce(func.sum(AgentRun.cost_usd), 0).label('total_cost'),
                func.coalesce(func.avg(AgentRun.items_processed), 0).label('avg_items'),
            )
            .select_from(AgentDefinition)
            .join(AgentRun, AgentRun.agent_id == AgentDefinition.id)
            .where(AgentRun.created_at >= since)
            .group_by(AgentDefinition.id, AgentDefinition.slug, AgentDefinition.name)
        )
        
        if agent_slugs:
            query = query.where(AgentDefinition.slug.in_(agent_slugs))
        
        result = await self.db.execute(query)
        
        metrics = []
        for row in result:
            total = row.total_runs or 0
            successful = row.successful_runs or 0
            success_rate = (successful / total) if total > 0 else 0.0
            
            # Get failure reasons for this agent
            failure_reasons = await self._get_agent_failure_reasons(
                row.agent_id, since, limit=3
            )
            
            metrics.append({
                'agent_id': str(row.agent_id),
                'agent_slug': row.agent_slug,
                'agent_name': row.agent_name,
                'total_runs': total,
                'successful_runs': successful,
                'failed_runs': row.failed_runs or 0,
                'success_rate': round(success_rate, 3),
                'avg_duration_seconds': round(float(row.avg_duration or 0), 1),
                'avg_cost_usd': round(float(row.avg_cost or 0), 4),
                'total_cost_usd': round(float(row.total_cost or 0), 2),
                'avg_items_processed': round(float(row.avg_items or 0), 1),
                'top_failure_reasons': failure_reasons,
            })
        
        # Sort by total runs descending
        metrics.sort(key=lambda x: x['total_runs'], reverse=True)
        return metrics

    async def _get_agent_failure_reasons(
        self,
        agent_id: UUID,
        since: datetime,
        limit: int = 3
    ) -> List[Dict[str, Any]]:
        """Analyze failure reasons for an agent."""
        result = await self.db.execute(
            select(
                AgentRun.error_message,
                func.count(AgentRun.id).label('count')
            )
            .where(
                AgentRun.agent_id == agent_id,
                AgentRun.status == AgentRunStatus.FAILED,
                AgentRun.created_at >= since,
                AgentRun.error_message.isnot(None)
            )
            .group_by(AgentRun.error_message)
            .order_by(desc(func.count(AgentRun.id)))
            .limit(limit)
        )
        
        reasons = []
        for row in result:
            # Extract key terms from error message
            error = row.error_message or "Unknown error"
            # Truncate long messages
            if len(error) > 100:
                error = error[:97] + "..."
            
            reasons.append({
                'reason': error,
                'count': row.count,
            })
        
        return reasons

    async def get_agent_cost_trend(
        self,
        days: int = 30,
        agent_slugs: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get daily cost breakdown by agent.
        
        Returns costs aggregated by day for trend visualization.
        """
        since = utc_now() - timedelta(days=days)
        
        # Use date_trunc for PostgreSQL - tests will skip this
        # Create a label for the date to use in ordering
        date_col = func.date_trunc('day', AgentRun.created_at).label('date')
        
        query = (
            select(
                date_col,
                AgentDefinition.slug.label('agent_slug'),
                AgentDefinition.name.label('agent_name'),
                func.coalesce(func.sum(AgentRun.cost_usd), 0).label('total_cost'),
                func.count(AgentRun.id).label('run_count'),
            )
            .select_from(AgentRun)
            .join(AgentDefinition, AgentRun.agent_id == AgentDefinition.id)
            .where(AgentRun.created_at >= since)
            .group_by(
                date_col,
                AgentDefinition.slug,
                AgentDefinition.name
            )
            .order_by(date_col)
        )
        
        if agent_slugs:
            query = query.where(AgentDefinition.slug.in_(agent_slugs))
        
        result = await self.db.execute(query)
        
        trends = []
        for row in result:
            trends.append({
                'date': row.date.strftime('%Y-%m-%d') if row.date else None,
                'agent_slug': row.agent_slug,
                'agent_name': row.agent_name,
                'total_cost_usd': round(float(row.total_cost or 0), 4),
                'run_count': row.run_count or 0,
            })
        
        return trends

    async def get_agent_suggestions(self) -> List[Dict[str, Any]]:
        """
        Generate AI-driven optimization suggestions for agents.
        
        Analyzes patterns to identify:
        - High failure rates with common causes
        - Low yield agents (running frequently but finding little)
        - Cost optimization opportunities
        - Schedule optimization
        """
        suggestions = []
        
        # Get 7-day performance data
        metrics = await self.get_agent_performance(days=7)
        
        for agent in metrics:
            # 1. High failure rate detection
            if agent['total_runs'] >= 3 and agent['success_rate'] < 0.85:
                failure_pct = (1 - agent['success_rate']) * 100
                top_reason = agent['top_failure_reasons'][0] if agent['top_failure_reasons'] else None
                
                suggestion = {
                    'agent_slug': agent['agent_slug'],
                    'agent_name': agent['agent_name'],
                    'suggestion_type': 'reliability',
                    'severity': 'warning' if agent['success_rate'] < 0.7 else 'info',
                    'title': f"High failure rate ({failure_pct:.0f}%)",
                    'description': f"{agent['agent_name']} is failing {failure_pct:.0f}% of runs.",
                }
                
                if top_reason:
                    suggestion['description'] += f" Most common error: {top_reason['reason']}"
                    suggestion['action'] = "Review error logs and consider adding retry logic or fixing the root cause."
                
                suggestions.append(suggestion)
            
            # 2. Low yield detection (running but not producing results)
            if agent['total_runs'] >= 5 and agent['avg_items_processed'] < 1:
                # Agent running frequently but finding nothing
                weekly_cost = agent['total_cost_usd']
                
                suggestions.append({
                    'agent_slug': agent['agent_slug'],
                    'agent_name': agent['agent_name'],
                    'suggestion_type': 'efficiency',
                    'severity': 'recommendation',
                    'title': f"Low yield: {agent['avg_items_processed']:.1f} items/run",
                    'description': f"{agent['agent_name']} ran {agent['total_runs']} times but averaged only {agent['avg_items_processed']:.1f} items per run.",
                    'potential_savings': f"${weekly_cost * 0.5:.2f}/week if frequency halved",
                    'action': "Consider reducing run frequency or improving search criteria.",
                })
            
            # 3. Cost optimization - high cost per item
            if agent['total_runs'] >= 3 and agent['avg_items_processed'] > 0:
                cost_per_item = agent['total_cost_usd'] / max(agent['avg_items_processed'] * agent['total_runs'], 1)
                if cost_per_item > 0.50:  # More than $0.50 per item
                    suggestions.append({
                        'agent_slug': agent['agent_slug'],
                        'agent_name': agent['agent_name'],
                        'suggestion_type': 'cost',
                        'severity': 'info',
                        'title': f"High cost per item: ${cost_per_item:.2f}",
                        'description': f"{agent['agent_name']} costs ${cost_per_item:.2f} per item processed.",
                        'action': "Consider using a lower-cost model tier or batching more items per run.",
                    })
        
        # Get schedule info for schedule-based suggestions
        agents_result = await self.db.execute(
            select(AgentDefinition)
            .where(AgentDefinition.is_enabled == True)
        )
        
        for agent in agents_result.scalars():
            # 4. Schedule optimization - check if interval seems too frequent
            if agent.schedule_interval_seconds and agent.schedule_interval_seconds < 3600:  # < 1 hour
                # Find this agent's metrics
                agent_metrics = next(
                    (m for m in metrics if m['agent_slug'] == agent.slug), 
                    None
                )
                if agent_metrics and agent_metrics['avg_items_processed'] < 2:
                    interval_mins = agent.schedule_interval_seconds / 60
                    suggestions.append({
                        'agent_slug': agent.slug,
                        'agent_name': agent.name,
                        'suggestion_type': 'schedule',
                        'severity': 'recommendation',
                        'title': f"Frequent schedule ({interval_mins:.0f}m) with low yield",
                        'description': f"{agent.name} runs every {interval_mins:.0f} minutes but only finds {agent_metrics['avg_items_processed']:.1f} items per run.",
                        'action': f"Consider increasing interval to {interval_mins * 2:.0f} minutes to reduce costs.",
                    })
        
        return suggestions

    # =========================================================================
    # Campaign Intelligence Analytics
    # =========================================================================

    async def get_top_patterns(
        self,
        user_id: Optional[UUID] = None,
        limit: int = 10,
        min_confidence: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Get top performing patterns for Campaign Intelligence.
        
        Returns patterns with stats sorted by success rate and usage.
        """
        from app.models.campaign_learning import CampaignPattern, PatternStatus
        
        # Build conditions
        conditions = [
            CampaignPattern.status == PatternStatus.ACTIVE,
            CampaignPattern.confidence_score >= min_confidence,
        ]
        
        if user_id:
            # User's patterns or global patterns
            conditions.append(
                or_(
                    CampaignPattern.user_id == user_id,
                    CampaignPattern.is_global == True
                )
            )
        else:
            conditions.append(CampaignPattern.is_global == True)
        
        query = (
            select(CampaignPattern)
            .where(*conditions)
            .order_by(
                desc(CampaignPattern.times_successful),
                desc(CampaignPattern.confidence_score)
            )
            .limit(limit)
        )
        
        result = await self.db.execute(query)
        patterns = result.scalars().all()
        
        return [
            {
                'id': str(p.id),
                'name': p.name,
                'description': p.description,
                'pattern_type': p.pattern_type.value,
                'confidence_score': p.confidence_score,
                'times_applied': p.times_applied,
                'times_successful': p.times_successful,
                'success_rate': p.success_rate,
                'pattern_data': p.pattern_data,
                'tags': p.tags or [],
                'source_campaign_id': str(p.source_campaign_id) if p.source_campaign_id else None,
                'last_applied_at': p.last_applied_at.isoformat() if p.last_applied_at else None,
                'created_at': p.created_at.isoformat() if p.created_at else None,
            }
            for p in patterns
        ]

    async def get_recent_lessons(
        self,
        user_id: UUID,
        days: int = 30,
        limit: int = 10,
        severity_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get recent lessons learned from campaigns.
        
        Returns lessons sorted by severity and recency.
        """
        from app.models.campaign_learning import CampaignLesson
        
        since = utc_now() - timedelta(days=days)
        
        conditions = [
            CampaignLesson.user_id == user_id,
            CampaignLesson.created_at >= since,
        ]
        
        if severity_filter:
            conditions.append(CampaignLesson.impact_severity == severity_filter)
        
        # Custom ordering: critical > high > medium > low
        severity_order = case(
            (CampaignLesson.impact_severity == 'critical', 1),
            (CampaignLesson.impact_severity == 'high', 2),
            (CampaignLesson.impact_severity == 'medium', 3),
            else_=4
        )
        
        query = (
            select(CampaignLesson)
            .where(*conditions)
            .order_by(severity_order, desc(CampaignLesson.created_at))
            .limit(limit)
        )
        
        result = await self.db.execute(query)
        lessons = result.scalars().all()
        
        return [
            {
                'id': str(l.id),
                'title': l.title,
                'description': l.description,
                'category': l.category.value,
                'impact_severity': l.impact_severity,
                'budget_impact': float(l.budget_impact) if l.budget_impact else None,
                'time_impact_minutes': l.time_impact_minutes,
                'prevention_steps': l.prevention_steps,
                'detection_signals': l.detection_signals,
                'source_campaign_id': str(l.source_campaign_id),
                'times_applied': l.times_applied,
                'tags': l.tags or [],
                'created_at': l.created_at.isoformat() if l.created_at else None,
            }
            for l in lessons
        ]

    async def get_pattern_effectiveness_trend(
        self,
        user_id: Optional[UUID] = None,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """
        Get pattern effectiveness over time.
        
        Shows how pattern success rates trend over the period.
        Uses PostgreSQL date_trunc for grouping.
        """
        from app.models.campaign_learning import CampaignPattern, PatternStatus
        from app.models import Campaign, CampaignStatus
        
        since = utc_now() - timedelta(days=days)
        
        # Build conditions for patterns
        pattern_conditions = [CampaignPattern.status == PatternStatus.ACTIVE]
        if user_id:
            pattern_conditions.append(
                or_(
                    CampaignPattern.user_id == user_id,
                    CampaignPattern.is_global == True
                )
            )
        
        # Get campaigns that used patterns, grouped by week
        week_col = func.date_trunc('week', Campaign.created_at).label('week')
        
        query = (
            select(
                week_col,
                func.count(Campaign.id).label('total_campaigns'),
                func.sum(
                    case((Campaign.status == CampaignStatus.COMPLETED, 1), else_=0)
                ).label('successful_campaigns'),
            )
            .select_from(Campaign)
            .where(
                Campaign.created_at >= since,
            )
            .group_by(week_col)
            .order_by(week_col)
        )
        
        result = await self.db.execute(query)
        
        trends = []
        for row in result:
            total = row.total_campaigns or 0
            successful = row.successful_campaigns or 0
            success_rate = (successful / total) if total > 0 else 0.0
            
            trends.append({
                'week': row.week.strftime('%Y-%m-%d') if row.week else None,
                'total_campaigns': total,
                'successful_campaigns': successful,
                'success_rate': round(success_rate, 3),
            })
        
        return trends

    async def get_campaign_intelligence_summary(
        self,
        user_id: UUID,
    ) -> Dict[str, Any]:
        """
        Get summary stats for Campaign Intelligence dashboard.
        """
        from app.models.campaign_learning import (
            CampaignPattern, CampaignLesson, PatternStatus
        )
        from app.models import Campaign, CampaignStatus
        
        # Pattern stats
        pattern_result = await self.db.execute(
            select(
                func.count(CampaignPattern.id).label('total'),
                func.count(case((CampaignPattern.status == PatternStatus.ACTIVE, 1))).label('active'),
                func.avg(CampaignPattern.confidence_score).label('avg_confidence'),
            )
            .where(
                or_(
                    CampaignPattern.user_id == user_id,
                    CampaignPattern.is_global == True
                )
            )
        )
        pattern_stats = pattern_result.one()
        
        # Lesson stats (last 30 days)
        since = utc_now() - timedelta(days=30)
        lesson_result = await self.db.execute(
            select(
                func.count(CampaignLesson.id).label('total'),
                func.count(case((CampaignLesson.impact_severity == 'critical', 1))).label('critical'),
                func.count(case((CampaignLesson.impact_severity == 'high', 1))).label('high'),
            )
            .where(
                CampaignLesson.user_id == user_id,
                CampaignLesson.created_at >= since,
            )
        )
        lesson_stats = lesson_result.one()
        
        # Campaign stats (last 30 days)
        campaign_result = await self.db.execute(
            select(
                func.count(Campaign.id).label('total'),
                func.count(case((Campaign.status == CampaignStatus.COMPLETED, 1))).label('completed'),
                func.count(case((Campaign.status == CampaignStatus.FAILED, 1))).label('failed'),
            )
            .where(
                Campaign.user_id == user_id,
                Campaign.created_at >= since,
            )
        )
        campaign_stats = campaign_result.one()
        
        total_campaigns = campaign_stats.total or 0
        completed = campaign_stats.completed or 0
        
        return {
            'patterns': {
                'total': pattern_stats.total or 0,
                'active': pattern_stats.active or 0,
                'avg_confidence': round(float(pattern_stats.avg_confidence or 0), 2),
            },
            'lessons': {
                'total_30d': lesson_stats.total or 0,
                'critical': lesson_stats.critical or 0,
                'high': lesson_stats.high or 0,
            },
            'campaigns': {
                'total_30d': total_campaigns,
                'completed': completed,
                'failed': campaign_stats.failed or 0,
                'success_rate': round(completed / total_campaigns, 3) if total_campaigns > 0 else 0.0,
            },
        }


# Singleton-style service factory
def get_analytics_service(db: AsyncSession) -> AnalyticsService:
    """Get an analytics service instance."""
    return AnalyticsService(db)
