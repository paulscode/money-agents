import { useState, useCallback } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Check,
  X,
  ExternalLink,
  Clock,
  DollarSign,
  Wrench,
  Zap,
  TrendingUp,
} from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import type { Opportunity, RankingTier, EffortLevel, TimeSensitivity } from '@/types/opportunity';

const TIER_CONFIG: Record<RankingTier, { label: string; color: string; bgColor: string }> = {
  top_pick: {
    label: '🏆 Top Pick',
    color: 'text-neon-cyan',
    bgColor: 'bg-neon-cyan/10 border-neon-cyan/30',
  },
  promising: {
    label: '⭐ Promising',
    color: 'text-green-400',
    bgColor: 'bg-green-500/10 border-green-500/30',
  },
  maybe: {
    label: '🤔 Maybe',
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/10 border-yellow-500/30',
  },
  unlikely: {
    label: '❓ Unlikely',
    color: 'text-gray-400',
    bgColor: 'bg-gray-500/10 border-gray-500/30',
  },
};

const TYPE_ICONS: Record<string, string> = {
  arbitrage: '📈',
  content: '✍️',
  service: '🛠️',
  product: '📦',
  automation: '⚙️',
  affiliate: '🤝',
  investment: '💰',
  other: '💡',
};

const EFFORT_LABELS: Record<EffortLevel, { label: string; color: string }> = {
  minimal: { label: 'Minimal', color: 'text-green-400' },
  moderate: { label: 'Moderate', color: 'text-yellow-400' },
  significant: { label: 'Significant', color: 'text-orange-400' },
  major: { label: 'Major', color: 'text-red-400' },
};

const TIME_LABELS: Record<TimeSensitivity, { label: string; color: string }> = {
  immediate: { label: '⚡ Immediate', color: 'text-red-400' },
  short: { label: '🔥 Short', color: 'text-orange-400' },
  medium: { label: '📅 Medium', color: 'text-yellow-400' },
  evergreen: { label: '🌲 Evergreen', color: 'text-green-400' },
};

interface OpportunityRowProps {
  opportunity: Opportunity;
  isSelected: boolean;
  onSelect: (id: string, selected: boolean) => void;
  onApprove: (id: string) => void;
  onDismiss: (id: string) => void;
  isApproving?: boolean;
  isDismissing?: boolean;
}

export function OpportunityRow({
  opportunity,
  isSelected,
  onSelect,
  onApprove,
  onDismiss,
  isApproving,
  isDismissing,
}: OpportunityRowProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const tier = opportunity.ranking_tier || 'maybe';
  const tierConfig = TIER_CONFIG[tier];
  const typeIcon = TYPE_ICONS[opportunity.opportunity_type] || '💡';
  const score = opportunity.overall_score
    ? Math.round(opportunity.overall_score * 100)
    : null;

  const handleCheckboxClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onSelect(opportunity.id, !isSelected);
    },
    [opportunity.id, isSelected, onSelect]
  );

  const handleApprove = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onApprove(opportunity.id);
    },
    [opportunity.id, onApprove]
  );

  const handleDismiss = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onDismiss(opportunity.id);
    },
    [opportunity.id, onDismiss]
  );

  const formatRevenue = (rev?: Opportunity['estimated_revenue_potential']) => {
    if (!rev) return null;
    const min = rev.min ? `$${rev.min.toLocaleString()}` : '';
    const max = rev.max ? `$${rev.max.toLocaleString()}` : '';
    if (min && max) return `${min} - ${max}`;
    if (min) return `${min}+`;
    if (max) return `up to ${max}`;
    return null;
  };

  const revenueStr = formatRevenue(opportunity.estimated_revenue_potential);

  return (
    <div
      className={`border-b border-gray-800 hover:bg-gray-800/30 transition-colors ${
        isSelected ? 'bg-neon-cyan/5' : ''
      }`}
    >
      {/* Main Row */}
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        {/* Checkbox */}
        <div
          onClick={handleCheckboxClick}
          className={`w-5 h-5 rounded border flex items-center justify-center cursor-pointer transition-colors ${
            isSelected
              ? 'bg-neon-cyan border-neon-cyan'
              : 'border-gray-600 hover:border-gray-400'
          }`}
        >
          {isSelected && <Check className="h-3 w-3 text-gray-900" />}
        </div>

        {/* Expand Arrow */}
        <div className="text-gray-500">
          {isExpanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </div>

        {/* Score */}
        <div className="w-12 text-center">
          {score !== null ? (
            <span
              className={`text-sm font-mono font-bold ${
                score >= 80
                  ? 'text-neon-cyan'
                  : score >= 60
                  ? 'text-green-400'
                  : score >= 40
                  ? 'text-yellow-400'
                  : 'text-gray-400'
              }`}
            >
              {score}
            </span>
          ) : (
            <span className="text-gray-500 text-xs">--</span>
          )}
        </div>

        {/* Tier Badge */}
        <div className="w-28">
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${tierConfig.bgColor} ${tierConfig.color}`}
          >
            {tierConfig.label}
          </span>
        </div>

        {/* Type */}
        <div className="w-8 text-center" title={opportunity.opportunity_type}>
          <span className="text-lg">{typeIcon}</span>
        </div>

        {/* Title & Summary */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-white truncate">
              {opportunity.title}
            </h3>
            {opportunity.time_sensitivity && (
              <span
                className={`text-xs ${
                  TIME_LABELS[opportunity.time_sensitivity].color
                }`}
              >
                {TIME_LABELS[opportunity.time_sensitivity].label}
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 truncate">{opportunity.summary}</p>
        </div>

        {/* Revenue Potential */}
        <div className="w-32 text-right">
          {revenueStr && (
            <span className="text-sm text-green-400 font-medium">
              {revenueStr}
            </span>
          )}
          {opportunity.estimated_revenue_potential?.timeframe && (
            <span className="text-xs text-gray-500 block">
              /{opportunity.estimated_revenue_potential.timeframe}
            </span>
          )}
        </div>

        {/* Effort */}
        <div className="w-20 text-center">
          {opportunity.estimated_effort && (
            <span
              className={`text-xs ${
                EFFORT_LABELS[opportunity.estimated_effort].color
              }`}
            >
              {EFFORT_LABELS[opportunity.estimated_effort].label}
            </span>
          )}
        </div>

        {/* Age */}
        <div className="w-24 text-right">
          <span className="text-xs text-gray-500">
            {formatDistanceToNow(new Date(opportunity.discovered_at), {
              addSuffix: true,
            })}
          </span>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 w-20 justify-end">
          <button
            onClick={handleApprove}
            disabled={isApproving || isDismissing}
            className="p-1.5 rounded bg-green-500/10 text-green-400 hover:bg-green-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title="Approve (A)"
          >
            {isApproving ? (
              <Zap className="h-4 w-4 animate-pulse" />
            ) : (
              <Check className="h-4 w-4" />
            )}
          </button>
          <button
            onClick={handleDismiss}
            disabled={isApproving || isDismissing}
            className="p-1.5 rounded bg-red-500/10 text-red-400 hover:bg-red-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            title="Dismiss (D)"
          >
            {isDismissing ? (
              <Zap className="h-4 w-4 animate-pulse" />
            ) : (
              <X className="h-4 w-4" />
            )}
          </button>
        </div>
      </div>

      {/* Expanded Details */}
      {isExpanded && (
        <div className="px-4 pb-4 pl-[72px] border-t border-gray-800/50 bg-gray-900/30">
          <div className="grid grid-cols-2 gap-6 py-3">
            {/* Left Column */}
            <div className="space-y-3">
              {/* Assessment */}
              {opportunity.initial_assessment && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1 flex items-center gap-1">
                    <TrendingUp className="h-3 w-3" />
                    Initial Assessment
                  </h4>
                  <p className="text-sm text-gray-300">
                    {opportunity.initial_assessment}
                  </p>
                </div>
              )}

              {/* Detailed Analysis */}
              {opportunity.detailed_analysis && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1">
                    Analysis
                  </h4>
                  <p className="text-sm text-gray-300 line-clamp-3">
                    {opportunity.detailed_analysis}
                  </p>
                </div>
              )}

              {/* Source URLs */}
              {opportunity.source_urls && opportunity.source_urls.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1 flex items-center gap-1">
                    <ExternalLink className="h-3 w-3" />
                    Sources
                  </h4>
                  <div className="flex flex-wrap gap-2">
                    {opportunity.source_urls.slice(0, 3).map((url, i) => {
                      // SA3-M7: Validate URL scheme before rendering as href
                      let hostname = url;
                      try { hostname = new URL(url).hostname; } catch { /* use raw */ }
                      if (!/^https?:\/\//i.test(url)) return null;
                      return (
                      <a
                        key={i}
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs text-neon-cyan hover:underline truncate max-w-[200px]"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {hostname}
                      </a>
                      );
                    })}
                    {opportunity.source_urls.length > 3 && (
                      <span className="text-xs text-gray-500">
                        +{opportunity.source_urls.length - 3} more
                      </span>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Right Column */}
            <div className="space-y-3">
              {/* Required Tools */}
              {opportunity.required_tools &&
                opportunity.required_tools.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium text-gray-400 mb-1 flex items-center gap-1">
                      <Wrench className="h-3 w-3" />
                      Required Tools
                    </h4>
                    <div className="flex flex-wrap gap-1">
                      {opportunity.required_tools.map((tool, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 text-xs bg-gray-700 text-gray-300 rounded"
                        >
                          {tool}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              {/* Cost Estimate */}
              {opportunity.estimated_cost && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1 flex items-center gap-1">
                    <DollarSign className="h-3 w-3" />
                    Estimated Cost
                  </h4>
                  <div className="text-sm text-gray-300">
                    {opportunity.estimated_cost.upfront && (
                      <span>
                        Upfront: ${opportunity.estimated_cost.upfront.toLocaleString()}
                      </span>
                    )}
                    {opportunity.estimated_cost.ongoing && (
                      <span className="ml-3">
                        Ongoing: ${opportunity.estimated_cost.ongoing.toLocaleString()}/mo
                      </span>
                    )}
                  </div>
                </div>
              )}

              {/* Score Breakdown */}
              {opportunity.score_breakdown && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1">
                    Score Breakdown
                  </h4>
                  <div className="grid grid-cols-2 gap-1 text-xs">
                    {Object.entries(opportunity.score_breakdown).map(
                      ([key, value]) => (
                        <div
                          key={key}
                          className="flex items-center justify-between"
                        >
                          <span className="text-gray-500 capitalize">
                            {key.replace(/_/g, ' ')}
                          </span>
                          <span className="text-gray-300 font-mono">
                            {Math.round(value * 100)}%
                          </span>
                        </div>
                      )
                    )}
                  </div>
                </div>
              )}

              {/* Blocking Requirements */}
              {opportunity.blocking_requirements &&
                opportunity.blocking_requirements.length > 0 && (
                  <div>
                    <h4 className="text-xs font-medium text-red-400 mb-1 flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      Blockers
                    </h4>
                    <ul className="text-xs text-gray-400 space-y-0.5">
                      {opportunity.blocking_requirements.map((req, i) => (
                        <li key={i}>• {req}</li>
                      ))}
                    </ul>
                  </div>
                )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
