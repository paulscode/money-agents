import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { learningService, type SuggestionAction } from '@/services/learning';
import type { ProactiveSuggestion, SuggestionType, SuggestionStatus } from '@/types';
import {
  Loader2,
  Lightbulb,
  TrendingUp,
  AlertTriangle,
  Sparkles,
  DollarSign,
  Clock,
  Shield,
  Check,
  X,
  ChevronDown,
  ChevronRight,
  Zap,
  RefreshCw,
  Brain,
} from 'lucide-react';

const suggestionTypeConfig: Record<SuggestionType, { icon: React.ElementType; label: string; color: string; bgColor: string }> = {
  optimization: { icon: TrendingUp, label: 'Optimization', color: 'text-blue-400', bgColor: 'bg-blue-500/20' },
  warning: { icon: AlertTriangle, label: 'Warning', color: 'text-yellow-400', bgColor: 'bg-yellow-500/20' },
  opportunity: { icon: Sparkles, label: 'Opportunity', color: 'text-purple-400', bgColor: 'bg-purple-500/20' },
  cost_saving: { icon: DollarSign, label: 'Cost Saving', color: 'text-green-400', bgColor: 'bg-green-500/20' },
  time_saving: { icon: Clock, label: 'Time Saving', color: 'text-neon-cyan', bgColor: 'bg-neon-cyan/20' },
  risk_mitigation: { icon: Shield, label: 'Risk Mitigation', color: 'text-orange-400', bgColor: 'bg-orange-500/20' },
};

const urgencyConfig: Record<string, { label: string; color: string; pulse: boolean }> = {
  critical: { label: 'Critical', color: 'bg-red-500', pulse: true },
  high: { label: 'High', color: 'bg-orange-500', pulse: false },
  medium: { label: 'Medium', color: 'bg-yellow-500', pulse: false },
  low: { label: 'Low', color: 'bg-blue-500', pulse: false },
};

const statusConfig: Record<SuggestionStatus, { label: string; color: string; bgColor: string }> = {
  pending: { label: 'Pending', color: 'text-yellow-400', bgColor: 'bg-yellow-500/20' },
  accepted: { label: 'Accepted', color: 'text-green-400', bgColor: 'bg-green-500/20' },
  rejected: { label: 'Rejected', color: 'text-red-400', bgColor: 'bg-red-500/20' },
  auto_applied: { label: 'Auto-Applied', color: 'text-neon-cyan', bgColor: 'bg-neon-cyan/20' },
  expired: { label: 'Expired', color: 'text-gray-400', bgColor: 'bg-gray-500/20' },
};

interface SuggestionCardProps {
  suggestion: ProactiveSuggestion;
  onAccept?: () => void;
  onReject?: () => void;
  isResponding?: boolean;
}

function SuggestionCard({ suggestion, onAccept, onReject, isResponding }: SuggestionCardProps) {
  const [expanded, setExpanded] = useState(suggestion.status === 'pending');
  const [feedback, setFeedback] = useState('');
  
  const typeConfig = suggestionTypeConfig[suggestion.suggestion_type];
  const urgencyConf = urgencyConfig[suggestion.urgency];
  const statusConf = statusConfig[suggestion.status];
  const TypeIcon = typeConfig.icon;
  
  const isPending = suggestion.status === 'pending' && !suggestion.is_expired;
  const confidencePercent = Math.round(suggestion.confidence * 100);
  
  return (
    <div className={`bg-gray-900/50 border rounded-lg overflow-hidden transition-all ${
      suggestion.status === 'pending' 
        ? 'border-yellow-500/30 ring-1 ring-yellow-500/10' 
        : 'border-gray-800 hover:border-gray-700'
    }`}>
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full px-4 py-3 flex items-center gap-3 text-left hover:bg-gray-800/30 transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 text-gray-500 flex-shrink-0" />
        ) : (
          <ChevronRight className="h-4 w-4 text-gray-500 flex-shrink-0" />
        )}
        
        {/* Urgency Indicator */}
        <div className="relative">
          <div className={`w-2 h-2 rounded-full ${urgencyConf.color}`} />
          {urgencyConf.pulse && (
            <div className={`absolute inset-0 w-2 h-2 rounded-full ${urgencyConf.color} animate-ping`} />
          )}
        </div>
        
        <div className={`p-2 rounded-lg ${typeConfig.bgColor} ${typeConfig.color}`}>
          <TypeIcon className="h-4 w-4" />
        </div>
        
        <div className="flex-1 min-w-0">
          <h4 className="text-sm font-medium text-white truncate">
            {suggestion.title}
          </h4>
          <p className="text-xs text-gray-500">{typeConfig.label}</p>
        </div>
        
        <div className="flex items-center gap-2">
          {/* Status Badge */}
          <span className={`px-2 py-0.5 rounded text-xs ${statusConf.bgColor} ${statusConf.color}`}>
            {statusConf.label}
          </span>
          
          {/* Confidence */}
          <span className="text-xs text-gray-500">
            {confidencePercent}% confident
          </span>
        </div>
      </button>
      
      {/* Expanded Content */}
      {expanded && (
        <div className="px-4 pb-4 space-y-4 border-t border-gray-800">
          <p className="text-sm text-gray-400 pt-3">{suggestion.description}</p>
          
          {/* Recommended Action */}
          <div className="space-y-2">
            <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
              <Zap className="h-3 w-3" />
              Recommended Action
            </h5>
            <div className="p-3 bg-gray-800/50 rounded-lg">
              <pre className="text-sm text-gray-300 whitespace-pre-wrap">
                {JSON.stringify(suggestion.recommended_action, null, 2)}
              </pre>
            </div>
          </div>
          
          {/* Benefits */}
          <div className="grid grid-cols-2 gap-3">
            {suggestion.estimated_benefit && (
              <div className="p-2 bg-green-500/10 border border-green-500/20 rounded-lg">
                <p className="text-xs text-gray-500">Expected Benefit</p>
                <p className="text-sm font-medium text-green-400">
                  {suggestion.estimated_benefit}
                </p>
              </div>
            )}
            {suggestion.estimated_cost !== null && suggestion.estimated_cost !== undefined && (
              <div className="p-2 bg-orange-500/10 border border-orange-500/20 rounded-lg">
                <p className="text-xs text-gray-500">Estimated Cost</p>
                <p className="text-sm font-medium text-orange-400">
                  ${suggestion.estimated_cost.toFixed(2)}
                </p>
              </div>
            )}
          </div>
          
          {/* Evidence */}
          {suggestion.evidence && Object.keys(suggestion.evidence).length > 0 && (
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
                <Brain className="h-3 w-3" />
                Supporting Evidence
              </h5>
              <div className="p-2 bg-gray-900/70 rounded-lg">
                <pre className="text-xs text-gray-400 overflow-x-auto max-h-32">
                  {JSON.stringify(suggestion.evidence, null, 2)}
                </pre>
              </div>
            </div>
          )}
          
          {/* Based on Patterns/Lessons */}
          {(suggestion.based_on_patterns?.length || suggestion.based_on_lessons?.length) && (
            <div className="flex flex-wrap gap-2 text-xs">
              {suggestion.based_on_patterns?.map((_, i) => (
                <span key={`p-${i}`} className="px-2 py-0.5 bg-neon-cyan/10 text-neon-cyan rounded">
                  Pattern
                </span>
              ))}
              {suggestion.based_on_lessons?.map((_, i) => (
                <span key={`l-${i}`} className="px-2 py-0.5 bg-orange-500/10 text-orange-400 rounded">
                  Lesson
                </span>
              ))}
            </div>
          )}
          
          {/* Auto-apply indicator */}
          {suggestion.can_auto_apply && suggestion.status === 'pending' && (
            <div className="p-2 bg-neon-cyan/10 border border-neon-cyan/20 rounded-lg flex items-center gap-2">
              <RefreshCw className="h-4 w-4 text-neon-cyan" />
              <span className="text-sm text-neon-cyan">
                This suggestion can be applied automatically
              </span>
            </div>
          )}
          
          {/* Action Buttons for Pending Suggestions */}
          {isPending && onAccept && onReject && (
            <div className="space-y-3 pt-2 border-t border-gray-800">
              <textarea
                value={feedback}
                onChange={(e) => setFeedback(e.target.value)}
                placeholder="Optional feedback..."
                className="w-full px-3 py-2 bg-gray-900/50 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:border-neon-cyan text-sm resize-none"
                rows={2}
              />
              <div className="flex gap-2">
                <button
                  onClick={onAccept}
                  disabled={isResponding}
                  className="flex-1 btn-primary flex items-center justify-center gap-2"
                >
                  {isResponding ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                  Accept
                </button>
                <button
                  onClick={onReject}
                  disabled={isResponding}
                  className="flex-1 px-4 py-2 bg-red-500/20 text-red-400 rounded-lg hover:bg-red-500/30 transition-colors flex items-center justify-center gap-2"
                >
                  {isResponding ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <X className="h-4 w-4" />
                  )}
                  Reject
                </button>
              </div>
            </div>
          )}
          
          {/* User Feedback (for responded suggestions) */}
          {suggestion.user_feedback && (
            <div className="p-2 bg-gray-800/50 rounded-lg">
              <p className="text-xs text-gray-500 mb-1">Your Feedback</p>
              <p className="text-sm text-gray-300">{suggestion.user_feedback}</p>
            </div>
          )}
          
          {/* Footer */}
          <div className="flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-800">
            <span>Created: {new Date(suggestion.created_at).toLocaleString()}</span>
            {suggestion.expires_at && (
              <span className={suggestion.is_expired ? 'text-red-400' : ''}>
                {suggestion.is_expired ? 'Expired' : `Expires: ${new Date(suggestion.expires_at).toLocaleString()}`}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface SuggestionPanelProps {
  campaignId: string;
}

export function SuggestionPanel({ campaignId }: SuggestionPanelProps) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<string>('');

  const { data, isLoading, error } = useQuery({
    queryKey: ['suggestions', campaignId, filter],
    queryFn: () => learningService.getCampaignSuggestions(campaignId, {
      status_filter: filter || undefined,
      limit: 50,
    }),
    refetchInterval: 30000, // Poll every 30 seconds for new suggestions
  });

  const respondMutation = useMutation({
    mutationFn: ({ suggestionId, action }: { suggestionId: string; action: SuggestionAction }) =>
      learningService.respondToSuggestion(suggestionId, action),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['suggestions', campaignId] });
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="h-6 w-6 animate-spin text-neon-cyan" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-center py-8 text-red-400">
        <AlertTriangle className="h-8 w-8 mx-auto mb-2" />
        <p>Failed to load suggestions</p>
      </div>
    );
  }

  const suggestions = data?.suggestions ?? [];
  const pendingCount = data?.pending_count ?? 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <Lightbulb className="h-5 w-5 text-neon-cyan" />
          AI Suggestions
          {pendingCount > 0 && (
            <span className="px-2 py-0.5 bg-yellow-500/20 text-yellow-400 rounded-full text-sm animate-pulse">
              {pendingCount} pending
            </span>
          )}
        </h3>
        
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="px-3 py-1.5 bg-gray-900/50 border border-gray-700 rounded-lg text-sm text-white focus:border-neon-cyan"
        >
          <option value="">All Suggestions</option>
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="rejected">Rejected</option>
          <option value="auto_applied">Auto-Applied</option>
        </select>
      </div>

      {/* Suggestions List */}
      {suggestions.length > 0 ? (
        <div className="space-y-2">
          {suggestions.map((suggestion) => (
            <SuggestionCard
              key={suggestion.id}
              suggestion={suggestion}
              onAccept={() => respondMutation.mutate({ 
                suggestionId: suggestion.id, 
                action: { action: 'accept' } 
              })}
              onReject={() => respondMutation.mutate({ 
                suggestionId: suggestion.id, 
                action: { action: 'reject' } 
              })}
              isResponding={respondMutation.isPending}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-gray-400">
          <Lightbulb className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="text-lg mb-1">No suggestions yet</p>
          <p className="text-sm text-gray-500">
            AI will generate suggestions as the campaign progresses
          </p>
        </div>
      )}
    </div>
  );
}
