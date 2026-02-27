/**
 * Bitcoin Budget Service — API client for budget operations.
 *
 * Covers:
 * - Spend approval CRUD
 * - Budget summaries (per-campaign and global)
 * - Transaction history
 * - Spend Advisor WebSocket chat
 */
import apiClient from '@/lib/api-client';
import { API_V1_PREFIX } from '@/lib/config';

const BASE = `${API_V1_PREFIX}/bitcoin`;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SpendApproval {
  id: string;
  campaign_id: string | null;
  requested_by_id: string;
  trigger: 'no_budget' | 'over_budget' | 'global_limit' | 'manual_review' | 'velocity_breaker';
  status: 'pending' | 'approved' | 'rejected' | 'expired' | 'cancelled';
  amount_sats: number;
  fee_estimate_sats: number;
  payment_request: string | null;
  destination_address: string | null;
  description: string;
  budget_context: Record<string, unknown>;
  reviewed_by_id: string | null;
  reviewed_at: string | null;
  review_notes: string | null;
  advisor_conversation_id: string | null;
  created_at: string;
  expires_at: string | null;
}

export interface BitcoinTransaction {
  id: string;
  campaign_id: string | null;
  user_id: string;
  tx_type: 'lightning_send' | 'lightning_receive' | 'onchain_send' | 'onchain_receive';
  status: 'pending' | 'confirmed' | 'failed' | 'expired';
  amount_sats: number;
  fee_sats: number;
  payment_hash: string | null;
  payment_request: string | null;
  txid: string | null;
  address: string | null;
  description: string | null;
  agent_tool_execution_id: string | null;
  approval_id: string | null;
  created_at: string;
  confirmed_at: string | null;
}

export interface CampaignBitcoinBudget {
  campaign_id: string;
  campaign_title: string | null;
  campaign_status: string | null;
  bitcoin_budget_sats: number | null;
  bitcoin_spent_sats: number;
  bitcoin_received_sats: number;
  bitcoin_remaining_sats: number | null;
  pending_approvals: number;
  recent_transactions: BitcoinTransaction[];
}

export interface GlobalBitcoinBudget {
  total_budget_sats: number;
  total_spent_sats: number;
  total_received_sats: number;
  total_remaining_sats: number;
  total_pending_sats: number;
  global_limit_sats: number;
  campaigns_with_budget: number;
  campaigns_over_budget: number;
  campaigns_near_budget: number;
  pending_approvals: number;
  wallet_balance_sats: number | null;
  campaigns: CampaignBitcoinBudget[];
}

export interface SpendAnalysis {
  approval_id: string;
  analysis: string;
  model_tier: string;
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export const budgetService = {
  // ── Approvals ──────────────────────────────────────────────────────
  async getPendingApprovals(campaignId?: string, limit = 50) {
    const params: Record<string, string> = { limit: String(limit) };
    if (campaignId) params.campaign_id = campaignId;
    const response = await apiClient.get(`${BASE}/approvals`, { params });
    return response.data as { approvals: SpendApproval[]; total: number };
  },

  async getPendingCount(campaignId?: string) {
    const params: Record<string, string> = {};
    if (campaignId) params.campaign_id = campaignId;
    const response = await apiClient.get(`${BASE}/approvals/count`, { params });
    return response.data as { pending_count: number };
  },

  async getApproval(approvalId: string) {
    const response = await apiClient.get(`${BASE}/approvals/${approvalId}`);
    return response.data as SpendApproval;
  },

  async reviewApproval(approvalId: string, action: 'approved' | 'rejected', reviewNotes?: string) {
    const response = await apiClient.post(`${BASE}/approvals/${approvalId}/review`, {
      action,
      review_notes: reviewNotes,
    });
    return response.data as SpendApproval;
  },

  async cancelApproval(approvalId: string) {
    const response = await apiClient.post(`${BASE}/approvals/${approvalId}/cancel`);
    return response.data as SpendApproval;
  },

  // ── Budget ─────────────────────────────────────────────────────────
  async getGlobalBudget() {
    const response = await apiClient.get(`${BASE}/budget/global`);
    return response.data as GlobalBitcoinBudget;
  },

  async getCampaignBudget(campaignId: string) {
    const response = await apiClient.get(`${BASE}/budget/campaign/${campaignId}`);
    return response.data as CampaignBitcoinBudget;
  },

  // ── Transactions ───────────────────────────────────────────────────
  async getTransactions(campaignId?: string, limit = 50, offset = 0) {
    const params: Record<string, string> = {
      limit: String(limit),
      offset: String(offset),
    };
    if (campaignId) params.campaign_id = campaignId;
    const response = await apiClient.get(`${BASE}/transactions`, { params });
    return response.data as { transactions: BitcoinTransaction[]; total: number };
  },

  // ── Spend Advisor ──────────────────────────────────────────────────
  async getSpendAnalysis(approvalId: string) {
    const response = await apiClient.get(`${BASE}/approvals/${approvalId}/analysis`);
    return response.data as SpendAnalysis;
  },
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

export function formatSats(sats: number): string {
  if (sats >= 100_000_000) {
    return `${(sats / 100_000_000).toFixed(8)} BTC`;
  }
  return `${sats.toLocaleString()} sats`;
}

export function triggerLabel(trigger: SpendApproval['trigger']): string {
  switch (trigger) {
    case 'no_budget': return 'No Budget Set';
    case 'over_budget': return 'Over Budget';
    case 'global_limit': return 'Exceeds Global Limit';
    case 'manual_review': return 'Manual Review';
    case 'velocity_breaker': return 'Velocity Limit Exceeded';
    default: return trigger;
  }
}

export function txTypeLabel(type: BitcoinTransaction['tx_type']): string {
  switch (type) {
    case 'lightning_send': return '⚡ Lightning Send';
    case 'lightning_receive': return '⚡ Lightning Receive';
    case 'onchain_send': return '🔗 On-chain Send';
    case 'onchain_receive': return '🔗 On-chain Receive';
    default: return type;
  }
}
