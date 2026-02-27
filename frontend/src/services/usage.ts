/**
 * Usage and Financial Dashboard Service
 * Updated: 2026-02-01
 */
import apiClient from '../lib/api-client';

// Types for usage statistics
export interface TokenUsage {
  model: string;
  message_count: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
}

export interface ToolUsage {
  tool_slug: string;
  tool_name: string;
  execution_count: number;
  success_count: number;
  failure_count: number;
  total_cost_units: number;
  estimated_cost_usd: number;
  avg_duration_ms: number;
}

export interface DailyUsage {
  date: string;
  token_count: number;
  message_count: number;
  tool_executions: number;
  estimated_cost_usd: number;
}

export interface UsageSummary {
  period_start: string;
  period_end: string;
  total_tokens: number;
  total_messages: number;
  total_tool_executions: number;
  total_estimated_cost_usd: number;
  by_model: TokenUsage[];
  by_tool: ToolUsage[];
  daily: DailyUsage[];
}

export interface RecentExecution {
  id: string;
  tool_name: string;
  tool_slug: string;
  status: string;
  duration_ms: number | null;
  cost_units: number | null;
  agent_name: string | null;
  created_at: string;
  error: string | null;
}

export interface CostBreakdown {
  period_days: number;
  total_cost_usd: number;
  llm_costs: {
    total_tokens: number;
    total_cost_usd: number;
    by_model: Array<{
      model: string;
      tokens: number;
      cost_usd: number;
      pricing: {
        input_per_1m: number;
        output_per_1m: number;
      };
    }>;
  };
  tool_costs: {
    total_executions: number;
    total_cost_usd: number;
    by_tool: Array<{
      tool: string;
      slug: string;
      executions: number;
      cost_units: number;
      cost_usd: number;
      pricing: {
        per_unit: number;
      };
    }>;
  };
  pricing_reference: {
    models: Record<string, { input_per_1m: number; output_per_1m: number }>;
    tools: Record<string, number>;
  };
}

// Financial Dashboard Types
export interface CampaignFinancials {
  id: string;
  name: string;
  status: string;
  budget_allocated: number;
  budget_spent: number;
  revenue_generated: number;
  profit_loss: number;
  roi_percent: number | null;
  created_at: string;
  last_activity_at: string | null;
  daily_data: Array<{
    date: string;
    spent: number;
    revenue: number;
    cumulative_spent: number;
    cumulative_revenue: number;
  }>;
}

export interface FinancialDashboard {
  total_spent: number;
  total_earned: number;
  net_profit_loss: number;
  is_profitable: boolean;
  // Bitcoin sats rollups
  total_spent_sats: number;
  total_received_sats: number;
  net_sats: number;
  spending_breakdown: {
    llm_costs: number;
    tool_costs: number;
    agent_costs: number;
    campaign_budgets: number;
  };
  campaigns: CampaignFinancials[];
  daily_totals: Array<{
    date: string;
    spent: number;
    revenue: number;
    profit_loss: number;
    cumulative_spent: number;
    cumulative_revenue: number;
    cumulative_profit_loss: number;
  }>;
  period_start: string;
  period_end: string;
}

const BASE_PATH = '/api/v1/usage';

export const usageService = {
  /**
   * Get comprehensive usage summary
   */
  async getSummary(days: number = 30): Promise<UsageSummary> {
    const response = await apiClient.get(`${BASE_PATH}/summary`, {
      params: { days },
    });
    return response.data;
  },

  /**
   * Get recent tool executions
   */
  async getRecentExecutions(limit: number = 20): Promise<RecentExecution[]> {
    const response = await apiClient.get(`${BASE_PATH}/recent-executions`, {
      params: { limit },
    });
    return response.data;
  },

  /**
   * Get detailed cost breakdown with pricing info
   */
  async getCostBreakdown(days: number = 30): Promise<CostBreakdown> {
    const response = await apiClient.get(`${BASE_PATH}/costs`, {
      params: { days },
    });
    return response.data;
  },

  /**
   * Get comprehensive financial dashboard data
   */
  async getFinancialDashboard(days: number = 30): Promise<FinancialDashboard> {
    const response = await apiClient.get(`${BASE_PATH}/financial-dashboard`, {
      params: { days },
    });
    return response.data;
  },
};
