import { useState } from 'react';
import { Layout } from '@/components/layout/Layout';
import { Zap, TrendingUp, Target, MessageSquare, ChevronRight } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { proposalsService } from '@/services/proposals';
import { opportunitiesService } from '@/services/opportunities';
import { tasksService } from '@/services/tasks';
import { campaignsService } from '@/services/campaigns';
import { TasksWidget } from '@/components/dashboard/TasksWidget';
import { FinancialRollup } from '@/components/dashboard/FinancialRollup';
import { CampaignPerformanceChart } from '@/components/dashboard/CampaignPerformanceChart';
import { SatsTracker } from '@/components/dashboard/SatsTracker';
import { WalletWidget } from '@/components/dashboard/WalletWidget';
import { UsageAnalyticsPanel } from '@/components/dashboard/UsageAnalyticsPanel';
import type { PipelineStats } from '@/services/opportunities';

export function DashboardPage() {
  const [financialDays, setFinancialDays] = useState(30);

  const { data: proposals } = useQuery({
    queryKey: ['proposals'],
    queryFn: () => proposalsService.list(),
  });

  const { data: pipelineStats } = useQuery({
    queryKey: ['pipeline-stats'],
    queryFn: () => opportunitiesService.getPipelineStats(),
    refetchInterval: 30000,
  });

  const { data: taskCounts } = useQuery({
    queryKey: ['task-counts'],
    queryFn: () => tasksService.getCounts(),
    refetchInterval: 30000,
  });

  const { data: campaigns } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => campaignsService.list(),
    refetchInterval: 60000,
  });

  const proposalCount = proposals?.length || 0;
  const activeTaskCount = (taskCounts?.ready || 0) + (taskCounts?.in_progress || 0);
  const activeCampaignCount = campaigns?.filter(c => c.status === 'active').length || 0;

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header with Wallet Widget + Sats Tracker */}
        <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-neon-cyan">Dashboard</h1>
            <p className="mt-1 text-gray-400">Welcome to Money Agents</p>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 min-w-0">
            <WalletWidget />
            <SatsTracker />
          </div>
        </div>

        {/* ⚡ FINANCIAL OVERVIEW - Featured Section */}
        <section>
          <div className="flex items-center gap-2 mb-4">
            <Zap className="w-5 h-5 text-neon-yellow" />
            <h2 className="text-lg font-semibold text-white">Financial Overview</h2>
            <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">
              {financialDays === 0 ? 'All Time' : `${financialDays} Days`}
            </span>
          </div>
          <FinancialRollup days={financialDays} onDaysChange={setFinancialDays} />
        </section>

        {/* 📊 Campaign Performance Chart */}
        <section>
          <CampaignPerformanceChart days={financialDays || 30} />
        </section>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
          <div className="card">
            <div className="flex items-center space-x-3">
              <div className="p-3 bg-neon-cyan/10 rounded-lg">
                <Zap className="h-6 w-6 text-neon-cyan" />
              </div>
              <div>
                <p className="text-sm text-gray-400">Active Tasks</p>
                <p className="text-2xl font-bold text-white">{activeTaskCount}</p>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="flex items-center space-x-3">
              <div className="p-3 bg-neon-purple/10 rounded-lg">
                <Target className="h-6 w-6 text-neon-purple" />
              </div>
              <div>
                <p className="text-sm text-gray-400">Proposals</p>
                <p className="text-2xl font-bold text-white">{proposalCount}</p>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="flex items-center space-x-3">
              <div className="p-3 bg-neon-green/10 rounded-lg">
                <TrendingUp className="h-6 w-6 text-neon-green" />
              </div>
              <div>
                <p className="text-sm text-gray-400">Active Campaigns</p>
                <p className="text-2xl font-bold text-white">{activeCampaignCount}</p>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="flex items-center space-x-3">
              <div className={`p-3 rounded-lg ${(taskCounts?.overdue || 0) > 0 ? 'bg-red-500/10' : 'bg-neon-yellow/10'}`}>
                <MessageSquare className={`h-6 w-6 ${(taskCounts?.overdue || 0) > 0 ? 'text-red-400' : 'text-neon-yellow'}`} />
              </div>
              <div>
                <p className="text-sm text-gray-400">Overdue Tasks</p>
                <p className={`text-2xl font-bold ${(taskCounts?.overdue || 0) > 0 ? 'text-red-400' : 'text-white'}`}>
                  {taskCounts?.overdue || 0}
                </p>
              </div>
            </div>
          </div>
        </div>

        {/* Main Content Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Tasks Widget */}
          <TasksWidget />

          {/* Opportunity Pipeline */}
          <div className="card">
            <h2 className="text-xl font-bold text-white mb-4">Opportunity Pipeline</h2>
            {pipelineStats ? (
              <PipelineFunnel stats={pipelineStats} />
            ) : (
              <div className="text-gray-400 text-center py-8">Loading pipeline...</div>
            )}
          </div>
        </div>

        {/* Usage Analytics (Admin Only) */}
        <UsageAnalyticsPanel />
      </div>
    </Layout>
  );
}

// Pipeline Funnel Component
function PipelineFunnel({ stats }: { stats: PipelineStats }) {
  // Find the second largest to use as scale reference when one bar dominates
  const counts = stats.stages.map(s => s.count).sort((a, b) => b - a);
  const maxCount = counts[0] || 1;
  const secondMax = counts[1] || maxCount;
  
  // If the largest is more than 5x the second largest, use truncation
  const truncationThreshold = 5;
  const shouldTruncate = maxCount > secondMax * truncationThreshold && secondMax > 0;
  const scaleMax = shouldTruncate ? secondMax * 2 : maxCount;
  
  const colorMap: Record<string, string> = {
    gray: 'bg-gray-500',
    blue: 'bg-blue-500',
    cyan: 'bg-neon-cyan',
    purple: 'bg-neon-purple',
    green: 'bg-neon-green',
  };

  const textColorMap: Record<string, string> = {
    gray: 'text-gray-400',
    blue: 'text-blue-400',
    cyan: 'text-neon-cyan',
    purple: 'text-neon-purple',
    green: 'text-neon-green',
  };

  return (
    <div className="space-y-3">
      {stats.stages.map((stage, index) => {
        const isTruncated = shouldTruncate && stage.count > scaleMax;
        const widthPercent = isTruncated 
          ? 95 // Nearly full width for truncated bars
          : Math.max(20, (stage.count / scaleMax) * 100);
        const isLast = index === stats.stages.length - 1;
        
        return (
          <div key={stage.name} className="flex items-center gap-3">
            <div className="w-24 text-right">
              <span className={`text-sm font-medium ${textColorMap[stage.color] || 'text-gray-400'}`}>
                {stage.name}
              </span>
            </div>
            <div className="flex-1 flex items-center gap-2">
              {isTruncated ? (
                // Truncated bar with break indicator
                <div className="flex items-center flex-1" style={{ width: `${widthPercent}%` }}>
                  <div 
                    className={`h-8 rounded-l ${colorMap[stage.color] || 'bg-gray-500'} flex items-center justify-start pl-3`}
                    style={{ width: '40%' }}
                  >
                    <span className="text-white font-bold text-sm">
                      {stage.count}
                    </span>
                  </div>
                  {/* Break indicator - zigzag */}
                  <svg className="h-8 w-4 flex-shrink-0" viewBox="0 0 16 32" fill="none">
                    <path 
                      d="M0 0 L8 8 L0 16 L8 24 L0 32" 
                      stroke="currentColor" 
                      strokeWidth="2"
                      className={textColorMap[stage.color] || 'text-gray-400'}
                      fill="none"
                    />
                    <path 
                      d="M8 0 L16 8 L8 16 L16 24 L8 32" 
                      stroke="currentColor" 
                      strokeWidth="2"
                      className={textColorMap[stage.color] || 'text-gray-400'}
                      fill="none"
                    />
                  </svg>
                  <div 
                    className={`h-8 rounded-r ${colorMap[stage.color] || 'bg-gray-500'} flex-1`}
                  />
                </div>
              ) : (
                // Normal bar
                <div 
                  className={`h-8 rounded ${colorMap[stage.color] || 'bg-gray-500'} transition-all duration-500 flex items-center justify-end pr-3`}
                  style={{ width: `${widthPercent}%` }}
                >
                  <span className="text-white font-bold text-sm">
                    {stage.count}
                  </span>
                </div>
              )}
              {!isLast && (
                <ChevronRight className="h-4 w-4 text-gray-600 flex-shrink-0" />
              )}
            </div>
          </div>
        );
      })}
      
      {/* Summary */}
      <div className="mt-4 pt-4 border-t border-gray-700 flex justify-between text-sm">
        <span className="text-gray-400">
          Total Opportunities: <span className="text-white font-medium">{stats.totals.opportunities}</span>
        </span>
        <span className="text-gray-400">
          Active Proposals: <span className="text-white font-medium">{stats.totals.proposals}</span>
        </span>
        <span className="text-gray-400">
          Campaigns: <span className="text-white font-medium">{stats.totals.campaigns}</span>
        </span>
      </div>
    </div>
  );
}
