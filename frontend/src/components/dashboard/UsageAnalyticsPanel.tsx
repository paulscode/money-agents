/**
 * Usage Analytics Panel
 * 
 * Collapsible admin-only section showing LLM usage, tool executions, and costs.
 * Displays a condensed view of usage data with expandable details.
 */
import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  BarChart3,
  Cpu,
  DollarSign,
  MessageSquare,
  Zap,
  ChevronDown,
  ChevronUp,
  Activity,
  Clock,
  CheckCircle,
  XCircle,
} from 'lucide-react';
import { formatDistanceToNow, format } from 'date-fns';
import { useAuthStore } from '@/stores/auth';
import { usageService, type RecentExecution } from '@/services/usage';

export const UsageAnalyticsPanel: React.FC = () => {
  const { user } = useAuthStore();
  const [isExpanded, setIsExpanded] = useState(false);
  const [days, setDays] = useState(30);

  // Only render for admins
  if (user?.role !== 'admin') {
    return null;
  }

  // Fetch usage summary
  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ['usage-summary', days],
    queryFn: () => usageService.getSummary(days),
    refetchInterval: 30000,
  });

  // Fetch recent executions (only when expanded)
  const { data: recentExecutions = [] } = useQuery({
    queryKey: ['recent-executions'],
    queryFn: () => usageService.getRecentExecutions(5),
    refetchInterval: 10000,
    enabled: isExpanded,
  });

  const formatCost = (cost: number) => {
    if (cost < 0.01) {
      return `$${cost.toFixed(4)}`;
    }
    return `$${cost.toFixed(2)}`;
  };

  const formatTokens = (tokens: number) => {
    if (tokens >= 1000000) {
      return `${(tokens / 1000000).toFixed(1)}M`;
    }
    if (tokens >= 1000) {
      return `${(tokens / 1000).toFixed(1)}K`;
    }
    return tokens.toString();
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="w-3.5 h-3.5 text-green-400" />;
      case 'failed':
      case 'timeout':
        return <XCircle className="w-3.5 h-3.5 text-red-400" />;
      default:
        return <Clock className="w-3.5 h-3.5 text-gray-400" />;
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return 'text-green-400';
      case 'failed':
        return 'text-red-400';
      case 'timeout':
        return 'text-yellow-400';
      default:
        return 'text-gray-400';
    }
  };

  return (
    <section className="mt-6">
      {/* Header - Always Visible */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-4 bg-gray-900/50 border border-gray-800 rounded-xl hover:bg-gray-900/70 transition-colors"
      >
        <div className="flex items-center gap-3">
          <BarChart3 className="w-5 h-5 text-cyan-400" />
          <h2 className="text-lg font-semibold text-white">Usage Analytics</h2>
          <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">Admin</span>
        </div>
        
        {/* Quick Summary (shown when collapsed) */}
        {!isExpanded && !summaryLoading && summary && (
          <div className="hidden sm:flex items-center gap-6 text-sm">
            <span className="text-gray-400">
              <span className="text-green-400 font-medium">{formatCost(summary.total_estimated_cost_usd)}</span> spent
            </span>
            <span className="text-gray-400">
              <span className="text-cyan-400 font-medium">{formatTokens(summary.total_tokens)}</span> tokens
            </span>
            <span className="text-gray-400">
              <span className="text-purple-400 font-medium">{summary.total_tool_executions}</span> tool runs
            </span>
          </div>
        )}
        
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <ChevronUp className="w-5 h-5 text-gray-400" />
          ) : (
            <ChevronDown className="w-5 h-5 text-gray-400" />
          )}
        </div>
      </button>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="mt-4 space-y-4">
          {/* Period Selector */}
          <div className="flex items-center justify-end gap-2">
            <span className="text-gray-400 text-sm">Period:</span>
            <select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="bg-gray-800 border border-gray-700 text-white px-3 py-1.5 rounded-lg text-sm focus:border-cyan-500 focus:ring-1 focus:ring-cyan-500"
            >
              <option value={7}>Last 7 days</option>
              <option value={14}>Last 14 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </div>

          {/* Summary Cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Total Cost */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-400 text-xs">Estimated Cost</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {summaryLoading ? '...' : formatCost(summary?.total_estimated_cost_usd || 0)}
                  </p>
                </div>
                <div className="p-2 bg-green-900/20 rounded-lg">
                  <DollarSign className="w-5 h-5 text-green-400" />
                </div>
              </div>
            </div>

            {/* Total Tokens */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-400 text-xs">Total Tokens</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {summaryLoading ? '...' : formatTokens(summary?.total_tokens || 0)}
                  </p>
                </div>
                <div className="p-2 bg-cyan-900/20 rounded-lg">
                  <MessageSquare className="w-5 h-5 text-cyan-400" />
                </div>
              </div>
            </div>

            {/* Tool Executions */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-400 text-xs">Tool Executions</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {summaryLoading ? '...' : summary?.total_tool_executions || 0}
                  </p>
                </div>
                <div className="p-2 bg-purple-900/20 rounded-lg">
                  <Zap className="w-5 h-5 text-purple-400" />
                </div>
              </div>
            </div>

            {/* Active Models */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-gray-400 text-xs">Active Models</p>
                  <p className="text-xl font-bold text-white mt-1">
                    {summaryLoading ? '...' : summary?.by_model.length || 0}
                  </p>
                </div>
                <div className="p-2 bg-yellow-900/20 rounded-lg">
                  <Cpu className="w-5 h-5 text-yellow-400" />
                </div>
              </div>
            </div>
          </div>

          {/* Two Column Layout: Model Usage + Recent Activity */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Model Usage Breakdown */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-3">
                <Cpu className="w-4 h-4 text-yellow-400" />
                Usage by Model
              </h3>
              
              {summaryLoading ? (
                <div className="text-gray-500 text-sm">Loading...</div>
              ) : summary?.by_model.length === 0 ? (
                <div className="text-gray-500 text-sm">No model usage data</div>
              ) : (
                <div className="space-y-2">
                  {summary?.by_model.slice(0, 5).map((model) => {
                    const totalTokens = summary.total_tokens || 1;
                    const percentage = (model.total_tokens / totalTokens) * 100;
                    return (
                      <div key={model.model} className="space-y-1">
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-gray-300 truncate max-w-[60%]">
                            {model.model}
                          </span>
                          <span className="text-gray-400">
                            {formatTokens(model.total_tokens)} • {formatCost(model.estimated_cost_usd)}
                          </span>
                        </div>
                        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-gradient-to-r from-yellow-600 to-yellow-400 rounded-full"
                            style={{ width: `${percentage}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Recent Tool Executions */}
            <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
              <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-3">
                <Activity className="w-4 h-4 text-cyan-400" />
                Recent Tool Executions
              </h3>
              
              {recentExecutions.length === 0 ? (
                <div className="text-gray-500 text-sm">No recent executions</div>
              ) : (
                <div className="space-y-2">
                  {recentExecutions.map((exec) => (
                    <div
                      key={exec.id}
                      className="flex items-center gap-2 p-2 bg-gray-800/30 rounded-lg"
                    >
                      {getStatusIcon(exec.status)}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between">
                          <span className="text-gray-300 text-xs font-medium truncate">
                            {exec.tool_name}
                          </span>
                          <span className={`text-xs ${getStatusColor(exec.status)}`}>
                            {exec.status}
                          </span>
                        </div>
                        <div className="text-[10px] text-gray-500">
                          {formatDistanceToNow(new Date(exec.created_at), { addSuffix: true })}
                          {exec.duration_ms && <span> • {exec.duration_ms}ms</span>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Cost Breakdown */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-4">
            <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-3">
              <DollarSign className="w-4 h-4 text-green-400" />
              Cost Breakdown
            </h3>
            
            {summaryLoading ? (
              <div className="text-gray-500 text-sm">Loading...</div>
            ) : (
              <div className="flex flex-wrap items-center gap-6 text-sm">
                <div className="flex items-center gap-2">
                  <span className="text-gray-400">LLM Costs:</span>
                  <span className="text-white font-medium">
                    {formatCost(summary?.by_model.reduce((acc, m) => acc + m.estimated_cost_usd, 0) || 0)}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-gray-400">Tool Costs:</span>
                  <span className="text-white font-medium">
                    {formatCost(summary?.by_tool.reduce((acc, t) => acc + t.estimated_cost_usd, 0) || 0)}
                  </span>
                </div>
                <div className="flex items-center gap-2 border-l border-gray-700 pl-6">
                  <span className="text-gray-300 font-medium">Total:</span>
                  <span className="text-cyan-400 font-bold">
                    {formatCost(summary?.total_estimated_cost_usd || 0)}
                  </span>
                </div>
                <span className="text-xs text-gray-500">
                  Estimates based on current provider pricing
                </span>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
};

export default UsageAnalyticsPanel;
