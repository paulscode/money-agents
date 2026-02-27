import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { DisclaimerStatus, DisclaimerAcknowledgeResponse } from '@/types';

export const disclaimerService = {
  async getStatus(): Promise<DisclaimerStatus> {
    const response = await apiClient.get<DisclaimerStatus>(ENDPOINTS.DISCLAIMER.STATUS);
    return response.data;
  },

  async acknowledge(showOnLogin: boolean): Promise<DisclaimerAcknowledgeResponse> {
    const response = await apiClient.post<DisclaimerAcknowledgeResponse>(
      ENDPOINTS.DISCLAIMER.ACKNOWLEDGE,
      { show_on_login: showOnLogin }
    );
    return response.data;
  },
};
