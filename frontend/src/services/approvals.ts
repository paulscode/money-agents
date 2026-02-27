/**
 * Tool Approval Service - API client for human-in-loop approval workflow
 */
import api from './api';

// Types
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';
export type ApprovalUrgency = 'low' | 'medium' | 'high' | 'critical';

export interface ApprovalRequest {
  id: string;
  tool_id: string;
  tool_name: string | null;
  parameters: Record<string, unknown>;
  requested_by_id: string;
  requested_by_name: string | null;
  campaign_id: string | null;
  status: ApprovalStatus;
  urgency: ApprovalUrgency;
  reason: string;
  expected_outcome: string | null;
  risk_assessment: string | null;
  estimated_cost: number | null;
  reviewed_by_id: string | null;
  reviewed_by_name: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  execution_id: string | null;
  created_at: string;
  expires_at: string | null;
}

export interface ApprovalListResponse {
  items: ApprovalRequest[];
  total: number;
  limit: number;
  offset: number;
}

export interface PendingCountResponse {
  critical: number;
  high: number;
  medium: number;
  low: number;
  total: number;
}

export interface CreateApprovalRequest {
  tool_id: string;
  parameters?: Record<string, unknown>;
  reason: string;
  campaign_id?: string;
  urgency?: ApprovalUrgency;
  expected_outcome?: string;
  risk_assessment?: string;
  estimated_cost?: number;
  expires_in_hours?: number;
}

export interface ExecutionResponse {
  execution_id: string;
  status: string;
  output: unknown;
  error: string | null;
}

// API Functions

/**
 * Create a new approval request
 */
export async function createApprovalRequest(data: CreateApprovalRequest): Promise<ApprovalRequest> {
  const response = await api.post('/approvals', data);
  return response.data;
}

/**
 * List pending approvals (admin only)
 */
export async function listPendingApprovals(params?: {
  urgency?: ApprovalUrgency;
  campaign_id?: string;
  limit?: number;
  offset?: number;
}): Promise<ApprovalListResponse> {
  const response = await api.get('/approvals', { params });
  return response.data;
}

/**
 * Get pending approval counts by urgency (admin only)
 */
export async function getPendingCount(): Promise<PendingCountResponse> {
  const response = await api.get('/approvals/pending/count');
  return response.data;
}

/**
 * List current user's approval requests
 */
export async function listMyRequests(params?: {
  status?: ApprovalStatus;
  limit?: number;
  offset?: number;
}): Promise<ApprovalListResponse> {
  const response = await api.get('/approvals/my-requests', { params });
  return response.data;
}

/**
 * Get a specific approval request
 */
export async function getApprovalRequest(requestId: string): Promise<ApprovalRequest> {
  const response = await api.get(`/approvals/${requestId}`);
  return response.data;
}

/**
 * Approve an approval request (admin only)
 */
export async function approveRequest(
  requestId: string,
  notes?: string
): Promise<ApprovalRequest> {
  const response = await api.post(`/approvals/${requestId}/approve`, { notes });
  return response.data;
}

/**
 * Reject an approval request (admin only)
 */
export async function rejectRequest(
  requestId: string,
  notes?: string
): Promise<ApprovalRequest> {
  const response = await api.post(`/approvals/${requestId}/reject`, { notes });
  return response.data;
}

/**
 * Cancel a pending approval request (requester only)
 */
export async function cancelRequest(requestId: string): Promise<ApprovalRequest> {
  const response = await api.post(`/approvals/${requestId}/cancel`);
  return response.data;
}

/**
 * Execute a tool after approval has been granted
 */
export async function executeApprovedRequest(
  requestId: string,
  data?: { conversation_id?: string; message_id?: string }
): Promise<ExecutionResponse> {
  const response = await api.post(`/approvals/${requestId}/execute`, data || {});
  return response.data;
}

// Utility functions

/**
 * Get urgency badge color
 */
export function getUrgencyColor(urgency: ApprovalUrgency): string {
  switch (urgency) {
    case 'critical':
      return 'bg-red-500';
    case 'high':
      return 'bg-orange-500';
    case 'medium':
      return 'bg-yellow-500';
    case 'low':
      return 'bg-blue-500';
    default:
      return 'bg-gray-500';
  }
}

/**
 * Get status badge color
 */
export function getStatusColor(status: ApprovalStatus): string {
  switch (status) {
    case 'pending':
      return 'bg-yellow-500';
    case 'approved':
      return 'bg-green-500';
    case 'rejected':
      return 'bg-red-500';
    case 'expired':
      return 'bg-gray-500';
    case 'cancelled':
      return 'bg-gray-400';
    default:
      return 'bg-gray-500';
  }
}

/**
 * Format relative time
 */
export function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${diffDays}d ago`;
}

/**
 * Check if request is expiring soon (within 1 hour)
 */
export function isExpiringSoon(expiresAt: string | null): boolean {
  if (!expiresAt) return false;
  const expires = new Date(expiresAt);
  const now = new Date();
  const diffMs = expires.getTime() - now.getTime();
  return diffMs > 0 && diffMs < 3600000; // Less than 1 hour
}
