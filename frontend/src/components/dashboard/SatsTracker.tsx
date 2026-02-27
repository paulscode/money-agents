/**
 * SatsTracker Component
 * 
 * Displays real-time "sats per dollar" with epic neon lightning aesthetic.
 * "We're still early - stack sats and zoom out"
 */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState, useRef } from 'react';
import { TrendingUp, TrendingDown, Minus, Zap, CloudLightning } from 'lucide-react';
import { AreaChart, Area, ResponsiveContainer, YAxis } from 'recharts';
import bitcoinSvg from '@/assets/bitcoin.svg';
import { logError } from '@/lib/logger';

// Bitcoin icon using the official logo
function BitcoinIcon({ className = '', size = 24 }: { className?: string; size?: number }) {
  return (
    <img 
      src={bitcoinSvg} 
      alt="Bitcoin" 
      width={size} 
      height={size} 
      className={className}
    />
  );
}

// Animated lightning bolt
function LightningBolt({ className = '' }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className}>
      <path 
        d="M13 2L4.5 13H11.5L10.5 22L19 11H12.5L13 2Z" 
        fill="currentColor"
        className="animate-pulse"
      />
    </svg>
  );
}

interface SatsData {
  satsPerDollar: number;
  btcPrice: number;
  change24h: number;
  timestamp: number;
}

interface HistoryPoint {
  time: number;
  sats: number;
}

async function fetchBitcoinPrice(): Promise<SatsData> {
  try {
    // Use CoinGecko free API
    const response = await fetch(
      'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true'
    );
    const data = await response.json();
    const btcPrice = data.bitcoin.usd;
    const change24h = data.bitcoin.usd_24h_change || 0;
    
    // Calculate sats per dollar (100,000,000 sats = 1 BTC)
    const satsPerDollar = Math.round(100_000_000 / btcPrice);
    
    return {
      satsPerDollar,
      btcPrice,
      change24h,
      timestamp: Date.now(),
    };
  } catch (error) {
    logError('Failed to fetch Bitcoin price:', error);
    throw error;
  }
}

export function SatsTracker() {
  const [history, setHistory] = useState<HistoryPoint[]>([]);
  const [priceDirection, setPriceDirection] = useState<'up' | 'down' | 'neutral'>('neutral');
  const [flash, setFlash] = useState(false);
  const prevSatsRef = useRef<number | null>(null);
  
  const { data, isLoading, error } = useQuery({
    queryKey: ['bitcoin-price'],
    queryFn: fetchBitcoinPrice,
    refetchInterval: 30000, // Refresh every 30 seconds
    staleTime: 25000,
  });
  
  // Update history and detect price changes
  useEffect(() => {
    if (data) {
      const newPoint: HistoryPoint = {
        time: data.timestamp,
        sats: data.satsPerDollar,
      };
      
      setHistory(prev => {
        const updated = [...prev, newPoint].slice(-20); // Keep last 20 points
        return updated;
      });
      
      // Detect direction change
      if (prevSatsRef.current !== null) {
        if (data.satsPerDollar > prevSatsRef.current) {
          setPriceDirection('up');
          setFlash(true);
        } else if (data.satsPerDollar < prevSatsRef.current) {
          setPriceDirection('down');
          setFlash(true);
        }
        // Clear flash after animation
        setTimeout(() => setFlash(false), 1000);
      }
      prevSatsRef.current = data.satsPerDollar;
    }
  }, [data]);
  
  // Direction indicator colors
  const directionColors = {
    up: {
      bg: 'bg-neon-green/20',
      border: 'border-neon-green/50',
      text: 'text-neon-green',
      glow: 'shadow-neon-green',
    },
    down: {
      bg: 'bg-neon-pink/20',
      border: 'border-neon-pink/50',
      text: 'text-neon-pink',
      glow: 'shadow-neon-pink',
    },
    neutral: {
      bg: 'bg-neon-cyan/20',
      border: 'border-neon-cyan/50',
      text: 'text-neon-cyan',
      glow: '',
    },
  };
  
  const colors = directionColors[priceDirection];
  const DirectionIcon = priceDirection === 'up' ? TrendingUp : priceDirection === 'down' ? TrendingDown : Minus;
  
  // Sats going UP means BTC price going DOWN (inverse relationship)
  // When sats/dollar increases, that's good for stackers (cheaper sats)
  const satsChangeIsPositive = priceDirection === 'up';
  
  // Invert the 24h change for sats display (if BTC went up 5%, sats went down ~5%)
  const satsChange24h = data ? -data.change24h : 0;
  
  // Show lightning for up or neutral, thunderstorm only for down
  const showLightning = priceDirection !== 'down';
  
  // Show fallback state if price data unavailable
  if (error || (!isLoading && !data)) {
    return (
      <div className="relative overflow-hidden rounded-xl border border-gray-700/50 bg-gray-800/30 backdrop-blur-sm px-4 py-3">
        <div className="flex items-center gap-2">
          <BitcoinIcon size={20} />
          <div>
            <p className="text-sm text-gray-400">Sats / USD</p>
            <p className="text-xs text-gray-500">Price data unavailable</p>
          </div>
        </div>
      </div>
    );
  }
  
  return (
    <div 
      className={`
        relative overflow-hidden rounded-xl border ${colors.border} ${colors.bg}
        backdrop-blur-sm px-4 py-3 transition-all duration-500
        ${flash ? colors.glow : ''}
      `}
    >
      {/* Animated background icons - lightning when up/neutral, thunderstorm when down */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        {showLightning ? (
          <>
            <LightningBolt className="absolute -top-4 -right-4 w-24 h-24 text-neon-yellow/5 rotate-12" />
            <LightningBolt className="absolute -bottom-4 -left-4 w-20 h-20 text-neon-cyan/5 -rotate-12" />
          </>
        ) : (
          <>
            <CloudLightning className="absolute -top-4 -right-4 w-24 h-24 text-neon-pink/10 rotate-12" />
            <CloudLightning className="absolute -bottom-4 -left-4 w-20 h-20 text-neon-pink/5 -rotate-12" />
          </>
        )}
      </div>
      
      <div className="relative flex items-center gap-4">
        {/* Bitcoin icon with glow */}
        <div className="relative">
          <BitcoinIcon size={32} className="drop-shadow-[0_0_8px_#f7931a]" />
          <Zap className="absolute -top-1 -right-1 w-3 h-3 text-neon-yellow animate-pulse" />
        </div>
        
        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-gray-400 text-xs font-medium uppercase tracking-wider">
              For just $1,
            </span>
            {/* Direction indicator - shows sats change (inverted from BTC) */}
            <div className={`flex items-center gap-1 px-1.5 py-0.5 rounded ${colors.bg}`}>
              <DirectionIcon className={`w-3 h-3 ${colors.text}`} />
              {data && (
                <span className={`text-xs font-mono ${colors.text}`}>
                  {satsChange24h >= 0 ? '+' : ''}
                  {satsChange24h.toFixed(1)}%
                </span>
              )}
            </div>
          </div>
          
          {/* Sats count with flash effect */}
          <div className="flex items-baseline gap-2">
            {isLoading ? (
              <div className="h-7 w-24 bg-gray-700 rounded animate-pulse" />
            ) : (
              <span 
                className={`
                  text-2xl font-bold font-mono tabular-nums
                  ${flash ? colors.text : 'text-white'}
                  transition-colors duration-300
                `}
              >
                {data?.satsPerDollar.toLocaleString()}
              </span>
            )}
            <span className="text-neon-yellow text-sm font-medium">sats</span>
          </div>
          
          {/* Slogan - split into two lines */}
          <p className="text-gray-500 text-[10px] italic tracking-wide mt-0.5">
            we're still early —
          </p>
          <p className="text-gray-500 text-[10px] italic tracking-wide">
            <span className="text-neon-cyan">stack sats</span> and{' '}
            <span className="text-neon-purple">zoom out</span>
          </p>
        </div>
        
        {/* Mini spark chart - tracks sats/dollar (UP = good for stackers) */}
        <div className="w-20 h-12 flex-shrink-0">
          {history.length > 1 ? (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={history} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                <defs>
                  <linearGradient id="satsGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop 
                      offset="5%" 
                      stopColor={satsChangeIsPositive ? '#06ffa5' : '#ff006e'} 
                      stopOpacity={0.4}
                    />
                    <stop 
                      offset="95%" 
                      stopColor={satsChangeIsPositive ? '#06ffa5' : '#ff006e'} 
                      stopOpacity={0}
                    />
                  </linearGradient>
                </defs>
                <YAxis domain={['dataMin', 'dataMax']} hide />
                <Area
                  type="monotone"
                  dataKey="sats"
                  stroke={satsChangeIsPositive ? '#06ffa5' : '#ff006e'}
                  strokeWidth={1.5}
                  fill="url(#satsGradient)"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div className="w-full h-full flex items-center justify-center">
              <div className="w-1 h-1 rounded-full bg-neon-cyan animate-ping" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default SatsTracker;
