import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { campaignsService } from '@/services/campaigns';
import { proposalsService } from '@/services/proposals';
import { StreamProgressCard, CampaignMetricsPanel } from '@/components/campaigns';
import { InputRequestPanel } from '@/components/campaigns/InputRequestPanel';
import { ConversationPanel } from '@/components/conversations/ConversationPanel';
import { SuggestionPanel, RevisionHistory, PatternLibrary, LessonViewer } from '@/components/learning';
import { useCampaignProgress } from '@/hooks/useCampaignProgress';
import type { InputProvideRequest } from '@/types';
import { 
  Loader2, 
  ArrowLeft, 
  DollarSign, 
  TrendingUp, 
  Calendar,
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  Pause,
  Square,
  AlertTriangle,
  ChevronRight,
  Send,
  RefreshCw,
  FileText,
  Layers,
  ListTodo,
  MessageSquare,
  Brain,
  Wifi,
  WifiOff,
  Bitcoin,
} from 'lucide-react';
import { useState, useCallback } from 'react';

const isDev = import.meta.env.DEV;

const statusConfig: Record<string, { color: string; bgColor: string; icon: React.ElementType; label: string }> = {
  initializing: { 
    color: 'text-blue-400', 
    bgColor: 'bg-blue-500/20 border-blue-500/30',
    icon: Clock,
    label: 'Initializing'
  },
  waiting_for_inputs: { 
    color: 'text-yellow-400', 
    bgColor: 'bg-yellow-500/20 border-yellow-500/30',
    icon: AlertTriangle,
    label: 'Waiting for Inputs'
  },
  active: { 
    color: 'text-green-400', 
    bgColor: 'bg-green-500/20 border-green-500/30',
    icon: Play,
    label: 'Active'
  },
  paused: { 
    color: 'text-orange-400', 
    bgColor: 'bg-orange-500/20 border-orange-500/30',
    icon: Pause,
    label: 'Paused'
  },
  completed: { 
    color: 'text-neon-cyan', 
    bgColor: 'bg-neon-cyan/20 border-neon-cyan/30',
    icon: CheckCircle2,
    label: 'Completed'
  },
  terminated: { 
    color: 'text-red-400', 
    bgColor: 'bg-red-500/20 border-red-500/30',
    icon: XCircle,
    label: 'Terminated'
  },
  failed: { 
    color: 'text-red-400', 
    bgColor: 'bg-red-500/20 border-red-500/30',
    icon: XCircle,
    label: 'Failed'
  },
};

export function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  
  const [userInput, setUserInput] = useState('');
  const [terminateReason, setTerminateReason] = useState('');
  const [showTerminateModal, setShowTerminateModal] = useState(false);
  const [activeTab, setActiveTab] = useState<'overview' | 'streams' | 'inputs' | 'discussion' | 'intelligence'>('overview');
  const [intelligenceSubTab, setIntelligenceSubTab] = useState<'suggestions' | 'revisions' | 'patterns' | 'lessons'>('suggestions');

  // WebSocket callbacks for real-time events
  const handleStatusChange = useCallback((oldStatus: string, newStatus: string) => {
    if (isDev) console.log(`Campaign status changed: ${oldStatus} -> ${newStatus}`);
  }, []);

  const handleInputRequired = useCallback((inputKey: string, title: string) => {
    if (isDev) console.log(`Input required: ${inputKey} - ${title}`);
    // Could show a toast notification here
  }, []);

  // Real-time WebSocket connection for campaign progress
  const { 
    state: wsState, 
    isConnected: wsConnected, 
    isConnecting: wsConnecting,
    error: wsError,
  } = useCampaignProgress({
    campaignId: id || '',
    enabled: !!id,
    onStatusChange: handleStatusChange,
    onInputRequired: handleInputRequired,
  });

  // Use WebSocket state or fallback to polling when not connected
  // When WebSocket is connected, disable frequent polling (use longer interval as backup)
  const { data: campaign, isLoading } = useQuery({
    queryKey: ['campaigns', id],
    queryFn: () => campaignsService.getById(id!),
    enabled: !!id,
    // When WebSocket is connected, poll less frequently as a backup
    refetchInterval: wsConnected ? 60000 : 10000,
  });

  const { data: proposal } = useQuery({
    queryKey: ['proposals', campaign?.proposal_id],
    queryFn: () => proposalsService.getById(campaign!.proposal_id),
    enabled: !!campaign?.proposal_id,
  });

  // Fetch streams data
  const { data: streamsData, isLoading: isLoadingStreams } = useQuery({
    queryKey: ['campaigns', id, 'streams'],
    queryFn: () => campaignsService.getStreams(id!),
    enabled: !!id,
    // When WebSocket is connected, poll less frequently as a backup
    refetchInterval: wsConnected ? 30000 : 5000,
  });

  // Fetch tasks data for timeline visualization
  const { data: tasksData } = useQuery({
    queryKey: ['campaigns', id, 'tasks'],
    queryFn: () => campaignsService.getTasks(id!),
    enabled: !!id,
    refetchInterval: wsConnected ? 60000 : 15000,
  });

  const pauseMutation = useMutation({
    mutationFn: () => campaignsService.pause(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => campaignsService.resume(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
    },
  });

  const terminateMutation = useMutation({
    mutationFn: (reason: string) => campaignsService.terminate(id!, reason),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
      setShowTerminateModal(false);
    },
  });

  const submitInputMutation = useMutation({
    mutationFn: (message: string) => campaignsService.submitInput(id!, message),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
      setUserInput('');
    },
  });

  // Mutation for providing a single structured input
  const provideInputMutation = useMutation({
    mutationFn: (input: InputProvideRequest) => campaignsService.provideInput(id!, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
      queryClient.invalidateQueries({ queryKey: ['campaigns', id, 'streams'] });
    },
  });

  // Mutation for providing multiple inputs at once
  const provideInputsBulkMutation = useMutation({
    mutationFn: (inputs: InputProvideRequest[]) => campaignsService.provideInputsBulk(id!, { inputs }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
      queryClient.invalidateQueries({ queryKey: ['campaigns', id, 'streams'] });
    },
  });

  const executeStepMutation = useMutation({
    mutationFn: () => campaignsService.executeStep(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['campaigns', id] });
    },
  });

  if (isLoading) {
    return (
      <Layout>
        <div className="flex justify-center items-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
        </div>
      </Layout>
    );
  }

  if (!campaign) {
    return (
      <Layout>
        <div className="text-center py-12">
          <p className="text-gray-400">Campaign not found</p>
        </div>
      </Layout>
    );
  }

  // Prefer WebSocket state when connected, fallback to polled campaign data
  const displayStatus = wsConnected && wsState?.status ? wsState.status : campaign.status;
  const displayBudgetSpent = wsConnected && wsState ? wsState.budget_spent : campaign.budget_spent;
  const displayRevenueGenerated = wsConnected && wsState ? wsState.revenue_generated : campaign.revenue_generated;
  const displayTasksTotal = wsConnected && wsState ? wsState.tasks_total : campaign.tasks_total;
  const displayTasksCompleted = wsConnected && wsState ? wsState.tasks_completed : campaign.tasks_completed;

  const status = statusConfig[displayStatus] || statusConfig.initializing;
  const StatusIcon = status.icon;
  
  const budgetUsedPercent = campaign.budget_allocated > 0 
    ? (displayBudgetSpent / campaign.budget_allocated) * 100 
    : 0;
  
  const progressPercent = displayTasksTotal > 0 
    ? (displayTasksCompleted / displayTasksTotal) * 100 
    : 0;
  
  const profit = displayRevenueGenerated - displayBudgetSpent;
  const isProfitable = profit > 0;

  const canPause = displayStatus === 'active';
  const canResume = displayStatus === 'paused';
  const canTerminate = ['active', 'paused', 'waiting_for_inputs'].includes(displayStatus);
  const canSubmitInput = displayStatus === 'waiting_for_inputs';
  const canExecuteStep = displayStatus === 'active';

  // Parse requirements checklist
  const requirements = Array.isArray(campaign.requirements_checklist) 
    ? campaign.requirements_checklist 
    : Object.entries(campaign.requirements_checklist || {}).map(([key, value]) => ({
        item: key,
        ...(typeof value === 'object' ? value : { completed: value }),
      }));

  return (
    <Layout>
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => navigate('/campaigns')}
            className="flex items-center gap-2 text-gray-400 hover:text-neon-cyan transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
            Back to Campaigns
          </button>
          
          {/* Action Buttons */}
          <div className="flex items-center gap-3">
            {canExecuteStep && (
              <button
                onClick={() => executeStepMutation.mutate()}
                disabled={executeStepMutation.isPending}
                className="btn-secondary flex items-center gap-2"
              >
                {executeStepMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Run Step
              </button>
            )}
            
            {canPause && (
              <button
                onClick={() => pauseMutation.mutate()}
                disabled={pauseMutation.isPending}
                className="btn-secondary flex items-center gap-2"
              >
                {pauseMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Pause className="h-4 w-4" />
                )}
                Pause
              </button>
            )}
            
            {canResume && (
              <button
                onClick={() => resumeMutation.mutate()}
                disabled={resumeMutation.isPending}
                className="btn-primary flex items-center gap-2"
              >
                {resumeMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Play className="h-4 w-4" />
                )}
                Resume
              </button>
            )}
            
            {canTerminate && (
              <button
                onClick={() => setShowTerminateModal(true)}
                className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center gap-2"
              >
                <Square className="h-4 w-4" />
                Terminate
              </button>
            )}
          </div>
        </div>

        {/* Status and Title */}
        <div className="space-y-4">
          <div className="flex items-center gap-4">
            <span className={`inline-flex items-center gap-2 px-4 py-2 rounded-lg border ${status.bgColor}`}>
              <StatusIcon className={`h-5 w-5 ${status.color}`} />
              <span className={status.color}>{status.label}</span>
            </span>
            {campaign.current_phase && (
              <span className="text-gray-400">
                Phase: <span className="text-white">{campaign.current_phase}</span>
              </span>
            )}
            {/* WebSocket Connection Status Indicator */}
            <span 
              className={`inline-flex items-center gap-1.5 px-2 py-1 rounded text-xs ${
                wsConnected 
                  ? 'bg-green-500/10 text-green-400 border border-green-500/30' 
                  : wsConnecting 
                    ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/30'
                    : 'bg-gray-500/10 text-gray-400 border border-gray-500/30'
              }`}
              title={wsConnected ? 'Real-time updates active' : wsConnecting ? 'Connecting...' : wsError || 'Polling for updates'}
            >
              {wsConnected ? (
                <><Wifi className="h-3 w-3" /> Live</>
              ) : wsConnecting ? (
                <><Loader2 className="h-3 w-3 animate-spin" /> Connecting</>
              ) : (
                <><WifiOff className="h-3 w-3" /> Polling</>
              )}
            </span>
          </div>
          
          <h1 className="text-3xl font-bold text-white flex items-center gap-3">
            {campaign.proposal_title || `Campaign #${campaign.id.slice(0, 8)}`}
            {campaign.bitcoin_budget_sats != null && campaign.bitcoin_budget_sats > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-yellow-500/15 text-yellow-400 border border-yellow-500/30 rounded-full">
                <Bitcoin className="h-3 w-3" /> BTC
              </span>
            )}
          </h1>
          
          {proposal && (
            <Link 
              to={`/proposals/${proposal.id}`}
              className="inline-flex items-center gap-2 text-neon-cyan hover:underline"
            >
              <FileText className="h-4 w-4" />
              <span className="text-gray-400">Proposal:</span> {proposal.title}
              <ChevronRight className="h-4 w-4" />
            </Link>
          )}
        </div>

        {/* Tabbed Progress Section */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden">
          {/* Tab Headers */}
          <div className="flex border-b border-gray-800">
            <button
              onClick={() => setActiveTab('overview')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                activeTab === 'overview' 
                  ? 'text-neon-cyan border-b-2 border-neon-cyan bg-gray-800/30' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/20'
              }`}
            >
              <TrendingUp className="h-4 w-4" />
              Overview
            </button>
            <button
              onClick={() => setActiveTab('streams')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                activeTab === 'streams' 
                  ? 'text-neon-cyan border-b-2 border-neon-cyan bg-gray-800/30' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/20'
              }`}
            >
              <Layers className="h-4 w-4" />
              Streams
              {streamsData && (
                <span className="text-xs px-1.5 py-0.5 bg-gray-700 rounded">
                  {streamsData.ready_streams}/{streamsData.total_streams}
                </span>
              )}
            </button>
            <button
              onClick={() => setActiveTab('inputs')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                activeTab === 'inputs' 
                  ? 'text-neon-cyan border-b-2 border-neon-cyan bg-gray-800/30' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/20'
              }`}
            >
              <ListTodo className="h-4 w-4" />
              Inputs
              {streamsData && streamsData.blocking_inputs.length > 0 && (
                <span className="text-xs px-1.5 py-0.5 bg-red-500/30 text-red-400 rounded">
                  {streamsData.blocking_inputs.length}
                </span>
              )}
            </button>
            <button
              onClick={() => setActiveTab('discussion')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                activeTab === 'discussion' 
                  ? 'text-neon-cyan border-b-2 border-neon-cyan bg-gray-800/30' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/20'
              }`}
            >
              <MessageSquare className="h-4 w-4" />
              Discussion
            </button>
            <button
              onClick={() => setActiveTab('intelligence')}
              className={`flex-1 px-4 py-3 text-sm font-medium transition-colors flex items-center justify-center gap-2 ${
                activeTab === 'intelligence' 
                  ? 'text-neon-cyan border-b-2 border-neon-cyan bg-gray-800/30' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-800/20'
              }`}
            >
              <Brain className="h-4 w-4" />
              Intelligence
            </button>
          </div>

          {/* Tab Content */}
          <div className="p-6">
            {activeTab === 'overview' && (
              <div className="space-y-6">
                {/* Progress Bar */}
                <div>
                  <h2 className="text-lg font-semibold text-white mb-4">Progress</h2>
                  <div className="mb-4">
                    <div className="flex items-center justify-between text-sm text-gray-400 mb-2">
                      <span>Tasks Completed</span>
                      <span>{streamsData?.completed_tasks ?? displayTasksCompleted} / {streamsData?.total_tasks ?? displayTasksTotal}</span>
                    </div>
                    <div className="h-3 bg-gray-800 rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-gradient-to-r from-neon-cyan to-neon-blue transition-all"
                        style={{ width: `${streamsData?.overall_progress_pct ?? progressPercent}%` }}
                      />
                    </div>
                  </div>
                </div>
                
                {/* Metrics Charts */}
                <CampaignMetricsPanel
                  campaign={{
                    budget_allocated: campaign.budget_allocated,
                    budget_spent: displayBudgetSpent,
                    revenue_generated: displayRevenueGenerated,
                    start_date: campaign.start_date,
                  }}
                  streams={streamsData?.streams?.map(s => ({
                    id: s.id,
                    name: s.name,
                    status: s.status,
                    tasks_total: s.tasks_total,
                    tasks_completed: s.tasks_completed,
                    tasks_failed: s.tasks_failed,
                    progress_pct: s.progress_pct,
                  })) || []}
                  tasks={tasksData || []}
                />
                
                {/* Quick Stats */}
                {streamsData && (
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div className="p-3 bg-gray-800/50 rounded-lg">
                      <p className="text-xs text-gray-400">Total Streams</p>
                      <p className="text-xl font-bold text-white">{streamsData.total_streams}</p>
                    </div>
                    <div className="p-3 bg-gray-800/50 rounded-lg">
                      <p className="text-xs text-gray-400">Ready</p>
                      <p className="text-xl font-bold text-green-400">{streamsData.ready_streams}</p>
                    </div>
                    <div className="p-3 bg-gray-800/50 rounded-lg">
                      <p className="text-xs text-gray-400">Blocked</p>
                      <p className="text-xl font-bold text-yellow-400">{streamsData.blocked_streams}</p>
                    </div>
                    <div className="p-3 bg-gray-800/50 rounded-lg">
                      <p className="text-xs text-gray-400">Completed</p>
                      <p className="text-xl font-bold text-neon-cyan">{streamsData.completed_streams}</p>
                    </div>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'streams' && (
              <div>
                <h2 className="text-lg font-semibold text-white mb-4">Execution Streams</h2>
                {isLoadingStreams ? (
                  <div className="flex justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-neon-cyan" />
                  </div>
                ) : streamsData && streamsData.streams.length > 0 ? (
                  <div className="space-y-3">
                    {streamsData.streams.map(stream => (
                      <StreamProgressCard key={stream.id} stream={stream} />
                    ))}
                  </div>
                ) : (
                  <div className="text-center py-8 text-gray-400">
                    <Layers className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    <p>No execution streams generated yet</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'inputs' && (
              <div>
                {streamsData ? (
                  <InputRequestPanel
                    inputs={streamsData.blocking_inputs}
                    onSubmit={async (input) => {
                      await provideInputMutation.mutateAsync(input);
                    }}
                    onSubmitBulk={async (inputs) => {
                      await provideInputsBulkMutation.mutateAsync(inputs);
                    }}
                    isSubmitting={provideInputMutation.isPending || provideInputsBulkMutation.isPending}
                  />
                ) : isLoadingStreams ? (
                  <div className="flex justify-center py-8">
                    <Loader2 className="h-6 w-6 animate-spin text-neon-cyan" />
                  </div>
                ) : (
                  <div className="text-center py-8 text-gray-400">
                    <ListTodo className="h-8 w-8 mx-auto mb-2 opacity-50" />
                    <p>No inputs required</p>
                  </div>
                )}
              </div>
            )}

            {activeTab === 'discussion' && (
              <div className="h-[600px]">
                <ConversationPanel
                  campaignId={campaign.id}
                  campaignTitle={proposal?.title || 'Campaign'}
                  campaign={campaign}
                />
              </div>
            )}

            {activeTab === 'intelligence' && (
              <div className="space-y-6">
                {/* Intelligence Sub-tabs */}
                <div className="flex gap-2 flex-wrap">
                  <button
                    onClick={() => setIntelligenceSubTab('suggestions')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      intelligenceSubTab === 'suggestions'
                        ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30'
                        : 'bg-gray-800/50 text-gray-400 hover:text-white border border-transparent'
                    }`}
                  >
                    AI Suggestions
                  </button>
                  <button
                    onClick={() => setIntelligenceSubTab('revisions')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      intelligenceSubTab === 'revisions'
                        ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30'
                        : 'bg-gray-800/50 text-gray-400 hover:text-white border border-transparent'
                    }`}
                  >
                    Plan Evolution
                  </button>
                  <button
                    onClick={() => setIntelligenceSubTab('patterns')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      intelligenceSubTab === 'patterns'
                        ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30'
                        : 'bg-gray-800/50 text-gray-400 hover:text-white border border-transparent'
                    }`}
                  >
                    Patterns
                  </button>
                  <button
                    onClick={() => setIntelligenceSubTab('lessons')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                      intelligenceSubTab === 'lessons'
                        ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30'
                        : 'bg-gray-800/50 text-gray-400 hover:text-white border border-transparent'
                    }`}
                  >
                    Lessons
                  </button>
                </div>

                {/* Intelligence Sub-tab Content */}
                {intelligenceSubTab === 'suggestions' && (
                  <SuggestionPanel campaignId={campaign.id} />
                )}

                {intelligenceSubTab === 'revisions' && (
                  <RevisionHistory campaignId={campaign.id} />
                )}

                {intelligenceSubTab === 'patterns' && (
                  <PatternLibrary campaignId={campaign.id} />
                )}

                {intelligenceSubTab === 'lessons' && (
                  <LessonViewer campaignId={campaign.id} />
                )}
              </div>
            )}
          </div>
        </div>

        {/* Financial Metrics */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-neon-cyan/20 rounded-lg">
                <DollarSign className="h-5 w-5 text-neon-cyan" />
              </div>
              <span className="text-gray-400 text-sm">Budget Allocated</span>
            </div>
            <p className="text-2xl font-bold text-white">
              ${campaign.budget_allocated.toLocaleString()}
            </p>
          </div>

          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-orange-500/20 rounded-lg">
                <DollarSign className="h-5 w-5 text-orange-400" />
              </div>
              <span className="text-gray-400 text-sm">Budget Spent</span>
            </div>
            <p className="text-2xl font-bold text-white">
              ${displayBudgetSpent.toLocaleString()}
            </p>
            <div className="h-1.5 bg-gray-800 rounded-full mt-2 overflow-hidden">
              <div 
                className={`h-full transition-all ${budgetUsedPercent > 80 ? 'bg-red-500' : 'bg-orange-400'}`}
                style={{ width: `${Math.min(budgetUsedPercent, 100)}%` }}
              />
            </div>
          </div>

          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className="p-2 bg-green-500/20 rounded-lg">
                <TrendingUp className="h-5 w-5 text-green-400" />
              </div>
              <span className="text-gray-400 text-sm">Revenue Generated</span>
            </div>
            <p className="text-2xl font-bold text-green-400">
              ${displayRevenueGenerated.toLocaleString()}
            </p>
          </div>

          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-2">
              <div className={`p-2 rounded-lg ${isProfitable ? 'bg-green-500/20' : 'bg-red-500/20'}`}>
                <DollarSign className={`h-5 w-5 ${isProfitable ? 'text-green-400' : 'text-red-400'}`} />
              </div>
              <span className="text-gray-400 text-sm">Net Profit</span>
            </div>
            <p className={`text-2xl font-bold ${isProfitable ? 'text-green-400' : 'text-red-400'}`}>
              {isProfitable ? '+' : ''}${profit.toLocaleString()}
            </p>
          </div>
        </div>

        {/* Bitcoin Budget Metrics */}
        {campaign.bitcoin_budget_sats != null && campaign.bitcoin_budget_sats > 0 && (() => {
          const btcBudget = campaign.bitcoin_budget_sats;
          const btcSpent = campaign.bitcoin_spent_sats || 0;
          const btcReceived = campaign.bitcoin_received_sats || 0;
          const btcRemaining = btcBudget - btcSpent;
          const btcUsedPct = btcBudget > 0 ? (btcSpent / btcBudget * 100) : 0;
          return (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div className="bg-gray-900/50 border border-yellow-500/20 rounded-lg p-6">
                <div className="flex items-center gap-3 mb-2">
                  <div className="p-2 bg-yellow-500/20 rounded-lg">
                    <Bitcoin className="h-5 w-5 text-yellow-400" />
                  </div>
                  <span className="text-gray-400 text-sm">BTC Budget</span>
                </div>
                <p className="text-2xl font-bold text-yellow-400">
                  {btcBudget.toLocaleString()} <span className="text-sm font-normal text-gray-500">sats</span>
                </p>
                <p className="text-xs text-gray-500 mt-1">{btcRemaining.toLocaleString()} remaining</p>
              </div>

              <div className="bg-gray-900/50 border border-yellow-500/20 rounded-lg p-6">
                <div className="flex items-center gap-3 mb-2">
                  <div className="p-2 bg-orange-500/20 rounded-lg">
                    <Bitcoin className="h-5 w-5 text-orange-400" />
                  </div>
                  <span className="text-gray-400 text-sm">BTC Spent</span>
                </div>
                <p className="text-2xl font-bold text-white">
                  {btcSpent.toLocaleString()} <span className="text-sm font-normal text-gray-500">sats</span>
                </p>
                <div className="h-1.5 bg-gray-800 rounded-full mt-2 overflow-hidden">
                  <div
                    className={`h-full transition-all ${btcUsedPct > 80 ? 'bg-red-500' : 'bg-yellow-400'}`}
                    style={{ width: `${Math.min(btcUsedPct, 100)}%` }}
                  />
                </div>
              </div>

              <div className="bg-gray-900/50 border border-yellow-500/20 rounded-lg p-6">
                <div className="flex items-center gap-3 mb-2">
                  <div className="p-2 bg-green-500/20 rounded-lg">
                    <TrendingUp className="h-5 w-5 text-green-400" />
                  </div>
                  <span className="text-gray-400 text-sm">BTC Received</span>
                </div>
                <p className="text-2xl font-bold text-green-400">
                  {btcReceived.toLocaleString()} <span className="text-sm font-normal text-gray-500">sats</span>
                </p>
              </div>
            </div>
          );
        })()}

        {/* Requirements Checklist */}
        {requirements.length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-white mb-4">Requirements Checklist</h2>
            <ul className="space-y-3">
              {requirements.map((req: any, index: number) => (
                <li key={index} className="flex items-start gap-3">
                  {req.completed ? (
                    <CheckCircle2 className="h-5 w-5 text-green-400 flex-shrink-0 mt-0.5" />
                  ) : (
                    <div className="h-5 w-5 rounded-full border-2 border-gray-600 flex-shrink-0 mt-0.5" />
                  )}
                  <div className="flex-1">
                    <p className={req.completed ? 'text-gray-400 line-through' : 'text-white'}>
                      {req.item}
                    </p>
                    {req.type && (
                      <span className="text-xs text-gray-500">Type: {req.type}</span>
                    )}
                  </div>
                  {req.blocking && !req.completed && (
                    <span className="px-2 py-1 text-xs bg-red-500/20 text-red-400 rounded">
                      Blocking
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* User Input Section */}
        {canSubmitInput && (
          <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-yellow-400 mb-2 flex items-center gap-2">
              <AlertTriangle className="h-5 w-5" />
              Your Input Required
            </h2>
            <p className="text-gray-400 mb-4">
              This campaign is waiting for your input to continue. Please provide the requested information below.
            </p>
            <div className="flex gap-3">
              <input
                type="text"
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                placeholder="Enter your response..."
                className="flex-1 px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && userInput.trim()) {
                    submitInputMutation.mutate(userInput);
                  }
                }}
              />
              <button
                onClick={() => submitInputMutation.mutate(userInput)}
                disabled={!userInput.trim() || submitInputMutation.isPending}
                className="btn-primary flex items-center gap-2"
              >
                {submitInputMutation.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                Submit
              </button>
            </div>
          </div>
        )}

        {/* Success Metrics */}
        {campaign.success_metrics && Object.keys(campaign.success_metrics).length > 0 && (
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
            <h2 className="text-lg font-semibold text-white mb-4">Success Metrics</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {Object.entries(campaign.success_metrics).map(([key, value]) => {
                // Handle nested metric objects with target/current/percentage
                const isNestedMetric = typeof value === 'object' && value !== null && 'target' in value;
                
                if (isNestedMetric) {
                  const metric = value as { target: string; current: number; percentage: number };
                  return (
                    <div key={key} className="p-4 bg-gray-800/50 rounded-lg">
                      <p className="text-gray-400 text-sm capitalize mb-2">{key.replace(/_/g, ' ')}</p>
                      <p className="text-white font-medium text-sm">{metric.target}</p>
                      <div className="mt-2">
                        <div className="flex justify-between text-xs mb-1">
                          <span className="text-gray-500">Progress</span>
                          <span className="text-neon-cyan">{metric.percentage}%</span>
                        </div>
                        <div className="h-1.5 bg-gray-700 rounded-full overflow-hidden">
                          <div 
                            className="h-full bg-neon-cyan transition-all"
                            style={{ width: `${Math.min(metric.percentage, 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  );
                }
                
                // Fallback for simple key-value metrics
                return (
                  <div key={key} className="p-3 bg-gray-800/50 rounded-lg">
                    <p className="text-gray-400 text-sm capitalize">{key.replace(/_/g, ' ')}</p>
                    <p className="text-white font-medium">
                      {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Timestamps */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
          <h2 className="text-lg font-semibold text-white mb-4">Timeline</h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            <div>
              <p className="text-gray-400 flex items-center gap-1.5">
                <Calendar className="h-4 w-4" />
                Created
              </p>
              <p className="text-white">{new Date(campaign.created_at).toLocaleString()}</p>
            </div>
            {campaign.start_date && (
              <div>
                <p className="text-gray-400 flex items-center gap-1.5">
                  <Play className="h-4 w-4" />
                  Started
                </p>
                <p className="text-white">{new Date(campaign.start_date).toLocaleString()}</p>
              </div>
            )}
            {campaign.last_activity_at && (
              <div>
                <p className="text-gray-400 flex items-center gap-1.5">
                  <Clock className="h-4 w-4" />
                  Last Activity
                </p>
                <p className="text-white">{new Date(campaign.last_activity_at).toLocaleString()}</p>
              </div>
            )}
            {campaign.end_date && (
              <div>
                <p className="text-gray-400 flex items-center gap-1.5">
                  <CheckCircle2 className="h-4 w-4" />
                  Ended
                </p>
                <p className="text-white">{new Date(campaign.end_date).toLocaleString()}</p>
              </div>
            )}
          </div>
        </div>

        {/* Terminate Modal */}
        {showTerminateModal && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
            <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 max-w-md w-full mx-4">
              <h3 className="text-xl font-bold text-white mb-4">Terminate Campaign</h3>
              <p className="text-gray-400 mb-4">
                Are you sure you want to terminate this campaign? This action cannot be undone.
              </p>
              <input
                type="text"
                value={terminateReason}
                onChange={(e) => setTerminateReason(e.target.value)}
                placeholder="Reason for termination..."
                className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-red-500 focus:ring-1 focus:ring-red-500 mb-4"
              />
              <div className="flex justify-end gap-3">
                <button
                  onClick={() => setShowTerminateModal(false)}
                  className="btn-secondary"
                >
                  Cancel
                </button>
                <button
                  onClick={() => terminateMutation.mutate(terminateReason)}
                  disabled={!terminateReason.trim() || terminateMutation.isPending}
                  className="px-4 py-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors disabled:opacity-50 flex items-center gap-2"
                >
                  {terminateMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Square className="h-4 w-4" />
                  )}
                  Terminate
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
