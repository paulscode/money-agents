/**
 * Tasks API service
 */
import apiClient from '@/lib/api-client';
import type { 
  Task, 
  TaskCreate, 
  TaskUpdate, 
  TaskCounts,
  TaskListResponse,
  TaskSummary,
  TaskStatus,
  TaskType,
  TaskSortBy,
} from '../types';

const BASE_URL = '/api/v1/tasks';

/**
 * List tasks with filtering and pagination.
 */
export async function listTasks(params?: {
  statuses?: TaskStatus[];
  task_types?: TaskType[];
  include_completed?: boolean;
  sort_by?: TaskSortBy;
  limit?: number;
  offset?: number;
}): Promise<TaskListResponse> {
  const response = await apiClient.get<TaskListResponse>(BASE_URL, { params });
  return response.data;
}

/**
 * Create a new task.
 */
export async function createTask(data: TaskCreate): Promise<Task> {
  const response = await apiClient.post<Task>(BASE_URL, data);
  return response.data;
}

/**
 * Get task summary for dashboard.
 */
export async function getTaskSummary(): Promise<TaskSummary> {
  const response = await apiClient.get<TaskSummary>(`${BASE_URL}/summary`);
  return response.data;
}

/**
 * Get task counts by status.
 */
export async function getTaskCounts(): Promise<TaskCounts> {
  const response = await apiClient.get<TaskCounts>(`${BASE_URL}/counts`);
  return response.data;
}

/**
 * Get task analytics for dashboard charts.
 */
export interface TaskAnalytics {
  period_days: number;
  completed_count: number;
  value_captured: number;
  active_value: number;
  avg_completion_hours: number | null;
  on_time_rate: number | null;
  by_type: Record<string, number>;
  completion_trend: Array<{ date: string; completed: number }>;
}

export async function getTaskAnalytics(days?: number): Promise<TaskAnalytics> {
  const params = days ? { days } : {};
  const response = await apiClient.get<TaskAnalytics>(`${BASE_URL}/analytics`, { params });
  return response.data;
}

/**
 * Get combined dashboard data (summary + analytics).
 */
export interface DashboardTasks {
  summary: TaskSummary;
  analytics: TaskAnalytics;
}

export async function getDashboardTasks(days?: number): Promise<DashboardTasks> {
  const params = days ? { days } : {};
  const response = await apiClient.get<DashboardTasks>(`${BASE_URL}/dashboard`, { params });
  return response.data;
}

/**
 * Get actionable tasks (ready to work on).
 */
export async function getActionableTasks(limit?: number): Promise<Task[]> {
  const params = limit ? { limit } : {};
  const response = await apiClient.get<Task[]>(`${BASE_URL}/actionable`, { params });
  return response.data;
}

/**
 * Get overdue tasks.
 */
export async function getOverdueTasks(limit?: number): Promise<Task[]> {
  const params = limit ? { limit } : {};
  const response = await apiClient.get<Task[]>(`${BASE_URL}/overdue`, { params });
  return response.data;
}

/**
 * Get tasks due soon.
 */
export async function getDueSoonTasks(hours?: number, limit?: number): Promise<Task[]> {
  const params: Record<string, number> = {};
  if (hours) params.hours = hours;
  if (limit) params.limit = limit;
  const response = await apiClient.get<Task[]>(`${BASE_URL}/due-soon`, { params });
  return response.data;
}

/**
 * Get tasks by source (campaign, opportunity, idea).
 */
export async function getTasksBySource(
  sourceType: string, 
  sourceId?: string,
  includeCompleted?: boolean
): Promise<Task[]> {
  const params: Record<string, any> = {};
  if (sourceId) params.source_id = sourceId;
  if (includeCompleted !== undefined) params.include_completed = includeCompleted;
  const response = await apiClient.get<Task[]>(`${BASE_URL}/by-source/${sourceType}`, { params });
  return response.data;
}

/**
 * Get a single task.
 */
export async function getTask(taskId: string): Promise<Task> {
  const response = await apiClient.get<Task>(`${BASE_URL}/${taskId}`);
  return response.data;
}

/**
 * Update a task.
 */
export async function updateTask(taskId: string, data: TaskUpdate): Promise<Task> {
  const response = await apiClient.patch<Task>(`${BASE_URL}/${taskId}`, data);
  return response.data;
}

/**
 * Delete a task.
 */
export async function deleteTask(taskId: string): Promise<void> {
  await apiClient.delete(`${BASE_URL}/${taskId}`);
}

/**
 * Complete a task.
 */
export async function completeTask(
  taskId: string, 
  data?: { completion_notes?: string; actual_value?: number }
): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/complete`, data || {});
  return response.data;
}

/**
 * Defer a task.
 */
export async function deferTask(taskId: string, deferUntil: string): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/defer`, { defer_until: deferUntil });
  return response.data;
}

/**
 * Block a task.
 */
export async function blockTask(
  taskId: string, 
  blockedBy: string, 
  blockedByTaskId?: string
): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/block`, {
    blocked_by: blockedBy,
    blocked_by_task_id: blockedByTaskId,
  });
  return response.data;
}

/**
 * Unblock a task.
 */
export async function unblockTask(taskId: string): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/unblock`);
  return response.data;
}

/**
 * Cancel a task.
 */
export async function cancelTask(taskId: string): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/cancel`);
  return response.data;
}

/**
 * Start working on a task.
 */
export async function startTask(taskId: string): Promise<Task> {
  const response = await apiClient.post<Task>(`${BASE_URL}/${taskId}/start`);
  return response.data;
}

/**
 * Recalculate all task priorities.
 */
export async function recalculatePriorities(): Promise<{ updated: number }> {
  const response = await apiClient.post<{ updated: number }>(`${BASE_URL}/recalculate-priorities`);
  return response.data;
}

/**
 * Sync auto-generated tasks from system events.
 * Creates tasks for pending campaign inputs, opportunity reviews, etc.
 */
export async function syncTasks(): Promise<{
  campaign_tasks_created: number;
  opportunity_review_task: string;
  deferred_tasks_activated: number;
}> {
  const response = await apiClient.post<{
    campaign_tasks_created: number;
    opportunity_review_task: string;
    deferred_tasks_activated: number;
  }>(`${BASE_URL}/sync`);
  return response.data;
}

export const tasksService = {
  list: listTasks,
  create: createTask,
  get: getTask,
  update: updateTask,
  delete: deleteTask,
  getSummary: getTaskSummary,
  getCounts: getTaskCounts,
  getAnalytics: getTaskAnalytics,
  getDashboard: getDashboardTasks,
  getActionable: getActionableTasks,
  getOverdue: getOverdueTasks,
  getDueSoon: getDueSoonTasks,
  getBySource: getTasksBySource,
  complete: completeTask,
  defer: deferTask,
  block: blockTask,
  unblock: unblockTask,
  cancel: cancelTask,
  start: startTask,
  recalculatePriorities,
  sync: syncTasks,
};
