/**
 * UtilizationChart Component
 * 
 * Visual representation of how much of the scheduled interval
 * is being used by agent runs.
 */
import { formatDuration } from '@/services/agentService';

interface UtilizationChartProps {
  avgDuration: number | null;
  minDuration: number | null;
  maxDuration: number | null;
  scheduleInterval: number;
  utilization: number | null;
}

export function UtilizationChart({
  avgDuration,
  minDuration,
  maxDuration,
  scheduleInterval,
  utilization,
}: UtilizationChartProps) {
  if (!avgDuration) {
    return (
      <div className="text-center py-4 text-gray-500 text-sm">
        No run data available yet
      </div>
    );
  }

  // Calculate positions on the timeline (as percentages)
  const maxPos = Math.min(100, ((maxDuration || avgDuration) / scheduleInterval) * 100);
  const avgPos = Math.min(100, (avgDuration / scheduleInterval) * 100);
  const minPos = minDuration ? Math.min(100, (minDuration / scheduleInterval) * 100) : avgPos;

  // Determine color based on utilization
  const getUtilizationColor = (util: number) => {
    if (util >= 80) return 'text-red-500';
    if (util >= 50) return 'text-yellow-500';
    return 'text-green-500';
  };

  const utilizationColor = utilization !== null ? getUtilizationColor(utilization) : 'text-gray-400';
  const barColor = utilization !== null ? (
    utilization >= 80 ? 'bg-red-500' :
    utilization >= 50 ? 'bg-yellow-500' :
    'bg-green-500'
  ) : 'bg-gray-500';

  return (
    <div className="space-y-3">
      {/* Utilization Percentage */}
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-400">Schedule Utilization</span>
        <span className={`text-lg font-bold ${utilizationColor}`}>
          {utilization !== null ? `${utilization.toFixed(1)}%` : 'N/A'}
        </span>
      </div>

      {/* Timeline Bar */}
      <div className="relative">
        {/* Background (full schedule) */}
        <div className="h-8 bg-gray-800 rounded-lg overflow-hidden relative">
          {/* Range indicator (min to max) */}
          <div
            className="absolute h-full bg-gray-700/50"
            style={{ 
              left: `${minPos}%`, 
              width: `${Math.max(0, maxPos - minPos)}%` 
            }}
          />
          
          {/* Average bar */}
          <div
            className={`absolute h-full ${barColor} rounded-r-lg transition-all duration-500`}
            style={{ width: `${avgPos}%` }}
          />

          {/* Average marker line */}
          <div
            className="absolute top-0 bottom-0 w-0.5 bg-white"
            style={{ left: `${avgPos}%` }}
          />

          {/* Labels inside the bar */}
          <div className="absolute inset-0 flex items-center px-3">
            <span className="text-xs font-medium text-white/80">
              Avg: {formatDuration(avgDuration)}
            </span>
          </div>
        </div>

        {/* Scale markers */}
        <div className="flex justify-between text-xs text-gray-500 mt-1">
          <span>0</span>
          <span>{formatDuration(scheduleInterval / 4)}</span>
          <span>{formatDuration(scheduleInterval / 2)}</span>
          <span>{formatDuration((scheduleInterval * 3) / 4)}</span>
          <span>{formatDuration(scheduleInterval)}</span>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div className="bg-gray-800/50 rounded p-2 text-center">
          <div className="text-gray-500">Min</div>
          <div className="text-white font-medium">
            {minDuration ? formatDuration(minDuration) : '-'}
          </div>
        </div>
        <div className="bg-gray-800/50 rounded p-2 text-center">
          <div className="text-gray-500">Avg</div>
          <div className={`font-medium ${utilizationColor}`}>
            {formatDuration(avgDuration)}
          </div>
        </div>
        <div className="bg-gray-800/50 rounded p-2 text-center">
          <div className="text-gray-500">Max</div>
          <div className="text-white font-medium">
            {maxDuration ? formatDuration(maxDuration) : '-'}
          </div>
        </div>
      </div>

      {/* Warning for high utilization */}
      {utilization !== null && utilization >= 80 && (
        <div className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2">
          ⚠️ High utilization - runs may overlap or timeout. Consider increasing schedule interval.
        </div>
      )}

      {/* Info for low utilization */}
      {utilization !== null && utilization < 20 && (
        <div className="text-xs text-green-400 bg-green-500/10 border border-green-500/20 rounded p-2">
          ✓ Low utilization - you could decrease the schedule interval for more frequent runs.
        </div>
      )}
    </div>
  );
}
