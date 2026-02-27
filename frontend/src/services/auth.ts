import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { 
  LoginRequest, 
  RegisterRequest, 
  AuthResponse,
  User,
  ResetPasswordRequest,
} from '@/types';

export const authService = {
  async login(data: LoginRequest): Promise<AuthResponse> {
    const response = await apiClient.post<AuthResponse>(ENDPOINTS.AUTH.LOGIN, data);
    return response.data;
  },

  async register(data: RegisterRequest): Promise<User> {
    const response = await apiClient.post<User>(ENDPOINTS.AUTH.REGISTER, data);
    return response.data;
  },

  async getCurrentUser(): Promise<User> {
    const response = await apiClient.get<User>(ENDPOINTS.USERS.ME);
    return response.data;
  },

  async updateCurrentUser(data: Partial<User>): Promise<User> {
    const response = await apiClient.put<User>(ENDPOINTS.USERS.ME, data);
    return response.data;
  },

  async resetPassword(data: ResetPasswordRequest): Promise<{ message: string }> {
    const response = await apiClient.post<{ message: string }>(ENDPOINTS.AUTH.RESET_PASSWORD, data);
    return response.data;
  },
};
