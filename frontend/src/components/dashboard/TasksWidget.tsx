import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { 
  CheckSquare, AlertCircle, Clock, TrendingUp, DollarSign,
  ChevronRight, Loader2, Target
} from 'lucide-react';
import { tasksService } from '@/services/tasks';
import { cn, formatCurrency } from '@/lib/utils';

interface TasksWidgetProps {
  className?: string;
}

export function TasksWidget({ className }: TasksWidgetProps) {
  const { data: dashboard, isLoading } = useQuery({
    queryKey: ['task-dashboard'],
    queryFn: () => tasksService.getDashboard(30),
    refetchInterval: 60000, // Refresh every minute
  });

  if (isLoading) {
    return (
      <div className={cn("card", className)}>
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        </div>
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div className={cn("card", className)}>
        <div className="text-center py-12 text-gray-400">
          Unable to load task data
        </div>
      </div>
    );
  }

  const { summary, analytics } = dashboard;
  const { counts } = summary;
  
  // Calculate active count
  const activeCount = counts.ready + counts.in_progress;
  const overdueCount = counts.overdue;
  const completedCount = analytics.completed_count;
  const valueCaptured = analytics.value_captured;
  const activeValue = analytics.active_value;

  return (
    <div className={cn("card", className)}>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-bold text-white flex items-center space-x-2">
          <CheckSquare className="h-5 w-5 text-neon-cyan" />
          <span>Tasks</span>
        </h2>
        <Link 
          to="/tasks" 
          className="text-sm text-neon-cyan hover:text-neon-cyan/80 flex items-center space-x-1"
        >
          <span>View All</span>
          <ChevronRight className="h-4 w-4" />
        </Link>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {/* Active Tasks */}
        <div className="bg-navy-800/50 rounded-lg p-3">
          <div className="flex items-center space-x-2 mb-1">
            <Target className="h-4 w-4 text-neon-cyan" />
            <span className="text-xs text-gray-400">Active</span>
          </div>
          <p className="text-2xl font-bold text-white">{activeCount}</p>
        </div>

        {/* Overdue */}
        <div className="bg-navy-800/50 rounded-lg p-3">
          <div className="flex items-center space-x-2 mb-1">
            <AlertCircle className={cn("h-4 w-4", overdueCount > 0 ? "text-red-400" : "text-gray-400")} />
            <span className="text-xs text-gray-400">Overdue</span>
          </div>
          <p className={cn(
            "text-2xl font-bold",
            overdueCount > 0 ? "text-red-400" : "text-white"
          )}>
            {overdueCount}
          </p>
        </div>

        {/* Completed (30 days) */}
        <div className="bg-navy-800/50 rounded-lg p-3">
          <div className="flex items-center space-x-2 mb-1">
            <TrendingUp className="h-4 w-4 text-neon-green" />
            <span className="text-xs text-gray-400">Completed (30d)</span>
          </div>
          <p className="text-2xl font-bold text-neon-green">{completedCount}</p>
        </div>

        {/* Value Captured */}
        <div className="bg-navy-800/50 rounded-lg p-3">
          <div className="flex items-center space-x-2 mb-1">
            <DollarSign className="h-4 w-4 text-neon-yellow" />
            <span className="text-xs text-gray-400">Value Captured</span>
          </div>
          <p className="text-2xl font-bold text-neon-yellow">
            {formatCurrency(valueCaptured)}
          </p>
        </div>
      </div>

      {/* Completion Trend Mini Chart */}
      {analytics.completion_trend.length > 0 && (
        <div className="mb-6">
          <h3 className="text-sm font-medium text-gray-400 mb-2">7-Day Completion Trend</h3>
          <div className="flex items-end justify-between h-16 gap-1">
            {analytics.completion_trend.map((day, i) => {
              const maxCompleted = Math.max(...analytics.completion_trend.map(d => d.completed), 1);
              const height = (day.completed / maxCompleted) * 100;
              return (
                <div 
                  key={i}
                  className="flex-1 flex flex-col items-center"
                >
                  <div 
                    className="w-full bg-neon-cyan/30 rounded-t transition-all duration-300 hover:bg-neon-cyan/50"
                    style={{ height: `${Math.max(height, 4)}%` }}
                    title={`${day.date}: ${day.completed} completed`}
                  />
                  <span className="text-[10px] text-gray-500 mt-1">
                    {new Date(day.date).toLocaleDateString('en-US', { weekday: 'short' }).slice(0, 1)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Top Priority Tasks */}
      <div>
        <h3 className="text-sm font-medium text-gray-400 mb-2">Priority Tasks</h3>
        {summary.top_tasks.length > 0 ? (
          <ul className="space-y-2">
            {summary.top_tasks.slice(0, 3).map((task) => (
              <li key={task.id}>
                <Link
                  to={`/tasks?selected=${task.id}`}
                  className="flex items-center justify-between p-2 rounded-lg bg-navy-800/30 hover:bg-navy-800/50 transition-colors group"
                >
                  <div className="flex items-center space-x-2 min-w-0">
                    <StatusIcon status={task.status} />
                    <span className="text-sm text-white truncate group-hover:text-neon-cyan transition-colors">
                      {task.title}
                    </span>
                  </div>
                  {task.estimated_value && (
                    <span className="text-xs text-neon-green ml-2 flex-shrink-0">
                      {formatCurrency(task.estimated_value)}
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-gray-500 text-center py-4">
            No active tasks. Create one to get started!
          </p>
        )}
      </div>

      {/* Active Value Footer */}
      {activeValue > 0 && (
        <div className="mt-4 pt-4 border-t border-navy-700 flex items-center justify-between">
          <span className="text-xs text-gray-400">Potential value in active tasks:</span>
          <span className="text-sm font-medium text-neon-green">
            {formatCurrency(activeValue)}
          </span>
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'in_progress':
      return <div className="h-2 w-2 rounded-full bg-blue-400" />;
    case 'blocked':
      return <div className="h-2 w-2 rounded-full bg-red-400" />;
    case 'ready':
    default:
      return <div className="h-2 w-2 rounded-full bg-neon-green" />;
  }
}
