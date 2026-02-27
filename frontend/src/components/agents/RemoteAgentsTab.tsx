/**
 * Remote Agents Tab Component
 * 
 * Manages distributed compute resources (machines running resource-agent).
 * Features:
 * - Connection status overview (total, connected, GPUs)
 * - Agent registration with API key generation
 * - Real-time connection monitoring
 * - Enable/disable/delete agent controls
 */
import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { 
  Server, 
  Plus, 
  Trash2, 
  Power, 
  PowerOff, 
  RefreshCw, 
  Key, 
  Copy, 
  Check, 
  Cpu, 
  MemoryStick, 
  Wifi,
  WifiOff,
  Clock,
  Monitor,
  ChevronDown,
  ChevronRight,
  AlertCircle
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { 
  remoteAgentsService, 
  type RemoteAgent, 
  type ConnectedAgent,
  type RemoteAgentCreate 
} from '@/services/remoteAgents';

// =============================================================================
// Status Badge Component
// =============================================================================

const StatusBadge: React.FC<{ status: string; isOnline?: boolean }> = ({ status, isOnline }) => {
  const getStatusStyles = () => {
    if (isOnline) {
      return 'bg-green-500/20 text-green-400 border-green-500/30';
    }
    switch (status) {
      case 'online':
        return 'bg-green-500/20 text-green-400 border-green-500/30';
      case 'offline':
        return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
      case 'busy':
        return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
      case 'maintenance':
        return 'bg-orange-500/20 text-orange-400 border-orange-500/30';
      case 'error':
        return 'bg-red-500/20 text-red-400 border-red-500/30';
      default:
        return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
    }
  };

  return (
    <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${getStatusStyles()}`}>
      {isOnline !== undefined ? (
        <>
          {isOnline ? <Wifi className="w-3 h-3 mr-1" /> : <WifiOff className="w-3 h-3 mr-1" />}
          {isOnline ? 'Connected' : 'Disconnected'}
        </>
      ) : (
        status
      )}
    </span>
  );
};

// =============================================================================
// Create Agent Modal
// =============================================================================

interface CreateAgentModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (apiKey: string, hostname: string) => void;
}

const CreateAgentModal: React.FC<CreateAgentModalProps> = ({ isOpen, onClose, onCreated }) => {
  const [formData, setFormData] = useState<RemoteAgentCreate>({
    hostname: '',
    display_name: '',
    description: '',
    tags: [],
  });
  const [tagInput, setTagInput] = useState('');
  const [error, setError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (data: RemoteAgentCreate) => remoteAgentsService.create(data),
    onSuccess: (response) => {
      onCreated(response.api_key, response.agent.hostname);
      setFormData({ hostname: '', display_name: '', description: '', tags: [] });
      setTagInput('');
    },
    onError: (err: any) => {
      setError(err.response?.data?.detail || 'Failed to create agent');
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!formData.hostname.trim()) {
      setError('Hostname is required');
      return;
    }
    createMutation.mutate(formData);
  };

  const addTag = () => {
    if (tagInput.trim() && !formData.tags?.includes(tagInput.trim())) {
      setFormData({ ...formData, tags: [...(formData.tags || []), tagInput.trim()] });
      setTagInput('');
    }
  };

  const removeTag = (tag: string) => {
    setFormData({ ...formData, tags: formData.tags?.filter(t => t !== tag) || [] });
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-navy-800 rounded-lg border border-navy-600 p-6 w-full max-w-md">
        <h2 className="text-xl font-bold text-white mb-4">Add Remote Agent</h2>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Hostname <span className="text-red-400">*</span>
            </label>
            <input
              type="text"
              value={formData.hostname}
              onChange={(e) => setFormData({ ...formData, hostname: e.target.value })}
              placeholder="e.g., my-pc"
              className="w-full px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-neon-cyan"
            />
            <p className="mt-1 text-xs text-gray-500">
              Must match the actual hostname of the machine
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Display Name
            </label>
            <input
              type="text"
              value={formData.display_name || ''}
              onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
              placeholder="e.g., Windows Workstation"
              className="w-full px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-neon-cyan"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Description
            </label>
            <textarea
              value={formData.description || ''}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              placeholder="e.g., GPU workstation with RTX 4090"
              rows={2}
              className="w-full px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-neon-cyan"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Tags
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={tagInput}
                onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTag())}
                placeholder="Add tag..."
                className="flex-1 px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:border-neon-cyan"
              />
              <button
                type="button"
                onClick={addTag}
                className="px-3 py-2 bg-navy-700 hover:bg-navy-600 rounded-lg text-gray-300"
              >
                Add
              </button>
            </div>
            {formData.tags && formData.tags.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {formData.tags.map(tag => (
                  <span
                    key={tag}
                    className="inline-flex items-center px-2 py-0.5 rounded bg-navy-700 text-xs text-gray-300"
                  >
                    {tag}
                    <button
                      type="button"
                      onClick={() => removeTag(tag)}
                      className="ml-1 text-gray-500 hover:text-red-400"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg text-red-400 text-sm">
              {error}
            </div>
          )}

          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-gray-400 hover:text-white"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 text-neon-cyan rounded-lg border border-neon-cyan/30 disabled:opacity-50"
            >
              {createMutation.isPending ? 'Creating...' : 'Create Agent'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

// =============================================================================
// API Key Modal (shown after creation)
// =============================================================================

interface ApiKeyModalProps {
  isOpen: boolean;
  apiKey: string;
  hostname: string;
  onClose: () => void;
}

const ApiKeyModal: React.FC<ApiKeyModalProps> = ({ isOpen, apiKey, hostname, onClose }) => {
  const [copied, setCopied] = useState(false);

  const copyToClipboard = () => {
    navigator.clipboard.writeText(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-navy-800 rounded-lg border border-navy-600 p-6 w-full max-w-lg">
        <div className="flex items-center gap-2 mb-4">
          <Key className="h-6 w-6 text-neon-yellow" />
          <h2 className="text-xl font-bold text-white">Agent Created Successfully!</h2>
        </div>

        <div className="space-y-4">
          <div className="p-4 bg-yellow-500/10 border border-yellow-500/30 rounded-lg">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-5 w-5 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div>
                <p className="text-yellow-400 font-medium">Save this API key now!</p>
                <p className="text-yellow-400/80 text-sm">
                  This key will only be shown once. Store it securely.
                </p>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              Hostname
            </label>
            <div className="px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-white font-mono">
              {hostname}
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">
              API Key
            </label>
            <div className="flex gap-2">
              <div className="flex-1 px-3 py-2 bg-navy-900 border border-navy-600 rounded-lg text-neon-cyan font-mono text-sm break-all">
                {apiKey}
              </div>
              <button
                onClick={copyToClipboard}
                className="px-3 py-2 bg-navy-700 hover:bg-navy-600 rounded-lg text-gray-300"
                title="Copy to clipboard"
              >
                {copied ? <Check className="h-5 w-5 text-green-400" /> : <Copy className="h-5 w-5" />}
              </button>
            </div>
          </div>

          <div className="p-4 bg-navy-900 rounded-lg">
            <p className="text-sm text-gray-400 mb-2">Configure the resource agent with:</p>
            <pre className="text-xs text-gray-300 overflow-x-auto">
{`broker:
  url: "ws://YOUR_SERVER:8000/api/v1/broker/agent"
  api_key: "${apiKey}"`}
            </pre>
          </div>
        </div>

        <div className="flex justify-end pt-4">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 text-neon-cyan rounded-lg border border-neon-cyan/30"
          >
            I've Saved the Key
          </button>
        </div>
      </div>
    </div>
  );
};

// =============================================================================
// Agent Card Component
// =============================================================================

interface AgentCardProps {
  agent: RemoteAgent;
  isConnected: boolean;
  connectedInfo?: ConnectedAgent;
  onDelete: () => void;
  onToggleEnabled: () => void;
  onRegenerateKey: () => void;
}

const RemoteAgentCard: React.FC<AgentCardProps> = ({ 
  agent, 
  isConnected, 
  connectedInfo,
  onDelete, 
  onToggleEnabled,
  onRegenerateKey 
}) => {
  const [expanded, setExpanded] = useState(false);

  const capabilities = agent.capabilities || {};
  const gpus = capabilities.gpus || [];
  const cpu = capabilities.cpu;
  const memory = capabilities.memory;

  return (
    <div className={`bg-navy-800 rounded-lg border ${isConnected ? 'border-green-500/30' : 'border-navy-600'} overflow-hidden`}>
      {/* Header */}
      <div className="p-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <div className={`p-2 rounded-lg ${isConnected ? 'bg-green-500/20' : 'bg-navy-700'}`}>
              <Server className={`h-6 w-6 ${isConnected ? 'text-green-400' : 'text-gray-400'}`} />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-lg font-semibold text-white">{agent.hostname}</h3>
                {agent.display_name && agent.display_name !== agent.hostname && (
                  <span className="text-sm text-gray-400">({agent.display_name})</span>
                )}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <StatusBadge status={agent.status} isOnline={isConnected} />
                {!agent.is_enabled && (
                  <span className="text-xs text-red-400 bg-red-500/10 px-2 py-0.5 rounded">
                    Disabled
                  </span>
                )}
              </div>
            </div>
          </div>

          <button
            onClick={() => setExpanded(!expanded)}
            className="p-1 text-gray-400 hover:text-white"
          >
            {expanded ? <ChevronDown className="h-5 w-5" /> : <ChevronRight className="h-5 w-5" />}
          </button>
        </div>

        {/* Quick Stats */}
        <div className="mt-4 grid grid-cols-3 gap-4">
          <div className="flex items-center gap-2">
            <Cpu className="h-4 w-4 text-gray-500" />
            <span className="text-sm text-gray-400">
              {cpu?.cores_logical ? `${cpu.cores_logical} cores` : 'Unknown'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <MemoryStick className="h-4 w-4 text-gray-500" />
            <span className="text-sm text-gray-400">
              {memory?.total_bytes != null ? `${(memory.total_bytes / (1024**3)).toFixed(1)} GB` : 'Unknown'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Monitor className="h-4 w-4 text-gray-500" />
            <span className="text-sm text-gray-400">
              {gpus.length > 0 ? `${gpus.length} GPU${gpus.length > 1 ? 's' : ''}` : 'No GPU'}
            </span>
          </div>
        </div>

        {/* GPU List (if any) */}
        {gpus.length > 0 && (
          <div className="mt-3 space-y-1">
            {gpus.map((gpu, idx) => (
              <div key={idx} className="flex items-center gap-2 text-sm">
                <div className="w-2 h-2 rounded-full bg-neon-purple" />
                <span className="text-gray-300">{gpu.name || 'Unknown GPU'}</span>
                <span className="text-gray-500">
                  {gpu.memory_total_mb != null ? `(${(gpu.memory_total_mb / 1024).toFixed(0)} GB)` : ''}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Last Seen */}
        {agent.last_seen_at && (
          <div className="mt-3 flex items-center gap-2 text-xs text-gray-500">
            <Clock className="h-3 w-3" />
            Last seen: {formatDistanceToNow(new Date(agent.last_seen_at), { addSuffix: true })}
          </div>
        )}
      </div>

      {/* Expanded Details */}
      {expanded && (
        <div className="border-t border-navy-600 p-4 space-y-4">
          {/* Description */}
          {agent.description && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Description</h4>
              <p className="text-sm text-gray-300">{agent.description}</p>
            </div>
          )}

          {/* Tags */}
          {agent.tags && agent.tags.length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Tags</h4>
              <div className="flex flex-wrap gap-1">
                {agent.tags.map(tag => (
                  <span key={tag} className="px-2 py-0.5 bg-navy-700 rounded text-xs text-gray-300">
                    {tag}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Connection Info */}
          {isConnected && connectedInfo && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Connection</h4>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-gray-500">Running Jobs:</span>{' '}
                  <span className="text-white">{connectedInfo.running_jobs} / {connectedInfo.max_concurrent_jobs}</span>
                </div>
                <div>
                  <span className="text-gray-500">Available:</span>{' '}
                  <span className={connectedInfo.is_available ? 'text-green-400' : 'text-yellow-400'}>
                    {connectedInfo.is_available ? 'Yes' : 'No'}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* IP Address */}
          {agent.ip_address && (
            <div>
              <h4 className="text-sm font-medium text-gray-400 mb-1">Network</h4>
              <p className="text-sm text-gray-300 font-mono">{agent.ip_address}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex flex-wrap gap-2 pt-2">
            <button
              onClick={onToggleEnabled}
              className={`flex items-center gap-1 px-3 py-1.5 rounded text-sm ${
                agent.is_enabled 
                  ? 'bg-orange-500/10 text-orange-400 hover:bg-orange-500/20' 
                  : 'bg-green-500/10 text-green-400 hover:bg-green-500/20'
              }`}
            >
              {agent.is_enabled ? <PowerOff className="h-4 w-4" /> : <Power className="h-4 w-4" />}
              {agent.is_enabled ? 'Disable' : 'Enable'}
            </button>
            <button
              onClick={onRegenerateKey}
              className="flex items-center gap-1 px-3 py-1.5 rounded text-sm bg-yellow-500/10 text-yellow-400 hover:bg-yellow-500/20"
            >
              <Key className="h-4 w-4" />
              Regenerate Key
            </button>
            <button
              onClick={onDelete}
              className="flex items-center gap-1 px-3 py-1.5 rounded text-sm bg-red-500/10 text-red-400 hover:bg-red-500/20"
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// =============================================================================
// Main Tab Component
// =============================================================================

interface RemoteAgentsTabProps {
  onAddClick?: () => void;
}

export const RemoteAgentsTab: React.FC<RemoteAgentsTabProps> = () => {
  const queryClient = useQueryClient();
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showApiKeyModal, setShowApiKeyModal] = useState(false);
  const [newApiKey, setNewApiKey] = useState('');
  const [newHostname, setNewHostname] = useState('');

  // Fetch all registered agents
  const { data: agents = [], isLoading: agentsLoading } = useQuery({
    queryKey: ['remote-agents'],
    queryFn: () => remoteAgentsService.getAll(),
    refetchInterval: 10000, // Refresh every 10 seconds
  });

  // Fetch currently connected agents
  const { data: connectedAgents = [] } = useQuery({
    queryKey: ['connected-agents'],
    queryFn: () => remoteAgentsService.getConnected(),
    refetchInterval: 5000, // Refresh every 5 seconds
  });

  // Create a set of connected hostnames for quick lookup
  const connectedHostnames = new Set(connectedAgents.map(a => a.hostname));
  const connectedAgentMap = new Map(connectedAgents.map(a => [a.hostname, a]));

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (hostname: string) => remoteAgentsService.delete(hostname),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote-agents'] });
    },
  });

  // Enable mutation
  const enableMutation = useMutation({
    mutationFn: (hostname: string) => remoteAgentsService.enable(hostname),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote-agents'] });
    },
  });

  // Disable mutation
  const disableMutation = useMutation({
    mutationFn: (hostname: string) => remoteAgentsService.disable(hostname),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['remote-agents'] });
    },
  });

  // Regenerate key mutation
  const regenerateKeyMutation = useMutation({
    mutationFn: (hostname: string) => remoteAgentsService.regenerateKey(hostname),
    onSuccess: (data, hostname) => {
      setNewApiKey(data.api_key);
      setNewHostname(hostname);
      setShowApiKeyModal(true);
    },
  });

  const handleDelete = (agent: RemoteAgent) => {
    if (confirm(`Are you sure you want to delete agent "${agent.hostname}"? This cannot be undone.`)) {
      deleteMutation.mutate(agent.hostname);
    }
  };

  const handleToggleEnabled = (agent: RemoteAgent) => {
    if (agent.is_enabled) {
      if (confirm(`Disable agent "${agent.hostname}"? It will not be able to connect.`)) {
        disableMutation.mutate(agent.hostname);
      }
    } else {
      enableMutation.mutate(agent.hostname);
    }
  };

  const handleRegenerateKey = (agent: RemoteAgent) => {
    if (confirm(`Regenerate API key for "${agent.hostname}"? The old key will stop working immediately.`)) {
      regenerateKeyMutation.mutate(agent.hostname);
    }
  };

  const handleAgentCreated = (apiKey: string, hostname: string) => {
    setShowCreateModal(false);
    setNewApiKey(apiKey);
    setNewHostname(hostname);
    setShowApiKeyModal(true);
    queryClient.invalidateQueries({ queryKey: ['remote-agents'] });
  };

  // Sort agents: connected first, then by hostname
  const sortedAgents = [...agents].sort((a, b) => {
    const aConnected = connectedHostnames.has(a.hostname);
    const bConnected = connectedHostnames.has(b.hostname);
    if (aConnected && !bConnected) return -1;
    if (!aConnected && bConnected) return 1;
    return a.hostname.localeCompare(b.hostname);
  });

  if (agentsLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <RefreshCw className="h-8 w-8 text-gray-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header with Add Button */}
      <div className="flex items-center justify-between">
        <p className="text-gray-400">
          Manage distributed compute resources across your machines
        </p>
        <button
          onClick={() => setShowCreateModal(true)}
          className="flex items-center gap-2 px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 text-neon-cyan rounded-lg border border-neon-cyan/30 transition-colors"
        >
          <Plus className="h-5 w-5" />
          Add Remote Agent
        </button>
      </div>

      {/* Connection Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-navy-800 rounded-lg border border-navy-600 p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-blue-500/20 rounded-lg">
              <Server className="h-6 w-6 text-blue-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-white">{agents.length}</p>
              <p className="text-sm text-gray-400">Total Agents</p>
            </div>
          </div>
        </div>
        <div className="bg-navy-800 rounded-lg border border-navy-600 p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-green-500/20 rounded-lg">
              <Wifi className="h-6 w-6 text-green-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-white">{connectedAgents.length}</p>
              <p className="text-sm text-gray-400">Connected</p>
            </div>
          </div>
        </div>
        <div className="bg-navy-800 rounded-lg border border-navy-600 p-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-500/20 rounded-lg">
              <Monitor className="h-6 w-6 text-purple-400" />
            </div>
            <div>
              <p className="text-2xl font-bold text-white">
                {connectedAgents.filter(a => a.has_gpu).length}
              </p>
              <p className="text-sm text-gray-400">GPUs Available</p>
            </div>
          </div>
        </div>
      </div>

      {/* Agent List */}
      {sortedAgents.length === 0 ? (
        <div className="bg-navy-800 rounded-lg border border-navy-600 p-8 text-center">
          <Server className="h-12 w-12 text-gray-500 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-white mb-2">No Remote Agents</h3>
          <p className="text-gray-400 mb-4">
            Register your first remote agent to start distributing workloads.
          </p>
          <button
            onClick={() => setShowCreateModal(true)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-neon-cyan/20 hover:bg-neon-cyan/30 text-neon-cyan rounded-lg border border-neon-cyan/30"
          >
            <Plus className="h-5 w-5" />
            Add Remote Agent
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {sortedAgents.map(agent => (
            <RemoteAgentCard
              key={agent.id}
              agent={agent}
              isConnected={connectedHostnames.has(agent.hostname)}
              connectedInfo={connectedAgentMap.get(agent.hostname)}
              onDelete={() => handleDelete(agent)}
              onToggleEnabled={() => handleToggleEnabled(agent)}
              onRegenerateKey={() => handleRegenerateKey(agent)}
            />
          ))}
        </div>
      )}

      {/* Create Modal */}
      <CreateAgentModal
        isOpen={showCreateModal}
        onClose={() => setShowCreateModal(false)}
        onCreated={handleAgentCreated}
      />

      {/* API Key Modal */}
      <ApiKeyModal
        isOpen={showApiKeyModal}
        apiKey={newApiKey}
        hostname={newHostname}
        onClose={() => setShowApiKeyModal(false)}
      />
    </div>
  );
};

export default RemoteAgentsTab;
