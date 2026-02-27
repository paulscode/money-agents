/**
 * Agent Management API Service
 * 
 * Handles all agent-related API calls for the Agent Management UI.
 */
import apiClient from '@/lib/api-client';
import type {
  AgentSummary,
  AgentRunSummary,
  RunStatistics,
  AgentUpdateRequest,
  BudgetUpdateRequest,
} from '@/types';

// =============================================================================
// Agent CRUD Operations
// =============================================================================

const BASE_PATH = '/api/v1/agents/scheduler';

export async function getAgents(includeDisabled = false): Promise<AgentSummary[]> {
  const params = includeDisabled ? { include_disabled: true } : {};
  const response = await apiClient.get<AgentSummary[]>(BASE_PATH, { params });
  return response.data;
}

export async function getAgent(slug: string): Promise<AgentSummary> {
  const response = await apiClient.get<AgentSummary>(`${BASE_PATH}/${slug}`);
  return response.data;
}

export async function updateAgent(
  slug: string,
  data: AgentUpdateRequest
): Promise<AgentSummary> {
  const response = await apiClient.patch<AgentSummary>(`${BASE_PATH}/${slug}`, data);
  return response.data;
}

// =============================================================================
// Agent Actions
// =============================================================================

export interface ActionResponse {
  success: boolean;
  message: string;
  agent_slug?: string;
  run_id?: string;
}

export async function pauseAgent(slug: string, reason?: string): Promise<ActionResponse> {
  const response = await apiClient.post<ActionResponse>(`${BASE_PATH}/${slug}/pause`, null, {
    params: reason ? { reason } : {},
  });
  return response.data;
}

export async function resumeAgent(slug: string): Promise<ActionResponse> {
  const response = await apiClient.post<ActionResponse>(`${BASE_PATH}/${slug}/resume`);
  return response.data;
}

export async function triggerAgent(slug: string, reason?: string): Promise<ActionResponse> {
  const response = await apiClient.post<ActionResponse>(`${BASE_PATH}/${slug}/trigger`, { reason });
  return response.data;
}

// =============================================================================
// Budget Management
// =============================================================================

export interface BudgetInfo {
  agent_slug: string;
  budget_limit: number | null;
  budget_period: string;
  budget_used: number;
  budget_remaining: number | null;
  budget_percentage: number;
  is_exceeded: boolean;
  next_reset: string | null;
  warning_triggered: boolean;
}

export async function getAgentBudget(slug: string): Promise<BudgetInfo> {
  const response = await apiClient.get<BudgetInfo>(`${BASE_PATH}/${slug}/budget`);
  return response.data;
}

export async function updateAgentBudget(
  slug: string,
  data: BudgetUpdateRequest
): Promise<AgentSummary> {
  const response = await apiClient.patch<AgentSummary>(`${BASE_PATH}/${slug}/budget`, data);
  return response.data;
}

// =============================================================================
// Run History & Statistics
// =============================================================================

export async function getAgentRuns(
  slug: string,
  limit = 20
): Promise<AgentRunSummary[]> {
  const response = await apiClient.get<AgentRunSummary[]>(`${BASE_PATH}/${slug}/runs`, {
    params: { limit },
  });
  return response.data;
}

export async function getRecentRuns(limit = 50): Promise<AgentRunSummary[]> {
  const response = await apiClient.get<AgentRunSummary[]>(`${BASE_PATH}/runs/recent`, {
    params: { limit },
  });
  return response.data;
}

export async function getAgentStatistics(
  slug: string,
  days = 7
): Promise<RunStatistics> {
  const response = await apiClient.get<RunStatistics>(`${BASE_PATH}/${slug}/stats`, {
    params: { days },
  });
  return response.data;
}

// =============================================================================
// Schedule Helpers
// =============================================================================

/**
 * Convert seconds to a human-readable duration string.
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  }
  if (seconds < 3600) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
  }
  if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
}

/**
 * Parse a duration string (e.g., "6h", "30m", "1d") to seconds.
 */
export function parseDuration(duration: string): number | null {
  const match = duration.match(/^(\d+(?:\.\d+)?)\s*(s|m|h|d)?$/i);
  if (!match) return null;
  
  const value = parseFloat(match[1]);
  const unit = (match[2] || 's').toLowerCase();
  
  const multipliers: Record<string, number> = {
    s: 1,
    m: 60,
    h: 3600,
    d: 86400,
  };
  
  return Math.round(value * (multipliers[unit] || 1));
}

/**
 * Common schedule presets for the UI.
 */
export const SCHEDULE_PRESETS = [
  { label: '5 minutes', seconds: 300 },
  { label: '10 minutes', seconds: 600 },
  { label: '15 minutes', seconds: 900 },
  { label: '30 minutes', seconds: 1800 },
  { label: '1 hour', seconds: 3600 },
  { label: '2 hours', seconds: 7200 },
  { label: '4 hours', seconds: 14400 },
  { label: '6 hours', seconds: 21600 },
  { label: '12 hours', seconds: 43200 },
  { label: '24 hours', seconds: 86400 },
];

/**
 * Get the status color for an agent status.
 */
export function getStatusColor(status: string): string {
  const colors: Record<string, string> = {
    idle: 'text-gray-400',
    running: 'text-neon-cyan',
    paused: 'text-yellow-500',
    error: 'text-red-500',
    budget_exceeded: 'text-orange-500',
  };
  return colors[status] || 'text-gray-400';
}

/**
 * Get the status badge color classes.
 */
export function getStatusBadgeClasses(status: string): string {
  const classes: Record<string, string> = {
    idle: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    running: 'bg-neon-cyan/20 text-neon-cyan border-neon-cyan/30',
    paused: 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30',
    error: 'bg-red-500/20 text-red-500 border-red-500/30',
    budget_exceeded: 'bg-orange-500/20 text-orange-500 border-orange-500/30',
  };
  return classes[status] || classes.idle;
}

/**
 * Get the run status badge color classes.
 */
export function getRunStatusBadgeClasses(status: string): string {
  const classes: Record<string, string> = {
    pending: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    running: 'bg-neon-cyan/20 text-neon-cyan border-neon-cyan/30',
    completed: 'bg-green-500/20 text-green-400 border-green-500/30',
    failed: 'bg-red-500/20 text-red-500 border-red-500/30',
    cancelled: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    timeout: 'bg-orange-500/20 text-orange-500 border-orange-500/30',
  };
  return classes[status] || classes.pending;
}
