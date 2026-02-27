/**
 * FinancialRollup Component
 * 
 * Epic high-level financial summary with neon lightning aesthetic.
 * Shows sats cards, spending breakdown (USD + sats bars), and USD cards.
 */
import { useQuery } from '@tanstack/react-query';
import { 
  TrendingUp, 
  TrendingDown, 
  DollarSign, 
  Zap, 
  CloudLightning,
  ArrowUpRight, 
  ArrowDownRight,
  Loader2,
  AlertCircle,
  Minus,
  Bitcoin,
  Calendar
} from 'lucide-react';
import { usageService } from '@/services/usage';
import { walletService } from '@/services/wallet';
import type { FinancialDashboard } from '@/services/usage';

// Animated number display with glow effect
function AnimatedNumber({ 
  value, 
  prefix = '$', 
  suffix = '',
  className = '',
  glowColor = 'neon-cyan',
  isSats = false,
}: { 
  value: number; 
  prefix?: string; 
  suffix?: string;
  className?: string;
  glowColor?: 'neon-cyan' | 'neon-green' | 'neon-pink' | 'neon-purple' | 'neon-yellow';
  isSats?: boolean;
}) {
  const formattedValue = isSats
    ? Math.abs(value).toLocaleString('en-US')
    : Math.abs(value).toLocaleString('en-US', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
  
  const isNegative = value < 0;
  
  return (
    <span className={`font-mono font-bold tabular-nums ${className}`}>
      {isNegative && '-'}{prefix}{formattedValue}{suffix && <span className="text-[0.6em] ml-1 opacity-70">{suffix}</span>}
    </span>
  );
}

// Individual stat card with neon glow
function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  variant = 'default',
  trend,
  trendLabel,
  isSats = false,
  prefix,
  suffix,
}: {
  title: string;
  value: number;
  subtitle?: string;
  icon: React.ElementType;
  variant?: 'default' | 'success' | 'danger' | 'warning';
  trend?: 'up' | 'down' | 'neutral';
  trendLabel?: string;
  isSats?: boolean;
  prefix?: string;
  suffix?: string;
}) {
  const variantStyles = {
    default: {
      border: 'border-neon-cyan/30',
      glow: 'hover:shadow-neon-cyan',
      icon: 'text-neon-cyan',
      bg: 'bg-neon-cyan/5',
      text: 'text-neon-cyan',
    },
    success: {
      border: 'border-neon-green/30',
      glow: 'hover:shadow-neon-green',
      icon: 'text-neon-green',
      bg: 'bg-neon-green/5',
      text: 'text-neon-green',
    },
    danger: {
      border: 'border-neon-pink/30',
      glow: 'hover:shadow-neon-pink',
      icon: 'text-neon-pink',
      bg: 'bg-neon-pink/5',
      text: 'text-neon-pink',
    },
    warning: {
      border: 'border-neon-yellow/30',
      glow: 'hover:shadow-neon-yellow',
      icon: 'text-neon-yellow',
      bg: 'bg-neon-yellow/5',
      text: 'text-neon-yellow',
    },
  };

  const styles = variantStyles[variant];
  
  const TrendIcon = trend === 'up' ? ArrowUpRight : trend === 'down' ? ArrowDownRight : null;

  return (
    <div
      className={`
        relative overflow-hidden rounded-xl border ${styles.border} ${styles.bg}
        bg-gray-900/80 backdrop-blur-sm p-5
        transition-all duration-300 ${styles.glow}
        group
      `}
    >
      {/* Animated corner accent */}
      <div className={`absolute top-0 right-0 w-24 h-24 ${styles.bg} rounded-bl-full opacity-50 
        group-hover:opacity-100 transition-opacity duration-500`} />
      
      {/* Lightning/Storm accent - CloudLightning for danger, Zap for success */}
      <div className="absolute -top-2 -right-2 opacity-10 group-hover:opacity-30 transition-opacity duration-300">
        {variant === 'danger' 
          ? <CloudLightning className={`w-16 h-16 ${styles.icon}`} strokeWidth={1} />
          : <Zap className={`w-16 h-16 ${styles.icon}`} strokeWidth={1} />
        }
      </div>
      
      <div className="relative z-10">
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className={`p-2 rounded-lg ${styles.bg} border ${styles.border}`}>
            <Icon className={`w-5 h-5 ${styles.icon}`} />
          </div>
          {TrendIcon && trendLabel && (
            <div className={`flex items-center gap-1 text-xs ${
              trend === 'up' ? 'text-neon-green' : 'text-neon-pink'
            }`}>
              <TrendIcon className="w-3 h-3" />
              <span>{trendLabel}</span>
            </div>
          )}
        </div>
        
        {/* Title */}
        <p className="text-gray-400 text-sm font-medium mb-1">{title}</p>
        
        {/* Value */}
        <AnimatedNumber 
          value={value} 
          prefix={prefix ?? (isSats ? '' : '$')}
          suffix={suffix ?? (isSats ? 'sats' : '')}
          isSats={isSats}
          className={`text-3xl ${styles.text}`}
        />
        
        {/* Subtitle */}
        {subtitle && (
          <p className="text-gray-500 text-xs mt-2">{subtitle}</p>
        )}
      </div>
    </div>
  );
}

// Main profit/loss indicator with dramatic styling
function ProfitLossIndicator({ 
  value, 
  isProfitable,
  periodLabel = '30 Days',
}: { 
  value: number; 
  isProfitable: boolean;
  periodLabel?: string;
}) {
  // Determine if we're at break-even (neutral state)
  const isBreakEven = value === 0;
  // Show lightning for profitable OR break-even, thunderstorm only for loss
  const showLightning = isProfitable || isBreakEven;
  
  // Colors: green for profit, cyan for break-even, pink for loss
  const colorClass = isBreakEven 
    ? 'neon-cyan' 
    : isProfitable 
      ? 'neon-green' 
      : 'neon-pink';
  
  return (
    <div
      className={`
        relative overflow-hidden rounded-xl p-6
        ${isBreakEven
          ? 'bg-gradient-to-br from-neon-cyan/10 via-gray-900 to-gray-900 border border-neon-cyan/40'
          : isProfitable 
            ? 'bg-gradient-to-br from-neon-green/10 via-gray-900 to-gray-900 border border-neon-green/40 shadow-neon-green' 
            : 'bg-gradient-to-br from-neon-pink/10 via-gray-900 to-gray-900 border border-neon-pink/40 shadow-neon-pink'
        }
        transition-all duration-500
      `}
    >
      {/* Animated background pattern */}
      <div className="absolute inset-0 opacity-5">
        <div className={`absolute inset-0 ${
          isBreakEven
            ? 'bg-[radial-gradient(circle_at_50%_50%,_#06d6a0_0%,_transparent_50%)]'
            : isProfitable 
              ? 'bg-[radial-gradient(circle_at_50%_50%,_#06ffa5_0%,_transparent_50%)]' 
              : 'bg-[radial-gradient(circle_at_50%_50%,_#ff006e_0%,_transparent_50%)]'
        } animate-pulse-slow`} />
      </div>
      
      {/* Lightning (profit/break-even) or Thunderstorm (loss) accents */}
      <div className="absolute top-2 right-4 opacity-20">
        {showLightning 
          ? <Zap className={`w-12 h-12 text-${colorClass}`} strokeWidth={1.5} />
          : <CloudLightning className="w-12 h-12 text-neon-pink" strokeWidth={1.5} />
        }
      </div>
      <div className="absolute bottom-2 left-4 opacity-10 -rotate-12">
        {showLightning 
          ? <Zap className={`w-20 h-20 text-${colorClass}`} strokeWidth={1} />
          : <CloudLightning className="w-20 h-20 text-neon-pink" strokeWidth={1} />
        }
      </div>
      
      <div className="relative z-10 text-center">
        {/* Status icon */}
        <div className={`
          inline-flex items-center justify-center w-16 h-16 rounded-full mb-4
          ${isBreakEven
            ? 'bg-neon-cyan/20 border-2 border-neon-cyan/50'
            : isProfitable 
              ? 'bg-neon-green/20 border-2 border-neon-green/50' 
              : 'bg-neon-pink/20 border-2 border-neon-pink/50'
          }
        `}>
          {isBreakEven
            ? <Minus className="w-8 h-8 text-neon-cyan" />
            : isProfitable 
              ? <TrendingUp className="w-8 h-8 text-neon-green animate-pulse-slow" />
              : <TrendingDown className="w-8 h-8 text-neon-pink animate-pulse-slow" />
          }
        </div>
        
        {/* Status label */}
        <p className={`text-lg font-semibold mb-2 ${
          isBreakEven ? 'text-neon-cyan' : isProfitable ? 'text-neon-green' : 'text-neon-pink'
        }`}>
          {isBreakEven ? '⚡ BREAK EVEN ⚡' : isProfitable ? '⚡ PROFITABLE ⚡' : '⚠️ NET LOSS'}
        </p>
        
        {/* Big number */}
        <AnimatedNumber 
          value={value}
          prefix={isBreakEven ? '$' : isProfitable ? '+$' : '-$'}
          className={`text-5xl ${isBreakEven ? 'text-neon-cyan' : isProfitable ? 'text-neon-green' : 'text-neon-pink'}`}
        />
        
        {/* Subtext */}
        <p className="text-gray-400 text-sm mt-3">
          {isBreakEven ? 'Break Even' : `Net ${isProfitable ? 'Profit' : 'Loss'}`} ({periodLabel})
        </p>
      </div>
    </div>
  );
}

// Spending breakdown mini-chart with USD + sats bars
function SpendingBreakdown({ 
  breakdown,
  totalSpentSats = 0,
  totalReceivedSats = 0,
  showSats = true,
}: { 
  breakdown: FinancialDashboard['spending_breakdown'];
  totalSpentSats?: number;
  totalReceivedSats?: number;
  showSats?: boolean;
}) {
  const total = breakdown.llm_costs + breakdown.tool_costs + (breakdown.agent_costs || 0) + breakdown.campaign_budgets;
  const totalSats = showSats ? totalSpentSats + totalReceivedSats : 0;
  
  const segments = [
    { label: 'LLM/AI Costs', value: breakdown.llm_costs, color: 'bg-neon-purple', textColor: 'text-neon-purple' },
    { label: 'Tool Costs', value: breakdown.tool_costs, color: 'bg-neon-cyan', textColor: 'text-neon-cyan' },
    { label: 'Agent Costs', value: breakdown.agent_costs || 0, color: 'bg-neon-green', textColor: 'text-neon-green' },
    { label: 'Campaign Budgets', value: breakdown.campaign_budgets, color: 'bg-neon-yellow', textColor: 'text-neon-yellow' },
  ].filter(s => s.value > 0);

  const satsSegments = [
    { label: 'Sats Sent', value: totalSpentSats, color: 'bg-neon-pink', textColor: 'text-neon-pink' },
    { label: 'Sats Received', value: totalReceivedSats, color: 'bg-orange-400', textColor: 'text-orange-400' },
  ].filter(s => s.value > 0);

  if (total === 0 && totalSats === 0) {
    return (
      <div className="rounded-xl border border-gray-700 bg-gray-900/50 p-4">
        <p className="text-gray-500 text-center text-sm">No spending data yet</p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-gray-700/50 bg-gray-900/50 p-4">
      <h4 className="text-gray-400 text-sm font-medium mb-3 flex items-center gap-2">
        <DollarSign className="w-4 h-4 text-neon-cyan" />
        Spending Breakdown
      </h4>
      
      {/* USD Stacked bar */}
      {total > 0 && (
        <>
          <p className="text-gray-500 text-xs mb-1 font-medium">USD</p>
          <div className="h-3 rounded-full bg-gray-800 overflow-hidden flex mb-3">
            {segments.map((seg) => (
              <div
                key={seg.label}
                className={`${seg.color} h-full transition-all duration-500`}
                style={{ width: `${(seg.value / total) * 100}%` }}
              />
            ))}
          </div>
        </>
      )}
      
      {/* Sats Stacked bar */}
      {totalSats > 0 && (
        <>
          <p className="text-gray-500 text-xs mb-1 font-medium flex items-center gap-1">
            <Bitcoin className="w-3 h-3 text-neon-yellow" /> SATS
          </p>
          <div className="h-3 rounded-full bg-gray-800 overflow-hidden flex mb-3">
            {satsSegments.map((seg) => (
              <div
                key={seg.label}
                className={`${seg.color} h-full transition-all duration-500`}
                style={{ width: `${(seg.value / totalSats) * 100}%` }}
              />
            ))}
          </div>
        </>
      )}
      
      {/* Legend */}
      <div className="space-y-2 mt-2">
        {segments.map(seg => (
          <div key={seg.label} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${seg.color}`} />
              <span className="text-gray-400">{seg.label}</span>
            </div>
            <span className={`font-mono ${seg.textColor}`}>
              ${seg.value.toFixed(2)}
            </span>
          </div>
        ))}
        {satsSegments.map(seg => (
          <div key={seg.label} className="flex items-center justify-between text-xs">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${seg.color}`} />
              <span className="text-gray-400">{seg.label}</span>
            </div>
            <span className={`font-mono ${seg.textColor}`}>
              {seg.value.toLocaleString()} sats
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Time window toggle
function TimeWindowToggle({
  days,
  onChange,
}: {
  days: number;
  onChange: (days: number) => void;
}) {
  const options = [
    { label: '30 Days', value: 30 },
    { label: 'All Time', value: 0 },
  ];

  return (
    <div className="inline-flex items-center gap-1 rounded-lg border border-gray-700/50 bg-gray-900/50 p-0.5">
      {options.map(opt => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={`
            flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all duration-200
            ${days === opt.value
              ? 'bg-neon-cyan/15 text-neon-cyan border border-neon-cyan/30 shadow-[0_0_6px_rgba(6,214,160,0.15)]'
              : 'text-gray-500 hover:text-gray-300 border border-transparent'
            }
          `}
        >
          <Calendar className="w-3 h-3" />
          {opt.label}
        </button>
      ))}
    </div>
  );
}

// Sats net indicator (compact version of ProfitLossIndicator for sats)
function SatsNetIndicator({ netSats }: { netSats: number }) {
  const isPositive = netSats > 0;
  const isZero = netSats === 0;
  const isNegative = netSats < 0;
  
  // Label: "Sats Stacked" if positive/zero, "Sats Lost" if negative
  const label = isNegative ? 'Sats Lost' : 'Sats Stacked';
  
  return (
    <div
      className={`
        relative overflow-hidden rounded-xl p-5
        ${isZero
          ? 'bg-gradient-to-br from-neon-yellow/5 via-gray-900 to-gray-900 border border-neon-yellow/30'
          : isPositive
            ? 'bg-gradient-to-br from-neon-green/10 via-gray-900 to-gray-900 border border-neon-green/40 shadow-neon-green'
            : 'bg-gradient-to-br from-neon-pink/10 via-gray-900 to-gray-900 border border-neon-pink/40 shadow-neon-pink'
        }
        transition-all duration-500 group
      `}
    >
      <div className="absolute top-0 right-0 w-24 h-24 opacity-10 group-hover:opacity-30 transition-opacity">
        {isNegative
          ? <CloudLightning className="w-16 h-16 text-neon-pink" strokeWidth={1} />
          : <Zap className={`w-16 h-16 ${isZero ? 'text-neon-yellow' : 'text-neon-green'}`} strokeWidth={1} />
        }
      </div>
      
      <div className="relative z-10">
        <div className="flex items-center justify-between mb-3">
          <div className={`p-2 rounded-lg ${isZero ? 'bg-neon-yellow/10 border border-neon-yellow/30' : isPositive ? 'bg-neon-green/10 border border-neon-green/30' : 'bg-neon-pink/10 border border-neon-pink/30'}`}>
            {isNegative
              ? <CloudLightning className="w-5 h-5 text-neon-pink" />
              : isZero
                ? <Minus className="w-5 h-5 text-neon-yellow" />
                : <TrendingUp className="w-5 h-5 text-neon-green" />
            }
          </div>
        </div>
        <p className="text-gray-400 text-sm font-medium mb-1">{label}</p>
        <AnimatedNumber
          value={netSats}
          prefix={isPositive ? '+' : ''}
          suffix="sats"
          isSats
          className={`text-3xl ${isZero ? 'text-neon-yellow' : isPositive ? 'text-neon-green' : 'text-neon-pink'}`}
        />
        <p className="text-gray-500 text-xs mt-2">
          {isZero ? 'Break even' : isPositive ? 'Net inflow' : 'Net outflow'}
        </p>
      </div>
    </div>
  );
}

// Main component
export function FinancialRollup({ 
  days = 30,
  onDaysChange,
}: { 
  days?: number;
  onDaysChange?: (days: number) => void;
}) {
  const { data: walletConfig } = useQuery({
    queryKey: ['wallet-config'],
    queryFn: () => walletService.getConfig(),
    staleTime: 60000,
    retry: 1,
  });

  const lndEnabled = !!walletConfig?.enabled;

  const { data, isLoading, error } = useQuery({
    queryKey: ['financial-dashboard', days],
    queryFn: () => usageService.getFinancialDashboard(days),
    staleTime: 60000, // 1 minute
    refetchInterval: 120000, // 2 minutes
  });

  const periodLabel = days === 0 ? 'All Time' : `${days} Days`;

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="h-40 rounded-xl bg-gray-900/50 border border-gray-700 animate-pulse flex items-center justify-center">
              <Loader2 className="w-6 h-6 text-neon-cyan animate-spin" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-xl border border-neon-pink/30 bg-neon-pink/5 p-6 text-center">
        <AlertCircle className="w-8 h-8 text-neon-pink mx-auto mb-2" />
        <p className="text-neon-pink">Failed to load financial data</p>
        <p className="text-gray-500 text-sm mt-1">Please try again later</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Time window toggle */}
      {onDaysChange && (
        <div className="flex justify-end">
          <TimeWindowToggle days={days} onChange={onDaysChange} />
        </div>
      )}

      {/* Sats stat cards row (above spending breakdown) — only when LND is enabled */}
      {lndEnabled && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <StatCard
            title="Sats Sent"
            value={data.total_spent_sats}
            subtitle="Lightning + on-chain outflows"
            icon={TrendingDown}
            variant="danger"
            isSats
          />
          <StatCard
            title="Sats Received"
            value={data.total_received_sats}
            subtitle="Lightning + on-chain inflows"
            icon={TrendingUp}
            variant="success"
            isSats
          />
          <SatsNetIndicator netSats={data.net_sats} />
        </div>
      )}
      
      {/* Spending breakdown (USD + sats bars) */}
      <SpendingBreakdown 
        breakdown={data.spending_breakdown}
        totalSpentSats={data.total_spent_sats}
        totalReceivedSats={data.total_received_sats}
        showSats={lndEnabled}
      />
      
      {/* USD stat cards row (below spending breakdown) */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="Total Spent"
          value={data.total_spent}
          subtitle="API costs + Campaign budgets"
          icon={TrendingDown}
          variant="danger"
        />
        <StatCard
          title="Total Earned"
          value={data.total_earned}
          subtitle="Campaign revenue generated"
          icon={TrendingUp}
          variant="success"
        />
        <ProfitLossIndicator 
          value={Math.abs(data.net_profit_loss)} 
          isProfitable={data.is_profitable}
          periodLabel={periodLabel}
        />
      </div>
    </div>
  );
}

export default FinancialRollup;
