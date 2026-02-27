import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { 
  Campaign, 
  CampaignCreate, 
  CampaignStreamsResponse, 
  CampaignInputsResponse,
  InputProvideRequest,
  BulkInputProvideRequest
} from '@/types';

export interface CampaignActionResponse {
  success: boolean;
  message: string;
  data?: Record<string, any>;
  tokens_used?: number;
  model_used?: string;
}

export interface CampaignInitRequest {
  proposal_id: string;
}

export interface TaskTimelineData {
  id: string;
  name: string;
  stream_name: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export const campaignsService = {
  // Basic CRUD operations
  async list(params?: { skip?: number; limit?: number; status?: string }): Promise<Campaign[]> {
    const response = await apiClient.get<Campaign[]>(ENDPOINTS.CAMPAIGNS.LIST, { params });
    return response.data;
  },

  async create(data: CampaignCreate): Promise<Campaign> {
    const response = await apiClient.post<Campaign>(ENDPOINTS.CAMPAIGNS.CREATE, data);
    return response.data;
  },

  async getById(id: string): Promise<Campaign> {
    const response = await apiClient.get<Campaign>(ENDPOINTS.CAMPAIGNS.BY_ID(id));
    return response.data;
  },

  async update(id: string, data: Partial<Campaign>): Promise<Campaign> {
    const response = await apiClient.put<Campaign>(ENDPOINTS.CAMPAIGNS.BY_ID(id), data);
    return response.data;
  },

  async delete(id: string): Promise<void> {
    await apiClient.delete(ENDPOINTS.CAMPAIGNS.BY_ID(id));
  },

  // Multi-stream operations
  async getStreams(campaignId: string): Promise<CampaignStreamsResponse> {
    const response = await apiClient.get<CampaignStreamsResponse>(
      ENDPOINTS.CAMPAIGNS.STREAMS(campaignId)
    );
    return response.data;
  },

  async getInputs(campaignId: string): Promise<CampaignInputsResponse> {
    const response = await apiClient.get<CampaignInputsResponse>(
      ENDPOINTS.CAMPAIGNS.INPUTS(campaignId)
    );
    return response.data;
  },

  async getTasks(campaignId: string, statusFilter?: string): Promise<TaskTimelineData[]> {
    const response = await apiClient.get<TaskTimelineData[]>(
      ENDPOINTS.CAMPAIGNS.TASKS(campaignId),
      { params: statusFilter ? { status_filter: statusFilter } : undefined }
    );
    return response.data;
  },

  async provideInput(campaignId: string, input: InputProvideRequest): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.INPUTS(campaignId),
      input
    );
    return response.data;
  },

  async provideInputsBulk(campaignId: string, data: BulkInputProvideRequest): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.INPUTS_BULK(campaignId),
      data
    );
    return response.data;
  },

  // Campaign Manager Agent operations
  async initialize(proposalId: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.INITIALIZE,
      { proposal_id: proposalId }
    );
    return response.data;
  },

  async submitInput(campaignId: string, message: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.INPUT(campaignId),
      { message }
    );
    return response.data;
  },

  async getStatus(campaignId: string): Promise<CampaignActionResponse> {
    const response = await apiClient.get<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.STATUS(campaignId)
    );
    return response.data;
  },

  async pause(campaignId: string, reason?: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.PAUSE(campaignId),
      undefined,
      { params: reason ? { reason } : undefined }
    );
    return response.data;
  },

  async resume(campaignId: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.RESUME(campaignId)
    );
    return response.data;
  },

  async terminate(campaignId: string, reason: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.TERMINATE(campaignId),
      { reason }
    );
    return response.data;
  },

  async executeStep(campaignId: string): Promise<CampaignActionResponse> {
    const response = await apiClient.post<CampaignActionResponse>(
      ENDPOINTS.CAMPAIGNS.STEP(campaignId)
    );
    return response.data;
  },
};
