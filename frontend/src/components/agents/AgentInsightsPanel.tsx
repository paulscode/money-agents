/**
 * Agent Insights Panel
 * 
 * Displays performance metrics, cost trends, and optimization suggestions
 * for agents. Part of the Agent Management page.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  TrendingUp,
  TrendingDown,
  AlertTriangle,
  Lightbulb,
  Clock,
  DollarSign,
  Activity,
  Target,
  ChevronDown,
  ChevronUp,
  CheckCircle,
  XCircle,
  BarChart3,
} from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import analyticsService from '@/services/analytics';
import type {
  AgentPerformance,
  CostTrendPoint,
  AgentSuggestion,
} from '@/services/analytics';

// =============================================================================
// Main Component
// =============================================================================

export function AgentInsightsPanel() {
  const [expanded, setExpanded] = useState(true);
  const [selectedDays, setSelectedDays] = useState(7);

  // Fetch performance data
  const { data: performance, isLoading: perfLoading } = useQuery({
    queryKey: ['agentPerformance', selectedDays],
    queryFn: () => analyticsService.getAgentPerformance(selectedDays),
    refetchInterval: 60000, // Refresh every minute
  });

  // Fetch cost trends for selected period
  const { data: costTrend, isLoading: costLoading } = useQuery({
    queryKey: ['agentCostTrend', selectedDays],
    queryFn: () => analyticsService.getAgentCostTrend(selectedDays),
    refetchInterval: 300000, // Refresh every 5 minutes
  });

  // Fetch suggestions
  const { data: suggestions } = useQuery({
    queryKey: ['agentSuggestions'],
    queryFn: () => analyticsService.getAgentSuggestions(),
    refetchInterval: 300000,
  });

  // Calculate totals
  const totals = performance?.reduce(
    (acc, agent) => ({
      runs: acc.runs + agent.total_runs,
      cost: acc.cost + agent.total_cost_usd,
      successful: acc.successful + agent.successful_runs,
      failed: acc.failed + agent.failed_runs,
    }),
    { runs: 0, cost: 0, successful: 0, failed: 0 }
  ) || { runs: 0, cost: 0, successful: 0, failed: 0 };

  const overallSuccessRate = totals.runs > 0 
    ? ((totals.successful / totals.runs) * 100).toFixed(1) 
    : '0';

  // Get unique agent slugs for chart
  const agentSlugs = costTrend
    ? [...new Set(costTrend.map(p => p.agent_slug))]
    : [];

  // Prepare chart data - aggregate by date, filling in the full date range with 0s
  const chartData = (() => {
    if (!costTrend) return [];

    // Build a zero-template with all agent slugs set to 0
    const zeroTemplate: Record<string, number> = { total: 0 };
    for (const slug of agentSlugs) {
      zeroTemplate[slug] = 0;
    }

    // Build a map of existing data points
    const dataMap = costTrend.reduce((acc, point) => {
      if (!acc[point.date]) {
        acc[point.date] = { ...zeroTemplate, date: point.date };
      }
      acc[point.date].total = (acc[point.date].total as number) + point.total_cost_usd;
      acc[point.date][point.agent_slug] = ((acc[point.date][point.agent_slug] as number) || 0) + point.total_cost_usd;
      return acc;
    }, {} as Record<string, Record<string, number | string>>);

    // Generate all dates in the selected range, filling gaps with zeros for all slugs
    const allDates: Record<string, number | string>[] = [];
    const now = new Date();
    for (let i = selectedDays - 1; i >= 0; i--) {
      const d = new Date(now);
      d.setDate(d.getDate() - i);
      const key = d.toISOString().slice(0, 10);
      allDates.push(dataMap[key] || { ...zeroTemplate, date: key });
    }
    return allDates;
  })();

  const colors = [
    '#22d3ee', // cyan
    '#a855f7', // purple
    '#22c55e', // green
    '#f59e0b', // amber
    '#ef4444', // red
    '#3b82f6', // blue
  ];

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between p-4 hover:bg-gray-800/30 transition-colors"
      >
        <div className="flex items-center gap-3">
          <BarChart3 className="h-5 w-5 text-neon-cyan" />
          <h3 className="text-lg font-semibold text-white">Performance Insights</h3>
          {suggestions && suggestions.length > 0 && (
            <span className="px-2 py-0.5 text-xs font-medium bg-amber-500/20 text-amber-400 rounded-full">
              {suggestions.length} suggestion{suggestions.length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronUp className="h-5 w-5 text-gray-400" />
        ) : (
          <ChevronDown className="h-5 w-5 text-gray-400" />
        )}
      </button>

      {expanded && (
        <div className="p-4 pt-0 space-y-6">
          {/* Time Range Selector */}
          <div className="flex items-center gap-2">
            <span className="text-sm text-gray-400">Period:</span>
            {[7, 14, 30].map((days) => (
              <button
                key={days}
                onClick={() => setSelectedDays(days)}
                className={`px-3 py-1 text-sm rounded-md transition-colors ${
                  selectedDays === days
                    ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30'
                    : 'bg-gray-800 text-gray-400 hover:text-white border border-gray-700'
                }`}
              >
                {days}d
              </button>
            ))}
          </div>

          {/* Summary Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              icon={Activity}
              label="Total Runs"
              value={totals.runs}
              color="text-neon-cyan"
            />
            <StatCard
              icon={CheckCircle}
              label="Success Rate"
              value={`${overallSuccessRate}%`}
              color={parseFloat(overallSuccessRate) >= 90 ? 'text-green-500' : 'text-amber-500'}
            />
            <StatCard
              icon={DollarSign}
              label={`${selectedDays}d Cost`}
              value={`$${totals.cost.toFixed(2)}`}
              color="text-neon-purple"
            />
            <StatCard
              icon={XCircle}
              label="Failed"
              value={totals.failed}
              color={totals.failed > 0 ? 'text-red-500' : 'text-gray-500'}
            />
          </div>

          {/* Efficiency Table */}
          <div>
            <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
              <Target className="h-4 w-4" />
              Efficiency Metrics ({selectedDays} days)
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-500 border-b border-gray-700">
                    <th className="pb-2 font-medium">Agent</th>
                    <th className="pb-2 font-medium text-right">Runs</th>
                    <th className="pb-2 font-medium text-right">Success</th>
                    <th className="pb-2 font-medium text-right">Avg Time</th>
                    <th className="pb-2 font-medium text-right">Avg Cost</th>
                    <th className="pb-2 font-medium text-right">{selectedDays}d Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {perfLoading ? (
                    <tr>
                      <td colSpan={6} className="py-4 text-center text-gray-500">
                        Loading...
                      </td>
                    </tr>
                  ) : performance && performance.length > 0 ? (
                    performance.map((agent) => (
                      <PerformanceRow key={agent.agent_id} agent={agent} />
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="py-4 text-center text-gray-500">
                        No agent runs in this period
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Cost Trend Chart */}
          {chartData.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                <TrendingUp className="h-4 w-4" />
                Cost Trend ({selectedDays} days)
              </h4>
              <div className="h-48 -ml-4">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                    <XAxis
                      dataKey="date"
                      tick={{ fill: '#9ca3af', fontSize: 10 }}
                      tickFormatter={(value) => {
                        const date = new Date(value);
                        return `${date.getMonth() + 1}/${date.getDate()}`;
                      }}
                    />
                    <YAxis
                      tick={{ fill: '#9ca3af', fontSize: 10 }}
                      tickFormatter={(value) => `$${value.toFixed(2)}`}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#1f2937',
                        border: '1px solid #374151',
                        borderRadius: '8px',
                      }}
                      formatter={(value: number) => [`$${value.toFixed(4)}`, '']}
                      labelFormatter={(label) => new Date(label).toLocaleDateString()}
                    />
                    <Legend />
                    {agentSlugs.map((slug, i) => (
                      <Area
                        key={slug}
                        type="monotone"
                        dataKey={slug}
                        name={slug.replace(/_/g, ' ')}
                        stackId="1"
                        stroke={colors[i % colors.length]}
                        fill={colors[i % colors.length]}
                        fillOpacity={0.6}
                      />
                    ))}
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Optimization Suggestions */}
          {suggestions && suggestions.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
                <Lightbulb className="h-4 w-4 text-amber-400" />
                Optimization Suggestions
              </h4>
              <div className="space-y-2">
                {suggestions.map((suggestion, i) => (
                  <SuggestionCard key={i} suggestion={suggestion} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Sub-components
// =============================================================================

interface StatCardProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number;
  color: string;
}

function StatCard({ icon: Icon, label, value, color }: StatCardProps) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-3 border border-gray-700">
      <div className="flex items-center gap-2 mb-1">
        <Icon className={`h-3.5 w-3.5 ${color}`} />
        <span className="text-xs text-gray-500">{label}</span>
      </div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
    </div>
  );
}

interface PerformanceRowProps {
  agent: AgentPerformance;
}

function PerformanceRow({ agent }: PerformanceRowProps) {
  const successPct = (agent.success_rate * 100).toFixed(0);
  const avgDuration = formatDuration(agent.avg_duration_seconds);

  return (
    <tr className="border-b border-gray-800 hover:bg-gray-800/30">
      <td className="py-2">
        <div className="flex items-center gap-2">
          <span className="text-white font-medium">
            {agent.agent_name.replace(/_/g, ' ')}
          </span>
          {agent.failed_runs > 0 && agent.top_failure_reasons.length > 0 && (
            <span
              className="text-xs text-red-400"
              title={agent.top_failure_reasons[0]?.reason}
            >
              <AlertTriangle className="h-3 w-3" />
            </span>
          )}
        </div>
      </td>
      <td className="py-2 text-right text-gray-300">{agent.total_runs}</td>
      <td className="py-2 text-right">
        <span
          className={`${
            agent.success_rate >= 0.95
              ? 'text-green-400'
              : agent.success_rate >= 0.85
              ? 'text-amber-400'
              : 'text-red-400'
          }`}
        >
          {successPct}%
        </span>
      </td>
      <td className="py-2 text-right text-gray-400">{avgDuration}</td>
      <td className="py-2 text-right text-gray-400">
        ${agent.avg_cost_usd.toFixed(3)}
      </td>
      <td className="py-2 text-right text-neon-purple font-medium">
        ${agent.total_cost_usd.toFixed(2)}
      </td>
    </tr>
  );
}

interface SuggestionCardProps {
  suggestion: AgentSuggestion;
}

function SuggestionCard({ suggestion }: SuggestionCardProps) {
  const severityColors = {
    info: 'border-blue-500/30 bg-blue-500/10',
    warning: 'border-amber-500/30 bg-amber-500/10',
    recommendation: 'border-green-500/30 bg-green-500/10',
  };

  const severityIcons = {
    info: <Lightbulb className="h-4 w-4 text-blue-400" />,
    warning: <AlertTriangle className="h-4 w-4 text-amber-400" />,
    recommendation: <TrendingUp className="h-4 w-4 text-green-400" />,
  };

  return (
    <div
      className={`p-3 rounded-lg border ${severityColors[suggestion.severity]}`}
    >
      <div className="flex items-start gap-3">
        {severityIcons[suggestion.severity]}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-white">
              {suggestion.title}
            </span>
            <span className="text-xs text-gray-500">
              ({suggestion.agent_name.replace(/_/g, ' ')})
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-1">{suggestion.description}</p>
          {suggestion.action && (
            <p className="text-xs text-neon-cyan mt-1">→ {suggestion.action}</p>
          )}
          {suggestion.potential_savings && (
            <span className="inline-flex items-center gap-1 mt-1 px-2 py-0.5 text-xs font-medium bg-green-500/20 text-green-400 rounded-full">
              <DollarSign className="h-3 w-3" />
              Save {suggestion.potential_savings}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Helpers
// =============================================================================

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds.toFixed(0)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return `${minutes}m ${remainingSeconds}s`;
}

export default AgentInsightsPanel;
