/**
 * WalletPage - Full Bitcoin wallet detail page
 * 
 * Shows:
 * - Node info (alias, pubkey, sync status, version)
 * - Balance breakdown (on-chain, lightning local/remote, pending)
 * - Channel list with capacity visualization
 * - Recent payments and invoices
 * - Recent on-chain transactions
 */
import { Layout } from '@/components/layout/Layout';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, useCallback, useEffect } from 'react';
import {
  Zap, LinkIcon, ArrowUpRight, ArrowDownLeft, Copy, Check,
  AlertCircle, Loader2, Radio, GitBranch, Shield, Clock,
  ChevronDown, ChevronUp, Download, X, RefreshCw, Send,
  CheckCircle2, Snowflake, Info, ArrowRight, ShieldCheck,
  ExternalLink, Plus, History
} from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import bitcoinSvg from '@/assets/bitcoin.svg';
import { walletService } from '@/services/wallet';
import { InvoiceQR } from '@/components/shared/InvoiceQR';
import type {
  WalletSummary, WalletTotals, OnchainBalance,
  Channel, Payment, Invoice, OnchainTransaction, RecommendedFees,
  DecodedInvoice, BoltzSwapStatus
} from '@/services/wallet';

function BitcoinIcon({ size = 24 }: { size?: number }) {
  return <img src={bitcoinSvg} alt="₿" width={size} height={size} className="inline-block" />;
}

function formatSats(sats: number): string {
  return sats.toLocaleString();
}

function formatBtc(sats: number): string {
  return (sats / 100_000_000).toFixed(8);
}

function formatDate(unixTimestamp: number): string {
  if (!unixTimestamp) return '—';
  return new Date(unixTimestamp * 1000).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  if (days > 0) return `${days}d ${hours}h`;
  const mins = Math.floor((seconds % 3600) / 60);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <button onClick={handleCopy} className="p-1 text-gray-500 hover:text-neon-cyan transition-colors" title="Copy">
      {copied ? <Check className="w-3 h-3 text-neon-green" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

export function WalletPage() {
  // Check wallet config first
  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['wallet-config'],
    queryFn: () => walletService.getConfig(),
    staleTime: 60000,
  });

  if (configLoading) {
    return (
      <Layout>
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-8 h-8 text-neon-cyan animate-spin" />
        </div>
      </Layout>
    );
  }

  if (!config?.enabled) {
    return (
      <Layout>
        <WalletDisabled />
      </Layout>
    );
  }

  return (
    <Layout>
      <WalletContent mempoolUrl={/^https?:\/\//i.test(config?.mempool_url || '') ? config!.mempool_url : 'https://mempool.space'} />
    </Layout>
  );
}

function WalletDisabled() {
  return (
    <div className="max-w-2xl mx-auto py-16 text-center space-y-4">
      <BitcoinIcon size={48} />
      <h1 className="text-2xl font-bold text-white">Bitcoin Wallet Not Configured</h1>
      <p className="text-gray-400">
        Connect your LND Lightning node to enable wallet features.
        Run the setup wizard to configure your node connection.
      </p>
      <div className="bg-navy-800 border border-navy-600 rounded-lg p-4 text-left text-sm space-y-2">
        <p className="text-gray-300 font-medium">Quick setup:</p>
        <code className="block text-neon-cyan bg-navy-900 p-2 rounded text-xs">
          python start.py  # Choose "Update Configuration"
        </code>
        <p className="text-gray-400 text-xs">
          Or set <span className="text-neon-yellow">USE_LND=true</span> in your .env file with your LND REST URL and macaroon.
        </p>
      </div>
    </div>
  );
}

function WalletContent({ mempoolUrl }: { mempoolUrl: string }) {
  const [activeTab, setActiveTab] = useState<'channels' | 'payments' | 'invoices' | 'onchain'>('channels');
  const [showReceive, setShowReceive] = useState(false);
  const [showSend, setShowSend] = useState(false);
  const [showReceiveInvoice, setShowReceiveInvoice] = useState(false);
  const [showColdStorage, setShowColdStorage] = useState(false);
  const [showOpenChannel, setShowOpenChannel] = useState(false);
  const [balanceInBtc, setBalanceInBtc] = useState(false);

  const { data: summary, isLoading: summaryLoading, error: summaryError } = useQuery({
    queryKey: ['wallet-summary'],
    queryFn: () => walletService.getSummary(),
    refetchInterval: 30000,
  });

  const { data: channelsData } = useQuery({
    queryKey: ['wallet-channels'],
    queryFn: () => walletService.getChannels(),
    enabled: activeTab === 'channels',
    refetchInterval: 60000,
  });

  const { data: paymentsData } = useQuery({
    queryKey: ['wallet-payments'],
    queryFn: () => walletService.getPayments(30),
    enabled: activeTab === 'payments',
    refetchInterval: 30000,
  });

  const { data: invoicesData } = useQuery({
    queryKey: ['wallet-invoices'],
    queryFn: () => walletService.getInvoices(30),
    enabled: activeTab === 'invoices',
    refetchInterval: 30000,
  });

  const { data: txnsData } = useQuery({
    queryKey: ['wallet-transactions'],
    queryFn: () => walletService.getTransactions(30),
    enabled: activeTab === 'onchain',
    refetchInterval: 30000,
  });

  const { data: feesData } = useQuery({
    queryKey: ['wallet-fees'],
    queryFn: () => walletService.getFees(),
    refetchInterval: 60000,
    retry: 1,
  });

  if (summaryLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-8 h-8 text-neon-yellow animate-spin" />
        <span className="ml-3 text-gray-400">Connecting to LND node...</span>
      </div>
    );
  }

  if (summaryError || !summary) {
    return (
      <div className="max-w-2xl mx-auto py-16 text-center space-y-4">
        <AlertCircle className="w-12 h-12 text-neon-pink mx-auto" />
        <h1 className="text-2xl font-bold text-white">Connection Failed</h1>
        <p className="text-gray-400">
          Unable to connect to your LND node. Check that your node is running and the REST URL, 
          macaroon, and network settings are correct.
        </p>
        <p className="text-xs text-gray-500">
          {summaryError instanceof Error ? summaryError.message : 'Unknown error'}
        </p>
      </div>
    );
  }

  const { totals, node_info, onchain } = summary;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BitcoinIcon size={32} />
        <div>
          <h1 className="text-3xl font-bold text-neon-yellow">Bitcoin Wallet</h1>
          {node_info?.alias && (
            <p className="text-gray-400 text-sm">{node_info.alias}</p>
          )}
        </div>
        {/* Sync indicator */}
        <div className="ml-auto flex items-center gap-2">
          <span className={`inline-block w-2 h-2 rounded-full ${totals.synced ? 'bg-neon-green' : 'bg-neon-yellow animate-pulse'}`} />
          <span className="text-sm text-gray-400">
            {totals.synced ? 'Synced' : 'Syncing...'}
          </span>
        </div>
      </div>

      {/* Fund Wallet Dialog */}
      {showReceive && (
        <FundWalletDialog onClose={() => setShowReceive(false)} />
      )}

      {/* Send Payment Dialog */}
      {showSend && (
        <SendPaymentDialog onClose={() => setShowSend(false)} />
      )}

      {/* Receive Invoice Dialog */}
      {showReceiveInvoice && (
        <ReceiveInvoiceDialog onClose={() => setShowReceiveInvoice(false)} />
      )}

      {/* Cold Storage Dialog */}
      {showColdStorage && summary && (
        <ColdStorageDialog
          onClose={() => setShowColdStorage(false)}
          totals={totals}
          onchain={onchain}
          mempoolUrl={mempoolUrl}
        />
      )}

      {/* Open Channel Dialog */}
      {showOpenChannel && summary && (
        <OpenChannelDialog
          onClose={() => setShowOpenChannel(false)}
          onchain={onchain}
          totals={totals}
          mempoolUrl={mempoolUrl}
        />
      )}

      {/* Balance Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Balance */}
        <div className="card bg-gradient-to-br from-neon-yellow/10 to-orange-500/5 border-neon-yellow/30">
          <div className="flex items-center gap-2 mb-2">
            <BitcoinIcon size={18} />
            <span className="text-sm text-gray-400">Total Balance</span>
            <button
              onClick={() => setShowColdStorage(true)}
              className="
                ml-auto flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-all
                bg-blue-500/10 border border-blue-500/20 text-blue-400
                hover:bg-blue-500/20 hover:border-blue-500/40 hover:text-blue-300
              "
            >
              <Snowflake className="w-3 h-3" />
              Cold Storage ≫
            </button>
          </div>
          <div
            onClick={() => setBalanceInBtc(!balanceInBtc)}
            className="cursor-pointer select-none"
            title="Click to toggle between sats and BTC"
          >
            {balanceInBtc ? (
              <>
                <p className="text-2xl font-bold font-mono text-white">
                  ₿{formatBtc(totals.total_balance_sats)}
                </p>
                <p className="text-xs text-gray-500 font-mono">
                  {formatSats(totals.total_balance_sats)} sats
                </p>
              </>
            ) : (
              <>
                <p className="text-2xl font-bold font-mono text-white">
                  {formatSats(totals.total_balance_sats)} <span className="text-base text-gray-400">sats</span>
                </p>
                <p className="text-xs text-gray-500 font-mono">
                  ₿{formatBtc(totals.total_balance_sats)}
                </p>
              </>
            )}
          </div>
        </div>

        {/* On-chain Balance */}
        <div className="card border-orange-500/20">
          <div className="flex items-center gap-2 mb-2">
            <LinkIcon className="w-4 h-4 text-orange-400" />
            <span className="text-sm text-gray-400">On-chain</span>
            <button
              onClick={() => setShowReceive(true)}
              className="
                ml-auto flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-all
                bg-neon-green/10 border border-neon-green/20 text-neon-green
                hover:bg-neon-green/20 hover:border-neon-green/40 hover:text-neon-green
              "
            >
              <Download className="w-3 h-3" />
              Add Funds
            </button>
          </div>
          <p className="text-xl font-bold font-mono text-white">
            {formatSats(totals.onchain_sats)}
          </p>
          <p className="text-xs text-gray-500">
            sats confirmed
            {(totals.unconfirmed_sats > 0) && (
              <span className="text-neon-yellow ml-2">+{formatSats(totals.unconfirmed_sats)} pending</span>
            )}
          </p>
        </div>

        {/* Lightning Balance */}
        <div className="card border-neon-yellow/20">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-4 h-4 text-neon-yellow" />
            <span className="text-sm text-gray-400">Lightning Outbound</span>
            <button
              onClick={() => setShowSend(true)}
              className="
                ml-auto flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-all
                bg-neon-cyan/10 border border-neon-cyan/20 text-neon-cyan
                hover:bg-neon-cyan/20 hover:border-neon-cyan/40 hover:text-neon-cyan
              "
            >
              <Send className="w-3 h-3" />
              Send
            </button>
          </div>
          <p className="text-xl font-bold font-mono text-white">
            {formatSats(totals.lightning_local_sats)}
          </p>
          <p className="text-xs text-gray-500">
            sats spendable
          </p>
        </div>

        {/* Inbound Liquidity */}
        <div className="card border-neon-cyan/20">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-4 h-4 text-neon-yellow" />
            <span className="text-sm text-gray-400">Lightning Inbound</span>
            <button
              onClick={() => setShowReceiveInvoice(true)}
              className="
                ml-auto flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-all
                bg-neon-green/10 border border-neon-green/20 text-neon-green
                hover:bg-neon-green/20 hover:border-neon-green/40 hover:text-neon-green
              "
            >
              <ArrowDownLeft className="w-3 h-3" />
              Receive
            </button>
          </div>
          <p className="text-xl font-bold font-mono text-white">
            {formatSats(totals.lightning_remote_sats)}
          </p>
          <p className="text-xs text-gray-500">
            sats receivable
          </p>
        </div>
      </div>

      {/* Fee Estimates */}
      {feesData?.priorities && <FeeEstimateBar fees={feesData} mempoolUrl={mempoolUrl} />}

      {/* Node Info */}
      {node_info && <NodeInfoCard info={node_info} />}

      {/* Tabs */}
      <div className="border-b border-navy-700">
        <nav className="flex gap-6">
          {[
            { id: 'channels' as const, label: 'Channels', icon: GitBranch, count: totals.num_active_channels },
            { id: 'payments' as const, label: 'Payments', icon: ArrowUpRight },
            { id: 'invoices' as const, label: 'Invoices', icon: ArrowDownLeft },
            { id: 'onchain' as const, label: 'On-chain', icon: LinkIcon },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`
                flex items-center gap-2 pb-3 px-1 text-sm font-medium border-b-2 transition-colors
                ${activeTab === tab.id
                  ? 'border-neon-yellow text-neon-yellow'
                  : 'border-transparent text-gray-400 hover:text-gray-200'
                }
              `}
            >
              <tab.icon className="w-4 h-4" />
              {tab.label}
              {tab.count !== undefined && (
                <span className="text-xs bg-navy-700 px-1.5 py-0.5 rounded-full">{tab.count}</span>
              )}
            </button>
          ))}
        </nav>
      </div>

      {/* Tab Content */}
      <div>
        {activeTab === 'channels' && <ChannelsTab channels={channelsData?.channels} mempoolUrl={mempoolUrl} onOpenChannel={() => setShowOpenChannel(true)} />}
        {activeTab === 'payments' && <PaymentsTab payments={paymentsData?.payments} />}
        {activeTab === 'invoices' && <InvoicesTab invoices={invoicesData?.invoices} />}
        {activeTab === 'onchain' && <OnchainTab transactions={txnsData?.transactions} mempoolUrl={mempoolUrl} />}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
 * Open Channel Dialog
 * ───────────────────────────────────────────────────────────── */

const POPULAR_NODES = [
  {
    name: 'ACINQ',
    address: '03864ef025fde8fb587d989186ce6a4a186895ee44a926bfc370e2c366597a3f8f@3.33.236.230:9735',
    minChannelSats: 500_000,
    description: 'Eclair team, large routing node',
  },
  {
    name: 'Boltz',
    address: '026165850492521f4ac8abd9bd8088123446d126f648ca35e60f88177dc149ceb2@[2803:6900:581::1:c175:f0ad]:9735',
    minChannelSats: 1_000_000,
    description: 'Non-custodial swap exchange',
  },
  {
    name: 'Kraken',
    address: '02f1a8c87607f415c8f22c00593002775941dea48869ce23096af27b0cfdcc0b69@52.13.118.208:9735',
    minChannelSats: 1_000_000,
    description: 'Major exchange, high liquidity',
  },
  {
    name: 'Voltage',
    address: '031f2669adab71548fad4432277a0d90233e3bc07ac29cfb0b3e01bd3fb26cb9fa@44.242.118.94:9735',
    minChannelSats: 1_000_000,
    description: 'Lightning infrastructure provider',
  },
  {
    name: 'WalletOfSatoshi',
    address: '035e4ff418fc8b5554c5d9eea66396c227bd429a3251c8cbc711002ba215bfc226@170.75.163.209:9735',
    minChannelSats: 1_000_000,
    description: 'Popular mobile wallet',
  },
] as const;

interface OpenChannelDialogProps {
  onClose: () => void;
  onchain: OnchainBalance | null;
  totals: WalletTotals;
  mempoolUrl: string;
}

function OpenChannelDialog({ onClose, onchain, totals, mempoolUrl }: OpenChannelDialogProps) {
  const queryClient = useQueryClient();

  // Form state
  const [nodeAddress, setNodeAddress] = useState('');
  const [amountSats, setAmountSats] = useState('');
  const [feePriority, setFeePriority] = useState<'low' | 'medium' | 'high'>('medium');
  const [step, setStep] = useState<'form' | 'confirm' | 'result'>('form');
  const [openResult, setOpenResult] = useState<{ success: boolean; funding_txid?: string; error?: string } | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  // Fee data
  const { data: feesData } = useQuery({
    queryKey: ['wallet-fees'],
    queryFn: () => walletService.getFees(),
    staleTime: 30000,
  });

  // Balance calculations
  const confirmedOnchain = onchain?.confirmed_balance ?? totals.onchain_sats;
  const lockedBalance = onchain?.locked_balance ?? 0;
  const reservedAnchor = onchain?.reserved_balance_anchor_chan ?? 0;
  const availableBalance = Math.max(0, confirmedOnchain - lockedBalance - reservedAnchor);

  const parsedAmount = parseInt(amountSats, 10);
  const amountValid = !isNaN(parsedAmount) && parsedAmount > 0;

  // Validate node address format: pubkey@host:port
  const nodeAddressValid = /^[0-9a-fA-F]{66}@.+:\d+$/.test(nodeAddress.trim()) ||
                           /^[0-9a-fA-F]{66}@\[.+\]:\d+$/.test(nodeAddress.trim());

  // Find selected popular node for min channel info
  const matchedNode = POPULAR_NODES.find(n => n.address === nodeAddress);
  const amountBelowMin = amountValid && matchedNode && parsedAmount < matchedNode.minChannelSats;
  const amountExceedsBalance = amountValid && parsedAmount > availableBalance;

  // Rough fee estimate: ~250 vbytes for a funding tx
  const feeRate = feesData?.priorities?.[feePriority]?.sat_per_vbyte ?? 0;
  const estimatedFee = feeRate * 250;
  const totalCost = amountValid ? parsedAmount + estimatedFee : 0;
  const totalExceedsBalance = amountValid && totalCost > availableBalance;

  const canProceed = nodeAddressValid && amountValid && !amountBelowMin && !amountExceedsBalance && !totalExceedsBalance;

  // Open channel mutation
  const openMutation = useMutation({
    mutationFn: async () => {
      const satPerVbyte = feesData?.priorities?.[feePriority]?.sat_per_vbyte;
      return walletService.openChannel({
        node_address: nodeAddress.trim(),
        local_funding_amount: parsedAmount,
        sat_per_vbyte: satPerVbyte,
      });
    },
    onSuccess: (data) => {
      setOpenResult({ success: true, funding_txid: data.funding_txid });
      setStep('result');
      queryClient.invalidateQueries({ queryKey: ['wallet-summary'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-channels'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-pending-channels-detail'] });
    },
    onError: (error) => {
      const message = error instanceof Error ? error.message : 'Channel open failed.';
      setOpenResult({ success: false, error: message });
      setStep('result');
    },
  });

  const handleSelectNode = (node: typeof POPULAR_NODES[number]) => {
    setNodeAddress(node.address);
    setSelectedNode(node.name);
    if (!amountSats || parseInt(amountSats, 10) < node.minChannelSats) {
      setAmountSats(String(node.minChannelSats));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      {/* Dialog */}
      <div className="relative w-full max-w-lg mx-4 card border-blue-500/30 bg-gradient-to-br from-navy-800 to-navy-900 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200 max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <GitBranch className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Open Channel</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-navy-700 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-5">
          Open a new Lightning channel to increase routing and payment capacity.
        </p>

        {/* ── Form Step ── */}
        {step === 'form' && (
          <div className="space-y-4">
            {/* Popular Nodes */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-2 block">
                Popular Nodes
              </label>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {POPULAR_NODES.map((node) => (
                  <button
                    key={node.name}
                    onClick={() => handleSelectNode(node)}
                    className={`
                      px-3 py-2.5 rounded-lg border text-left transition-all
                      ${selectedNode === node.name && nodeAddress === node.address
                        ? 'border-blue-500/50 bg-blue-500/10 text-white'
                        : 'border-navy-600 bg-navy-800/50 text-gray-400 hover:border-navy-500 hover:text-gray-300'
                      }
                    `}
                  >
                    <span className="text-xs font-medium block">{node.name}</span>
                    <span className="text-[10px] block mt-0.5 text-gray-500">
                      Min {formatSats(node.minChannelSats)}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Node Address */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">
                Node Address
              </label>
              <input
                type="text"
                value={nodeAddress}
                onChange={(e) => {
                  setNodeAddress(e.target.value);
                  setSelectedNode(null);
                }}
                placeholder="pubkey@host:port"
                className="
                  w-full px-3 py-2.5 rounded-lg bg-navy-900 border border-navy-600
                  text-white font-mono text-[11px] placeholder-gray-600
                  focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                  transition-colors
                "
                spellCheck={false}
                autoComplete="off"
              />
              {nodeAddress.trim().length > 0 && !nodeAddressValid && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Expected format: pubkey@host:port
                </p>
              )}
              {matchedNode && (
                <p className="text-[10px] text-gray-500 mt-1">
                  {matchedNode.description}
                </p>
              )}
            </div>

            {/* Available Balance */}
            <div className="bg-navy-900/50 border border-navy-700 border-l-2 border-l-neon-green/30 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">Available on-chain</span>
                <span className="text-sm font-mono text-neon-green/90 font-medium">
                  {formatSats(availableBalance)} sats
                </span>
              </div>
            </div>

            {/* Channel Amount */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs text-gray-500 uppercase tracking-wide">
                  Channel Size (sats)
                </label>
                <button
                  onClick={() => {
                    const max = Math.max(0, availableBalance - estimatedFee - 1000);
                    setAmountSats(max.toString());
                  }}
                  className="text-[10px] text-blue-400 hover:text-blue-300 font-medium transition-colors"
                >
                  Send Max
                </button>
              </div>
              <input
                type="text"
                inputMode="numeric"
                value={amountSats}
                onChange={(e) => {
                  const val = e.target.value.replace(/[^0-9]/g, '');
                  setAmountSats(val);
                }}
                placeholder={matchedNode ? formatSats(matchedNode.minChannelSats) : '0'}
                className="
                  w-full px-3 py-2.5 rounded-lg bg-navy-900 border border-navy-600
                  text-white font-mono text-sm placeholder-gray-600
                  focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                  transition-colors
                "
              />
              {amountValid && (
                <p className="text-[10px] text-gray-500 mt-1">
                  ≈ ₿{formatBtc(parsedAmount)}
                </p>
              )}
              {amountBelowMin && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  {matchedNode!.name} requires minimum {formatSats(matchedNode!.minChannelSats)} sats
                </p>
              )}
              {amountExceedsBalance && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Exceeds available balance ({formatSats(availableBalance)} sats)
                </p>
              )}
            </div>

            {/* Fee Priority */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">
                Fee Priority
              </label>
              <div className="grid grid-cols-3 gap-2">
                {([
                  { value: 'low' as const, label: 'Low', desc: '~1 hour', color: 'text-neon-green' },
                  { value: 'medium' as const, label: 'Medium', desc: '~30 min', color: 'text-neon-yellow' },
                  { value: 'high' as const, label: 'High', desc: 'Next block', color: 'text-orange-400' },
                ]).map((p) => (
                  <button
                    key={p.value}
                    onClick={() => setFeePriority(p.value)}
                    className={`
                      px-3 py-2 rounded-lg border text-left transition-all
                      ${feePriority === p.value
                        ? 'border-blue-500/50 bg-blue-500/10 text-white'
                        : 'border-navy-600 bg-navy-800/50 text-gray-400 hover:border-navy-500 hover:text-gray-300'
                      }
                    `}
                  >
                    <span className="text-xs font-medium block">{p.label}</span>
                    <span className={`text-[10px] block mt-0.5 ${feePriority === p.value ? p.color : 'text-gray-500'}`}>
                      {p.desc}
                      {feesData?.priorities?.[p.value] && (
                        <span className="ml-1">({feesData.priorities[p.value].sat_per_vbyte} sat/vB)</span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Cost Summary */}
            {amountValid && estimatedFee > 0 && (
              <div className="bg-navy-900/50 border border-navy-700 rounded-lg p-3 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Channel capacity</span>
                  <span className="text-gray-300 font-mono">{formatSats(parsedAmount)} sats</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Est. funding fee</span>
                  <span className="text-gray-300 font-mono">~{formatSats(estimatedFee)} sats</span>
                </div>
                <div className="border-t border-navy-700 pt-1.5 flex items-center justify-between text-xs">
                  <span className="text-gray-400 font-medium">Est. total</span>
                  <span className="text-white font-mono font-medium">~{formatSats(totalCost)} sats</span>
                </div>
                {totalExceedsBalance && (
                  <p className="text-[10px] text-neon-pink flex items-center gap-1 pt-1">
                    <AlertCircle className="w-3 h-3" />
                    Channel size + fee exceeds available balance
                  </p>
                )}
              </div>
            )}

            {/* Proceed Button */}
            <button
              onClick={() => setStep('confirm')}
              disabled={!canProceed}
              className="
                w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg
                bg-blue-500/15 border border-blue-500/30 text-blue-400 font-medium
                hover:bg-blue-500/25 hover:border-blue-500/50 transition-all
                disabled:opacity-30 disabled:cursor-not-allowed
              "
            >
              <ArrowRight className="w-4 h-4" />
              Review Channel
            </button>
          </div>
        )}

        {/* ── Confirm Step ── */}
        {step === 'confirm' && (
          <div className="space-y-4">
            <div className="bg-navy-900/50 border border-blue-500/20 rounded-lg p-4 space-y-3">
              <h3 className="text-sm font-medium text-white flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-blue-400" />
                Confirm Channel Open
              </h3>

              <div className="space-y-2">
                <div className="flex items-start justify-between text-xs gap-2">
                  <span className="text-gray-500 flex-shrink-0">Peer</span>
                  <span className="text-gray-300 font-mono text-[11px] text-right break-all">
                    {matchedNode ? matchedNode.name : nodeAddress.split('@')[0].slice(0, 16) + '...'}
                  </span>
                </div>
                {matchedNode && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500">Description</span>
                    <span className="text-gray-300">{matchedNode.description}</span>
                  </div>
                )}
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Channel size</span>
                  <span className="text-white font-mono font-medium">{formatSats(parsedAmount)} sats</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Fee priority</span>
                  <span className="text-gray-300">
                    {feePriority.charAt(0).toUpperCase() + feePriority.slice(1)}
                    {feesData?.priorities?.[feePriority] && ` (${feesData.priorities[feePriority].sat_per_vbyte} sat/vB)`}
                  </span>
                </div>
                {estimatedFee > 0 && (
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-gray-500">Est. funding fee</span>
                    <span className="text-gray-300 font-mono">~{formatSats(estimatedFee)} sats</span>
                  </div>
                )}
              </div>

              <div className="flex items-start gap-1.5 pt-1">
                <AlertCircle className="w-3 h-3 text-neon-yellow mt-0.5 flex-shrink-0" />
                <p className="text-[10px] text-gray-500 leading-relaxed">
                  Opening a channel locks your on-chain funds into a 2-of-2 multisig. The funding transaction
                  must confirm before the channel becomes active (typically 3 confirmations).
                </p>
              </div>
            </div>

            <div className="flex gap-3">
              <button
                onClick={() => setStep('form')}
                disabled={openMutation.isPending}
                className="flex-1 px-4 py-2.5 rounded-lg border border-navy-600 text-gray-400
                           hover:bg-navy-800 hover:text-gray-300 transition-all text-sm font-medium"
              >
                Back
              </button>
              <button
                onClick={() => openMutation.mutate()}
                disabled={openMutation.isPending}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg
                           bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-all
                           disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {openMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Connecting...
                  </>
                ) : (
                  <>
                    <GitBranch className="w-4 h-4" />
                    Open Channel
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* ── Result Step ── */}
        {step === 'result' && openResult && (
          <div className="space-y-4">
            {openResult.success ? (
              <>
                <div className="text-center py-4">
                  <CheckCircle2 className="w-12 h-12 text-neon-green mx-auto mb-3" />
                  <h3 className="text-lg font-semibold text-white mb-1">Channel Opening</h3>
                  <p className="text-sm text-gray-400">
                    Funding transaction broadcast successfully. The channel will become active after 3 confirmations.
                  </p>
                </div>

                {openResult.funding_txid && (
                  <div className="bg-navy-900/50 border border-navy-700 rounded-lg p-3 space-y-2">
                    <div className="text-xs text-gray-500 mb-1">Funding Transaction</div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-mono text-gray-300 truncate flex-1">
                        {openResult.funding_txid}
                      </span>
                      <CopyButton text={openResult.funding_txid} />
                    </div>
                    <a
                      href={`${mempoolUrl}/tx/${openResult.funding_txid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300 transition-colors mt-1"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                      View on Mempool Explorer
                    </a>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="text-center py-4">
                  <AlertCircle className="w-12 h-12 text-neon-pink mx-auto mb-3" />
                  <h3 className="text-lg font-semibold text-white mb-1">Channel Open Failed</h3>
                  <p className="text-sm text-gray-400">
                    The channel could not be opened.
                  </p>
                </div>

                {openResult.error && (
                  <div className="bg-red-500/5 border border-red-500/20 rounded-lg p-3">
                    <p className="text-xs text-red-400 font-mono leading-relaxed break-all">
                      {openResult.error}
                    </p>
                  </div>
                )}
              </>
            )}

            <button
              onClick={openResult.success ? onClose : () => { setStep('form'); setOpenResult(null); }}
              className="w-full px-4 py-2.5 rounded-lg border border-navy-600 text-gray-300
                         hover:bg-navy-800 hover:text-white transition-all text-sm font-medium"
            >
              {openResult.success ? 'Done' : 'Try Again'}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
 * Cold Storage Dialog
 * ───────────────────────────────────────────────────────────── */

// Bitcoin address validation helpers
function isValidBitcoinAddress(addr: string): boolean {
  const trimmed = addr.trim();
  // Bech32 mainnet (bc1q... = p2wpkh/p2wsh, bc1p... = taproot)
  if (/^bc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{25,87}$/i.test(trimmed)) return true;
  // P2PKH (1...)
  if (/^1[1-9A-HJ-NP-Za-km-z]{25,34}$/.test(trimmed)) return true;
  // P2SH (3...)
  if (/^3[1-9A-HJ-NP-Za-km-z]{25,34}$/.test(trimmed)) return true;
  return false;
}

function getAddressType(addr: string): string {
  const trimmed = addr.trim();
  if (/^bc1p/i.test(trimmed)) return 'Taproot (P2TR)';
  if (/^bc1q/i.test(trimmed)) return 'Native SegWit (P2WPKH)';
  if (/^3/.test(trimmed)) return 'SegWit (P2SH)';
  if (/^1/.test(trimmed)) return 'Legacy (P2PKH)';
  return 'Unknown';
}

// ═══════════════════════════════════════════════════════════════════
// Lightning Cold Storage Tab — Boltz Reverse Swap
// ═══════════════════════════════════════════════════════════════════

const SWAP_STATUS_CONFIG: Record<string, { label: string; color: string; bgColor: string; icon: 'spin' | 'check' | 'error' | 'clock' }> = {
  created:        { label: 'Created',       color: 'text-blue-400',    bgColor: 'bg-blue-400/10',    icon: 'clock' },
  paying_invoice: { label: 'Paying',        color: 'text-neon-yellow', bgColor: 'bg-neon-yellow/10', icon: 'spin' },
  invoice_paid:   { label: 'Invoice Paid',  color: 'text-blue-400',    bgColor: 'bg-blue-400/10',    icon: 'spin' },
  claiming:       { label: 'Claiming',      color: 'text-neon-yellow', bgColor: 'bg-neon-yellow/10', icon: 'spin' },
  claimed:        { label: 'Claimed',       color: 'text-neon-green',  bgColor: 'bg-neon-green/10',  icon: 'spin' },
  completed:      { label: 'Complete',      color: 'text-neon-green',  bgColor: 'bg-neon-green/10',  icon: 'check' },
  failed:         { label: 'Failed',        color: 'text-red-400',     bgColor: 'bg-red-400/10',     icon: 'error' },
  cancelled:      { label: 'Cancelled',     color: 'text-gray-400',    bgColor: 'bg-gray-400/10',    icon: 'error' },
  refunded:       { label: 'Refunded',      color: 'text-orange-400',  bgColor: 'bg-orange-400/10',  icon: 'clock' },
};

function SwapStatusBadge({ status }: { status: string }) {
  const cfg = SWAP_STATUS_CONFIG[status] || { label: status, color: 'text-gray-400', bgColor: 'bg-gray-400/10', icon: 'clock' as const };
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium ${cfg.color} ${cfg.bgColor}`}>
      {cfg.icon === 'spin' && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {cfg.icon === 'check' && <CheckCircle2 className="w-2.5 h-2.5" />}
      {cfg.icon === 'error' && <AlertCircle className="w-2.5 h-2.5" />}
      {cfg.icon === 'clock' && <Clock className="w-2.5 h-2.5" />}
      {cfg.label}
    </span>
  );
}

function SwapHistoryPanel({ swaps, mempoolUrl }: { swaps: BoltzSwapStatus[]; mempoolUrl: string }) {
  const [expanded, setExpanded] = useState(false);
  const displaySwaps = expanded ? swaps : swaps.slice(0, 3);

  return (
    <div className="border-t border-navy-700 pt-4 mt-2">
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-xs font-medium text-gray-400 flex items-center gap-1.5">
          <History className="w-3.5 h-3.5" />
          Recent Swaps
        </h4>
        {swaps.length > 3 && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-[10px] text-blue-400 hover:text-blue-300"
          >
            {expanded ? 'Show less' : `Show all (${swaps.length})`}
          </button>
        )}
      </div>
      <div className="space-y-2">
        {displaySwaps.map((swap) => {
          const isActive = !['completed', 'failed', 'cancelled', 'refunded'].includes(swap.status);
          const age = timeAgo(swap.created_at);
          return (
            <div
              key={swap.id}
              className={`rounded-lg p-2.5 text-xs ${
                isActive
                  ? 'bg-blue-500/5 border border-blue-500/20'
                  : 'bg-navy-900/30 border border-navy-700'
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className="text-white font-mono">{formatSats(swap.invoice_amount_sats)} sats</span>
                  <SwapStatusBadge status={swap.status} />
                </div>
                <span className="text-[10px] text-gray-600">{age}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-gray-500 font-mono truncate max-w-[180px]">
                  → {swap.destination_address.slice(0, 10)}…{swap.destination_address.slice(-6)}
                </span>
                {swap.claim_txid && (
                  <a
                    href={`${mempoolUrl}/tx/${swap.claim_txid}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-blue-400 hover:text-blue-300"
                  >
                    tx <ExternalLink className="w-2.5 h-2.5" />
                  </a>
                )}
                {swap.error_message && !swap.claim_txid && (
                  <span className="text-red-400 truncate max-w-[140px]" title={swap.error_message}>
                    {swap.error_message.length > 30 ? swap.error_message.slice(0, 30) + '…' : swap.error_message}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

const SWAP_STEPS = [
  { key: 'created', label: 'Swap Created', desc: 'Boltz reverse swap initialized' },
  { key: 'paying_invoice', label: 'Paying Invoice', desc: 'Lightning payment sent to Boltz' },
  { key: 'invoice_paid', label: 'Invoice Paid', desc: 'Waiting for Boltz lockup on-chain' },
  { key: 'claiming', label: 'Claiming Funds', desc: 'Signing and broadcasting claim transaction' },
  { key: 'claimed', label: 'Claim Broadcast', desc: 'Claim transaction in mempool' },
  { key: 'completed', label: 'Complete', desc: 'Funds sent to cold storage' },
] as const;

function getStepIndex(status: string): number {
  const idx = SWAP_STEPS.findIndex((s) => s.key === status);
  return idx >= 0 ? idx : 0;
}

function LightningColdStorageTab({ totals, mempoolUrl }: { totals: WalletTotals; mempoolUrl: string }) {
  const queryClient = useQueryClient();
  const [lnAddress, setLnAddress] = useState('');
  const [lnAmount, setLnAmount] = useState('');
  const [routingFeePercent, setRoutingFeePercent] = useState(3.0);
  const [lnStep, setLnStep] = useState<'form' | 'confirm' | 'progress' | 'result'>('form');
  const [activeSwapId, setActiveSwapId] = useState<string | null>(null);
  const [swapResult, setSwapResult] = useState<{ success: boolean; claimTxid?: string; error?: string } | null>(null);

  // Fetch recent swaps for history & auto-resume
  const { data: swapHistory, isLoading: historyLoading } = useQuery({
    queryKey: ['boltz-swap-history'],
    queryFn: () => walletService.listLightningColdStorageSwaps(10),
    staleTime: 15_000,
    refetchInterval: lnStep === 'form' ? 30_000 : false,
  });

  // Auto-resume an active (non-terminal) swap on mount
  useEffect(() => {
    if (!swapHistory?.swaps || activeSwapId || lnStep !== 'form') return;
    const activeSwap = swapHistory.swaps.find(
      (s) => !['completed', 'failed', 'cancelled', 'refunded'].includes(s.status)
    );
    if (activeSwap) {
      setActiveSwapId(activeSwap.id);
      setLnStep('progress');
    }
  }, [swapHistory, activeSwapId, lnStep]);

  // Fetch Boltz fee info
  const { data: feeInfo, isLoading: feesLoading } = useQuery({
    queryKey: ['boltz-fees'],
    queryFn: () => walletService.getLightningColdStorageFees(),
    staleTime: 60_000,
  });

  // Poll active swap status
  const { data: swapStatus } = useQuery({
    queryKey: ['boltz-swap-status', activeSwapId],
    queryFn: () => activeSwapId ? walletService.getLightningColdStorageStatus(activeSwapId) : null,
    enabled: !!activeSwapId && lnStep === 'progress',
    refetchInterval: 5_000,
  });

  // Initiate swap mutation
  const initSwapMutation = useMutation({
    mutationFn: ({ amount, address, routingFee }: { amount: number; address: string; routingFee: number }) =>
      walletService.initiateLightningColdStorage(amount, address, routingFee),
    onSuccess: (data) => {
      setActiveSwapId(data.id);
      setLnStep('progress');
    },
    onError: (error: Error & { response?: { data?: { detail?: string } } }) => {
      setSwapResult({
        success: false,
        error: error.response?.data?.detail || error.message,
      });
      setLnStep('result');
    },
  });

  // Cancel swap mutation
  const cancelMutation = useMutation({
    mutationFn: (swapId: string) => walletService.cancelLightningColdStorage(swapId),
    onSuccess: () => {
      setLnStep('form');
      setActiveSwapId(null);
    },
  });

  // Watch for terminal states
  if (swapStatus && lnStep === 'progress') {
    if (swapStatus.status === 'completed') {
      if (!swapResult) {
        setSwapResult({ success: true, claimTxid: swapStatus.claim_txid || undefined });
        setLnStep('result');
        queryClient.invalidateQueries({ queryKey: ['wallet'] });
      }
    } else if (['failed', 'cancelled', 'refunded'].includes(swapStatus.status)) {
      if (!swapResult) {
        setSwapResult({
          success: false,
          error: swapStatus.error_message || `Swap ${swapStatus.status}`,
        });
        setLnStep('result');
      }
    }
  }

  const amount = parseInt(lnAmount) || 0;
  const isValidAddress = lnAddress.length >= 26;
  const minAmount = feeInfo?.min_amount_sats || 25_000;
  const maxAmount = feeInfo?.max_amount_sats || 25_000_000;
  const isAmountValid = amount >= minAmount && amount <= maxAmount && amount <= totals.lightning_local_sats;

  // Calculate estimated receive amount
  const feePercent = feeInfo?.fee_percentage || 0.5;
  const minerFees = feeInfo?.total_miner_fee_sats || 795;
  const boltzFee = Math.ceil(amount * (feePercent / 100));
  const maxRoutingFee = Math.max(1000, Math.ceil(amount * (routingFeePercent / 100)));
  const estimatedReceive = amount > 0 ? amount - boltzFee - minerFees : 0;

  // ── Form Step ──
  if (lnStep === 'form') {
    return (
      <div className="space-y-4">
        {/* Info banner */}
        <div className="bg-navy-900/50 border border-blue-500/20 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-2">
            <Zap className="w-4 h-4 text-neon-yellow" />
            <h3 className="text-sm font-medium text-white">Lightning → Cold Storage</h3>
            {feeInfo?.tor_enabled && (
              <span className="ml-auto flex items-center gap-1 text-[10px] text-blue-400">
                <Shield className="w-3 h-3" /> Tor
              </span>
            )}
          </div>
          <p className="text-[11px] text-gray-400">
            Trustless reverse swap via{' '}
            <a href="https://boltz.exchange" target="_blank" rel="noopener noreferrer"
               className="text-blue-400 hover:text-blue-300 underline underline-offset-2">
              Boltz Exchange
            </a>
            . Non-custodial, time-locked contracts.
          </p>
        </div>

        {/* Spendable info */}
        <div className="bg-navy-900/50 border border-navy-700 border-l-2 border-l-neon-green/30 rounded-lg p-3">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-500">Lightning outbound (sendable)</span>
            <span className="text-sm font-mono text-neon-green/90 font-medium">{formatSats(totals.lightning_local_sats)} sats</span>
          </div>
        </div>

        {/* Address input */}
        <div>
          <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">Cold Storage Address</label>
          <input
            type="text"
            value={lnAddress}
            onChange={(e) => setLnAddress(e.target.value.trim())}
            placeholder="bc1q... or bc1p..."
            className="w-full px-3 py-2.5 bg-navy-900/50 border border-navy-600
                       rounded-lg text-white text-sm font-mono placeholder-gray-600
                       focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
          />
        </div>

        {/* Amount input */}
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-xs text-gray-500 uppercase tracking-wide">Amount (sats)</label>
            <button
              onClick={() => setLnAmount(String(Math.min(totals.lightning_local_sats, maxAmount)))}
              className="text-[10px] text-blue-400 hover:text-blue-300 font-medium"
            >
              Send Max
            </button>
          </div>
          <input
            type="number"
            value={lnAmount}
            onChange={(e) => setLnAmount(e.target.value)}
            placeholder={`${formatSats(minAmount)} – ${formatSats(maxAmount)}`}
            min={minAmount}
            max={maxAmount}
            className="w-full px-3 py-2.5 bg-navy-900/50 border border-navy-600
                       rounded-lg text-white text-sm font-mono placeholder-gray-600
                       focus:outline-none focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20"
          />
          {amount > 0 && amount < minAmount && (
            <p className="text-[10px] text-red-400 mt-1">Minimum: {formatSats(minAmount)} sats</p>
          )}
          {amount > maxAmount && (
            <p className="text-[10px] text-red-400 mt-1">Maximum: {formatSats(maxAmount)} sats</p>
          )}
          {amount > totals.lightning_local_sats && (
            <p className="text-[10px] text-red-400 mt-1">
              Exceeds spendable balance ({formatSats(totals.lightning_local_sats)} sats)
            </p>
          )}
        </div>

        {/* Fee preview */}
        {amount > 0 && isAmountValid && (
          <div className="bg-navy-900/30 border border-navy-700 rounded-lg p-3 space-y-1.5">
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">Boltz fee ({feePercent}%)</span>
              <span className="text-gray-300 font-mono">-{formatSats(boltzFee)} sats</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">Miner fees (lockup + claim)</span>
              <span className="text-gray-300 font-mono">-{formatSats(minerFees)} sats</span>
            </div>
            <div className="flex justify-between text-xs">
              <span className="text-gray-500">Max routing fee ({routingFeePercent}%)</span>
              <span className="text-gray-300 font-mono">≤ {formatSats(maxRoutingFee)} sats</span>
            </div>
            <div className="border-t border-navy-600 pt-1.5 flex justify-between text-xs">
              <span className="text-gray-400 font-medium">You receive on-chain</span>
              <span className="text-neon-green font-mono font-medium">≈ {formatSats(Math.max(0, estimatedReceive))} sats</span>
            </div>
          </div>
        )}

        {/* Routing fee limit */}
        {amount > 0 && isAmountValid && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="text-gray-500">Routing fee limit</span>
              <span className="text-gray-400 font-mono">{routingFeePercent.toFixed(1)}%</span>
            </div>
            <input
              type="range"
              min="0.5"
              max="5.0"
              step="0.5"
              value={routingFeePercent}
              onChange={(e) => setRoutingFeePercent(parseFloat(e.target.value))}
              className="w-full h-1.5 rounded-full appearance-none bg-navy-700 accent-neon-yellow cursor-pointer
                [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:bg-neon-yellow [&::-webkit-slider-thumb]:appearance-none"
            />
            <p className="text-[10px] text-gray-600">
              Higher limits improve routing success. Actual fee is often much lower.
            </p>
          </div>
        )}

        {/* Submit */}
        <button
          onClick={() => setLnStep('confirm')}
          disabled={!isValidAddress || !isAmountValid || feesLoading}
          className="w-full py-2.5 rounded-lg text-sm font-medium transition-all
                     bg-blue-600 hover:bg-blue-500 text-white
                     disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-blue-600"
        >
          Review Swap
        </button>

        {/* Recent Swaps */}
        {(swapHistory?.swaps?.length ?? 0) > 0 && (
          <SwapHistoryPanel swaps={swapHistory!.swaps} mempoolUrl={mempoolUrl} />
        )}
      </div>
    );
  }

  // ── Confirm Step ──
  if (lnStep === 'confirm') {
    return (
      <div className="space-y-4">
        <div className="bg-navy-900/50 border border-blue-500/20 rounded-lg p-4 space-y-3">
          <h3 className="text-sm font-medium text-white flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-blue-400" />
            Confirm Lightning → Cold Storage
          </h3>
          <div className="space-y-2 text-xs">
            <div className="flex justify-between">
              <span className="text-gray-500">Send</span>
              <span className="text-white font-mono">{formatSats(amount)} sats</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Boltz fee ({feePercent}%)</span>
              <span className="text-gray-400 font-mono">-{formatSats(boltzFee)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Miner fees</span>
              <span className="text-gray-400 font-mono">-{formatSats(minerFees)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-gray-500">Max routing fee ({routingFeePercent}%)</span>
              <span className="text-gray-400 font-mono">≤ {formatSats(maxRoutingFee)}</span>
            </div>
            <div className="border-t border-navy-600 pt-2 flex justify-between">
              <span className="text-gray-300 font-medium">Receive on-chain</span>
              <span className="text-neon-green font-mono font-medium">≈ {formatSats(Math.max(0, estimatedReceive))} sats</span>
            </div>
            <div className="border-t border-navy-600 pt-2">
              <p className="text-gray-500 mb-1">Destination</p>
              <p className="text-white font-mono text-[11px] break-all">{lnAddress}</p>
            </div>
          </div>
        </div>

        <div className="p-3 rounded-lg bg-neon-yellow/5 border border-neon-yellow/15">
          <p className="text-[11px] text-neon-yellow">
            <strong>Important:</strong> Once started, the swap cannot be cancelled after the Lightning
            payment is sent. The process is automatic and typically completes within a few minutes.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            onClick={() => setLnStep('form')}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm
                       bg-navy-700 border border-navy-600 text-gray-300
                       hover:border-blue-500/30 hover:text-white transition-all"
          >
            Back
          </button>
          <button
            onClick={() => initSwapMutation.mutate({ amount, address: lnAddress, routingFee: routingFeePercent })}
            disabled={initSwapMutation.isPending}
            className="flex-1 px-4 py-2.5 rounded-lg text-sm font-medium transition-all
                       bg-blue-600 hover:bg-blue-500 text-white
                       disabled:opacity-50"
          >
            {initSwapMutation.isPending ? (
              <span className="flex items-center justify-center gap-2">
                <Loader2 className="w-4 h-4 animate-spin" /> Starting...
              </span>
            ) : (
              'Start Swap'
            )}
          </button>
        </div>
      </div>
    );
  }

  // ── Progress Step ──
  if (lnStep === 'progress') {
    const currentStep = swapStatus ? getStepIndex(swapStatus.status) : 0;
    const isFailed = swapStatus && ['failed', 'refunded', 'cancelled'].includes(swapStatus.status);

    return (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-medium text-white">Swap Progress</h3>
          <Loader2 className="w-4 h-4 text-blue-400 animate-spin" />
        </div>

        {/* Step tracker */}
        <div className="space-y-0">
          {SWAP_STEPS.map((step, idx) => {
            const isDone = idx < currentStep || swapStatus?.status === 'completed';
            const isCurrent = idx === currentStep && !isFailed && swapStatus?.status !== 'completed';

            return (
              <div key={step.key} className="flex items-start gap-3 relative">
                {/* Vertical connector line */}
                {idx < SWAP_STEPS.length - 1 && (
                  <div
                    className={`absolute left-[9px] top-[20px] w-0.5 h-6 ${
                      isDone ? 'bg-neon-green/40' : 'bg-navy-600'
                    }`}
                  />
                )}
                {/* Step icon */}
                <div className="flex-shrink-0 mt-0.5">
                  {isDone ? (
                    <CheckCircle2 className="w-[18px] h-[18px] text-neon-green" />
                  ) : isCurrent ? (
                    <Loader2 className="w-[18px] h-[18px] text-blue-400 animate-spin" />
                  ) : (
                    <div className="w-[18px] h-[18px] rounded-full border border-navy-600" />
                  )}
                </div>
                {/* Step text */}
                <div className="pb-4">
                  <p className={`text-xs font-medium ${
                    isDone ? 'text-neon-green' : isCurrent ? 'text-white' : 'text-gray-600'
                  }`}>
                    {step.label}
                  </p>
                  <p className={`text-[10px] ${isCurrent ? 'text-gray-400' : 'text-gray-600'}`}>
                    {step.desc}
                  </p>
                </div>
              </div>
            );
          })}
        </div>

        {/* Error display */}
        {isFailed && swapStatus && (
          <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20">
            <p className="text-xs text-red-400">{swapStatus.error_message || 'Swap failed'}</p>
          </div>
        )}

        {/* Cancel (only if still in 'created' state) */}
        {swapStatus?.status === 'created' && (
          <button
            onClick={() => activeSwapId && cancelMutation.mutate(activeSwapId)}
            disabled={cancelMutation.isPending}
            className="w-full py-2 rounded-lg text-xs text-gray-400 border border-navy-600
                       hover:text-red-400 hover:border-red-500/30 transition-all"
          >
            Cancel Swap
          </button>
        )}
      </div>
    );
  }

  // ── Result Step ──
  return (
    <div className="space-y-4">
      {swapResult?.success ? (
        <div className="flex flex-col items-center gap-3 py-4">
          <div className="w-12 h-12 rounded-full bg-neon-green/10 border border-neon-green/30 flex items-center justify-center">
            <CheckCircle2 className="w-6 h-6 text-neon-green" />
          </div>
          <h3 className="text-lg font-semibold text-white">Swap Complete</h3>
          <p className="text-xs text-gray-400 text-center">
            {formatSats(Math.max(0, estimatedReceive))} sats sent to cold storage
          </p>
          {swapResult.claimTxid && (
            <a
              href={`${mempoolUrl}/tx/${swapResult.claimTxid}`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-xs text-blue-400 hover:text-blue-300"
            >
              View on Mempool <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>
      ) : (
        <div className="flex flex-col items-center gap-3 py-4">
          <div className="w-12 h-12 rounded-full bg-red-500/10 border border-red-500/30 flex items-center justify-center">
            <AlertCircle className="w-6 h-6 text-red-400" />
          </div>
          <h3 className="text-lg font-semibold text-white">Swap Failed</h3>
          <p className="text-xs text-gray-400 text-center">{swapResult?.error}</p>
          {swapResult?.error?.includes('refund') && (
            <div className="p-2 rounded bg-neon-yellow/5 border border-neon-yellow/15">
              <p className="text-[10px] text-neon-yellow text-center">
                If Lightning funds were paid before the on-chain claim, Boltz auto-refunds via Lightning.
                If the swap timed out after lockup, contact support.
              </p>
            </div>
          )}
        </div>
      )}

      <button
        onClick={() => {
          setLnStep('form');
          setActiveSwapId(null);
          setSwapResult(null);
          setLnAmount('');
          setLnAddress('');
        }}
        className="w-full py-2.5 rounded-lg text-sm bg-navy-700 border border-navy-600
                   text-gray-300 hover:text-white transition-all"
      >
        {swapResult?.success ? 'New Swap' : 'Try Again'}
      </button>
    </div>
  );
}

interface ColdStorageDialogProps {
  onClose: () => void;
  totals: WalletTotals;
  onchain: OnchainBalance | null;
  mempoolUrl: string;
}

function ColdStorageDialog({ onClose, totals, onchain, mempoolUrl }: ColdStorageDialogProps) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<'onchain' | 'lightning'>('onchain');

  // On-chain send state
  const [address, setAddress] = useState('');
  const [amountSats, setAmountSats] = useState('');
  const [feePriority, setFeePriority] = useState<'low' | 'medium' | 'high'>('medium');
  const [step, setStep] = useState<'form' | 'confirm' | 'result'>('form');
  const [sendResult, setSendResult] = useState<{ success: boolean; txid?: string; error?: string } | null>(null);

  // Fee data
  const { data: feesData } = useQuery({
    queryKey: ['wallet-fees'],
    queryFn: () => walletService.getFees(),
    staleTime: 30000,
  });

  // Fee estimate for current inputs
  const parsedAmount = parseInt(amountSats, 10);
  const addressValid = isValidBitcoinAddress(address);
  const amountValid = !isNaN(parsedAmount) && parsedAmount > 0;

  const { data: feeEstimate } = useQuery({
    queryKey: ['cold-storage-fee-estimate', address, parsedAmount, feePriority],
    queryFn: () => {
      const targetConf = feePriority === 'high' ? 1 : feePriority === 'medium' ? 3 : 6;
      return walletService.estimateFee(address, parsedAmount, targetConf);
    },
    enabled: addressValid && amountValid && parsedAmount >= 546,
    staleTime: 15000,
    retry: 1,
  });

  // Sendable calculations
  const confirmedOnchain = onchain?.confirmed_balance ?? totals.onchain_sats;
  const lockedBalance = onchain?.locked_balance ?? 0;
  const reservedAnchor = onchain?.reserved_balance_anchor_chan ?? 0;
  const maxSendableOnchain = Math.max(0, confirmedOnchain - lockedBalance - reservedAnchor);
  const hasLockedFunds = lockedBalance > 0 || reservedAnchor > 0;

  // Send mutation
  const sendMutation = useMutation({
    mutationFn: async () => {
      const feeRate = feesData?.priorities?.[feePriority]?.sat_per_vbyte;
      return walletService.sendOnchain(address, parsedAmount, feeRate, 'Cold storage withdrawal');
    },
    onSuccess: (data) => {
      setSendResult({ success: true, txid: data.txid });
      setStep('result');
      queryClient.invalidateQueries({ queryKey: ['wallet-summary'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-transactions'] });
    },
    onError: (error) => {
      setSendResult({
        success: false,
        error: error instanceof Error ? error.message : 'Transaction failed. Check your node connection.',
      });
      setStep('result');
    },
  });

  // Validation
  const amountTooLow = amountValid && parsedAmount < 546;
  const amountExceedsBalance = amountValid && parsedAmount > maxSendableOnchain;
  const estimatedTotal = feeEstimate ? parsedAmount + feeEstimate.fee_sat : parsedAmount;
  const totalExceedsBalance = amountValid && feeEstimate && estimatedTotal > maxSendableOnchain;
  const canProceed = addressValid && amountValid && !amountTooLow && !amountExceedsBalance && !totalExceedsBalance;

  const handleSetMax = () => {
    // Account for estimated fee — leave some room
    const feeBuffer = feeEstimate?.fee_sat ?? 500;
    const max = Math.max(0, maxSendableOnchain - feeBuffer);
    setAmountSats(max.toString());
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Dialog */}
      <div className="relative w-full max-w-lg mx-4 card border-blue-500/30 bg-gradient-to-br from-navy-800 to-navy-900 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <Snowflake className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold text-white">Cold Storage</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-navy-700 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-5">
          Send funds to a secure offline wallet for long-term storage.
        </p>

        {/* Tab Selector */}
        <div className="flex rounded-lg bg-navy-900 border border-navy-700 p-1 mb-5">
          <button
            onClick={() => { setTab('onchain'); setStep('form'); setSendResult(null); }}
            className={`
              flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium transition-all
              ${tab === 'onchain'
                ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30'
                : 'text-gray-500 hover:text-gray-300 border border-transparent'
              }
            `}
          >
            <LinkIcon className="w-3.5 h-3.5" />
            On-Chain
          </button>
          <button
            onClick={() => { setTab('lightning'); setStep('form'); setSendResult(null); }}
            className={`
              flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-sm font-medium transition-all
              ${tab === 'lightning'
                ? 'bg-blue-500/15 text-blue-400 border border-blue-500/30'
                : 'text-gray-500 hover:text-gray-300 border border-transparent'
              }
            `}
          >
            <Zap className="w-3.5 h-3.5" />
            Lightning
          </button>
        </div>

        {/* On-Chain Tab */}
        {tab === 'onchain' && step === 'form' && (
          <div className="space-y-4">
            {/* Info banner */}
            <div className="bg-navy-900/50 border border-blue-500/20 rounded-lg p-3">
              <div className="flex items-center gap-2 mb-2">
                <LinkIcon className="w-4 h-4 text-orange-400" />
                <h3 className="text-sm font-medium text-white">On-Chain → Cold Storage</h3>
              </div>
              <p className="text-[11px] text-gray-400">
                Direct on-chain transaction to your cold storage address. Standard network fees apply.
              </p>
            </div>

            {/* Sendable Info */}
            <div className="bg-navy-900/50 border border-navy-700 border-l-2 border-l-neon-green/30 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">On-chain (sendable)</span>
                <span className="text-sm font-mono text-neon-green/90 font-medium">
                  {formatSats(maxSendableOnchain)} sats
                </span>
              </div>
              {hasLockedFunds && (
                <div className="flex items-start gap-1.5 mt-2">
                  <Info className="w-3 h-3 text-blue-400 mt-0.5 flex-shrink-0" />
                  <p className="text-[10px] text-gray-500 leading-relaxed">
                    {lockedBalance > 0 && (
                      <span>{formatSats(lockedBalance)} sats locked in pending operations. </span>
                    )}
                    {reservedAnchor > 0 && (
                      <span>{formatSats(reservedAnchor)} sats reserved for channel anchors. </span>
                    )}
                    These funds cannot be sent until released.
                  </p>
                </div>
              )}
            </div>

            {/* Address Input */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">
                Cold Storage Address
              </label>
              <input
                type="text"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="bc1q... or bc1p..."
                className="
                  w-full px-3 py-2.5 rounded-lg bg-navy-900 border border-navy-600
                  text-white font-mono text-sm placeholder-gray-600
                  focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                  transition-colors
                "
                spellCheck={false}
                autoComplete="off"
              />
              {address.trim().length > 0 && !addressValid && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Invalid Bitcoin address format
                </p>
              )}
              {addressValid && (
                <p className="text-[10px] text-gray-500 mt-1">
                  {getAddressType(address)}
                </p>
              )}
            </div>

            {/* Amount Input */}
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs text-gray-500 uppercase tracking-wide">
                  Amount (sats)
                </label>
                <button
                  onClick={handleSetMax}
                  className="text-[10px] text-blue-400 hover:text-blue-300 font-medium transition-colors"
                >
                  Send Max
                </button>
              </div>
              <input
                type="text"
                inputMode="numeric"
                value={amountSats}
                onChange={(e) => {
                  const val = e.target.value.replace(/[^0-9]/g, '');
                  setAmountSats(val);
                }}
                placeholder="0"
                className="
                  w-full px-3 py-2.5 rounded-lg bg-navy-900 border border-navy-600
                  text-white font-mono text-sm placeholder-gray-600
                  focus:border-blue-500/50 focus:ring-1 focus:ring-blue-500/20 focus:outline-none
                  transition-colors
                "
              />
              {amountValid && (
                <p className="text-[10px] text-gray-500 mt-1">
                  ≈ ₿{formatBtc(parsedAmount)}
                </p>
              )}
              {amountTooLow && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Minimum 546 sats (dust limit)
                </p>
              )}
              {amountExceedsBalance && (
                <p className="text-[10px] text-neon-pink mt-1 flex items-center gap-1">
                  <AlertCircle className="w-3 h-3" />
                  Exceeds sendable balance ({formatSats(maxSendableOnchain)} sats)
                </p>
              )}
            </div>

            {/* Fee Priority */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">
                Fee Priority
              </label>
              <div className="grid grid-cols-3 gap-2">
                {([
                  { value: 'low' as const, label: 'Low', desc: '~1 hour', color: 'text-neon-green' },
                  { value: 'medium' as const, label: 'Medium', desc: '~30 min', color: 'text-neon-yellow' },
                  { value: 'high' as const, label: 'High', desc: 'Next block', color: 'text-orange-400' },
                ]).map((p) => (
                  <button
                    key={p.value}
                    onClick={() => setFeePriority(p.value)}
                    className={`
                      px-3 py-2 rounded-lg border text-left transition-all
                      ${feePriority === p.value
                        ? 'border-blue-500/50 bg-blue-500/10 text-white'
                        : 'border-navy-600 bg-navy-800/50 text-gray-400 hover:border-navy-500 hover:text-gray-300'
                      }
                    `}
                  >
                    <span className="text-xs font-medium block">{p.label}</span>
                    <span className={`text-[10px] block mt-0.5 ${feePriority === p.value ? p.color : 'text-gray-500'}`}>
                      {p.desc}
                      {feesData?.priorities?.[p.value] && (
                        <span className="ml-1">({feesData.priorities[p.value].sat_per_vbyte} sat/vB)</span>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {/* Fee Estimate */}
            {feeEstimate && amountValid && addressValid && (
              <div className="bg-navy-900/50 border border-navy-700 rounded-lg p-3 space-y-1.5">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Estimated fee</span>
                  <span className="text-gray-300 font-mono">{formatSats(feeEstimate.fee_sat)} sats</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Fee rate</span>
                  <span className="text-gray-300 font-mono">{feeEstimate.sat_per_vbyte} sat/vB</span>
                </div>
                <div className="border-t border-navy-700 pt-1.5 flex items-center justify-between text-xs">
                  <span className="text-gray-400 font-medium">Total deducted</span>
                  <span className="text-white font-mono font-medium">{formatSats(estimatedTotal)} sats</span>
                </div>
                {totalExceedsBalance && (
                  <p className="text-[10px] text-neon-pink flex items-center gap-1 pt-1">
                    <AlertCircle className="w-3 h-3" />
                    Amount + fee exceeds sendable balance
                  </p>
                )}
              </div>
            )}

            {/* Proceed Button */}
            <button
              onClick={() => setStep('confirm')}
              disabled={!canProceed}
              className="
                w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg
                bg-blue-500/15 border border-blue-500/30 text-blue-400 font-medium
                hover:bg-blue-500/25 hover:border-blue-500/50 transition-all
                disabled:opacity-30 disabled:cursor-not-allowed
              "
            >
              <ArrowRight className="w-4 h-4" />
              Review Transaction
            </button>
          </div>
        )}

        {/* On-Chain Confirmation Step */}
        {tab === 'onchain' && step === 'confirm' && (
          <div className="space-y-4">
            <div className="bg-navy-900/50 border border-blue-500/20 rounded-lg p-4 space-y-3">
              <h3 className="text-sm font-medium text-white flex items-center gap-2">
                <ShieldCheck className="w-4 h-4 text-blue-400" />
                Confirm Transaction
              </h3>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">To address</span>
                  <span className="text-gray-300 font-mono text-[11px] max-w-[220px] truncate" title={address}>
                    {address}
                  </span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Address type</span>
                  <span className="text-gray-300">{getAddressType(address)}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">Amount</span>
                  <span className="text-white font-mono font-medium">{formatSats(parsedAmount)} sats</span>
                </div>
                {feeEstimate && (
                  <>
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-gray-500">Network fee</span>
                      <span className="text-gray-300 font-mono">{formatSats(feeEstimate.fee_sat)} sats</span>
                    </div>
                    <div className="border-t border-navy-700 pt-2 flex items-center justify-between text-xs">
                      <span className="text-gray-400 font-medium">Total</span>
                      <span className="text-white font-mono font-medium">{formatSats(estimatedTotal)} sats</span>
                    </div>
                  </>
                )}
              </div>

              <div className="flex items-start gap-1.5 pt-1">
                <AlertCircle className="w-3 h-3 text-neon-yellow mt-0.5 flex-shrink-0" />
                <p className="text-[10px] text-gray-500 leading-relaxed">
                  This transaction is <span className="text-neon-yellow">irreversible</span>. Double-check the address before confirming.
                  On-chain transactions typically take 10-60 minutes to confirm.
                </p>
              </div>
            </div>

            <div className="flex gap-2">
              <button
                onClick={() => setStep('form')}
                className="
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg
                  bg-navy-700 border border-navy-600 text-gray-300
                  hover:border-blue-500/30 hover:text-blue-400 transition-all text-sm
                "
              >
                Back
              </button>
              <button
                onClick={() => sendMutation.mutate()}
                disabled={sendMutation.isPending}
                className="
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm
                  bg-blue-500/20 border border-blue-500/40 text-blue-400 font-medium
                  hover:bg-blue-500/30 hover:border-blue-500/60 transition-all
                  disabled:opacity-50 disabled:cursor-not-allowed
                "
              >
                {sendMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Sending...
                  </>
                ) : (
                  <>
                    <Snowflake className="w-4 h-4" />
                    Send to Cold Storage
                  </>
                )}
              </button>
            </div>
          </div>
        )}

        {/* On-Chain Result Step */}
        {tab === 'onchain' && step === 'result' && sendResult && (
          <div className="space-y-4">
            {sendResult.success ? (
              <div className="text-center space-y-3">
                <div className="w-14 h-14 mx-auto rounded-full bg-neon-green/10 border border-neon-green/20 flex items-center justify-center">
                  <CheckCircle2 className="w-7 h-7 text-neon-green" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">Transaction Sent</h3>
                  <p className="text-xs text-gray-400 mt-1">
                    {formatSats(parsedAmount)} sats sent to cold storage
                  </p>
                </div>

                {sendResult.txid && (
                  <div className="bg-navy-900/50 border border-navy-700 rounded-lg p-3">
                    <span className="text-[10px] text-gray-500 uppercase tracking-wider block mb-1.5">Transaction ID</span>
                    <p className="font-mono text-xs text-gray-300 break-all select-all">
                      {sendResult.txid}
                    </p>
                    <a
                      href={`${mempoolUrl}/tx/${sendResult.txid}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[10px] text-blue-400 hover:text-blue-300 mt-2 transition-colors"
                    >
                      <ExternalLink className="w-3 h-3" />
                      View on Mempool
                    </a>
                  </div>
                )}

                <button
                  onClick={onClose}
                  className="
                    w-full px-4 py-2.5 rounded-lg text-sm
                    bg-navy-700 border border-navy-600 text-gray-300
                    hover:border-blue-500/30 hover:text-white transition-all
                  "
                >
                  Done
                </button>
              </div>
            ) : (
              <div className="text-center space-y-3">
                <div className="w-14 h-14 mx-auto rounded-full bg-neon-pink/10 border border-neon-pink/20 flex items-center justify-center">
                  <AlertCircle className="w-7 h-7 text-neon-pink" />
                </div>
                <div>
                  <h3 className="text-lg font-semibold text-white">Transaction Failed</h3>
                  <p className="text-xs text-gray-400 mt-1">
                    {sendResult.error}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => { setStep('form'); setSendResult(null); }}
                    className="
                      flex-1 px-4 py-2.5 rounded-lg text-sm
                      bg-navy-700 border border-navy-600 text-gray-300
                      hover:border-blue-500/30 hover:text-white transition-all
                    "
                  >
                    Try Again
                  </button>
                  <button
                    onClick={onClose}
                    className="
                      flex-1 px-4 py-2.5 rounded-lg text-sm
                      bg-navy-700 border border-navy-600 text-gray-300
                      hover:text-white transition-all
                    "
                  >
                    Close
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Lightning Tab — Boltz Reverse Swap */}
        {tab === 'lightning' && (
          <LightningColdStorageTab totals={totals} mempoolUrl={mempoolUrl} />
        )}
      </div>
    </div>
  );
}

function FundWalletDialog({ onClose }: { onClose: () => void }) {
  const [addressType, setAddressType] = useState<'p2tr' | 'p2wkh' | 'np2wkh'>('p2tr');
  const [generatedAddress, setGeneratedAddress] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const generateMutation = useMutation({
    mutationFn: (type: string) => walletService.getNewAddress(type),
    onSuccess: (data) => {
      setGeneratedAddress(data.address);
      setCopied(false);
    },
  });

  const handleGenerate = useCallback(() => {
    generateMutation.mutate(addressType);
  }, [addressType, generateMutation]);

  const handleCopy = useCallback(async () => {
    if (!generatedAddress) return;
    await navigator.clipboard.writeText(generatedAddress);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }, [generatedAddress]);

  const handleNewAddress = useCallback(() => {
    setGeneratedAddress(null);
    setCopied(false);
    generateMutation.reset();
  }, [generateMutation]);

  const addressTypes = [
    { value: 'p2tr' as const, label: 'Taproot', desc: 'Low fees, best privacy' },
    { value: 'p2wkh' as const, label: 'Native SegWit', desc: 'Widely compatible' },
    { value: 'np2wkh' as const, label: 'Nested SegWit', desc: 'Legacy compatibility' },
  ];

  // Bitcoin URI for QR code (BIP21)
  const qrValue = generatedAddress ? `bitcoin:${generatedAddress}` : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 card border-orange-500/30 bg-gradient-to-br from-navy-800 to-navy-900 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Download className="w-5 h-5 text-orange-400" />
            <h2 className="text-lg font-semibold text-white">Fund Wallet</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-navy-700 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-gray-500 -mt-3 mb-5">Generate a fresh on-chain address to receive bitcoin.</p>

        {!generatedAddress ? (
          /* Address Type Selection + Generate */
          <div className="space-y-4">
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-2 block">Address Type</label>
              <div className="grid grid-cols-3 gap-2">
                {addressTypes.map((t) => (
                  <button
                    key={t.value}
                    onClick={() => setAddressType(t.value)}
                    className={`
                      px-3 py-2.5 rounded-lg border text-left transition-all
                      ${addressType === t.value
                        ? 'border-orange-500/50 bg-orange-500/10 text-white'
                        : 'border-navy-600 bg-navy-800/50 text-gray-400 hover:border-navy-500 hover:text-gray-300'
                      }
                    `}
                  >
                    <span className="text-sm font-medium block">{t.label}</span>
                    <span className="text-[10px] text-gray-500 block mt-0.5">{t.desc}</span>
                  </button>
                ))}
              </div>
            </div>

            <button
              onClick={handleGenerate}
              disabled={generateMutation.isPending}
              className="
                w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg
                bg-orange-500/15 border border-orange-500/30 text-orange-400 font-medium
                hover:bg-orange-500/25 hover:border-orange-500/50 transition-all
                disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              {generateMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Download className="w-4 h-4" />
                  Generate Address
                </>
              )}
            </button>

            {generateMutation.isError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-pink/10 border border-neon-pink/20">
                <AlertCircle className="w-4 h-4 text-neon-pink flex-shrink-0" />
                <p className="text-xs text-neon-pink">
                  {generateMutation.error instanceof Error
                    ? generateMutation.error.message
                    : 'Failed to generate address. Check your LND connection.'}
                </p>
              </div>
            )}
          </div>
        ) : (
          /* Generated Address Display */
          <div className="space-y-4">
            {/* QR Code */}
            <div className="flex justify-center">
              <div className="bg-white rounded-2xl p-4 shadow-lg shadow-black/20">
                <QRCodeSVG
                  value={qrValue}
                  size={200}
                  level="M"
                  includeMargin={false}
                />
              </div>
            </div>

            {/* Address Type Badge */}
            <div className="flex justify-center">
              <span className="text-[10px] uppercase tracking-wider text-gray-500 bg-navy-800 border border-navy-700 px-2.5 py-1 rounded-full">
                {addressTypes.find(t => t.value === addressType)?.label} Address
              </span>
            </div>

            {/* Address Text + Copy */}
            <div
              onClick={handleCopy}
              className="
                group cursor-pointer bg-navy-900 border border-navy-700 rounded-xl
                p-4 hover:border-orange-500/30 transition-all
              "
            >
              <p className="font-mono text-sm text-gray-300 text-center break-all leading-relaxed select-all group-hover:text-white transition-colors">
                {generatedAddress}
              </p>
              <div className="flex items-center justify-center gap-1.5 mt-3">
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-neon-green" />
                    <span className="text-xs text-neon-green font-medium">Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5 text-gray-500 group-hover:text-orange-400 transition-colors" />
                    <span className="text-xs text-gray-500 group-hover:text-orange-400 transition-colors">Click to copy</span>
                  </>
                )}
              </div>
            </div>

            {/* Action Buttons */}
            <div className="flex gap-2">
              <button
                onClick={handleNewAddress}
                className="
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg
                  bg-navy-700 border border-navy-600 text-gray-300
                  hover:border-orange-500/30 hover:text-orange-400 transition-all text-sm
                "
              >
                <RefreshCw className="w-3.5 h-3.5" />
                New Address
              </button>
              <button
                onClick={handleCopy}
                className={`
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm transition-all
                  ${copied
                    ? 'bg-neon-green/15 border border-neon-green/30 text-neon-green'
                    : 'bg-orange-500/15 border border-orange-500/30 text-orange-400 hover:bg-orange-500/25'
                  }
                `}
              >
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    Copy Address
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function SendPaymentDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [invoice, setInvoice] = useState('');
  const [decodedInvoice, setDecodedInvoice] = useState<DecodedInvoice | null>(null);
  const [feeLimitSats, setFeeLimitSats] = useState<string>('');
  const [paymentResult, setPaymentResult] = useState<{ success: boolean; hash?: string; fees?: number; hops?: number; error?: string } | null>(null);

  // Detect input type
  const inputTrimmed = invoice.trim();
  const isLightningAddress = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(inputTrimmed);
  const isLNURL = /^lnurl[a-z0-9]+$/i.test(inputTrimmed);
  const isBOLT11 = /^ln(bc|tb|tbs|bcrt)[a-z0-9]+$/i.test(inputTrimmed);

  const decodeMutation = useMutation({
    mutationFn: (payReq: string) => walletService.decodeInvoice(payReq),
    onSuccess: (data) => {
      setDecodedInvoice(data);
    },
  });

  const sendMutation = useMutation({
    mutationFn: () => {
      const bolt11 = inputTrimmed;
      const feeLimit = feeLimitSats ? parseInt(feeLimitSats, 10) : undefined;
      return walletService.sendPayment(bolt11, feeLimit);
    },
    onSuccess: (data) => {
      setPaymentResult({
        success: true,
        hash: data.payment_hash,
        fees: data.payment_route?.total_fees,
        hops: data.payment_route?.hops,
      });
      // Refresh wallet data
      queryClient.invalidateQueries({ queryKey: ['wallet-summary'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-payments'] });
    },
    onError: (err) => {
      const message = err instanceof Error ? err.message : 'Payment failed';
      // Try to extract detail from axios error
      const axiosErr = err as { response?: { data?: { detail?: string } } };
      setPaymentResult({
        success: false,
        error: axiosErr.response?.data?.detail || message,
      });
    },
  });

  const handlePaste = useCallback(async () => {
    const text = await navigator.clipboard.readText();
    if (text) {
      // Strip lightning: prefix if present
      const clean = text.trim().replace(/^lightning:/i, '');
      setInvoice(clean);
      setDecodedInvoice(null);
      setPaymentResult(null);
    }
  }, []);

  const handleDecode = useCallback(() => {
    if (isBOLT11) {
      decodeMutation.mutate(inputTrimmed);
    }
  }, [inputTrimmed, isBOLT11, decodeMutation]);

  const handleSend = useCallback(() => {
    sendMutation.mutate();
  }, [sendMutation]);

  const handleReset = useCallback(() => {
    setInvoice('');
    setDecodedInvoice(null);
    setPaymentResult(null);
    setFeeLimitSats('');
    decodeMutation.reset();
    sendMutation.reset();
  }, [decodeMutation, sendMutation]);

  // Check if invoice is expired
  const isExpired = decodedInvoice
    ? (decodedInvoice.timestamp + decodedInvoice.expiry) * 1000 < Date.now()
    : false;

  // Time remaining
  const expiresAt = decodedInvoice
    ? new Date((decodedInvoice.timestamp + decodedInvoice.expiry) * 1000)
    : null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 card border-neon-cyan/30 bg-gradient-to-br from-navy-800 to-navy-900 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <Send className="w-5 h-5 text-neon-cyan" />
            <h2 className="text-lg font-semibold text-white">Send Payment</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-navy-700 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-5">Pay a Lightning invoice, LNURL, or Lightning address.</p>

        {paymentResult ? (
          /* ── Payment Result ── */
          <div className="space-y-4">
            {paymentResult.success ? (
              <div className="text-center space-y-3">
                <div className="w-14 h-14 mx-auto rounded-full bg-neon-green/15 border border-neon-green/30 flex items-center justify-center">
                  <CheckCircle2 className="w-7 h-7 text-neon-green" />
                </div>
                <div>
                  <p className="text-lg font-semibold text-white">Payment Sent</p>
                  {decodedInvoice && (
                    <p className="text-sm text-gray-400 mt-1">
                      {formatSats(decodedInvoice.num_satoshis)} sats
                    </p>
                  )}
                </div>
                {(paymentResult.fees !== undefined || paymentResult.hops !== undefined) && (
                  <div className="flex justify-center gap-4 text-xs text-gray-500">
                    {paymentResult.fees !== undefined && (
                      <span>Fee: <span className="text-gray-300">{paymentResult.fees} sats</span></span>
                    )}
                    {paymentResult.hops !== undefined && (
                      <span>Hops: <span className="text-gray-300">{paymentResult.hops}</span></span>
                    )}
                  </div>
                )}
                {paymentResult.hash && (
                  <div className="bg-navy-900 border border-navy-700 rounded-lg p-3">
                    <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-1">Payment Hash</p>
                    <div className="flex items-center gap-2">
                      <p className="font-mono text-[11px] text-gray-400 truncate flex-1">{paymentResult.hash}</p>
                      <CopyButton text={paymentResult.hash} />
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center space-y-3">
                <div className="w-14 h-14 mx-auto rounded-full bg-neon-pink/15 border border-neon-pink/30 flex items-center justify-center">
                  <AlertCircle className="w-7 h-7 text-neon-pink" />
                </div>
                <div>
                  <p className="text-lg font-semibold text-white">Payment Failed</p>
                  <p className="text-sm text-gray-400 mt-1">{paymentResult.error}</p>
                </div>
              </div>
            )}

            <div className="flex gap-2 pt-2">
              <button
                onClick={handleReset}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-navy-700 border border-navy-600 text-gray-300 hover:border-neon-cyan/30 hover:text-neon-cyan transition-all text-sm"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                New Payment
              </button>
              <button
                onClick={onClose}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg bg-neon-cyan/15 border border-neon-cyan/30 text-neon-cyan hover:bg-neon-cyan/25 transition-all text-sm"
              >
                Done
              </button>
            </div>
          </div>
        ) : !decodedInvoice ? (
          /* ── Invoice Input ── */
          <div className="space-y-4">
            {/* Input area */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-xs text-gray-500 uppercase tracking-wide">Invoice or Address</label>
                <button
                  onClick={handlePaste}
                  className="flex items-center gap-1 text-[11px] text-neon-cyan hover:text-neon-cyan/80 transition-colors"
                >
                  <Copy className="w-3 h-3" />
                  Paste
                </button>
              </div>
              <textarea
                value={invoice}
                onChange={(e) => {
                  setInvoice(e.target.value);
                  setDecodedInvoice(null);
                  decodeMutation.reset();
                }}
                placeholder="lnbc... / LNURL... / user@wallet.com"
                className="
                  w-full h-24 px-3 py-2.5 rounded-lg text-sm font-mono
                  bg-navy-900 border border-navy-700 text-gray-300 placeholder-gray-600
                  focus:outline-none focus:border-neon-cyan/40 focus:ring-1 focus:ring-neon-cyan/20
                  resize-none transition-all
                "
                spellCheck={false}
              />
            </div>

            {/* Input type indicator */}
            {inputTrimmed && (
              <div className="flex items-center gap-2">
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${
                  isBOLT11 ? 'bg-neon-green' :
                  isLightningAddress ? 'bg-neon-yellow' :
                  isLNURL ? 'bg-neon-yellow' :
                  'bg-gray-500'
                }`} />
                <span className="text-xs text-gray-500">
                  {isBOLT11 ? 'BOLT11 Invoice' :
                   isLightningAddress ? 'Lightning Address' :
                   isLNURL ? 'LNURL' :
                   'Unknown format'}
                </span>
              </div>
            )}

            {/* Unsupported type notice */}
            {inputTrimmed && (isLightningAddress || isLNURL) && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-yellow/10 border border-neon-yellow/20">
                <AlertCircle className="w-4 h-4 text-neon-yellow flex-shrink-0" />
                <p className="text-xs text-neon-yellow">
                  {isLightningAddress ? 'Lightning Address' : 'LNURL'} support coming soon. For now, request a BOLT11 invoice from the recipient and paste it here.
                </p>
              </div>
            )}

            {/* Decode error */}
            {decodeMutation.isError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-pink/10 border border-neon-pink/20">
                <AlertCircle className="w-4 h-4 text-neon-pink flex-shrink-0" />
                <p className="text-xs text-neon-pink">
                  {(() => {
                    const axiosErr = decodeMutation.error as { response?: { data?: { detail?: string } } };
                    return axiosErr.response?.data?.detail || 'Failed to decode invoice. Check the format and try again.';
                  })()}
                </p>
              </div>
            )}

            {/* Decode / Review button */}
            <button
              onClick={handleDecode}
              disabled={!isBOLT11 || decodeMutation.isPending}
              className="
                w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg
                bg-neon-cyan/15 border border-neon-cyan/30 text-neon-cyan font-medium
                hover:bg-neon-cyan/25 hover:border-neon-cyan/50 transition-all
                disabled:opacity-40 disabled:cursor-not-allowed
              "
            >
              {decodeMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Decoding...
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4" />
                  Review Invoice
                </>
              )}
            </button>
          </div>
        ) : (
          /* ── Decoded Invoice Review ── */
          <div className="space-y-4">
            {/* Amount */}
            <div className="text-center py-2">
              <p className="text-3xl font-bold font-mono text-white">
                {decodedInvoice.num_satoshis > 0 ? formatSats(decodedInvoice.num_satoshis) : 'Any amount'}
              </p>
              <p className="text-xs text-gray-500 mt-1">
                {decodedInvoice.num_satoshis > 0 ? 'sats' : '(open amount invoice)'}
              </p>
            </div>

            {/* Details card */}
            <div className="bg-navy-900 border border-navy-700 rounded-xl p-4 space-y-3">
              {decodedInvoice.description && (
                <div>
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-0.5">Description</p>
                  <p className="text-sm text-gray-300">{decodedInvoice.description}</p>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-0.5">Destination</p>
                  <p className="font-mono text-[11px] text-gray-400 truncate">{decodedInvoice.destination}</p>
                </div>
                <div>
                  <p className="text-[10px] text-gray-500 uppercase tracking-wide mb-0.5">Expires</p>
                  <p className={`text-[11px] ${isExpired ? 'text-neon-pink' : 'text-gray-400'}`}>
                    {isExpired ? 'EXPIRED' : expiresAt ? expiresAt.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}
                  </p>
                </div>
              </div>
            </div>

            {/* Expired warning */}
            {isExpired && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-pink/10 border border-neon-pink/20">
                <AlertCircle className="w-4 h-4 text-neon-pink flex-shrink-0" />
                <p className="text-xs text-neon-pink">
                  This invoice has expired. Request a fresh one from the recipient.
                </p>
              </div>
            )}

            {/* Fee limit (optional) */}
            <div>
              <label className="text-[10px] text-gray-500 uppercase tracking-wide mb-1 block">
                Max Routing Fee <span className="normal-case text-gray-600">(optional)</span>
              </label>
              <div className="relative">
                <input
                  type="number"
                  min="0"
                  value={feeLimitSats}
                  onChange={(e) => setFeeLimitSats(e.target.value)}
                  placeholder="Auto"
                  className="
                    w-full px-3 py-2 rounded-lg text-sm font-mono
                    bg-navy-900 border border-navy-700 text-gray-300 placeholder-gray-600
                    focus:outline-none focus:border-neon-cyan/40 focus:ring-1 focus:ring-neon-cyan/20
                    transition-all [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none
                  "
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-600">sats</span>
              </div>
            </div>

            {/* Send error */}
            {sendMutation.isError && !paymentResult && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-pink/10 border border-neon-pink/20">
                <AlertCircle className="w-4 h-4 text-neon-pink flex-shrink-0" />
                <p className="text-xs text-neon-pink">
                  {(() => {
                    const axiosErr = sendMutation.error as { response?: { data?: { detail?: string } } };
                    return axiosErr.response?.data?.detail || 'Payment failed. Try again.';
                  })()}
                </p>
              </div>
            )}

            {/* Action buttons */}
            <div className="flex gap-2 pt-1">
              <button
                onClick={handleReset}
                className="flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-navy-700 border border-navy-600 text-gray-300 hover:border-navy-500 hover:text-gray-200 transition-all text-sm"
              >
                <ChevronUp className="w-3.5 h-3.5 rotate-[-90deg]" />
                Back
              </button>
              <button
                onClick={handleSend}
                disabled={isExpired || sendMutation.isPending}
                className="
                  flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg
                  bg-neon-cyan/15 border border-neon-cyan/30 text-neon-cyan font-medium
                  hover:bg-neon-cyan/25 hover:border-neon-cyan/50 transition-all
                  disabled:opacity-40 disabled:cursor-not-allowed text-sm
                "
              >
                {sendMutation.isPending ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Sending...
                  </>
                ) : (
                  <>
                    <Send className="w-4 h-4" />
                    Send {decodedInvoice.num_satoshis > 0 ? `${formatSats(decodedInvoice.num_satoshis)} sats` : 'Payment'}
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ReceiveInvoiceDialog({ onClose }: { onClose: () => void }) {
  const [amountSats, setAmountSats] = useState<string>('');
  const [memo, setMemo] = useState('');
  const [expiry, setExpiry] = useState<'600' | '3600' | '86400'>('3600');
  const [createdInvoice, setCreatedInvoice] = useState<{ paymentRequest: string; rHash: string } | null>(null);
  const [copied, setCopied] = useState(false);

  const createMutation = useMutation({
    mutationFn: () => {
      const amount = amountSats ? parseInt(amountSats, 10) : 0;
      return walletService.createInvoice(amount, memo, parseInt(expiry, 10));
    },
    onSuccess: (data) => {
      setCreatedInvoice({
        paymentRequest: data.payment_request,
        rHash: data.r_hash,
      });
    },
  });

  const handleCreate = useCallback(() => {
    createMutation.mutate();
  }, [createMutation]);

  const handleCopy = useCallback(async () => {
    if (!createdInvoice) return;
    await navigator.clipboard.writeText(createdInvoice.paymentRequest);
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }, [createdInvoice]);

  const handleReset = useCallback(() => {
    setCreatedInvoice(null);
    setCopied(false);
    setAmountSats('');
    setMemo('');
    createMutation.reset();
  }, [createMutation]);

  const expiryOptions = [
    { value: '600' as const, label: '10 min' },
    { value: '3600' as const, label: '1 hour' },
    { value: '86400' as const, label: '24 hours' },
  ];

  // BOLT11 invoices should be uppercased in QR for optimal encoding
  const qrValue = createdInvoice ? createdInvoice.paymentRequest.toUpperCase() : '';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      {/* Dialog */}
      <div className="relative w-full max-w-md mx-4 card border-neon-green/30 bg-gradient-to-br from-navy-800 to-navy-900 shadow-2xl shadow-black/40 animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <ArrowDownLeft className="w-5 h-5 text-neon-green" />
            <h2 className="text-lg font-semibold text-white">Receive Lightning</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-gray-500 hover:text-gray-300 hover:bg-navy-700 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-5">Create an invoice for someone to pay you over Lightning.</p>

        {!createdInvoice ? (
          /* ── Invoice Form ── */
          <div className="space-y-4">
            {/* Amount */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">Amount</label>
              <div className="relative">
                <input
                  type="number"
                  min="0"
                  value={amountSats}
                  onChange={(e) => setAmountSats(e.target.value)}
                  placeholder="0 = any amount"
                  className="
                    w-full px-3 py-2.5 rounded-lg text-sm font-mono
                    bg-navy-900 border border-navy-700 text-gray-300 placeholder-gray-600
                    focus:outline-none focus:border-neon-green/40 focus:ring-1 focus:ring-neon-green/20
                    transition-all [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none
                  "
                />
                <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-600">sats</span>
              </div>
              <p className="text-[10px] text-gray-600 mt-1">Leave empty or 0 for an open-amount invoice.</p>
            </div>

            {/* Memo */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">Memo <span className="normal-case text-gray-600">(optional)</span></label>
              <input
                type="text"
                value={memo}
                onChange={(e) => setMemo(e.target.value)}
                placeholder="What is this payment for?"
                maxLength={256}
                className="
                  w-full px-3 py-2.5 rounded-lg text-sm
                  bg-navy-900 border border-navy-700 text-gray-300 placeholder-gray-600
                  focus:outline-none focus:border-neon-green/40 focus:ring-1 focus:ring-neon-green/20
                  transition-all
                "
              />
            </div>

            {/* Expiry */}
            <div>
              <label className="text-xs text-gray-500 uppercase tracking-wide mb-1.5 block">Expires In</label>
              <div className="grid grid-cols-3 gap-2">
                {expiryOptions.map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setExpiry(opt.value)}
                    className={`
                      px-3 py-2 rounded-lg border text-sm font-medium text-center transition-all
                      ${expiry === opt.value
                        ? 'border-neon-green/50 bg-neon-green/10 text-white'
                        : 'border-navy-600 bg-navy-800/50 text-gray-400 hover:border-navy-500 hover:text-gray-300'
                      }
                    `}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Error */}
            {createMutation.isError && (
              <div className="flex items-center gap-2 p-3 rounded-lg bg-neon-pink/10 border border-neon-pink/20">
                <AlertCircle className="w-4 h-4 text-neon-pink flex-shrink-0" />
                <p className="text-xs text-neon-pink">
                  {(() => {
                    const axiosErr = createMutation.error as { response?: { data?: { detail?: string } } };
                    return axiosErr.response?.data?.detail || 'Failed to create invoice.';
                  })()}
                </p>
              </div>
            )}

            {/* Create button */}
            <button
              onClick={handleCreate}
              disabled={createMutation.isPending}
              className="
                w-full flex items-center justify-center gap-2 px-4 py-3 rounded-lg
                bg-neon-green/15 border border-neon-green/30 text-neon-green font-medium
                hover:bg-neon-green/25 hover:border-neon-green/50 transition-all
                disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              {createMutation.isPending ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Creating...
                </>
              ) : (
                <>
                  <Zap className="w-4 h-4" />
                  Create Invoice
                </>
              )}
            </button>
          </div>
        ) : (
          /* ── Created Invoice Display ── */
          <div className="space-y-4">
            {/* Amount summary */}
            {amountSats && parseInt(amountSats, 10) > 0 && (
              <div className="text-center">
                <p className="text-2xl font-bold font-mono text-white">{formatSats(parseInt(amountSats, 10))}</p>
                <p className="text-xs text-gray-500">sats</p>
              </div>
            )}

            {/* QR Code */}
            <div className="flex justify-center">
              <div className="bg-white rounded-2xl p-4 shadow-lg shadow-black/20">
                <QRCodeSVG
                  value={qrValue}
                  size={200}
                  level="M"
                  includeMargin={false}
                />
              </div>
            </div>

            {/* Label */}
            <div className="flex justify-center gap-2">
              <span className="text-[10px] uppercase tracking-wider text-gray-500 bg-navy-800 border border-navy-700 px-2.5 py-1 rounded-full">
                Lightning Invoice
              </span>
              {memo && (
                <span className="text-[10px] text-gray-500 bg-navy-800 border border-navy-700 px-2.5 py-1 rounded-full truncate max-w-[180px]">
                  {memo}
                </span>
              )}
            </div>

            {/* Invoice text + copy */}
            <div
              onClick={handleCopy}
              className="
                group cursor-pointer bg-navy-900 border border-navy-700 rounded-xl
                p-4 hover:border-neon-green/30 transition-all
              "
            >
              <p className="font-mono text-[11px] text-gray-400 text-center break-all leading-relaxed select-all group-hover:text-gray-300 transition-colors max-h-20 overflow-y-auto">
                {createdInvoice.paymentRequest}
              </p>
              <div className="flex items-center justify-center gap-1.5 mt-3">
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-neon-green" />
                    <span className="text-xs text-neon-green font-medium">Copied!</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5 text-gray-500 group-hover:text-neon-green transition-colors" />
                    <span className="text-xs text-gray-500 group-hover:text-neon-green transition-colors">Click to copy</span>
                  </>
                )}
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex gap-2">
              <button
                onClick={handleReset}
                className="
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg
                  bg-navy-700 border border-navy-600 text-gray-300
                  hover:border-neon-green/30 hover:text-neon-green transition-all text-sm
                "
              >
                <RefreshCw className="w-3.5 h-3.5" />
                New Invoice
              </button>
              <button
                onClick={handleCopy}
                className={`
                  flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm transition-all
                  ${copied
                    ? 'bg-neon-green/15 border border-neon-green/30 text-neon-green'
                    : 'bg-neon-green/15 border border-neon-green/30 text-neon-green hover:bg-neon-green/25'
                  }
                `}
              >
                {copied ? (
                  <>
                    <Check className="w-3.5 h-3.5" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    Copy Invoice
                  </>
                )}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FeeEstimateBar({ fees, mempoolUrl }: { fees: RecommendedFees; mempoolUrl: string }) {
  if (!fees.priorities) return null;
  const { low, medium, high } = fees.priorities;

  const priorityColor = (key: string) => {
    switch (key) {
      case 'low': return 'text-neon-green';
      case 'medium': return 'text-neon-yellow';
      case 'high': return 'text-neon-pink';
      default: return 'text-gray-400';
    }
  };

  const priorityBorder = (key: string) => {
    switch (key) {
      case 'low': return 'border-neon-green/30';
      case 'medium': return 'border-neon-yellow/30';
      case 'high': return 'border-neon-pink/30';
      default: return 'border-navy-600';
    }
  };

  const items = [
    { key: 'low', ...low },
    { key: 'medium', ...medium },
    { key: 'high', ...high },
  ];

  return (
    <div className="flex flex-wrap items-center gap-3 px-1">
      <div className="flex items-center gap-1.5 text-xs text-gray-500">
        <Zap className="w-3 h-3" />
        <span>Mempool Fees</span>
      </div>
      {items.map(item => (
        <div
          key={item.key}
          className={`flex items-center gap-2 bg-navy-800/60 border ${priorityBorder(item.key)} rounded-lg px-3 py-1.5`}
        >
          <span className="text-xs text-gray-400 capitalize">{item.key}</span>
          <span className={`text-sm font-mono font-semibold ${priorityColor(item.key)}`}>
            {item.sat_per_vbyte}
          </span>
          <span className="text-xs text-gray-600">sat/vB</span>
        </div>
      ))}
      <a
        href={`${mempoolUrl}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs text-gray-600 hover:text-neon-cyan transition-colors ml-auto"
      >
        mempool →
      </a>
    </div>
  );
}

function NodeInfoCard({ info }: { info: WalletSummary['node_info'] }) {
  const [expanded, setExpanded] = useState(false);

  if (!info) return null;

  return (
    <div className="card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <Radio className="w-5 h-5 text-neon-green" />
          <div className="text-left">
            <h3 className="text-sm font-medium text-white">{info.alias || 'LND Node'}</h3>
            <p className="text-xs text-gray-500">
              {info.num_active_channels} channels · {info.num_peers} peers · block {info.block_height.toLocaleString()}
            </p>
          </div>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
      </button>
      
      {expanded && (
        <div className="mt-4 pt-4 border-t border-navy-700 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <InfoRow label="Pubkey" value={info.identity_pubkey} copyable mono truncate />
            <InfoRow label="Version" value={info.version} />
            <InfoRow label="Synced to Chain" value={info.synced_to_chain ? '✓ Yes' : '✗ No'} />
            <InfoRow label="Synced to Graph" value={info.synced_to_graph ? '✓ Yes' : '✗ No'} />
            <InfoRow label="Active Channels" value={String(info.num_active_channels)} />
            <InfoRow label="Inactive Channels" value={String(info.num_inactive_channels)} />
            <InfoRow label="Pending Channels" value={String(info.num_pending_channels)} />
            <InfoRow label="Peers" value={String(info.num_peers)} />
          </div>
          {info.uris && info.uris.length > 0 && (
            <div className="pt-2">
              <p className="text-xs text-gray-500 mb-1">URIs:</p>
              {info.uris.map((uri, i) => (
                <p key={i} className="text-xs text-gray-400 font-mono break-all">{uri}</p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function InfoRow({ label, value, copyable, mono, truncate }: {
  label: string;
  value: string;
  copyable?: boolean;
  mono?: boolean;
  truncate?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 w-32 flex-shrink-0">{label}</span>
      <span className={`text-xs text-gray-300 ${mono ? 'font-mono' : ''} ${truncate ? 'truncate' : ''}`}>
        {value}
      </span>
      {copyable && <CopyButton text={value} />}
    </div>
  );
}

// --- Tab Components ---

function ChannelsTab({ channels, mempoolUrl, onOpenChannel }: { channels?: Channel[]; mempoolUrl: string; onOpenChannel: () => void }) {
  const { data: pendingData } = useQuery({
    queryKey: ['wallet-pending-channels-detail'],
    queryFn: () => walletService.getPendingChannelsDetail(),
    refetchInterval: 15_000,
  });

  const pendingChannels = pendingData?.pending_channels ?? [];

  if (!channels) {
    return <div className="text-center py-8"><Loader2 className="w-5 h-5 text-neon-yellow animate-spin mx-auto" /></div>;
  }

  // Sort: active first, then by capacity descending
  const sorted = [...channels].sort((a, b) => {
    if (a.active !== b.active) return a.active ? -1 : 1;
    return b.capacity - a.capacity;
  });

  return (
    <div className="space-y-4">
      {/* Header with Open Channel button */}
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">
          {channels.length} channel{channels.length !== 1 ? 's' : ''}
          {pendingChannels.length > 0 && ` · ${pendingChannels.length} pending`}
        </span>
        <button
          onClick={onOpenChannel}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium
                     bg-blue-500/15 border border-blue-500/30 text-blue-400
                     hover:bg-blue-500/25 hover:border-blue-500/50 transition-all"
        >
          <Plus className="w-3.5 h-3.5" />
          Open Channel
        </button>
      </div>

      {/* Pending Channels */}
      {pendingChannels.length > 0 && (
        <div className="space-y-2">
          {pendingChannels.map((pch) => {
            const txid = pch.channel_point?.split(':')[0] || pch.closing_txid || '';
            const statusLabel = pch.type === 'pending_open' ? 'Opening' : pch.type === 'pending_close' ? 'Closing' : 'Force Closing';
            const statusColor = pch.type === 'pending_open' ? 'text-neon-yellow' : 'text-orange-400';
            return (
              <div key={pch.channel_point} className="card py-3 border-dashed border-navy-600">
                <div className="flex items-center justify-between mb-1.5">
                  <div className="flex items-center gap-2">
                    <Loader2 className={`w-3.5 h-3.5 animate-spin ${statusColor}`} />
                    <span className="text-sm font-medium text-white truncate max-w-[200px]">
                      {pch.remote_node_pub.slice(0, 12)}...
                    </span>
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${statusColor} bg-navy-800`}>
                      {statusLabel}
                    </span>
                  </div>
                  <span className="text-xs text-gray-400 font-mono">
                    {formatSats(pch.capacity)} cap
                  </span>
                </div>
                {txid && (
                  <a
                    href={`${mempoolUrl}/tx/${txid}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-blue-400 hover:text-blue-300 flex items-center gap-1"
                  >
                    <ExternalLink className="w-3 h-3" />
                    View on Mempool
                  </a>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Active / Inactive Channels */}
      {sorted.length === 0 && pendingChannels.length === 0 ? (
        <div className="text-center py-8 text-gray-400">
          <GitBranch className="w-8 h-8 mx-auto mb-2 text-gray-600" />
          No open channels
        </div>
      ) : (
        <div className="space-y-2">
          {sorted.map(ch => (
            <ChannelRow key={ch.chan_id} channel={ch} />
          ))}
        </div>
      )}
    </div>
  );
}

function ChannelRow({ channel }: { channel: Channel }) {
  const localPct = channel.capacity > 0 ? (channel.local_balance / channel.capacity) * 100 : 0;
  const remotePct = channel.capacity > 0 ? (channel.remote_balance / channel.capacity) * 100 : 0;
  
  return (
    <div className={`card py-3 ${!channel.active ? 'opacity-60' : ''}`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${channel.active ? 'bg-neon-green' : 'bg-gray-500'}`} />
          <span className="text-sm font-medium text-white truncate max-w-[200px]">
            {channel.peer_alias || `${channel.remote_pubkey.slice(0, 12)}...`}
          </span>
          {channel.private && (
            <span title="Private channel">
              <Shield className="w-3 h-3 text-gray-500" />
            </span>
          )}
        </div>
        <span className="text-xs text-gray-400 font-mono">
          {formatSats(channel.capacity)} cap
        </span>
      </div>
      
      {/* Capacity bar */}
      <div className="h-2 bg-navy-900 rounded-full overflow-hidden flex">
        <div
          className="bg-neon-yellow/70 rounded-l"
          style={{ width: `${localPct}%` }}
          title={`Local: ${formatSats(channel.local_balance)} sats`}
        />
        <div
          className="bg-neon-cyan/40 rounded-r"
          style={{ width: `${remotePct}%` }}
          title={`Remote: ${formatSats(channel.remote_balance)} sats`}
        />
      </div>
      
      {/* Labels under bar */}
      <div className="flex justify-between text-[10px] mt-1">
        <span className="text-neon-yellow/70">
          ← {formatSats(channel.local_balance)} local
        </span>
        <span className="text-neon-cyan/60">
          {formatSats(channel.remote_balance)} remote →
        </span>
      </div>
      
      {/* Stats row */}
      <div className="flex items-center gap-4 mt-2 text-[10px] text-gray-500">
        <span>Sent: {formatSats(channel.total_satoshis_sent)}</span>
        <span>Recv: {formatSats(channel.total_satoshis_received)}</span>
        <span>Updates: {channel.num_updates.toLocaleString()}</span>
        {channel.uptime > 0 && (
          <span className="ml-auto">
            <Clock className="w-3 h-3 inline mr-0.5" />
            {formatUptime(channel.uptime)} / {formatUptime(channel.lifetime)}
          </span>
        )}
      </div>
    </div>
  );
}

function PaymentsTab({ payments }: { payments?: Payment[] }) {
  if (!payments) {
    return <div className="text-center py-8"><Loader2 className="w-5 h-5 text-neon-yellow animate-spin mx-auto" /></div>;
  }

  if (payments.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <ArrowUpRight className="w-8 h-8 mx-auto mb-2 text-gray-600" />
        No payments yet
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_7rem_4rem_6rem] gap-4 px-3 py-2 text-xs text-gray-500 font-medium border-b border-navy-700">
        <span>Hash</span>
        <span className="text-right">Amount</span>
        <span className="text-right">Fee</span>
        <span className="text-right">Date</span>
      </div>
      {payments.map((p, i) => {
        const statusColor = 
          p.status === 'SUCCEEDED' ? 'text-neon-green' : 
          p.status === 'FAILED' ? 'text-neon-pink' : 
          'text-neon-yellow';
          
        return (
          <div key={p.payment_hash || i} className="grid grid-cols-[1fr_7rem_4rem_6rem] gap-4 px-3 py-2 text-xs hover:bg-navy-800 rounded transition-colors items-center">
            <div className="flex items-center gap-2">
              <ArrowUpRight className={`w-3 h-3 flex-shrink-0 ${statusColor}`} />
              <span className="font-mono text-gray-400 truncate">{p.payment_hash.slice(0, 16)}...</span>
              <CopyButton text={p.payment_hash} />
            </div>
            <span className="text-white font-mono text-right">{formatSats(p.value_sat)}</span>
            <span className="text-gray-500 font-mono text-right">{p.fee_sat}</span>
            <span className="text-gray-500 text-right whitespace-nowrap">{formatDate(p.creation_date)}</span>
          </div>
        );
      })}
    </div>
  );
}

function InvoicesTab({ invoices }: { invoices?: Invoice[] }) {
  const [expandedHash, setExpandedHash] = useState<string | null>(null);

  if (!invoices) {
    return <div className="text-center py-8"><Loader2 className="w-5 h-5 text-neon-yellow animate-spin mx-auto" /></div>;
  }

  if (invoices.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <ArrowDownLeft className="w-8 h-8 mx-auto mb-2 text-gray-600" />
        No invoices yet
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_7rem_4.5rem_6rem] gap-4 px-3 py-2 text-xs text-gray-500 font-medium border-b border-navy-700">
        <span>Memo / Type</span>
        <span className="text-right">Amount</span>
        <span className="text-right">Status</span>
        <span className="text-right">Date</span>
      </div>
      {invoices.map((inv, i) => {
        const stateColor = 
          inv.state === 'SETTLED' ? 'text-neon-green' :
          inv.state === 'CANCELED' ? 'text-neon-pink' :
          inv.state === 'ACCEPTED' ? 'text-neon-cyan' :
          'text-gray-400';
        const stateLabel = 
          inv.state === 'SETTLED' ? 'Settled' :
          inv.state === 'CANCELED' ? 'Canceled' :
          inv.state === 'ACCEPTED' ? 'Accepted' :
          'Open';
        const isExpanded = expandedHash === inv.r_hash;
        const hasQR = inv.payment_request && inv.state === 'OPEN';

        return (
          <div key={inv.r_hash || i}>
            <div
              className={`grid grid-cols-[1fr_7rem_4.5rem_6rem] gap-4 px-3 py-2 text-xs hover:bg-navy-800 rounded transition-colors items-center ${hasQR ? 'cursor-pointer' : ''}`}
              onClick={() => hasQR && setExpandedHash(isExpanded ? null : inv.r_hash)}
            >
              <div className="flex items-center gap-2">
                <ArrowDownLeft className={`w-3 h-3 flex-shrink-0 ${stateColor}`} />
                <span className="text-gray-300 truncate">
                  {inv.memo || (inv.is_keysend ? '⚡ Keysend' : 'Invoice')}
                </span>
                {hasQR && (
                  <span className="text-[10px] text-neon-cyan/60 ml-1">QR</span>
                )}
              </div>
              <span className="text-white font-mono text-right">
                {formatSats(inv.settled ? inv.amt_paid_sat : inv.value)}
              </span>
              <span className={`text-right ${stateColor}`}>{stateLabel}</span>
              <span className="text-gray-500 text-right whitespace-nowrap">
                {formatDate(inv.settled ? inv.settle_date : inv.creation_date)}
              </span>
            </div>
            {isExpanded && inv.payment_request && (
              <div className="flex justify-center py-3">
                <InvoiceQR paymentRequest={inv.payment_request} size={200} label="Scan to Pay" />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function OnchainTab({ transactions, mempoolUrl }: { transactions?: OnchainTransaction[]; mempoolUrl: string }) {
  if (!transactions) {
    return <div className="text-center py-8"><Loader2 className="w-5 h-5 text-neon-yellow animate-spin mx-auto" /></div>;
  }

  if (transactions.length === 0) {
    return (
      <div className="text-center py-8 text-gray-400">
        <LinkIcon className="w-8 h-8 mx-auto mb-2 text-gray-600" />
        No on-chain transactions yet
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="grid grid-cols-[1fr_7rem_4rem_6rem] gap-4 px-3 py-2 text-xs text-gray-500 font-medium border-b border-navy-700">
        <span>Transaction</span>
        <span className="text-right">Amount</span>
        <span className="text-right">Confs</span>
        <span className="text-right">Date</span>
      </div>
      {transactions.map((tx, i) => {
        const isIncoming = tx.amount > 0;
        
        return (
          <div key={tx.tx_hash || i} className="grid grid-cols-[1fr_7rem_4rem_6rem] gap-4 px-3 py-2 text-xs hover:bg-navy-800 rounded transition-colors items-center">
            <div className="flex items-center gap-2">
              {isIncoming
                ? <ArrowDownLeft className="w-3 h-3 flex-shrink-0 text-neon-green" />
                : <ArrowUpRight className="w-3 h-3 flex-shrink-0 text-neon-pink" />
              }
              <span className="font-mono text-gray-400 truncate">
                <a
                  href={`${mempoolUrl}/tx/${tx.tx_hash}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-neon-cyan transition-colors"
                  title="View in Mempool Explorer"
                >
                  {tx.tx_hash.slice(0, 16)}...
                </a>
              </span>
              <CopyButton text={tx.tx_hash} />
              {tx.label && (
                <span className="text-gray-500 truncate">{tx.label}</span>
              )}
            </div>
            <span className={`font-mono text-right ${isIncoming ? 'text-neon-green' : 'text-white'}`}>
              {isIncoming ? '+' : ''}{formatSats(tx.amount)}
            </span>
            <span className={`text-right ${tx.num_confirmations === 0 ? 'text-neon-yellow' : 'text-gray-500'}`}>
              {tx.num_confirmations === 0 ? 'pending' : tx.num_confirmations.toLocaleString()}
            </span>
            <span className="text-gray-500 text-right whitespace-nowrap">{formatDate(tx.time_stamp)}</span>
          </div>
        );
      })}
    </div>
  );
}

export default WalletPage;
