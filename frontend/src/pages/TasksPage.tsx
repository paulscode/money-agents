import { useState, useMemo, useCallback, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Layout } from '@/components/layout/Layout';
import { tasksService } from '@/services/tasks';
import { format, formatDistanceToNow, isPast, isToday, isTomorrow, addDays } from 'date-fns';
import {
  CheckCircle2,
  Clock,
  AlertTriangle,
  Pause,
  Ban,
  Play,
  Plus,
  ChevronDown,
  Filter,
  Loader2,
  RefreshCw,
  DollarSign,
  Timer,
  Target,
  MoreVertical,
  Calendar,
  ArrowUpRight,
  Zap,
} from 'lucide-react';
import type { Task, TaskStatus, TaskType, TaskSortBy, TaskCounts } from '@/types';

// Status configuration
const STATUS_CONFIG: Record<TaskStatus, { label: string; color: string; icon: React.ComponentType<any> }> = {
  created: { label: 'New', color: 'bg-blue-500', icon: Plus },
  ready: { label: 'Ready', color: 'bg-green-500', icon: Target },
  blocked: { label: 'Blocked', color: 'bg-red-500', icon: Ban },
  deferred: { label: 'Deferred', color: 'bg-gray-500', icon: Pause },
  in_progress: { label: 'In Progress', color: 'bg-yellow-500', icon: Play },
  completed: { label: 'Completed', color: 'bg-neon-green', icon: CheckCircle2 },
  cancelled: { label: 'Cancelled', color: 'bg-gray-600', icon: Ban },
  delegated: { label: 'Delegated', color: 'bg-purple-500', icon: ArrowUpRight },
};

// Task type labels
const TYPE_LABELS: Record<TaskType, string> = {
  campaign_action: 'Campaign',
  review_required: 'Review',
  follow_up: 'Follow Up',
  personal: 'Personal',
  system: 'System',
  idea_action: 'Idea',
};

// Sort options
const SORT_OPTIONS: { value: TaskSortBy; label: string }[] = [
  { value: 'priority', label: 'Priority' },
  { value: 'due_date', label: 'Due Date' },
  { value: 'value', label: 'Est. Value' },
  { value: 'value_per_hour', label: 'Value/Hour' },
  { value: 'created', label: 'Created' },
  { value: 'updated', label: 'Updated' },
];

export function TasksPage() {
  const queryClient = useQueryClient();
  
  // Filters
  const [showCompleted, setShowCompleted] = useState(false);
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'all'>('all');
  const [typeFilter, setTypeFilter] = useState<TaskType | 'all'>('all');
  const [sortBy, setSortBy] = useState<TaskSortBy>('priority');
  
  // UI state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  
  // Fetch tasks
  const { data: tasksResponse, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['tasks', statusFilter, typeFilter, sortBy, showCompleted],
    queryFn: () => tasksService.list({
      statuses: statusFilter !== 'all' ? [statusFilter] : undefined,
      task_types: typeFilter !== 'all' ? [typeFilter] : undefined,
      include_completed: showCompleted,
      sort_by: sortBy,
      limit: 100,
    }),
    refetchInterval: 30000,
  });
  
  // Fetch counts
  const { data: counts } = useQuery({
    queryKey: ['task-counts'],
    queryFn: () => tasksService.getCounts(),
    refetchInterval: 30000,
  });
  
  // Mutations
  const completeMutation = useMutation({
    mutationFn: (taskId: string) => tasksService.complete(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['task-counts'] });
    },
  });
  
  const startMutation = useMutation({
    mutationFn: (taskId: string) => tasksService.start(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
    },
  });
  
  const deferMutation = useMutation({
    mutationFn: ({ taskId, until }: { taskId: string; until: string }) => 
      tasksService.defer(taskId, until),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['task-counts'] });
    },
  });
  
  const cancelMutation = useMutation({
    mutationFn: (taskId: string) => tasksService.cancel(taskId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tasks'] });
      queryClient.invalidateQueries({ queryKey: ['task-counts'] });
    },
  });
  
  // Sync auto-generated tasks
  const syncMutation = useMutation({
    mutationFn: () => tasksService.sync(),
    onSuccess: (data) => {
      // Refresh task list if any changes were made
      if (data.campaign_tasks_created > 0 || 
          data.opportunity_review_task === 'updated' ||
          data.deferred_tasks_activated > 0) {
        queryClient.invalidateQueries({ queryKey: ['tasks'] });
        queryClient.invalidateQueries({ queryKey: ['task-counts'] });
      }
    },
  });
  
  // Sync on page load
  useEffect(() => {
    syncMutation.mutate();
  }, []); // Run once on mount
  
  // Quick defer options
  const handleDefer = useCallback((taskId: string, days: number) => {
    const until = addDays(new Date(), days).toISOString();
    deferMutation.mutate({ taskId, until });
  }, [deferMutation]);
  
  const tasks = tasksResponse?.tasks || [];
  
  return (
    <Layout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold text-neon-cyan">Tasks</h1>
            <p className="mt-1 text-gray-400">
              {counts?.active || 0} active tasks • {counts?.overdue || 0} overdue
            </p>
          </div>
          
          <div className="flex items-center gap-3">
            <button
              onClick={() => {
                syncMutation.mutate();
                refetch();
              }}
              disabled={isFetching || syncMutation.isPending}
              className="btn-secondary flex items-center gap-2"
              title="Sync auto-generated tasks and refresh"
            >
              <RefreshCw className={`h-4 w-4 ${(isFetching || syncMutation.isPending) ? 'animate-spin' : ''}`} />
              {syncMutation.isPending ? 'Syncing...' : 'Refresh'}
            </button>
            <button
              onClick={() => setShowCreateModal(true)}
              className="btn-primary flex items-center gap-2"
            >
              <Plus className="h-4 w-4" />
              New Task
            </button>
          </div>
        </div>
        
        {/* Stats Cards */}
        {counts && <TaskStatsCards counts={counts} />}
        
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-gray-400" />
            <span className="text-sm text-gray-400">Filters:</span>
          </div>
          
          {/* Status Filter */}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as TaskStatus | 'all')}
            className="px-3 py-2 bg-navy-800 border border-navy-600 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm [&>option]:bg-navy-800 [&>option]:text-white"
          >
            <option value="all">All Statuses</option>
            {Object.entries(STATUS_CONFIG).map(([status, config]) => (
              <option key={status} value={status}>{config.label}</option>
            ))}
          </select>
          
          {/* Type Filter */}
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as TaskType | 'all')}
            className="px-3 py-2 bg-navy-800 border border-navy-600 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm [&>option]:bg-navy-800 [&>option]:text-white"
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([type, label]) => (
              <option key={type} value={type}>{label}</option>
            ))}
          </select>
          
          {/* Sort */}
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as TaskSortBy)}
            className="px-3 py-2 bg-navy-800 border border-navy-600 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan text-sm [&>option]:bg-navy-800 [&>option]:text-white"
          >
            {SORT_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                Sort: {option.label}
              </option>
            ))}
          </select>
          
          {/* Show Completed */}
          <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
            <input
              type="checkbox"
              checked={showCompleted}
              onChange={(e) => setShowCompleted(e.target.checked)}
              className="rounded border-gray-600 bg-dark-card text-neon-cyan focus:ring-neon-cyan"
            />
            Show completed
          </label>
        </div>
        
        {/* Tasks List */}
        <div className="space-y-3">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-neon-cyan" />
            </div>
          ) : tasks.length === 0 ? (
            <div className="card text-center py-12">
              <Target className="h-12 w-12 text-gray-600 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-white mb-2">No tasks found</h3>
              <p className="text-gray-400 mb-4">
                {statusFilter !== 'all' || typeFilter !== 'all'
                  ? 'Try adjusting your filters'
                  : 'Create your first task to get started'}
              </p>
              <button
                onClick={() => setShowCreateModal(true)}
                className="btn-primary inline-flex items-center gap-2"
              >
                <Plus className="h-4 w-4" />
                Create Task
              </button>
            </div>
          ) : (
            tasks.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                onComplete={() => completeMutation.mutate(task.id)}
                onStart={() => startMutation.mutate(task.id)}
                onDefer={(days) => handleDefer(task.id, days)}
                onCancel={() => cancelMutation.mutate(task.id)}
                onClick={() => setSelectedTask(task)}
                isLoading={
                  completeMutation.isPending ||
                  startMutation.isPending ||
                  deferMutation.isPending ||
                  cancelMutation.isPending
                }
              />
            ))
          )}
        </div>
        
        {/* Create Task Modal */}
        {showCreateModal && (
          <CreateTaskModal
            onClose={() => setShowCreateModal(false)}
            onCreated={() => {
              setShowCreateModal(false);
              queryClient.invalidateQueries({ queryKey: ['tasks'] });
              queryClient.invalidateQueries({ queryKey: ['task-counts'] });
            }}
          />
        )}
        
        {/* Task Detail Modal */}
        {selectedTask && (
          <TaskDetailModal
            task={selectedTask}
            onClose={() => setSelectedTask(null)}
            onUpdate={() => {
              queryClient.invalidateQueries({ queryKey: ['tasks'] });
              queryClient.invalidateQueries({ queryKey: ['task-counts'] });
            }}
          />
        )}
      </div>
    </Layout>
  );
}

// Stats Cards Component
function TaskStatsCards({ counts }: { counts: TaskCounts }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div className="card">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-blue-500/10 rounded-lg">
            <Target className="h-5 w-5 text-blue-400" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Active</p>
            <p className="text-xl font-bold text-white">{counts.active}</p>
          </div>
        </div>
      </div>
      
      <div className="card">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-red-500/10 rounded-lg">
            <AlertTriangle className="h-5 w-5 text-red-400" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Overdue</p>
            <p className="text-xl font-bold text-red-400">{counts.overdue}</p>
          </div>
        </div>
      </div>
      
      <div className="card">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-yellow-500/10 rounded-lg">
            <Clock className="h-5 w-5 text-yellow-400" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Due Today</p>
            <p className="text-xl font-bold text-yellow-400">{counts.due_today}</p>
          </div>
        </div>
      </div>
      
      <div className="card">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-neon-green/10 rounded-lg">
            <CheckCircle2 className="h-5 w-5 text-neon-green" />
          </div>
          <div>
            <p className="text-xs text-gray-400">Completed</p>
            <p className="text-xl font-bold text-neon-green">{counts.completed}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

// Task Row Component
function TaskRow({
  task,
  onComplete,
  onStart,
  onDefer,
  onCancel,
  onClick,
  isLoading,
}: {
  task: Task;
  onComplete: () => void;
  onStart: () => void;
  onDefer: (days: number) => void;
  onCancel: () => void;
  onClick: () => void;
  isLoading: boolean;
}) {
  const [showMenu, setShowMenu] = useState(false);
  const statusConfig = STATUS_CONFIG[task.status];
  const StatusIcon = statusConfig.icon;
  
  // Due date formatting
  const dueInfo = useMemo(() => {
    if (!task.due_date) return null;
    const dueDate = new Date(task.due_date);
    const overdue = isPast(dueDate) && !isToday(dueDate);
    const today = isToday(dueDate);
    const tomorrow = isTomorrow(dueDate);
    
    let label = format(dueDate, 'MMM d');
    let color = 'text-gray-400';
    
    if (overdue) {
      label = `Overdue: ${formatDistanceToNow(dueDate)} ago`;
      color = 'text-red-400';
    } else if (today) {
      label = 'Due today';
      color = 'text-yellow-400';
    } else if (tomorrow) {
      label = 'Due tomorrow';
      color = 'text-yellow-400';
    }
    
    return { label, color, overdue };
  }, [task.due_date]);
  
  // Priority badge color
  const priorityColor = useMemo(() => {
    if (task.priority_score >= 80) return 'bg-red-500/20 text-red-400 border-red-500/30';
    if (task.priority_score >= 60) return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30';
    if (task.priority_score >= 40) return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
    return 'bg-gray-500/20 text-gray-400 border-gray-500/30';
  }, [task.priority_score]);
  
  const isActionable = task.status === 'created' || task.status === 'ready';
  const isCompletable = isActionable || task.status === 'in_progress';
  const isCompleted = task.status === 'completed' || task.status === 'cancelled';
  
  return (
    <div 
      className={`card hover:border-neon-cyan/50 transition-colors cursor-pointer ${
        isCompleted ? 'opacity-60' : ''
      }`}
      onClick={onClick}
    >
      <div className="flex items-center gap-4">
        {/* Checkbox / Status */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            if (isCompletable) onComplete();
          }}
          disabled={!isCompletable || isLoading}
          className={`flex-shrink-0 w-6 h-6 rounded-full border-2 flex items-center justify-center transition-colors ${
            isCompleted
              ? 'bg-neon-green border-neon-green'
              : isCompletable
              ? 'border-gray-500 hover:border-neon-cyan'
              : 'border-gray-600 cursor-not-allowed'
          }`}
        >
          {isCompleted && <CheckCircle2 className="h-4 w-4 text-dark" />}
        </button>
        
        {/* Main Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <h3 className={`font-medium ${isCompleted ? 'line-through text-gray-500' : 'text-white'}`}>
              {task.title}
            </h3>
            <span className={`px-2 py-0.5 text-xs rounded-full border ${priorityColor}`}>
              P{Math.round(task.priority_score)}
            </span>
            <span className={`px-2 py-0.5 text-xs rounded-full ${statusConfig.color} text-white`}>
              {statusConfig.label}
            </span>
          </div>
          
          <div className="flex items-center gap-4 text-sm text-gray-400">
            <span className="flex items-center gap-1">
              <StatusIcon className="h-3 w-3" />
              {TYPE_LABELS[task.task_type]}
            </span>
            
            {dueInfo && (
              <span className={`flex items-center gap-1 ${dueInfo.color}`}>
                <Calendar className="h-3 w-3" />
                {dueInfo.label}
              </span>
            )}
            
            {task.estimated_value && (
              <span className="flex items-center gap-1 text-neon-green">
                <DollarSign className="h-3 w-3" />
                ${task.estimated_value.toLocaleString()}
              </span>
            )}
            
            {task.estimated_effort_minutes && (
              <span className="flex items-center gap-1">
                <Timer className="h-3 w-3" />
                {task.estimated_effort_minutes < 60
                  ? `${task.estimated_effort_minutes}m`
                  : `${Math.round(task.estimated_effort_minutes / 60)}h`}
              </span>
            )}
            
            {task.value_per_hour && (
              <span className="flex items-center gap-1 text-neon-cyan">
                <ArrowUpRight className="h-3 w-3" />
                ${Math.round(task.value_per_hour)}/hr
              </span>
            )}
          </div>
          
          {task.blocked_by && (
            <p className="text-xs text-red-400 mt-1">
              Blocked: {task.blocked_by}
            </p>
          )}
        </div>
        
        {/* Actions */}
        <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
          {isActionable && (
            <button
              onClick={() => onStart()}
              disabled={isLoading}
              className="p-2 hover:bg-white/10 rounded-lg transition-colors"
              title="Start"
            >
              <Play className="h-4 w-4 text-neon-cyan" />
            </button>
          )}
          
          <div className="relative">
            <button
              onClick={() => setShowMenu(!showMenu)}
              className="p-2 hover:bg-white/10 rounded-lg transition-colors"
            >
              <MoreVertical className="h-4 w-4 text-gray-400" />
            </button>
            
            {showMenu && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-dark-card border border-white/10 rounded-lg shadow-lg z-10">
                <button
                  onClick={() => { onDefer(1); setShowMenu(false); }}
                  className="w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-white/5"
                >
                  Defer 1 day
                </button>
                <button
                  onClick={() => { onDefer(7); setShowMenu(false); }}
                  className="w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-white/5"
                >
                  Defer 1 week
                </button>
                <button
                  onClick={() => { onDefer(30); setShowMenu(false); }}
                  className="w-full px-4 py-2 text-left text-sm text-gray-300 hover:bg-white/5"
                >
                  Defer 1 month
                </button>
                <hr className="border-white/10 my-1" />
                {task.status === 'in_progress' && (
                  <button
                    onClick={() => { onComplete(); setShowMenu(false); }}
                    className="w-full px-4 py-2 text-left text-sm text-neon-green hover:bg-white/5"
                  >
                    Mark as complete
                  </button>
                )}
                <button
                  onClick={() => { onCancel(); setShowMenu(false); }}
                  className="w-full px-4 py-2 text-left text-sm text-red-400 hover:bg-white/5"
                >
                  Cancel task
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// Create Task Modal
function CreateTaskModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [taskType, setTaskType] = useState<TaskType>('personal');
  const [dueDate, setDueDate] = useState('');
  const [estimatedValue, setEstimatedValue] = useState('');
  const [estimatedEffort, setEstimatedEffort] = useState('');
  
  const createMutation = useMutation({
    mutationFn: () => tasksService.create({
      title,
      description: description || undefined,
      task_type: taskType,
      due_date: dueDate || undefined,
      estimated_value: estimatedValue ? parseFloat(estimatedValue) : undefined,
      estimated_effort_minutes: estimatedEffort ? parseInt(estimatedEffort) : undefined,
    }),
    onSuccess: onCreated,
  });
  
  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    createMutation.mutate();
  };
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-dark-card border border-white/10 rounded-lg p-6 w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-xl font-bold text-white mb-4">Create Task</h2>
        
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Title *</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="input w-full"
              placeholder="What needs to be done?"
              autoFocus
            />
          </div>
          
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-1">Description</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="input w-full h-24"
              placeholder="Additional details..."
            />
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Type</label>
              <select
                value={taskType}
                onChange={(e) => setTaskType(e.target.value as TaskType)}
                className="w-full px-3 py-2 bg-navy-800 border border-navy-600 rounded-lg text-white focus:border-neon-cyan focus:ring-1 focus:ring-neon-cyan [&>option]:bg-navy-800 [&>option]:text-white"
              >
                {Object.entries(TYPE_LABELS).map(([type, label]) => (
                  <option key={type} value={type}>{label}</option>
                ))}
              </select>
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Due Date</label>
              <input
                type="datetime-local"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                className="input w-full"
              />
            </div>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Est. Value ($)</label>
              <input
                type="number"
                value={estimatedValue}
                onChange={(e) => setEstimatedValue(e.target.value)}
                className="input w-full"
                placeholder="0"
                min="0"
                step="1"
              />
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-300 mb-1">Est. Effort (mins)</label>
              <input
                type="number"
                value={estimatedEffort}
                onChange={(e) => setEstimatedEffort(e.target.value)}
                className="input w-full"
                placeholder="30"
                min="1"
              />
            </div>
          </div>
          
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="btn-secondary"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!title.trim() || createMutation.isPending}
              className="btn-primary"
            >
              {createMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : (
                <Plus className="h-4 w-4 mr-2" />
              )}
              Create Task
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// Task Detail Modal (placeholder - can be expanded)
function TaskDetailModal({
  task,
  onClose,
  onUpdate,
}: {
  task: Task;
  onClose: () => void;
  onUpdate: () => void;
}) {
  const statusConfig = STATUS_CONFIG[task.status];
  
  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-navy-900 border border-navy-700 rounded-lg p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="flex items-center gap-2 mb-2">
              <span className={`px-2 py-1 text-xs rounded-full ${statusConfig.color} text-white`}>
                {statusConfig.label}
              </span>
              <span className="px-2 py-1 text-xs rounded-full bg-gray-600 text-gray-300">
                {TYPE_LABELS[task.task_type]}
              </span>
              <span className="text-sm text-gray-400">
                Priority: {Math.round(task.priority_score)}
              </span>
            </div>
            <h2 className="text-2xl font-bold text-white">{task.title}</h2>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white"
          >
            ✕
          </button>
        </div>
        
        {task.description && (
          <div className="mb-6">
            <h3 className="text-sm font-medium text-gray-400 mb-2">Description</h3>
            <p className="text-gray-300 whitespace-pre-wrap">{task.description}</p>
          </div>
        )}
        
        <div className="grid grid-cols-2 gap-4 mb-6">
          {task.due_date && (
            <div>
              <h3 className="text-sm font-medium text-gray-400 mb-1">Due Date</h3>
              <p className={`${task.is_overdue ? 'text-red-400' : 'text-white'}`}>
                {format(new Date(task.due_date), 'PPP p')}
              </p>
            </div>
          )}
          
          {task.estimated_value && (
            <div>
              <h3 className="text-sm font-medium text-gray-400 mb-1">Estimated Value</h3>
              <p className="text-neon-green">${task.estimated_value.toLocaleString()}</p>
            </div>
          )}
          
          {task.estimated_effort_minutes && (
            <div>
              <h3 className="text-sm font-medium text-gray-400 mb-1">Estimated Effort</h3>
              <p className="text-white">
                {task.estimated_effort_minutes < 60
                  ? `${task.estimated_effort_minutes} minutes`
                  : `${(task.estimated_effort_minutes / 60).toFixed(1)} hours`}
              </p>
            </div>
          )}
          
          {task.value_per_hour && (
            <div>
              <h3 className="text-sm font-medium text-gray-400 mb-1">Value per Hour</h3>
              <p className="text-neon-cyan">${Math.round(task.value_per_hour)}/hr</p>
            </div>
          )}
        </div>
        
        {task.source_type && (
          <div className="mb-6">
            <h3 className="text-sm font-medium text-gray-400 mb-1">Source</h3>
            <p className="text-gray-300">
              {task.source_type}: {task.source_id}
            </p>
          </div>
        )}
        
        {task.blocked_by && (
          <div className="mb-6 p-3 bg-red-500/10 border border-red-500/30 rounded-lg">
            <h3 className="text-sm font-medium text-red-400 mb-1">Blocked By</h3>
            <p className="text-gray-300">{task.blocked_by}</p>
          </div>
        )}
        
        <div className="text-xs text-gray-500">
          Created {format(new Date(task.created_at), 'PPP p')}
          {task.completed_at && (
            <> • Completed {format(new Date(task.completed_at), 'PPP p')}</>
          )}
        </div>
      </div>
    </div>
  );
}
