import apiClient from '../lib/api-client';
import type {
  Tool,
  ToolCreate,
  ToolUpdate,
  ToolStatus,
  ToolCategory,
  AssignToolRequest,
  UpdateToolStatusRequest,
  ToolExecution,
  ToolExecuteRequest,
} from '../types';

const BASE_PATH = '/api/v1/tools/';

export const toolsService = {
  /**
   * List all tools with optional filters
   */
  async listTools(params?: {
    status?: ToolStatus;
    category?: ToolCategory;
    search?: string;
    assigned_to_me?: boolean;
    requested_by_me?: boolean;
    skip?: number;
    limit?: number;
  }): Promise<Tool[]> {
    const response = await apiClient.get(BASE_PATH, { params });
    return response.data;
  },

  /**
   * List only implemented/available tools
   */
  async listAvailableTools(category?: ToolCategory): Promise<Tool[]> {
    const params = category ? { category } : undefined;
    const response = await apiClient.get(`${BASE_PATH}available`, { params });
    return response.data;
  },

  /**
   * Get a specific tool by ID
   */
  async getTool(id: string): Promise<Tool> {
    const response = await apiClient.get(`${BASE_PATH}${id}`);
    return response.data;
  },

  /**
   * Create a new tool request
   */
  async createTool(data: ToolCreate): Promise<Tool> {
    const response = await apiClient.post(BASE_PATH, data);
    return response.data;
  },

  /**
   * Update a tool
   */
  async updateTool(id: string, data: ToolUpdate): Promise<Tool> {
    const response = await apiClient.put(`${BASE_PATH}${id}`, data);
    return response.data;
  },

  /**
   * Approve a tool (admin only)
   */
  async approveTool(id: string, assignToUserId?: string): Promise<Tool> {
    const data = assignToUserId ? { user_id: assignToUserId } : undefined;
    const response = await apiClient.post(`${BASE_PATH}${id}/approve`, data);
    return response.data;
  },

  /**
   * Reject a tool (admin only)
   */
  async rejectTool(id: string, notes?: string): Promise<Tool> {
    const params = notes ? { notes } : undefined;
    const response = await apiClient.post(`${BASE_PATH}${id}/reject`, null, { params });
    return response.data;
  },

  /**
   * Assign or reassign a tool
   */
  async assignTool(id: string, userId: string): Promise<Tool> {
    const data: AssignToolRequest = { user_id: userId };
    const response = await apiClient.put(`${BASE_PATH}${id}/assign`, data);
    return response.data;
  },

  /**
   * Update tool status
   */
  async updateToolStatus(
    id: string,
    status: ToolStatus,
    notes?: string
  ): Promise<Tool> {
    const data: UpdateToolStatusRequest = { status, notes };
    const response = await apiClient.put(`${BASE_PATH}${id}/status`, data);
    return response.data;
  },

  /**
   * Delete a tool
   */
  async deleteTool(id: string): Promise<void> {
    await apiClient.delete(`${BASE_PATH}${id}`);
  },

  /**
   * Execute a tool with given parameters
   */
  async executeTool(id: string, request: ToolExecuteRequest): Promise<ToolExecution> {
    const response = await apiClient.post(`${BASE_PATH}${id}/execute`, request);
    return response.data;
  },

  /**
   * List recent executions of a tool
   */
  async listExecutions(id: string, limit: number = 20): Promise<ToolExecution[]> {
    const response = await apiClient.get(`${BASE_PATH}${id}/executions`, { params: { limit } });
    return response.data;
  },
};
