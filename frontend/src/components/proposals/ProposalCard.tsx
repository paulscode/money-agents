import { Link } from 'react-router-dom';
import type { Proposal } from '@/types';
import { DollarSign, TrendingUp, AlertTriangle, Calendar, ChevronRight, MessageCircle } from 'lucide-react';

interface ProposalCardProps {
  proposal: Proposal;
  unreadCount?: number;
}

const statusColors: Record<string, string> = {
  pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  under_review: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  approved: 'bg-green-500/20 text-green-400 border-green-500/30',
  rejected: 'bg-red-500/20 text-red-400 border-red-500/30',
  deferred: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  changes_requested: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
};

const riskColors: Record<string, string> = {
  low: 'text-green-400',
  medium: 'text-yellow-400',
  high: 'text-red-400',
};

export function ProposalCard({ proposal, unreadCount = 0 }: ProposalCardProps) {
  return (
    <Link
      to={`/proposals/${proposal.id}`}
      className="block bg-gray-900/50 border border-gray-800 rounded-lg p-6 hover:border-neon-cyan/50 hover:shadow-glow-cyan transition-all group relative"
    >
      {/* Unread badge */}
      {unreadCount > 0 && (
        <div className="absolute -top-2 -right-2 flex items-center gap-1.5 bg-neon-cyan/20 border border-neon-cyan px-2.5 py-1 rounded-full">
          <MessageCircle className="h-3.5 w-3.5 text-neon-cyan" />
          <span className="text-xs font-bold text-neon-cyan">{unreadCount}</span>
        </div>
      )}
      
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <h3 className="text-xl font-semibold text-white group-hover:text-neon-cyan transition-colors mb-2">
            {proposal.title}
          </h3>
          <p className="text-gray-400 text-sm line-clamp-2">
            {proposal.summary}
          </p>
        </div>
        <ChevronRight className="h-5 w-5 text-gray-600 group-hover:text-neon-cyan transition-colors flex-shrink-0 ml-4" />
      </div>

      <div className="flex items-center gap-4 mb-4 text-sm">
        <div className="flex items-center gap-1.5">
          <DollarSign className="h-4 w-4 text-neon-cyan" />
          <span className="text-gray-300">${proposal.initial_budget.toLocaleString()}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <AlertTriangle className={`h-4 w-4 ${riskColors[proposal.risk_level]}`} />
          <span className="text-gray-300 capitalize">{proposal.risk_level} Risk</span>
        </div>
        {(() => {
          const returns = proposal.expected_returns;
          if (!returns) return null;
          let displayText: string | null = null;
          // Handle refined format: monthly_min/monthly_max
          if (returns.monthly_min !== undefined || returns.monthly_max !== undefined) {
            const min = returns.monthly_min ?? 0;
            const max = returns.monthly_max ?? min;
            displayText = min === max ? `$${min.toLocaleString()}/mo` : `$${min.toLocaleString()}-${max.toLocaleString()}/mo`;
          }
          // Handle original opportunity format: min/max
          else if (returns.min !== undefined || returns.max !== undefined) {
            const min = returns.min ?? 0;
            const max = returns.max ?? min;
            displayText = min === max ? `$${min.toLocaleString()}/mo` : `$${min.toLocaleString()}-${max.toLocaleString()}/mo`;
          }
          // Handle simple monthly format
          else if (returns.monthly) {
            displayText = `$${returns.monthly.toLocaleString()}/mo`;
          }
          if (!displayText) return null;
          return (
            <div className="flex items-center gap-1.5">
              <TrendingUp className="h-4 w-4 text-green-400" />
              <span className="text-gray-300">{displayText}</span>
            </div>
          );
        })()}
      </div>

      <div className="flex items-center justify-between">
        <span
          className={`px-3 py-1 rounded-full text-xs font-medium border ${
            statusColors[proposal.status] || statusColors.pending
          }`}
        >
          {proposal.status.replace('_', ' ').toUpperCase()}
        </span>
        <div className="flex items-center gap-1.5 text-xs text-gray-500">
          <Calendar className="h-3.5 w-3.5" />
          <span>{new Date(proposal.submitted_at).toLocaleDateString()}</span>
        </div>
      </div>
    </Link>
  );
}
