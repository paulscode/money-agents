/**
 * AgentConfigModal Component
 * 
 * Modal for configuring agent settings:
 * - Schedule interval (with presets)
 * - Budget limits and period
 * - Batch size / items per run
 * - Model tier selection
 */
import { useState, useEffect } from 'react';
import type { AgentSummary, BudgetPeriod } from '@/types';
import { useUpdateAgent, useUpdateAgentBudget } from '@/hooks/useAgents';
import { formatDuration, parseDuration, SCHEDULE_PRESETS } from '@/services/agentService';
import { 
  X, 
  Clock, 
  DollarSign, 
  Layers, 
  Cpu,
  Save,
  AlertTriangle,
  RotateCcw,
  Timer,
} from 'lucide-react';
import { logError } from '@/lib/logger';

// Default configurations per agent
const AGENT_DEFAULTS: Record<string, {
  schedule_interval_seconds: number;
  default_model_tier: string;
  budget_limit: number | null;
  budget_period: BudgetPeriod;
  batch_size?: number;
  expected_run_duration_minutes: number;
}> = {
  opportunity_scout: {
    schedule_interval_seconds: 21600, // 6 hours
    default_model_tier: 'fast',
    budget_limit: null,
    budget_period: 'daily',
    batch_size: 10,
    expected_run_duration_minutes: 60,
  },
  proposal_writer: {
    schedule_interval_seconds: 300, // 5 minutes
    default_model_tier: 'reasoning',
    budget_limit: null,
    budget_period: 'daily',
    expected_run_duration_minutes: 5,
  },
  tool_scout: {
    schedule_interval_seconds: 3600, // 1 hour
    default_model_tier: 'fast',
    budget_limit: null,
    budget_period: 'daily',
    expected_run_duration_minutes: 15,
  },
  campaign_manager: {
    schedule_interval_seconds: 600, // 10 minutes
    default_model_tier: 'reasoning',
    budget_limit: null,
    budget_period: 'daily',
    expected_run_duration_minutes: 120,
  },
};

interface AgentConfigModalProps {
  agent: AgentSummary;
  isOpen: boolean;
  onClose: () => void;
}

export function AgentConfigModal({ agent, isOpen, onClose }: AgentConfigModalProps) {
  // Get defaults for this agent (fallback to reasonable defaults if not defined)
  const defaults = AGENT_DEFAULTS[agent.slug] || {
    schedule_interval_seconds: 3600,
    default_model_tier: 'fast',
    budget_limit: null,
    budget_period: 'daily' as BudgetPeriod,
    expected_run_duration_minutes: 30,
  };

  // Form state
  const [scheduleSeconds, setScheduleSeconds] = useState(agent.schedule_interval_seconds);
  const [customSchedule, setCustomSchedule] = useState('');
  const [budgetLimit, setBudgetLimit] = useState(agent.budget_limit || 0);
  const [budgetPeriod, setBudgetPeriod] = useState<BudgetPeriod>(agent.budget_period);
  const [warningThreshold, setWarningThreshold] = useState(
    (agent.budget_warning_threshold || 0.8) * 100
  );
  const [modelTier, setModelTier] = useState(agent.default_model_tier);
  const [batchSize, setBatchSize] = useState(agent.config?.batch_size || 10);
  const [isEnabled, setIsEnabled] = useState(agent.is_enabled);
  const [expectedDuration, setExpectedDuration] = useState(
    agent.expected_run_duration_minutes || defaults.expected_run_duration_minutes || 30
  );

  const updateAgent = useUpdateAgent();
  const updateBudget = useUpdateAgentBudget();

  const isLoading = updateAgent.isPending || updateBudget.isPending;
  const hasChanges = 
    scheduleSeconds !== agent.schedule_interval_seconds ||
    budgetLimit !== (agent.budget_limit || 0) ||
    budgetPeriod !== agent.budget_period ||
    modelTier !== agent.default_model_tier ||
    batchSize !== (agent.config?.batch_size || 10) ||
    isEnabled !== agent.is_enabled ||
    expectedDuration !== (agent.expected_run_duration_minutes || defaults.expected_run_duration_minutes || 30);

  // Check if current settings differ from defaults
  const isNotDefault = 
    scheduleSeconds !== defaults.schedule_interval_seconds ||
    modelTier !== defaults.default_model_tier ||
    budgetLimit !== (defaults.budget_limit || 0) ||
    budgetPeriod !== defaults.budget_period ||
    expectedDuration !== (defaults.expected_run_duration_minutes || 30) ||
    (defaults.batch_size !== undefined && batchSize !== defaults.batch_size);

  // Reset form when agent changes
  useEffect(() => {
    setScheduleSeconds(agent.schedule_interval_seconds);
    setBudgetLimit(agent.budget_limit || 0);
    setBudgetPeriod(agent.budget_period);
    setWarningThreshold((agent.budget_warning_threshold || 0.8) * 100);
    setModelTier(agent.default_model_tier);
    setBatchSize(agent.config?.batch_size || 10);
    setIsEnabled(agent.is_enabled);
    setExpectedDuration(agent.expected_run_duration_minutes || defaults.expected_run_duration_minutes || 30);
  }, [agent, defaults.expected_run_duration_minutes]);

  const handleResetToDefaults = () => {
    setScheduleSeconds(defaults.schedule_interval_seconds);
    setModelTier(defaults.default_model_tier);
    setBudgetLimit(defaults.budget_limit || 0);
    setBudgetPeriod(defaults.budget_period);
    setWarningThreshold(80); // Default warning threshold
    setExpectedDuration(defaults.expected_run_duration_minutes || 30);
    if (defaults.batch_size !== undefined) {
      setBatchSize(defaults.batch_size);
    }
    setCustomSchedule('');
  };

  const handleSchedulePreset = (seconds: number) => {
    setScheduleSeconds(seconds);
    setCustomSchedule('');
  };

  const handleCustomSchedule = (value: string) => {
    setCustomSchedule(value);
    const parsed = parseDuration(value);
    if (parsed !== null && parsed >= 60) {
      setScheduleSeconds(parsed);
    }
  };

  const handleSave = async () => {
    try {
      // Update main agent settings
      await updateAgent.mutateAsync({
        slug: agent.slug,
        data: {
          is_enabled: isEnabled,
          schedule_interval_seconds: scheduleSeconds,
          default_model_tier: modelTier,
          expected_run_duration_minutes: expectedDuration,
          config: {
            ...agent.config,
            batch_size: batchSize,
          },
        },
      });

      // Update budget separately
      await updateBudget.mutateAsync({
        slug: agent.slug,
        data: {
          budget_limit: budgetLimit > 0 ? budgetLimit : undefined,
          budget_period: budgetPeriod,
          warning_threshold: warningThreshold / 100,
        },
      });

      onClose();
    } catch (error) {
      logError('Failed to save agent config:', error);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />
      
      {/* Modal */}
      <div className="relative bg-gray-900 border border-gray-800 rounded-xl shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-gray-900 border-b border-gray-800 p-4 flex items-center justify-between">
          <h2 className="text-xl font-bold text-white">
            Configure {agent.name}
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-lg transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Enable/Disable Toggle */}
          <div className="flex items-center justify-between p-4 bg-gray-800/50 rounded-lg">
            <div>
              <div className="font-medium text-white">Enable Agent</div>
              <div className="text-sm text-gray-400">
                Disabled agents won't run automatically
              </div>
            </div>
            <button
              onClick={() => setIsEnabled(!isEnabled)}
              className={`relative w-14 h-7 rounded-full transition-colors ${
                isEnabled ? 'bg-neon-cyan' : 'bg-gray-700'
              }`}
            >
              <div className={`absolute top-1 w-5 h-5 bg-white rounded-full transition-all ${
                isEnabled ? 'left-8' : 'left-1'
              }`} />
            </button>
          </div>

          {/* Schedule Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Clock className="h-5 w-5 text-neon-cyan" />
              <h3 className="font-semibold text-white">Schedule</h3>
            </div>
            
            <div className="space-y-3">
              {/* Preset Buttons */}
              <div className="flex flex-wrap gap-2">
                {SCHEDULE_PRESETS.map((preset) => (
                  <button
                    key={preset.seconds}
                    onClick={() => handleSchedulePreset(preset.seconds)}
                    className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                      scheduleSeconds === preset.seconds
                        ? 'bg-neon-cyan/20 border-neon-cyan text-neon-cyan'
                        : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                    }`}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              {/* Custom Input */}
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-400">Or custom:</span>
                <input
                  type="text"
                  value={customSchedule}
                  onChange={(e) => handleCustomSchedule(e.target.value)}
                  placeholder="e.g., 45m, 8h, 2d"
                  className="px-3 py-1.5 bg-gray-800 border border-gray-700 rounded-lg text-white text-sm 
                           placeholder:text-gray-500 focus:border-neon-cyan focus:outline-none w-32"
                />
                {scheduleSeconds && (
                  <span className="text-sm text-gray-500">
                    = {formatDuration(scheduleSeconds)}
                  </span>
                )}
              </div>

              {scheduleSeconds < 300 && (
                <div className="flex items-center gap-2 text-yellow-500 text-sm">
                  <AlertTriangle className="h-4 w-4" />
                  Very short intervals may increase costs significantly
                </div>
              )}
            </div>
          </div>

          {/* Budget Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <DollarSign className="h-5 w-5 text-neon-purple" />
              <h3 className="font-semibold text-white">Budget</h3>
            </div>
            
            <div className="grid sm:grid-cols-2 gap-4">
              {/* Budget Limit */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">
                  Spending Limit (USD)
                </label>
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  value={budgetLimit}
                  onChange={(e) => setBudgetLimit(parseFloat(e.target.value) || 0)}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white
                           focus:border-neon-cyan focus:outline-none"
                  placeholder="0 = unlimited"
                />
              </div>

              {/* Budget Period */}
              <div>
                <label className="block text-sm text-gray-400 mb-1">
                  Reset Period
                </label>
                <select
                  value={budgetPeriod}
                  onChange={(e) => setBudgetPeriod(e.target.value as BudgetPeriod)}
                  className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white
                           focus:border-neon-cyan focus:outline-none"
                >
                  <option value="hourly">Hourly</option>
                  <option value="daily">Daily</option>
                  <option value="weekly">Weekly</option>
                  <option value="monthly">Monthly</option>
                </select>
              </div>

              {/* Warning Threshold */}
              <div className="sm:col-span-2">
                <label className="block text-sm text-gray-400 mb-1">
                  Warning at {warningThreshold}% of budget
                </label>
                <input
                  type="range"
                  min="50"
                  max="100"
                  step="5"
                  value={warningThreshold}
                  onChange={(e) => setWarningThreshold(parseInt(e.target.value))}
                  className="w-full accent-neon-cyan"
                />
              </div>
            </div>
          </div>

          {/* Batch Size Section (for Opportunity Scout) */}
          {agent.slug === 'opportunity_scout' && (
            <div>
              <div className="flex items-center gap-2 mb-3">
                <Layers className="h-5 w-5 text-green-500" />
                <h3 className="font-semibold text-white">Batch Size</h3>
              </div>
              
              <div>
                <label className="block text-sm text-gray-400 mb-1">
                  Opportunities per run: {batchSize}
                </label>
                <input
                  type="range"
                  min="5"
                  max="50"
                  step="5"
                  value={batchSize}
                  onChange={(e) => setBatchSize(parseInt(e.target.value))}
                  className="w-full accent-green-500"
                />
                <div className="flex justify-between text-xs text-gray-500 mt-1">
                  <span>5 (faster)</span>
                  <span>50 (more thorough)</span>
                </div>
              </div>
            </div>
          )}

          {/* Model Tier Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Cpu className="h-5 w-5 text-orange-500" />
              <h3 className="font-semibold text-white">Model Tier</h3>
            </div>
            
            <div className="grid grid-cols-3 gap-2">
              {['fast', 'reasoning', 'quality'].map((tier) => (
                <button
                  key={tier}
                  onClick={() => setModelTier(tier)}
                  className={`p-3 rounded-lg border transition-colors ${
                    modelTier === tier
                      ? 'bg-orange-500/20 border-orange-500 text-orange-500'
                      : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600'
                  }`}
                >
                  <div className="font-medium capitalize">{tier}</div>
                  <div className="text-xs opacity-75">
                    {tier === 'fast' && 'Lowest cost'}
                    {tier === 'reasoning' && 'Balanced'}
                    {tier === 'quality' && 'Best results'}
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Expected Duration Section */}
          <div>
            <div className="flex items-center gap-2 mb-3">
              <Timer className="h-5 w-5 text-cyan-500" />
              <h3 className="font-semibold text-white">Expected Run Duration</h3>
            </div>
            
            <div>
              <label className="block text-sm text-gray-400 mb-1">
                Expected duration: {expectedDuration} minute{expectedDuration !== 1 ? 's' : ''}
                {expectedDuration >= 60 && ` (${(expectedDuration / 60).toFixed(1)} hours)`}
              </label>
              <input
                type="range"
                min="5"
                max="240"
                step="5"
                value={expectedDuration}
                onChange={(e) => setExpectedDuration(parseInt(e.target.value))}
                className="w-full accent-cyan-500"
              />
              <div className="flex justify-between text-xs text-gray-500 mt-1">
                <span>5 min</span>
                <span>4 hours</span>
              </div>
              <p className="text-xs text-gray-500 mt-2">
                Used for crash recovery. Jobs running longer than {Math.round(expectedDuration * 1.5)} min 
                (1.5× expected) will be considered stale after a system restart.
              </p>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-gray-900 border-t border-gray-800 p-4 flex items-center justify-between">
          <button
            onClick={handleResetToDefaults}
            disabled={isLoading || !isNotDefault}
            className="px-4 py-2 text-gray-400 hover:text-white transition-colors 
                     disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            title="Reset to factory defaults"
          >
            <RotateCcw className="h-4 w-4" />
            Reset to Defaults
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="px-4 py-2 text-gray-400 hover:text-white transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={isLoading || !hasChanges}
              className="px-4 py-2 bg-neon-cyan text-black font-medium rounded-lg 
                       hover:bg-neon-cyan/90 transition-colors disabled:opacity-50 
                       disabled:cursor-not-allowed flex items-center gap-2"
            >
              <Save className="h-4 w-4" />
              {isLoading ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
