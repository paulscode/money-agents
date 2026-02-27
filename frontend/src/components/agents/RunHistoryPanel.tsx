/**
 * RunHistoryPanel Component
 * 
 * Displays a list of agent runs with status, duration, and results.
 */
import type { AgentRunSummary } from '@/types';
import { formatDuration, getRunStatusBadgeClasses } from '@/services/agentService';
import { 
  Clock, 
  Zap, 
  DollarSign,
  Hash,
  AlertCircle,
  User,
} from 'lucide-react';

interface RunHistoryPanelProps {
  runs: AgentRunSummary[];
  showAgentName?: boolean;
  compact?: boolean;
}

export function RunHistoryPanel({ runs, showAgentName, compact }: RunHistoryPanelProps) {
  if (runs.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        No runs recorded yet
      </div>
    );
  }

  if (compact) {
    return (
      <div className="space-y-2 max-h-80 overflow-y-auto">
        {runs.map((run) => (
          <CompactRunItem key={run.id} run={run} />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {runs.map((run) => (
        <RunItem key={run.id} run={run} showAgentName={showAgentName} />
      ))}
    </div>
  );
}

// =============================================================================
// Run Item Components
// =============================================================================

interface RunItemProps {
  run: AgentRunSummary;
  showAgentName?: boolean;
}

function RunItem({ run, showAgentName }: RunItemProps) {
  const startTime = run.started_at ? new Date(run.started_at) : null;
  const isRecent = startTime && Date.now() - startTime.getTime() < 3600000; // Last hour

  return (
    <div className={`bg-gray-800/50 border border-gray-700 rounded-lg p-4 ${
      run.status === 'running' ? 'border-neon-cyan/50' : isRecent ? 'border-gray-600' : ''
    }`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          {showAgentName && (
            <span className="text-sm font-medium text-neon-cyan">
              {run.agent_slug}
            </span>
          )}
          <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${
            getRunStatusBadgeClasses(run.status)
          }`}>
            {run.status}
          </span>
          <span className="text-xs text-gray-500 capitalize px-2 py-0.5 bg-gray-800 rounded-full">
            {run.trigger_type}
          </span>
        </div>
        <div className="text-xs text-gray-500">
          {startTime ? formatRelativeTime(startTime) : 'Pending'}
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-3 text-sm">
        <StatBadge
          icon={Clock}
          value={run.duration_seconds ? formatDuration(run.duration_seconds) : '-'}
          label="Duration"
          highlight={run.status === 'running'}
        />
        <StatBadge
          icon={Hash}
          value={run.items_processed.toString()}
          label="Processed"
        />
        <StatBadge
          icon={Zap}
          value={run.tokens_used.toLocaleString()}
          label="Tokens"
        />
        <StatBadge
          icon={DollarSign}
          value={`$${run.cost_usd.toFixed(3)}`}
          label="Cost"
        />
      </div>

      {/* Error Message */}
      {run.error_message && (
        <div className="mt-3 p-2 bg-red-500/10 border border-red-500/20 rounded-lg">
          <div className="flex items-start gap-2 text-sm text-red-400">
            <AlertCircle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <span className="line-clamp-2">{run.error_message}</span>
          </div>
        </div>
      )}

      {/* Trigger Reason */}
      {run.trigger_reason && (
        <div className="mt-2 text-xs text-gray-500 truncate">
          <User className="h-3 w-3 inline mr-1" />
          {run.trigger_reason}
        </div>
      )}
    </div>
  );
}

function CompactRunItem({ run }: { run: AgentRunSummary }) {
  const startTime = run.started_at ? new Date(run.started_at) : null;

  return (
    <div className={`flex items-center gap-3 p-2 rounded-lg ${
      run.status === 'running' 
        ? 'bg-neon-cyan/10 border border-neon-cyan/30' 
        : 'bg-gray-800/50'
    }`}>
      {/* Status */}
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
        run.status === 'completed' ? 'bg-green-500' :
        run.status === 'running' ? 'bg-neon-cyan animate-pulse' :
        run.status === 'failed' ? 'bg-red-500' :
        'bg-gray-500'
      }`} />

      {/* Time */}
      <div className="text-xs text-gray-500 w-16 flex-shrink-0">
        {startTime ? formatRelativeTime(startTime) : 'Pending'}
      </div>

      {/* Duration */}
      <div className="text-sm text-white w-12 flex-shrink-0">
        {run.duration_seconds ? formatDuration(run.duration_seconds) : '-'}
      </div>

      {/* Items */}
      <div className="text-xs text-gray-400 flex-1">
        {run.items_processed} items
      </div>

      {/* Cost */}
      <div className="text-xs text-gray-500 w-16 text-right">
        ${run.cost_usd.toFixed(3)}
      </div>

      {/* Error indicator */}
      {run.status === 'failed' && (
        <AlertCircle className="h-4 w-4 text-red-500 flex-shrink-0" />
      )}
    </div>
  );
}

// =============================================================================
// Helper Components
// =============================================================================

interface StatBadgeProps {
  icon: React.ComponentType<{ className?: string }>;
  value: string;
  label: string;
  highlight?: boolean;
}

function StatBadge({ icon: Icon, value, label, highlight }: StatBadgeProps) {
  return (
    <div className="text-center">
      <div className={`flex items-center justify-center gap-1 ${
        highlight ? 'text-neon-cyan' : 'text-white'
      }`}>
        <Icon className="h-3 w-3 text-gray-500" />
        <span className="font-medium">{value}</span>
      </div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

// =============================================================================
// Helpers
// =============================================================================

function formatRelativeTime(date: Date): string {
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  
  return date.toLocaleDateString();
}
