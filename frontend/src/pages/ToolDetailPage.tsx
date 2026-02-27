import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { toolsService } from '@/services/tools';
import { resourcesService } from '@/services/resources';
import { conversationsService } from '@/services/conversations';
import { ConversationPanel } from '@/components/conversations/ConversationPanel';
import { ToolWorkbench } from '@/components/tools/ToolWorkbench';
import { useAuthStore } from '@/stores/auth';
import { 
  Loader2,
  ArrowLeft, 
  Calendar, 
  User, 
  Tag, 
  Wrench, 
  CheckCircle, 
  XCircle, 
  Clock,
  Edit,
  Trash2,
  FileText,
  MessageSquare,
  AlertCircle,
  GitBranch,
  DollarSign,
  Activity,
  ExternalLink,
  Info,
  Flag,
  Hash,
  Package,
  Server,
  Cpu,
  Zap,
  HardDrive,
  Box,
  Database,
  Globe,
  Terminal,
  FlaskConical,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { useState, useRef, useEffect } from 'react';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';

const CATEGORY_LABELS: Record<string, string> = {
  api: 'API Integration',
  data_source: 'Data Source',
  automation: 'Automation',
  analysis: 'Analysis',
  communication: 'Communication',
};

const CATEGORY_ICONS: Record<string, string> = {
  api: '🔌',
  data_source: '📊',
  automation: '⚙️',
  analysis: '🔍',
  communication: '💬',
};

const STATUS_COLORS: Record<string, string> = {
  requested: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  under_review: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  changes_requested: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  approved: 'bg-green-500/20 text-green-400 border-green-500/30',
  rejected: 'bg-red-500/20 text-red-400 border-red-500/30',
  implementing: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  testing: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  blocked: 'bg-red-500/20 text-red-400 border-red-500/30',
  on_hold: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  implemented: 'bg-green-500/20 text-green-400 border-green-500/30',
  deprecated: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
  retired: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
};

const formatStatus = (status: string) => {
  return status
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
};

const INTEGRATION_COMPLEXITY_COLORS: Record<string, string> = {
  trivial: 'text-green-400 bg-green-500/10',
  simple: 'text-green-400 bg-green-500/10',
  low: 'text-green-400 bg-green-500/10',
  moderate: 'text-yellow-400 bg-yellow-500/10',
  medium: 'text-yellow-400 bg-yellow-500/10',
  complex: 'text-red-400 bg-red-500/10',
  high: 'text-red-400 bg-red-500/10',
};

const RESOURCE_TYPE_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  gpu: Zap,
  cpu: Cpu,
  ram: Database,
  storage: HardDrive,
  custom: Box,
};

const RESOURCE_TYPE_COLORS: Record<string, string> = {
  gpu: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  cpu: 'text-blue-400 bg-blue-500/10 border-blue-500/30',
  ram: 'text-purple-400 bg-purple-500/10 border-purple-500/30',
  storage: 'text-green-400 bg-green-500/10 border-green-500/30',
  custom: 'text-gray-400 bg-gray-500/10 border-gray-500/30',
};

const INTERFACE_TYPE_LABELS: Record<string, string> = {
  http: 'HTTP API',
  script: 'Script Execution',
  websocket: 'WebSocket',
  grpc: 'gRPC',
  mcp: 'MCP Server',
};

export function ToolDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const user = useAuthStore((state) => state.user);
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [activeTab, setActiveTab] = useState<'details' | 'discussion' | 'workbench'>('details');
  const isDeletingRef = useRef(false);

  const { data: tool, isLoading } = useQuery({
    queryKey: ['tool', id],
    queryFn: () => toolsService.getTool(id!),
    enabled: !!id && !isDeletingRef.current,
  });

  // Fetch unread count for this tool
  const { data: unreadCount = 0 } = useQuery({
    queryKey: ['unread-count', 'tool', id],
    queryFn: () => conversationsService.getToolUnreadCount(id!),
    enabled: !!id,
    refetchInterval: 10000, // Poll every 10 seconds
  });

  // Fetch all resources to show required resources by name
  const { data: allResources = [] } = useQuery({
    queryKey: ['resources'],
    queryFn: () => resourcesService.getAll(),
    enabled: !!tool?.resource_ids?.length,
  });

  // Get the resources that this tool requires
  const requiredResources = allResources.filter(
    (r) => tool?.resource_ids?.includes(r.id)
  );

  const approveMutation = useMutation({
    mutationFn: () => toolsService.approveTool(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tool', id] });
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => toolsService.rejectTool(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tool', id] });
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });

  const updateStatusMutation = useMutation({
    mutationFn: (status: string) => toolsService.updateToolStatus(id!, status as any),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tool', id] });
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => {
      // Disable the query immediately when delete starts
      isDeletingRef.current = true;
      return toolsService.deleteTool(id!);
    },
    onSuccess: () => {
      // Cancel any outgoing refetches for this tool
      queryClient.cancelQueries({ queryKey: ['tool', id] });
      // Remove from cache
      queryClient.removeQueries({ queryKey: ['tool', id] });
      // Update the list cache by filtering out the deleted tool
      queryClient.setQueryData(['tools'], (old: any) => 
        old ? old.filter((t: any) => t.id !== id) : []
      );
      // Navigate away
      navigate('/tools');
    },
  });

  // Invalidate unread count when switching to discussion tab
  useEffect(() => {
    if (activeTab === 'discussion') {
      const timer = setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['unread-count', 'tool', id] });
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

  if (!tool) {
    return (
      <Layout>
        <div className="text-center py-12">
          <p className="text-gray-400">Tool not found</p>
        </div>
      </Layout>
    );
  }

  const isAdmin = user?.role === 'admin';
  const canApproveReject = isAdmin && tool.status === 'requested';

  return (
    <Layout>
      <div className="max-w-5xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => navigate('/tools')}
            className="flex items-center gap-2 text-gray-400 hover:text-neon-cyan transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
            Back to Tools
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate(`/tools/${id}/edit`)}
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
            <div className="flex items-center gap-4">
              <span className="text-5xl">{CATEGORY_ICONS[tool.category]}</span>
              <div>
                <h1 className="text-4xl font-bold text-white mb-2">{tool.name}</h1>
                <span className="px-3 py-1 text-sm bg-gray-800/50 text-gray-300 rounded-full">
                  {CATEGORY_LABELS[tool.category]}
                </span>
              </div>
            </div>
            <span
              className={`px-4 py-2 rounded-lg text-sm font-medium border ${
                STATUS_COLORS[tool.status]
              }`}
            >
              {formatStatus(tool.status)}
            </span>
          </div>
          
          <p className="text-xl text-gray-300">{tool.description}</p>
          
          {/* Quick Info Badges */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="px-3 py-1 text-xs bg-gray-800/50 text-gray-400 rounded-full font-mono">
              {tool.slug}
            </span>
            {tool.priority && (
              <span className={`px-3 py-1 text-xs rounded-full flex items-center gap-1 ${
                tool.priority === 'critical' ? 'bg-red-500/20 text-red-400' :
                tool.priority === 'high' ? 'bg-orange-500/20 text-orange-400' :
                tool.priority === 'medium' ? 'bg-yellow-500/20 text-yellow-400' :
                'bg-gray-500/20 text-gray-400'
              }`}>
                <Flag className="h-3 w-3" />
                {tool.priority} priority
              </span>
            )}
            {tool.version && (
              <span className="px-3 py-1 text-xs bg-blue-500/20 text-blue-400 rounded-full flex items-center gap-1">
                <Package className="h-3 w-3" />
                v{tool.version}
              </span>
            )}
          </div>
          
          <div className="flex items-center gap-6 text-sm text-gray-400">
            <div className="flex items-center gap-2">
              <User className="h-4 w-4" />
              <span>Requested by {tool.requester_username}</span>
            </div>
            <div className="flex items-center gap-2">
              <User className="h-4 w-4" />
              <span>Assigned to {tool.assigned_to_username || 'Unassigned'}</span>
            </div>
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4" />
              <span>Created {formatDistanceToNow(new Date(tool.created_at), { addSuffix: true })}</span>
            </div>
            {tool.approved_at && (
              <div className="flex items-center gap-2">
                <CheckCircle className="h-4 w-4 text-green-400" />
                <span>Approved {formatDistanceToNow(new Date(tool.approved_at), { addSuffix: true })}</span>
              </div>
            )}
            {tool.implemented_at && (
              <div className="flex items-center gap-2">
                <Wrench className="h-4 w-4 text-cyan-400" />
                <span>Implemented {formatDistanceToNow(new Date(tool.implemented_at), { addSuffix: true })}</span>
              </div>
            )}
          </div>
          
          {/* Estimated Completion */}
          {tool.estimated_completion_date && (
            <div className="flex items-center gap-2 text-sm">
              <Clock className="h-4 w-4 text-yellow-400" />
              <span className="text-yellow-400">
                Estimated completion: {new Date(tool.estimated_completion_date).toLocaleDateString()}
              </span>
            </div>
          )}
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
            {(tool.status === 'implemented' || tool.status === 'deprecated') && (
              <button
                onClick={() => setActiveTab('workbench')}
                className={`px-6 py-3 font-medium transition-all relative ${
                  activeTab === 'workbench'
                    ? 'text-neon-cyan'
                    : 'text-gray-400 hover:text-gray-300'
                }`}
              >
                <div className="flex items-center gap-2">
                  <FlaskConical className="h-4 w-4" />
                  Workbench
                </div>
                {activeTab === 'workbench' && (
                  <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-gradient-to-r from-neon-cyan to-neon-blue" />
                )}
              </button>
            )}
          </div>
        </div>

        {/* Tab Content */}
        {activeTab === 'discussion' ? (
          <div className="h-[calc(100vh-500px)] min-h-[500px]">
            <ConversationPanel 
              toolId={tool.id} 
              toolName={tool.name}
              tool={tool}
            />
          </div>
        ) : activeTab === 'workbench' ? (
          <ToolWorkbench tool={tool} />
        ) : (
          <div className="space-y-6">
            {/* Key Metrics */}
            {(tool.cost_model || tool.integration_complexity || tool.dependencies || tool.cost_details || tool.external_documentation_url) && (
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {tool.cost_model && (
                  <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="p-2 bg-neon-cyan/20 rounded-lg">
                        <DollarSign className="h-5 w-5 text-neon-cyan" />
                      </div>
                      <span className="text-gray-400 text-sm">Cost Model</span>
                    </div>
                    <p className="text-lg font-semibold text-white capitalize">
                      {tool.cost_model.replace(/_/g, ' ')}
                    </p>
                    {tool.cost_details && (
                      <p className="text-sm text-gray-400 mt-2">
                        {typeof tool.cost_details === 'object' && tool.cost_details.details 
                          ? tool.cost_details.details 
                          : typeof tool.cost_details === 'string' 
                            ? tool.cost_details 
                            : JSON.stringify(tool.cost_details)}
                      </p>
                    )}
                  </div>
                )}

                {tool.integration_complexity && (
                  <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                    <div className="flex items-center gap-3 mb-2">
                      <div className={`p-2 rounded-lg ${INTEGRATION_COMPLEXITY_COLORS[tool.integration_complexity] || 'bg-gray-500/20'}`}>
                        <Activity className="h-5 w-5" />
                      </div>
                      <span className="text-gray-400 text-sm">Integration Complexity</span>
                    </div>
                    <p className="text-lg font-semibold text-white capitalize">
                      {tool.integration_complexity}
                    </p>
                  </div>
                )}

                {tool.external_documentation_url && (
                  <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="p-2 bg-blue-500/20 rounded-lg">
                        <ExternalLink className="h-5 w-5 text-blue-400" />
                      </div>
                      <span className="text-gray-400 text-sm">Documentation</span>
                    </div>
                    {/* SA3-M7: Only render link if URL has safe http(s) scheme */}
                    {/^https?:\/\//i.test(tool.external_documentation_url!) && (
                    <a 
                      href={tool.external_documentation_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-neon-cyan hover:text-neon-blue transition-colors flex items-center gap-1"
                    >
                      View Docs
                      <ExternalLink className="h-4 w-4" />
                    </a>
                    )}
                  </div>
                )}

                {tool.dependencies && tool.dependencies.length > 0 && (
                  <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                    <div className="flex items-center gap-3 mb-2">
                      <div className="p-2 bg-purple-500/20 rounded-lg">
                        <GitBranch className="h-5 w-5 text-purple-400" />
                      </div>
                      <span className="text-gray-400 text-sm">Dependencies</span>
                    </div>
                    <div className="space-y-1">
                      {tool.dependencies.map((dep: string, idx: number) => (
                        <p key={idx} className="text-sm text-white">{dep}</p>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Required Resources */}
            {requiredResources.length > 0 && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <Server className="h-5 w-5 text-neon-cyan" />
                  Required Resources
                </h2>
                <p className="text-sm text-gray-400 mb-4">Hardware resources this tool needs to function</p>
                <div className="flex flex-wrap gap-2">
                  {requiredResources.map((resource) => {
                    const Icon = RESOURCE_TYPE_ICONS[resource.resource_type] || Box;
                    const colorClasses = RESOURCE_TYPE_COLORS[resource.resource_type] || RESOURCE_TYPE_COLORS.custom;
                    return (
                      <span
                        key={resource.id}
                        className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border ${colorClasses}`}
                      >
                        <Icon className="h-4 w-4" />
                        <span className="text-sm font-medium">{resource.name}</span>
                        <span className="text-xs opacity-70 capitalize">({resource.resource_type})</span>
                      </span>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Execution Interface */}
            {tool.interface_type && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <Terminal className="h-5 w-5 text-cyan-400" />
                  Execution Interface
                </h2>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <span className="text-sm text-gray-400">Interface Type</span>
                    <p className="text-lg font-semibold text-white">
                      {INTERFACE_TYPE_LABELS[tool.interface_type] || tool.interface_type}
                    </p>
                  </div>
                  {tool.timeout_seconds && (
                    <div>
                      <span className="text-sm text-gray-400">Timeout</span>
                      <p className="text-lg font-semibold text-white">{tool.timeout_seconds}s</p>
                    </div>
                  )}
                </div>
                {tool.interface_config && Object.keys(tool.interface_config).length > 0 && (
                  <div className="mt-4">
                    <span className="text-sm text-gray-400 block mb-2">Configuration</span>
                    <div className="border border-gray-700 rounded-lg overflow-hidden">
                      <CodeMirror
                        value={JSON.stringify(tool.interface_config, null, 2)}
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

            {/* Distributed Execution */}
            {tool.available_on_agents && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <Globe className="h-5 w-5 text-blue-400" />
                  Distributed Execution
                </h2>
                <div className="space-y-4">
                  <div>
                    <span className="text-sm text-gray-400 block mb-2">Available On</span>
                    {tool.available_on_agents.length === 0 ? (
                      <p className="text-gray-500 italic">Disabled on all agents</p>
                    ) : tool.available_on_agents.includes('*') ? (
                      <span className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-green-500/10 text-green-400 border border-green-500/30">
                        <Globe className="h-4 w-4" />
                        All Agents
                      </span>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {tool.available_on_agents.map((agent: string) => (
                          <span
                            key={agent}
                            className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/30"
                          >
                            <Server className="h-4 w-4" />
                            {agent}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  {tool.agent_resource_map && Object.keys(tool.agent_resource_map).length > 0 && (
                    <div>
                      <span className="text-sm text-gray-400 block mb-2">Agent Resource Mapping</span>
                      <div className="border border-gray-700 rounded-lg overflow-hidden">
                        <CodeMirror
                          value={JSON.stringify(tool.agent_resource_map, null, 2)}
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
              </div>
            )}

            {/* Review Actions */}
            {canApproveReject && (
              <div className="bg-blue-500/10 border border-blue-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-blue-400 mb-3">Review Actions</h3>
                <div className="flex gap-3">
                  <button
                    onClick={() => approveMutation.mutate()}
                    disabled={approveMutation.isPending}
                    className="px-4 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors flex items-center gap-2"
                  >
                    <CheckCircle className="h-4 w-4" />
                    {approveMutation.isPending ? 'Approving...' : 'Approve'}
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('under_review')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-purple-500/20 text-purple-400 rounded-lg hover:bg-purple-500/30 transition-colors flex items-center gap-2"
                  >
                    <Clock className="h-4 w-4" />
                    Under Review
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('changes_requested')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-orange-500/20 text-orange-400 rounded-lg hover:bg-orange-500/30 transition-colors flex items-center gap-2"
                  >
                    <AlertCircle className="h-4 w-4" />
                    Request Changes
                  </button>
                  <button
                    onClick={() => rejectMutation.mutate()}
                    disabled={rejectMutation.isPending}
                    className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center gap-2"
                  >
                    <XCircle className="h-4 w-4" />
                    {rejectMutation.isPending ? 'Rejecting...' : 'Reject'}
                  </button>
                </div>
              </div>
            )}

            {/* Changes Requested - User can edit and resubmit */}
            {tool.status === 'changes_requested' && (
              <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-orange-400 mb-3">Changes Requested</h3>
                <p className="text-sm text-gray-400 mb-4">
                  The reviewer has requested changes. Edit the tool details and resubmit for review.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => navigate(`/tools/${id}/edit`)}
                    className="px-4 py-2 bg-cyan-500/20 text-cyan-400 rounded-lg hover:bg-cyan-500/30 transition-colors flex items-center gap-2"
                  >
                    <Edit className="h-4 w-4" />
                    Edit Tool
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('requested')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-blue-500/20 text-blue-400 rounded-lg hover:bg-blue-500/30 transition-colors flex items-center gap-2"
                  >
                    <Clock className="h-4 w-4" />
                    Resubmit for Review
                  </button>
                </div>
              </div>
            )}

            {/* Status Management for Approved Tools */}
            {tool.status === 'approved' && (
              <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-green-400 mb-3">Status Management</h3>
                <div className="flex gap-3">
                  <button
                    onClick={() => updateStatusMutation.mutate('implementing')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-cyan-500/20 text-cyan-400 rounded-lg hover:bg-cyan-500/30 transition-colors flex items-center gap-2"
                  >
                    <Wrench className="h-4 w-4" />
                    Start Implementing
                  </button>
                </div>
              </div>
            )}

            {/* Implementation Status Actions */}
            {(tool.status === 'implementing' || tool.status === 'testing') && (
              <div className="bg-cyan-500/10 border border-cyan-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-cyan-400 mb-3">Implementation Status</h3>
                <div className="flex gap-3">
                  {tool.status === 'implementing' && (
                    <button
                      onClick={() => updateStatusMutation.mutate('testing')}
                      disabled={updateStatusMutation.isPending}
                      className="px-4 py-2 bg-yellow-500/20 text-yellow-400 rounded-lg hover:bg-yellow-500/30 transition-colors flex items-center gap-2"
                    >
                      <Activity className="h-4 w-4" />
                      Move to Testing
                    </button>
                  )}
                  {tool.status === 'testing' && (
                    <button
                      onClick={() => updateStatusMutation.mutate('implemented')}
                      disabled={updateStatusMutation.isPending}
                      className="px-4 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors flex items-center gap-2"
                    >
                      <CheckCircle className="h-4 w-4" />
                      Mark as Implemented
                    </button>
                  )}
                  <button
                    onClick={() => updateStatusMutation.mutate('blocked')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center gap-2"
                  >
                    <XCircle className="h-4 w-4" />
                    Mark as Blocked
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('on_hold')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-gray-500/20 text-gray-400 rounded-lg hover:bg-gray-500/30 transition-colors flex items-center gap-2"
                  >
                    <Clock className="h-4 w-4" />
                    Put On Hold
                  </button>
                </div>
              </div>
            )}

            {/* Blocked/On Hold Status Actions */}
            {(tool.status === 'blocked' || tool.status === 'on_hold') && (
              <div className={`${tool.status === 'blocked' ? 'bg-red-500/10 border-red-500/30' : 'bg-gray-500/10 border-gray-500/30'} border rounded-lg p-6`}>
                <h3 className={`text-lg font-semibold ${tool.status === 'blocked' ? 'text-red-400' : 'text-gray-400'} mb-3`}>
                  {tool.status === 'blocked' ? 'Blocked Tool' : 'On Hold'}
                </h3>
                <p className="text-sm text-gray-400 mb-4">
                  {tool.status === 'blocked' 
                    ? 'This tool is blocked. Resolve the issue and resume development.'
                    : 'This tool is on hold. Resume when ready.'}
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => updateStatusMutation.mutate('implementing')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-cyan-500/20 text-cyan-400 rounded-lg hover:bg-cyan-500/30 transition-colors flex items-center gap-2"
                  >
                    <Wrench className="h-4 w-4" />
                    Resume Implementing
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('testing')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-yellow-500/20 text-yellow-400 rounded-lg hover:bg-yellow-500/30 transition-colors flex items-center gap-2"
                  >
                    <Activity className="h-4 w-4" />
                    Move to Testing
                  </button>
                </div>
              </div>
            )}

            {/* Implemented Tool Actions */}
            {tool.status === 'implemented' && isAdmin && (
              <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-green-400 mb-3">Lifecycle Management</h3>
                <p className="text-sm text-gray-400 mb-4">
                  This tool is live and available. Deprecate it to discourage new usage while keeping it available.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => updateStatusMutation.mutate('deprecated')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-orange-500/20 text-orange-400 rounded-lg hover:bg-orange-500/30 transition-colors flex items-center gap-2"
                  >
                    <AlertCircle className="h-4 w-4" />
                    Deprecate
                  </button>
                </div>
              </div>
            )}

            {/* Deprecated Tool Actions */}
            {tool.status === 'deprecated' && isAdmin && (
              <div className="bg-orange-500/10 border border-orange-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-orange-400 mb-3">Deprecated Tool</h3>
                <p className="text-sm text-gray-400 mb-4">
                  This tool is deprecated. You can retire it to make it unavailable, or re-activate it.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => updateStatusMutation.mutate('implemented')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors flex items-center gap-2"
                  >
                    <CheckCircle className="h-4 w-4" />
                    Re-activate
                  </button>
                  <button
                    onClick={() => updateStatusMutation.mutate('retired')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-gray-500/20 text-gray-400 rounded-lg hover:bg-gray-500/30 transition-colors flex items-center gap-2"
                  >
                    <XCircle className="h-4 w-4" />
                    Retire
                  </button>
                </div>
              </div>
            )}

            {/* Retired Tool Actions */}
            {tool.status === 'retired' && isAdmin && (
              <div className="bg-gray-500/10 border border-gray-500/30 rounded-lg p-6">
                <h3 className="text-lg font-semibold text-gray-400 mb-3">Retired Tool</h3>
                <p className="text-sm text-gray-400 mb-4">
                  This tool has been retired and is no longer available. You can re-activate it if needed.
                </p>
                <div className="flex gap-3">
                  <button
                    onClick={() => updateStatusMutation.mutate('implemented')}
                    disabled={updateStatusMutation.isPending}
                    className="px-4 py-2 bg-green-500/20 text-green-400 rounded-lg hover:bg-green-500/30 transition-colors flex items-center gap-2"
                  >
                    <CheckCircle className="h-4 w-4" />
                    Re-activate
                  </button>
                </div>
              </div>
            )}

            {/* Tags */}
            {tool.tags && tool.tags.length > 0 && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <Tag className="h-5 w-5 text-neon-cyan" />
                  Tags
                </h2>
                <div className="flex flex-wrap gap-2">
                  {tool.tags.map((tag: string, idx: number) => (
                    <span
                      key={idx}
                      className="px-3 py-1.5 text-sm bg-gray-800/50 text-gray-300 rounded-lg border border-gray-700"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Usage Instructions */}
            {tool.usage_instructions && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4">Usage Instructions</h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.usage_instructions} />
                </div>
              </div>
            )}

            {/* Example Code */}
            {tool.example_code && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4">Example Code</h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.example_code} />
                </div>
              </div>
            )}

            {/* Strengths */}
            {tool.strengths && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <CheckCircle className="h-5 w-5 text-green-400" />
                  Strengths
                </h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.strengths} />
                </div>
              </div>
            )}

            {/* Weaknesses */}
            {tool.weaknesses && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <AlertCircle className="h-5 w-5 text-orange-400" />
                  Weaknesses
                </h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.weaknesses} />
                </div>
              </div>
            )}

            {/* Best Use Cases */}
            {tool.best_use_cases && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <CheckCircle className="h-5 w-5 text-cyan-400" />
                  Best Use Cases
                </h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.best_use_cases} />
                </div>
              </div>
            )}

            {/* Implementation Notes */}
            {tool.implementation_notes && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4">Implementation Notes</h2>
                <div data-color-mode="dark" className="prose prose-invert max-w-none">
                  <SanitizedMarkdown source={tool.implementation_notes} />
                </div>
              </div>
            )}

            {/* Blockers */}
            {tool.blockers && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <XCircle className="h-5 w-5 text-red-400" />
                  Blockers
                </h2>
                <p className="text-gray-300 leading-relaxed whitespace-pre-wrap">{tool.blockers}</p>
              </div>
            )}

            {/* Dependencies */}
            {tool.dependencies && tool.dependencies.length > 0 && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4 flex items-center gap-2">
                  <GitBranch className="h-5 w-5 text-purple-400" />
                  Tool Dependencies
                </h2>
                <p className="text-sm text-gray-400 mb-4">Other tools that must be available for this tool to work.</p>
                <ul className="space-y-2">
                  {tool.dependencies.map((dep: string, idx: number) => (
                    <li key={idx} className="flex items-center gap-2 text-gray-300">
                      <span className="w-2 h-2 bg-purple-400 rounded-full"></span>
                      {dep}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Environment Variables */}
            {tool.required_environment_variables && Object.keys(tool.required_environment_variables).length > 0 && (
              <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6">
                <h2 className="text-2xl font-bold text-white mb-4">Environment Variables</h2>
                <div className="border border-gray-700 rounded-lg overflow-hidden">
                  <CodeMirror
                    value={JSON.stringify(tool.required_environment_variables, null, 2)}
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
            <h3 className="text-xl font-bold text-white mb-4">Delete Tool</h3>
            <p className="text-gray-300 mb-6">
              Are you sure you want to delete this tool? This action cannot be undone.
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
