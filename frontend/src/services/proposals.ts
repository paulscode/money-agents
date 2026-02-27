import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { Proposal, ProposalCreate, ProposalUpdate } from '@/types';

export const proposalsService = {
  async list(params?: { skip?: number; limit?: number; status?: string; has_campaign?: boolean }): Promise<Proposal[]> {
    const response = await apiClient.get<Proposal[]>(ENDPOINTS.PROPOSALS.LIST, { params });
    return response.data;
  },

  async create(data: ProposalCreate): Promise<Proposal> {
    const response = await apiClient.post<Proposal>(ENDPOINTS.PROPOSALS.CREATE, data);
    return response.data;
  },

  async getById(id: string): Promise<Proposal> {
    const response = await apiClient.get<Proposal>(ENDPOINTS.PROPOSALS.BY_ID(id));
    return response.data;
  },

  async update(id: string, data: ProposalUpdate): Promise<Proposal> {
    const response = await apiClient.put<Proposal>(ENDPOINTS.PROPOSALS.BY_ID(id), data);
    return response.data;
  },

  async delete(id: string): Promise<void> {
    await apiClient.delete(ENDPOINTS.PROPOSALS.BY_ID(id));
  },
};
