import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { useState } from 'react';
import { Layout } from '@/components/layout/Layout';
import { toolsService } from '@/services/tools';
import { Plus, Loader2, Filter, Grid, List, Wrench, User, Calendar, Activity, Server, Image as ImageIcon } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import { ToolOperationsTab } from '@/components/tools/ToolOperationsTab';
import { ResourcesTab } from '@/components/tools/ResourcesTab';
import { MediaLibraryTab } from '@/components/tools/MediaLibraryTab';
import type { ToolStatus, ToolCategory } from '@/types';
import { formatDistanceToNow } from 'date-fns';

type TabType = 'catalog' | 'operations' | 'resources' | 'media';

const STATUS_COLORS: Record<ToolStatus, string> = {
  requested: 'bg-blue-500/10 text-blue-400 border-blue-500/30',
  under_review: 'bg-yellow-500/10 text-yellow-400 border-yellow-500/30',
  changes_requested: 'bg-orange-500/10 text-orange-400 border-orange-500/30',
  approved: 'bg-green-500/10 text-green-400 border-green-500/30',
  rejected: 'bg-red-500/10 text-red-400 border-red-500/30',
  implementing: 'bg-purple-500/10 text-purple-400 border-purple-500/30',
  testing: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/30',
  blocked: 'bg-red-600/10 text-red-400 border-red-600/30',
  on_hold: 'bg-gray-500/10 text-gray-400 border-gray-500/30',
  implemented: 'bg-neon-cyan/10 text-neon-cyan border-neon-cyan/30',
  deprecated: 'bg-yellow-600/10 text-yellow-600 border-yellow-600/30',
  retired: 'bg-gray-600/10 text-gray-600 border-gray-600/30',
};

const CATEGORY_ICONS: Record<ToolCategory, string> = {
  api: '🔌',
  data_source: '📊',
  automation: '⚙️',
  analysis: '🔍',
  communication: '💬',
};

export function ToolsPage() {
  const { user } = useAuthStore();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = (searchParams.get('tab') as TabType) || 'catalog';
  
  const setActiveTab = (tab: TabType) => {
    setSearchParams({ tab });
  };

  const [statusFilter, setStatusFilter] = useState<ToolStatus | 'all'>('all');
  const [categoryFilter, setCategoryFilter] = useState<ToolCategory | 'all'>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [searchQuery, setSearchQuery] = useState('');

  const { data: tools, isLoading } = useQuery({
    queryKey: ['tools', statusFilter, categoryFilter, searchQuery],
    queryFn: () =>
      toolsService.listTools({
        status: statusFilter !== 'all' ? statusFilter : undefined,
        category: categoryFilter !== 'all' ? categoryFilter : undefined,
        search: searchQuery || undefined,
      }),
    enabled: activeTab === 'catalog',
  });

  const statusOptions: Array<{ value: ToolStatus | 'all'; label: string }> = [
    { value: 'all', label: 'All Status' },
    { value: 'requested', label: 'Requested' },
    { value: 'under_review', label: 'Under Review' },
    { value: 'approved', label: 'Approved' },
    { value: 'implementing', label: 'Implementing' },
    { value: 'testing', label: 'Testing' },
    { value: 'implemented', label: 'Implemented' },
    { value: 'blocked', label: 'Blocked' },
    { value: 'on_hold', label: 'On Hold' },
  ];

  const categoryOptions: Array<{ value: ToolCategory | 'all'; label: string; icon: string }> = [
    { value: 'all', label: 'All Categories', icon: '📦' },
    { value: 'api', label: 'API', icon: CATEGORY_ICONS.api },
    { value: 'data_source', label: 'Data Source', icon: CATEGORY_ICONS.data_source },
    { value: 'automation', label: 'Automation', icon: CATEGORY_ICONS.automation },
    { value: 'analysis', label: 'Analysis', icon: CATEGORY_ICONS.analysis },
    { value: 'communication', label: 'Communication', icon: CATEGORY_ICONS.communication },
  ];

  const formatStatus = (status: string) => {
    return status
      .split('_')
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(' ');
  };

  // Grid View Component
  const ToolsGridView = () => (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {tools?.map((tool) => (
        <Link
          key={tool.id}
          to={`/tools/${tool.id}`}
          className="group bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 hover:border-neon-cyan/50 transition-all duration-300 p-6 hover:shadow-lg hover:shadow-neon-cyan/20"
        >
          {/* Header */}
          <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-3">
              <span className="text-3xl">{CATEGORY_ICONS[tool.category]}</span>
              <div>
                <h3 className="font-semibold text-white group-hover:text-neon-cyan transition-colors">
                  {tool.name}
                </h3>
                <span
                  className={`inline-block px-2 py-0.5 text-xs font-medium border rounded-full mt-1 ${
                    STATUS_COLORS[tool.status]
                  }`}
                >
                  {formatStatus(tool.status)}
                </span>
              </div>
            </div>
          </div>

          {/* Description */}
          <p className="text-gray-400 text-sm line-clamp-2 mb-4">{tool.description}</p>

          {/* Tags */}
          {tool.tags && tool.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {tool.tags.slice(0, 3).map((tag, idx) => (
                <span
                  key={idx}
                  className="px-2 py-0.5 text-xs bg-gray-800/50 text-gray-400 rounded-full"
                >
                  {tag}
                </span>
              ))}
              {tool.tags.length > 3 && (
                <span className="px-2 py-0.5 text-xs bg-gray-800/50 text-gray-400 rounded-full">
                  +{tool.tags.length - 3}
                </span>
              )}
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-between text-xs text-gray-500 pt-4 border-t border-gray-800">
            <div className="flex items-center gap-1">
              <User className="h-3.5 w-3.5" />
              <span>{tool.assigned_to_username || 'Unassigned'}</span>
            </div>
            <div className="flex items-center gap-1">
              <Calendar className="h-3.5 w-3.5" />
              <span>{formatDistanceToNow(new Date(tool.created_at), { addSuffix: true })}</span>
            </div>
          </div>
        </Link>
      ))}
    </div>
  );

  // List View Component
  const ToolsListView = () => (
    <div className="space-y-3">
      {tools?.map((tool) => (
        <Link
          key={tool.id}
          to={`/tools/${tool.id}`}
          className="group flex items-center gap-4 bg-gradient-to-r from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-lg border border-gray-800 hover:border-neon-cyan/50 transition-all duration-300 p-4 hover:shadow-lg hover:shadow-neon-cyan/10"
        >
          <span className="text-2xl">{CATEGORY_ICONS[tool.category]}</span>

          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-1">
              <h3 className="font-semibold text-white group-hover:text-neon-cyan transition-colors truncate">
                {tool.name}
              </h3>
              <span
                className={`px-2 py-0.5 text-xs font-medium border rounded-full ${
                  STATUS_COLORS[tool.status]
                }`}
              >
                {formatStatus(tool.status)}
              </span>
            </div>
            <p className="text-sm text-gray-400 truncate">{tool.description}</p>
          </div>

          <div className="flex items-center gap-6 text-sm text-gray-500">
            <div className="flex items-center gap-1.5">
              <User className="h-4 w-4" />
              <span>{tool.assigned_to_username || 'Unassigned'}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Calendar className="h-4 w-4" />
              <span className="whitespace-nowrap">
                {formatDistanceToNow(new Date(tool.created_at), { addSuffix: true })}
              </span>
            </div>
          </div>
        </Link>
      ))}
    </div>
  );

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white flex items-center gap-3">
              <Wrench className="h-8 w-8 text-neon-cyan" />
              Tools
            </h1>
            <p className="mt-1 text-gray-400">Manage and discover available tools for agents</p>
          </div>
          {activeTab === 'catalog' && (
            <Link to="/tools/new" className="btn-primary inline-flex items-center justify-center">
              <Plus className="h-5 w-5 mr-2" />
              Request Tool
            </Link>
          )}
        </div>

        {/* Tabs — Catalog and Media Library visible to all users; Operations and Resources admin-only */}
        <div className="border-b border-navy-600">
          <nav className="flex gap-4" aria-label="Tabs">
            <button
              onClick={() => setActiveTab('catalog')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'catalog'
                  ? 'border-neon-cyan text-neon-cyan'
                  : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              <Wrench className="h-4 w-4" />
              Catalog
            </button>
            {user?.role === 'admin' && (
              <>
                <button
                  onClick={() => setActiveTab('operations')}
                  className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                    activeTab === 'operations'
                      ? 'border-neon-cyan text-neon-cyan'
                      : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
                  }`}
                >
                  <Activity className="h-4 w-4" />
                  Operations
                </button>
                <button
                  onClick={() => setActiveTab('resources')}
                  className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                    activeTab === 'resources'
                      ? 'border-neon-cyan text-neon-cyan'
                      : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
                  }`}
                >
                  <Server className="h-4 w-4" />
                  Resources
                </button>
              </>
            )}
            <button
              onClick={() => setActiveTab('media')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'media'
                  ? 'border-neon-cyan text-neon-cyan'
                  : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              <ImageIcon className="h-4 w-4" />
              Media Library
            </button>
          </nav>
        </div>

        {/* Tab Content */}
        {activeTab === 'operations' && user?.role === 'admin' ? (
          <ToolOperationsTab />
        ) : activeTab === 'resources' && user?.role === 'admin' ? (
          <ResourcesTab />
        ) : activeTab === 'media' ? (
          <MediaLibraryTab />
        ) : (
          <>
            {/* Filters */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {/* Search */}
              <div className="md:col-span-1">
                <input
                  type="text"
                  placeholder="Search tools..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                />
              </div>

              {/* Status Filter */}
              <div className="flex items-center gap-3">
                <Filter className="h-5 w-5 text-gray-400" />
                <select
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as ToolStatus | 'all')}
                  className="flex-1 px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                >
                  {statusOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Category Filter */}
              <div className="flex items-center gap-3">
                <select
                  value={categoryFilter}
                  onChange={(e) => setCategoryFilter(e.target.value as ToolCategory | 'all')}
                  className="flex-1 px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                >
                  {categoryOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.icon} {option.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* View Toggle */}
            <div className="flex items-center justify-end gap-2">
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

            {/* Content */}
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
              </div>
            ) : !tools || tools.length === 0 ? (
              <div className="text-center py-12">
                <Wrench className="h-16 w-16 mx-auto mb-4 text-gray-600" />
                <h3 className="text-xl font-semibold text-gray-400 mb-2">No tools found</h3>
                <p className="text-gray-500">Try adjusting your filters or request a new tool</p>
              </div>
            ) : viewMode === 'grid' ? (
              <ToolsGridView />
            ) : (
              <ToolsListView />
            )}
          </>
        )}
      </div>
    </Layout>
  );
}
