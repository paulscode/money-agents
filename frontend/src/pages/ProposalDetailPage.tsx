import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { proposalsService } from '@/services/proposals';
import { conversationsService } from '@/services/conversations';
import { ConversationPanel } from '@/components/conversations/ConversationPanel';
import { 
  Loader2, 
  ArrowLeft, 
  DollarSign, 
  TrendingUp, 
  AlertTriangle, 
  Calendar,
  CheckCircle,
  XCircle,
  Clock,
  Edit,
  Trash2,
  FileText,
  MessageSquare,
  ChevronLeft,
  Rocket,
  Bitcoin
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';

const statusColors: Record<string, string> = {
  pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  under_review: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  approved: 'bg-green-500/20 text-green-400 border-green-500/30',
  rejected: 'bg-red-500/20 text-red-400 border-red-500/30',
  deferred: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  changes_requested: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
};

const riskColors: Record<string, string> = {
  low: 'text-green-400 bg-green-500/10',
  medium: 'text-yellow-400 bg-yellow-500/10',
  high: 'text-red-400 bg-red-500/10',
};

export function ProposalDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [activeTab, setActiveTab] = useState<'details' | 'discussion'>('details');
  const isDeletingRef = useRef(false);

  const { data: proposal, isLoading } = useQuery({
    queryKey: ['proposals', id],
    queryFn: () => proposalsService.getById(id!),
    enabled: !!id && !isDeletingRef.current,
  });

  // Fetch unread count for this proposal
  const { data: unreadCount = 0 } = useQuery({
    queryKey: ['unread-count', id],
    queryFn: () => conversationsService.getProposalUnreadCount(id!),
    enabled: !!id,
    refetchInterval: 10000, // Poll every 10 seconds
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      // Disable the query immediately when delete starts
      isDeletingRef.current = true;
      return proposalsService.delete(id!);
    },
    onSuccess: () => {
      // Cancel any outgoing refetches for this proposal
      queryClient.cancelQueries({ queryKey: ['proposals', id] });
      // Remove from cache
      queryClient.removeQueries({ queryKey: ['proposals', id] });
      // Update the list cache by filtering out the deleted proposal
      queryClient.setQueryData(['proposals'], (old: any) => 
        old ? old.filter((p: any) => p.id !== id) : []
      );
      // Navigate away
      navigate('/proposals');
    },
  });

  const updateStatusMutation = useMutation({
    mutationFn: (status: string) => proposalsService.update(id!, { status: status as any }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['proposals', id] });
      queryClient.invalidateQueries({ queryKey: ['proposals'] });
    },
  });

  // Invalidate unread count when switching to discussion tab
  // This ensures the count updates immediately when messages are marked as read
  useEffect(() => {
    if (activeTab === 'discussion') {
      // Small delay to allow the ConversationPanel to mark messages as read first
      const timer = setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['unread-count', id] });
        queryClient.invalidateQueries({ queryKey: ['unread-counts'] });
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [activeTab, id, queryClient]);

  if (isLoading) {
    return (
      <Layout>
        <div className="flex justify-center items-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
        </div>
      </Layout>
    );
  }

  if (!proposal) {
    return (
      <Layout>
        <div className="text-center py-12">
          <p className="text-gray-400">Proposal not found</p>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => navigate('/proposals')}
            className="flex items-center gap-2 text-gray-400 hover:text-neon-cyan transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
            Back to Proposals
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate(`/proposals/${id}/edit`)}
              className="btn-secondary flex items-center"
            >
              <Edit className="h-4 w-4 mr-2" />
              Edit
            </button>
            <button
              onClick={() => setShowDeleteModal(true)}
              className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center gap-2"
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </button>
          </div>
        </div>

        {/* Title and Status */}
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-4">
            <h1 className="text-4xl font-bold text-white">{proposal.title}</h1>
            <span
              className={`px-4 py-2 rounded-lg text-sm font-medium border ${
                statusColors[proposal.status] || statusColors.pending
              }`}
            >
              {proposal.status.replace('_', ' ').toUpperCase()}
            </span>
          </div>
          
          <p className="text-xl text-gray-300">{proposal.summary}</p>
          
          {/* Campaign Link (if proposal has been converted to a campaign) */}
          {proposal.campaign_id && (
            <Link 
              to={`/campaigns/${proposal.campaign_id}`}
              className="inline-flex items-center gap-2 text-neon-cyan hover:underline"
            >
              <ChevronLeft className="h-4 w-4" />
              <span className="text-gray-400">Campaign:</span>
              <Rocket className="h-4 w-4" />
              #{proposal.campaign_id.slice(0, 8)}
            </Link>
          )}
          
          <div className="flex items-center gap-6 text-sm text-gray-400">
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4" />
              <span>Submitted {new Date(proposal.submitted_at).toLocaleDateString()}</span>
            </div>
            {proposal.source && (
              <div className="flex items-center gap-2">
                <span>Source: {proposal.source}</span>
              </div>
            )}
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="border-b border-gray-800">
          <div className="flex gap-1">
            <button
              onClick={() => setActiveTab('details')}
              className={`px-6 py-3 font-medium transition-all relative ${
                activeTab === 'details'
                  ? 'text-neon-cyan'
                  : 'text-gray-400 hover:text-gray-300'
              }`}
            >
              <div className="flex items-center gap-2">
                <FileText className="h-4 w-4" />
                Details
              </div>
              {activeTab === 'details' && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-neon-cyan to-neon-blue" />
              )}
            </button>
            <button
              onClick={() => setActiveTab('discussion')}
              className={`px-6 py-3 font-medium transition-all relative ${
                activeTab === 'discussion'
                  ? 'text-neon-cyan'
                  : 'text-gray-400 hover:text-gray-300'
              }`}
            >
              <div className="flex items-center gap-2">
                <MessageSquare className="h-4 w-4" />
                Discussion
                {unreadCount > 0 && activeTab !== 'discussion' && (
                  <span className="ml-1 px-2 py-0.5 text-xs font-bold bg-neon-cyan text-gray-900 rounded-full animate-pulse">
                    {unreadCount}
                  </span>
                )}
              </div>
              {activeTab === 'discussion' && (
                <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-neon-cyan to-neon-blue" />
              )}
            </button>
          </div>
        </div>

        {/* Tab Content */}
        {activeTab === 'discussion' ? (
          <div className="h-[calc(100vh-500px)] min-h-[500px]">
            <ConversationPanel 
              proposalId={proposal.id} 
              proposalTitle={proposal.title}
              proposal={proposal}
            />
          </div>
        ) : (
          <div className="space-y-6">
            {/* Key Metrics */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-neon-cyan/20 rounded-lg">
                <DollarSign className="h-5 w-5 text-neon-cyan" />
              </div>
              <span className="text-gray-400 text-sm">Initial Budget</span>
            </div>
            <p className="text-2xl font-bold text-white">
              ${proposal.initial_budget.toLocaleString()}
            </p>
          </div>

          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-green-500/20 rounded-lg">
                <TrendingUp className="h-5 w-5 text-green-400" />
              </div>
              <span className="text-gray-400 text-sm">Expected Returns</span>
            </div>
            <p className="text-2xl font-bold text-white">
              {(() => {
                const returns = proposal.expected_returns;
                if (!returns) return 'TBD';
                // Handle refined format: monthly_min/monthly_max
                if (returns.monthly_min !== undefined || returns.monthly_max !== undefined) {
                  const min = returns.monthly_min ?? 0;
                  const max = returns.monthly_max ?? min;
                  if (min === max) {
                    return `$${min.toLocaleString()}/mo`;
                  }
                  return `$${min.toLocaleString()}-${max.toLocaleString()}/mo`;
                }
                // Handle original opportunity format: min/max
                if (returns.min !== undefined || returns.max !== undefined) {
                  const min = returns.min ?? 0;
                  const max = returns.max ?? min;
                  const timeframe = returns.timeframe || 'monthly';
                  if (min === max) {
                    return `$${min.toLocaleString()}/${timeframe.charAt(0)}`;
                  }
                  return `$${min.toLocaleString()}-${max.toLocaleString()}/${timeframe.charAt(0)}`;
                }
                // Handle simple monthly format
                if (returns.monthly) {
                  return `$${returns.monthly.toLocaleString()}/mo`;
                }
                return 'TBD';
              })()}
            </p>
          </div>

          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className={`p-2 rounded-lg ${riskColors[proposal.risk_level]}`}>
                <AlertTriangle className="h-5 w-5" />
              </div>
              <span className="text-gray-400 text-sm">Risk Level</span>
            </div>
            <p className="text-2xl font-bold text-white capitalize">
              {proposal.risk_level}
            </p>
          </div>

          {proposal.bitcoin_budget_sats != null && proposal.bitcoin_budget_sats > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-yellow-500/20 rounded-lg">
                <Bitcoin className="h-5 w-5 text-yellow-400" />
              </div>
              <span className="text-gray-400 text-sm">Bitcoin Budget</span>
            </div>
            <p className="text-2xl font-bold text-yellow-400">
              {proposal.bitcoin_budget_sats.toLocaleString()} sats
            </p>
            {proposal.bitcoin_budget_rationale && (
              <p className="text-xs text-gray-500 mt-1">{proposal.bitcoin_budget_rationale}</p>
            )}
          </div>
          )}
        </div>

        {/* Status Actions */}
        {proposal.status === 'pending' && (
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-6">
            <h3 className="text-lg font-semibold text-yellow-400 mb-3">Review Actions</h3>
            <div className="flex gap-3">
              <button
                onClick={() => updateStatusMutation.mutate('approved')}
                disabled={updateStatusMutation.isPending}
                className="px-4 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors flex items-center gap-2"
              >
                <CheckCircle className="h-4 w-4" />
                Approve
              </button>
              <button
                onClick={() => updateStatusMutation.mutate('under_review')}
                disabled={updateStatusMutation.isPending}
                className="px-4 py-2 bg-blue-500/20 text-blue-400 rounded-lg hover:bg-blue-500/30 transition-colors flex items-center gap-2"
              >
                <Clock className="h-4 w-4" />
                Review
              </button>
              <button
                onClick={() => updateStatusMutation.mutate('deferred')}
                disabled={updateStatusMutation.isPending}
                className="px-4 py-2 bg-gray-500/20 text-gray-400 rounded-lg hover:bg-gray-500/30 transition-colors flex items-center gap-2"
              >
                <Clock className="h-4 w-4" />
                Defer
              </button>
              <button
                onClick={() => updateStatusMutation.mutate('rejected')}
                disabled={updateStatusMutation.isPending}
                className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center gap-2"
              >
                <XCircle className="h-4 w-4" />
                Reject
              </button>
            </div>
          </div>
        )}

        {/* Description */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-bold text-white mb-4">Detailed Description</h2>
          <div data-color-mode="dark" className="prose prose-invert max-w-none">
            <SanitizedMarkdown source={proposal.detailed_description} />
          </div>
        </div>

        {/* Risk Assessment */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-bold text-white mb-4">Risk Assessment</h2>
          <div data-color-mode="dark" className="prose prose-invert max-w-none mb-4">
            <SanitizedMarkdown source={proposal.risk_description} />
          </div>
          
          <h3 className="text-lg font-semibold text-white mb-2">Stop Loss Threshold</h3>
          <div className="border border-gray-700 rounded-lg overflow-hidden">
            <CodeMirror
              value={JSON.stringify(proposal.stop_loss_threshold, null, 2)}
              extensions={[json()]}
              theme="dark"
              editable={false}
              basicSetup={{
                lineNumbers: false,
                foldGutter: false,
                highlightActiveLine: false,
              }}
              style={{
                fontSize: '14px',
                backgroundColor: 'rgba(0, 0, 0, 0.3)',
              }}
            />
          </div>
        </div>

        {/* Success Criteria */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-bold text-white mb-4">Success Criteria</h2>
          <div className="border border-gray-700 rounded-lg overflow-hidden">
            <CodeMirror
              value={JSON.stringify(proposal.success_criteria, null, 2)}
              extensions={[json()]}
              theme="dark"
              editable={false}
              basicSetup={{
                lineNumbers: false,
                foldGutter: false,
                highlightActiveLine: false,
              }}
              style={{
                fontSize: '14px',
                backgroundColor: 'rgba(0, 0, 0, 0.3)',
              }}
            />
          </div>
        </div>

        {/* Required Tools */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-bold text-white mb-4">Required Tools</h2>
          <div className="border border-gray-700 rounded-lg overflow-hidden">
            <CodeMirror
              value={JSON.stringify(proposal.required_tools, null, 2)}
              extensions={[json()]}
              theme="dark"
              editable={false}
              basicSetup={{
                lineNumbers: false,
                foldGutter: false,
                highlightActiveLine: false,
              }}
              style={{
                fontSize: '14px',
                backgroundColor: 'rgba(0, 0, 0, 0.3)',
              }}
            />
          </div>
        </div>

        {/* Required Inputs */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-2xl font-bold text-white mb-4">Required Inputs</h2>
          <div className="border border-gray-700 rounded-lg overflow-hidden">
            <CodeMirror
              value={JSON.stringify(proposal.required_inputs, null, 2)}
              extensions={[json()]}
              theme="dark"
              editable={false}
              basicSetup={{
                lineNumbers: false,
                foldGutter: false,
                highlightActiveLine: false,
              }}
              style={{
                fontSize: '14px',
                backgroundColor: 'rgba(0, 0, 0, 0.3)',
              }}
            />
          </div>
        </div>

        {/* Implementation Timeline */}
        {proposal.implementation_timeline && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <h2 className="text-2xl font-bold text-white mb-4">Implementation Timeline</h2>
            <div className="border border-gray-700 rounded-lg overflow-hidden">
              <CodeMirror
                value={JSON.stringify(proposal.implementation_timeline, null, 2)}
                extensions={[json()]}
                theme="dark"
                editable={false}
                basicSetup={{
                  lineNumbers: false,
                  foldGutter: false,
                  highlightActiveLine: false,
                }}
                style={{
                  fontSize: '14px',
                  backgroundColor: 'rgba(0, 0, 0, 0.3)',
                }}
              />
            </div>
          </div>
        )}
          </div>
        )}
      </div>

      {/* Delete Confirmation Modal */}
      {showDeleteModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 max-w-md w-full mx-4">
            <h3 className="text-xl font-bold text-white mb-4">Delete Proposal</h3>
            <p className="text-gray-300 mb-6">
              Are you sure you want to delete this proposal? This action cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowDeleteModal(false)}
                className="btn-secondary"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  deleteMutation.mutate();
                  setShowDeleteModal(false);
                }}
                disabled={deleteMutation.isPending}
                className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors"
              >
                {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Layout>
  );
}
