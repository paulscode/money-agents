import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { Alert } from '@/components/common/Alert';
import { ResourceSelector } from '@/components/common/ResourceSelector';
import { DistributedExecutionConfig } from '@/components/tools/DistributedExecutionConfig';
import { InterfaceConfigEditor, type InterfaceType } from '@/components/tools/InterfaceConfigEditor';
import { toolsService } from '@/services/tools';
import type { ToolUpdate, ToolCategory } from '@/types';
import { ArrowLeft, Loader2, Wrench, Save, Server } from 'lucide-react';
import MDEditor from '@uiw/react-md-editor';
import rehypeSanitize from 'rehype-sanitize';
import { logError } from '@/lib/logger';

const CATEGORY_OPTIONS: Array<{ value: ToolCategory; label: string; icon: string }> = [
  { value: 'api', label: 'API Integration', icon: '🔌' },
  { value: 'data_source', label: 'Data Source', icon: '📊' },
  { value: 'automation', label: 'Automation', icon: '⚙️' },
  { value: 'analysis', label: 'Analysis', icon: '🔍' },
  { value: 'communication', label: 'Communication', icon: '💬' },
];

export function ToolEditPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [alertError, setAlertError] = useState<{ title: string; message: string } | null>(null);
  const [alertSuccess, setAlertSuccess] = useState<string | null>(null);

  // Fetch existing tool data
  const { data: tool, isLoading: isLoadingTool } = useQuery({
    queryKey: ['tool', id],
    queryFn: () => toolsService.getTool(id!),
    enabled: !!id,
  });

  const [formData, setFormData] = useState<ToolUpdate>({
    name: '',
    slug: '',
    category: 'api',
    description: '',
    tags: [],
    resource_ids: [],
    usage_instructions: '',
    strengths: '',
    weaknesses: '',
    best_use_cases: '',
    implementation_notes: '',
    external_documentation_url: '',
    integration_complexity: '',
    cost_model: '',
    priority: '',
    // Execution interface
    interface_type: null,
    interface_config: null,
    input_schema: null,
    output_schema: null,
    timeout_seconds: null,
    // Distributed execution
    available_on_agents: null,
    agent_resource_map: null,
  });

  const [tagInput, setTagInput] = useState('');

  // Populate form when tool data loads
  useEffect(() => {
    if (tool) {
      setFormData({
        name: tool.name,
        slug: tool.slug,
        category: tool.category,
        description: tool.description,
        tags: tool.tags || [],
        resource_ids: tool.resource_ids || [],
        usage_instructions: tool.usage_instructions || '',
        strengths: tool.strengths || '',
        weaknesses: tool.weaknesses || '',
        best_use_cases: tool.best_use_cases || '',
        implementation_notes: tool.implementation_notes || '',
        external_documentation_url: tool.external_documentation_url || '',
        integration_complexity: tool.integration_complexity || '',
        cost_model: tool.cost_model || '',
        priority: tool.priority || '',
        // Execution interface
        interface_type: tool.interface_type,
        interface_config: tool.interface_config,
        input_schema: tool.input_schema,
        output_schema: tool.output_schema,
        timeout_seconds: tool.timeout_seconds,
        // Distributed execution
        available_on_agents: tool.available_on_agents,
        agent_resource_map: tool.agent_resource_map,
      });
    }
  }, [tool]);

  // Handle tag input
  const handleAddTag = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && tagInput.trim()) {
      e.preventDefault();
      if (!formData.tags?.includes(tagInput.trim())) {
        setFormData((prev) => ({
          ...prev,
          tags: [...(prev.tags || []), tagInput.trim()],
        }));
      }
      setTagInput('');
    }
  };

  const handleRemoveTag = (tag: string) => {
    setFormData((prev) => ({
      ...prev,
      tags: prev.tags?.filter((t) => t !== tag) || [],
    }));
  };

  const updateMutation = useMutation({
    mutationFn: (data: ToolUpdate) => toolsService.updateTool(id!, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
      queryClient.invalidateQueries({ queryKey: ['tool', id] });
      setAlertSuccess('Tool updated successfully');
      setTimeout(() => navigate(`/tools/${id}`), 1500);
    },
    onError: (error: any) => {
      logError('Tool update error:', error);
      const errorMessage = error.response?.data?.detail 
        || error.message 
        || 'An error occurred while updating the tool.';
      setAlertError({
        title: 'Failed to Update Tool',
        message: typeof errorMessage === 'string' ? errorMessage : JSON.stringify(errorMessage),
      });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAlertError(null);
    setAlertSuccess(null);

    // Validation
    if (!formData.name?.trim()) {
      setAlertError({ title: 'Validation Error', message: 'Tool name is required' });
      return;
    }
    if (!formData.description?.trim()) {
      setAlertError({ title: 'Validation Error', message: 'Description is required' });
      return;
    }

    // Clean up the data - use null to explicitly clear fields, undefined to leave unchanged
    // For fields we want to allow clearing, use null instead of undefined
    const cleanedData: ToolUpdate = {
      name: formData.name,
      slug: formData.slug,
      category: formData.category,
      description: formData.description,
      tags: formData.tags && formData.tags.length > 0 ? formData.tags : [],
      resource_ids: formData.resource_ids && formData.resource_ids.length > 0 ? formData.resource_ids : [],
      // For optional text fields, use null to clear them (not undefined which would skip the update)
      usage_instructions: formData.usage_instructions?.trim() || null,
      strengths: formData.strengths?.trim() || null,
      weaknesses: formData.weaknesses?.trim() || null,
      best_use_cases: formData.best_use_cases?.trim() || null,
      implementation_notes: formData.implementation_notes?.trim() || null,
      external_documentation_url: formData.external_documentation_url?.trim() || null,
      integration_complexity: formData.integration_complexity || null,
      cost_model: formData.cost_model?.trim() || null,
      priority: formData.priority || null,
      // Execution interface
      interface_type: formData.interface_type,
      interface_config: formData.interface_config,
      input_schema: formData.input_schema,
      output_schema: formData.output_schema,
      timeout_seconds: formData.timeout_seconds,
      // Distributed execution - these can be null (local only), [] (disabled), or arrays
      available_on_agents: formData.available_on_agents,
      agent_resource_map: formData.agent_resource_map,
    };

    updateMutation.mutate(cleanedData);
  };

  if (isLoadingTool) {
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
          <button
            onClick={() => navigate('/tools')}
            className="mt-4 text-neon-cyan hover:underline"
          >
            Back to Tools
          </button>
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate(`/tools/${id}`)}
              className="p-2 hover:bg-gray-800 rounded-lg transition-colors"
            >
              <ArrowLeft className="h-5 w-5 text-gray-400" />
            </button>
            <div>
              <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                <Wrench className="h-8 w-8 text-neon-cyan" />
                Edit Tool
              </h1>
              <p className="mt-1 text-gray-400">
                Modify tool details and configuration
              </p>
            </div>
          </div>
        </div>

        {/* Alerts */}
        {alertError && (
          <Alert
            type="error"
            title={alertError.title}
            message={alertError.message}
            onClose={() => setAlertError(null)}
          />
        )}
        {alertSuccess && (
          <Alert
            type="success"
            title="Success"
            message={alertSuccess}
            onClose={() => setAlertSuccess(null)}
          />
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Basic Information */}
          <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Basic Information</h2>
            
            <div className="space-y-4">
              {/* Name */}
              <div>
                <label htmlFor="name" className="block text-sm font-medium text-gray-300 mb-2">
                  Tool Name *
                </label>
                <input
                  id="name"
                  type="text"
                  required
                  value={formData.name || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="e.g., Stripe Payment Integration"
                />
              </div>

              {/* Slug */}
              <div>
                <label htmlFor="slug" className="block text-sm font-medium text-gray-300 mb-2">
                  Slug
                </label>
                <input
                  id="slug"
                  type="text"
                  value={formData.slug || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, slug: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-gray-400 placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="stripe-payment-integration"
                />
                <p className="mt-1 text-xs text-gray-500">Used in URLs and API references</p>
              </div>

              {/* Category */}
              <div>
                <label htmlFor="category" className="block text-sm font-medium text-gray-300 mb-2">
                  Category *
                </label>
                <select
                  id="category"
                  required
                  value={formData.category || 'api'}
                  onChange={(e) => setFormData((prev) => ({ ...prev, category: e.target.value as ToolCategory }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                >
                  {CATEGORY_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.icon} {option.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Description */}
              <div>
                <label htmlFor="description" className="block text-sm font-medium text-gray-300 mb-2">
                  Description *
                </label>
                <textarea
                  id="description"
                  required
                  rows={4}
                  value={formData.description || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, description: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="Brief description of what this tool does and why it's needed..."
                />
              </div>

              {/* Tags */}
              <div>
                <label htmlFor="tags" className="block text-sm font-medium text-gray-300 mb-2">
                  Tags
                </label>
                <input
                  id="tags"
                  type="text"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  onKeyDown={handleAddTag}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="Type and press Enter to add tags..."
                />
                {formData.tags && formData.tags.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {formData.tags.map((tag) => (
                      <span
                        key={tag}
                        className="inline-flex items-center gap-1 px-3 py-1 bg-neon-cyan/10 text-neon-cyan text-sm rounded-full border border-neon-cyan/30"
                      >
                        {tag}
                        <button
                          type="button"
                          onClick={() => handleRemoveTag(tag)}
                          className="hover:text-neon-cyan/70"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>

              {/* Required Resources */}
              <ResourceSelector
                selectedIds={formData.resource_ids || []}
                onChange={(ids) => setFormData((prev) => ({ ...prev, resource_ids: ids }))}
                helpText="Select any resources (GPU, storage, etc.) this tool requires to function"
              />
            </div>
          </div>

          {/* Implementation Details (admin/implementation-phase fields) */}
          <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Implementation Details</h2>
            
            <div className="space-y-4">
              {/* Implementation Notes */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Implementation Notes
                </label>
                <div data-color-mode="dark">
                  <MDEditor
                    value={formData.implementation_notes || ''}
                    onChange={(value) => setFormData((prev) => ({ ...prev, implementation_notes: value || '' }))}
                    preview="edit"
                    height={150}
                    previewOptions={{ rehypePlugins: [[rehypeSanitize]] }}
                  />
                </div>
                <p className="mt-1 text-xs text-gray-500">Technical notes about implementation progress</p>
              </div>

              {/* Integration Complexity */}
              <div>
                <label htmlFor="integration_complexity" className="block text-sm font-medium text-gray-300 mb-2">
                  Integration Complexity
                </label>
                <select
                  id="integration_complexity"
                  value={formData.integration_complexity || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, integration_complexity: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                >
                  <option value="">Not specified</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>

              {/* Priority */}
              <div>
                <label htmlFor="priority" className="block text-sm font-medium text-gray-300 mb-2">
                  Priority
                </label>
                <select
                  id="priority"
                  value={formData.priority || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, priority: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                >
                  <option value="">Not specified</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>

              {/* Cost Model */}
              <div>
                <label htmlFor="cost_model" className="block text-sm font-medium text-gray-300 mb-2">
                  Cost Model
                </label>
                <input
                  id="cost_model"
                  type="text"
                  value={formData.cost_model || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, cost_model: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="e.g., free, per_use, subscription"
                />
              </div>

              {/* External Documentation URL */}
              <div>
                <label htmlFor="external_documentation_url" className="block text-sm font-medium text-gray-300 mb-2">
                  External Documentation URL
                </label>
                <input
                  id="external_documentation_url"
                  type="url"
                  value={formData.external_documentation_url || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, external_documentation_url: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="https://docs.example.com/..."
                />
              </div>
            </div>
          </div>

          {/* Execution Interface */}
          <InterfaceConfigEditor
            interfaceType={formData.interface_type as InterfaceType}
            interfaceConfig={formData.interface_config ?? null}
            onTypeChange={(type) => setFormData(prev => ({ ...prev, interface_type: type }))}
            onConfigChange={(config) => setFormData(prev => ({ ...prev, interface_config: config }))}
            inputSchema={formData.input_schema ?? null}
            outputSchema={formData.output_schema ?? null}
            onInputSchemaChange={(schema) => setFormData(prev => ({ ...prev, input_schema: schema }))}
            onOutputSchemaChange={(schema) => setFormData(prev => ({ ...prev, output_schema: schema }))}
            timeoutSeconds={formData.timeout_seconds ?? null}
            onTimeoutChange={(timeout) => setFormData(prev => ({ ...prev, timeout_seconds: timeout }))}
          />

          {/* Distributed Execution */}
          <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4 flex items-center gap-2">
              <Server className="h-5 w-5 text-neon-purple" />
              Distributed Execution
            </h2>
            <p className="text-sm text-gray-400 mb-4">
              Configure where this tool can be executed and what resources it requires on each agent.
            </p>
            
            <DistributedExecutionConfig
              availableOnAgents={formData.available_on_agents ?? null}
              agentResourceMap={formData.agent_resource_map ?? null}
              onChange={(availableOnAgents, agentResourceMap) => 
                setFormData(prev => ({
                  ...prev,
                  available_on_agents: availableOnAgents,
                  agent_resource_map: agentResourceMap,
                }))
              }
              helpText="Configure which remote agents can execute this tool. For GPU-intensive tools like Ollama, select specific agents and assign their GPU resources."
            />
          </div>

          {/* Additional Details */}
          <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Documentation</h2>
            
            <div className="space-y-4">
              {/* Usage Instructions */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Usage Instructions
                </label>
                <div data-color-mode="dark">
                  <MDEditor
                    value={formData.usage_instructions || ''}
                    onChange={(value) => setFormData((prev) => ({ ...prev, usage_instructions: value || '' }))}
                    preview="edit"
                    height={200}
                    previewOptions={{ rehypePlugins: [[rehypeSanitize]] }}
                  />
                </div>
                <p className="mt-1 text-xs text-gray-500">How should this tool be used?</p>
              </div>

              {/* Strengths */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Strengths
                </label>
                <textarea
                  rows={3}
                  value={formData.strengths || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, strengths: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="What are the advantages of this tool?"
                />
              </div>

              {/* Weaknesses */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Weaknesses / Limitations
                </label>
                <textarea
                  rows={3}
                  value={formData.weaknesses || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, weaknesses: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="What are the limitations or drawbacks?"
                />
              </div>

              {/* Best Use Cases */}
              <div>
                <label className="block text-sm font-medium text-gray-300 mb-2">
                  Best Use Cases
                </label>
                <textarea
                  rows={3}
                  value={formData.best_use_cases || ''}
                  onChange={(e) => setFormData((prev) => ({ ...prev, best_use_cases: e.target.value }))}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="When is this tool most useful?"
                />
              </div>
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center justify-end gap-4">
            <button
              type="button"
              onClick={() => navigate(`/tools/${id}`)}
              className="px-6 py-2 bg-gray-800 text-gray-300 rounded-lg hover:bg-gray-700 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={updateMutation.isPending}
              className="btn-primary inline-flex items-center justify-center"
            >
              {updateMutation.isPending ? (
                <>
                  <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="h-5 w-5 mr-2" />
                  Save Changes
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </Layout>
  );
}
