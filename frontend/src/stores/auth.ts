import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { User } from '@/types';
import { STORAGE_KEYS } from '@/lib/config';
import { resetDisclaimerState } from '@/lib/disclaimer-state';

/**
 * Subset of User stored in localStorage to minimize PII exposure.
 * Full user object is kept in memory only (zustand state).
 */
function minimizeUserForStorage(user: User): Partial<User> {
  return {
    id: user.id,
    username: user.username,
    role: user.role,
    is_active: user.is_active,
    is_superuser: user.is_superuser,
    display_name: user.display_name,
    avatar_url: user.avatar_url,
    disclaimer_acknowledged_at: user.disclaimer_acknowledged_at,
    show_disclaimer_on_login: user.show_disclaimer_on_login,
  };
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  setAuth: (user: User, token: string) => void;
  clearAuth: () => void;
  updateUser: (user: User) => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isAuthenticated: false,
      
      setAuth: (user, token) => {
        sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, token);
        sessionStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(minimizeUserForStorage(user)));
        set({ user, token, isAuthenticated: true });
      },
      
      clearAuth: () => {
        sessionStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN);
        sessionStorage.removeItem(STORAGE_KEYS.USER);
        resetDisclaimerState();
        set({ user: null, token: null, isAuthenticated: false });
      },
      
      updateUser: (user) => {
        sessionStorage.setItem(STORAGE_KEYS.USER, JSON.stringify(minimizeUserForStorage(user)));
        set({ user });
      },
    }),
    {
      name: 'auth-storage',
      storage: {
        getItem: (name) => {
          const str = sessionStorage.getItem(name);
          return str ? JSON.parse(str) : null;
        },
        setItem: (name, value) => {
          sessionStorage.setItem(name, JSON.stringify(value));
        },
        removeItem: (name) => {
          sessionStorage.removeItem(name);
        },
      },
    }
  )
);
