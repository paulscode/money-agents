import apiClient from '../lib/api-client';
import type {
  ToolHealthStatus,
  ToolHealthCheck,
  ToolHealthSummary,
  ToolHealthSettings,
  ToolHealthCheckResult,
  HealthStatus,
} from '../types';

const BASE_PATH = '/api/v1/tool-health';

export interface ToolHealthHistory {
  tool_id: string;
  tool_name: string;
  history: ToolHealthCheck[];
}

export interface UnhealthyTool {
  id: string;
  name: string;
  status: HealthStatus;
  message: string | null;
  last_health_check: string | null;
  health_response_ms: number | null;
}

export const toolHealthService = {
  /**
   * Get the current health status of a tool
   */
  async getToolHealth(toolId: string): Promise<ToolHealthStatus> {
    const response = await apiClient.get(`${BASE_PATH}/tools/${toolId}/health`);
    return response.data;
  },

  /**
   * Run a health check on a tool
   */
  async checkToolHealth(
    toolId: string,
    checkType: 'connectivity' | 'validation' | 'full' = 'connectivity'
  ): Promise<ToolHealthCheckResult> {
    const response = await apiClient.post(`${BASE_PATH}/tools/${toolId}/health/check`, null, {
      params: { check_type: checkType },
    });
    return response.data;
  },

  /**
   * Get health check history for a tool
   */
  async getToolHealthHistory(
    toolId: string,
    limit: number = 50,
    checkType?: string
  ): Promise<ToolHealthHistory> {
    const params: Record<string, any> = { limit };
    if (checkType) {
      params.check_type = checkType;
    }
    const response = await apiClient.get(`${BASE_PATH}/tools/${toolId}/health/history`, {
      params,
    });
    return response.data;
  },

  /**
   * Get health summary for all tools
   */
  async getHealthSummary(): Promise<ToolHealthSummary> {
    const response = await apiClient.get(`${BASE_PATH}/tools/health/summary`);
    return response.data;
  },

  /**
   * Get list of unhealthy/degraded tools
   */
  async getUnhealthyTools(): Promise<UnhealthyTool[]> {
    const response = await apiClient.get(`${BASE_PATH}/tools/health/unhealthy`);
    return response.data;
  },

  /**
   * Update health check settings for a tool
   */
  async updateHealthSettings(
    toolId: string,
    settings: ToolHealthSettings
  ): Promise<{ message: string; tool_id: string } & ToolHealthSettings> {
    const response = await apiClient.put(`${BASE_PATH}/tools/${toolId}/health/settings`, settings);
    return response.data;
  },

  /**
   * Run health checks on all tools (admin only)
   */
  async checkAllTools(): Promise<{
    message: string;
    total_checked: number;
    results: ToolHealthCheckResult[];
  }> {
    const response = await apiClient.post(`${BASE_PATH}/tools/health/check-all`);
    return response.data;
  },
};

export default toolHealthService;
