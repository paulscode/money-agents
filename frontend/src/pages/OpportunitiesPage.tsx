import { useState, useCallback, useEffect, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { opportunitiesService } from '@/services/opportunities';
import { HopperStatusCard } from '@/components/opportunities/HopperStatusCard';
import { OpportunityRow } from '@/components/opportunities/OpportunityRow';
import { BulkActionsToolbar } from '@/components/opportunities/BulkActionsToolbar';
import { ScoutSettingsModal } from '@/components/opportunities/ScoutSettingsModal';
import {
  Search,
  Loader2,
  Filter,
  RefreshCw,
  Keyboard,
  ChevronDown,
  TrendingDown,
  AlertTriangle,
  Target,
  Settings,
} from 'lucide-react';
import type {
  RankingTier,
  OpportunityStatus,
  OpportunityType,
} from '@/types/opportunity';

type SortField = 'score' | 'discovered_at' | 'tier' | 'revenue';
type SortOrder = 'asc' | 'desc';

const TIER_ORDER: Record<RankingTier, number> = {
  top_pick: 0,
  promising: 1,
  maybe: 2,
  unlikely: 3,
};

export function OpportunitiesPage() {
  const queryClient = useQueryClient();

  // Filters
  const [tierFilter, setTierFilter] = useState<RankingTier | 'all'>('all');
  const [typeFilter, setTypeFilter] = useState<OpportunityType | 'all'>('all');
  const [statusFilter, setStatusFilter] = useState<OpportunityStatus | 'all'>(
    'evaluated'
  );
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [showDismissed, setShowDismissed] = useState(false);

  // Debounce search query (300ms) to avoid excessive API calls
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchQuery), 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Sorting
  const [sortField, setSortField] = useState<SortField>('score');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  // Selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // UI state
  const [showKeyboardHelp, setShowKeyboardHelp] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);

  // Action tracking
  const [pendingAction, setPendingAction] = useState<{
    id: string;
    action: 'approve' | 'dismiss';
  } | null>(null);

  // Fetch opportunities
  const {
    data: response,
    isLoading,
    error,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: [
      'opportunities',
      tierFilter,
      typeFilter,
      statusFilter,
      showDismissed,
      debouncedSearch,
    ],
    queryFn: () =>
      opportunitiesService.listOpportunities({
        tier: tierFilter !== 'all' ? tierFilter : undefined,
        opportunity_type: typeFilter !== 'all' ? typeFilter : undefined,
        status: statusFilter !== 'all' ? statusFilter : undefined,
        include_dismissed: showDismissed,
        search: debouncedSearch || undefined,
        limit: 100,
      }),
    refetchInterval: 30000, // Auto-refresh every 30 seconds
  });

  // Sort opportunities locally (search is now server-side)
  const sortedOpportunities = useMemo(() => {
    if (!response?.opportunities) return [];

    let filtered = response.opportunities;

    // Sort
    return [...filtered].sort((a, b) => {
      let comparison = 0;

      switch (sortField) {
        case 'score':
          comparison = (a.overall_score || 0) - (b.overall_score || 0);
          break;
        case 'discovered_at':
          comparison =
            new Date(a.discovered_at).getTime() -
            new Date(b.discovered_at).getTime();
          break;
        case 'tier':
          comparison =
            TIER_ORDER[a.ranking_tier || 'unlikely'] -
            TIER_ORDER[b.ranking_tier || 'unlikely'];
          break;
        case 'revenue':
          const aRev = a.estimated_revenue_potential?.max || 0;
          const bRev = b.estimated_revenue_potential?.max || 0;
          comparison = aRev - bRev;
          break;
      }

      return sortOrder === 'desc' ? -comparison : comparison;
    });
  }, [response?.opportunities, sortField, sortOrder]);

  // Approve mutation
  const approveMutation = useMutation({
    mutationFn: (id: string) => opportunitiesService.approveOpportunity(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      setPendingAction(null);
    },
    onError: () => {
      setPendingAction(null);
    },
  });

  // Dismiss mutation
  const dismissMutation = useMutation({
    mutationFn: (id: string) => opportunitiesService.dismissOpportunity(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      setPendingAction(null);
    },
    onError: () => {
      setPendingAction(null);
    },
  });

  // Bulk dismiss mutation
  const bulkDismissMutation = useMutation({
    mutationFn: (ids: string[]) =>
      opportunitiesService.bulkDismiss({ opportunity_ids: ids }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['opportunities'] });
      setSelectedIds(new Set());
    },
  });

  // Selection handlers
  const handleSelect = useCallback((id: string, selected: boolean) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (selected) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }, []);

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(sortedOpportunities.map((o) => o.id)));
  }, [sortedOpportunities]);

  const handleDeselectAll = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  // Action handlers
  const handleApprove = useCallback(
    (id: string) => {
      setPendingAction({ id, action: 'approve' });
      approveMutation.mutate(id);
    },
    [approveMutation]
  );

  const handleDismiss = useCallback(
    (id: string) => {
      setPendingAction({ id, action: 'dismiss' });
      dismissMutation.mutate(id);
    },
    [dismissMutation]
  );

  const handleBulkApprove = useCallback(async () => {
    // Approve each selected opportunity
    for (const id of selectedIds) {
      await approveMutation.mutateAsync(id);
    }
    setSelectedIds(new Set());
  }, [selectedIds, approveMutation]);

  const handleBulkDismiss = useCallback(() => {
    bulkDismissMutation.mutate(Array.from(selectedIds));
  }, [selectedIds, bulkDismissMutation]);

  // Sort handler
  const handleSort = useCallback(
    (field: SortField) => {
      if (sortField === field) {
        setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
      } else {
        setSortField(field);
        setSortOrder('desc');
      }
    },
    [sortField]
  );

  // Keyboard navigation
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Ignore if typing in an input
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return;
      }

      const opportunities = sortedOpportunities;
      const focused =
        focusedIndex >= 0 && focusedIndex < opportunities.length
          ? opportunities[focusedIndex]
          : null;

      switch (e.key) {
        case 'j': // Down
        case 'ArrowDown':
          e.preventDefault();
          setFocusedIndex((prev) =>
            Math.min(prev + 1, opportunities.length - 1)
          );
          break;

        case 'k': // Up
        case 'ArrowUp':
          e.preventDefault();
          setFocusedIndex((prev) => Math.max(prev - 1, 0));
          break;

        case 'a': // Approve
          if (focused && !pendingAction) {
            e.preventDefault();
            handleApprove(focused.id);
          }
          break;

        case 'd': // Dismiss
          if (focused && !pendingAction) {
            e.preventDefault();
            handleDismiss(focused.id);
          }
          break;

        case ' ': // Toggle selection
          if (focused) {
            e.preventDefault();
            handleSelect(focused.id, !selectedIds.has(focused.id));
          }
          break;

        case 'Escape':
          e.preventDefault();
          setSelectedIds(new Set());
          setFocusedIndex(-1);
          break;

        case 'r': // Refresh
          if (!e.metaKey && !e.ctrlKey) {
            e.preventDefault();
            refetch();
          }
          break;

        case '?': // Help
          e.preventDefault();
          setShowKeyboardHelp((prev) => !prev);
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [
    sortedOpportunities,
    focusedIndex,
    pendingAction,
    selectedIds,
    handleApprove,
    handleDismiss,
    handleSelect,
    refetch,
  ]);

  const tierOptions: Array<{ value: RankingTier | 'all'; label: string }> = [
    { value: 'all', label: 'All Tiers' },
    { value: 'top_pick', label: '🏆 Top Pick' },
    { value: 'promising', label: '⭐ Promising' },
    { value: 'maybe', label: '🤔 Maybe' },
    { value: 'unlikely', label: '❓ Unlikely' },
  ];

  const typeOptions: Array<{ value: OpportunityType | 'all'; label: string }> = [
    { value: 'all', label: 'All Types' },
    { value: 'arbitrage', label: '📈 Arbitrage' },
    { value: 'content', label: '✍️ Content' },
    { value: 'service', label: '🛠️ Service' },
    { value: 'product', label: '📦 Product' },
    { value: 'automation', label: '⚙️ Automation' },
    { value: 'affiliate', label: '🤝 Affiliate' },
    { value: 'investment', label: '💰 Investment' },
    { value: 'other', label: '💡 Other' },
  ];

  const statusOptions: Array<{
    value: OpportunityStatus | 'all';
    label: string;
  }> = [
    { value: 'all', label: 'All Status' },
    { value: 'evaluated', label: 'Ready for Review' },
    { value: 'discovered', label: 'Discovered' },
    { value: 'researching', label: 'Researching' },
    { value: 'presented', label: 'Presented' },
    { value: 'approved', label: 'Approved' },
  ];

  return (
    <Layout>
      <div className="space-y-4">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white flex items-center gap-3">
              <Target className="h-7 w-7 text-neon-cyan" />
              Opportunity Scout
            </h1>
            <p className="mt-1 text-sm text-gray-400">
              Review and approve opportunities • Sorted by score (best first)
            </p>
          </div>

          {/* Hopper Status */}
          {response?.hopper_status && (
            <HopperStatusCard
              hopper={response.hopper_status}
              className="w-64"
            />
          )}
        </div>

        {/* Filters Row */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Search */}
          <div className="relative flex-1 min-w-[200px] max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
            <input
              type="text"
              placeholder="Search opportunities..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm"
            />
          </div>

          {/* Tier Filter */}
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-gray-500" />
            <select
              value={tierFilter}
              onChange={(e) => setTierFilter(e.target.value as RankingTier | 'all')}
              className="px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
            >
              {tierOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {/* Type Filter */}
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as OpportunityType | 'all')}
            className="px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          >
            {typeOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as OpportunityStatus | 'all')
            }
            className="px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
          >
            {statusOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          {/* Show dismissed toggle */}
          <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showDismissed}
              onChange={(e) => setShowDismissed(e.target.checked)}
              className="rounded border-gray-600 bg-gray-800 text-neon-cyan focus:ring-neon-cyan"
            />
            Show dismissed
          </label>

          {/* Refresh button */}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 text-gray-400 hover:text-white transition-colors"
            title="Refresh (R)"
          >
            <RefreshCw
              className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`}
            />
          </button>

          {/* Keyboard help */}
          <button
            onClick={() => setShowKeyboardHelp((prev) => !prev)}
            className="p-2 text-gray-400 hover:text-white transition-colors"
            title="Keyboard shortcuts (?)"
          >
            <Keyboard className="h-4 w-4" />
          </button>

          {/* Settings */}
          <button
            onClick={() => setShowSettings(true)}
            className="p-2 text-gray-400 hover:text-white transition-colors"
            title="Scout settings"
          >
            <Settings className="h-4 w-4" />
          </button>
        </div>

        {/* Keyboard help panel */}
        {showKeyboardHelp && (
          <div className="bg-blue-900/20 border border-blue-500/30 rounded-lg p-4 text-sm">
            <div className="flex items-center gap-2 mb-2 font-medium text-blue-400">
              <Keyboard className="h-4 w-4" />
              Keyboard Shortcuts
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-1 text-xs text-gray-300">
              <span>
                <kbd className="kbd">j/↓</kbd> Move down
              </span>
              <span>
                <kbd className="kbd">k/↑</kbd> Move up
              </span>
              <span>
                <kbd className="kbd">a</kbd> Approve
              </span>
              <span>
                <kbd className="kbd">d</kbd> Dismiss
              </span>
              <span>
                <kbd className="kbd">Space</kbd> Toggle select
              </span>
              <span>
                <kbd className="kbd">Esc</kbd> Clear selection
              </span>
              <span>
                <kbd className="kbd">r</kbd> Refresh
              </span>
              <span>
                <kbd className="kbd">?</kbd> Toggle help
              </span>
            </div>
          </div>
        )}

        {/* Bulk Actions Toolbar */}
        {sortedOpportunities.length > 0 && (
          <BulkActionsToolbar
            selectedCount={selectedIds.size}
            totalCount={sortedOpportunities.length}
            onSelectAll={handleSelectAll}
            onDeselectAll={handleDeselectAll}
            onBulkApprove={handleBulkApprove}
            onBulkDismiss={handleBulkDismiss}
            isApproving={approveMutation.isPending}
            isDismissing={bulkDismissMutation.isPending}
          />
        )}

        {/* Error state */}
        {error && (
          <div className="bg-red-900/20 border border-red-500/30 rounded-lg p-4 flex items-center gap-3">
            <AlertTriangle className="h-5 w-5 text-red-400 flex-shrink-0" />
            <span className="text-red-300">Failed to load opportunities. Please try again.</span>
          </div>
        )}

        {/* Loading state */}
        {isLoading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
            <span className="ml-2 text-gray-400">Loading opportunities...</span>
          </div>
        )}

        {/* Opportunities Table */}
        {!isLoading && sortedOpportunities.length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
            {/* Table Header */}
            <div className="flex items-center gap-3 px-4 py-2 bg-gray-800/50 border-b border-gray-700 text-xs text-gray-400 uppercase tracking-wide">
              <div className="w-5"></div>
              <div className="w-4"></div>
              <button
                onClick={() => handleSort('score')}
                className="w-12 text-center flex items-center justify-center gap-1 hover:text-white"
              >
                Score
                {sortField === 'score' && (
                  <ChevronDown
                    className={`h-3 w-3 transition-transform ${
                      sortOrder === 'asc' ? 'rotate-180' : ''
                    }`}
                  />
                )}
              </button>
              <button
                onClick={() => handleSort('tier')}
                className="w-28 flex items-center gap-1 hover:text-white"
              >
                Tier
                {sortField === 'tier' && (
                  <ChevronDown
                    className={`h-3 w-3 transition-transform ${
                      sortOrder === 'asc' ? 'rotate-180' : ''
                    }`}
                  />
                )}
              </button>
              <div className="w-8 text-center">Type</div>
              <div className="flex-1">Title</div>
              <button
                onClick={() => handleSort('revenue')}
                className="w-32 text-right flex items-center justify-end gap-1 hover:text-white"
              >
                Revenue
                {sortField === 'revenue' && (
                  <ChevronDown
                    className={`h-3 w-3 transition-transform ${
                      sortOrder === 'asc' ? 'rotate-180' : ''
                    }`}
                  />
                )}
              </button>
              <div className="w-20 text-center">Effort</div>
              <button
                onClick={() => handleSort('discovered_at')}
                className="w-24 text-right flex items-center justify-end gap-1 hover:text-white"
              >
                Age
                {sortField === 'discovered_at' && (
                  <ChevronDown
                    className={`h-3 w-3 transition-transform ${
                      sortOrder === 'asc' ? 'rotate-180' : ''
                    }`}
                  />
                )}
              </button>
              <div className="w-20 text-center">Actions</div>
            </div>

            {/* Table Body */}
            <div>
              {sortedOpportunities.map((opportunity, index) => (
                <div
                  key={opportunity.id}
                  className={
                    focusedIndex === index
                      ? 'ring-2 ring-inset ring-neon-cyan/50'
                      : ''
                  }
                >
                  <OpportunityRow
                    opportunity={opportunity}
                    isSelected={selectedIds.has(opportunity.id)}
                    onSelect={handleSelect}
                    onApprove={handleApprove}
                    onDismiss={handleDismiss}
                    isApproving={
                      pendingAction?.id === opportunity.id &&
                      pendingAction.action === 'approve'
                    }
                    isDismissing={
                      pendingAction?.id === opportunity.id &&
                      pendingAction.action === 'dismiss'
                    }
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!isLoading && sortedOpportunities.length === 0 && (
          <div className="text-center py-12 bg-gray-900/30 rounded-lg border border-gray-800">
            <TrendingDown className="h-12 w-12 text-gray-600 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-gray-400 mb-2">
              No opportunities found
            </h3>
            <p className="text-sm text-gray-500 mb-4">
              {searchQuery
                ? 'Try adjusting your search or filters'
                : statusFilter === 'evaluated'
                ? 'No opportunities are ready for review. The Scout is working on it!'
                : 'Try changing your filters to see more opportunities'}
            </p>
            <button
              onClick={() => {
                setSearchQuery('');
                setTierFilter('all');
                setTypeFilter('all');
                setStatusFilter('all');
              }}
              className="text-neon-cyan hover:underline text-sm"
            >
              Clear all filters
            </button>
          </div>
        )}

        {/* Stats footer */}
        {response && response.total > 0 && (
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>
              Showing {sortedOpportunities.length} of {response.total}{' '}
              opportunities
            </span>
            {selectedIds.size > 0 && (
              <span className="text-neon-cyan">
                {selectedIds.size} selected
              </span>
            )}
          </div>
        )}
      </div>

      {/* Inline keyboard styles */}
      <style>{`
        .kbd {
          display: inline-block;
          padding: 0.125rem 0.375rem;
          font-family: ui-monospace, monospace;
          font-size: 0.75rem;
          line-height: 1rem;
          background-color: rgb(31 41 55);
          border: 1px solid rgb(75 85 99);
          border-radius: 0.25rem;
          margin-right: 0.25rem;
        }
      `}</style>

      {/* Settings Modal */}
      <ScoutSettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
      />
    </Layout>
  );
}
