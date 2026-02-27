import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, PlayCircle, Wrench, Power, Cpu, Server, HardDrive, RefreshCw, Zap, Clock, CheckCircle, XCircle, MemoryStick, Network } from 'lucide-react';
import { resourcesService } from '@/services/resources';
import type { Resource, ResourceStatus } from '@/types';
import { formatDistanceToNow } from 'date-fns';

export const ResourcesTab: React.FC = () => {
  const queryClient = useQueryClient();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [selectedResource, setSelectedResource] = useState<Resource | null>(null);
  const [showQueueModal, setShowQueueModal] = useState(false);
  const [showTestModal, setShowTestModal] = useState(false);
  const [showStorageModal, setShowStorageModal] = useState(false);

  // Fetch all resources
  const { data: resources = [], isLoading } = useQuery({
    queryKey: ['resources'],
    queryFn: () => resourcesService.getAll(),
    refetchInterval: 5000, // Auto-refresh every 5 seconds
  });

  // Detect system resources mutation
  const detectResourcesMutation = useMutation({
    mutationFn: () => resourcesService.detectResources(),
    onSuccess: (data) => {
      alert(`${data.message}`);
      queryClient.invalidateQueries({ queryKey: ['resources'] });
    },
    onError: (error: any) => {
      alert(`Failed to detect resources: ${error.message}`);
    },
  });

  // Update status mutation
  const updateStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: ResourceStatus }) =>
      resourcesService.updateStatus(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['resources'] });
    },
    onError: (error: any) => {
      alert(`Failed to update status: ${error.message}`);
    },
  });

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => resourcesService.delete(id),
    onSuccess: () => {
      alert('Resource deleted successfully');
      queryClient.invalidateQueries({ queryKey: ['resources'] });
    },
    onError: (error: any) => {
      alert(`Failed to delete resource: ${error.message}`);
    },
  });

  const handleStatusChange = (resource: Resource, newStatus: ResourceStatus) => {
    const statusActions: Record<ResourceStatus, string> = {
      available: 'enable',
      disabled: 'disable',
      maintenance: 'put in maintenance mode',
      in_use: 'mark as in use',
    };

    if (confirm(`Are you sure you want to ${statusActions[newStatus]} "${resource.name}"?`)) {
      updateStatusMutation.mutate({ id: resource.id, status: newStatus });
    }
  };

  const handleDelete = (resource: Resource) => {
    if (resource.is_system_resource) {
      alert('System resources (GPUs) cannot be deleted.');
      return;
    }

    if (confirm(`Are you sure you want to delete "${resource.name}"? This action cannot be undone.`)) {
      deleteMutation.mutate(resource.id);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <div className="text-gray-400">Loading resources...</div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <p className="text-gray-400">
            Manage system resources (CPU, RAM, GPU, Storage) and custom resources for tool execution
          </p>
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => detectResourcesMutation.mutate()}
            disabled={detectResourcesMutation.isPending}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${detectResourcesMutation.isPending ? 'animate-spin' : ''}`} />
            Detect Resources
          </button>
          <button
            onClick={() => setShowCreateForm(true)}
            className="px-4 py-2 bg-neon-cyan hover:bg-neon-cyan/80 text-gray-900 rounded-lg font-medium transition-colors flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Add Custom Resource
          </button>
        </div>
      </div>

      {/* Resources Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {resources.map((resource) => (
          <ResourceCard
            key={resource.id}
            resource={resource}
            onStatusChange={handleStatusChange}
            onDelete={handleDelete}
            onShowQueue={() => {
              setSelectedResource(resource);
              setShowQueueModal(true);
            }}
            onShowTest={() => {
              setSelectedResource(resource);
              setShowTestModal(true);
            }}
            onShowStorage={() => {
              setSelectedResource(resource);
              setShowStorageModal(true);
            }}
            isUpdating={updateStatusMutation.isPending}
            isDeleting={deleteMutation.isPending}
          />
        ))}
      </div>

      {/* Empty State */}
      {resources.length === 0 && (
        <div className="text-center py-12">
          <div className="w-16 h-16 bg-gray-800 rounded-full flex items-center justify-center mx-auto mb-4">
            <Server className="w-8 h-8 text-gray-600" />
          </div>
          <h3 className="text-xl font-semibold mb-2">No Resources Found</h3>
          <p className="text-gray-400 mb-6">
            Get started by detecting system resources or adding a custom resource
          </p>
          <div className="flex gap-3 justify-center">
            <button
              onClick={() => detectResourcesMutation.mutate()}
              disabled={detectResourcesMutation.isPending}
              className="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg font-medium transition-colors disabled:opacity-50"
            >
              Detect Resources
            </button>
            <button
              onClick={() => setShowCreateForm(true)}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg font-medium transition-colors"
            >
              Add Custom Resource
            </button>
          </div>
        </div>
      )}

      {/* Create Resource Modal */}
      {showCreateForm && (
        <CreateResourceModal
          onClose={() => setShowCreateForm(false)}
          onSuccess={() => {
            setShowCreateForm(false);
            queryClient.invalidateQueries({ queryKey: ['resources'] });
          }}
        />
      )}

      {/* Queue Modal */}
      {showQueueModal && selectedResource && (
        <QueueModal
          resource={selectedResource}
          onClose={() => {
            setShowQueueModal(false);
            setSelectedResource(null);
          }}
        />
      )}

      {/* Test Load Modal */}
      {showTestModal && selectedResource && (
        <TestLoadModal
          resource={selectedResource}
          onClose={() => {
            setShowTestModal(false);
            setSelectedResource(null);
          }}
          onSuccess={() => {
            queryClient.invalidateQueries({ queryKey: ['resources'] });
          }}
        />
      )}

      {/* Storage Info Modal */}
      {showStorageModal && selectedResource && selectedResource.category === 'capacity' && (
        <StorageModal
          resource={selectedResource}
          onClose={() => {
            setShowStorageModal(false);
            setSelectedResource(null);
          }}
          onRefresh={() => {
            queryClient.invalidateQueries({ queryKey: ['resources'] });
          }}
        />
      )}
    </div>
  );
};

// Create Resource Modal Component
interface CreateResourceModalProps {
  onClose: () => void;
  onSuccess: () => void;
}

const CreateResourceModal: React.FC<CreateResourceModalProps> = ({ onClose, onSuccess }) => {
  const [resourceCategory, setResourceCategory] = useState<'custom' | 'storage'>('custom');
  const [name, setName] = useState('');
  const [resourceType, setResourceType] = useState<'cpu' | 'custom'>('custom');
  const [storagePath, setStoragePath] = useState('');
  const [minFreeGb, setMinFreeGb] = useState(10);
  const [metadata, setMetadata] = useState('{}');

  const createMutation = useMutation({
    mutationFn: async () => {
      if (resourceCategory === 'storage') {
        return resourcesService.createStorage({
          name,
          path: storagePath,
          min_free_gb: minFreeGb,
        });
      } else {
        let parsedMetadata = {};
        try {
          parsedMetadata = JSON.parse(metadata);
        } catch (e) {
          throw new Error('Invalid JSON in metadata field');
        }
        return resourcesService.create({
          name,
          resource_type: resourceType,
          metadata: parsedMetadata,
        });
      }
    },
    onSuccess: () => {
      alert('Resource created successfully');
      onSuccess();
    },
    onError: (error: any) => {
      alert(`Failed to create resource: ${error.message}`);
    },
  });

  const isValid = resourceCategory === 'storage' 
    ? name.trim() && storagePath.trim()
    : name.trim();

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md w-full mx-4">
        <h2 className="text-xl font-bold mb-4">Create Custom Resource</h2>

        <div className="space-y-4">
          {/* Resource Category Selector */}
          <div>
            <label className="block text-sm font-medium mb-2">Resource Category</label>
            <div className="flex gap-2">
              <button
                onClick={() => setResourceCategory('custom')}
                className={`flex-1 px-4 py-2 rounded-lg font-medium transition-colors ${
                  resourceCategory === 'custom'
                    ? 'bg-blue-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                <Cpu className="w-4 h-4 inline-block mr-2" />
                Custom (Compute)
              </button>
              <button
                onClick={() => setResourceCategory('storage')}
                className={`flex-1 px-4 py-2 rounded-lg font-medium transition-colors ${
                  resourceCategory === 'storage'
                    ? 'bg-purple-600 text-white'
                    : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
                }`}
              >
                <HardDrive className="w-4 h-4 inline-block mr-2" />
                Storage
              </button>
            </div>
            <p className="text-xs text-gray-500 mt-1">
              {resourceCategory === 'custom' 
                ? 'Queue-based compute resource for running jobs' 
                : 'Capacity-based storage for large files'}
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Resource Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500"
              placeholder={resourceCategory === 'storage' ? 'e.g., Secondary Drive' : 'e.g., Custom API Service'}
            />
          </div>

          {resourceCategory === 'storage' ? (
            <>
              <div>
                <label className="block text-sm font-medium mb-2">Storage Path</label>
                <input
                  type="text"
                  value={storagePath}
                  onChange={(e) => setStoragePath(e.target.value)}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500 font-mono"
                  placeholder="/mnt/data or /home/user/storage"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Mount point or directory path for storage
                </p>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">
                  Minimum Free Space (GB)
                  <span className="text-gray-500 text-xs ml-2">Buffer to keep available</span>
                </label>
                <input
                  type="number"
                  value={minFreeGb}
                  onChange={(e) => setMinFreeGb(Number(e.target.value))}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500"
                  min={1}
                  step={1}
                />
              </div>
            </>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium mb-2">Resource Type</label>
                <select
                  value={resourceType}
                  onChange={(e) => setResourceType(e.target.value as 'cpu' | 'custom')}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500"
                >
                  <option value="custom">Custom</option>
                  <option value="cpu">CPU</option>
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2">
                  Metadata (JSON)
                  <span className="text-gray-500 text-xs ml-2">Optional</span>
                </label>
                <textarea
                  value={metadata}
                  onChange={(e) => setMetadata(e.target.value)}
                  className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-blue-500 font-mono text-sm"
                  rows={4}
                  placeholder='{"key": "value"}'
                />
              </div>
            </>
          )}
        </div>

        <div className="flex gap-3 mt-6">
          <button
            onClick={() => createMutation.mutate()}
            disabled={!isValid || createMutation.isPending}
            className="flex-1 px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            Create Resource
          </button>
          <button
            onClick={onClose}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium transition-colors"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
};

// Queue Modal Component
interface QueueModalProps {
  resource: Resource;
  onClose: () => void;
}

const QueueModal: React.FC<QueueModalProps> = ({ resource, onClose }) => {
  const queryClient = useQueryClient();
  const { data: jobs = [], isLoading } = useQuery({
    queryKey: ['resource-queue', resource.id],
    queryFn: () => resourcesService.getQueue(resource.id),
    refetchInterval: 3000, // Refresh every 3 seconds
  });

  const completeJobMutation = useMutation({
    mutationFn: ({ jobId, success }: { jobId: string; success: boolean }) => 
      resourcesService.completeTestJob(jobId, success),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['resource-queue', resource.id] });
      queryClient.invalidateQueries({ queryKey: ['resources'] });
    },
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-bold">Job Queue: {resource.name}</h2>
          <div className="text-sm text-gray-400">
            {jobs.filter(j => j.status === 'running').length} running • {jobs.filter(j => j.status === 'queued').length} queued
          </div>
        </div>

        {isLoading ? (
          <div className="text-center py-8 text-gray-400">Loading queue...</div>
        ) : jobs.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            <Clock className="w-12 h-12 mx-auto mb-2 text-gray-600" />
            <p>No jobs in queue</p>
            <p className="text-xs mt-1">Use the "Test" button to simulate load</p>
          </div>
        ) : (
          <div className="space-y-3">
            {jobs.map((job) => {
              const isTestJob = job.parameters?.test === true;
              return (
                <div key={job.id} className="bg-gray-700 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="font-medium">Job {job.id.slice(0, 8)}</span>
                      {isTestJob && (
                        <span className="px-2 py-0.5 bg-neon-cyan/20 text-neon-cyan text-xs rounded border border-neon-cyan/30">
                          Test
                        </span>
                      )}
                    </div>
                    <span className={`px-2 py-1 rounded text-xs ${
                      job.status === 'running' ? 'bg-blue-900/30 text-blue-400' :
                      job.status === 'queued' ? 'bg-yellow-900/30 text-yellow-400' :
                      job.status === 'completed' ? 'bg-green-900/30 text-green-400' :
                      job.status === 'failed' ? 'bg-red-900/30 text-red-400' :
                      'bg-gray-900/30 text-gray-400'
                    }`}>
                      {job.status}
                    </span>
                  </div>
                  <div className="text-sm text-gray-400">
                    <p>Tool: {job.tool_id.slice(0, 8)}</p>
                    {isTestJob && job.parameters?.description && (
                      <p className="text-neon-cyan/70">{job.parameters.description}</p>
                    )}
                    <p>Queued: {formatDistanceToNow(new Date(job.queued_at), { addSuffix: true })}</p>
                    {job.started_at && (
                      <p>Started: {formatDistanceToNow(new Date(job.started_at), { addSuffix: true })}</p>
                    )}
                    {job.completed_at && (
                      <p>Completed: {formatDistanceToNow(new Date(job.completed_at), { addSuffix: true })}</p>
                    )}
                  </div>
                  {isTestJob && (job.status === 'running' || job.status === 'queued') && (
                    <div className="flex gap-2 mt-3">
                      <button
                        onClick={() => completeJobMutation.mutate({ jobId: job.id, success: true })}
                        disabled={completeJobMutation.isPending}
                        className="px-3 py-1.5 bg-green-900/20 hover:bg-green-900/30 text-green-400 rounded text-xs font-medium transition-colors disabled:opacity-50 flex items-center gap-1"
                      >
                        <CheckCircle className="w-3 h-3" />
                        Complete
                      </button>
                      <button
                        onClick={() => completeJobMutation.mutate({ jobId: job.id, success: false })}
                        disabled={completeJobMutation.isPending}
                        className="px-3 py-1.5 bg-red-900/20 hover:bg-red-900/30 text-red-400 rounded text-xs font-medium transition-colors disabled:opacity-50 flex items-center gap-1"
                      >
                        <XCircle className="w-3 h-3" />
                        Fail
                      </button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="mt-6">
          <button
            onClick={onClose}
            className="w-full px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

// Test Load Modal Component
interface TestLoadModalProps {
  resource: Resource;
  onClose: () => void;
  onSuccess: () => void;
}

const TestLoadModal: React.FC<TestLoadModalProps> = ({ resource, onClose, onSuccess }) => {
  const [numJobs, setNumJobs] = useState(5);
  const [jobDuration, setJobDuration] = useState(10);

  const simulateMutation = useMutation({
    mutationFn: () => resourcesService.simulateLoad(resource.id, numJobs, jobDuration),
    onSuccess: (data) => {
      alert(data.message);
      onSuccess();
      onClose();
    },
    onError: (error: any) => {
      alert(`Failed to simulate load: ${error.message}`);
    },
  });

  const clearMutation = useMutation({
    mutationFn: () => resourcesService.clearTestJobs(resource.id),
    onSuccess: (data) => {
      alert(data.message);
      onSuccess();
    },
    onError: (error: any) => {
      alert(`Failed to clear test jobs: ${error.message}`);
    },
  });

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-md w-full mx-4">
        <div className="flex items-center gap-3 mb-4">
          <div className="p-2 bg-neon-cyan/20 rounded-lg">
            <Zap className="w-6 h-6 text-neon-cyan" />
          </div>
          <div>
            <h2 className="text-xl font-bold">Test Queue System</h2>
            <p className="text-sm text-gray-400">{resource.name}</p>
          </div>
        </div>

        <div className="bg-gray-900/50 border border-gray-700 rounded-lg p-4 mb-6">
          <p className="text-sm text-gray-300 mb-2">
            This will create dummy jobs to test the resource queue and observe how jobs are processed.
          </p>
          <p className="text-xs text-gray-400">
            💡 Jobs will be queued and you can monitor them in real-time. Complete them manually or let them timeout.
          </p>
        </div>

        <div className="space-y-4 mb-6">
          <div>
            <label className="block text-sm font-medium mb-2">Number of Jobs</label>
            <input
              type="number"
              min="1"
              max="50"
              value={numJobs}
              onChange={(e) => setNumJobs(parseInt(e.target.value) || 1)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-neon-cyan"
            />
            <p className="text-xs text-gray-500 mt-1">Max: 50 jobs</p>
          </div>

          <div>
            <label className="block text-sm font-medium mb-2">Job Duration (seconds)</label>
            <input
              type="number"
              min="1"
              max="300"
              value={jobDuration}
              onChange={(e) => setJobDuration(parseInt(e.target.value) || 1)}
              className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:border-neon-cyan"
            />
            <p className="text-xs text-gray-500 mt-1">Max: 300 seconds (5 minutes)</p>
          </div>
        </div>

        <div className="flex gap-3">
          <button
            onClick={() => simulateMutation.mutate()}
            disabled={simulateMutation.isPending}
            className="flex-1 px-4 py-2 bg-neon-cyan hover:bg-neon-cyan/80 text-gray-900 rounded-lg font-medium transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            <Zap className="w-4 h-4" />
            Simulate Load
          </button>
          <button
            onClick={() => clearMutation.mutate()}
            disabled={clearMutation.isPending}
            className="px-4 py-2 bg-red-900/20 hover:bg-red-900/30 text-red-400 rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            Clear Tests
          </button>
        </div>

        <button
          onClick={onClose}
          className="w-full mt-3 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
};

// ResourceCard Component
interface ResourceCardProps {
  resource: Resource;
  onStatusChange: (resource: Resource, status: ResourceStatus) => void;
  onDelete: (resource: Resource) => void;
  onShowQueue: () => void;
  onShowTest: () => void;
  onShowStorage: () => void;
  isUpdating: boolean;
  isDeleting: boolean;
}

const ResourceCard: React.FC<ResourceCardProps> = ({
  resource,
  onStatusChange,
  onDelete,
  onShowQueue,
  onShowTest,
  onShowStorage,
  isUpdating,
  isDeleting,
}) => {
  const isStorage = resource.category === 'capacity';
  const isRemote = !!resource.agent_hostname;
  
  // For remote resources, strip the hostname prefix from display name
  const displayName = isRemote && resource.name.includes(': ')
    ? resource.name.split(': ').slice(1).join(': ')
    : resource.name;

  const getResourceIcon = (type: string) => {
    switch (type) {
      case 'gpu':
        return <Cpu className="w-5 h-5" />;
      case 'cpu':
        return <Server className="w-5 h-5" />;
      case 'ram':
        return <MemoryStick className="w-5 h-5" />;
      case 'storage':
        return <HardDrive className="w-5 h-5" />;
      default:
        return <HardDrive className="w-5 h-5" />;
    }
  };

  const getStatusColor = (status: ResourceStatus) => {
    switch (status) {
      case 'available':
        return 'bg-green-900/20 text-green-400 border-green-700';
      case 'in_use':
        return 'bg-blue-900/20 text-blue-400 border-blue-700';
      case 'maintenance':
        return 'bg-yellow-900/20 text-yellow-400 border-yellow-700';
      case 'disabled':
        return 'bg-gray-700/20 text-gray-400 border-gray-600';
      default:
        return 'bg-gray-700/20 text-gray-400 border-gray-600';
    }
  };

  const getStatusActions = (resource: Resource): Array<{ label: string; status: ResourceStatus; icon: React.ReactNode }> => {
    const actions = [];
    
    if (resource.status === 'disabled') {
      actions.push({ label: 'Enable', status: 'available' as ResourceStatus, icon: <PlayCircle className="w-4 h-4" /> });
    } else if (resource.status === 'maintenance') {
      actions.push({ label: 'Resume', status: 'available' as ResourceStatus, icon: <PlayCircle className="w-4 h-4" /> });
    }
    
    if (resource.status !== 'disabled') {
      actions.push({ label: 'Disable', status: 'disabled' as ResourceStatus, icon: <Power className="w-4 h-4" /> });
    }
    
    if (resource.status !== 'maintenance') {
      actions.push({ label: 'Maintenance', status: 'maintenance' as ResourceStatus, icon: <Wrench className="w-4 h-4" /> });
    }

    return actions;
  };

  const getQueueHealth = (queued: number): { color: string; label: string; bgColor: string } => {
    if (queued === 0) return { color: 'text-green-400', label: 'Healthy', bgColor: 'bg-green-900/20 border-green-700' };
    if (queued <= 3) return { color: 'text-green-400', label: 'Light Load', bgColor: 'bg-green-900/20 border-green-700' };
    if (queued <= 10) return { color: 'text-yellow-400', label: 'Moderate', bgColor: 'bg-yellow-900/20 border-yellow-700' };
    return { color: 'text-red-400', label: 'Heavy Load', bgColor: 'bg-red-900/20 border-red-700' };
  };

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  };

  const getStorageHealth = (): { color: string; label: string; bgColor: string } => {
    if (!resource.total_bytes || !resource.used_bytes) {
      return { color: 'text-gray-400', label: 'Unknown', bgColor: 'bg-gray-700/20 border-gray-600' };
    }
    const usedPercent = (resource.used_bytes / resource.total_bytes) * 100;
    if (usedPercent < 70) return { color: 'text-green-400', label: 'Healthy', bgColor: 'bg-green-900/20 border-green-700' };
    if (usedPercent < 85) return { color: 'text-yellow-400', label: 'Moderate', bgColor: 'bg-yellow-900/20 border-yellow-700' };
    return { color: 'text-red-400', label: 'Low Space', bgColor: 'bg-red-900/20 border-red-700' };
  };

  return (
    <div className={`rounded-lg p-6 transition-colors ${
      isRemote 
        ? 'bg-gradient-to-br from-gray-800 to-cyan-950/30 border-2 border-neon-cyan/40 hover:border-neon-cyan/60' 
        : 'bg-gray-800 border border-gray-700 hover:border-gray-600'
    }`}>
      {/* Remote Agent Banner */}
      {isRemote && (
        <div className="flex items-center gap-2 mb-4 pb-3 border-b border-neon-cyan/20">
          <Network className="w-4 h-4 text-neon-cyan" />
          <span className="text-neon-cyan font-medium">{resource.agent_hostname}</span>
          <span className="text-gray-500 text-sm">• Remote Agent</span>
        </div>
      )}
      
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${
            isRemote ? 'bg-neon-cyan/20' : isStorage ? 'bg-purple-900/30' : 'bg-gray-700'
          }`}>
            {getResourceIcon(resource.resource_type)}
          </div>
          <div>
            <h3 className="font-semibold text-lg">{displayName}</h3>
            <p className="text-sm text-gray-400 capitalize">
              {resource.resource_type}
              {isStorage && <span className="text-purple-400 ml-2">• Capacity</span>}
            </p>
          </div>
        </div>
        <div className="flex flex-col items-end gap-1">
          {resource.is_system_resource && !isRemote && (
            <span className="px-2 py-1 bg-purple-900/30 text-purple-400 text-xs rounded border border-purple-700">
              System
            </span>
          )}
        </div>
      </div>

      {/* Status */}
      <div className="mb-4 flex items-center gap-3">
        <span className={`inline-block px-3 py-1 rounded-full text-sm font-medium border ${getStatusColor(resource.status)}`}>
          {resource.status.replace('_', ' ')}
        </span>
        {resource.status === 'available' && !isStorage && (
          <span className={`inline-block px-2 py-1 rounded text-xs font-medium border ${getQueueHealth(resource.jobs_queued).bgColor} ${getQueueHealth(resource.jobs_queued).color}`}>
            {getQueueHealth(resource.jobs_queued).label}
          </span>
        )}
        {resource.status === 'available' && isStorage && (
          <span className={`inline-block px-2 py-1 rounded text-xs font-medium border ${getStorageHealth().bgColor} ${getStorageHealth().color}`}>
            {getStorageHealth().label}
          </span>
        )}
      </div>

      {/* Storage Visualization */}
      {isStorage && resource.total_bytes && resource.total_bytes > 0 && (
        <div className="mb-4">
          <div className="flex items-center justify-between text-xs text-gray-400 mb-2">
            <span>Storage Usage</span>
            <span>{formatBytes(resource.used_bytes || 0)} / {formatBytes(resource.total_bytes)}</span>
          </div>
          <div className="h-3 bg-gray-700 rounded-full overflow-hidden flex">
            <div 
              className="bg-purple-500 transition-all duration-300"
              style={{ width: `${((resource.used_bytes || 0) / resource.total_bytes) * 100}%` }}
              title={`${formatBytes(resource.used_bytes || 0)} used`}
            />
            {resource.reserved_bytes && resource.reserved_bytes > 0 && (
              <div 
                className="bg-yellow-500 transition-all duration-300"
                style={{ width: `${(resource.reserved_bytes / resource.total_bytes) * 100}%` }}
                title={`${formatBytes(resource.reserved_bytes)} reserved`}
              />
            )}
          </div>
          <div className="flex items-center justify-between text-xs mt-1">
            <span className="text-purple-400">● {formatBytes(resource.used_bytes || 0)} used</span>
            <span className="text-green-400">● {formatBytes(resource.available_bytes || 0)} available</span>
          </div>
        </div>
      )}

      {/* Queue Visualization (compute resources) */}
      {!isStorage && (resource.jobs_running > 0 || resource.jobs_queued > 0) && (
        <div className="mb-4">
          <div className="flex items-center justify-between text-xs text-gray-400 mb-2">
            <span>Queue Status</span>
            <span>{resource.jobs_running + resource.jobs_queued} total</span>
          </div>
          <div className="h-3 bg-gray-700 rounded-full overflow-hidden flex">
            {resource.jobs_running > 0 && (
              <div 
                className="bg-blue-500 transition-all duration-300"
                style={{ width: `${(resource.jobs_running / (resource.jobs_running + resource.jobs_queued)) * 100}%` }}
                title={`${resource.jobs_running} running`}
              />
            )}
            {resource.jobs_queued > 0 && (
              <div 
                className="bg-yellow-500 transition-all duration-300"
                style={{ width: `${(resource.jobs_queued / (resource.jobs_running + resource.jobs_queued)) * 100}%` }}
                title={`${resource.jobs_queued} queued`}
              />
            )}
          </div>
          <div className="flex items-center justify-between text-xs mt-1">
            <span className="text-blue-400">● {resource.jobs_running} running</span>
            <span className="text-yellow-400">● {resource.jobs_queued} queued</span>
          </div>
        </div>
      )}

      {/* Stats Grid */}
      {isStorage ? (
        <div className="grid grid-cols-2 gap-4 mb-4 p-3 bg-gray-750 rounded-lg">
          <div>
            <p className="text-xs text-gray-400 mb-1">Total</p>
            <p className="text-xl font-bold text-purple-400">{formatBytes(resource.total_bytes || 0)}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 mb-1">Available</p>
            <p className="text-xl font-bold text-green-400">{formatBytes(resource.available_bytes || 0)}</p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 mb-4 p-3 bg-gray-750 rounded-lg">
          <div>
            <p className="text-xs text-gray-400 mb-1">Queued</p>
            <p className="text-xl font-bold text-yellow-400">{resource.jobs_queued}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400 mb-1">Running</p>
            <p className="text-xl font-bold text-blue-400">{resource.jobs_running}</p>
          </div>
        </div>
      )}

      {/* Metadata */}
      {resource.metadata && Object.keys(resource.metadata).length > 0 && (
        <div className="mb-4 text-sm space-y-1">
          {resource.metadata.memory_mb && (
            <p className="text-gray-400">
              <span className="text-gray-500">VRAM:</span> {Math.round(resource.metadata.memory_mb / 1024)} GB
            </p>
          )}
          {resource.metadata.cores_logical && (
            <p className="text-gray-400">
              <span className="text-gray-500">Cores:</span> {resource.metadata.cores_logical} logical
              {resource.metadata.cores_physical && ` (${resource.metadata.cores_physical} physical)`}
            </p>
          )}
          {resource.metadata.path && (
            <p className="text-gray-400 font-mono text-xs">
              <span className="text-gray-500 font-sans">Path:</span> {resource.metadata.path}
            </p>
          )}
          {resource.metadata.driver && (
            <p className="text-gray-400 text-xs">
              <span className="text-gray-500">Driver:</span> {resource.metadata.driver}
              {resource.metadata.cuda && ` • CUDA ${resource.metadata.cuda}`}
            </p>
          )}
        </div>
      )}

      {/* Timestamps */}
      <div className="text-xs text-gray-500 mb-4">
        Created {formatDistanceToNow(new Date(resource.created_at), { addSuffix: true })}
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-2">
        {getStatusActions(resource).map((action) => (
          <button
            key={action.label}
            onClick={() => onStatusChange(resource, action.status)}
            disabled={isUpdating}
            className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {action.icon}
            {action.label}
          </button>
        ))}
        
        {isStorage ? (
          <button
            onClick={onShowStorage}
            className="px-3 py-1.5 bg-purple-900/30 hover:bg-purple-900/50 text-purple-400 rounded text-sm font-medium transition-colors flex items-center gap-2"
          >
            <HardDrive className="w-4 h-4" />
            Storage Info
          </button>
        ) : (
          <>
            <button
              onClick={onShowQueue}
              className="px-3 py-1.5 bg-gray-700 hover:bg-gray-600 rounded text-sm font-medium transition-colors flex items-center gap-2"
            >
              <Clock className="w-4 h-4" />
              Queue ({resource.jobs_queued + resource.jobs_running})
            </button>

            <button
              onClick={onShowTest}
              className="px-3 py-1.5 bg-neon-cyan/20 hover:bg-neon-cyan/30 text-neon-cyan rounded text-sm font-medium transition-colors flex items-center gap-2"
              title="Simulate load for testing"
            >
              <Zap className="w-4 h-4" />
              Test
            </button>
          </>
        )}

        {!resource.is_system_resource && (
          <button
            onClick={() => onDelete(resource)}
            disabled={isDeleting}
            className="px-3 py-1.5 bg-red-900/20 hover:bg-red-900/30 text-red-400 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center gap-2 ml-auto"
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </button>
        )}
      </div>
    </div>
  );
};

// Storage Modal Component
interface StorageModalProps {
  resource: Resource;
  onClose: () => void;
  onRefresh: () => void;
}

const StorageModal: React.FC<StorageModalProps> = ({ resource, onClose, onRefresh }) => {
  const queryClient = useQueryClient();
  
  const { data: storageInfo, isLoading } = useQuery({
    queryKey: ['storage-info', resource.id],
    queryFn: () => resourcesService.getStorageInfo(resource.id),
    refetchInterval: 5000,
  });

  const { data: files = [] } = useQuery({
    queryKey: ['storage-files', resource.id],
    queryFn: () => resourcesService.getStorageFiles(resource.id),
  });

  const scanMutation = useMutation({
    mutationFn: () => resourcesService.scanStorage(resource.id),
    onSuccess: (data) => {
      alert(data.message);
      queryClient.invalidateQueries({ queryKey: ['storage-info', resource.id] });
      onRefresh();
    },
    onError: (error: any) => {
      alert(`Failed to scan storage: ${error.message}`);
    },
  });

  const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-purple-900/30 rounded-lg">
              <HardDrive className="w-6 h-6 text-purple-400" />
            </div>
            <div>
              <h2 className="text-xl font-bold">{resource.name}</h2>
              <p className="text-sm text-gray-400 font-mono">{storageInfo?.path || resource.metadata?.path}</p>
            </div>
          </div>
          <button
            onClick={() => scanMutation.mutate()}
            disabled={scanMutation.isPending}
            className="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 rounded text-sm font-medium transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            <RefreshCw className={`w-4 h-4 ${scanMutation.isPending ? 'animate-spin' : ''}`} />
            Scan
          </button>
        </div>

        {isLoading ? (
          <div className="text-center py-8 text-gray-400">Loading storage info...</div>
        ) : storageInfo ? (
          <div className="space-y-6">
            {/* Space Overview */}
            <div className="bg-gray-750 rounded-lg p-4">
              <h3 className="text-sm font-medium text-gray-400 mb-3">Space Overview</h3>
              
              <div className="h-4 bg-gray-700 rounded-full overflow-hidden flex mb-3">
                <div 
                  className="bg-purple-500 transition-all duration-300"
                  style={{ width: `${(storageInfo.used_bytes / storageInfo.total_bytes) * 100}%` }}
                />
                {storageInfo.reserved_bytes > 0 && (
                  <div 
                    className="bg-yellow-500 transition-all duration-300"
                    style={{ width: `${(storageInfo.reserved_bytes / storageInfo.total_bytes) * 100}%` }}
                  />
                )}
              </div>

              <div className="grid grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-gray-400">Total</p>
                  <p className="font-medium">{formatBytes(storageInfo.total_bytes)}</p>
                </div>
                <div>
                  <p className="text-gray-400">Used</p>
                  <p className="font-medium text-purple-400">{formatBytes(storageInfo.used_bytes)}</p>
                </div>
                <div>
                  <p className="text-gray-400">Reserved</p>
                  <p className="font-medium text-yellow-400">{formatBytes(storageInfo.reserved_bytes)}</p>
                </div>
                <div>
                  <p className="text-gray-400">Available</p>
                  <p className="font-medium text-green-400">{formatBytes(storageInfo.available_bytes)}</p>
                </div>
              </div>
            </div>

            {/* Active Reservations */}
            <div className="bg-gray-750 rounded-lg p-4">
              <h3 className="text-sm font-medium text-gray-400 mb-3">
                Active Reservations ({storageInfo.active_reservations.length})
              </h3>
              {storageInfo.active_reservations.length === 0 ? (
                <p className="text-gray-500 text-sm">No active reservations</p>
              ) : (
                <div className="space-y-2">
                  {storageInfo.active_reservations.map((res) => (
                    <div key={res.id} className="flex items-center justify-between text-sm bg-gray-700 rounded p-2">
                      <div>
                        <span className="font-medium">{res.agent_name}</span>
                        {res.purpose && <span className="text-gray-400 ml-2">{res.purpose}</span>}
                      </div>
                      <div className="text-yellow-400">{formatBytes(res.bytes_reserved)}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Tracked Files Summary */}
            <div className="bg-gray-750 rounded-lg p-4">
              <h3 className="text-sm font-medium text-gray-400 mb-3">
                Tracked Files ({storageInfo.tracked_files_count})
              </h3>
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <p className="text-gray-400">Files Tracked</p>
                  <p className="font-medium">{storageInfo.tracked_files_count}</p>
                </div>
                <div>
                  <p className="text-gray-400">Total Size</p>
                  <p className="font-medium">{formatBytes(storageInfo.tracked_files_size)}</p>
                </div>
              </div>
              
              {files.length > 0 && (
                <div className="mt-4 max-h-40 overflow-y-auto">
                  <table className="w-full text-sm">
                    <thead className="text-gray-400 text-xs">
                      <tr>
                        <th className="text-left pb-2">File</th>
                        <th className="text-left pb-2">Agent</th>
                        <th className="text-right pb-2">Size</th>
                      </tr>
                    </thead>
                    <tbody>
                      {files.slice(0, 10).map((file) => (
                        <tr key={file.id} className="border-t border-gray-700">
                          <td className="py-1 font-mono text-xs truncate max-w-[200px]" title={file.file_path}>
                            {file.file_path.split('/').pop()}
                            {file.is_temporary && (
                              <span className="text-yellow-400 ml-1">(temp)</span>
                            )}
                          </td>
                          <td className="py-1 text-gray-400">{file.agent_name}</td>
                          <td className="py-1 text-right">{formatBytes(file.size_bytes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {files.length > 10 && (
                    <p className="text-xs text-gray-500 mt-2">...and {files.length - 10} more files</p>
                  )}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="text-center py-8 text-gray-400">No storage information available</div>
        )}

        <button
          onClick={onClose}
          className="w-full mt-6 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg font-medium transition-colors"
        >
          Close
        </button>
      </div>
    </div>
  );
};
