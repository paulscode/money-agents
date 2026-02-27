import type { TaskStream } from '@/types';
import { 
  CheckCircle2, 
  Clock, 
  AlertTriangle, 
  Play, 
  Pause,
  XCircle,
  ChevronDown,
  ChevronRight
} from 'lucide-react';
import { useState } from 'react';

interface StreamProgressCardProps {
  stream: TaskStream;
  isExpanded?: boolean;
  onToggle?: () => void;
}

const statusConfig: Record<string, { 
  color: string; 
  bgColor: string; 
  icon: React.ElementType; 
  label: string 
}> = {
  pending: {
    color: 'text-gray-400',
    bgColor: 'bg-gray-500/20 border-gray-500/30',
    icon: Clock,
    label: 'Pending'
  },
  blocked: {
    color: 'text-yellow-400',
    bgColor: 'bg-yellow-500/20 border-yellow-500/30',
    icon: AlertTriangle,
    label: 'Blocked'
  },
  ready: {
    color: 'text-blue-400',
    bgColor: 'bg-blue-500/20 border-blue-500/30',
    icon: Play,
    label: 'Ready'
  },
  running: {
    color: 'text-green-400',
    bgColor: 'bg-green-500/20 border-green-500/30',
    icon: Play,
    label: 'Running'
  },
  completed: {
    color: 'text-neon-cyan',
    bgColor: 'bg-neon-cyan/20 border-neon-cyan/30',
    icon: CheckCircle2,
    label: 'Completed'
  },
  failed: {
    color: 'text-red-400',
    bgColor: 'bg-red-500/20 border-red-500/30',
    icon: XCircle,
    label: 'Failed'
  }
};

export function StreamProgressCard({ stream, isExpanded = false, onToggle }: StreamProgressCardProps) {
  const [localExpanded, setLocalExpanded] = useState(isExpanded);
  const expanded = onToggle ? isExpanded : localExpanded;
  const handleToggle = onToggle || (() => setLocalExpanded(!localExpanded));
  
  const status = statusConfig[stream.status] || statusConfig.pending;
  const StatusIcon = status.icon;
  
  const progressPercent = stream.tasks_total > 0 
    ? (stream.tasks_completed / stream.tasks_total) * 100 
    : 0;

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg overflow-hidden hover:border-gray-700 transition-colors">
      {/* Header */}
      <button
        onClick={handleToggle}
        className="w-full px-4 py-3 flex items-center justify-between text-left hover:bg-gray-800/30 transition-colors"
      >
        <div className="flex items-center gap-3 flex-1 min-w-0">
          {/* Expand/Collapse */}
          {expanded ? (
            <ChevronDown className="h-4 w-4 text-gray-500 flex-shrink-0" />
          ) : (
            <ChevronRight className="h-4 w-4 text-gray-500 flex-shrink-0" />
          )}
          
          {/* Stream Name */}
          <div className="flex-1 min-w-0">
            <h4 className="text-sm font-medium text-white truncate">
              {stream.name.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
            </h4>
            {stream.description && (
              <p className="text-xs text-gray-500 truncate">{stream.description}</p>
            )}
          </div>
        </div>
        
        {/* Status Badge */}
        <div className="flex items-center gap-3 flex-shrink-0">
          <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-medium border ${status.bgColor} ${status.color}`}>
            <StatusIcon className="h-3 w-3" />
            {status.label}
          </span>
          
          {/* Progress */}
          <span className="text-xs text-gray-400 w-16 text-right">
            {stream.tasks_completed}/{stream.tasks_total}
          </span>
        </div>
      </button>
      
      {/* Progress Bar */}
      <div className="px-4 pb-2">
        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div 
            className={`h-full transition-all duration-300 ${
              stream.status === 'completed' ? 'bg-neon-cyan' :
              stream.status === 'failed' ? 'bg-red-500' :
              stream.status === 'running' ? 'bg-green-500' :
              'bg-blue-500'
            }`}
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>
      
      {/* Expanded Content */}
      {expanded && (
        <div className="px-4 pb-4 border-t border-gray-800">
          {/* Stats Row */}
          <div className="flex items-center gap-4 py-3 text-xs">
            <div className="flex items-center gap-1.5">
              <CheckCircle2 className="h-3.5 w-3.5 text-green-400" />
              <span className="text-gray-400">{stream.tasks_completed} completed</span>
            </div>
            {stream.tasks_failed > 0 && (
              <div className="flex items-center gap-1.5">
                <XCircle className="h-3.5 w-3.5 text-red-400" />
                <span className="text-gray-400">{stream.tasks_failed} failed</span>
              </div>
            )}
            {stream.tasks_blocked > 0 && (
              <div className="flex items-center gap-1.5">
                <AlertTriangle className="h-3.5 w-3.5 text-yellow-400" />
                <span className="text-gray-400">{stream.tasks_blocked} blocked</span>
              </div>
            )}
          </div>
          
          {/* Blocking Reasons */}
          {stream.blocking_reasons.length > 0 && (
            <div className="mt-2 p-3 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
              <h5 className="text-xs font-medium text-yellow-400 mb-2">Waiting for:</h5>
              <ul className="space-y-1">
                {stream.blocking_reasons.map((reason, idx) => (
                  <li key={idx} className="text-xs text-gray-400 flex items-start gap-2">
                    <AlertTriangle className="h-3 w-3 text-yellow-500 mt-0.5 flex-shrink-0" />
                    {reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
