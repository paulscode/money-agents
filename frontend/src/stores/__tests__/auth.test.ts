/**
 * Tests for the Zustand auth store.
 *
 * Covers:
 * - setAuth: sets user+token, persists to sessionStorage
 * - clearAuth: clears state and sessionStorage
 * - updateUser: updates user and persists minimized version
 * - minimizeUserForStorage: strips PII from stored user
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock disclaimer-state before importing auth store
vi.mock('@/lib/disclaimer-state', () => ({
  resetDisclaimerState: vi.fn(),
}));

import { useAuthStore } from '@/stores/auth';
import { resetDisclaimerState } from '@/lib/disclaimer-state';
import { STORAGE_KEYS } from '@/lib/config';

const mockUser = {
  id: 'user-123',
  username: 'testuser',
  email: 'test@example.com',
  role: 'user' as const,
  is_active: true,
  is_superuser: false,
  display_name: 'Test User',
  avatar_url: null,
  disclaimer_acknowledged_at: null,
  show_disclaimer_on_login: true,
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
};

describe('useAuthStore', () => {
  beforeEach(() => {
    // Reset store state
    useAuthStore.setState({ user: null, token: null, isAuthenticated: false });
    sessionStorage.clear();
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts unauthenticated', () => {
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });
  });

  describe('setAuth', () => {
    it('sets user and token in state', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');
      const state = useAuthStore.getState();

      expect(state.user).toEqual(mockUser);
      expect(state.token).toBe('jwt-token-123');
      expect(state.isAuthenticated).toBe(true);
    });

    it('persists token to sessionStorage', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');
      expect(sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)).toBe('jwt-token-123');
    });

    it('persists minimized user to sessionStorage', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');
      const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEYS.USER) || '{}');

      // Should include safe fields
      expect(stored.id).toBe('user-123');
      expect(stored.username).toBe('testuser');
      expect(stored.role).toBe('user');

      // Should NOT include PII (email, timestamps)
      expect(stored.email).toBeUndefined();
      expect(stored.created_at).toBeUndefined();
      expect(stored.updated_at).toBeUndefined();
    });
  });

  describe('clearAuth', () => {
    it('clears state', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');
      useAuthStore.getState().clearAuth();
      const state = useAuthStore.getState();

      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      expect(state.isAuthenticated).toBe(false);
    });

    it('removes token and user from sessionStorage', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');
      useAuthStore.getState().clearAuth();

      expect(sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)).toBeNull();
      expect(sessionStorage.getItem(STORAGE_KEYS.USER)).toBeNull();
    });

    it('calls resetDisclaimerState', () => {
      useAuthStore.getState().clearAuth();
      expect(resetDisclaimerState).toHaveBeenCalled();
    });
  });

  describe('updateUser', () => {
    it('updates user in state', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');

      const updated = { ...mockUser, display_name: 'Updated Name' };
      useAuthStore.getState().updateUser(updated as any);

      expect(useAuthStore.getState().user?.display_name).toBe('Updated Name');
    });

    it('persists minimized user to sessionStorage', () => {
      useAuthStore.getState().setAuth(mockUser as any, 'jwt-token-123');

      const updated = { ...mockUser, display_name: 'Updated Name' };
      useAuthStore.getState().updateUser(updated as any);

      const stored = JSON.parse(sessionStorage.getItem(STORAGE_KEYS.USER) || '{}');
      expect(stored.display_name).toBe('Updated Name');
      expect(stored.email).toBeUndefined();
    });
  });
});
