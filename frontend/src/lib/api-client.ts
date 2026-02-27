import axios from 'axios';
import type { AxiosInstance, AxiosError } from 'axios';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';

// Create axios instance
const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

const isDev = import.meta.env.DEV;

if (isDev) console.log('API Client initialized with baseURL:', API_BASE_URL);

// Request interceptor to add auth token
apiClient.interceptors.request.use(
  (config) => {
    const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    if (isDev) console.log('API Request:', config.method?.toUpperCase(), config.baseURL + config.url);
    return config;
  },
  (error) => {
    if (isDev) console.error('Request interceptor error:', error);
    return Promise.reject(error);
  }
);

// Response interceptor for error handling
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status;
    const detail = (error.response?.data as { detail?: string })?.detail ?? '';

    // GAP-8: Only log errors in development builds — console.error in production
    // can leak URL paths, error codes, and response data to the browser console.
    if (isDev) {
      console.error('API Error:', {
        message: error.message,
        status,
        data: error.response?.data,
        config: {
          url: error.config?.url,
          method: error.config?.method,
        }
      });
    }

    if (status === 401) {
      // Expired or invalid token — redirect to login
      sessionStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);
      sessionStorage.removeItem(STORAGE_KEYS.USER);
      window.location.href = '/login?reason=session_expired';
    }

    // 403 with account-level messages means the session is unusable.
    // Legitimate permission errors (e.g. "Admin privileges required") are
    // left alone so components can handle them.
    if (status === 403) {
      const sessionDead = [
        'Account is inactive',
        'Inactive user',
        'Not authenticated',
        'Could not validate credentials',
      ].some((msg) => detail.includes(msg));

      if (sessionDead) {
        sessionStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);
        sessionStorage.removeItem(STORAGE_KEYS.USER);
        window.location.href = '/login?reason=session_expired';
      }
    }

    return Promise.reject(error);
  }
);

export default apiClient;
