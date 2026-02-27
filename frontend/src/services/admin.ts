import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { User, ResetCodeResponse } from '@/types';

export interface UserApprovalRequest {
  role: 'user' | 'admin';
}

export const adminService = {
  async getAllUsers(): Promise<User[]> {
    const response = await apiClient.get<User[]>(`${ENDPOINTS.ADMIN.BASE}/users`);
    return response.data;
  },

  async getPendingUsers(): Promise<User[]> {
    const response = await apiClient.get<User[]>(`${ENDPOINTS.ADMIN.BASE}/users/pending`);
    return response.data;
  },

  async approveUser(userId: string, role: 'user' | 'admin'): Promise<User> {
    const response = await apiClient.post<User>(
      `${ENDPOINTS.ADMIN.BASE}/users/${userId}/approve`,
      { role }
    );
    return response.data;
  },

  async rejectUser(userId: string): Promise<void> {
    await apiClient.delete(`${ENDPOINTS.ADMIN.BASE}/users/${userId}`);
  },

  async deleteUser(userId: string): Promise<void> {
    await apiClient.delete(`${ENDPOINTS.ADMIN.BASE}/users/${userId}/delete`);
  },

  async updateUserRole(userId: string, role: 'user' | 'admin'): Promise<User> {
    const response = await apiClient.put<User>(
      `${ENDPOINTS.ADMIN.BASE}/users/${userId}/role`,
      { role }
    );
    return response.data;
  },

  async deactivateUser(userId: string): Promise<User> {
    const response = await apiClient.put<User>(
      `${ENDPOINTS.ADMIN.BASE}/users/${userId}/deactivate`
    );
    return response.data;
  },

  async reactivateUser(userId: string): Promise<User> {
    const response = await apiClient.put<User>(
      `${ENDPOINTS.ADMIN.BASE}/users/${userId}/reactivate`
    );
    return response.data;
  },

  async generateResetCode(userId: string): Promise<ResetCodeResponse> {
    const response = await apiClient.post<ResetCodeResponse>(
      `${ENDPOINTS.ADMIN.BASE}/users/${userId}/reset-code`
    );
    return response.data;
  },
};
