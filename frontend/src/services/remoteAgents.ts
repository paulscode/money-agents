import apiClient from '@/lib/api-client';

// =============================================================================
// Types
// =============================================================================

export type RemoteAgentStatus = 'online' | 'offline' | 'busy' | 'maintenance' | 'error';

export interface RemoteAgent {
  id: string;
  hostname: string;
  display_name: string | null;
  name: string; // computed: display_name || hostname
  description: string | null;
  tags: string[];
  status: RemoteAgentStatus;
  max_concurrent_jobs: number;
  capabilities: {
    platform?: {
      system: string;
      release: string;
      version: string;
      machine: string;
    };
    cpu?: {
      cores_physical: number;
      cores_logical: number;
      model: string;
      architecture: string;
      frequency_mhz?: number;
    };
    memory?: {
      total_bytes: number;
      available_bytes: number;
      used_bytes: number;
      percent_used: number;
    };
    gpus?: Array<{
      index: number;
      name: string;
      memory_total_mb: number;
      memory_free_mb: number;
      memory_used_mb: number;
      driver_version?: string;
      cuda_version?: string;
      temperature_c?: number;
      utilization_percent?: number;
    }>;
    storage?: Array<{
      path: string;
      total_bytes: number;
      free_bytes: number;
      used_bytes: number;
      percent_used: number;
      filesystem?: string;
    }>;
    network?: {
      hostname: string;
      ip_address: string;
    };
    timestamp?: string;
  } | null;
  live_stats: {
    cpu_percent?: number;
    memory_percent?: number;
    gpu_stats?: Array<{
      utilization_percent: number;
      memory_used_mb: number;
    }>;
  } | null;
  last_seen_at: string | null;
  connected_at: string | null;
  ip_address: string | null;
  is_enabled: boolean;
  created_at: string;
}

export interface RemoteAgentCreate {
  hostname: string;
  display_name?: string;
  description?: string;
  tags?: string[];
}

export interface RemoteAgentCreateResponse {
  agent: RemoteAgent;
  api_key: string; // Only shown once!
}

export interface ConnectedAgent {
  agent_id: string;
  hostname: string;
  display_name: string | null;
  name: string;
  connected_at: string;
  last_heartbeat: string;
  running_jobs: number;
  max_concurrent_jobs: number;
  is_available: boolean;
  has_gpu: boolean;
  gpu_names: string[];
}

export interface RemoteResource {
  id: string;
  name: string;
  local_name: string;
  agent_hostname: string;
  resource_type: string;
  status: string;
  metadata: Record<string, any> | null;
}

// =============================================================================
// Service
// =============================================================================

export const remoteAgentsService = {
  /**
   * List all registered remote agents
   */
  async getAll(): Promise<RemoteAgent[]> {
    const response = await apiClient.get('/api/v1/broker/agents');
    return response.data;
  },

  /**
   * Get a specific agent by hostname
   */
  async getByHostname(hostname: string): Promise<RemoteAgent> {
    const response = await apiClient.get(`/api/v1/broker/agents/${hostname}`);
    return response.data;
  },

  /**
   * Get currently connected agents (live from memory)
   */
  async getConnected(): Promise<ConnectedAgent[]> {
    const response = await apiClient.get('/api/v1/broker/agents/connected');
    return response.data;
  },

  /**
   * Create a new remote agent registration
   * Returns the agent and API key (only shown once!)
   */
  async create(data: RemoteAgentCreate): Promise<RemoteAgentCreateResponse> {
    const response = await apiClient.post('/api/v1/broker/agents', data);
    return response.data;
  },

  /**
   * Delete a remote agent
   */
  async delete(hostname: string): Promise<void> {
    await apiClient.delete(`/api/v1/broker/agents/${hostname}`);
  },

  /**
   * Enable a remote agent
   */
  async enable(hostname: string): Promise<{ message: string }> {
    const response = await apiClient.post(`/api/v1/broker/agents/${hostname}/enable`);
    return response.data;
  },

  /**
   * Disable a remote agent
   */
  async disable(hostname: string): Promise<{ message: string }> {
    const response = await apiClient.post(`/api/v1/broker/agents/${hostname}/disable`);
    return response.data;
  },

  /**
   * Regenerate API key for an agent (returns new key, only shown once!)
   */
  async regenerateKey(hostname: string): Promise<{ api_key: string; message: string }> {
    const response = await apiClient.post(`/api/v1/broker/agents/${hostname}/regenerate-key`);
    return response.data;
  },

  /**
   * Get resources for a specific agent
   */
  async getResources(hostname: string): Promise<RemoteResource[]> {
    const response = await apiClient.get(`/api/v1/resources?agent_hostname=${hostname}`);
    return response.data;
  },
};
