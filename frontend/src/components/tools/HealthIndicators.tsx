import { useState, useEffect } from 'react';
import { 
  Heart, 
  HeartPulse, 
  HeartCrack, 
  HelpCircle, 
  RefreshCw, 
  Clock, 
  Activity,
  Settings,
  CheckCircle,
  AlertTriangle,
  XCircle,
} from 'lucide-react';
import { toolHealthService, UnhealthyTool } from '../../services/toolHealth';
import type { ToolHealthStatus, ToolHealthSummary, ToolHealthCheck, HealthStatus } from '../../types';
import { logError } from '@/lib/logger';

// =============================================================================
// Health Status Badge - Compact indicator for lists
// =============================================================================

interface HealthBadgeProps {
  status: HealthStatus | null;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
  className?: string;
}

/**
 * Get icon and color for health status
 */
function getHealthStatusConfig(status: HealthStatus | null) {
  switch (status) {
    case 'healthy':
      return {
        icon: Heart,
        color: 'text-green-500',
        bgColor: 'bg-green-100',
        label: 'Healthy',
      };
    case 'degraded':
      return {
        icon: HeartPulse,
        color: 'text-yellow-500',
        bgColor: 'bg-yellow-100',
        label: 'Degraded',
      };
    case 'unhealthy':
      return {
        icon: HeartCrack,
        color: 'text-red-500',
        bgColor: 'bg-red-100',
        label: 'Unhealthy',
      };
    default:
      return {
        icon: HelpCircle,
        color: 'text-gray-400',
        bgColor: 'bg-gray-100',
        label: 'Unknown',
      };
  }
}

/**
 * Compact health status badge for tool lists
 */
export function HealthBadge({ status, size = 'md', showLabel = false, className = '' }: HealthBadgeProps) {
  const config = getHealthStatusConfig(status);
  const Icon = config.icon;
  
  const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-5 h-5',
    lg: 'w-6 h-6',
  };
  
  return (
    <div 
      className={`inline-flex items-center gap-1.5 ${className}`}
      title={`Health: ${config.label}`}
    >
      <Icon className={`${sizeClasses[size]} ${config.color}`} />
      {showLabel && (
        <span className={`text-sm font-medium ${config.color}`}>{config.label}</span>
      )}
    </div>
  );
}

// =============================================================================
// Health Status Pill - For tool detail views
// =============================================================================

interface HealthPillProps {
  status: HealthStatus | null;
  message?: string | null;
  responseTime?: number | null;
  lastChecked?: string | null;
  className?: string;
}

/**
 * Format response time for display
 */
function formatResponseTime(ms: number | null): string {
  if (ms === null || ms === undefined) return '-';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/**
 * Format last checked time
 */
function formatLastChecked(timestamp: string | null): string {
  if (!timestamp) return 'Never checked';
  
  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  
  if (diffMs < 60000) return 'Just now';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)}m ago`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)}h ago`;
  return date.toLocaleDateString();
}

/**
 * Detailed health status pill with message and timing
 */
export function HealthPill({ 
  status, 
  message, 
  responseTime, 
  lastChecked,
  className = '' 
}: HealthPillProps) {
  const config = getHealthStatusConfig(status);
  const Icon = config.icon;
  
  return (
    <div className={`rounded-lg p-3 ${config.bgColor} ${className}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className={`w-5 h-5 ${config.color}`} />
          <span className={`font-medium ${config.color}`}>{config.label}</span>
        </div>
        {responseTime !== null && (
          <div className="flex items-center gap-1 text-sm text-gray-600">
            <Clock className="w-4 h-4" />
            {formatResponseTime(responseTime)}
          </div>
        )}
      </div>
      {message && (
        <p className="mt-1 text-sm text-gray-600">{message}</p>
      )}
      {lastChecked && (
        <p className="mt-1 text-xs text-gray-500">
          Last checked: {formatLastChecked(lastChecked)}
        </p>
      )}
    </div>
  );
}

// =============================================================================
// Tool Health Card - Full status with actions
// =============================================================================

interface ToolHealthCardProps {
  toolId: string;
  onSettingsClick?: () => void;
  className?: string;
}

/**
 * Full health status card for a single tool
 */
export function ToolHealthCard({ toolId, onSettingsClick, className = '' }: ToolHealthCardProps) {
  const [status, setStatus] = useState<ToolHealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const fetchHealth = async () => {
    try {
      setLoading(true);
      const data = await toolHealthService.getToolHealth(toolId);
      setStatus(data);
      setError(null);
    } catch (err) {
      setError('Failed to load health status');
      logError('Health status error:', err);
    } finally {
      setLoading(false);
    }
  };
  
  const runHealthCheck = async () => {
    try {
      setChecking(true);
      const result = await toolHealthService.checkToolHealth(toolId);
      // Update status with result
      setStatus(prev => prev ? {
        ...prev,
        status: result.status,
        message: result.message,
        response_time_ms: result.response_time_ms,
        last_checked: result.checked_at,
      } : null);
    } catch (err) {
      logError('Health check error:', err);
    } finally {
      setChecking(false);
    }
  };
  
  useEffect(() => {
    fetchHealth();
  }, [toolId]);
  
  if (loading) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="animate-pulse">
          <div className="h-6 bg-gray-200 rounded w-1/3 mb-2"></div>
          <div className="h-4 bg-gray-200 rounded w-2/3"></div>
        </div>
      </div>
    );
  }
  
  if (error || !status) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-red-500 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          {error || 'Unable to load health status'}
        </div>
      </div>
    );
  }
  
  const config = getHealthStatusConfig(status.status);
  const Icon = config.icon;
  
  return (
    <div className={`bg-white rounded-lg shadow ${className}`}>
      <div className="p-4 border-b">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-900 flex items-center gap-2">
            <Activity className="w-5 h-5 text-gray-500" />
            Health Status
          </h3>
          <div className="flex items-center gap-2">
            <button
              onClick={runHealthCheck}
              disabled={checking}
              className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors disabled:opacity-50"
              title="Run health check"
            >
              <RefreshCw className={`w-4 h-4 ${checking ? 'animate-spin' : ''}`} />
            </button>
            {onSettingsClick && (
              <button
                onClick={onSettingsClick}
                className="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
                title="Health check settings"
              >
                <Settings className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
      </div>
      
      <div className="p-4">
        <HealthPill
          status={status.status}
          message={status.message}
          responseTime={status.response_time_ms}
          lastChecked={status.last_checked}
        />
        
        <div className="mt-4 grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-gray-500">Auto-check:</span>
            <span className={`ml-2 ${status.health_check_enabled ? 'text-green-600' : 'text-gray-400'}`}>
              {status.health_check_enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
          <div>
            <span className="text-gray-500">Interval:</span>
            <span className="ml-2 text-gray-900">
              {status.health_check_interval_minutes}m
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Health Summary Card - Dashboard overview
// =============================================================================

interface HealthSummaryCardProps {
  onViewAll?: () => void;
  className?: string;
}

/**
 * Dashboard card showing health summary across all tools
 */
export function HealthSummaryCard({ onViewAll, className = '' }: HealthSummaryCardProps) {
  const [summary, setSummary] = useState<ToolHealthSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const fetchSummary = async () => {
    try {
      setLoading(true);
      const data = await toolHealthService.getHealthSummary();
      setSummary(data);
      setError(null);
    } catch (err) {
      setError('Failed to load health summary');
      logError('Health summary error:', err);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchSummary();
    // Refresh every 5 minutes
    const interval = setInterval(fetchSummary, 300000);
    return () => clearInterval(interval);
  }, []);
  
  if (loading) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="animate-pulse">
          <div className="h-6 bg-gray-200 rounded w-1/3 mb-4"></div>
          <div className="grid grid-cols-4 gap-4">
            {[1, 2, 3, 4].map(i => (
              <div key={i} className="h-12 bg-gray-200 rounded"></div>
            ))}
          </div>
        </div>
      </div>
    );
  }
  
  if (error || !summary) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-red-500 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          {error || 'Unable to load health summary'}
        </div>
      </div>
    );
  }
  
  const statCards = [
    { label: 'Healthy', value: summary.healthy, icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50' },
    { label: 'Degraded', value: summary.degraded, icon: AlertTriangle, color: 'text-yellow-500', bg: 'bg-yellow-50' },
    { label: 'Unhealthy', value: summary.unhealthy, icon: XCircle, color: 'text-red-500', bg: 'bg-red-50' },
    { label: 'Unknown', value: summary.unknown, icon: HelpCircle, color: 'text-gray-400', bg: 'bg-gray-50' },
  ];
  
  return (
    <div className={`bg-white rounded-lg shadow ${className}`}>
      <div className="p-4 border-b flex items-center justify-between">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <HeartPulse className="w-5 h-5 text-gray-500" />
          Tool Health Overview
        </h3>
        {onViewAll && (
          <button
            onClick={onViewAll}
            className="text-sm text-blue-600 hover:text-blue-800"
          >
            View all →
          </button>
        )}
      </div>
      
      <div className="p-4">
        <div className="grid grid-cols-4 gap-4">
          {statCards.map(stat => (
            <div key={stat.label} className={`${stat.bg} rounded-lg p-3 text-center`}>
              <stat.icon className={`w-6 h-6 ${stat.color} mx-auto mb-1`} />
              <p className="text-2xl font-bold text-gray-900">{stat.value}</p>
              <p className="text-xs text-gray-500">{stat.label}</p>
            </div>
          ))}
        </div>
        
        <div className="mt-4 flex items-center justify-between text-sm text-gray-500">
          <span>{summary.total_tools} total tools</span>
          <span>{summary.health_checks_enabled} with auto-check</span>
        </div>
      </div>
    </div>
  );
}

// =============================================================================
// Unhealthy Tools List - Alert panel
// =============================================================================

interface UnhealthyToolsListProps {
  onToolClick?: (toolId: string) => void;
  maxItems?: number;
  className?: string;
}

/**
 * List of tools that are unhealthy or degraded
 */
export function UnhealthyToolsList({ onToolClick, maxItems = 5, className = '' }: UnhealthyToolsListProps) {
  const [tools, setTools] = useState<UnhealthyTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const fetchUnhealthy = async () => {
    try {
      setLoading(true);
      const data = await toolHealthService.getUnhealthyTools();
      setTools(data.slice(0, maxItems));
      setError(null);
    } catch (err) {
      setError('Failed to load unhealthy tools');
      logError('Unhealthy tools error:', err);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchUnhealthy();
  }, [maxItems]);
  
  if (loading) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="animate-pulse space-y-3">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-12 bg-gray-200 rounded"></div>
          ))}
        </div>
      </div>
    );
  }
  
  if (error) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-red-500 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          {error}
        </div>
      </div>
    );
  }
  
  if (tools.length === 0) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-center text-gray-500">
          <CheckCircle className="w-8 h-8 mx-auto mb-2 text-green-500" />
          <p>All tools are healthy!</p>
        </div>
      </div>
    );
  }
  
  return (
    <div className={`bg-white rounded-lg shadow ${className}`}>
      <div className="p-4 border-b">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-yellow-500" />
          Tools Needing Attention
        </h3>
      </div>
      
      <div className="divide-y">
        {tools.map(tool => (
          <div
            key={tool.id}
            className={`p-4 hover:bg-gray-50 ${onToolClick ? 'cursor-pointer' : ''}`}
            onClick={() => onToolClick?.(tool.id)}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <HealthBadge status={tool.status} size="sm" />
                <span className="font-medium text-gray-900">{tool.name}</span>
              </div>
              {tool.health_response_ms && (
                <span className="text-sm text-gray-500">
                  {formatResponseTime(tool.health_response_ms)}
                </span>
              )}
            </div>
            {tool.message && (
              <p className="mt-1 text-sm text-gray-600 truncate">{tool.message}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// =============================================================================
// Health History Chart - For detailed view
// =============================================================================

interface HealthHistoryProps {
  toolId: string;
  limit?: number;
  className?: string;
}

/**
 * Display health check history for a tool
 */
export function HealthHistory({ toolId, limit = 20, className = '' }: HealthHistoryProps) {
  const [history, setHistory] = useState<ToolHealthCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const fetchHistory = async () => {
    try {
      setLoading(true);
      const data = await toolHealthService.getToolHealthHistory(toolId, limit);
      setHistory(data.history);
      setError(null);
    } catch (err) {
      setError('Failed to load health history');
      logError('Health history error:', err);
    } finally {
      setLoading(false);
    }
  };
  
  useEffect(() => {
    fetchHistory();
  }, [toolId, limit]);
  
  if (loading) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="animate-pulse space-y-2">
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="h-8 bg-gray-200 rounded"></div>
          ))}
        </div>
      </div>
    );
  }
  
  if (error) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-red-500 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5" />
          {error}
        </div>
      </div>
    );
  }
  
  if (history.length === 0) {
    return (
      <div className={`bg-white rounded-lg shadow p-4 ${className}`}>
        <div className="text-center text-gray-500">
          <Clock className="w-8 h-8 mx-auto mb-2" />
          <p>No health check history yet</p>
        </div>
      </div>
    );
  }
  
  return (
    <div className={`bg-white rounded-lg shadow ${className}`}>
      <div className="p-4 border-b">
        <h3 className="font-semibold text-gray-900 flex items-center gap-2">
          <Clock className="w-5 h-5 text-gray-500" />
          Health Check History
        </h3>
      </div>
      
      <div className="divide-y max-h-96 overflow-y-auto">
        {history.map(check => {
          const config = getHealthStatusConfig(check.status);
          const Icon = config.icon;
          
          return (
            <div key={check.id} className="p-3 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <Icon className={`w-4 h-4 ${config.color}`} />
                <div>
                  <span className={`text-sm font-medium ${config.color}`}>
                    {config.label}
                  </span>
                  {check.message && (
                    <p className="text-xs text-gray-500 truncate max-w-xs">
                      {check.message}
                    </p>
                  )}
                </div>
              </div>
              <div className="text-right">
                <p className="text-sm text-gray-600">
                  {formatResponseTime(check.response_time_ms)}
                </p>
                <p className="text-xs text-gray-400">
                  {formatLastChecked(check.checked_at)}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// =============================================================================
// Health Settings Modal - Configure auto-checks
// =============================================================================

interface HealthSettingsModalProps {
  toolId: string;
  isOpen: boolean;
  onClose: () => void;
  onSaved?: () => void;
}

/**
 * Modal for configuring health check settings
 */
export function HealthSettingsModal({ 
  toolId, 
  isOpen, 
  onClose, 
  onSaved 
}: HealthSettingsModalProps) {
  const [enabled, setEnabled] = useState(false);
  const [interval, setInterval] = useState(30);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const fetchSettings = async () => {
    try {
      setLoading(true);
      const status = await toolHealthService.getToolHealth(toolId);
      setEnabled(status.health_check_enabled);
      setInterval(status.health_check_interval_minutes);
      setError(null);
    } catch (err) {
      setError('Failed to load settings');
      logError('Settings error:', err);
    } finally {
      setLoading(false);
    }
  };
  
  const handleSave = async () => {
    try {
      setSaving(true);
      await toolHealthService.updateHealthSettings(toolId, {
        health_check_enabled: enabled,
        health_check_interval_minutes: interval,
      });
      onSaved?.();
      onClose();
    } catch (err) {
      setError('Failed to save settings');
      logError('Save error:', err);
    } finally {
      setSaving(false);
    }
  };
  
  useEffect(() => {
    if (isOpen) {
      fetchSettings();
    }
  }, [isOpen, toolId]);
  
  if (!isOpen) return null;
  
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="p-4 border-b flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900">
            Health Check Settings
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600"
          >
            <XCircle className="w-5 h-5" />
          </button>
        </div>
        
        <div className="p-4">
          {loading ? (
            <div className="animate-pulse space-y-4">
              <div className="h-8 bg-gray-200 rounded"></div>
              <div className="h-8 bg-gray-200 rounded"></div>
            </div>
          ) : (
            <>
              {error && (
                <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg">
                  {error}
                </div>
              )}
              
              <div className="space-y-4">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-700">
                    Enable automatic health checks
                  </label>
                  <button
                    onClick={() => setEnabled(!enabled)}
                    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                      enabled ? 'bg-blue-600' : 'bg-gray-300'
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                        enabled ? 'translate-x-6' : 'translate-x-1'
                      }`}
                    />
                  </button>
                </div>
                
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Check interval (minutes)
                  </label>
                  <select
                    value={interval}
                    onChange={(e) => setInterval(Number(e.target.value))}
                    disabled={!enabled}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-100 disabled:text-gray-500"
                  >
                    <option value={5}>5 minutes</option>
                    <option value={15}>15 minutes</option>
                    <option value={30}>30 minutes</option>
                    <option value={60}>1 hour</option>
                    <option value={360}>6 hours</option>
                    <option value={1440}>24 hours</option>
                  </select>
                </div>
              </div>
            </>
          )}
        </div>
        
        <div className="p-4 border-t flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={loading || saving}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default {
  HealthBadge,
  HealthPill,
  ToolHealthCard,
  HealthSummaryCard,
  UnhealthyToolsList,
  HealthHistory,
  HealthSettingsModal,
};
