import apiClient from '../lib/api-client';

const BASE_PATH = '/api/v1/rate-limits';

// Types
export type RateLimitScope = 'global' | 'user' | 'tool' | 'user_tool';
export type RateLimitPeriod = 'minute' | 'hour' | 'day' | 'week' | 'month';

export interface RateLimit {
  id: string;
  scope: RateLimitScope;
  user_id: string | null;
  tool_id: string | null;
  max_executions: number;
  period: RateLimitPeriod;
  max_cost_units: number | null;
  allow_burst: boolean;
  burst_multiplier: number;
  is_active: boolean;
  name: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
  created_by_id: string | null;
}

export interface RateLimitCreate {
  scope: RateLimitScope;
  max_executions: number;
  period: RateLimitPeriod;
  user_id?: string;
  tool_id?: string;
  max_cost_units?: number;
  allow_burst?: boolean;
  burst_multiplier?: number;
  name?: string;
  description?: string;
}

export interface RateLimitUpdate {
  max_executions?: number;
  period?: RateLimitPeriod;
  max_cost_units?: number;
  allow_burst?: boolean;
  burst_multiplier?: number;
  name?: string;
  description?: string;
  is_active?: boolean;
}

export interface RateLimitStatus {
  allowed: boolean;
  current_count: number;
  max_count: number;
  remaining: number;
  period: RateLimitPeriod | null;
  period_start: string | null;
  period_end: string | null;
  retry_after_seconds: number | null;
  limit_id: string | null;
  limit_name: string | null;
}

export interface RateLimitSummary {
  limits: Array<{
    id: string;
    scope: RateLimitScope;
    max_executions: number;
    period: RateLimitPeriod;
    current_count: number;
    remaining: number;
    period_start: string;
    period_end: string;
    name: string | null;
    tool_id: string | null;
    user_id: string | null;
  }>;
  total_remaining: number; // -1 = unlimited
  most_restrictive: {
    id: string;
    scope: RateLimitScope;
    max_executions: number;
    period: RateLimitPeriod;
    current_count: number;
    remaining: number;
  } | null;
}

export interface RateLimitViolation {
  id: string;
  rate_limit_id: string;
  user_id: string | null;
  tool_id: string | null;
  current_count: number;
  limit_count: number;
  period_start: string;
  agent_name: string | null;
  violated_at: string;
}

export const rateLimitsService = {
  // =========================================================================
  // Admin: Rate Limit Management
  // =========================================================================

  /**
   * Create a new rate limit
   */
  async createRateLimit(data: RateLimitCreate): Promise<RateLimit> {
    const response = await apiClient.post(BASE_PATH, data);
    return response.data;
  },

  /**
   * List all rate limits (admin)
   */
  async listRateLimits(params?: {
    scope?: RateLimitScope;
    user_id?: string;
    tool_id?: string;
    is_active?: boolean;
    limit?: number;
    offset?: number;
  }): Promise<RateLimit[]> {
    const response = await apiClient.get(BASE_PATH, { params });
    return response.data;
  },

  /**
   * Get a specific rate limit
   */
  async getRateLimit(id: string): Promise<RateLimit> {
    const response = await apiClient.get(`${BASE_PATH}/${id}`);
    return response.data;
  },

  /**
   * Update a rate limit
   */
  async updateRateLimit(id: string, data: RateLimitUpdate): Promise<RateLimit> {
    const response = await apiClient.patch(`${BASE_PATH}/${id}`, data);
    return response.data;
  },

  /**
   * Delete a rate limit
   */
  async deleteRateLimit(id: string): Promise<void> {
    await apiClient.delete(`${BASE_PATH}/${id}`);
  },

  // =========================================================================
  // User: Rate Limit Status
  // =========================================================================

  /**
   * Check rate limit status for a tool
   */
  async checkRateLimit(toolId: string): Promise<RateLimitStatus> {
    const response = await apiClient.get(`${BASE_PATH}/check/${toolId}`);
    return response.data;
  },

  /**
   * Get rate limit summary for current user
   */
  async getMySummary(toolId?: string): Promise<RateLimitSummary> {
    const params = toolId ? { tool_id: toolId } : undefined;
    const response = await apiClient.get(`${BASE_PATH}/summary/me`, { params });
    return response.data;
  },

  /**
   * Get rate limit summary for a specific tool
   */
  async getToolSummary(toolId: string): Promise<RateLimitSummary> {
    const response = await apiClient.get(`${BASE_PATH}/summary/tool/${toolId}`);
    return response.data;
  },

  // =========================================================================
  // Admin: Violation Tracking
  // =========================================================================

  /**
   * List rate limit violations (admin)
   */
  async listViolations(params?: {
    rate_limit_id?: string;
    user_id?: string;
    tool_id?: string;
    since_hours?: number;
    limit?: number;
    offset?: number;
  }): Promise<RateLimitViolation[]> {
    const response = await apiClient.get(`${BASE_PATH}/violations`, { params });
    return response.data;
  },

  /**
   * Get violation count
   */
  async getViolationCount(params?: {
    user_id?: string;
    tool_id?: string;
    since_hours?: number;
  }): Promise<{ count: number; since_hours: number }> {
    const response = await apiClient.get(`${BASE_PATH}/violations/count`, { params });
    return response.data;
  },
};

export default rateLimitsService;
