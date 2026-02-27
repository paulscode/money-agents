import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState, useMemo } from 'react';
import { Layout } from '@/components/layout/Layout';
import { ProposalCard } from '@/components/proposals/ProposalCard';
import { proposalsService } from '@/services/proposals';
import { conversationsService } from '@/services/conversations';
import { Plus, Loader2, Filter, Grid, List } from 'lucide-react';
import type { ProposalStatus } from '@/types';

type CampaignFilter = 'needs_action' | 'in_progress' | 'all';

export function ProposalsPage() {
  const [statusFilter, setStatusFilter] = useState<ProposalStatus | 'all'>('all');
  const [campaignFilter, setCampaignFilter] = useState<CampaignFilter>('needs_action');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  const { data: proposals, isLoading } = useQuery({
    queryKey: ['proposals', statusFilter, campaignFilter],
    queryFn: () => {
      const params: { status?: string; has_campaign?: boolean } = {};
      if (statusFilter !== 'all') {
        params.status = statusFilter;
      }
      if (campaignFilter === 'needs_action') {
        params.has_campaign = false;
      } else if (campaignFilter === 'in_progress') {
        params.has_campaign = true;
      }
      // 'all' passes neither filter
      return proposalsService.list(Object.keys(params).length > 0 ? params : undefined);
    },
  });

  // Fetch unread counts for all proposals
  const { data: unreadCounts = [] } = useQuery({
    queryKey: ['unreadCounts'],
    queryFn: () => conversationsService.getAllProposalsUnreadCounts(),
    refetchInterval: 10000, // Refresh every 10 seconds
  });

  // Create a map for quick lookup
  const unreadCountMap = useMemo(() => {
    return new Map(unreadCounts.map(uc => [uc.proposal_id, uc.unread_count]));
  }, [unreadCounts]);

  // Filter proposals - exclude rejected from "needs_action" view
  const filteredProposals = useMemo(() => {
    const list = proposals || [];
    if (campaignFilter === 'needs_action') {
      // Exclude rejected proposals from "Needs Action" - they're done
      return list.filter(p => p.status !== 'rejected');
    }
    return list;
  }, [proposals, campaignFilter]);

  const campaignFilterOptions: Array<{ value: CampaignFilter; label: string }> = [
    { value: 'needs_action', label: '📋 Needs Action' },
    { value: 'in_progress', label: '🚀 In Progress' },
    { value: 'all', label: 'All Proposals' },
  ];

  const statusOptions: Array<{ value: ProposalStatus | 'all'; label: string }> = [
    { value: 'all', label: 'Any Status' },
    { value: 'draft_from_scout', label: '🤖 Scout Draft' },
    { value: 'pending', label: 'Pending' },
    { value: 'proposed', label: 'Proposed' },
    { value: 'under_review', label: 'Under Review' },
    { value: 'approved', label: 'Approved' },
    { value: 'rejected', label: 'Rejected' },
    { value: 'deferred', label: 'Deferred' },
    { value: 'submitted', label: 'Submitted' },
    { value: 'changes_requested', label: 'Changes Requested' },
  ];

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white">Proposals</h1>
            <p className="mt-1 text-gray-400">AI-generated money-making opportunities</p>
          </div>
          <Link to="/proposals/new" className="btn-primary inline-flex items-center justify-center">
            <Plus className="h-5 w-5 mr-2" />
            New Proposal
          </Link>
        </div>

        {/* Filters and View Toggle */}
        <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
          <div className="flex items-center gap-3 flex-wrap">
            <Filter className="h-5 w-5 text-gray-400" />
            <select
              value={campaignFilter}
              onChange={(e) => setCampaignFilter(e.target.value as CampaignFilter)}
              className="px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            >
              {campaignFilterOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as ProposalStatus | 'all')}
              className="px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            >
              {statusOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'grid'
                  ? 'bg-neon-cyan/20 text-neon-cyan'
                  : 'bg-gray-900/50 text-gray-400 hover:text-white'
              }`}
              title="Grid View"
            >
              <Grid className="h-5 w-5" />
            </button>
            <button
              onClick={() => setViewMode('list')}
              className={`p-2 rounded-lg transition-colors ${
                viewMode === 'list'
                  ? 'bg-neon-cyan/20 text-neon-cyan'
                  : 'bg-gray-900/50 text-gray-400 hover:text-white'
              }`}
              title="List View"
            >
              <List className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Loading State */}
        {isLoading ? (
          <div className="flex justify-center items-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
          </div>
        ) : filteredProposals.length > 0 ? (
          <div
            className={
              viewMode === 'grid'
                ? 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6'
                : 'space-y-4'
            }
          >
            {filteredProposals.map((proposal) => (
              <ProposalCard 
                key={proposal.id} 
                proposal={proposal} 
                unreadCount={unreadCountMap.get(proposal.id) || 0}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-12 bg-gray-900/50 border border-gray-800 rounded-lg">
            <p className="text-gray-400 mb-4">
              {campaignFilter === 'needs_action' && statusFilter === 'all'
                ? 'No proposals awaiting action. All proposals have campaigns!'
                : campaignFilter === 'in_progress' && statusFilter === 'all'
                ? 'No proposals with campaigns yet.'
                : campaignFilter === 'all' && statusFilter === 'all'
                ? 'No proposals yet. Create your first one!'
                : `No matching proposals found.`}
            </p>
            {campaignFilter === 'needs_action' ? (
              <button 
                onClick={() => setCampaignFilter('all')}
                className="btn-secondary inline-flex items-center"
              >
                View All Proposals
              </button>
            ) : (
              <Link to="/proposals/new" className="btn-primary inline-flex items-center">
                <Plus className="h-5 w-5 mr-2" />
                Create Proposal
              </Link>
            )}
          </div>
        )}
      </div>
    </Layout>
  );
}
