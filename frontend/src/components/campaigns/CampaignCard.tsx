import { Link } from 'react-router-dom';
import type { Campaign } from '@/types';
import { 
  DollarSign, 
  TrendingUp, 
  Calendar, 
  ChevronRight, 
  Play, 
  Pause, 
  CheckCircle2, 
  XCircle,
  Clock,
  AlertTriangle
} from 'lucide-react';

interface CampaignCardProps {
  campaign: Campaign;
}

const statusConfig: Record<string, { color: string; icon: React.ElementType; label: string }> = {
  initializing: { 
    color: 'bg-blue-500/20 text-blue-400 border-blue-500/30', 
    icon: Clock,
    label: 'Initializing'
  },
  waiting_for_inputs: { 
    color: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30', 
    icon: AlertTriangle,
    label: 'Waiting for Inputs'
  },
  active: { 
    color: 'bg-green-500/20 text-green-400 border-green-500/30', 
    icon: Play,
    label: 'Active'
  },
  paused: { 
    color: 'bg-orange-500/20 text-orange-400 border-orange-500/30', 
    icon: Pause,
    label: 'Paused'
  },
  completed: { 
    color: 'bg-neon-cyan/20 text-neon-cyan border-neon-cyan/30', 
    icon: CheckCircle2,
    label: 'Completed'
  },
  terminated: { 
    color: 'bg-red-500/20 text-red-400 border-red-500/30', 
    icon: XCircle,
    label: 'Terminated'
  },
  failed: { 
    color: 'bg-red-500/20 text-red-400 border-red-500/30', 
    icon: XCircle,
    label: 'Failed'
  },
};

export function CampaignCard({ campaign }: CampaignCardProps) {
  const status = statusConfig[campaign.status] || statusConfig.initializing;
  const StatusIcon = status.icon;
  
  const budgetUsedPercent = campaign.budget_allocated > 0 
    ? (campaign.budget_spent / campaign.budget_allocated) * 100 
    : 0;
  
  const progressPercent = campaign.tasks_total > 0 
    ? (campaign.tasks_completed / campaign.tasks_total) * 100 
    : 0;
  
  const profit = campaign.revenue_generated - campaign.budget_spent;
  const isProfitable = profit > 0;

  return (
    <Link
      to={`/campaigns/${campaign.id}`}
      className="block bg-gray-900/50 border border-gray-800 rounded-lg p-6 hover:border-neon-cyan/50 hover:shadow-glow-cyan transition-all group"
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <span
              className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${status.color}`}
            >
              <StatusIcon className="h-3.5 w-3.5" />
              {status.label}
            </span>
            {campaign.current_phase && (
              <span className="text-xs text-gray-500">
                Phase: {campaign.current_phase}
              </span>
            )}
          </div>
          <h3 className="text-lg font-semibold text-white group-hover:text-neon-cyan transition-colors">
            {campaign.proposal_title || `Campaign #${campaign.id.slice(0, 8)}`}
          </h3>
        </div>
        <ChevronRight className="h-5 w-5 text-gray-600 group-hover:text-neon-cyan transition-colors flex-shrink-0 ml-4" />
      </div>

      {/* Progress Bar */}
      <div className="mb-4">
        <div className="flex items-center justify-between text-xs text-gray-400 mb-1">
          <span>Progress</span>
          <span>{campaign.tasks_completed} / {campaign.tasks_total} tasks</span>
        </div>
        <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div 
            className="h-full bg-gradient-to-r from-neon-cyan to-neon-blue transition-all"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Financial Metrics */}
      <div className="grid grid-cols-3 gap-4 mb-4">
        <div className="text-center">
          <div className="flex items-center justify-center gap-1 text-gray-400 text-xs mb-1">
            <DollarSign className="h-3.5 w-3.5" />
            <span>Budget</span>
          </div>
          <p className="text-white font-medium">
            ${campaign.budget_spent.toLocaleString(undefined, { maximumFractionDigits: 0 })}
            <span className="text-gray-500 text-xs"> / ${campaign.budget_allocated.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span>
          </p>
          <div className="h-1 bg-gray-800 rounded-full mt-1 overflow-hidden">
            <div 
              className={`h-full transition-all ${budgetUsedPercent > 80 ? 'bg-red-500' : 'bg-neon-cyan'}`}
              style={{ width: `${Math.min(budgetUsedPercent, 100)}%` }}
            />
          </div>
        </div>
        
        <div className="text-center">
          <div className="flex items-center justify-center gap-1 text-gray-400 text-xs mb-1">
            <TrendingUp className="h-3.5 w-3.5" />
            <span>Revenue</span>
          </div>
          <p className="text-green-400 font-medium">
            ${campaign.revenue_generated.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
        </div>
        
        <div className="text-center">
          <div className="text-gray-400 text-xs mb-1">Net</div>
          <p className={`font-medium ${isProfitable ? 'text-green-400' : 'text-red-400'}`}>
            {isProfitable ? '+' : ''}${profit.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          </p>
        </div>
      </div>

      {/* Requirements Status */}
      {!campaign.all_requirements_met && campaign.status === 'waiting_for_inputs' && (
        <div className="flex items-center gap-2 px-3 py-2 bg-yellow-500/10 border border-yellow-500/20 rounded-lg text-yellow-400 text-sm">
          <AlertTriangle className="h-4 w-4" />
          <span>Waiting for your input to continue</span>
        </div>
      )}

      {/* Timestamps */}
      <div className="flex items-center justify-between mt-4 pt-4 border-t border-gray-800 text-xs text-gray-500">
        <div className="flex items-center gap-1.5">
          <Calendar className="h-3.5 w-3.5" />
          <span>Created {new Date(campaign.created_at).toLocaleDateString()}</span>
        </div>
        {campaign.last_activity_at && (
          <span>Active {new Date(campaign.last_activity_at).toLocaleDateString()}</span>
        )}
      </div>
    </Link>
  );
}
