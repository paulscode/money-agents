/**
 * Tool Operations Tab
 * 
 * Admin-only view of tool health, rate limits, approvals, and operational alerts.
 * Provides at-a-glance operational status to minimize manual checking.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import {
  Activity,
  AlertTriangle,
  CheckCircle,
  Clock,
  RefreshCw,
  Shield,
  XCircle,
  TrendingUp,
  ChevronRight,
  AlertCircle,
  HelpCircle,
} from 'lucide-react';
import {
  analyticsService,
  type ToolOperationsSummary,
} from '@/services/analytics';

// =============================================================================
// Health Status Card
// =============================================================================

interface HealthCardProps {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'unknown';
  count: number;
  icon: React.ReactNode;
  color: string;
  bgColor: string;
}

const HealthCard: React.FC<HealthCardProps> = ({ status, count, icon, color, bgColor }) => (
  <div className={`${bgColor} border border-gray-700 rounded-xl p-4 flex items-center gap-4`}>
    <div className={`p-3 rounded-lg ${color} bg-opacity-20`}>
      {icon}
    </div>
    <div>
      <p className="text-2xl font-bold text-white">{count}</p>
      <p className="text-sm text-gray-400 capitalize">{status}</p>
    </div>
  </div>
);

// =============================================================================
// Needs Attention Panel
// =============================================================================

interface NeedsAttentionProps {
  summary: ToolOperationsSummary;
}

const NeedsAttentionPanel: React.FC<NeedsAttentionProps> = ({ summary }) => {
  const issues = [];

  // Pending approvals
  if (summary.approvals.pending_count > 0) {
    const urgencyText = [];
    if (summary.approvals.critical_count > 0) {
      urgencyText.push(`${summary.approvals.critical_count} critical`);
    }
    if (summary.approvals.high_count > 0) {
      urgencyText.push(`${summary.approvals.high_count} high`);
    }
    if (summary.approvals.medium_count > 0) {
      urgencyText.push(`${summary.approvals.medium_count} medium`);
    }
    issues.push({
      icon: <Clock className="w-4 h-4 text-yellow-400" />,
      text: `${summary.approvals.pending_count} pending approvals (${urgencyText.join(', ')})`,
      severity: summary.approvals.critical_count > 0 ? 'critical' : 'warning',
      link: '/admin/approvals',
    });
  }

  // Rate limits near threshold
  if (summary.rate_limit_alerts.length > 0) {
    const nearLimit = summary.rate_limit_alerts.filter(a => a.usage_percent >= 0.9).length;
    issues.push({
      icon: <AlertTriangle className="w-4 h-4 text-orange-400" />,
      text: `${summary.rate_limit_alerts.length} rate limits near threshold${nearLimit > 0 ? ` (${nearLimit} >90%)` : ''}`,
      severity: nearLimit > 0 ? 'warning' : 'info',
      link: '/admin/rate-limits',
    });
  }

  // Unhealthy tools
  if (summary.unhealthy_tools.length > 0) {
    const unhealthyCount = summary.unhealthy_tools.filter(t => t.health_status === 'unhealthy').length;
    const degradedCount = summary.unhealthy_tools.filter(t => t.health_status === 'degraded').length;
    const text = [];
    if (unhealthyCount > 0) text.push(`${unhealthyCount} unhealthy`);
    if (degradedCount > 0) text.push(`${degradedCount} degraded`);
    issues.push({
      icon: <XCircle className="w-4 h-4 text-red-400" />,
      text: `${text.join(', ')} tool${summary.unhealthy_tools.length > 1 ? 's' : ''}`,
      severity: unhealthyCount > 0 ? 'critical' : 'warning',
      link: '/tools',
    });
  }

  // Recent violations
  if (summary.recent_violations_count > 0) {
    issues.push({
      icon: <Shield className="w-4 h-4 text-purple-400" />,
      text: `${summary.recent_violations_count} rate limit violations (24h)`,
      severity: 'info',
      link: '/admin/rate-limits',
    });
  }

  if (issues.length === 0) {
    return (
      <div className="bg-green-900/20 border border-green-500/30 rounded-xl p-4">
        <div className="flex items-center gap-2 text-green-400">
          <CheckCircle className="w-5 h-5" />
          <span className="font-medium">All systems operational</span>
        </div>
        <p className="text-sm text-gray-400 mt-1">No issues require attention</p>
      </div>
    );
  }

  return (
    <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-4 space-y-3">
      <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide flex items-center gap-2">
        <AlertCircle className="w-4 h-4 text-yellow-400" />
        Needs Attention
      </h3>
      {issues.map((issue, idx) => (
        <Link
          key={idx}
          to={issue.link}
          className={`flex items-center gap-2 p-2 rounded-lg transition-colors ${
            issue.severity === 'critical'
              ? 'bg-red-900/20 hover:bg-red-900/30'
              : issue.severity === 'warning'
              ? 'bg-yellow-900/20 hover:bg-yellow-900/30'
              : 'bg-gray-800/50 hover:bg-gray-800'
          }`}
        >
          {issue.icon}
          <span className="text-sm text-gray-200 flex-1">{issue.text}</span>
          <ChevronRight className="w-4 h-4 text-gray-500" />
        </Link>
      ))}
    </div>
  );
};

// =============================================================================
// Pending Approvals List
// =============================================================================

interface PendingApprovalsProps {
  approvals: ToolOperationsSummary['pending_approvals'];
}

const PendingApprovalsList: React.FC<PendingApprovalsProps> = ({ approvals }) => {
  if (approvals.length === 0) {
    return (
      <div className="text-gray-500 text-sm py-4 text-center">
        No pending approvals
      </div>
    );
  }

  const urgencyStyles: Record<string, string> = {
    critical: 'bg-red-500/20 text-red-400 border-red-500/30',
    high: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
    medium: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    low: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  };

  const urgencyIcons: Record<string, React.ReactNode> = {
    critical: <AlertTriangle className="w-3 h-3" />,
    high: <AlertCircle className="w-3 h-3" />,
    medium: <Clock className="w-3 h-3" />,
    low: <HelpCircle className="w-3 h-3" />,
  };

  return (
    <div className="space-y-2">
      {approvals.map((approval) => (
        <div
          key={approval.id}
          className="flex items-start gap-3 p-3 bg-gray-800/30 rounded-lg border border-gray-700/50"
        >
          <span
            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium border ${
              urgencyStyles[approval.urgency] || urgencyStyles.low
            }`}
          >
            {urgencyIcons[approval.urgency]}
            {approval.urgency.toUpperCase()}
          </span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-white">{approval.tool_name}</span>
              {approval.estimated_cost && (
                <span className="text-xs text-green-400">${approval.estimated_cost.toFixed(2)}</span>
              )}
            </div>
            <p className="text-xs text-gray-400 truncate mt-0.5">{approval.reason}</p>
            <p className="text-xs text-gray-500 mt-1">
              Waiting {approval.pending_minutes ? `${approval.pending_minutes}m` : 'just now'}
            </p>
          </div>
          <Link
            to={`/admin/approvals/${approval.id}`}
            className="text-xs text-cyan-400 hover:text-cyan-300 whitespace-nowrap"
          >
            Review
          </Link>
        </div>
      ))}
    </div>
  );
};

// =============================================================================
// Unhealthy Tools List
// =============================================================================

interface UnhealthyToolsProps {
  tools: ToolOperationsSummary['unhealthy_tools'];
}

const UnhealthyToolsList: React.FC<UnhealthyToolsProps> = ({ tools }) => {
  if (tools.length === 0) {
    return (
      <div className="text-gray-500 text-sm py-4 text-center">
        All tools healthy
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {tools.map((tool) => (
        <Link
          key={tool.id}
          to={`/tools/${tool.slug}`}
          className="flex items-center gap-3 p-3 bg-gray-800/30 rounded-lg border border-gray-700/50 hover:bg-gray-800/50 transition-colors"
        >
          <div
            className={`w-2 h-2 rounded-full ${
              tool.health_status === 'unhealthy' ? 'bg-red-500' : 'bg-yellow-500'
            }`}
          />
          <div className="flex-1 min-w-0">
            <span className="text-sm font-medium text-white">{tool.name}</span>
            <p className="text-xs text-gray-400 truncate mt-0.5">
              {tool.health_message || `${tool.health_status} for ${tool.unhealthy_minutes}m`}
            </p>
          </div>
          {tool.response_time_ms && (
            <span className="text-xs text-gray-500">{tool.response_time_ms}ms</span>
          )}
        </Link>
      ))}
    </div>
  );
};

// =============================================================================
// Rate Limit Alerts List
// =============================================================================

interface RateLimitAlertsProps {
  alerts: ToolOperationsSummary['rate_limit_alerts'];
}

const RateLimitAlertsList: React.FC<RateLimitAlertsProps> = ({ alerts }) => {
  if (alerts.length === 0) {
    return (
      <div className="text-gray-500 text-sm py-4 text-center">
        No rate limits near threshold
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {alerts.map((alert, idx) => (
        <div
          key={idx}
          className="flex items-center gap-3 p-3 bg-gray-800/30 rounded-lg border border-gray-700/50"
        >
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-white">{alert.tool_name}</span>
              <span className="text-xs text-gray-500">{alert.period}</span>
            </div>
            <div className="flex items-center gap-2 mt-1">
              <div className="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${
                    alert.usage_percent >= 0.95
                      ? 'bg-red-500'
                      : alert.usage_percent >= 0.9
                      ? 'bg-orange-500'
                      : 'bg-yellow-500'
                  }`}
                  style={{ width: `${Math.min(alert.usage_percent * 100, 100)}%` }}
                />
              </div>
              <span className="text-xs text-gray-400 whitespace-nowrap">
                {alert.current_usage}/{alert.max_allowed}
              </span>
            </div>
          </div>
          <span
            className={`text-sm font-bold ${
              alert.usage_percent >= 0.95
                ? 'text-red-400'
                : alert.usage_percent >= 0.9
                ? 'text-orange-400'
                : 'text-yellow-400'
            }`}
          >
            {(alert.usage_percent * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  );
};

// =============================================================================
// Execution Sparkline
// =============================================================================

interface SparklineProps {
  data: number[];
  color?: string;
  height?: number;
}

const Sparkline: React.FC<SparklineProps> = ({ data, color = '#00d9ff', height = 32 }) => {
  if (data.length === 0) return null;

  const max = Math.max(...data, 1);
  const width = 100;
  const points = data.map((val, idx) => {
    const x = (idx / (data.length - 1)) * width;
    const y = height - (val / max) * height;
    return `${x},${y}`;
  }).join(' ');

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
};

// =============================================================================
// Main Tab Component
// =============================================================================

export const ToolOperationsTab: React.FC = () => {
  // Fetch tool operations summary
  const {
    data: summary,
    isLoading: summaryLoading,
    refetch: refetchSummary,
    isRefetching,
  } = useQuery({
    queryKey: ['tool-operations-summary'],
    queryFn: () => analyticsService.getToolOperationsSummary(),
    refetchInterval: 30000,
  });

  // Fetch execution trends for sparklines
  const { data: executionTrends } = useQuery({
    queryKey: ['execution-trends', 24],
    queryFn: () => analyticsService.getExecutionTrends(24),
    refetchInterval: 60000,
  });

  // Fetch violation trends
  const { data: violationTrends } = useQuery({
    queryKey: ['violation-trends', 7],
    queryFn: () => analyticsService.getViolationTrends(7),
    refetchInterval: 300000,
  });

  // Group execution trends by tool for sparklines
  const trendsByTool = React.useMemo(() => {
    if (!executionTrends) return new Map<string, { name: string; data: number[]; total: number }>();
    
    const grouped = new Map<string, { name: string; data: number[]; total: number }>();
    
    executionTrends.forEach((trend) => {
      const existing = grouped.get(trend.tool_slug) || { 
        name: trend.tool_name, 
        data: [], 
        total: 0 
      };
      existing.data.push(trend.execution_count);
      existing.total += trend.execution_count;
      grouped.set(trend.tool_slug, existing);
    });
    
    return grouped;
  }, [executionTrends]);

  // Aggregate violation counts by tool
  const violationsByTool = React.useMemo(() => {
    if (!violationTrends) return [];
    
    const aggregated = new Map<string, { name: string; count: number }>();
    
    violationTrends.forEach((trend) => {
      const existing = aggregated.get(trend.tool_slug) || { name: trend.tool_name, count: 0 };
      existing.count += trend.violation_count;
      aggregated.set(trend.tool_slug, existing);
    });
    
    return Array.from(aggregated.entries())
      .map(([slug, data]) => ({ slug, ...data }))
      .sort((a, b) => b.count - a.count);
  }, [violationTrends]);

  if (summaryLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw className="w-8 h-8 text-cyan-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header with Refresh */}
      <div className="flex items-center justify-between">
        <p className="text-gray-400">
          Unified view of tool health, rate limits, and approvals
        </p>
        <button
          onClick={() => refetchSummary()}
          disabled={isRefetching}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <RefreshCw className={`w-4 h-4 ${isRefetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {summary && (
        <>
          {/* Health Status Cards */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <HealthCard
              status="healthy"
              count={summary.health.healthy}
              icon={<CheckCircle className="w-6 h-6 text-green-400" />}
              color="text-green-400"
              bgColor="bg-green-900/20"
            />
            <HealthCard
              status="degraded"
              count={summary.health.degraded}
              icon={<AlertTriangle className="w-6 h-6 text-yellow-400" />}
              color="text-yellow-400"
              bgColor="bg-yellow-900/20"
            />
            <HealthCard
              status="unhealthy"
              count={summary.health.unhealthy}
              icon={<XCircle className="w-6 h-6 text-red-400" />}
              color="text-red-400"
              bgColor="bg-red-900/20"
            />
            <HealthCard
              status="unknown"
              count={summary.health.unknown}
              icon={<HelpCircle className="w-6 h-6 text-gray-400" />}
              color="text-gray-400"
              bgColor="bg-gray-800"
            />
          </div>

          {/* Needs Attention Panel */}
          <NeedsAttentionPanel summary={summary} />

          {/* Main Content Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Pending Approvals */}
            <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Clock className="w-5 h-5 text-yellow-400" />
                  Pending Approvals
                  {summary.approvals.pending_count > 0 && (
                    <span className="bg-yellow-500/20 text-yellow-400 text-xs px-2 py-0.5 rounded-full">
                      {summary.approvals.pending_count}
                    </span>
                  )}
                </h2>
                <Link to="/admin/approvals" className="text-xs text-cyan-400 hover:text-cyan-300">
                  View All
                </Link>
              </div>
              <PendingApprovalsList approvals={summary.pending_approvals} />
            </div>

            {/* Unhealthy Tools */}
            <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <XCircle className="w-5 h-5 text-red-400" />
                  Unhealthy Tools
                  {summary.unhealthy_tools.length > 0 && (
                    <span className="bg-red-500/20 text-red-400 text-xs px-2 py-0.5 rounded-full">
                      {summary.unhealthy_tools.length}
                    </span>
                  )}
                </h2>
              </div>
              <UnhealthyToolsList tools={summary.unhealthy_tools} />
            </div>

            {/* Rate Limit Alerts */}
            <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <Shield className="w-5 h-5 text-orange-400" />
                  Rate Limits Near Threshold
                </h2>
                <Link to="/admin/rate-limits" className="text-xs text-cyan-400 hover:text-cyan-300">
                  Manage Limits
                </Link>
              </div>
              <RateLimitAlertsList alerts={summary.rate_limit_alerts} />
            </div>

            {/* Execution Trends (24h) */}
            <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <TrendingUp className="w-5 h-5 text-cyan-400" />
                  Tool Activity (24h)
                </h2>
              </div>
              {trendsByTool.size === 0 ? (
                <div className="text-gray-500 text-sm py-4 text-center">
                  No executions in the last 24 hours
                </div>
              ) : (
                <div className="space-y-3">
                  {Array.from(trendsByTool.entries())
                    .sort((a, b) => b[1].total - a[1].total)
                    .slice(0, 6)
                    .map(([slug, { name, data, total }]) => (
                      <div key={slug} className="flex items-center gap-3">
                        <div className="flex-1 min-w-0">
                          <span className="text-sm text-white truncate block">{name}</span>
                          <span className="text-xs text-gray-500">{total} executions</span>
                        </div>
                        <Sparkline data={data} />
                      </div>
                    ))}
                </div>
              )}
            </div>
          </div>

          {/* Violation Trends (7 days) */}
          {violationsByTool.length > 0 && (
            <div className="bg-gray-900/50 border border-gray-700 rounded-xl p-5">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-semibold text-white flex items-center gap-2">
                  <AlertTriangle className="w-5 h-5 text-purple-400" />
                  Rate Limit Violations (7 days)
                </h2>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-gray-400 border-b border-gray-700">
                      <th className="text-left py-2 px-2">Tool</th>
                      <th className="text-right py-2 px-2">Violations</th>
                      <th className="text-right py-2 px-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {violationsByTool.slice(0, 5).map(({ slug, name, count }) => (
                      <tr key={slug} className="border-b border-gray-800/50">
                        <td className="py-2 px-2 text-gray-300">{name}</td>
                        <td className="py-2 px-2 text-right">
                          <span className="text-purple-400 font-medium">{count}</span>
                        </td>
                        <td className="py-2 px-2 text-right">
                          <Link
                            to={`/admin/rate-limits?tool=${slug}`}
                            className="text-cyan-400 hover:text-cyan-300 text-xs"
                          >
                            Adjust Limit
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default ToolOperationsTab;
