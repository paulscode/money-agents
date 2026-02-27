import apiClient from '@/lib/api-client';
import { API_V1_PREFIX } from '@/lib/config';

// Types

export interface WalletConfig {
  enabled: boolean;
  rest_url_configured: boolean;
  macaroon_configured: boolean;
  mempool_url: string;
  max_payment_sats: number;
}

export interface NodeInfo {
  alias: string;
  identity_pubkey: string;
  num_active_channels: number;
  num_inactive_channels: number;
  num_pending_channels: number;
  num_peers: number;
  block_height: number;
  synced_to_chain: boolean;
  synced_to_graph: boolean;
  version: string;
  commit_hash: string;
  uris: string[];
}

export interface OnchainBalance {
  total_balance: number;
  confirmed_balance: number;
  unconfirmed_balance: number;
  locked_balance: number;
  reserved_balance_anchor_chan: number;
}

export interface LightningBalance {
  local_balance_sat: number;
  remote_balance_sat: number;
  pending_open_local_sat: number;
  pending_open_remote_sat: number;
  unsettled_local_sat: number;
  unsettled_remote_sat: number;
}

export interface PendingChannels {
  pending_open_channels: number;
  pending_closing_channels: number;
  pending_force_closing_channels: number;
  waiting_close_channels: number;
  total_limbo_balance: number;
}

export interface WalletTotals {
  total_balance_sats: number;
  onchain_sats: number;
  lightning_local_sats: number;
  lightning_remote_sats: number;
  unconfirmed_sats: number;
  num_active_channels: number;
  num_pending_channels: number;
  synced: boolean;
}

export interface WalletSummary {
  connected: boolean;
  node_info: NodeInfo | null;
  onchain: OnchainBalance | null;
  lightning: LightningBalance | null;
  pending_channels: PendingChannels | null;
  totals: WalletTotals;
}

export interface Channel {
  chan_id: string;
  remote_pubkey: string;
  channel_point: string;
  capacity: number;
  local_balance: number;
  remote_balance: number;
  commit_fee: number;
  total_satoshis_sent: number;
  total_satoshis_received: number;
  num_updates: number;
  active: boolean;
  private: boolean;
  initiator: boolean;
  peer_alias: string;
  uptime: number;
  lifetime: number;
}

export interface Payment {
  payment_hash: string;
  value_sat: number;
  fee_sat: number;
  status: string;
  creation_date: number;
  payment_request: string;
  failure_reason: string;
}

export interface Invoice {
  memo: string;
  r_hash: string;
  value: number;
  settled: boolean;
  creation_date: number;
  settle_date: number;
  amt_paid_sat: number;
  state: string;
  is_keysend: boolean;
  payment_request: string;
}

export interface OnchainTransaction {
  tx_hash: string;
  amount: number;
  num_confirmations: number;
  block_height: number;
  time_stamp: number;
  total_fees: number;
  label: string;
}

export interface FeePriority {
  label: string;
  sat_per_vbyte: number;
}

export interface RecommendedFees {
  priorities: {
    low: FeePriority;
    medium: FeePriority;
    high: FeePriority;
  } | null;
  economy: number | null;
  minimum: number | null;
  raw: Record<string, number> | null;
  mempool_url: string;
  unavailable?: boolean;
  message?: string;
}

export interface NewAddressResponse {
  address: string;
  address_type: string;
}

export interface DecodedInvoice {
  destination: string;
  payment_hash: string;
  num_satoshis: number;
  timestamp: number;
  expiry: number;
  description: string;
  description_hash: string;
  cltv_expiry: number;
  num_msat: number;
  features: Record<string, unknown>;
}

export interface PaymentResult {
  payment_hash: string;
  payment_preimage: string;
  payment_route: {
    total_amt: number;
    total_fees: number;
    total_amt_msat: number;
    total_fees_msat: number;
    hops: number;
  } | null;
}

export interface CreatedInvoice {
  r_hash: string;
  payment_request: string;
  add_index: string;
}

export interface VelocityBreakerStatus {
  is_tripped: boolean;
  tripped_at: string | null;
  trip_context: {
    count: number;
    window_seconds: number;
    threshold: number;
    recent_tx_ids: string[];
  } | null;
  reset_at: string | null;
  reset_by_user_id: string | null;
  config: {
    max_txns: number;
    window_seconds: number;
  };
}

// Boltz Cold Storage Types

export interface BoltzFeeInfo {
  min_amount_sats: number;
  max_amount_sats: number;
  fee_percentage: number;
  miner_fee_lockup_sats: number;
  miner_fee_claim_sats: number;
  total_miner_fee_sats: number;
  tor_enabled: boolean;
  default_routing_fee_limit_percent: number;
}

export interface BoltzSwapStatus {
  id: string;
  boltz_swap_id: string;
  status: string;
  boltz_status: string | null;
  invoice_amount_sats: number;
  onchain_amount_sats: number | null;
  destination_address: string;
  fee_percentage: string | null;
  miner_fee_sats: number | null;
  boltz_invoice: string | null;
  claim_txid: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

// Channel Management Types

export interface OpenChannelRequest {
  node_address: string;
  local_funding_amount: number;
  sat_per_vbyte?: number;
}

export interface OpenChannelResult {
  funding_txid: string;
  output_index: number;
}

export interface PendingChannelDetail {
  type: 'pending_open' | 'pending_close' | 'force_closing';
  remote_node_pub: string;
  channel_point: string;
  capacity: number;
  local_balance: number;
  remote_balance: number;
  commit_fee?: number;
  confirmation_height?: number;
  closing_txid?: string;
  blocks_til_maturity?: number;
}

// API Service

const BASE = `${API_V1_PREFIX}/wallet`;

export const walletService = {
  async getConfig(): Promise<WalletConfig> {
    const response = await apiClient.get(`${BASE}/config`);
    return response.data;
  },

  async getSummary(): Promise<WalletSummary> {
    const response = await apiClient.get(`${BASE}/summary`);
    return response.data;
  },

  async getNodeInfo(): Promise<NodeInfo> {
    const response = await apiClient.get(`${BASE}/info`);
    return response.data;
  },

  async getBalance(): Promise<{ onchain: OnchainBalance; lightning: LightningBalance }> {
    const response = await apiClient.get(`${BASE}/balance`);
    return response.data;
  },

  async getChannels(): Promise<{ channels: Channel[] }> {
    const response = await apiClient.get(`${BASE}/channels`);
    return response.data;
  },

  async getPendingChannels(): Promise<PendingChannels> {
    const response = await apiClient.get(`${BASE}/channels/pending`);
    return response.data;
  },

  async getPayments(limit = 20): Promise<{ payments: Payment[] }> {
    const response = await apiClient.get(`${BASE}/payments`, { params: { limit } });
    return response.data;
  },

  async getInvoices(limit = 20): Promise<{ invoices: Invoice[] }> {
    const response = await apiClient.get(`${BASE}/invoices`, { params: { limit } });
    return response.data;
  },

  async getTransactions(limit = 20): Promise<{ transactions: OnchainTransaction[] }> {
    const response = await apiClient.get(`${BASE}/transactions`, { params: { limit } });
    return response.data;
  },

  async getFees(): Promise<RecommendedFees> {
    const response = await apiClient.get(`${BASE}/fees`);
    return response.data;
  },

  async getNewAddress(addressType: string = 'p2tr'): Promise<NewAddressResponse> {
    const response = await apiClient.post(`${BASE}/address/new`, {
      address_type: addressType,
    });
    return response.data;
  },

  async decodeInvoice(paymentRequest: string): Promise<DecodedInvoice> {
    const response = await apiClient.post(`${BASE}/decode`, {
      payment_request: paymentRequest,
    });
    return response.data;
  },

  async sendPayment(paymentRequest: string, feeLimitSats?: number, timeoutSeconds = 60): Promise<PaymentResult> {
    const response = await apiClient.post(`${BASE}/payments/send`, {
      payment_request: paymentRequest,
      fee_limit_sats: feeLimitSats ?? null,
      timeout_seconds: timeoutSeconds,
    });
    return response.data;
  },

  async createInvoice(amountSats: number, memo = '', expiry = 3600): Promise<CreatedInvoice> {
    const response = await apiClient.post(`${BASE}/invoices/create`, {
      amount_sats: amountSats,
      memo,
      expiry,
    });
    return response.data;
  },

  async getSafetyLimit(): Promise<{ max_payment_sats: number }> {
    const response = await apiClient.get(`${BASE}/safety-limit`);
    return response.data;
  },

  async updateSafetyLimit(maxPaymentSats: number): Promise<{ max_payment_sats: number }> {
    const response = await apiClient.put(`${BASE}/safety-limit`, {
      max_payment_sats: maxPaymentSats,
    });
    return response.data;
  },

  async getVelocityBreakerStatus(): Promise<VelocityBreakerStatus> {
    const response = await apiClient.get(`${BASE}/velocity-breaker`);
    return response.data;
  },

  async resetVelocityBreaker(): Promise<{ is_tripped: boolean; reset_at: string | null; reset_by_user_id: string | null }> {
    const response = await apiClient.post(`${BASE}/velocity-breaker/reset`);
    return response.data;
  },

  async sendOnchain(address: string, amountSats: number, satPerVbyte?: number, label = ''): Promise<{ txid: string }> {
    const response = await apiClient.post(`${BASE}/send`, {
      address,
      amount_sats: amountSats,
      sat_per_vbyte: satPerVbyte ?? null,
      label,
    });
    return response.data;
  },

  async estimateFee(address: string, amountSats: number, targetConf = 6): Promise<{ fee_sat: number; feerate_sat_per_byte: number; sat_per_vbyte: number }> {
    const response = await apiClient.get(`${BASE}/fee-estimate`, {
      params: { address, amount_sats: amountSats, target_conf: targetConf },
    });
    return response.data;
  },

  // Cold Storage — Lightning (Boltz)
  async getLightningColdStorageFees(): Promise<BoltzFeeInfo> {
    const response = await apiClient.get(`${BASE}/cold-storage/lightning/fees`);
    return response.data;
  },

  async initiateLightningColdStorage(amountSats: number, destinationAddress: string, routingFeeLimitPercent?: number): Promise<BoltzSwapStatus> {
    const response = await apiClient.post(`${BASE}/cold-storage/lightning`, {
      amount_sats: amountSats,
      destination_address: destinationAddress,
      routing_fee_limit_percent: routingFeeLimitPercent ?? 3.0,
    });
    return response.data;
  },

  async getLightningColdStorageStatus(swapId: string): Promise<BoltzSwapStatus> {
    const response = await apiClient.get(`${BASE}/cold-storage/lightning/${swapId}`);
    return response.data;
  },

  async cancelLightningColdStorage(swapId: string): Promise<BoltzSwapStatus> {
    const response = await apiClient.post(`${BASE}/cold-storage/lightning/${swapId}/cancel`);
    return response.data;
  },

  async listLightningColdStorageSwaps(limit = 20): Promise<{ swaps: BoltzSwapStatus[] }> {
    const response = await apiClient.get(`${BASE}/cold-storage/lightning/swaps`, { params: { limit } });
    return response.data;
  },

  // Channel Management
  async openChannel(req: OpenChannelRequest): Promise<OpenChannelResult> {
    const response = await apiClient.post(`${BASE}/channels/open`, req);
    return response.data;
  },

  async getPendingChannelsDetail(): Promise<{ pending_channels: PendingChannelDetail[] }> {
    const response = await apiClient.get(`${BASE}/channels/pending/detail`);
    return response.data;
  },
};
