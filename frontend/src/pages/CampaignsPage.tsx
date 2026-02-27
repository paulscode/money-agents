import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { useState } from 'react';
import { Layout } from '@/components/layout/Layout';
import { CampaignCard } from '@/components/campaigns/CampaignCard';
import { campaignsService } from '@/services/campaigns';
import { Loader2, Filter, Grid, List, Rocket, TrendingUp, DollarSign, CheckCircle2 } from 'lucide-react';
import type { CampaignStatus } from '@/types';

export function CampaignsPage() {
  const [statusFilter, setStatusFilter] = useState<CampaignStatus | 'all'>('all');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');

  const { data: campaigns, isLoading } = useQuery({
    queryKey: ['campaigns', statusFilter],
    queryFn: () => campaignsService.list(statusFilter !== 'all' ? { status: statusFilter } : undefined),
  });

  const filteredCampaigns = campaigns || [];

  // Calculate summary stats
  const stats = {
    active: filteredCampaigns.filter(c => c.status === 'active').length,
    totalBudget: filteredCampaigns.reduce((sum, c) => sum + c.budget_allocated, 0),
    totalSpent: filteredCampaigns.reduce((sum, c) => sum + c.budget_spent, 0),
    totalRevenue: filteredCampaigns.reduce((sum, c) => sum + c.revenue_generated, 0),
    completed: filteredCampaigns.filter(c => c.status === 'completed').length,
  };

  const statusOptions: Array<{ value: CampaignStatus | 'all'; label: string }> = [
    { value: 'all', label: 'All Campaigns' },
    { value: 'initializing', label: '🔄 Initializing' },
    { value: 'waiting_for_inputs', label: '⏳ Waiting for Inputs' },
    { value: 'active', label: '▶️ Active' },
    { value: 'paused', label: '⏸️ Paused' },
    { value: 'completed', label: '✅ Completed' },
    { value: 'terminated', label: '🛑 Terminated' },
    { value: 'failed', label: '❌ Failed' },
  ];

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white">Campaigns</h1>
            <p className="mt-1 text-gray-400">Active and past money-making campaigns</p>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
              <Rocket className="h-4 w-4 text-green-400" />
              <span>Active</span>
            </div>
            <p className="text-2xl font-bold text-white">{stats.active}</p>
          </div>
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
              <DollarSign className="h-4 w-4 text-neon-cyan" />
              <span>Budget Used</span>
            </div>
            <p className="text-2xl font-bold text-white">
              ${stats.totalSpent.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              <span className="text-sm text-gray-500"> / ${stats.totalBudget.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
            </p>
          </div>
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
              <TrendingUp className="h-4 w-4 text-green-400" />
              <span>Total Revenue</span>
            </div>
            <p className="text-2xl font-bold text-green-400">
              ${stats.totalRevenue.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            </p>
          </div>
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
            <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
              <CheckCircle2 className="h-4 w-4 text-neon-cyan" />
              <span>Completed</span>
            </div>
            <p className="text-2xl font-bold text-white">{stats.completed}</p>
          </div>
        </div>

        {/* Filters and View Toggle */}
        <div className="flex flex-col sm:flex-row gap-4 items-start sm:items-center justify-between">
          <div className="flex items-center gap-3">
            <Filter className="h-5 w-5 text-gray-400" />
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as CampaignStatus | 'all')}
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
        ) : filteredCampaigns.length > 0 ? (
          <div
            className={
              viewMode === 'grid'
                ? 'grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6'
                : 'space-y-4'
            }
          >
            {filteredCampaigns.map((campaign) => (
              <CampaignCard key={campaign.id} campaign={campaign} />
            ))}
          </div>
        ) : (
          <div className="text-center py-12 bg-gray-900/50 border border-gray-800 rounded-lg">
            <Rocket className="h-12 w-12 mx-auto text-gray-600 mb-4" />
            <p className="text-gray-400 mb-2">
              {statusFilter === 'all'
                ? 'No campaigns yet.'
                : `No ${statusFilter.replace('_', ' ')} campaigns found.`}
            </p>
            <p className="text-gray-500 text-sm mb-4">
              Campaigns are created when you approve a proposal.
            </p>
            <Link to="/proposals" className="btn-primary inline-flex items-center">
              View Proposals
            </Link>
          </div>
        )}
      </div>
    </Layout>
  );
}
