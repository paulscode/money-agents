/**
 * Notification types for the frontend.
 */

export type NotificationType =
  | 'task_created'
  | 'task_due_soon'
  | 'task_overdue'
  | 'task_completed'
  | 'campaign_started'
  | 'campaign_completed'
  | 'campaign_failed'
  | 'input_required'
  | 'threshold_warning'
  | 'opportunities_discovered'
  | 'high_value_opportunity'
  | 'proposal_submitted'
  | 'proposal_approved'
  | 'proposal_needs_review'
  | 'agent_error'
  | 'system_alert'
  | 'credential_expiring';

export type NotificationPriority = 'low' | 'medium' | 'high' | 'urgent';

export interface Notification {
  id: string;
  user_id: string;
  type: NotificationType;
  priority: NotificationPriority;
  title: string;
  message: string;
  link?: string;
  link_text?: string;
  source_type?: string;
  source_id?: string;
  extra_data?: Record<string, unknown>;
  read_at?: string;
  dismissed_at?: string;
  created_at: string;
  is_read: boolean;
  is_dismissed: boolean;
}

export interface NotificationListResponse {
  notifications: Notification[];
  total_unread: number;
}

export interface NotificationCountsResponse {
  total: number;
  by_priority: {
    low: number;
    medium: number;
    high: number;
    urgent: number;
  };
}

export interface MarkReadResponse {
  success: boolean;
  count: number;
}

export interface DismissResponse {
  success: boolean;
  count: number;
}
