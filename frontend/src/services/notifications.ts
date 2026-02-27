/**
 * Notification API service.
 */
import apiClient from '@/lib/api-client';
import type {
  Notification,
  NotificationListResponse,
  NotificationCountsResponse,
  NotificationType,
  MarkReadResponse,
  DismissResponse,
} from '@/types/notification';

const BASE_PATH = '/api/v1/notifications';

/**
 * Get notifications for the current user.
 */
export async function getNotifications(params?: {
  unread_only?: boolean;
  include_dismissed?: boolean;
  types?: NotificationType[];
  limit?: number;
  offset?: number;
}): Promise<NotificationListResponse> {
  const response = await apiClient.get<NotificationListResponse>(BASE_PATH, { params });
  return response.data;
}

/**
 * Get notification counts (for badge).
 */
export async function getNotificationCounts(): Promise<NotificationCountsResponse> {
  const response = await apiClient.get<NotificationCountsResponse>(`${BASE_PATH}/counts`);
  return response.data;
}

/**
 * Get a single notification by ID.
 */
export async function getNotification(id: string): Promise<Notification> {
  const response = await apiClient.get<Notification>(`${BASE_PATH}/${id}`);
  return response.data;
}

/**
 * Mark a notification as read.
 */
export async function markNotificationRead(id: string): Promise<MarkReadResponse> {
  const response = await apiClient.post<MarkReadResponse>(`${BASE_PATH}/${id}/read`);
  return response.data;
}

/**
 * Mark all notifications as read.
 */
export async function markAllNotificationsRead(): Promise<MarkReadResponse> {
  const response = await apiClient.post<MarkReadResponse>(`${BASE_PATH}/read-all`);
  return response.data;
}

/**
 * Dismiss a notification.
 */
export async function dismissNotification(id: string): Promise<DismissResponse> {
  const response = await apiClient.post<DismissResponse>(`${BASE_PATH}/${id}/dismiss`);
  return response.data;
}

/**
 * Dismiss old notifications.
 */
export async function dismissOldNotifications(days: number = 30): Promise<DismissResponse> {
  const response = await apiClient.post<DismissResponse>(`${BASE_PATH}/dismiss-old`, null, {
    params: { days },
  });
  return response.data;
}
