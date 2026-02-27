import React, { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { 
  Server, 
  Globe, 
  Ban, 
  Cpu, 
  Monitor,
  ChevronDown,
  ChevronRight,
  Info,
  Wifi,
  WifiOff
} from 'lucide-react';
import { remoteAgentsService, type RemoteAgent } from '@/services/remoteAgents';

// =============================================================================
// Types
// =============================================================================

type ExecutionMode = 'local' | 'all' | 'specific' | 'disabled';

interface DistributedExecutionConfigProps {
  /** 
   * Which agents can run this tool:
   * - null = local only (central server)
   * - [] = explicitly disabled everywhere
   * - ["*"] = all agents
   * - ["host1", "host2"] = specific agents
   */
  availableOnAgents: string[] | null;
  
  /**
   * Per-agent resource requirements.
   * Keys are agent hostnames, values are lists of local resource names.
   * e.g., { "workstation-01": ["gpu-0"], "minipc-01": ["storage-fast"] }
   */
  agentResourceMap: Record<string, string[]> | null;
  
  /** Callback when configuration changes */
  onChange: (
    availableOnAgents: string[] | null, 
    agentResourceMap: Record<string, string[]> | null
  ) => void;
  
  /** Help text to display */
  helpText?: string;
}

// =============================================================================
// Helper Components
// =============================================================================

const ModeCard: React.FC<{
  selected: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  title: string;
  description: string;
}> = ({ selected, onClick, icon, title, description }) => (
  <button
    type="button"
    onClick={onClick}
    className={`p-4 rounded-lg border text-left transition-all ${
      selected 
        ? 'border-neon-cyan bg-neon-cyan/10' 
        : 'border-gray-700 bg-gray-900/30 hover:border-gray-600'
    }`}
  >
    <div className="flex items-center gap-3 mb-2">
      <div className={`p-2 rounded-lg ${selected ? 'bg-neon-cyan/20 text-neon-cyan' : 'bg-gray-800 text-gray-400'}`}>
        {icon}
      </div>
      <span className={`font-medium ${selected ? 'text-white' : 'text-gray-300'}`}>{title}</span>
    </div>
    <p className="text-sm text-gray-500">{description}</p>
  </button>
);

const AgentCheckbox: React.FC<{
  agent: RemoteAgent;
  isConnected: boolean;
  isSelected: boolean;
  onToggle: () => void;
}> = ({ agent, isConnected, isSelected, onToggle }) => (
  <label 
    className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-all ${
      isSelected 
        ? 'border-neon-cyan/50 bg-neon-cyan/5' 
        : 'border-gray-700 bg-gray-900/30 hover:border-gray-600'
    }`}
  >
    <input
      type="checkbox"
      checked={isSelected}
      onChange={onToggle}
      className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-neon-cyan focus:ring-neon-cyan focus:ring-offset-0"
    />
    <div className="flex-1 min-w-0">
      <div className="flex items-center gap-2">
        <span className="font-medium text-white truncate">{agent.hostname}</span>
        {agent.display_name && agent.display_name !== agent.hostname && (
          <span className="text-sm text-gray-400 truncate">({agent.display_name})</span>
        )}
      </div>
      <div className="flex items-center gap-2 text-xs text-gray-500 mt-1">
        {isConnected ? (
          <span className="flex items-center gap-1 text-green-400">
            <Wifi className="h-3 w-3" /> Online
          </span>
        ) : (
          <span className="flex items-center gap-1">
            <WifiOff className="h-3 w-3" /> Offline
          </span>
        )}
        {agent.capabilities?.cpu && (
          <span className="flex items-center gap-1">
            <Cpu className="h-3 w-3" /> {agent.capabilities.cpu.cores_logical} cores
          </span>
        )}
        {agent.capabilities?.gpus && agent.capabilities.gpus.length > 0 && (
          <span className="flex items-center gap-1">
            <Monitor className="h-3 w-3" /> {agent.capabilities.gpus.length} GPU
          </span>
        )}
      </div>
    </div>
  </label>
);

// =============================================================================
// Resource Mapping Section
// =============================================================================

interface ResourceMappingProps {
  selectedAgents: string[];
  agents: RemoteAgent[];
  agentResourceMap: Record<string, string[]>;
  onChange: (newMap: Record<string, string[]>) => void;
}

const ResourceMapping: React.FC<ResourceMappingProps> = ({
  selectedAgents,
  agents,
  agentResourceMap,
  onChange
}) => {
  const [expandedAgent, setExpandedAgent] = useState<string | null>(null);
  
  // Build a map of hostname -> agent for easy lookup
  const agentMap = useMemo(() => 
    new Map(agents.map(a => [a.hostname, a])), 
    [agents]
  );
  
  const handleToggleResource = (hostname: string, resourceName: string) => {
    const currentResources = agentResourceMap[hostname] || [];
    const newResources = currentResources.includes(resourceName)
      ? currentResources.filter(r => r !== resourceName)
      : [...currentResources, resourceName];
    
    const newMap = { ...agentResourceMap };
    if (newResources.length > 0) {
      newMap[hostname] = newResources;
    } else {
      delete newMap[hostname];
    }
    onChange(newMap);
  };
  
  // Get available resources for an agent
  const getAgentResources = (agent: RemoteAgent): { name: string; type: string; info: string }[] => {
    const resources: { name: string; type: string; info: string }[] = [];
    
    // Add GPUs
    if (agent.capabilities?.gpus) {
      agent.capabilities.gpus.forEach((gpu, idx) => {
        resources.push({
          name: `gpu-${idx}`,
          type: 'GPU',
          info: `${gpu.name} (${Math.round(gpu.memory_total_mb / 1024)} GB)`
        });
      });
    }
    
    // Add storage volumes
    if (agent.capabilities?.storage) {
      agent.capabilities.storage.forEach((vol, idx) => {
        if (vol.path !== '/' && !vol.path.startsWith('/boot')) {
          resources.push({
            name: `storage-${idx}`,
            type: 'Storage',
            info: `${vol.path} (${Math.round(vol.free_bytes / (1024**3))} GB free)`
          });
        }
      });
    }
    
    return resources;
  };
  
  if (selectedAgents.length === 0) {
    return null;
  }
  
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm text-gray-400">
        <Info className="h-4 w-4" />
        <span>Configure resource requirements per agent (optional)</span>
      </div>
      
      {selectedAgents.map(hostname => {
        const agent = agentMap.get(hostname);
        if (!agent) return null;
        
        const resources = getAgentResources(agent);
        const selectedResources = agentResourceMap[hostname] || [];
        const isExpanded = expandedAgent === hostname;
        
        return (
          <div key={hostname} className="border border-gray-700 rounded-lg overflow-hidden">
            <button
              type="button"
              onClick={() => setExpandedAgent(isExpanded ? null : hostname)}
              className="w-full flex items-center justify-between p-3 bg-gray-900/50 hover:bg-gray-800/50 transition-colors"
            >
              <div className="flex items-center gap-2">
                <Server className="h-4 w-4 text-gray-400" />
                <span className="font-medium text-white">{hostname}</span>
                {selectedResources.length > 0 && (
                  <span className="text-xs bg-neon-purple/20 text-neon-purple px-2 py-0.5 rounded">
                    {selectedResources.length} resource{selectedResources.length !== 1 ? 's' : ''}
                  </span>
                )}
              </div>
              {isExpanded ? (
                <ChevronDown className="h-4 w-4 text-gray-400" />
              ) : (
                <ChevronRight className="h-4 w-4 text-gray-400" />
              )}
            </button>
            
            {isExpanded && (
              <div className="p-3 space-y-2 bg-gray-900/30">
                {resources.length === 0 ? (
                  <p className="text-sm text-gray-500">No resources detected on this agent</p>
                ) : (
                  resources.map(resource => (
                    <label
                      key={resource.name}
                      className="flex items-center gap-3 p-2 rounded hover:bg-gray-800/50 cursor-pointer"
                    >
                      <input
                        type="checkbox"
                        checked={selectedResources.includes(resource.name)}
                        onChange={() => handleToggleResource(hostname, resource.name)}
                        className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-neon-purple focus:ring-neon-purple focus:ring-offset-0"
                      />
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono bg-gray-800 px-2 py-0.5 rounded text-gray-300">
                            {resource.name}
                          </span>
                          <span className="text-xs text-gray-500">{resource.type}</span>
                        </div>
                        <p className="text-sm text-gray-400 mt-0.5">{resource.info}</p>
                      </div>
                    </label>
                  ))
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};

// =============================================================================
// Main Component
// =============================================================================

export const DistributedExecutionConfig: React.FC<DistributedExecutionConfigProps> = ({
  availableOnAgents,
  agentResourceMap,
  onChange,
  helpText
}) => {
  // Fetch available agents
  const { data: agents = [] } = useQuery({
    queryKey: ['remote-agents'],
    queryFn: () => remoteAgentsService.getAll(),
    staleTime: 30000, // 30 seconds
  });
  
  // Fetch connected agents for status
  const { data: connectedAgents = [] } = useQuery({
    queryKey: ['connected-agents'],
    queryFn: () => remoteAgentsService.getConnected(),
    staleTime: 10000, // 10 seconds
  });
  
  const connectedHostnames = useMemo(
    () => new Set(connectedAgents.map(a => a.hostname)),
    [connectedAgents]
  );
  
  // Determine current mode
  const currentMode: ExecutionMode = useMemo(() => {
    if (availableOnAgents === null) return 'local';
    if (availableOnAgents.length === 0) return 'disabled';
    if (availableOnAgents.includes('*')) return 'all';
    return 'specific';
  }, [availableOnAgents]);
  
  // Get selected agents (for 'specific' mode)
  const selectedAgents = useMemo(() => {
    if (currentMode !== 'specific') return [];
    return availableOnAgents || [];
  }, [currentMode, availableOnAgents]);
  
  // Handle mode change
  const handleModeChange = (mode: ExecutionMode) => {
    switch (mode) {
      case 'local':
        onChange(null, null);
        break;
      case 'disabled':
        onChange([], null);
        break;
      case 'all':
        onChange(['*'], agentResourceMap);
        break;
      case 'specific':
        // Keep existing selection or start empty
        onChange(selectedAgents.length > 0 ? selectedAgents : [], agentResourceMap);
        break;
    }
  };
  
  // Handle agent selection toggle
  const handleAgentToggle = (hostname: string) => {
    const newSelection = selectedAgents.includes(hostname)
      ? selectedAgents.filter(h => h !== hostname)
      : [...selectedAgents, hostname];
    
    // Also clean up resource map for deselected agents
    let newResourceMap = agentResourceMap ? { ...agentResourceMap } : {};
    if (!newSelection.includes(hostname) && newResourceMap[hostname]) {
      delete newResourceMap[hostname];
    }
    if (Object.keys(newResourceMap).length === 0) {
      newResourceMap = null as any;
    }
    
    onChange(newSelection, newResourceMap);
  };
  
  // Handle resource map change
  const handleResourceMapChange = (newMap: Record<string, string[]>) => {
    const cleanedMap = Object.keys(newMap).length > 0 ? newMap : null;
    onChange(availableOnAgents, cleanedMap);
  };
  
  return (
    <div className="space-y-6">
      {/* Mode Selection */}
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-3">
          Execution Location
        </label>
        <div className="grid grid-cols-2 gap-3">
          <ModeCard
            selected={currentMode === 'local'}
            onClick={() => handleModeChange('local')}
            icon={<Cpu className="h-5 w-5" />}
            title="Local Only"
            description="Runs on central server only"
          />
          <ModeCard
            selected={currentMode === 'all'}
            onClick={() => handleModeChange('all')}
            icon={<Globe className="h-5 w-5" />}
            title="All Worker Agents"
            description="Can run on any connected agent"
          />
          <ModeCard
            selected={currentMode === 'specific'}
            onClick={() => handleModeChange('specific')}
            icon={<Server className="h-5 w-5" />}
            title="Specific Worker Agents"
            description="Only runs on selected agents"
          />
          <ModeCard
            selected={currentMode === 'disabled'}
            onClick={() => handleModeChange('disabled')}
            icon={<Ban className="h-5 w-5" />}
            title="Disabled"
            description="Cannot be executed remotely"
          />
        </div>
      </div>
      
      {/* Agent Selection (for 'specific' mode) */}
      {currentMode === 'specific' && (
        <div>
          <label className="block text-sm font-medium text-gray-300 mb-3">
            Select Worker Agents
          </label>
          {agents.length === 0 ? (
            <div className="p-4 bg-gray-900/30 border border-gray-700 rounded-lg text-center">
              <Server className="h-8 w-8 text-gray-600 mx-auto mb-2" />
              <p className="text-gray-400">No remote agents registered</p>
              <p className="text-sm text-gray-500 mt-1">
                Register agents in Admin → Remote Agents
              </p>
            </div>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {agents
                .sort((a, b) => {
                  // Connected agents first
                  const aConn = connectedHostnames.has(a.hostname) ? 0 : 1;
                  const bConn = connectedHostnames.has(b.hostname) ? 0 : 1;
                  if (aConn !== bConn) return aConn - bConn;
                  return a.hostname.localeCompare(b.hostname);
                })
                .map(agent => (
                  <AgentCheckbox
                    key={agent.hostname}
                    agent={agent}
                    isConnected={connectedHostnames.has(agent.hostname)}
                    isSelected={selectedAgents.includes(agent.hostname)}
                    onToggle={() => handleAgentToggle(agent.hostname)}
                  />
                ))}
            </div>
          )}
        </div>
      )}
      
      {/* Resource Mapping (for 'specific' or 'all' modes with selected agents) */}
      {(currentMode === 'specific' && selectedAgents.length > 0) && (
        <ResourceMapping
          selectedAgents={selectedAgents}
          agents={agents}
          agentResourceMap={agentResourceMap || {}}
          onChange={handleResourceMapChange}
        />
      )}
      
      {/* Help Text */}
      {helpText && (
        <p className="text-xs text-gray-500 flex items-start gap-2">
          <Info className="h-4 w-4 flex-shrink-0 mt-0.5" />
          {helpText}
        </p>
      )}
    </div>
  );
};
