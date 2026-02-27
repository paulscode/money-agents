/**
 * WalletWidget Component
 * 
 * Dashboard widget showing LND wallet summary.
 * Displays total balance, lightning/on-chain split, channel count.
 * Clicking navigates to the full wallet detail page.
 * 
 * Only renders when USE_LND is enabled in backend config.
 */
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Zap, LinkIcon, AlertCircle, Loader2, ChevronRight, ShieldAlert } from 'lucide-react';
import bitcoinSvg from '@/assets/bitcoin.svg';
import { walletService } from '@/services/wallet';
import type { WalletSummary } from '@/services/wallet';

function BitcoinIcon({ size = 20 }: { size?: number }) {
  return <img src={bitcoinSvg} alt="₿" width={size} height={size} className="inline-block" />;
}

function formatSats(sats: number): string {
  if (sats >= 100_000_000) {
    return `${(sats / 100_000_000).toFixed(4)} BTC`;
  }
  if (sats >= 1_000_000) {
    return `${(sats / 1_000_000).toFixed(2)}M sats`;
  }
  if (sats >= 1_000) {
    return `${(sats / 1_000).toFixed(1)}k sats`;
  }
  return `${sats.toLocaleString()} sats`;
}

function formatSatsCompact(sats: number): string {
  if (sats >= 100_000_000) {
    return `${(sats / 100_000_000).toFixed(2)} BTC`;
  }
  if (sats >= 1_000_000) {
    return `${(sats / 1_000_000).toFixed(1)}M`;
  }
  if (sats >= 10_000) {
    return `${(sats / 1_000).toFixed(0)}k`;
  }
  if (sats >= 1_000) {
    return `${(sats / 1_000).toFixed(1)}k`;
  }
  return sats.toLocaleString();
}

export function WalletWidget() {
  const navigate = useNavigate();
  
  // First check if LND is enabled
  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['wallet-config'],
    queryFn: () => walletService.getConfig(),
    staleTime: 60000,
    retry: 1,
  });
  
  // Fetch wallet summary only if LND is enabled
  const { data: summary, isLoading: summaryLoading, error } = useQuery({
    queryKey: ['wallet-summary'],
    queryFn: () => walletService.getSummary(),
    enabled: !!config?.enabled,
    refetchInterval: 30000,
    staleTime: 25000,
    retry: 1,
  });

  // Check velocity breaker status
  const { data: breakerStatus } = useQuery({
    queryKey: ['velocity-breaker-status'],
    queryFn: () => walletService.getVelocityBreakerStatus(),
    enabled: !!config?.enabled,
    refetchInterval: 30000,
    staleTime: 25000,
    retry: 1,
  });
  
  // Don't render if LND is not enabled
  if (configLoading) return null;
  if (!config?.enabled) return null;
  
  const isLoading = summaryLoading;
  const isError = !!error;
  
  return (
    <div
      onClick={() => navigate(breakerStatus?.is_tripped ? '/budget' : '/wallet')}
      className={`
        relative overflow-hidden rounded-xl border bg-neon-yellow/5
        backdrop-blur-sm px-4 py-3 transition-all duration-300
        cursor-pointer group min-w-[200px] max-w-xs
        ${breakerStatus?.is_tripped
          ? 'border-red-500/60 hover:border-red-500 hover:bg-red-500/10 hover:shadow-[0_0_20px_rgba(239,68,68,0.2)] animate-pulse-slow'
          : 'border-neon-yellow/30 hover:border-neon-yellow/60 hover:bg-neon-yellow/10 hover:shadow-[0_0_20px_rgba(247,147,26,0.15)]'
        }
      `}
    >
      {/* Background decoration */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute -top-6 -right-6 w-24 h-24 rounded-full bg-neon-yellow/5 group-hover:bg-neon-yellow/10 transition-all" />
        <div className="absolute -bottom-4 -left-4 w-16 h-16 rounded-full bg-orange-500/5 group-hover:bg-orange-500/10 transition-all" />
      </div>
      
      <div className="relative">
        {/* Header */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <BitcoinIcon size={22} />
            <span className="text-xs font-medium uppercase tracking-wider text-gray-400">
              Wallet
            </span>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-500 group-hover:text-neon-yellow transition-colors" />
        </div>
        
        {isLoading ? (
          <WalletLoading />
        ) : isError ? (
          <WalletError />
        ) : summary ? (
          <>
            {breakerStatus?.is_tripped && (
              <div className="flex items-center gap-2 mb-2 px-2 py-1.5 rounded-lg bg-red-500/15 border border-red-500/30">
                <ShieldAlert className="w-4 h-4 text-red-400 shrink-0" />
                <span className="text-xs font-medium text-red-400">
                  Agent payments blocked — review needed
                </span>
              </div>
            )}
            <WalletData summary={summary} />
          </>
        ) : null}
      </div>
    </div>
  );
}

function WalletLoading() {
  return (
    <div className="flex items-center gap-2 py-2">
      <Loader2 className="w-4 h-4 text-neon-yellow animate-spin" />
      <span className="text-sm text-gray-400">Connecting to node...</span>
    </div>
  );
}

function WalletError() {
  return (
    <div className="flex items-center gap-2 py-1">
      <AlertCircle className="w-4 h-4 text-neon-pink" />
      <div>
        <span className="text-sm text-neon-pink">Node offline</span>
        <p className="text-[10px] text-gray-500">Click to view details</p>
      </div>
    </div>
  );
}

function WalletData({ summary }: { summary: WalletSummary }) {
  const { totals, node_info } = summary;
  const totalBtc = totals.total_balance_sats / 100_000_000;
  
  return (
    <div className="space-y-2">
      {/* Total Balance */}
      <div>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold font-mono tabular-nums text-white">
            {totalBtc >= 0.01 
              ? `₿${totalBtc.toFixed(4)}`
              : formatSats(totals.total_balance_sats)
            }
          </span>
        </div>
        {totalBtc >= 0.01 && (
          <p className="text-xs text-gray-500 font-mono">
            {totals.total_balance_sats.toLocaleString()} sats
          </p>
        )}
      </div>
      
      {/* Balance breakdown */}
      <div className="flex items-center gap-3 text-xs min-w-0">
        {/* Lightning */}
        <div className="flex items-center gap-1 shrink-0">
          <Zap className="w-3 h-3 text-neon-yellow shrink-0" />
          <span className="text-gray-400 truncate">
            {formatSatsCompact(totals.lightning_local_sats)}
          </span>
        </div>
        
        {/* On-chain */}
        <div className="flex items-center gap-1 shrink-0">
          <LinkIcon className="w-3 h-3 text-orange-400 shrink-0" />
          <span className="text-gray-400 truncate">
            {formatSatsCompact(totals.onchain_sats)}
          </span>
        </div>
        
        {/* Channels */}
        <div className="flex items-center gap-1 ml-auto shrink-0">
          <span className={`inline-block w-1.5 h-1.5 rounded-full ${totals.synced ? 'bg-neon-green' : 'bg-neon-yellow animate-pulse'}`} />
          <span className="text-gray-500">
            {totals.num_active_channels} ch
          </span>
        </div>
      </div>
      
      {/* Node alias */}
      {node_info?.alias && (
        <p className="text-[10px] text-gray-500 truncate">
          {node_info.alias}
        </p>
      )}
    </div>
  );
}

export default WalletWidget;
