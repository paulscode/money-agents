/**
 * Agent Management Page
 * 
 * A comprehensive dashboard for monitoring and controlling AI agents and remote compute resources.
 * Features:
 * - Tabbed interface: AI Agents / Remote Agents
 * - AI Agent status overview with live updates
 * - Schedule configuration and budget management
 * - Run history and performance visualization
 * - Remote agent registration and connection monitoring
 */
import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Layout } from '@/components/layout/Layout';
import { AgentCard, AgentConfigModal, RunHistoryPanel, AgentInsightsPanel, RemoteAgentsTab } from '@/components/agents';
import { useAgents, useRecentRuns } from '@/hooks/useAgents';
import type { AgentSummary } from '@/types';
import { 
  Bot, 
  Activity, 
  RefreshCw, 
  Clock, 
  AlertTriangle,
  CheckCircle,
  Settings,
  Network,
} from 'lucide-react';

type TabType = 'ai' | 'remote';

export default function AgentManagementPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = (searchParams.get('tab') as TabType) || 'ai';
  
  const setActiveTab = (tab: TabType) => {
    setSearchParams({ tab });
  };
  
  const { data: agents, isLoading, error, refetch } = useAgents(true);
  const { data: recentRuns } = useRecentRuns(20);
  const [selectedAgent, setSelectedAgent] = useState<AgentSummary | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);

  // Calculate overview stats
  const stats = agents ? {
    total: agents.length,
    running: agents.filter(a => a.status === 'running').length,
    idle: agents.filter(a => a.status === 'idle' && a.is_enabled).length,
    paused: agents.filter(a => a.status === 'paused' || !a.is_enabled).length,
    error: agents.filter(a => a.status === 'error' || a.status === 'budget_exceeded').length,
    totalCost: agents.reduce((sum, a) => sum + a.total_cost_usd, 0),
    totalRuns: agents.reduce((sum, a) => sum + a.total_runs, 0),
  } : null;

  const handleConfigure = (agent: AgentSummary) => {
    setSelectedAgent(agent);
    setShowConfig(true);
  };

  const handleExpand = (slug: string) => {
    setExpandedAgent(expandedAgent === slug ? null : slug);
  };

  if (isLoading && activeTab === 'ai') {
    return (
      <Layout>
        <div className="flex items-center justify-center h-64">
          <RefreshCw className="h-8 w-8 text-neon-cyan animate-spin" />
        </div>
      </Layout>
    );
  }

  if (error && activeTab === 'ai') {
    return (
      <Layout>
        <div className="text-center py-12">
          <AlertTriangle className="h-12 w-12 text-red-500 mx-auto mb-4" />
          <h2 className="text-xl font-semibold text-white mb-2">Failed to load agents</h2>
          <p className="text-gray-400 mb-4">{(error as Error).message}</p>
          <button onClick={() => refetch()} className="btn-primary">
            Try Again
          </button>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-white flex items-center gap-3">
              <Bot className="h-8 w-8 text-neon-cyan" />
              Agent Control Center
            </h1>
            <p className="mt-1 text-gray-400">
              Monitor and configure your AI agents and remote compute resources
            </p>
          </div>
          {activeTab === 'ai' && (
            <button
              onClick={() => refetch()}
              className="btn-secondary inline-flex items-center gap-2"
            >
              <RefreshCw className="h-4 w-4" />
              Refresh
            </button>
          )}
        </div>

        {/* Tabs */}
        <div className="border-b border-navy-600">
          <nav className="flex gap-4" aria-label="Tabs">
            <button
              onClick={() => setActiveTab('ai')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'ai'
                  ? 'border-neon-cyan text-neon-cyan'
                  : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              <Bot className="h-4 w-4" />
              Local Agents
            </button>
            <button
              onClick={() => setActiveTab('remote')}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === 'remote'
                  ? 'border-neon-cyan text-neon-cyan'
                  : 'border-transparent text-gray-400 hover:text-white hover:border-gray-500'
              }`}
            >
              <Network className="h-4 w-4" />
              Remote Agents
            </button>
          </nav>
        </div>

        {/* Tab Content */}
        {activeTab === 'ai' ? (
          <>
            {/* Overview Stats */}
            {stats && (
              <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-4">
                <StatCard
                  icon={Bot}
                  label="Total Agents"
                  value={stats.total}
                  color="text-gray-400"
                />
                <StatCard
                  icon={Activity}
                  label="Running"
                  value={stats.running}
                  color="text-neon-cyan"
                  pulse={stats.running > 0}
                />
                <StatCard
                  icon={Clock}
                  label="Idle"
                  value={stats.idle}
                  color="text-gray-400"
                />
                <StatCard
                  icon={AlertTriangle}
                  label="Paused/Errors"
                  value={stats.paused + stats.error}
                  color={stats.error > 0 ? 'text-red-500' : 'text-yellow-500'}
                />
                <StatCard
                  icon={CheckCircle}
                  label="Total Runs"
                  value={stats.totalRuns}
                  color="text-green-500"
                />
                <StatCard
                  icon={Settings}
                  label="Total Cost"
                  value={`$${stats.totalCost.toFixed(2)}`}
                  color="text-neon-purple"
                />
              </div>
            )}

            {/* Performance Insights Panel */}
            <AgentInsightsPanel />

            {/* Agent Cards */}
            <div className="space-y-4">
              <h2 className="text-lg font-semibold text-white">Agents</h2>
              <div className="space-y-4">
                {agents?.map((agent) => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    isExpanded={expandedAgent === agent.slug}
                    onExpand={() => handleExpand(agent.slug)}
                    onConfigure={() => handleConfigure(agent)}
                  />
                ))}
              </div>
            </div>

            {/* Recent Activity */}
            {recentRuns && recentRuns.length > 0 && (
              <div className="space-y-4">
                <h2 className="text-lg font-semibold text-white">Recent Activity</h2>
                <RunHistoryPanel runs={recentRuns} showAgentName />
              </div>
            )}
          </>
        ) : (
          <RemoteAgentsTab />
        )}
      </div>

      {/* Configuration Modal */}
      {selectedAgent && (
        <AgentConfigModal
          agent={selectedAgent}
          isOpen={showConfig}
          onClose={() => {
            setShowConfig(false);
            setSelectedAgent(null);
          }}
        />
      )}
    </Layout>
  );
}

// =============================================================================
// Stat Card Component
// =============================================================================

interface StatCardProps {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string | number;
  color: string;
  pulse?: boolean;
}

function StatCard({ icon: Icon, label, value, color, pulse }: StatCardProps) {
  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
      <div className="flex items-center gap-2 mb-2">
        <Icon className={`h-4 w-4 ${color} ${pulse ? 'animate-pulse' : ''}`} />
        <span className="text-xs text-gray-500 uppercase tracking-wider">{label}</span>
      </div>
      <div className={`text-2xl font-bold ${color}`}>{value}</div>
    </div>
  );
}
