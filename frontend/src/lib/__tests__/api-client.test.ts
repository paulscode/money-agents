/**
 * Tests for the API client (axios instance with interceptors).
 *
 * Covers:
 * - Request interceptor: attaches Bearer token from sessionStorage
 * - Response interceptor: 401 → clears storage + redirects
 * - Response interceptor: 403 session-dead detection
 * - Normal errors pass through
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import type { InternalAxiosRequestConfig } from 'axios';

// vi.hoisted() ensures the handlers object is available when the hoisted vi.mock() runs
const mockHandlers = vi.hoisted(() => ({
  requestFulfilled: null as any,
  responseRejected: null as any,
}));

vi.mock('axios', () => ({
  default: {
    create: vi.fn(() => ({
      interceptors: {
        request: {
          use: vi.fn((fulfilled: any) => {
            mockHandlers.requestFulfilled = fulfilled;
            return 0;
          }),
        },
        response: {
          use: vi.fn((_fulfilled: any, rejected: any) => {
            mockHandlers.responseRejected = rejected;
            return 0;
          }),
        },
      },
      defaults: { headers: { common: {} } },
    })),
  },
}));

// Must import after mocking axios – triggers interceptor registration
import { STORAGE_KEYS } from '@/lib/config';
import '@/lib/api-client';

describe('api-client interceptors', () => {
  const originalLocation = window.location;

  beforeEach(() => {
    sessionStorage.clear();
    // Mock window.location.href setter
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '', protocol: 'http:', host: 'localhost' },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: originalLocation,
    });
  });

  describe('request interceptor', () => {
    it('attaches Bearer token when present', () => {
      sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, 'my-jwt-token');

      const config = {
        headers: {} as any,
        method: 'get',
        url: '/test',
        baseURL: 'http://localhost:8000',
      } as InternalAxiosRequestConfig;

      const result = mockHandlers.requestFulfilled(config);
      expect(result.headers.Authorization).toBe('Bearer my-jwt-token');
    });

    it('does not set Authorization when no token', () => {
      const config = {
        headers: {} as any,
        method: 'get',
        url: '/test',
        baseURL: 'http://localhost:8000',
      } as InternalAxiosRequestConfig;

      const result = mockHandlers.requestFulfilled(config);
      expect(result.headers.Authorization).toBeUndefined();
    });
  });

  describe('response interceptor – 401', () => {
    it('clears storage and redirects on 401', async () => {
      sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, 'old-token');
      sessionStorage.setItem(STORAGE_KEYS.USER, '{"id":"x"}');

      const error = {
        response: { status: 401, data: {} },
        message: 'Unauthorized',
        config: { url: '/api/test', method: 'get', baseURL: 'http://localhost:8000' },
      };

      await expect(mockHandlers.responseRejected(error)).rejects.toBeDefined();

      expect(sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)).toBeNull();
      expect(sessionStorage.getItem(STORAGE_KEYS.USER)).toBeNull();
      expect(window.location.href).toContain('/login?reason=session_expired');
    });
  });

  describe('response interceptor – 403 session dead', () => {
    const sessionDeadMessages = [
      'Account is inactive',
      'Inactive user',
      'Not authenticated',
      'Could not validate credentials',
    ];

    for (const msg of sessionDeadMessages) {
      it(`redirects on 403 with "${msg}"`, async () => {
        sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, 'token');

        const error = {
          response: { status: 403, data: { detail: msg } },
          message: 'Forbidden',
          config: { url: '/api/test', method: 'get', baseURL: 'http://localhost:8000' },
        };

        await expect(mockHandlers.responseRejected(error)).rejects.toBeDefined();

        expect(sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)).toBeNull();
        expect(window.location.href).toContain('/login');
      });
    }

    it('does NOT redirect on 403 with permission message', async () => {
      sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, 'token');

      const error = {
        response: { status: 403, data: { detail: 'Admin privileges required' } },
        message: 'Forbidden',
        config: { url: '/api/admin/users', method: 'get', baseURL: 'http://localhost:8000' },
      };

      await expect(mockHandlers.responseRejected(error)).rejects.toBeDefined();

      // Token should NOT be cleared for this type of 403
      expect(sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)).toBe('token');
    });
  });

  describe('response interceptor – other errors', () => {
    it('rejects with the error for non-auth errors', async () => {
      const error = {
        response: { status: 500, data: { detail: 'Internal error' } },
        message: 'Server error',
        config: { url: '/api/test', method: 'get', baseURL: 'http://localhost:8000' },
      };

      await expect(mockHandlers.responseRejected(error)).rejects.toBeDefined();
    });
  });
});
