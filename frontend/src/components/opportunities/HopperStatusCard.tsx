import { Box, AlertTriangle, CheckCircle, XCircle } from 'lucide-react';
import type { HopperStatus } from '@/types/opportunity';

interface HopperStatusCardProps {
  hopper: HopperStatus;
  className?: string;
}

export function HopperStatusCard({ hopper, className = '' }: HopperStatusCardProps) {
  const percentage = Math.round(
    (hopper.total_committed / hopper.max_capacity) * 100
  );

  const statusConfig = {
    available: {
      icon: CheckCircle,
      color: 'text-green-400',
      bgColor: 'bg-green-500/10',
      borderColor: 'border-green-500/30',
      barColor: 'bg-green-500',
      label: 'Available',
    },
    warning: {
      icon: AlertTriangle,
      color: 'text-yellow-400',
      bgColor: 'bg-yellow-500/10',
      borderColor: 'border-yellow-500/30',
      barColor: 'bg-yellow-500',
      label: 'Near Capacity',
    },
    full: {
      icon: XCircle,
      color: 'text-red-400',
      bgColor: 'bg-red-500/10',
      borderColor: 'border-red-500/30',
      barColor: 'bg-red-500',
      label: 'Full',
    },
  };

  const config = statusConfig[hopper.status];
  const StatusIcon = config.icon;

  return (
    <div
      className={`${config.bgColor} ${config.borderColor} border rounded-lg p-4 ${className}`}
    >
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <Box className={`h-5 w-5 ${config.color}`} />
          <span className="text-sm font-medium text-gray-300">
            Proposal Hopper
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <StatusIcon className={`h-4 w-4 ${config.color}`} />
          <span className={`text-sm font-medium ${config.color}`}>
            {config.label}
          </span>
        </div>
      </div>

      {/* Progress bar */}
      <div className="h-2 bg-gray-700 rounded-full overflow-hidden mb-2">
        <div
          className={`h-full ${config.barColor} transition-all duration-300`}
          style={{ width: `${Math.min(percentage, 100)}%` }}
        />
      </div>

      {/* Stats row */}
      <div className="flex items-center justify-between text-xs text-gray-400">
        <span>
          <span className="text-white font-medium">{hopper.total_committed}</span>
          {' / '}
          {hopper.max_capacity} slots
        </span>
        <span>
          <span className="text-white font-medium">{hopper.available_slots}</span>
          {' available'}
        </span>
      </div>

      {/* Breakdown */}
      <div className="mt-2 pt-2 border-t border-gray-700/50 flex items-center justify-around text-xs">
        <div className="flex items-center gap-1.5">
          <span className="text-gray-500">Active:</span>
          <span className="text-gray-300 font-medium">{hopper.active_proposals}</span>
        </div>
        <div className="w-px h-4 bg-gray-700" />
        <div className="flex items-center gap-1.5">
          <span className="text-gray-500">Pending:</span>
          <span className="text-yellow-400 font-medium">{hopper.pending_approvals}</span>
        </div>
      </div>
    </div>
  );
}
