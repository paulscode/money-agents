import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { Alert } from '@/components/common/Alert';
import { ResourceSelector } from '@/components/common/ResourceSelector';
import { toolsService } from '@/services/tools';
import type { ToolCreate, ToolCategory } from '@/types';
import { ArrowLeft, Loader2, Wrench, Save } from 'lucide-react';
import MDEditor from '@uiw/react-md-editor';
import rehypeSanitize from 'rehype-sanitize';

const CATEGORY_OPTIONS: Array<{ value: ToolCategory; label: string; icon: string }> = [
  { value: 'api', label: 'API Integration', icon: '🔌' },
  { value: 'data_source', label: 'Data Source', icon: '📊' },
  { value: 'automation', label: 'Automation', icon: '⚙️' },
  { value: 'analysis', label: 'Analysis', icon: '🔍' },
  { value: 'communication', label: 'Communication', icon: '💬' },
];

export function ToolCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [alertError, setAlertError] = useState<{ title: string; message: string } | null>(null);

  const [formData, setFormData] = useState<ToolCreate>({
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
  });

  const [tagInput, setTagInput] = useState('');

  // Auto-generate slug from name
  const handleNameChange = (name: string) => {
    setFormData((prev) => ({
      ...prev,
      name,
      slug: name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
    }));
  };

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

  const createMutation = useMutation({
    mutationFn: (data: ToolCreate) => toolsService.createTool(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
      navigate('/tools');
    },
    onError: (error: any) => {
      if (import.meta.env.DEV) {
        console.error('Tool creation error:', error);
        console.error('Error response:', error.response);
      }
      const errorMessage = error.response?.data?.detail 
        || error.message 
        || 'An error occurred while creating the tool request.';
      setAlertError({
        title: 'Failed to Create Tool Request',
        message: typeof errorMessage === 'string' ? errorMessage : JSON.stringify(errorMessage),
      });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAlertError(null);

    // Validation
    if (!formData.name.trim()) {
      setAlertError({ title: 'Validation Error', message: 'Tool name is required' });
      return;
    }
    if (!formData.description.trim()) {
      setAlertError({ title: 'Validation Error', message: 'Description is required' });
      return;
    }

    // Clean up the data - remove empty strings and replace with undefined
    const cleanedData: ToolCreate = {
      name: formData.name,
      slug: formData.slug,
      category: formData.category,
      description: formData.description,
      tags: formData.tags && formData.tags.length > 0 ? formData.tags : undefined,
      resource_ids: formData.resource_ids && formData.resource_ids.length > 0 ? formData.resource_ids : undefined,
      usage_instructions: formData.usage_instructions?.trim() || undefined,
      strengths: formData.strengths?.trim() || undefined,
      weaknesses: formData.weaknesses?.trim() || undefined,
      best_use_cases: formData.best_use_cases?.trim() || undefined,
    };

    createMutation.mutate(cleanedData);
  };

  return (
    <Layout>
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => navigate('/tools')}
              className="p-2 hover:bg-gray-800 rounded-lg transition-colors"
            >
              <ArrowLeft className="h-5 w-5 text-gray-400" />
            </button>
            <div>
              <h1 className="text-3xl font-bold text-white flex items-center gap-3">
                <Wrench className="h-8 w-8 text-neon-cyan" />
                Request New Tool
              </h1>
              <p className="mt-1 text-gray-400">
                Submit a request for a new tool to be added to the catalog
              </p>
            </div>
          </div>
        </div>

        {/* Alert */}
        {alertError && (
          <Alert
            type="error"
            title={alertError.title}
            message={alertError.message}
            onClose={() => setAlertError(null)}
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
                  value={formData.name}
                  onChange={(e) => handleNameChange(e.target.value)}
                  className="w-full px-4 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  placeholder="e.g., Stripe Payment Integration"
                />
              </div>

              {/* Slug (auto-generated) */}
              <div>
                <label htmlFor="slug" className="block text-sm font-medium text-gray-300 mb-2">
                  Slug (auto-generated)
                </label>
                <input
                  id="slug"
                  type="text"
                  value={formData.slug}
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
                  value={formData.category}
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
                  value={formData.description}
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

          {/* Additional Details */}
          <div className="bg-gradient-to-br from-gray-900/90 to-gray-900/50 backdrop-blur-sm rounded-xl border border-gray-800 p-6">
            <h2 className="text-xl font-semibold text-white mb-4">Additional Details</h2>
            
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
              onClick={() => navigate('/tools')}
              className="px-6 py-2 bg-gray-800 text-gray-300 rounded-lg hover:bg-gray-700 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="btn-primary inline-flex items-center justify-center"
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                  Submitting...
                </>
              ) : (
                <>
                  <Save className="h-5 w-5 mr-2" />
                  Submit Request
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </Layout>
  );
}
