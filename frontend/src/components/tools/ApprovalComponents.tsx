/**
 * Tool Approval Components - UI for human-in-loop approval workflow
 */
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ApprovalRequest,
  ApprovalUrgency,
  PendingCountResponse,
  listPendingApprovals,
  getPendingCount,
  approveRequest,
  rejectRequest,
  getUrgencyColor,
  getStatusColor,
  formatRelativeTime,
  isExpiringSoon,
} from '../../services/approvals';

// =============================================================================
// Pending Count Badge - Shows count of pending approvals in header/sidebar
// =============================================================================

export function PendingApprovalsBadge() {
  const { data: counts } = useQuery({
    queryKey: ['approval-counts'],
    queryFn: getPendingCount,
    refetchInterval: 30000, // Refresh every 30s
  });

  if (!counts || counts.total === 0) return null;

  return (
    <span className="inline-flex items-center justify-center px-2 py-1 text-xs font-bold leading-none text-white bg-red-600 rounded-full">
      {counts.total > 99 ? '99+' : counts.total}
    </span>
  );
}

// =============================================================================
// Pending Counts Summary - Shows breakdown by urgency
// =============================================================================

interface PendingCountsSummaryProps {
  counts: PendingCountResponse | undefined;
}

export function PendingCountsSummary({ counts }: PendingCountsSummaryProps) {
  if (!counts) return null;

  return (
    <div className="flex gap-3 text-sm">
      {counts.critical > 0 && (
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-red-500" />
          <span className="text-red-400">{counts.critical} critical</span>
        </span>
      )}
      {counts.high > 0 && (
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-orange-500" />
          <span className="text-orange-400">{counts.high} high</span>
        </span>
      )}
      {counts.medium > 0 && (
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-yellow-500" />
          <span className="text-yellow-400">{counts.medium} medium</span>
        </span>
      )}
      {counts.low > 0 && (
        <span className="flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-blue-500" />
          <span className="text-blue-400">{counts.low} low</span>
        </span>
      )}
    </div>
  );
}

// =============================================================================
// Approval Card - Single approval request display
// =============================================================================

interface ApprovalCardProps {
  request: ApprovalRequest;
  onApprove?: (id: string, notes?: string) => void;
  onReject?: (id: string, notes?: string) => void;
  isReviewing?: boolean;
}

export function ApprovalCard({ request, onApprove, onReject, isReviewing }: ApprovalCardProps) {
  const [notes, setNotes] = useState('');
  const [showDetails, setShowDetails] = useState(false);
  const expiringSoon = isExpiringSoon(request.expires_at);

  return (
    <div className={`bg-slate-800 rounded-lg p-4 border ${expiringSoon ? 'border-orange-500' : 'border-slate-700'}`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-1">
            <span className={`px-2 py-0.5 text-xs font-medium rounded ${getUrgencyColor(request.urgency)} text-white`}>
              {request.urgency.toUpperCase()}
            </span>
            <span className={`px-2 py-0.5 text-xs font-medium rounded ${getStatusColor(request.status)} text-white`}>
              {request.status}
            </span>
            {expiringSoon && (
              <span className="px-2 py-0.5 text-xs font-medium rounded bg-orange-600 text-white animate-pulse">
                Expiring soon
              </span>
            )}
          </div>
          <h3 className="text-lg font-semibold text-white">
            {request.tool_name || 'Unknown Tool'}
          </h3>
          <p className="text-sm text-slate-400">
            Requested by {request.requested_by_name || 'Unknown'} • {formatRelativeTime(request.created_at)}
          </p>
        </div>
      </div>

      {/* Reason */}
      <div className="mb-3">
        <p className="text-sm text-slate-300">{request.reason}</p>
      </div>

      {/* Details Toggle */}
      <button
        onClick={() => setShowDetails(!showDetails)}
        className="text-sm text-blue-400 hover:text-blue-300 mb-3"
      >
        {showDetails ? '▼ Hide details' : '▶ Show details'}
      </button>

      {/* Expanded Details */}
      {showDetails && (
        <div className="bg-slate-900 rounded p-3 mb-3 space-y-2 text-sm">
          {request.expected_outcome && (
            <div>
              <span className="text-slate-500">Expected outcome:</span>
              <p className="text-slate-300">{request.expected_outcome}</p>
            </div>
          )}
          {request.risk_assessment && (
            <div>
              <span className="text-slate-500">Risk assessment:</span>
              <p className="text-slate-300">{request.risk_assessment}</p>
            </div>
          )}
          {request.estimated_cost !== null && (
            <div>
              <span className="text-slate-500">Estimated cost:</span>
              <span className="text-slate-300 ml-2">${request.estimated_cost.toFixed(2)}</span>
            </div>
          )}
          <div>
            <span className="text-slate-500">Parameters:</span>
            <pre className="text-slate-300 text-xs mt-1 overflow-x-auto">
              {JSON.stringify(request.parameters, null, 2)}
            </pre>
          </div>
          {request.expires_at && (
            <div>
              <span className="text-slate-500">Expires:</span>
              <span className="text-slate-300 ml-2">
                {new Date(request.expires_at).toLocaleString()}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Actions (only for pending) */}
      {request.status === 'pending' && onApprove && onReject && (
        <div className="space-y-3">
          <textarea
            placeholder="Review notes (optional)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded px-3 py-2 text-sm text-white placeholder-slate-500 resize-none"
            rows={2}
          />
          <div className="flex gap-2">
            <button
              onClick={() => onApprove(request.id, notes || undefined)}
              disabled={isReviewing}
              className="flex-1 bg-green-600 hover:bg-green-700 disabled:bg-green-800 text-white px-4 py-2 rounded font-medium transition-colors"
            >
              {isReviewing ? 'Processing...' : '✓ Approve'}
            </button>
            <button
              onClick={() => onReject(request.id, notes || undefined)}
              disabled={isReviewing}
              className="flex-1 bg-red-600 hover:bg-red-700 disabled:bg-red-800 text-white px-4 py-2 rounded font-medium transition-colors"
            >
              {isReviewing ? 'Processing...' : '✗ Reject'}
            </button>
          </div>
        </div>
      )}

      {/* Review result (for reviewed requests) */}
      {request.status !== 'pending' && request.reviewed_by_name && (
        <div className="bg-slate-900 rounded p-3 text-sm">
          <p className="text-slate-400">
            {request.status === 'approved' ? 'Approved' : 'Rejected'} by {request.reviewed_by_name}
            {request.reviewed_at && ` • ${formatRelativeTime(request.reviewed_at)}`}
          </p>
          {request.review_notes && (
            <p className="text-slate-300 mt-1">{request.review_notes}</p>
          )}
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Approval Queue - List of pending approvals for admin
// =============================================================================

interface ApprovalQueueProps {
  urgencyFilter?: ApprovalUrgency;
  campaignId?: string;
}

export function ApprovalQueue({ urgencyFilter, campaignId }: ApprovalQueueProps) {
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ['pending-approvals', urgencyFilter, campaignId],
    queryFn: () => listPendingApprovals({ urgency: urgencyFilter, campaign_id: campaignId }),
    refetchInterval: 10000, // Refresh every 10s
  });

  const { data: counts } = useQuery({
    queryKey: ['approval-counts'],
    queryFn: getPendingCount,
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, notes }: { id: string; notes?: string }) => approveRequest(id, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-approvals'] });
      queryClient.invalidateQueries({ queryKey: ['approval-counts'] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, notes }: { id: string; notes?: string }) => rejectRequest(id, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['pending-approvals'] });
      queryClient.invalidateQueries({ queryKey: ['approval-counts'] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-blue-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-900/50 border border-red-700 rounded-lg p-4 text-red-300">
        Failed to load approval requests: {String(error)}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-white">Pending Approvals</h2>
        <PendingCountsSummary counts={counts} />
      </div>

      {/* Empty state */}
      {(!data?.items || data.items.length === 0) && (
        <div className="bg-slate-800 rounded-lg p-8 text-center">
          <p className="text-slate-400">No pending approval requests</p>
        </div>
      )}

      {/* Request list */}
      <div className="space-y-4">
        {data?.items.map((request) => (
          <ApprovalCard
            key={request.id}
            request={request}
            onApprove={(id, notes) => approveMutation.mutate({ id, notes })}
            onReject={(id, notes) => rejectMutation.mutate({ id, notes })}
            isReviewing={approveMutation.isPending || rejectMutation.isPending}
          />
        ))}
      </div>

      {/* Pagination info */}
      {data && data.total > data.items.length && (
        <p className="text-sm text-slate-500 text-center">
          Showing {data.items.length} of {data.total} requests
        </p>
      )}
    </div>
  );
}

// =============================================================================
// Tool Approval Indicator - Shows if a tool requires approval
// =============================================================================

interface ToolApprovalIndicatorProps {
  requiresApproval: boolean;
  urgency?: string;
}

export function ToolApprovalIndicator({ requiresApproval, urgency }: ToolApprovalIndicatorProps) {
  if (!requiresApproval) return null;

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="px-2 py-1 rounded bg-amber-600/20 text-amber-400 border border-amber-600/50">
        🔐 Requires Approval
      </span>
      {urgency && (
        <span className={`px-2 py-1 rounded text-white text-xs ${getUrgencyColor(urgency as ApprovalUrgency)}`}>
          {urgency.toUpperCase()} priority
        </span>
      )}
    </div>
  );
}

// =============================================================================
// Compact Approval Status - For tool detail pages
// =============================================================================

interface CompactApprovalStatusProps {
  request: ApprovalRequest;
}

export function CompactApprovalStatus({ request }: CompactApprovalStatusProps) {
  return (
    <div className="inline-flex items-center gap-2 px-3 py-1.5 bg-slate-800 rounded-lg border border-slate-700">
      <span className={`w-2 h-2 rounded-full ${getStatusColor(request.status)}`} />
      <span className="text-sm text-slate-300">
        {request.status === 'pending' && 'Awaiting approval'}
        {request.status === 'approved' && 'Approved - Ready to execute'}
        {request.status === 'rejected' && 'Rejected'}
        {request.status === 'expired' && 'Expired'}
        {request.status === 'cancelled' && 'Cancelled'}
      </span>
    </div>
  );
}
