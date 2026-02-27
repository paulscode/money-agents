import apiClient from '../lib/api-client';
import type {
  Opportunity,
  OpportunityListResponse,
  OpportunityDecision,
  BulkDismissRequest,
  BulkDismissResponse,
  HopperStatus,
  UserScoutSettings,
  ScoutStatistics,
  OpportunityStatus,
  OpportunityType,
  RankingTier,
} from '../types/opportunity';

const BASE_PATH = '/api/v1/opportunities';

export interface ListOpportunitiesParams {
  status?: OpportunityStatus;
  tier?: RankingTier;
  opportunity_type?: OpportunityType;
  include_dismissed?: boolean;
  search?: string;
  skip?: number;
  limit?: number;
}

export interface PipelineStage {
  name: string;
  count: number;
  color: string;
}

export interface PipelineStats {
  stages: PipelineStage[];
  totals: {
    opportunities: number;
    proposals: number;
    campaigns: number;
  };
}

export const opportunitiesService = {
  /**
   * List opportunities with filtering and pagination
   */
  async listOpportunities(
    params?: ListOpportunitiesParams
  ): Promise<OpportunityListResponse> {
    const response = await apiClient.get(BASE_PATH, { params });
    return response.data;
  },

  /**
   * Get opportunities grouped by tier
   */
  async getOpportunitiesByTier(): Promise<Record<string, Opportunity[]>> {
    const response = await apiClient.get(`${BASE_PATH}/by-tier`);
    return response.data;
  },

  /**
   * Get hopper (proposal capacity) status
   */
  async getHopperStatus(): Promise<HopperStatus> {
    const response = await apiClient.get(`${BASE_PATH}/hopper`);
    return response.data;
  },

  /**
   * Get a single opportunity by ID
   */
  async getOpportunity(id: string): Promise<Opportunity> {
    const response = await apiClient.get(`${BASE_PATH}/${id}`);
    return response.data;
  },

  /**
   * Approve an opportunity to become a proposal
   */
  async approveOpportunity(
    id: string,
    decision?: OpportunityDecision
  ): Promise<Opportunity> {
    const response = await apiClient.post(
      `${BASE_PATH}/${id}/approve`,
      decision || {}
    );
    return response.data;
  },

  /**
   * Dismiss an opportunity
   */
  async dismissOpportunity(
    id: string,
    decision?: OpportunityDecision
  ): Promise<Opportunity> {
    const response = await apiClient.post(
      `${BASE_PATH}/${id}/dismiss`,
      decision || {}
    );
    return response.data;
  },

  /**
   * Request more research on an opportunity
   */
  async requestResearch(
    id: string,
    decision?: OpportunityDecision
  ): Promise<Opportunity> {
    const response = await apiClient.post(
      `${BASE_PATH}/${id}/research`,
      decision || {}
    );
    return response.data;
  },

  /**
   * Bulk dismiss multiple opportunities
   */
  async bulkDismiss(request: BulkDismissRequest): Promise<BulkDismissResponse> {
    const response = await apiClient.post(`${BASE_PATH}/bulk-dismiss`, request);
    return response.data;
  },

  /**
   * Get user's scout settings
   */
  async getSettings(): Promise<UserScoutSettings> {
    const response = await apiClient.get(`${BASE_PATH}/settings`);
    return response.data;
  },

  /**
   * Update user's scout settings
   */
  async updateSettings(
    settings: Partial<UserScoutSettings>
  ): Promise<UserScoutSettings> {
    const response = await apiClient.put(`${BASE_PATH}/settings`, settings);
    return response.data;
  },

  /**
   * Get scout statistics
   */
  async getStatistics(days: number = 30): Promise<ScoutStatistics> {
    const response = await apiClient.get(`${BASE_PATH}/statistics`, {
      params: { days },
    });
    return response.data;
  },

  /**
   * Get pipeline funnel statistics for dashboard
   */
  async getPipelineStats(): Promise<PipelineStats> {
    const response = await apiClient.get(`${BASE_PATH}/pipeline`);
    return response.data;
  },
};

export default opportunitiesService;
