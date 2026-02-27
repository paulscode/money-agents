/**
 * CampaignPerformanceChart Component
 * 
 * Multi-line chart showing campaign profit/loss over time.
 * Epic neon lightning aesthetic with wow-factor visualization.
 */
import { useQuery } from '@tanstack/react-query';
import {
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  Area,
  ComposedChart,
} from 'recharts';
import { 
  Zap, 
  TrendingUp, 
  Activity,
  Loader2,
  AlertCircle,
  BarChart3
} from 'lucide-react';
import { usageService } from '@/services/usage';
import type { CampaignFinancials } from '@/services/usage';
import { useState } from 'react';

// Neon color palette for campaigns
const CAMPAIGN_COLORS = [
  '#00d9ff', // neon-cyan
  '#9d4edd', // neon-purple
  '#06ffa5', // neon-green
  '#ffbe0b', // neon-yellow
  '#ff006e', // neon-pink
  '#0066ff', // electric-blue
  '#ff5500', // orange
  '#00ff88', // bright green
  '#ff00ff', // magenta
  '#00ffff', // cyan
];

// Custom tooltip with neon styling
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload || !payload.length) return null;

  // Filter out null values (campaigns that don't have data for this date)
  const validPayload = payload.filter((entry: any) => entry.value !== null && entry.value !== undefined);
  
  if (validPayload.length === 0) return null;

  return (
    <div className="bg-gray-900/95 backdrop-blur-sm border border-neon-cyan/30 rounded-lg p-3 shadow-neon-cyan">
      <p className="text-gray-400 text-xs mb-2 font-medium">{label}</p>
      <div className="space-y-1.5">
        {validPayload.map((entry: any, index: number) => (
          <div key={index} className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <div 
                className="w-2 h-2 rounded-full" 
                style={{ backgroundColor: entry.color }}
              />
              <span className="text-gray-300 text-xs truncate max-w-[120px]">
                {entry.name}
              </span>
            </div>
            <span 
              className="font-mono text-xs font-bold"
              style={{ color: entry.value >= 0 ? '#06ffa5' : '#ff006e' }}
            >
              {entry.value >= 0 ? '+' : ''}${entry.value.toFixed(2)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Custom legend with campaign details
function CampaignLegend({ 
  campaigns, 
  colors,
  selectedCampaigns,
  onToggleCampaign
}: { 
  campaigns: CampaignFinancials[];
  colors: string[];
  selectedCampaigns: Set<string>;
  onToggleCampaign: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-2 mt-4">
      {campaigns.map((campaign, index) => {
        const isSelected = selectedCampaigns.has(campaign.id);
        const color = colors[index % colors.length];
        
        return (
          <button
            key={campaign.id}
            onClick={() => onToggleCampaign(campaign.id)}
            className={`
              flex items-center gap-2 px-3 py-1.5 rounded-full text-xs
              border transition-all duration-200
              ${isSelected 
                ? 'bg-gray-800 border-gray-600' 
                : 'bg-gray-900/50 border-gray-700/50 opacity-50'
              }
              hover:opacity-100
            `}
          >
            <div 
              className="w-2 h-2 rounded-full"
              style={{ 
                backgroundColor: color,
                boxShadow: isSelected ? `0 0 6px ${color}` : 'none'
              }}
            />
            <span className="text-gray-300 truncate max-w-[100px]">
              {campaign.name}
            </span>
            <span 
              className="font-mono text-[10px]"
              style={{ color: campaign.profit_loss >= 0 ? '#06ffa5' : '#ff006e' }}
            >
              {campaign.profit_loss >= 0 ? '+' : ''}${campaign.profit_loss.toFixed(0)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

// View mode toggle
function ViewModeToggle({ 
  mode, 
  onModeChange 
}: { 
  mode: 'cumulative' | 'daily' | 'profit';
  onModeChange: (mode: 'cumulative' | 'daily' | 'profit') => void;
}) {
  const modes = [
    { id: 'cumulative', label: 'Cumulative', icon: TrendingUp },
    { id: 'daily', label: 'Daily', icon: Activity },
    { id: 'profit', label: 'Profit/Loss', icon: BarChart3 },
  ] as const;

  return (
    <div className="flex bg-gray-900/50 border border-gray-700/50 rounded-lg p-1">
      {modes.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          onClick={() => onModeChange(id)}
          className={`
            flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
            transition-all duration-200
            ${mode === id 
              ? 'bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30' 
              : 'text-gray-400 hover:text-gray-300'
            }
          `}
        >
          <Icon className="w-3.5 h-3.5" />
          {label}
        </button>
      ))}
    </div>
  );
}

// Main chart component
export function CampaignPerformanceChart({ days = 30 }: { days?: number }) {
  const [viewMode, setViewMode] = useState<'cumulative' | 'daily' | 'profit'>('profit');
  const [selectedCampaigns, setSelectedCampaigns] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useQuery({
    queryKey: ['financial-dashboard', days],
    queryFn: () => usageService.getFinancialDashboard(days),
    staleTime: 60000,
    refetchInterval: 120000,
  });

  // Initialize selected campaigns when data loads
  if (data && selectedCampaigns.size === 0 && data.campaigns.length > 0) {
    setSelectedCampaigns(new Set(data.campaigns.map(c => c.id)));
  }

  const toggleCampaign = (id: string) => {
    setSelectedCampaigns(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-700/50 bg-gray-900/50 p-6 h-[400px] flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="w-8 h-8 text-neon-cyan animate-spin mx-auto mb-2" />
          <p className="text-gray-500 text-sm">Loading chart data...</p>
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-xl border border-neon-pink/30 bg-neon-pink/5 p-6 h-[400px] flex items-center justify-center">
        <div className="text-center">
          <AlertCircle className="w-8 h-8 text-neon-pink mx-auto mb-2" />
          <p className="text-neon-pink">Failed to load chart data</p>
        </div>
      </div>
    );
  }

  // Build chart data - aggregate lines only
  const chartData = data.daily_totals.map(day => {
    const point: Record<string, number | string> = {
      date: new Date(day.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      fullDate: day.date,
    };

    // Add aggregate line data
    if (viewMode === 'cumulative') {
      point['Total Revenue'] = day.cumulative_revenue;
      point['Total Spent'] = day.cumulative_spent;
    } else if (viewMode === 'daily') {
      point['Daily Revenue'] = day.revenue;
      point['Daily Spent'] = day.spent;
    } else {
      point['Net Profit/Loss'] = day.cumulative_profit_loss;
    }

    return point;
  });

  const hasCampaigns = data.campaigns.length > 0;

  return (
    <div className="rounded-xl border border-gray-700/50 bg-gray-900/50 overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-gray-800 flex items-center justify-between flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-neon-purple/10 border border-neon-purple/30 rounded-lg">
            <Zap className="w-5 h-5 text-neon-purple" />
          </div>
          <div>
            <h3 className="text-white font-semibold flex items-center gap-2">
              Campaign Performance
              <span className="text-xs text-neon-cyan font-mono bg-neon-cyan/10 px-2 py-0.5 rounded">
                LIVE
              </span>
            </h3>
            <p className="text-gray-500 text-xs">
              {hasCampaigns 
                ? `${data.campaigns.length} campaigns • Last ${days} days`
                : 'No campaigns yet'
              }
            </p>
          </div>
        </div>
        
        <ViewModeToggle mode={viewMode} onModeChange={setViewMode} />
      </div>

      {/* Chart Area */}
      <div className="p-6">
        {!hasCampaigns ? (
          <div className="h-[300px] flex items-center justify-center">
            <div className="text-center">
              <div className="w-16 h-16 bg-gray-800 rounded-full flex items-center justify-center mx-auto mb-4">
                <Activity className="w-8 h-8 text-gray-600" />
              </div>
              <p className="text-gray-400 font-medium">No Campaign Data Yet</p>
              <p className="text-gray-500 text-sm mt-1">
                Create campaigns to see performance metrics here
              </p>
            </div>
          </div>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={chartData}>
                <defs>
                  {/* Gradient for the main area */}
                  <linearGradient id="profitGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#06ffa5" stopOpacity={0.3} />
                    <stop offset="50%" stopColor="#06ffa5" stopOpacity={0.1} />
                    <stop offset="100%" stopColor="#06ffa5" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="lossGradient" x1="0" y1="1" x2="0" y2="0">
                    <stop offset="0%" stopColor="#ff006e" stopOpacity={0.3} />
                    <stop offset="50%" stopColor="#ff006e" stopOpacity={0.1} />
                    <stop offset="100%" stopColor="#ff006e" stopOpacity={0} />
                  </linearGradient>
                  {/* Glow filters for lines */}
                  {CAMPAIGN_COLORS.map((color, i) => (
                    <filter key={i} id={`glow-${i}`} x="-50%" y="-50%" width="200%" height="200%">
                      <feGaussianBlur stdDeviation="2" result="coloredBlur" />
                      <feMerge>
                        <feMergeNode in="coloredBlur" />
                        <feMergeNode in="SourceGraphic" />
                      </feMerge>
                    </filter>
                  ))}
                </defs>

                <CartesianGrid 
                  strokeDasharray="3 3" 
                  stroke="#374151" 
                  strokeOpacity={0.3}
                  vertical={false}
                />
                
                <XAxis 
                  dataKey="date" 
                  stroke="#6b7280"
                  fontSize={11}
                  tickLine={false}
                  axisLine={{ stroke: '#374151', strokeOpacity: 0.5 }}
                />
                
                <YAxis 
                  stroke="#6b7280"
                  fontSize={11}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value) => `$${value}`}
                />
                
                {/* Zero reference line */}
                <ReferenceLine 
                  y={0} 
                  stroke="#6b7280" 
                  strokeDasharray="5 5"
                  strokeOpacity={0.5}
                />
                
                <Tooltip content={<CustomTooltip />} />

                {/* Main aggregate lines based on view mode */}
                {viewMode === 'profit' && (
                  <>
                    <Area
                      type="monotone"
                      dataKey="Net Profit/Loss"
                      stroke="#00d9ff"
                      strokeWidth={2}
                      fill="url(#profitGradient)"
                      dot={false}
                      activeDot={{ r: 4, stroke: '#00d9ff', strokeWidth: 2, fill: '#0f172a' }}
                    />
                  </>
                )}

                {viewMode === 'cumulative' && (
                  <>
                    <Line
                      type="monotone"
                      dataKey="Total Revenue"
                      stroke="#06ffa5"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4, stroke: '#06ffa5', strokeWidth: 2, fill: '#0f172a' }}
                    />
                    <Line
                      type="monotone"
                      dataKey="Total Spent"
                      stroke="#ff006e"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4, stroke: '#ff006e', strokeWidth: 2, fill: '#0f172a' }}
                    />
                  </>
                )}

                {viewMode === 'daily' && (
                  <>
                    <Line
                      type="monotone"
                      dataKey="Daily Revenue"
                      stroke="#06ffa5"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4, stroke: '#06ffa5', strokeWidth: 2, fill: '#0f172a' }}
                    />
                    <Line
                      type="monotone"
                      dataKey="Daily Spent"
                      stroke="#ff006e"
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 4, stroke: '#ff006e', strokeWidth: 2, fill: '#0f172a' }}
                    />
                  </>
                )}
              </ComposedChart>
            </ResponsiveContainer>

            {/* Campaign toggles */}
            {hasCampaigns && (
              <CampaignLegend
                campaigns={data.campaigns}
                colors={CAMPAIGN_COLORS}
                selectedCampaigns={selectedCampaigns}
                onToggleCampaign={toggleCampaign}
              />
            )}
          </>
        )}
      </div>

      {/* Stats footer */}
      {hasCampaigns && (
        <div className="px-6 py-4 border-t border-gray-800 bg-gray-900/30">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {/* Best performing */}
            {(() => {
              const best = [...data.campaigns].sort((a, b) => b.profit_loss - a.profit_loss)[0];
              return best ? (
                <div className="text-center">
                  <p className="text-gray-500 text-xs mb-1">Top Performer</p>
                  <p className="text-neon-green text-sm font-medium truncate">{best.name}</p>
                  <p className="text-neon-green/70 text-xs font-mono">
                    +${best.profit_loss.toFixed(2)}
                  </p>
                </div>
              ) : null;
            })()}
            
            {/* Worst performing */}
            {(() => {
              const worst = [...data.campaigns].sort((a, b) => a.profit_loss - b.profit_loss)[0];
              return worst && worst.profit_loss < 0 ? (
                <div className="text-center">
                  <p className="text-gray-500 text-xs mb-1">Needs Attention</p>
                  <p className="text-neon-pink text-sm font-medium truncate">{worst.name}</p>
                  <p className="text-neon-pink/70 text-xs font-mono">
                    ${worst.profit_loss.toFixed(2)}
                  </p>
                </div>
              ) : (
                <div className="text-center">
                  <p className="text-gray-500 text-xs mb-1">All Healthy</p>
                  <p className="text-neon-green text-sm">✓</p>
                </div>
              );
            })()}
            
            {/* Best ROI */}
            {(() => {
              const bestROI = [...data.campaigns]
                .filter(c => c.roi_percent !== null)
                .sort((a, b) => (b.roi_percent || 0) - (a.roi_percent || 0))[0];
              return bestROI ? (
                <div className="text-center">
                  <p className="text-gray-500 text-xs mb-1">Best ROI</p>
                  <p className="text-neon-cyan text-sm font-medium truncate">{bestROI.name}</p>
                  <p className="text-neon-cyan/70 text-xs font-mono">
                    {bestROI.roi_percent?.toFixed(0)}%
                  </p>
                </div>
              ) : null;
            })()}
            
            {/* Active campaigns */}
            <div className="text-center">
              <p className="text-gray-500 text-xs mb-1">Active Campaigns</p>
              <p className="text-white text-sm font-medium">
                {data.campaigns.filter(c => c.status === 'active').length}
              </p>
              <p className="text-gray-500 text-xs">
                of {data.campaigns.length} total
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default CampaignPerformanceChart;
