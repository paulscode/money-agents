/**
 * ScoutSettingsModal Component
 * 
 * Modal for configuring user's Opportunity Scout preferences:
 * - Auto-dismiss threshold
 * - Preferred/excluded opportunity types
 * - Revenue thresholds
 * - Focus/avoid domains
 */
import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { opportunitiesService } from '@/services/opportunities';
import type { UserScoutSettings, OpportunityType } from '@/types/opportunity';
import {
  X,
  Settings,
  Save,
  Plus,
  Target,
  AlertTriangle,
  Loader2,
  RotateCcw,
} from 'lucide-react';

const OPPORTUNITY_TYPES: Array<{ value: OpportunityType; label: string; emoji: string }> = [
  { value: 'arbitrage', label: 'Arbitrage', emoji: '📈' },
  { value: 'content', label: 'Content', emoji: '✍️' },
  { value: 'service', label: 'Service', emoji: '🛠️' },
  { value: 'product', label: 'Product', emoji: '📦' },
  { value: 'affiliate', label: 'Affiliate', emoji: '🔗' },
  { value: 'other', label: 'Other', emoji: '💡' },
];

interface ScoutSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export function ScoutSettingsModal({ isOpen, onClose }: ScoutSettingsModalProps) {
  const queryClient = useQueryClient();

  // Fetch current settings
  const { data: settings, isLoading: isLoadingSettings } = useQuery({
    queryKey: ['scout-settings'],
    queryFn: () => opportunitiesService.getSettings(),
    enabled: isOpen,
  });

  // Form state
  const [autoDismissScore, setAutoDismissScore] = useState<number>(0);
  const [autoDismissTypes, setAutoDismissTypes] = useState<string[]>([]);
  const [preferredTypes, setPreferredTypes] = useState<string[]>([]);
  const [excludedTypes, setExcludedTypes] = useState<string[]>([]);
  const [preferredDomains, setPreferredDomains] = useState<string[]>([]);
  const [excludedKeywords, setExcludedKeywords] = useState<string[]>([]);
  const [maxActiveProposals, setMaxActiveProposals] = useState<number>(10);
  const [hopperWarningThreshold, setHopperWarningThreshold] = useState<number>(8);
  const [autoPauseDiscovery, setAutoPauseDiscovery] = useState<boolean>(true);
  const [maxBacklogSize, setMaxBacklogSize] = useState<number>(200);
  const [showUnlikelyTier, setShowUnlikelyTier] = useState<boolean>(true);

  // New input fields
  const [newPreferredDomain, setNewPreferredDomain] = useState('');
  const [newExcludedKeyword, setNewExcludedKeyword] = useState('');

  // Populate form when settings load
  useEffect(() => {
    if (settings) {
      setAutoDismissScore(settings.auto_dismiss_below_score || 0);
      setAutoDismissTypes(settings.auto_dismiss_types || []);
      setPreferredTypes(settings.preferred_types || []);
      setExcludedTypes(settings.excluded_types || []);
      setPreferredDomains(settings.preferred_domains || []);
      setExcludedKeywords(settings.excluded_keywords || []);
      setMaxActiveProposals(settings.max_active_proposals || 10);
      setHopperWarningThreshold(settings.hopper_warning_threshold || 8);
      setAutoPauseDiscovery(settings.auto_pause_discovery ?? true);
      setMaxBacklogSize(settings.max_backlog_size ?? 200);
      setShowUnlikelyTier(settings.show_unlikely_tier ?? true);
    }
  }, [settings]);

  // Save mutation
  const saveMutation = useMutation({
    mutationFn: (data: Partial<UserScoutSettings>) =>
      opportunitiesService.updateSettings(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['scout-settings'] });
      onClose();
    },
  });

  const handleSave = () => {
    saveMutation.mutate({
      auto_dismiss_below_score: autoDismissScore > 0 ? autoDismissScore : undefined,
      auto_dismiss_types: autoDismissTypes,
      preferred_types: preferredTypes,
      excluded_types: excludedTypes,
      preferred_domains: preferredDomains,
      excluded_keywords: excludedKeywords,
      max_active_proposals: maxActiveProposals,
      hopper_warning_threshold: hopperWarningThreshold,
      auto_pause_discovery: autoPauseDiscovery,
      max_backlog_size: maxBacklogSize,
      show_unlikely_tier: showUnlikelyTier,
    });
  };

  const handleReset = () => {
    setAutoDismissScore(0);
    setAutoDismissTypes([]);
    setPreferredTypes([]);
    setExcludedTypes([]);
    setPreferredDomains([]);
    setExcludedKeywords([]);
    setMaxActiveProposals(10);
    setHopperWarningThreshold(8);
    setAutoPauseDiscovery(true);
    setMaxBacklogSize(200);
    setShowUnlikelyTier(true);
  };

  const toggleType = (type: string, list: 'preferred' | 'excluded') => {
    if (list === 'preferred') {
      if (preferredTypes.includes(type)) {
        setPreferredTypes(preferredTypes.filter((t) => t !== type));
      } else {
        setPreferredTypes([...preferredTypes, type]);
        // Remove from excluded if adding to preferred
        setExcludedTypes(excludedTypes.filter((t) => t !== type));
      }
    } else {
      if (excludedTypes.includes(type)) {
        setExcludedTypes(excludedTypes.filter((t) => t !== type));
      } else {
        setExcludedTypes([...excludedTypes, type]);
        // Remove from preferred if adding to excluded
        setPreferredTypes(preferredTypes.filter((t) => t !== type));
      }
    }
  };

  const addPreferredDomain = () => {
    if (newPreferredDomain.trim() && !preferredDomains.includes(newPreferredDomain.trim())) {
      setPreferredDomains([...preferredDomains, newPreferredDomain.trim()]);
      setNewPreferredDomain('');
    }
  };

  const addExcludedKeyword = () => {
    if (newExcludedKeyword.trim() && !excludedKeywords.includes(newExcludedKeyword.trim())) {
      setExcludedKeywords([...excludedKeywords, newExcludedKeyword.trim()]);
      setNewExcludedKeyword('');
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
        <div className="sticky top-0 bg-gray-900 border-b border-gray-800 p-4 flex items-center justify-between z-10">
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <Settings className="h-5 w-5 text-neon-cyan" />
            Scout Settings
          </h2>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-white hover:bg-gray-800 rounded-lg transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content */}
        {isLoadingSettings ? (
          <div className="p-12 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
          </div>
        ) : (
          <div className="p-6 space-y-6">
            {/* Auto-Dismiss Section */}
            <section className="space-y-3">
              <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-yellow-500" />
                Auto-Dismiss Rules
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 space-y-4">
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Auto-dismiss opportunities scoring below:
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min="0"
                      max="100"
                      step="5"
                      value={autoDismissScore}
                      onChange={(e) => setAutoDismissScore(Number(e.target.value))}
                      className="flex-1 h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-neon-cyan"
                    />
                    <span className="text-sm font-mono text-white w-16 text-right">
                      {autoDismissScore === 0 ? 'Disabled' : `${autoDismissScore}%`}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 mt-1">
                    Set to 0 to disable auto-dismiss
                  </p>
                </div>
              </div>
            </section>

            {/* Opportunity Types Section */}
            <section className="space-y-3">
              <h3 className="text-sm font-medium text-gray-300 flex items-center gap-2">
                <Target className="h-4 w-4 text-neon-cyan" />
                Opportunity Types
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 space-y-4">
                {/* Preferred Types */}
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Preferred types (prioritized in results):
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {OPPORTUNITY_TYPES.map((type) => (
                      <button
                        key={type.value}
                        onClick={() => toggleType(type.value, 'preferred')}
                        className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                          preferredTypes.includes(type.value)
                            ? 'bg-green-600/30 border border-green-500 text-green-400'
                            : 'bg-gray-700/50 border border-gray-600 text-gray-400 hover:border-gray-500'
                        }`}
                      >
                        {type.emoji} {type.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Excluded Types */}
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Excluded types (auto-filtered out):
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {OPPORTUNITY_TYPES.map((type) => (
                      <button
                        key={type.value}
                        onClick={() => toggleType(type.value, 'excluded')}
                        className={`px-3 py-1.5 rounded-lg text-sm transition-colors ${
                          excludedTypes.includes(type.value)
                            ? 'bg-red-600/30 border border-red-500 text-red-400'
                            : 'bg-gray-700/50 border border-gray-600 text-gray-400 hover:border-gray-500'
                        }`}
                      >
                        {type.emoji} {type.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </section>

            {/* Domains & Keywords Section */}
            <section className="space-y-3">
              <h3 className="text-sm font-medium text-gray-300">
                Domains & Keywords
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 space-y-4">
                {/* Preferred Domains */}
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Preferred domains (topics/niches to focus on):
                  </label>
                  <div className="flex flex-wrap gap-2 mb-2">
                    {preferredDomains.map((domain) => (
                      <span
                        key={domain}
                        className="inline-flex items-center gap-1 px-2 py-1 bg-green-600/20 border border-green-500/50 rounded text-sm text-green-400"
                      >
                        {domain}
                        <button
                          onClick={() =>
                            setPreferredDomains(preferredDomains.filter((d) => d !== domain))
                          }
                          className="hover:text-green-300"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={newPreferredDomain}
                      onChange={(e) => setNewPreferredDomain(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && addPreferredDomain()}
                      placeholder="e.g., AI, SaaS, crypto"
                      className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    />
                    <button
                      onClick={addPreferredDomain}
                      className="px-3 py-2 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded-lg text-gray-400 hover:text-white transition-colors"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Excluded Keywords */}
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Excluded keywords (filter out opportunities containing):
                  </label>
                  <div className="flex flex-wrap gap-2 mb-2">
                    {excludedKeywords.map((keyword) => (
                      <span
                        key={keyword}
                        className="inline-flex items-center gap-1 px-2 py-1 bg-red-600/20 border border-red-500/50 rounded text-sm text-red-400"
                      >
                        {keyword}
                        <button
                          onClick={() =>
                            setExcludedKeywords(excludedKeywords.filter((k) => k !== keyword))
                          }
                          className="hover:text-red-300"
                        >
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={newExcludedKeyword}
                      onChange={(e) => setNewExcludedKeyword(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && addExcludedKeyword()}
                      placeholder="e.g., gambling, adult"
                      className="flex-1 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm placeholder-gray-500 focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    />
                    <button
                      onClick={addExcludedKeyword}
                      className="px-3 py-2 bg-gray-700 hover:bg-gray-600 border border-gray-600 rounded-lg text-gray-400 hover:text-white transition-colors"
                    >
                      <Plus className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </div>
            </section>

            {/* Hopper & Display Settings Section */}
            <section className="space-y-3">
              <h3 className="text-sm font-medium text-gray-300">
                Hopper & Display Settings
              </h3>
              <div className="bg-gray-800/50 rounded-lg p-4 space-y-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm text-gray-400 mb-2">
                      Max active proposals:
                    </label>
                    <input
                      type="number"
                      min="1"
                      max="20"
                      value={maxActiveProposals}
                      onChange={(e) => setMaxActiveProposals(Number(e.target.value))}
                      className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Scout pauses when this limit is reached
                    </p>
                  </div>
                  <div>
                    <label className="block text-sm text-gray-400 mb-2">
                      Warning threshold:
                    </label>
                    <input
                      type="number"
                      min="1"
                      max={maxActiveProposals}
                      value={hopperWarningThreshold}
                      onChange={(e) => setHopperWarningThreshold(Number(e.target.value))}
                      className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      Show warning at this level
                    </p>
                  </div>
                </div>

                {/* Max Backlog Size */}
                <div>
                  <label className="block text-sm text-gray-400 mb-2">
                    Max unreviewed backlog:
                  </label>
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    value={maxBacklogSize}
                    onChange={(e) => setMaxBacklogSize(Number(e.target.value))}
                    className="w-full px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg text-white text-sm focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan"
                  />
                  <p className="text-xs text-gray-500 mt-1">
                    Scout skips runs when this many unreviewed opportunities are queued (0 = no limit)
                  </p>
                </div>

                {/* Toggle options */}
                <div className="space-y-3 pt-2">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={autoPauseDiscovery}
                      onChange={(e) => setAutoPauseDiscovery(e.target.checked)}
                      className="rounded border-gray-600 bg-gray-700 text-neon-cyan focus:ring-neon-cyan"
                    />
                    <span className="text-sm text-gray-300">
                      Auto-pause discovery when hopper is full
                    </span>
                  </label>
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={showUnlikelyTier}
                      onChange={(e) => setShowUnlikelyTier(e.target.checked)}
                      className="rounded border-gray-600 bg-gray-700 text-neon-cyan focus:ring-neon-cyan"
                    />
                    <span className="text-sm text-gray-300">
                      Show "Unlikely" tier opportunities
                    </span>
                  </label>
                </div>
              </div>
            </section>
          </div>
        )}

        {/* Footer */}
        <div className="sticky bottom-0 bg-gray-900 border-t border-gray-800 p-4 flex items-center justify-between">
          <button
            onClick={handleReset}
            className="px-4 py-2 text-gray-400 hover:text-white flex items-center gap-2 transition-colors"
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
              disabled={saveMutation.isPending}
              className="px-4 py-2 bg-neon-cyan hover:bg-neon-cyan/80 text-black font-medium rounded-lg flex items-center gap-2 transition-colors disabled:opacity-50"
            >
              {saveMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              Save Settings
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
