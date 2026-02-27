import { useState, useEffect } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { Alert } from '@/components/common/Alert';
import { proposalsService } from '@/services/proposals';
import type { ProposalCreate, RiskLevel } from '@/types';
import { ArrowLeft, Loader2, DollarSign, AlertTriangle, Target, Wrench, FileInput, Calendar, Bitcoin } from 'lucide-react';
import MDEditor from '@uiw/react-md-editor';
import rehypeSanitize from 'rehype-sanitize';
import CodeMirror from '@uiw/react-codemirror';
import { json } from '@codemirror/lang-json';

export function ProposalCreatePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { id } = useParams<{ id: string }>();
  const isEditMode = !!id;
  const [alertError, setAlertError] = useState<{ title: string; message: string } | null>(null);

  // Load existing proposal if in edit mode
  const { data: existingProposal, isLoading: isLoadingProposal } = useQuery({
    queryKey: ['proposals', id],
    queryFn: () => proposalsService.getById(id!),
    enabled: isEditMode,
  });

  const [formData, setFormData] = useState<ProposalCreate>({
    title: '',
    summary: '',
    detailed_description: '',
    initial_budget: 0,
    risk_level: 'medium' as RiskLevel,
    risk_description: '',
    stop_loss_threshold: {},
    success_criteria: {},
    required_tools: {},
    required_inputs: {},
  });

  const [jsonFields, setJsonFields] = useState({
    stop_loss_threshold: '{\n  "max_loss": 1000,\n  "time_limit_days": 30\n}',
    success_criteria: '{\n  "min_revenue": 5000,\n  "target_roi": 2.0\n}',
    required_tools: '{\n  "openai": "GPT-4 access",\n  "stripe": "Payment processing"\n}',
    required_inputs: '{\n  "api_keys": ["OPENAI_API_KEY"],\n  "accounts": ["stripe_account"]\n}',
    recurring_costs: '',
    expected_returns: '',
    implementation_timeline: '',
    tags: '',
  });

  // Populate form with existing proposal data when in edit mode
  useEffect(() => {
    if (existingProposal) {
      setFormData({
        title: existingProposal.title,
        summary: existingProposal.summary,
        detailed_description: existingProposal.detailed_description,
        initial_budget: existingProposal.initial_budget,
        bitcoin_budget_sats: existingProposal.bitcoin_budget_sats ?? undefined,
        bitcoin_budget_rationale: existingProposal.bitcoin_budget_rationale ?? undefined,
        risk_level: existingProposal.risk_level,
        risk_description: existingProposal.risk_description,
        stop_loss_threshold: existingProposal.stop_loss_threshold,
        success_criteria: existingProposal.success_criteria,
        required_tools: existingProposal.required_tools,
        required_inputs: existingProposal.required_inputs,
        recurring_costs: existingProposal.recurring_costs,
        expected_returns: existingProposal.expected_returns,
        implementation_timeline: existingProposal.implementation_timeline,
        source: existingProposal.source,
        tags: existingProposal.tags,
      });

      setJsonFields({
        stop_loss_threshold: JSON.stringify(existingProposal.stop_loss_threshold, null, 2),
        success_criteria: JSON.stringify(existingProposal.success_criteria, null, 2),
        required_tools: JSON.stringify(existingProposal.required_tools, null, 2),
        required_inputs: JSON.stringify(existingProposal.required_inputs, null, 2),
        recurring_costs: existingProposal.recurring_costs ? JSON.stringify(existingProposal.recurring_costs, null, 2) : '',
        expected_returns: existingProposal.expected_returns ? JSON.stringify(existingProposal.expected_returns, null, 2) : '',
        implementation_timeline: existingProposal.implementation_timeline ? JSON.stringify(existingProposal.implementation_timeline, null, 2) : '',
        tags: existingProposal.tags ? JSON.stringify(existingProposal.tags, null, 2) : '',
      });
    }
  }, [existingProposal]);

  const createMutation = useMutation({
    mutationFn: (data: ProposalCreate) => 
      isEditMode 
        ? proposalsService.update(id!, data) 
        : proposalsService.create(data),
    onSuccess: async (data) => {
      // Set the proposal in the cache immediately so it's available on the detail page
      queryClient.setQueryData(['proposals', data.id], data);
      // Invalidate the list to refetch it (but not the individual proposal we just created)
      await queryClient.invalidateQueries({ 
        queryKey: ['proposals'],
        exact: true, // Only invalidate the list query, not individual proposals
      });
      navigate(`/proposals/${data.id}`);
    },
    onError: (error: any) => {
      if (import.meta.env.DEV) {
        console.error('Proposal creation error:', error);
        console.error('Error response:', error?.response?.data);
      }
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setAlertError(null);

    const jsonFieldLabels: Record<keyof typeof jsonFields, string> = {
      stop_loss_threshold: 'Stop Loss Threshold',
      success_criteria: 'Success Criteria',
      required_tools: 'Required Tools',
      required_inputs: 'Required Inputs',
      recurring_costs: 'Recurring Costs',
      expected_returns: 'Expected Returns',
      implementation_timeline: 'Implementation Timeline',
      tags: 'Tags',
    };

    try {
      const proposalData: ProposalCreate = {
        ...formData,
      };

      // Validate and parse required JSON fields
      const requiredFields: Array<keyof typeof jsonFields> = [
        'stop_loss_threshold',
        'success_criteria',
        'required_tools',
        'required_inputs',
      ];

      for (const field of requiredFields) {
        try {
          proposalData[field] = JSON.parse(jsonFields[field]);
        } catch {
          setAlertError({
            title: 'Invalid JSON',
            message: `The "${jsonFieldLabels[field]}" field contains invalid JSON. Please check the syntax and try again.`,
          });
          return;
        }
      }

      // Validate and parse optional JSON fields
      const optionalFields: Array<keyof typeof jsonFields> = [
        'recurring_costs',
        'expected_returns',
        'implementation_timeline',
        'tags',
      ];

      for (const field of optionalFields) {
        if (jsonFields[field].trim()) {
          try {
            proposalData[field] = JSON.parse(jsonFields[field]);
          } catch {
            setAlertError({
              title: 'Invalid JSON',
              message: `The "${jsonFieldLabels[field]}" field contains invalid JSON. Please check the syntax and try again.`,
            });
            return;
          }
        }
      }

      createMutation.mutate(proposalData);
    } catch (error) {
      setAlertError({
        title: 'Submission Error',
        message: 'An unexpected error occurred. Please try again.',
      });
    }
  };

  if (isLoadingProposal) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
        </div>
      </Layout>
    );
  }

  return (
    <Layout>
      {alertError && (
        <Alert
          type="error"
          title={alertError.title}
          message={alertError.message}
          onClose={() => setAlertError(null)}
        />
      )}
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <button
            onClick={() => navigate(isEditMode ? `/proposals/${id}` : '/proposals')}
            className="flex items-center gap-2 text-gray-400 hover:text-neon-cyan transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
            {isEditMode ? 'Back to Proposal' : 'Back to Proposals'}
          </button>
        </div>

        <div>
          <h1 className="text-4xl font-bold text-white mb-2">
            {isEditMode ? 'Edit Proposal' : 'Create New Proposal'}
          </h1>
          <p className="text-gray-400">
            {isEditMode 
              ? 'Update the proposal details' 
              : 'Submit a new money-making opportunity for review'
            }
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-6">
          {/* Basic Information */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <FileInput className="h-5 w-5 text-neon-cyan" />
              Basic Information
            </h2>

            <div>
              <label htmlFor="title" className="block text-sm font-medium text-gray-300 mb-2">
                Title *
              </label>
              <input
                id="title"
                type="text"
                required
                value={formData.title}
                onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                placeholder="E.g., Automated Content Marketing Campaign"
              />
            </div>

            <div>
              <label htmlFor="summary" className="block text-sm font-medium text-gray-300 mb-2">
                Summary *
              </label>
              <textarea
                id="summary"
                required
                value={formData.summary}
                onChange={(e) => setFormData({ ...formData, summary: e.target.value })}
                rows={2}
                className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan resize-none"
                placeholder="Brief one-paragraph summary of the opportunity..."
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Detailed Description *
              </label>
              <div data-color-mode="dark" className="border border-gray-700 rounded-lg overflow-hidden">
                <MDEditor
                  value={formData.detailed_description}
                  onChange={(val) => setFormData({ ...formData, detailed_description: val || '' })}
                  preview="edit"
                  height={300}
                  previewOptions={{ rehypePlugins: [[rehypeSanitize]] }}
                  style={{
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                />
              </div>
              <p className="text-xs text-gray-500 mt-1">Supports markdown formatting. Use ``` with language name for code blocks (e.g., ```json)</p>
            </div>

            <div>
              <label htmlFor="source" className="block text-sm font-medium text-gray-300 mb-2">
                Source
              </label>
              <input
                id="source"
                type="text"
                value={formData.source || ''}
                onChange={(e) => setFormData({ ...formData, source: e.target.value })}
                className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                placeholder="E.g., agent_scout, manual, research"
              />
            </div>
          </div>

          {/* Financial Details */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <DollarSign className="h-5 w-5 text-neon-cyan" />
              Financial Details
            </h2>

            <div>
              <label htmlFor="initial_budget" className="block text-sm font-medium text-gray-300 mb-2">
                Initial Budget * ($)
              </label>
              <input
                id="initial_budget"
                type="number"
                required
                min="0"
                step="0.01"
                value={formData.initial_budget || ''}
                onChange={(e) => setFormData({ ...formData, initial_budget: parseFloat(e.target.value) || 0 })}
                className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                placeholder="1000.00"
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label htmlFor="bitcoin_budget_sats" className="block text-sm font-medium text-gray-300 mb-2">
                  <span className="flex items-center gap-1.5">Bitcoin Budget (sats)
                    <span className="text-xs text-gray-500 font-normal">— optional, for campaigns that spend BTC</span>
                  </span>
                </label>
                <input
                  id="bitcoin_budget_sats"
                  type="number"
                  min="0"
                  step="1"
                  value={formData.bitcoin_budget_sats ?? ''}
                  onChange={(e) => setFormData({ ...formData, bitcoin_budget_sats: e.target.value ? parseInt(e.target.value) || 0 : undefined })}
                  className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-yellow-400 focus:ring-1 focus:ring-yellow-400"
                  placeholder="100000"
                />
              </div>
              <div>
                <label htmlFor="bitcoin_budget_rationale" className="block text-sm font-medium text-gray-300 mb-2">
                  Bitcoin Budget Rationale
                </label>
                <input
                  id="bitcoin_budget_rationale"
                  type="text"
                  value={formData.bitcoin_budget_rationale ?? ''}
                  onChange={(e) => setFormData({ ...formData, bitcoin_budget_rationale: e.target.value || undefined })}
                  className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-yellow-400 focus:ring-1 focus:ring-yellow-400"
                  placeholder="E.g., Lightning payments for service access, Nostr zaps for promotion"
                />
              </div>
            </div>

            <div>
              <label htmlFor="recurring_costs" className="block text-sm font-medium text-gray-300 mb-2">
                Recurring Costs (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.recurring_costs}
                  onChange={(value) => setJsonFields({ ...jsonFields, recurring_costs: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="60px"
                  maxHeight="200px"
                />
              </div>
            </div>

            <div>
              <label htmlFor="expected_returns" className="block text-sm font-medium text-gray-300 mb-2">
                Expected Returns (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.expected_returns}
                  onChange={(value) => setJsonFields({ ...jsonFields, expected_returns: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="60px"
                  maxHeight="200px"
                />
              </div>
            </div>
          </div>

          {/* Risk Assessment */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-neon-cyan" />
              Risk Assessment
            </h2>

            <div>
              <label htmlFor="risk_level" className="block text-sm font-medium text-gray-300 mb-2">
                Risk Level *
              </label>
              <select
                id="risk_level"
                required
                value={formData.risk_level}
                onChange={(e) => setFormData({ ...formData, risk_level: e.target.value as RiskLevel })}
                className="w-full px-4 py-2 bg-black/30 border border-gray-700 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
              >
                <option value="low">Low Risk</option>
                <option value="medium">Medium Risk</option>
                <option value="high">High Risk</option>
              </select>
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-300 mb-2">
                Risk Description *
              </label>
              <div data-color-mode="dark" className="border border-gray-700 rounded-lg overflow-hidden">
                <MDEditor
                  value={formData.risk_description}
                  onChange={(val) => setFormData({ ...formData, risk_description: val || '' })}
                  preview="edit"
                  height={200}
                  previewOptions={{ rehypePlugins: [[rehypeSanitize]] }}
                  style={{
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                />
              </div>
              <p className="text-xs text-gray-500 mt-1">Supports markdown formatting. Use ``` with language name for code blocks (e.g., ```json)</p>
            </div>

            <div>
              <label htmlFor="stop_loss_threshold" className="block text-sm font-medium text-gray-300 mb-2">
                Stop Loss Threshold * (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.stop_loss_threshold}
                  onChange={(value) => setJsonFields({ ...jsonFields, stop_loss_threshold: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="80px"
                  maxHeight="200px"
                />
              </div>
            </div>
          </div>

          {/* Success Criteria */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <Target className="h-5 w-5 text-neon-cyan" />
              Success Criteria
            </h2>

            <div>
              <label htmlFor="success_criteria" className="block text-sm font-medium text-gray-300 mb-2">
                Success Criteria * (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.success_criteria}
                  onChange={(value) => setJsonFields({ ...jsonFields, success_criteria: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="80px"
                  maxHeight="200px"
                />
              </div>
            </div>
          </div>

          {/* Requirements */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <Wrench className="h-5 w-5 text-neon-cyan" />
              Requirements
            </h2>

            <div>
              <label htmlFor="required_tools" className="block text-sm font-medium text-gray-300 mb-2">
                Required Tools * (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.required_tools}
                  onChange={(value) => setJsonFields({ ...jsonFields, required_tools: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="80px"
                  maxHeight="200px"
                />
              </div>
            </div>

            <div>
              <label htmlFor="required_inputs" className="block text-sm font-medium text-gray-300 mb-2">
                Required Inputs * (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.required_inputs}
                  onChange={(value) => setJsonFields({ ...jsonFields, required_inputs: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="80px"
                  maxHeight="200px"
                />
              </div>
            </div>
          </div>

          {/* Implementation Timeline */}
          <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-6 space-y-6">
            <h2 className="text-xl font-semibold text-white flex items-center gap-2">
              <Calendar className="h-5 w-5 text-neon-cyan" />
              Implementation Timeline
            </h2>

            <div>
              <label htmlFor="implementation_timeline" className="block text-sm font-medium text-gray-300 mb-2">
                Implementation Timeline (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.implementation_timeline}
                  onChange={(value) => setJsonFields({ ...jsonFields, implementation_timeline: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="80px"
                  maxHeight="200px"
                />
              </div>
            </div>

            <div>
              <label htmlFor="tags" className="block text-sm font-medium text-gray-300 mb-2">
                Tags (JSON)
              </label>
              <div className="border border-gray-700 rounded-lg overflow-hidden">
                <CodeMirror
                  value={jsonFields.tags}
                  onChange={(value) => setJsonFields({ ...jsonFields, tags: value })}
                  extensions={[json()]}
                  theme="dark"
                  basicSetup={{
                    lineNumbers: false,
                    foldGutter: false,
                    highlightActiveLine: false,
                  }}
                  style={{
                    fontSize: '14px',
                    backgroundColor: 'rgba(0, 0, 0, 0.3)',
                  }}
                  minHeight="40px"
                  maxHeight="200px"
                />
              </div>
            </div>
          </div>

          {/* Submit Button */}
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={() => navigate('/proposals')}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="btn-primary"
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 className="h-5 w-5 mr-2 animate-spin" />
                  {isEditMode ? 'Updating...' : 'Creating...'}
                </>
              ) : (
                isEditMode ? 'Update Proposal' : 'Create Proposal'
              )}
            </button>
          </div>

          {createMutation.isError && (
            <div className="bg-red-500/20 border border-red-500/30 rounded-lg p-4 text-red-400">
              <p className="font-semibold">Failed to create proposal</p>
              <p className="text-sm mt-1">
                {(createMutation.error as any)?.response?.data?.detail || 
                 (createMutation.error as any)?.message || 
                 'Please check your input and try again.'}
              </p>
            </div>
          )}
        </form>
      </div>
    </Layout>
  );
}
