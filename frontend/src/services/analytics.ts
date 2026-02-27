/**
 * Analytics Service - Frontend API client for analytics endpoints
 * 
 * Provides:
 * - Tool operations summary
 * - Active alerts
 * - Execution trends
 * - Violation trends
 */
import apiClient from '../lib/api-client';

const BASE_PATH = '/api/v1/analytics';

// =============================================================================
// Types
// =============================================================================

export interface HealthSummary {
  healthy: number;
  degraded: number;
  unhealthy: number;
  unknown: number;
  total: number;
}

export interface ApprovalSummary {
  pending_count: number;
  critical_count: number;
  high_count: number;
  medium_count: number;
  low_count: number;
  oldest_pending_minutes: number | null;
}

export interface RateLimitAlert {
  tool_id: string | null;
  tool_name: string;
  tool_slug: string;
  limit_name: string;
  current_usage: number;
  max_allowed: number;
  usage_percent: number;
  period: string;
  period_resets_at: string | null;
}

export interface UnhealthyTool {
  id: string;
  name: string;
  slug: string;
  health_status: string;
  health_message: string | null;
  last_health_check: string | null;
  response_time_ms: number | null;
  unhealthy_minutes: number | null;
}

export interface PendingApproval {
  id: string;
  urgency: string;
  tool_id: string;
  tool_name: string;
  tool_slug: string;
  reason: string;
  estimated_cost: number | null;
  requested_at: string | null;
  pending_minutes: number | null;
}

export interface ToolOperationsSummary {
  health: HealthSummary;
  approvals: ApprovalSummary;
  rate_limit_alerts: RateLimitAlert[];
  unhealthy_tools: UnhealthyTool[];
  recent_violations_count: number;
  pending_approvals: PendingApproval[];
}

export interface Alert {
  id: string;
  severity: 'critical' | 'high' | 'medium' | 'low';
  category: 'health' | 'approval' | 'rate_limit' | 'budget' | 'agent' | 'campaign';
  title: string;
  message: string;
  source_type: string;
  source_id: string | null;
  source_name: string | null;
  action_url: string | null;
  created_at: string | null;
}

export interface AlertCounts {
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

export interface ExecutionTrend {
  hour: string | null;
  tool_id: string;
  tool_slug: string;
  tool_name: string;
  execution_count: number;
  success_count: number;
  failure_count: number;
  avg_duration_ms: number;
}

export interface ViolationTrend {
  day: string | null;
  tool_id: string | null;
  tool_slug: string;
  tool_name: string;
  violation_count: number;
}

// Agent Analytics Types
export interface FailureReason {
  reason: string;
  count: number;
}

export interface AgentPerformance {
  agent_id: string;
  agent_slug: string;
  agent_name: string;
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  success_rate: number;
  avg_duration_seconds: number;
  avg_cost_usd: number;
  total_cost_usd: number;
  avg_items_processed: number;
  top_failure_reasons: FailureReason[];
}

export interface CostTrendPoint {
  date: string;
  agent_slug: string;
  agent_name: string;
  total_cost_usd: number;
  run_count: number;
}

export interface AgentSuggestion {
  agent_slug: string;
  agent_name: string;
  suggestion_type: 'efficiency' | 'cost' | 'schedule' | 'reliability';
  severity: 'info' | 'warning' | 'recommendation';
  title: string;
  description: string;
  potential_savings: string | null;
  action: string | null;
}

// Campaign Intelligence Types
export interface PatternSummary {
  id: string;
  name: string;
  pattern_type: string;
  status: string;
  success_rate: number;
  confidence_score: number;
  times_used: number;
  agent_type: string | null;
  target_market: string | null;
  average_yield: number | null;
  is_global: boolean;
  user_id: string | null;
  created_at: string | null;
}

export interface LessonSummary {
  id: string;
  title: string;
  category: string;
  severity: string;
  context: string | null;
  failure_analysis: string | null;
  prevention_steps: string[] | null;
  pattern_id: string | null;
  pattern_name: string | null;
  created_at: string | null;
}

export interface EffectivenessTrend {
  week: string;
  campaign_count: number;
  success_count: number;
  success_rate: number;
}

export interface IntelligenceSummary {
  total_patterns: number;
  active_patterns: number;
  average_pattern_confidence: number;
  total_lessons: number;
  critical_lessons: number;
  recent_campaigns: number;
  successful_campaigns: number;
  campaign_success_rate: number;
}

// =============================================================================
// Service
// =============================================================================

export const analyticsService = {
  /**
   * Get comprehensive tool operations summary
   * 
   * Includes health status counts, pending approvals, rate limit alerts,
   * unhealthy tools list, and recent violations count.
   */
  async getToolOperationsSummary(): Promise<ToolOperationsSummary> {
    const response = await apiClient.get(`${BASE_PATH}/tool-operations/summary`);
    return response.data;
  },

  /**
   * Get all active alerts requiring attention
   * 
   * Aggregates alerts from multiple sources:
   * - Unhealthy tools
   * - Pending approvals (critical/high urgency)
   * - Rate limits above 90%
   * - Agent errors/budget issues
   */
  async getActiveAlerts(): Promise<Alert[]> {
    const response = await apiClient.get(`${BASE_PATH}/alerts`);
    return response.data;
  },

  /**
   * Get alert counts by severity
   * 
   * Useful for showing badge counts in the header.
   */
  async getAlertCounts(): Promise<AlertCounts> {
    const response = await apiClient.get(`${BASE_PATH}/alerts/count`);
    return response.data;
  },

  /**
   * Get tool execution trends in hourly buckets
   * 
   * @param hours Number of hours to look back (default 24, max 168)
   * @param toolIds Optional array of tool IDs to filter
   */
  async getExecutionTrends(hours: number = 24, toolIds?: string[]): Promise<ExecutionTrend[]> {
    const params: Record<string, string> = { hours: hours.toString() };
    if (toolIds && toolIds.length > 0) {
      params.tool_ids = toolIds.join(',');
    }
    const response = await apiClient.get(`${BASE_PATH}/executions/trends`, { params });
    return response.data;
  },

  /**
   * Get rate limit violation trends by day
   * 
   * @param days Number of days to look back (default 7, max 30)
   * @param toolId Optional tool ID to filter
   */
  async getViolationTrends(days: number = 7, toolId?: string): Promise<ViolationTrend[]> {
    const params: Record<string, string> = { days: days.toString() };
    if (toolId) {
      params.tool_id = toolId;
    }
    const response = await apiClient.get(`${BASE_PATH}/violations/trends`, { params });
    return response.data;
  },

  // ==========================================================================
  // Agent Analytics
  // ==========================================================================

  /**
   * Get performance metrics for agents
   * 
   * Returns efficiency metrics including run counts, success rates,
   * average duration/cost, and top failure reasons.
   * 
   * @param days Number of days to analyze (default 7, max 90)
   * @param agentSlugs Optional array of agent slugs to filter
   */
  async getAgentPerformance(days: number = 7, agentSlugs?: string[]): Promise<AgentPerformance[]> {
    const params: Record<string, string> = { days: days.toString() };
    if (agentSlugs && agentSlugs.length > 0) {
      params.agent_slugs = agentSlugs.join(',');
    }
    const response = await apiClient.get(`${BASE_PATH}/agents/performance`, { params });
    return response.data;
  },

  /**
   * Get daily cost breakdown by agent
   * 
   * Returns costs aggregated by day for trend visualization.
   * Admin only.
   * 
   * @param days Number of days to analyze (default 30, max 90)
   * @param agentSlugs Optional array of agent slugs to filter
   */
  async getAgentCostTrend(days: number = 30, agentSlugs?: string[]): Promise<CostTrendPoint[]> {
    const params: Record<string, string> = { days: days.toString() };
    if (agentSlugs && agentSlugs.length > 0) {
      params.agent_slugs = agentSlugs.join(',');
    }
    const response = await apiClient.get(`${BASE_PATH}/agents/cost-trend`, { params });
    return response.data;
  },

  /**
   * Get AI-generated optimization suggestions for agents
   * 
   * Analyzes patterns to identify:
   * - High failure rates with common causes
   * - Low yield agents
   * - Cost optimization opportunities
   * - Schedule optimization suggestions
   * 
   * Admin only.
   */
  async getAgentSuggestions(): Promise<AgentSuggestion[]> {
    const response = await apiClient.get(`${BASE_PATH}/agents/suggestions`);
    return response.data;
  },

  // ==========================================================================
  // Campaign Intelligence
  // ==========================================================================

  /**
   * Get top performing campaign patterns
   * 
   * Returns patterns sorted by success rate and usage.
   * Includes user's patterns and global patterns.
   */
  async getTopPatterns(limit: number = 10, minConfidence: number = 0.5): Promise<PatternSummary[]> {
    const params = { limit: limit.toString(), min_confidence: minConfidence.toString() };
    const response = await apiClient.get(`${BASE_PATH}/campaigns/patterns`, { params });
    return response.data;
  },

  /**
   * Get recent lessons learned from campaigns
   * 
   * Returns lessons sorted by severity and recency.
   */
  async getRecentLessons(days: number = 30, limit: number = 10, severity?: string): Promise<LessonSummary[]> {
    const params: Record<string, string> = { days: days.toString(), limit: limit.toString() };
    if (severity) {
      params.severity = severity;
    }
    const response = await apiClient.get(`${BASE_PATH}/campaigns/lessons`, { params });
    return response.data;
  },

  /**
   * Get campaign effectiveness trend over time
   * 
   * Shows weekly success rates.
   */
  async getEffectivenessTrend(days: number = 30): Promise<EffectivenessTrend[]> {
    const params = { days: days.toString() };
    const response = await apiClient.get(`${BASE_PATH}/campaigns/effectiveness`, { params });
    return response.data;
  },

  /**
   * Get campaign intelligence summary stats
   * 
   * Provides pattern, lesson, and campaign statistics.
   */
  async getIntelligenceSummary(): Promise<IntelligenceSummary> {
    const response = await apiClient.get(`${BASE_PATH}/campaigns/summary`);
    return response.data;
  },

  /**
   * Create a proposal from a pattern
   * 
   * Creates a draft proposal pre-populated with pattern data.
   */
  async createProposalFromPattern(patternId: string): Promise<unknown> {
    const response = await apiClient.post(`/api/v1/proposals/from-pattern/${patternId}`);
    return response.data;
  },
};

export default analyticsService;
