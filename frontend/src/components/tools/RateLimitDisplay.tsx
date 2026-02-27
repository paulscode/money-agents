import { useState, useEffect } from 'react';
import { AlertTriangle, Clock, TrendingUp, Shield, RefreshCw } from 'lucide-react';
import { rateLimitsService, RateLimitStatus, RateLimitSummary, RateLimitPeriod } from '../../services/rateLimits';
import { logError } from '@/lib/logger';

interface RateLimitDisplayProps {
  toolId?: string;
  showSummary?: boolean;
  compact?: boolean;
  className?: string;
}

/**
 * Format period for display
 */
function formatPeriod(period: RateLimitPeriod): string {
  const labels: Record<RateLimitPeriod, string> = {
    minute: 'per minute',
    hour: 'per hour',
    day: 'per day',
    week: 'per week',
    month: 'per month',
  };
  return labels[period] || period;
}

/**
 * Format time remaining until period reset
 */
function formatTimeRemaining(periodEnd: string | null): string {
  if (!periodEnd) return '';
  
  const end = new Date(periodEnd);
  const now = new Date();
  const diffMs = end.getTime() - now.getTime();
  
  if (diffMs <= 0) return 'Resetting...';
  
  const hours = Math.floor(diffMs / (1000 * 60 * 60));
  const minutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));
  const seconds = Math.floor((diffMs % (1000 * 60)) / 1000);
  
  if (hours > 0) return `${hours}h ${minutes}m`;
  if (minutes > 0) return `${minutes}m ${seconds}s`;
  return `${seconds}s`;
}

/**
 * Get color based on usage percentage
 */
function getUsageColor(remaining: number, max: number): string {
  if (max === 0) return 'bg-gray-300';
  const percentage = (remaining / max) * 100;
  if (percentage > 50) return 'bg-green-500';
  if (percentage > 20) return 'bg-yellow-500';
  return 'bg-red-500';
}

/**
 * Display rate limit status for a specific tool
 */
export function RateLimitIndicator({ toolId, compact = false, className = '' }: { 
  toolId: string; 
  compact?: boolean;
  className?: string;
}) {
  const [status, setStatus] = useState<RateLimitStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStatus = async () => {
    try {
      setLoading(true);
      const data = await rateLimitsService.checkRateLimit(toolId);
      setStatus(data);
      setError(null);
    } catch (err) {
      setError('Failed to load rate limit');
      logError('Rate limit check error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    // Refresh every 30 seconds
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [toolId]);

  if (loading) {
    return (
      <div className={`flex items-center gap-2 text-sm text-gray-500 ${className}`}>
        <RefreshCw className="h-4 w-4 animate-spin" />
        {!compact && <span>Loading...</span>}
      </div>
    );
  }

  if (error || !status) {
    return null; // Don't show anything if there's an error or no limits
  }

  // No limits configured
  if (status.max_count === 0 && status.remaining === 0) {
    return null;
  }

  const percentage = status.max_count > 0 ? ((status.remaining / status.max_count) * 100) : 100;
  const isBlocked = !status.allowed;

  if (compact) {
    return (
      <div className={`flex items-center gap-1 ${className}`}>
        {isBlocked ? (
          <span className="text-xs text-red-600 font-medium flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" />
            Rate limited
          </span>
        ) : (
          <span className={`text-xs ${percentage > 20 ? 'text-gray-500' : 'text-yellow-600'}`}>
            {status.remaining}/{status.max_count}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className={`rounded-lg border p-3 ${isBlocked ? 'border-red-200 bg-red-50' : 'border-gray-200 bg-white'} ${className}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Shield className={`h-4 w-4 ${isBlocked ? 'text-red-500' : 'text-gray-400'}`} />
          <span className="text-sm font-medium">
            {status.limit_name || 'Rate Limit'}
          </span>
        </div>
        {status.period && (
          <span className="text-xs text-gray-500">
            {formatPeriod(status.period)}
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-2 bg-gray-200 rounded-full overflow-hidden mb-2">
        <div
          className={`h-full transition-all ${getUsageColor(status.remaining, status.max_count)}`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
        />
      </div>

      <div className="flex items-center justify-between text-xs">
        <span className={isBlocked ? 'text-red-600 font-medium' : 'text-gray-600'}>
          {isBlocked ? (
            <>
              <AlertTriangle className="h-3 w-3 inline mr-1" />
              Limit exceeded
            </>
          ) : (
            `${status.remaining} of ${status.max_count} remaining`
          )}
        </span>
        {status.period_end && (
          <span className="text-gray-500 flex items-center gap-1">
            <Clock className="h-3 w-3" />
            Resets in {formatTimeRemaining(status.period_end)}
          </span>
        )}
      </div>

      {isBlocked && status.retry_after_seconds && (
        <div className="mt-2 text-xs text-red-600">
          Try again in {Math.ceil(status.retry_after_seconds / 60)} minutes
        </div>
      )}
    </div>
  );
}

/**
 * Display a summary of all rate limits for the current user
 */
export function RateLimitSummaryCard({ toolId, className = '' }: { 
  toolId?: string;
  className?: string;
}) {
  const [summary, setSummary] = useState<RateLimitSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchSummary = async () => {
    try {
      setLoading(true);
      const data = toolId 
        ? await rateLimitsService.getToolSummary(toolId)
        : await rateLimitsService.getMySummary();
      setSummary(data);
      setError(null);
    } catch (err) {
      setError('Failed to load rate limits');
      logError('Rate limit summary error:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSummary();
    const interval = setInterval(fetchSummary, 60000); // Refresh every minute
    return () => clearInterval(interval);
  }, [toolId]);

  if (loading) {
    return (
      <div className={`bg-white rounded-lg border p-4 ${className}`}>
        <div className="flex items-center gap-2 text-gray-500">
          <RefreshCw className="h-4 w-4 animate-spin" />
          Loading rate limits...
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={`bg-white rounded-lg border p-4 ${className}`}>
        <div className="text-sm text-red-600">{error}</div>
      </div>
    );
  }

  if (!summary || summary.limits.length === 0) {
    return (
      <div className={`bg-white rounded-lg border p-4 ${className}`}>
        <div className="flex items-center gap-2 text-gray-500">
          <Shield className="h-4 w-4" />
          <span className="text-sm">No rate limits configured</span>
        </div>
      </div>
    );
  }

  const isUnlimited = summary.total_remaining === -1;
  const isLow = !isUnlimited && summary.total_remaining < 10;

  return (
    <div className={`bg-white rounded-lg border ${isLow ? 'border-yellow-300' : ''} ${className}`}>
      <div className="p-4 border-b">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="h-5 w-5 text-gray-400" />
            <h3 className="font-medium">Rate Limits</h3>
          </div>
          {!isUnlimited && (
            <span className={`text-sm font-medium ${isLow ? 'text-yellow-600' : 'text-green-600'}`}>
              {summary.total_remaining} remaining
            </span>
          )}
        </div>
      </div>

      <div className="divide-y">
        {summary.limits.map((limit) => {
          const percentage = limit.max_executions > 0 
            ? (limit.remaining / limit.max_executions) * 100 
            : 100;

          return (
            <div key={limit.id} className="p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="text-sm font-medium">
                  {limit.name || `${limit.scope} limit`}
                </span>
                <span className="text-xs text-gray-500">
                  {formatPeriod(limit.period)}
                </span>
              </div>
              
              <div className="h-1.5 bg-gray-200 rounded-full overflow-hidden mb-1">
                <div
                  className={`h-full transition-all ${getUsageColor(limit.remaining, limit.max_executions)}`}
                  style={{ width: `${Math.min(percentage, 100)}%` }}
                />
              </div>
              
              <div className="flex items-center justify-between text-xs text-gray-500">
                <span>{limit.current_count} used / {limit.max_executions} total</span>
                <span>{formatTimeRemaining(limit.period_end)}</span>
              </div>
            </div>
          );
        })}
      </div>

      {summary.most_restrictive && summary.most_restrictive.remaining < 5 && (
        <div className="p-3 bg-yellow-50 border-t border-yellow-200">
          <div className="flex items-center gap-2 text-yellow-700 text-sm">
            <AlertTriangle className="h-4 w-4" />
            <span>
              Approaching limit: {summary.most_restrictive.remaining} executions remaining 
              {formatPeriod(summary.most_restrictive.period)}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Main component that can show either a single tool's status or a full summary
 */
export default function RateLimitDisplay({ 
  toolId, 
  showSummary = false, 
  compact = false,
  className = '' 
}: RateLimitDisplayProps) {
  if (showSummary) {
    return <RateLimitSummaryCard toolId={toolId} className={className} />;
  }

  if (toolId) {
    return <RateLimitIndicator toolId={toolId} compact={compact} className={className} />;
  }

  return <RateLimitSummaryCard className={className} />;
}
