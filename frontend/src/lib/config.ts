// API Configuration
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';
export const API_V1_PREFIX = '/api/v1';

// App Configuration
export const APP_NAME = 'Money Agents';
export const APP_VERSION = '1.0.0';

// Storage Keys
export const STORAGE_KEYS = {
  ACCESS_TOKEN: 'money_agents_token',
  USER: 'money_agents_user',
} as const;

// API Endpoints
export const ENDPOINTS = {
  AUTH: {
    REGISTER: `${API_V1_PREFIX}/auth/register`,
    LOGIN: `${API_V1_PREFIX}/auth/login`,
    RESET_PASSWORD: `${API_V1_PREFIX}/auth/reset-password`,
    PLATFORM: `${API_V1_PREFIX}/auth/platform`,
  },
  DISCLAIMER: {
    STATUS: `${API_V1_PREFIX}/disclaimer/status`,
    ACKNOWLEDGE: `${API_V1_PREFIX}/disclaimer/acknowledge`,
  },
  USERS: {
    ME: `${API_V1_PREFIX}/users/me`,
    BY_ID: (id: string) => `${API_V1_PREFIX}/users/${id}`,
  },
  ADMIN: {
    BASE: `${API_V1_PREFIX}/admin`,
  },
  PROPOSALS: {
    LIST: `${API_V1_PREFIX}/proposals/`,
    CREATE: `${API_V1_PREFIX}/proposals/`,
    BY_ID: (id: string) => `${API_V1_PREFIX}/proposals/${id}`,
  },
  CAMPAIGNS: {
    LIST: `${API_V1_PREFIX}/campaigns/`,
    CREATE: `${API_V1_PREFIX}/campaigns/`,
    BY_ID: (id: string) => `${API_V1_PREFIX}/campaigns/${id}`,
    // Multi-stream endpoints
    STREAMS: (id: string) => `${API_V1_PREFIX}/campaigns/${id}/streams`,
    INPUTS: (id: string) => `${API_V1_PREFIX}/campaigns/${id}/inputs`,
    INPUTS_BULK: (id: string) => `${API_V1_PREFIX}/campaigns/${id}/inputs/bulk`,
    TASKS: (id: string) => `${API_V1_PREFIX}/campaigns/${id}/tasks`,
    // Campaign Manager Agent endpoints
    INITIALIZE: `${API_V1_PREFIX}/agents/campaign-manager/initialize`,
    INPUT: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/input`,
    STATUS: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/status`,
    PAUSE: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/pause`,
    RESUME: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/resume`,
    TERMINATE: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/terminate`,
    STEP: (id: string) => `${API_V1_PREFIX}/agents/campaign-manager/${id}/step`,
    WS_STREAM: `${API_V1_PREFIX}/agents/campaign-manager/stream`,
  },
  CONVERSATIONS: {
    LIST: `${API_V1_PREFIX}/conversations/`,
    CREATE: `${API_V1_PREFIX}/conversations/`,
    BY_ID: (id: string) => `${API_V1_PREFIX}/conversations/${id}`,
    MESSAGES: (id: string) => `${API_V1_PREFIX}/conversations/${id}/messages`,
  },
} as const;
