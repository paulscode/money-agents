/**
 * BudgetPage — Bitcoin Budget Management
 *
 * Shows:
 * - Global budget summary dashboard
 * - Pending spend approval requests with approve/reject
 * - Transaction history
 * - Spend Advisor chat (per-approval)
 */
import { Layout } from '@/components/layout/Layout';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, useRef, useEffect } from 'react';
import {
  Shield, AlertTriangle, CheckCircle, XCircle, Clock,
  Zap, LinkIcon, ArrowUpRight, ArrowDownLeft, MessageCircle,
  Loader2, ChevronDown, ChevronUp, DollarSign, TrendingUp,
  Copy, Check, Pencil, ShieldAlert
} from 'lucide-react';
import bitcoinSvg from '@/assets/bitcoin.svg';
import {
  budgetService,
  formatSats,
  triggerLabel,
  txTypeLabel,
} from '@/services/budget';
import { logError } from '@/lib/logger';
import { walletService } from '@/services/wallet';
import { useAuthStore } from '@/stores/auth';
import { InvoiceQRToggle, InvoiceQR } from '@/components/shared/InvoiceQR';
import type {
  SpendApproval,
  BitcoinTransaction,
  GlobalBitcoinBudget,
  SpendAnalysis,
} from '@/services/budget';

function BitcoinIcon({ size = 24 }: { size?: number }) {
  return <img src={bitcoinSvg} alt="₿" width={size} height={size} className="inline-block" />;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '—';
  return new Date(dateStr).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

// ──────────────────────────────────────────────────────────────────────
// Status badges
// ──────────────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
    approved: 'bg-green-500/20 text-green-400 border-green-500/30',
    rejected: 'bg-red-500/20 text-red-400 border-red-500/30',
    expired: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    cancelled: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    confirmed: 'bg-green-500/20 text-green-400 border-green-500/30',
    failed: 'bg-red-500/20 text-red-400 border-red-500/30',
  };
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full border ${styles[status] || styles.pending}`}>
      {status}
    </span>
  );
}

function TriggerBadge({ trigger }: { trigger: SpendApproval['trigger'] }) {
  const styles: Record<string, string> = {
    no_budget: 'bg-orange-500/20 text-orange-400',
    over_budget: 'bg-red-500/20 text-red-400',
    global_limit: 'bg-purple-500/20 text-purple-400',
    manual_review: 'bg-blue-500/20 text-blue-400',
    velocity_breaker: 'bg-red-500/20 text-red-400',
  };
  return (
    <span className={`px-2 py-0.5 text-xs font-medium rounded-full ${styles[trigger] || ''}`}>
      {triggerLabel(trigger)}
    </span>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Inline Safety Limit Editor
// ──────────────────────────────────────────────────────────────────────

function SafetyLimitControl({ currentSats }: { currentSats: number }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState('');
  const [saving, setSaving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [editing]);

  const startEditing = () => {
    setValue(String(currentSats));
    setEditing(true);
  };

  const cancel = () => {
    setEditing(false);
    setValue('');
  };

  const save = async () => {
    const parsed = parseInt(value, 10);
    if (isNaN(parsed) || parsed < -1) return;
    setSaving(true);
    try {
      await walletService.updateSafetyLimit(parsed);
      queryClient.invalidateQueries({ queryKey: ['bitcoin-budget-global'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-config'] });
      setEditing(false);
    } catch (e) {
      logError('Failed to update safety limit:', e);
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') save();
    if (e.key === 'Escape') cancel();
  };

  if (editing) {
    return (
      <div className="flex items-center gap-1.5 mt-2">
        <Shield className="w-3 h-3 text-gray-500 shrink-0" />
        <input
          ref={inputRef}
          type="number"
          min="-1"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={cancel}
          className="w-24 px-1.5 py-0.5 text-xs bg-gray-800 border border-purple-500/50 rounded text-white font-mono
                     focus:outline-none focus:border-purple-400"
          disabled={saving}
        />
        <span className="text-xs text-gray-500">sats</span>
        {saving && <Loader2 className="w-3 h-3 animate-spin text-purple-400" />}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5 mt-2 group/limit">
      <span className="text-xs text-gray-500">
        Safety limit: {currentSats === 0 ? 'all require approval' : currentSats === -1 ? 'no limit' : formatSats(currentSats)}
      </span>
      <button
        onClick={startEditing}
        className="opacity-0 group-hover/limit:opacity-100 transition-opacity p-0.5 rounded hover:bg-purple-500/20"
        title="Edit safety limit"
      >
        <Pencil className="w-3 h-3 text-gray-500 hover:text-purple-400" />
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Global Budget Summary
// ──────────────────────────────────────────────────────────────────────

function BudgetOverview({ data }: { data: GlobalBitcoinBudget }) {
  const usagePercent = data.total_budget_sats > 0
    ? Math.min(100, (data.total_spent_sats / data.total_budget_sats) * 100)
    : 0;

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
      {/* Total Budget */}
      <div className="card bg-gradient-to-br from-neon-yellow/10 to-orange-500/5 border-neon-yellow/30">
        <div className="flex items-center gap-2 mb-2">
          <BitcoinIcon size={20} />
          <span className="text-sm text-gray-400">Total Budget</span>
        </div>
        <div className="text-2xl font-bold text-neon-yellow">
          {formatSats(data.total_budget_sats)}
        </div>
        <div className="text-xs text-gray-500 mt-1">
          {data.campaigns_with_budget} campaign{data.campaigns_with_budget !== 1 ? 's' : ''} with budget
        </div>
      </div>

      {/* Spent */}
      <div className="card bg-gradient-to-br from-red-500/10 to-red-900/5 border-red-500/20">
        <div className="flex items-center gap-2 mb-2">
          <ArrowUpRight className="w-5 h-5 text-red-400" />
          <span className="text-sm text-gray-400">Total Spent</span>
        </div>
        <div className="text-2xl font-bold text-red-400">
          {formatSats(data.total_spent_sats)}
        </div>
        <div className="mt-2 h-1.5 bg-gray-800 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${usagePercent > 90 ? 'bg-red-500' : usagePercent > 70 ? 'bg-yellow-500' : 'bg-neon-green'}`}
            style={{ width: `${usagePercent}%` }}
          />
        </div>
        <div className="text-xs text-gray-500 mt-1">{usagePercent.toFixed(1)}% of budget used</div>
      </div>

      {/* Remaining */}
      <div className="card bg-gradient-to-br from-neon-green/10 to-green-900/5 border-neon-green/20">
        <div className="flex items-center gap-2 mb-2">
          <TrendingUp className="w-5 h-5 text-neon-green" />
          <span className="text-sm text-gray-400">Remaining</span>
        </div>
        <div className="text-2xl font-bold text-neon-green">
          {formatSats(data.total_remaining_sats)}
        </div>
        {data.wallet_balance_sats !== null && (
          <div className="text-xs text-gray-500 mt-1">
            Wallet: {formatSats(data.wallet_balance_sats)}
          </div>
        )}
      </div>

      {/* Alerts */}
      <div className="card bg-gradient-to-br from-purple-500/10 to-purple-900/5 border-purple-500/20">
        <div className="flex items-center gap-2 mb-2">
          <Shield className="w-5 h-5 text-purple-400" />
          <span className="text-sm text-gray-400">Alerts</span>
        </div>
        <div className="space-y-1">
          {data.pending_approvals > 0 && (
            <div className="flex items-center gap-1 text-yellow-400 text-sm">
              <AlertTriangle className="w-3.5 h-3.5" />
              {data.pending_approvals} pending approval{data.pending_approvals !== 1 ? 's' : ''}
            </div>
          )}
          {data.campaigns_over_budget > 0 && (
            <div className="flex items-center gap-1 text-red-400 text-sm">
              <XCircle className="w-3.5 h-3.5" />
              {data.campaigns_over_budget} over budget
            </div>
          )}
          {data.campaigns_near_budget > 0 && (
            <div className="flex items-center gap-1 text-yellow-400 text-sm">
              <AlertTriangle className="w-3.5 h-3.5" />
              {data.campaigns_near_budget} near budget ({'≥'}80%)
            </div>
          )}
          {data.total_pending_sats > 0 && (
            <div className="flex items-center gap-1 text-blue-400 text-sm">
              <Clock className="w-3.5 h-3.5" />
              {formatSats(data.total_pending_sats)} pending
            </div>
          )}
          {data.pending_approvals === 0 && data.campaigns_over_budget === 0 && data.campaigns_near_budget === 0 && (
            <div className="flex items-center gap-1 text-neon-green text-sm">
              <CheckCircle className="w-3.5 h-3.5" />
              All clear
            </div>
          )}
        </div>
        <div className="text-xs text-gray-500 mt-2">
          <SafetyLimitControl currentSats={data.global_limit_sats} />
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Spend Approval Card
// ──────────────────────────────────────────────────────────────────────

function ApprovalCard({
  approval,
  onApprove,
  onReject,
  isPending,
}: {
  approval: SpendApproval;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
  isPending: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [showAdvisor, setShowAdvisor] = useState(false);

  return (
    <div className="card border-yellow-500/30 bg-gradient-to-br from-yellow-900/10 to-transparent">
      <div className="flex items-start justify-between">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-lg font-bold text-neon-yellow">
              {formatSats(approval.amount_sats)}
            </span>
            <TriggerBadge trigger={approval.trigger} />
            <StatusBadge status={approval.status} />
          </div>
          <p className="text-sm text-gray-300 truncate">{approval.description}</p>
          <p className="text-xs text-gray-500 mt-1">
            {formatDate(approval.created_at)}
            {approval.campaign_id && ` · Campaign ${approval.campaign_id.slice(0, 8)}…`}
          </p>
        </div>

        {/* Action buttons (only for pending) */}
        {approval.status === 'pending' && (
          <div className="flex items-center gap-2 ml-4 flex-shrink-0">
            <button
              onClick={() => setShowAdvisor(!showAdvisor)}
              className="p-2 rounded-lg bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 transition-colors"
              title="Ask Spend Advisor"
            >
              <MessageCircle className="w-4 h-4" />
            </button>
            <button
              onClick={() => onReject(approval.id)}
              disabled={isPending}
              className="px-3 py-1.5 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors text-sm font-medium disabled:opacity-50"
            >
              Reject
            </button>
            <button
              onClick={() => onApprove(approval.id)}
              disabled={isPending}
              className="px-3 py-1.5 rounded-lg bg-green-500/20 text-green-400 hover:bg-green-500/30 transition-colors text-sm font-medium disabled:opacity-50"
            >
              Approve
            </button>
          </div>
        )}

        <button
          onClick={() => setExpanded(!expanded)}
          className="p-1 text-gray-500 hover:text-gray-300 ml-2"
        >
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      </div>

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-gray-800">
          <div className="grid grid-cols-2 gap-3 text-sm">
            {approval.fee_estimate_sats > 0 && (
              <div>
                <span className="text-gray-500">Est. Fee:</span>{' '}
                <span className="text-gray-300">{formatSats(approval.fee_estimate_sats)}</span>
              </div>
            )}
            {approval.destination_address && (
              <div className="col-span-2">
                <span className="text-gray-500">Address:</span>{' '}
                <span className="text-gray-300 font-mono text-xs">{approval.destination_address}</span>
              </div>
            )}
            {approval.payment_request && (
              <div className="col-span-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span className="text-gray-500">Invoice:</span>{' '}
                    <span className="text-gray-300 font-mono text-xs truncate block">
                      {approval.payment_request.slice(0, 60)}…
                    </span>
                  </div>
                  <InvoiceQRToggle paymentRequest={approval.payment_request} />
                </div>
              </div>
            )}
            {/* Budget context */}
            {approval.budget_context && typeof approval.budget_context === 'object' && (
              <>
                {approval.budget_context.campaign_budget_sats !== undefined && (
                  <div>
                    <span className="text-gray-500">Campaign Budget:</span>{' '}
                    <span className="text-gray-300">
                      {formatSats(Number(approval.budget_context.campaign_budget_sats))}
                    </span>
                  </div>
                )}
                {approval.budget_context.campaign_remaining_sats !== undefined && (
                  <div>
                    <span className="text-gray-500">Remaining:</span>{' '}
                    <span className="text-gray-300">
                      {formatSats(Number(approval.budget_context.campaign_remaining_sats))}
                    </span>
                  </div>
                )}
              </>
            )}
            {approval.review_notes && (
              <div className="col-span-2">
                <span className="text-gray-500">Review Notes:</span>{' '}
                <span className="text-gray-300">{approval.review_notes}</span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Spend Advisor panel */}
      {showAdvisor && (
        <SpendAdvisorPanel approvalId={approval.id} />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Spend Advisor Panel (REST-based analysis)
// ──────────────────────────────────────────────────────────────────────

function SpendAdvisorPanel({ approvalId }: { approvalId: string }) {
  const { data: analysis, isLoading, error } = useQuery({
    queryKey: ['spend-analysis', approvalId],
    queryFn: () => budgetService.getSpendAnalysis(approvalId),
    staleTime: 5 * 60 * 1000, // Cache for 5 min
  });

  return (
    <div className="mt-3 pt-3 border-t border-purple-500/20">
      <div className="flex items-center gap-2 mb-2">
        <Shield className="w-4 h-4 text-purple-400" />
        <span className="text-sm font-medium text-purple-400">Spend Advisor Analysis</span>
        <span className="text-xs text-gray-500">(quality tier)</span>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-gray-400 text-sm py-4">
          <Loader2 className="w-4 h-4 animate-spin" />
          Analyzing spend request…
        </div>
      )}

      {error && (
        <div className="text-red-400 text-sm py-2">
          Failed to load analysis. {(error as Error).message}
        </div>
      )}

      {analysis && (
        <div className="prose prose-invert prose-sm max-w-none text-gray-300 whitespace-pre-wrap">
          {analysis.analysis}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Transaction List
// ──────────────────────────────────────────────────────────────────────

function TransactionList({ transactions }: { transactions: BitcoinTransaction[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (transactions.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500">
        No transactions recorded yet
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {transactions.map((tx) => {
        const isSend = tx.tx_type.includes('send');
        const isExpanded = expandedId === tx.id;
        const isLightning = tx.tx_type.includes('lightning');
        return (
          <div key={tx.id} className="rounded-lg bg-gray-900/50 border border-gray-800 overflow-hidden">
            <div
              className="flex items-center justify-between p-3 cursor-pointer hover:bg-white/[0.02] transition-colors"
              onClick={() => setExpandedId(isExpanded ? null : tx.id)}
            >
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-lg ${isSend ? 'bg-red-500/10' : 'bg-green-500/10'}`}>
                  {isSend
                    ? <ArrowUpRight className="w-4 h-4 text-red-400" />
                    : <ArrowDownLeft className="w-4 h-4 text-green-400" />
                  }
                </div>
                <div>
                  <div className="text-sm font-medium text-gray-200 flex items-center gap-2">
                    {txTypeLabel(tx.tx_type)}
                    {isExpanded
                      ? <ChevronUp className="w-3.5 h-3.5 text-gray-500" />
                      : <ChevronDown className="w-3.5 h-3.5 text-gray-500" />}
                  </div>
                  <div className="text-xs text-gray-500">
                    {formatDate(tx.created_at)}
                    {tx.description && ` · ${tx.description}`}
                  </div>
                </div>
              </div>
              <div className="text-right">
                <div className={`text-sm font-bold ${isSend ? 'text-red-400' : 'text-green-400'}`}>
                  {isSend ? '-' : '+'}{formatSats(tx.amount_sats)}
                </div>
                <StatusBadge status={tx.status} />
              </div>
            </div>

            {/* Expanded detail panel */}
            {isExpanded && (
              <div className="px-4 pb-4 pt-1 border-t border-gray-800">
                <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs">
                  {tx.description && (
                    <div className="col-span-2 mb-1">
                      <span className="text-gray-500">Notes:</span>{' '}
                      <span className="text-gray-300">{tx.description}</span>
                    </div>
                  )}
                  {tx.fee_sats > 0 && (
                    <DetailRow label="Fee" value={`${formatSats(tx.fee_sats)} sats`} />
                  )}
                  {tx.fee_sats > 0 && (
                    <DetailRow label="Total" value={`${formatSats(tx.amount_sats + tx.fee_sats)} sats`} />
                  )}
                  {tx.confirmed_at && (
                    <DetailRow label="Confirmed" value={formatDate(tx.confirmed_at)} />
                  )}
                  {tx.campaign_id && (
                    <DetailRow label="Campaign" value={tx.campaign_id.slice(0, 12) + '…'} mono />
                  )}
                  {isLightning && tx.payment_hash && (
                    <DetailRow label="Payment Hash" value={tx.payment_hash} mono copyable />
                  )}
                  {!isLightning && tx.txid && (
                    <DetailRow label="TXID" value={tx.txid} mono copyable />
                  )}
                  {tx.address && (
                    <DetailRow label="Address" value={tx.address} mono copyable />
                  )}
                  {tx.approval_id && (
                    <DetailRow label="Approval" value={tx.approval_id.slice(0, 12) + '…'} mono />
                  )}
                  <DetailRow label="ID" value={tx.id.slice(0, 12) + '…'} mono />
                </div>
                {/* QR code for receivable lightning invoices */}
                {tx.payment_request && !isSend && tx.status === 'pending' && (
                  <div className="mt-3 flex justify-center">
                    <InvoiceQR paymentRequest={tx.payment_request} size={160} label="Invoice" />
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Small label+value row for the transaction detail panel */
function DetailRow({ label, value, mono, copyable }: {
  label: string;
  value: string;
  mono?: boolean;
  copyable?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="col-span-2 flex items-center gap-2">
      <span className="text-gray-500 w-24 shrink-0">{label}:</span>
      <span className={`text-gray-300 truncate ${mono ? 'font-mono text-[11px]' : ''}`}>{value}</span>
      {copyable && (
        <button onClick={handleCopy} className="flex-shrink-0 p-0.5 hover:text-neon-cyan transition-colors text-gray-500">
          {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
        </button>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Per-Campaign Bitcoin Budget Breakdown
// ──────────────────────────────────────────────────────────────────────

function CampaignBreakdown({
  campaigns,
  totalBudget,
}: {
  campaigns: import('@/services/budget').CampaignBitcoinBudget[];
  totalBudget: number;
}) {
  if (campaigns.length === 0) {
    return (
      <div className="card text-center py-12 text-gray-500">
        <DollarSign className="w-8 h-8 mx-auto mb-2 text-gray-600" />
        <p>No campaigns with Bitcoin budgets</p>
        <p className="text-xs mt-1">Assign bitcoin_budget_sats to your campaign proposals</p>
      </div>
    );
  }

  // Sort by highest spent first
  const sorted = [...campaigns].sort((a, b) => b.bitcoin_spent_sats - a.bitcoin_spent_sats);

  return (
    <div className="space-y-3">
      {sorted.map((c) => {
        const budget = c.bitcoin_budget_sats || 0;
        const spent = c.bitcoin_spent_sats;
        const pct = budget > 0 ? Math.min(100, (spent / budget) * 100) : 0;
        const barColor = pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-neon-green';
        const shareOfTotal = totalBudget > 0 && budget > 0 ? ((budget / totalBudget) * 100).toFixed(1) : null;

        return (
          <div key={c.campaign_id} className="card border-gray-800">
            <div className="flex items-start justify-between mb-2">
              <div className="min-w-0">
                <h4 className="text-sm font-medium text-white truncate">
                  {c.campaign_title || c.campaign_id.slice(0, 12) + '…'}
                </h4>
                <div className="flex items-center gap-2 mt-0.5">
                  {c.campaign_status && (
                    <span className="text-[10px] uppercase tracking-wide text-gray-500">
                      {c.campaign_status}
                    </span>
                  )}
                  {shareOfTotal && (
                    <span className="text-[10px] text-gray-600">
                      {shareOfTotal}% of total budget
                    </span>
                  )}
                </div>
              </div>
              <div className="text-right flex-shrink-0 ml-4">
                <div className="text-sm font-bold text-white">
                  {formatSats(spent)}{' '}
                  <span className="text-gray-500 font-normal">/ {budget > 0 ? formatSats(budget) : '∞'}</span>
                </div>
                {c.bitcoin_remaining_sats != null && c.bitcoin_remaining_sats >= 0 && (
                  <div className="text-xs text-gray-500">
                    {formatSats(c.bitcoin_remaining_sats)} remaining
                  </div>
                )}
              </div>
            </div>

            {/* Progress bar */}
            {budget > 0 && (
              <div className="h-2 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${barColor}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
            )}
            <div className="flex items-center justify-between mt-1.5">
              <span className="text-xs text-gray-500">{pct.toFixed(1)}% used</span>
              <div className="flex items-center gap-3 text-xs">
                {c.bitcoin_received_sats > 0 && (
                  <span className="text-green-400">+{formatSats(c.bitcoin_received_sats)} received</span>
                )}
                {c.pending_approvals > 0 && (
                  <span className="text-yellow-400">{c.pending_approvals} pending</span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────
// Main Page
// ──────────────────────────────────────────────────────────────────────

export function BudgetPage() {
  const queryClient = useQueryClient();
  const { user } = useAuthStore();
  const [activeTab, setActiveTab] = useState<'approvals' | 'campaigns' | 'transactions'>('approvals');

  // Velocity breaker status
  const { data: breakerStatus } = useQuery({
    queryKey: ['velocity-breaker-status'],
    queryFn: () => walletService.getVelocityBreakerStatus(),
    refetchInterval: 30_000,
  });

  const resetBreakerMutation = useMutation({
    mutationFn: () => walletService.resetVelocityBreaker(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['velocity-breaker-status'] });
      queryClient.invalidateQueries({ queryKey: ['bitcoin-approvals-pending'] });
    },
  });

  // Queries
  const { data: globalBudget, isLoading: budgetLoading } = useQuery({
    queryKey: ['bitcoin-budget-global'],
    queryFn: () => budgetService.getGlobalBudget(),
    refetchInterval: 30_000,
  });

  const { data: approvalsData, isLoading: approvalsLoading } = useQuery({
    queryKey: ['bitcoin-approvals-pending'],
    queryFn: () => budgetService.getPendingApprovals(),
    refetchInterval: 15_000,
  });

  const { data: txData, isLoading: txLoading } = useQuery({
    queryKey: ['bitcoin-transactions'],
    queryFn: () => budgetService.getTransactions(),
    enabled: activeTab === 'transactions',
  });

  // Mutations
  const reviewMutation = useMutation({
    mutationFn: ({ id, action, notes }: { id: string; action: 'approved' | 'rejected'; notes?: string }) =>
      budgetService.reviewApproval(id, action, notes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['bitcoin-approvals-pending'] });
      queryClient.invalidateQueries({ queryKey: ['bitcoin-budget-global'] });
      queryClient.invalidateQueries({ queryKey: ['bitcoin-transactions'] });
      // Wallet balances change when approval triggers a payment
      queryClient.invalidateQueries({ queryKey: ['wallet-summary'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-payments'] });
      queryClient.invalidateQueries({ queryKey: ['wallet-invoices'] });
    },
  });

  const handleApprove = (id: string) => {
    if (confirm('Are you sure you want to approve this Bitcoin spend?')) {
      reviewMutation.mutate({ id, action: 'approved' });
    }
  };

  const handleReject = (id: string) => {
    const notes = prompt('Rejection reason (optional):');
    reviewMutation.mutate({ id, action: 'rejected', notes: notes || undefined });
  };

  const approvals = approvalsData?.approvals || [];
  const transactions = txData?.transactions || [];

  return (
    <Layout>
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <BitcoinIcon size={32} />
          <div>
            <h1 className="text-2xl font-bold text-white">Bitcoin Budget</h1>
            <p className="text-sm text-gray-400">
              Track spending, review approvals, and manage campaign Bitcoin budgets
            </p>
          </div>
        </div>

        {/* Velocity Circuit Breaker Alert */}
        {breakerStatus?.is_tripped && (
          <div className="mb-6 rounded-xl border border-red-500/50 bg-gradient-to-r from-red-900/30 to-red-800/10 p-4 shadow-lg shadow-red-500/10">
            <div className="flex items-start gap-3">
              <div className="p-2 rounded-lg bg-red-500/20 shrink-0">
                <ShieldAlert className="w-6 h-6 text-red-400" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-semibold text-red-400 mb-1">
                  Velocity Circuit Breaker Tripped
                </h3>
                <p className="text-sm text-gray-300 mb-2">
                  All agent payments are blocked. A burst of{' '}
                  <span className="font-mono text-white">
                    {breakerStatus.trip_context?.count ?? '?'}
                  </span>{' '}
                  transactions in{' '}
                  <span className="font-mono text-white">
                    {breakerStatus.trip_context?.window_seconds
                      ? `${Math.round(breakerStatus.trip_context.window_seconds / 60)} min`
                      : '?'}
                  </span>{' '}
                  exceeded the threshold of{' '}
                  <span className="font-mono text-white">
                    {breakerStatus.trip_context?.threshold ?? breakerStatus.config?.max_txns ?? '?'}
                  </span>.
                </p>
                <p className="text-xs text-gray-500 mb-3">
                  Tripped at{' '}
                  {breakerStatus.tripped_at
                    ? new Date(breakerStatus.tripped_at).toLocaleString()
                    : 'unknown'}
                  {breakerStatus.trip_context?.recent_tx_ids && breakerStatus.trip_context.recent_tx_ids.length > 0 && (
                    <span>
                      {' '}&middot; {breakerStatus.trip_context.recent_tx_ids.length} recent transaction{breakerStatus.trip_context.recent_tx_ids.length !== 1 ? 's' : ''}
                    </span>
                  )}
                </p>
                {user?.role === 'admin' ? (
                  <button
                    onClick={() => {
                      if (confirm('Have you reviewed the recent transactions? This will re-enable agent payments.')) {
                        resetBreakerMutation.mutate();
                      }
                    }}
                    disabled={resetBreakerMutation.isPending}
                    className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors flex items-center gap-2"
                  >
                    {resetBreakerMutation.isPending ? (
                      <><Loader2 className="w-4 h-4 animate-spin" /> Resetting…</>
                    ) : (
                      <><CheckCircle className="w-4 h-4" /> I've Reviewed — Reset Breaker</>
                    )}
                  </button>
                ) : (
                  <p className="text-xs text-gray-500 italic">
                    Only admins can reset the velocity breaker.
                  </p>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Budget Overview */}
        {budgetLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin text-neon-yellow" />
          </div>
        ) : globalBudget ? (
          <BudgetOverview data={globalBudget} />
        ) : (
          <div className="card mb-6 text-center py-8 text-gray-500">
            <BitcoinIcon size={48} />
            <p className="mt-2">No budget data available</p>
            <p className="text-xs mt-1">Set bitcoin_budget_sats on your campaign proposals to start tracking</p>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1 mb-4 bg-gray-900/50 p-1 rounded-lg w-fit">
          <button
            onClick={() => setActiveTab('approvals')}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'approvals'
                ? 'bg-neon-yellow/20 text-neon-yellow'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <Shield className="w-4 h-4" />
              Approvals
              {approvals.length > 0 && (
                <span className="bg-yellow-500/20 text-yellow-400 text-xs px-1.5 py-0.5 rounded-full">
                  {approvals.length}
                </span>
              )}
            </span>
          </button>
          <button
            onClick={() => setActiveTab('campaigns')}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'campaigns'
                ? 'bg-neon-yellow/20 text-neon-yellow'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <DollarSign className="w-4 h-4" />
              Campaigns
              {globalBudget && globalBudget.campaigns.length > 0 && (
                <span className="bg-gray-700/50 text-gray-400 text-xs px-1.5 py-0.5 rounded-full">
                  {globalBudget.campaigns.length}
                </span>
              )}
            </span>
          </button>
          <button
            onClick={() => setActiveTab('transactions')}
            className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${
              activeTab === 'transactions'
                ? 'bg-neon-cyan/20 text-neon-cyan'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            <span className="flex items-center gap-1.5">
              <Zap className="w-4 h-4" />
              Transactions
            </span>
          </button>
        </div>

        {/* Content */}
        {activeTab === 'approvals' && (
          <div className="space-y-3">
            {approvalsLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="w-6 h-6 animate-spin text-neon-yellow" />
              </div>
            ) : approvals.length === 0 ? (
              <div className="card text-center py-12 text-gray-500">
                <CheckCircle className="w-8 h-8 mx-auto mb-2 text-neon-green" />
                <p>No pending approvals</p>
                <p className="text-xs mt-1">All spend requests are within budget</p>
              </div>
            ) : (
              approvals.map((a) => (
                <ApprovalCard
                  key={a.id}
                  approval={a}
                  onApprove={handleApprove}
                  onReject={handleReject}
                  isPending={reviewMutation.isPending}
                />
              ))
            )}
          </div>
        )}

        {activeTab === 'campaigns' && globalBudget && (
          <CampaignBreakdown campaigns={globalBudget.campaigns} totalBudget={globalBudget.total_budget_sats} />
        )}

        {activeTab === 'transactions' && (
          txLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="w-6 h-6 animate-spin text-neon-cyan" />
            </div>
          ) : (
            <TransactionList transactions={transactions} />
          )
        )}
      </div>
    </Layout>
  );
}
