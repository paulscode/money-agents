/**
 * AgentCard Component
 * 
 * Displays an agent's status, schedule, and key metrics.
 * Expandable to show run history and detailed statistics.
 */
import type { AgentSummary } from '@/types';
import { 
  useAgentRuns, 
  useAgentStatistics,
  usePauseAgent,
  useResumeAgent,
  useTriggerAgent,
} from '@/hooks/useAgents';
import { 
  formatDuration, 
  getStatusBadgeClasses,
} from '@/services/agentService';
import { RunHistoryPanel } from './RunHistoryPanel';
import { UtilizationChart } from './UtilizationChart';
import { CountdownTimer } from './CountdownTimer';
import { 
  Bot, 
  Play, 
  Pause, 
  Settings, 
  ChevronDown, 
  ChevronUp,
  Clock,
  Zap,
  DollarSign,
  Activity,
  AlertCircle,
  CheckCircle,
  XCircle,
  RefreshCw,
} from 'lucide-react';

interface AgentCardProps {
  agent: AgentSummary;
  isExpanded: boolean;
  onExpand: () => void;
  onConfigure: () => void;
}

export function AgentCard({ agent, isExpanded, onExpand, onConfigure }: AgentCardProps) {
  const { data: runs } = useAgentRuns(agent.slug, 10);
  const { data: stats } = useAgentStatistics(agent.slug, 7);
  
  const pauseMutation = usePauseAgent();
  const resumeMutation = useResumeAgent();
  const triggerMutation = useTriggerAgent();

  const isLoading = pauseMutation.isPending || resumeMutation.isPending || triggerMutation.isPending;

  const handlePause = () => {
    pauseMutation.mutate({ slug: agent.slug });
  };

  const handleResume = () => {
    resumeMutation.mutate(agent.slug);
  };

  const handleTrigger = () => {
    triggerMutation.mutate({ slug: agent.slug, reason: 'Manual trigger from UI' });
  };

  // next_run_at is used directly by CountdownTimer component

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
      {/* Main Card Content */}
      <div className="p-4">
        <div className="flex items-start justify-between gap-4">
          {/* Left: Agent Info */}
          <div className="flex items-start gap-3 min-w-0">
            <div className={`p-2 rounded-lg ${
              agent.status === 'running' 
                ? 'bg-neon-cyan/20' 
                : agent.status === 'error' || agent.status === 'budget_exceeded'
                ? 'bg-red-500/20'
                : 'bg-gray-800'
            }`}>
              <Bot className={`h-6 w-6 ${
                agent.status === 'running' 
                  ? 'text-neon-cyan animate-pulse' 
                  : agent.status === 'error' || agent.status === 'budget_exceeded'
                  ? 'text-red-500'
                  : 'text-gray-400'
              }`} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="text-lg font-semibold text-white truncate">
                  {agent.name}
                </h3>
                <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
                  getStatusBadgeClasses(agent.status)
                }`}>
                  {agent.status.replace('_', ' ')}
                </span>
                {!agent.is_enabled && (
                  <span className="px-2 py-0.5 text-xs font-medium rounded-full border bg-gray-700/50 text-gray-400 border-gray-600">
                    disabled
                  </span>
                )}
              </div>
              <p className="text-sm text-gray-400 mt-0.5 truncate">
                {agent.description}
              </p>
            </div>
          </div>

          {/* Right: Actions */}
          <div className="flex items-center gap-2 flex-shrink-0">
            {agent.status === 'running' ? (
              <button
                onClick={handlePause}
                disabled={isLoading}
                className="p-2 rounded-lg bg-yellow-500/20 text-yellow-500 hover:bg-yellow-500/30 
                         transition-colors disabled:opacity-50"
                title="Pause agent"
              >
                <Pause className="h-4 w-4" />
              </button>
            ) : agent.status === 'paused' || !agent.is_enabled ? (
              <button
                onClick={handleResume}
                disabled={isLoading}
                className="p-2 rounded-lg bg-green-500/20 text-green-500 hover:bg-green-500/30 
                         transition-colors disabled:opacity-50"
                title="Resume agent"
              >
                <Play className="h-4 w-4" />
              </button>
            ) : (
              <button
                onClick={handleTrigger}
                disabled={isLoading || agent.status === 'budget_exceeded'}
                className="p-2 rounded-lg bg-neon-cyan/20 text-neon-cyan hover:bg-neon-cyan/30 
                         transition-colors disabled:opacity-50"
                title="Run now"
              >
                {isLoading ? (
                  <RefreshCw className="h-4 w-4 animate-spin" />
                ) : (
                  <Zap className="h-4 w-4" />
                )}
              </button>
            )}
            <button
              onClick={onConfigure}
              className="p-2 rounded-lg bg-gray-800 text-gray-400 hover:text-white 
                       hover:bg-gray-700 transition-colors"
              title="Configure"
            >
              <Settings className="h-4 w-4" />
            </button>
            <button
              onClick={onExpand}
              className="p-2 rounded-lg bg-gray-800 text-gray-400 hover:text-white 
                       hover:bg-gray-700 transition-colors"
              title={isExpanded ? 'Collapse' : 'Expand'}
            >
              {isExpanded ? (
                <ChevronUp className="h-4 w-4" />
              ) : (
                <ChevronDown className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        {/* Status Message */}
        {agent.status_message && (
          <div className={`mt-3 p-2 rounded-lg text-sm ${
            agent.status === 'error' || agent.status === 'budget_exceeded'
              ? 'bg-red-500/10 text-red-400 border border-red-500/20'
              : 'bg-gray-800 text-gray-400'
          }`}>
            <AlertCircle className="h-4 w-4 inline mr-2" />
            {agent.status_message}
          </div>
        )}

        {/* Quick Stats Bar */}
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-4">
          {/* Schedule */}
          <div className="flex items-center gap-2">
            <Clock className="h-4 w-4 text-gray-500" />
            <div className="text-sm">
              <span className="text-gray-400">Every </span>
              <span className="text-white font-medium">
                {formatDuration(agent.schedule_interval_seconds)}
              </span>
            </div>
          </div>

          {/* Next Run */}
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-gray-500" />
            <div className="text-sm">
              {agent.status === 'running' ? (
                <span className="text-neon-cyan">Running now</span>
              ) : agent.next_run_at ? (
                <CountdownTimer
                  targetTime={agent.next_run_at}
                  label="Next: "
                  zeroText="Starting..."
                />
              ) : (
                <span className="text-gray-500">Not scheduled</span>
              )}
            </div>
          </div>

          {/* Success Rate */}
          <div className="flex items-center gap-2">
            {agent.success_rate >= 90 ? (
              <CheckCircle className="h-4 w-4 text-green-500" />
            ) : agent.success_rate >= 70 ? (
              <CheckCircle className="h-4 w-4 text-yellow-500" />
            ) : (
              <XCircle className="h-4 w-4 text-red-500" />
            )}
            <div className="text-sm">
              <span className={`font-medium ${
                agent.success_rate >= 90 ? 'text-green-500' 
                : agent.success_rate >= 70 ? 'text-yellow-500' 
                : 'text-red-500'
              }`}>
                {agent.success_rate.toFixed(0)}%
              </span>
              <span className="text-gray-400"> success</span>
            </div>
          </div>

          {/* Total Cost (All-Time) */}
          <div className="flex items-center gap-2">
            <DollarSign className={`h-4 w-4 ${
              agent.budget_warning ? 'text-orange-500' : 'text-gray-500'
            }`} />
            <div className="text-sm">
              <span className="text-white font-medium" title="All-time total cost">
                ${agent.total_cost_usd.toFixed(2)}
              </span>
              <span className="text-gray-500 ml-1">total</span>
            </div>
          </div>
        </div>

        {/* Budget Progress Bar */}
        {agent.budget_limit && (
          <div className="mt-3">
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="text-gray-500">
                {agent.budget_period} budget
              </span>
              <span className={`${agent.budget_warning ? 'text-orange-500' : 'text-gray-400'}`}>
                ${agent.budget_used.toFixed(2)} / ${agent.budget_limit.toFixed(0)}
              </span>
            </div>
            <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
              <div
                className={`h-full transition-all duration-300 ${
                  agent.budget_percentage_used >= 100
                    ? 'bg-red-500'
                    : agent.budget_warning
                    ? 'bg-orange-500'
                    : 'bg-neon-cyan'
                }`}
                style={{ width: `${Math.min(100, agent.budget_percentage_used)}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Expanded Content */}
      {isExpanded && (
        <div className="border-t border-gray-800 p-4 bg-gray-900/30">
          <div className="grid lg:grid-cols-2 gap-6">
            {/* Left: Run Statistics */}
            <div>
              <h4 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                <Activity className="h-4 w-4 text-neon-cyan" />
                Performance (Last 7 Days)
              </h4>
              {stats ? (
                <div className="space-y-4">
                  {/* Utilization Chart */}
                  <UtilizationChart 
                    avgDuration={stats.avg_duration_seconds}
                    minDuration={stats.min_duration_seconds}
                    maxDuration={stats.max_duration_seconds}
                    scheduleInterval={stats.schedule_interval_seconds}
                    utilization={stats.avg_utilization_percent}
                  />
                  
                  {/* Stats Grid */}
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <StatItem 
                      label="Total Runs" 
                      value={stats.total_runs.toString()} 
                    />
                    <StatItem 
                      label="Completed" 
                      value={stats.completed_runs.toString()} 
                      color="text-green-500"
                    />
                    <StatItem 
                      label="Avg Items" 
                      value={stats.avg_items_processed.toFixed(1)} 
                    />
                    <StatItem 
                      label="Week Cost" 
                      value={`$${stats.total_cost_usd.toFixed(2)}`} 
                    />
                  </div>
                </div>
              ) : (
                <div className="text-gray-500 text-sm">Loading statistics...</div>
              )}
            </div>

            {/* Right: Run History */}
            <div>
              <h4 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                <Clock className="h-4 w-4 text-neon-cyan" />
                Recent Runs
              </h4>
              {runs && runs.length > 0 ? (
                <RunHistoryPanel runs={runs} compact />
              ) : (
                <div className="text-gray-500 text-sm">No runs yet</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Helper Components
// =============================================================================

interface StatItemProps {
  label: string;
  value: string;
  color?: string;
}

function StatItem({ label, value, color = 'text-white' }: StatItemProps) {
  return (
    <div className="bg-gray-800/50 rounded-lg p-2">
      <div className="text-xs text-gray-500">{label}</div>
      <div className={`font-medium ${color}`}>{value}</div>
    </div>
  );
}
