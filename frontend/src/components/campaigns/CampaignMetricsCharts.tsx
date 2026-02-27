/**
 * Campaign Metrics Charts
 * 
 * Visualization components for campaign performance data:
 * - BudgetDonutChart: Shows budget allocation vs spending
 * - StreamProgressChart: Horizontal bar chart comparing stream progress
 * - TaskTimelineChart: Timeline of task completion
 */
import { 
  PieChart, 
  Pie, 
  Cell, 
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
} from 'recharts';
import { DollarSign, TrendingUp, Clock } from 'lucide-react';

// ============================================================================
// Budget Donut Chart
// ============================================================================

interface BudgetDonutChartProps {
  allocated: number;
  spent: number;
  revenue: number;
}

const BUDGET_COLORS = {
  spent: '#ef4444',     // red-500
  remaining: '#22c55e', // green-500
  revenue: '#06b6d4',   // cyan-500
};

export function BudgetDonutChart({ allocated, spent, revenue }: BudgetDonutChartProps) {
  const remaining = Math.max(0, allocated - spent);
  const profit = revenue - spent;
  
  const data = [
    { name: 'Spent', value: spent, color: BUDGET_COLORS.spent },
    { name: 'Remaining', value: remaining, color: BUDGET_COLORS.remaining },
  ];
  
  // Filter out zero values
  const filteredData = data.filter(d => d.value > 0);
  
  if (filteredData.length === 0) {
    filteredData.push({ name: 'Allocated', value: allocated, color: '#6b7280' });
  }

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
        <DollarSign className="h-4 w-4" />
        Budget Overview
      </h3>
      
      <div className="flex items-center gap-4">
        {/* Donut Chart - use fixed dimensions to avoid ResponsiveContainer issues */}
        <div className="w-32 h-32 flex-shrink-0">
          <PieChart width={128} height={128}>
            <Pie
              data={filteredData}
              cx="50%"
              cy="50%"
              innerRadius={30}
              outerRadius={50}
              paddingAngle={2}
              dataKey="value"
            >
              {filteredData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.color} />
              ))}
            </Pie>
          </PieChart>
        </div>
        
        {/* Stats */}
        <div className="flex-1 space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-400">Allocated</span>
            <span className="text-white font-medium">${allocated.toLocaleString()}</span>
          </div>
          <div className="flex justify-between">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-red-500"></span>
              <span className="text-gray-400">Spent</span>
            </span>
            <span className="text-red-400 font-medium">${spent.toLocaleString()}</span>
          </div>
          <div className="flex justify-between">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-green-500"></span>
              <span className="text-gray-400">Remaining</span>
            </span>
            <span className="text-green-400 font-medium">${remaining.toLocaleString()}</span>
          </div>
          <div className="border-t border-gray-700 pt-2 flex justify-between">
            <span className="text-gray-400">Revenue</span>
            <span className="text-neon-cyan font-medium">${revenue.toLocaleString()}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-400">Profit</span>
            <span className={`font-medium ${profit >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {profit >= 0 ? '+' : ''}{profit.toLocaleString()}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Stream Progress Chart
// ============================================================================

interface StreamData {
  id: string;
  name: string;
  status: string;
  tasks_total: number;
  tasks_completed: number;
  tasks_failed: number;
  progress_pct: number;
}

interface StreamProgressChartProps {
  streams: StreamData[];
}

const STREAM_COLORS = {
  completed: '#06b6d4', // cyan-500
  failed: '#ef4444',    // red-500
  pending: '#374151',   // gray-700
};

export function StreamProgressChart({ streams }: StreamProgressChartProps) {
  if (!streams || streams.length === 0) {
    return (
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
          <TrendingUp className="h-4 w-4" />
          Stream Progress
        </h3>
        <p className="text-gray-500 text-sm text-center py-4">No streams to display</p>
      </div>
    );
  }

  // Transform data for stacked bar chart
  const chartData = streams.map(stream => ({
    name: stream.name.length > 20 ? stream.name.slice(0, 20) + '...' : stream.name,
    completed: stream.tasks_completed,
    failed: stream.tasks_failed,
    pending: stream.tasks_total - stream.tasks_completed - stream.tasks_failed,
    total: stream.tasks_total,
    progress: stream.progress_pct,
  }));

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
        <TrendingUp className="h-4 w-4" />
        Stream Progress
      </h3>
      
      {/* Fixed dimensions - fits in typical card layout */}
      <BarChart
        width={380}
        height={180}
        data={chartData}
        layout="vertical"
        margin={{ top: 5, right: 20, left: 5, bottom: 5 }}
      >
          <XAxis 
            type="number" 
            stroke="#6b7280" 
            fontSize={10}
            allowDecimals={false}
          />
          <YAxis 
            type="category" 
            dataKey="name" 
            stroke="#6b7280" 
            fontSize={10}
            width={80}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1f2937',
              border: '1px solid #374151',
              borderRadius: '0.5rem',
              color: '#fff',
            }}
            formatter={(value, name) => [value, String(name).charAt(0).toUpperCase() + String(name).slice(1)]}
          />
          <Legend 
            wrapperStyle={{ fontSize: '10px' }}
            formatter={(value) => <span className="text-gray-400">{value}</span>}
          />
          <Bar dataKey="completed" stackId="a" fill={STREAM_COLORS.completed} name="Completed" />
          <Bar dataKey="failed" stackId="a" fill={STREAM_COLORS.failed} name="Failed" />
          <Bar dataKey="pending" stackId="a" fill={STREAM_COLORS.pending} name="Pending" radius={[0, 4, 4, 0]} />
        </BarChart>
    </div>
  );
}

// ============================================================================
// Task Timeline Chart
// ============================================================================

interface TaskTimelineData {
  id: string;
  name: string;
  stream_name: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

interface TaskTimelineChartProps {
  tasks: TaskTimelineData[];
  campaignStartDate?: string;
}

export function TaskTimelineChart({ tasks, campaignStartDate }: TaskTimelineChartProps) {
  // Filter to completed tasks with timing data
  const completedTasks = tasks
    .filter(t => t.completed_at && t.started_at)
    .sort((a, b) => new Date(a.started_at!).getTime() - new Date(b.started_at!).getTime());
  
  if (completedTasks.length === 0) {
    return (
      <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
          <Clock className="h-4 w-4" />
          Task Timeline
        </h3>
        <p className="text-gray-500 text-sm text-center py-4">No completed tasks yet</p>
      </div>
    );
  }

  // Calculate time range
  const startTime = campaignStartDate 
    ? new Date(campaignStartDate).getTime()
    : new Date(completedTasks[0].started_at!).getTime();
  
  const endTime = Math.max(
    ...completedTasks.map(t => new Date(t.completed_at!).getTime())
  );
  
  const totalDuration = endTime - startTime;

  // Group by stream for coloring
  const streamColors: Record<string, string> = {};
  const colorPalette = ['#06b6d4', '#8b5cf6', '#f59e0b', '#10b981', '#ec4899', '#3b82f6'];
  let colorIndex = 0;
  
  completedTasks.forEach(task => {
    if (!streamColors[task.stream_name]) {
      streamColors[task.stream_name] = colorPalette[colorIndex % colorPalette.length];
      colorIndex++;
    }
  });

  const formatTime = (ms: number) => {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${(ms / 60000).toFixed(1)}m`;
  };

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
        <Clock className="h-4 w-4" />
        Task Timeline
      </h3>
      
      {/* Legend */}
      <div className="flex flex-wrap gap-3 mb-4 text-xs">
        {Object.entries(streamColors).map(([stream, color]) => (
          <div key={stream} className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full" style={{ backgroundColor: color }}></span>
            <span className="text-gray-400">{stream}</span>
          </div>
        ))}
      </div>
      
      {/* Timeline */}
      <div className="relative space-y-1.5">
        {completedTasks.slice(0, 15).map(task => {
          const taskStart = new Date(task.started_at!).getTime();
          const taskEnd = new Date(task.completed_at!).getTime();
          const left = ((taskStart - startTime) / totalDuration) * 100;
          const width = Math.max(2, ((taskEnd - taskStart) / totalDuration) * 100);
          
          return (
            <div 
              key={task.id} 
              className="relative h-5 bg-gray-800/50 rounded group"
              title={`${task.name} (${task.stream_name}): ${formatTime(task.duration_ms || (taskEnd - taskStart))}`}
            >
              <div
                className="absolute h-full rounded transition-all group-hover:brightness-125"
                style={{
                  left: `${left}%`,
                  width: `${width}%`,
                  backgroundColor: streamColors[task.stream_name],
                }}
              />
              <span className="absolute left-1 top-0 bottom-0 flex items-center text-[10px] text-white/80 truncate pr-2 pointer-events-none">
                {task.name}
              </span>
            </div>
          );
        })}
        
        {completedTasks.length > 15 && (
          <p className="text-xs text-gray-500 pt-1">
            +{completedTasks.length - 15} more tasks...
          </p>
        )}
      </div>
      
      {/* Time scale */}
      <div className="flex justify-between text-[10px] text-gray-500 mt-2 pt-1 border-t border-gray-700">
        <span>Start</span>
        <span>{formatTime(totalDuration / 4)}</span>
        <span>{formatTime(totalDuration / 2)}</span>
        <span>{formatTime((totalDuration * 3) / 4)}</span>
        <span>{formatTime(totalDuration)}</span>
      </div>
    </div>
  );
}

// ============================================================================
// Combined Metrics Panel
// ============================================================================

interface CampaignMetricsPanelProps {
  campaign: {
    budget_allocated: number;
    budget_spent: number;
    revenue_generated: number;
    start_date?: string;
  };
  streams: StreamData[];
  tasks?: TaskTimelineData[];
}

export function CampaignMetricsPanel({ campaign, streams, tasks = [] }: CampaignMetricsPanelProps) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BudgetDonutChart
          allocated={campaign.budget_allocated}
          spent={campaign.budget_spent}
          revenue={campaign.revenue_generated}
        />
        <StreamProgressChart streams={streams} />
      </div>
      {tasks.length > 0 && (
        <TaskTimelineChart tasks={tasks} campaignStartDate={campaign.start_date} />
      )}
    </div>
  );
}
