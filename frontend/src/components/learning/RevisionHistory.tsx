import { useQuery } from '@tanstack/react-query';
import { learningService } from '@/services/learning';
import type { PlanRevision, RevisionTrigger } from '@/types';
import {
  Loader2,
  GitBranch,
  AlertTriangle,
  XCircle,
  Layers,
  DollarSign,
  MessageSquare,
  Info,
  TrendingUp,
  ExternalLink,
  CheckCircle2,
  Clock,
  HelpCircle,
  ChevronDown,
  ChevronRight,
  User,
  Bot,
  Plus,
  Minus,
  Edit3,
} from 'lucide-react';
import { useState } from 'react';

const triggerConfig: Record<RevisionTrigger, { icon: React.ElementType; label: string; color: string; bgColor: string }> = {
  task_failure: { icon: XCircle, label: 'Task Failure', color: 'text-red-400', bgColor: 'bg-red-500/20' },
  stream_blocked: { icon: Layers, label: 'Stream Blocked', color: 'text-orange-400', bgColor: 'bg-orange-500/20' },
  budget_concern: { icon: DollarSign, label: 'Budget Concern', color: 'text-yellow-400', bgColor: 'bg-yellow-500/20' },
  user_feedback: { icon: MessageSquare, label: 'User Feedback', color: 'text-blue-400', bgColor: 'bg-blue-500/20' },
  new_information: { icon: Info, label: 'New Information', color: 'text-purple-400', bgColor: 'bg-purple-500/20' },
  optimization: { icon: TrendingUp, label: 'Optimization', color: 'text-green-400', bgColor: 'bg-green-500/20' },
  external_change: { icon: ExternalLink, label: 'External Change', color: 'text-neon-cyan', bgColor: 'bg-neon-cyan/20' },
};

const outcomeConfig: Record<string, { icon: React.ElementType; label: string; color: string }> = {
  success: { icon: CheckCircle2, label: 'Successful', color: 'text-green-400' },
  pending: { icon: Clock, label: 'Pending', color: 'text-yellow-400' },
  failure: { icon: XCircle, label: 'Failed', color: 'text-red-400' },
  unknown: { icon: HelpCircle, label: 'Unknown', color: 'text-gray-400' },
};

interface RevisionCardProps {
  revision: PlanRevision;
  isLatest: boolean;
}

function RevisionCard({ revision, isLatest }: RevisionCardProps) {
  const [expanded, setExpanded] = useState(isLatest);
  
  const triggerConf = triggerConfig[revision.trigger];
  const TriggerIcon = triggerConf.icon;
  
  const outcomeKey = revision.outcome_assessed 
    ? (revision.outcome_success ? 'success' : 'failure')
    : 'pending';
  const outcomeConf = outcomeConfig[outcomeKey];
  const OutcomeIcon = outcomeConf.icon;
  
  const totalChanges = revision.tasks_added + revision.tasks_removed + revision.tasks_modified + 
                       revision.streams_added + revision.streams_removed;
  
  return (
    <div className="relative">
      {/* Timeline Line */}
      <div className="absolute left-5 top-10 bottom-0 w-0.5 bg-gray-800" />
      
      <div className={`bg-gray-900/50 border rounded-lg overflow-hidden ml-10 ${
        isLatest ? 'border-neon-cyan/30 ring-1 ring-neon-cyan/10' : 'border-gray-800'
      }`}>
        {/* Timeline Node */}
        <div className={`absolute left-3 top-4 w-5 h-5 rounded-full flex items-center justify-center ${
          isLatest ? 'bg-neon-cyan' : 'bg-gray-700'
        }`}>
          <span className="text-xs font-bold text-gray-900">
            {revision.revision_number}
          </span>
        </div>
        
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
          
          <div className={`p-1.5 rounded ${triggerConf.bgColor}`}>
            <TriggerIcon className={`h-4 w-4 ${triggerConf.color}`} />
          </div>
          
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h4 className="text-sm font-medium text-white truncate">
                Revision #{revision.revision_number}
              </h4>
              {isLatest && (
                <span className="px-1.5 py-0.5 bg-neon-cyan/20 text-neon-cyan rounded text-xs">
                  Latest
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500">{triggerConf.label}</p>
          </div>
          
          <div className="flex items-center gap-3">
            {/* Initiator */}
            <div className="flex items-center gap-1 text-xs text-gray-500">
              {revision.initiated_by === 'agent' ? (
                <Bot className="h-3 w-3" />
              ) : (
                <User className="h-3 w-3" />
              )}
              <span className="capitalize">{revision.initiated_by}</span>
            </div>
            
            {/* Outcome */}
            <div className="flex items-center gap-1">
              <OutcomeIcon className={`h-4 w-4 ${outcomeConf.color}`} />
            </div>
          </div>
        </button>
        
        {/* Expanded Content */}
        {expanded && (
          <div className="px-4 pb-4 space-y-4 border-t border-gray-800">
            {/* Trigger Details */}
            <div className="pt-3">
              <p className="text-sm text-gray-400">{revision.trigger_details}</p>
            </div>
            
            {/* Changes Summary */}
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-gray-400 uppercase flex items-center gap-1">
                <Edit3 className="h-3 w-3" />
                Changes ({totalChanges})
              </h5>
              <p className="text-sm text-white bg-gray-800/50 rounded-lg p-3">
                {revision.changes_summary}
              </p>
              
              {/* Change Stats */}
              <div className="flex flex-wrap gap-2">
                {revision.tasks_added > 0 && (
                  <span className="px-2 py-1 bg-green-500/20 text-green-400 rounded text-xs flex items-center gap-1">
                    <Plus className="h-3 w-3" />
                    {revision.tasks_added} tasks added
                  </span>
                )}
                {revision.tasks_removed > 0 && (
                  <span className="px-2 py-1 bg-red-500/20 text-red-400 rounded text-xs flex items-center gap-1">
                    <Minus className="h-3 w-3" />
                    {revision.tasks_removed} tasks removed
                  </span>
                )}
                {revision.tasks_modified > 0 && (
                  <span className="px-2 py-1 bg-yellow-500/20 text-yellow-400 rounded text-xs flex items-center gap-1">
                    <Edit3 className="h-3 w-3" />
                    {revision.tasks_modified} tasks modified
                  </span>
                )}
                {revision.streams_added > 0 && (
                  <span className="px-2 py-1 bg-blue-500/20 text-blue-400 rounded text-xs flex items-center gap-1">
                    <Plus className="h-3 w-3" />
                    {revision.streams_added} streams added
                  </span>
                )}
                {revision.streams_removed > 0 && (
                  <span className="px-2 py-1 bg-orange-500/20 text-orange-400 rounded text-xs flex items-center gap-1">
                    <Minus className="h-3 w-3" />
                    {revision.streams_removed} streams removed
                  </span>
                )}
              </div>
            </div>
            
            {/* Reasoning */}
            <div className="space-y-2">
              <h5 className="text-xs font-medium text-gray-400 uppercase">Reasoning</h5>
              <p className="text-sm text-gray-300">{revision.reasoning}</p>
            </div>
            
            {/* Expected Improvement */}
            {revision.expected_improvement && (
              <div className="p-2 bg-neon-cyan/10 border border-neon-cyan/20 rounded-lg">
                <p className="text-xs text-gray-500 mb-1">Expected Improvement</p>
                <p className="text-sm text-neon-cyan">{revision.expected_improvement}</p>
              </div>
            )}
            
            {/* Outcome (if assessed) */}
            {revision.outcome_assessed && (
              <div className={`p-3 rounded-lg ${
                revision.outcome_success 
                  ? 'bg-green-500/10 border border-green-500/20' 
                  : 'bg-red-500/10 border border-red-500/20'
              }`}>
                <div className="flex items-center gap-2 mb-1">
                  <OutcomeIcon className={`h-4 w-4 ${outcomeConf.color}`} />
                  <span className={`text-sm font-medium ${outcomeConf.color}`}>
                    {outcomeConf.label}
                  </span>
                </div>
                {revision.outcome_notes && (
                  <p className="text-sm text-gray-400">{revision.outcome_notes}</p>
                )}
              </div>
            )}
            
            {/* Approval Status */}
            {revision.approved_by_user && (
              <div className="flex items-center gap-2 text-sm text-green-400">
                <CheckCircle2 className="h-4 w-4" />
                <span>Approved by user</span>
              </div>
            )}
            
            {/* Footer */}
            <div className="flex items-center justify-between text-xs text-gray-500 pt-2 border-t border-gray-800">
              <span>Created: {new Date(revision.created_at).toLocaleString()}</span>
              {revision.outcome_assessed_at && (
                <span>Assessed: {new Date(revision.outcome_assessed_at).toLocaleString()}</span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

interface RevisionHistoryProps {
  campaignId: string;
}

export function RevisionHistory({ campaignId }: RevisionHistoryProps) {
  const { data: revisions, isLoading, error } = useQuery({
    queryKey: ['revisions', campaignId],
    queryFn: () => learningService.getCampaignRevisions(campaignId),
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
        <p>Failed to load revision history</p>
      </div>
    );
  }

  const sortedRevisions = [...(revisions ?? [])].reverse(); // Show newest first

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold text-white flex items-center gap-2">
          <GitBranch className="h-5 w-5 text-neon-cyan" />
          Plan Evolution
        </h3>
        <span className="text-sm text-gray-500">
          {sortedRevisions.length} revision{sortedRevisions.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Revision Timeline */}
      {sortedRevisions.length > 0 ? (
        <div className="space-y-4">
          {sortedRevisions.map((revision, index) => (
            <RevisionCard
              key={revision.id}
              revision={revision}
              isLatest={index === 0}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-gray-400">
          <GitBranch className="h-12 w-12 mx-auto mb-3 opacity-30" />
          <p className="text-lg mb-1">No plan revisions</p>
          <p className="text-sm text-gray-500">
            Revisions are recorded when the execution plan changes
          </p>
        </div>
      )}
    </div>
  );
}
