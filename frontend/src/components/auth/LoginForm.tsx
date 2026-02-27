import { useState, useRef, useCallback } from 'react';
import { useNavigate, Link, useSearchParams } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { Zap, User, Lock, AlertCircle, Clock } from 'lucide-react';
import { authService } from '@/services/auth';
import { useAuthStore } from '@/stores/auth';
import { STORAGE_KEYS } from '@/lib/config';
import type { LoginRequest } from '@/types';

/** Exponential backoff after failed login attempts to slow brute-forcing. */
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30_000;

export function LoginForm() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const sessionExpired = searchParams.get('reason') === 'session_expired';
  const { setAuth } = useAuthStore();
  const [formData, setFormData] = useState<LoginRequest>({
    identifier: '',
    password: '',
  });
  const failCountRef = useRef(0);
  const [throttledUntil, setThrottledUntil] = useState<number>(0);

  const isThrottled = useCallback(() => Date.now() < throttledUntil, [throttledUntil]);

  const loginMutation = useMutation({
    mutationFn: async (data: LoginRequest) => {
      if (isThrottled()) {
        const secs = Math.ceil((throttledUntil - Date.now()) / 1000);
        throw new Error(`Too many attempts. Please wait ${secs}s before trying again.`);
      }
      const authResponse = await authService.login(data);
      // Save token immediately so it's available for the next request
      sessionStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, authResponse.access_token);
      const user = await authService.getCurrentUser();
      return { authResponse, user };
    },
    onSuccess: ({ authResponse, user }) => {
      failCountRef.current = 0;
      setThrottledUntil(0);
      setAuth(user, authResponse.access_token);
      navigate('/dashboard');
    },
    onError: () => {
      failCountRef.current += 1;
      const delay = Math.min(BASE_DELAY_MS * Math.pow(2, failCountRef.current - 1), MAX_DELAY_MS);
      setThrottledUntil(Date.now() + delay);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    loginMutation.mutate(formData);
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-navy-950 px-4">
      <div className="max-w-md w-full space-y-8">
        {/* Logo */}
        <div className="text-center">
          <div className="inline-flex items-center justify-center space-x-3">
            <div className="relative">
              <Zap className="h-12 w-12 text-neon-cyan" />
              <div className="absolute inset-0 blur-lg bg-neon-cyan/30 animate-pulse-slow"></div>
            </div>
            <h1 className="text-4xl font-bold text-neon-cyan">Money Agents</h1>
          </div>
          <p className="mt-2 text-gray-400">Sign in to your account</p>
        </div>

        {/* Form */}
        <form className="card space-y-6" onSubmit={handleSubmit}>
          {sessionExpired && !loginMutation.isError && (
            <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-lg p-4 flex items-start space-x-3">
              <Clock className="h-5 w-5 text-yellow-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-yellow-300">
                Your session has expired. Please sign in again.
              </div>
            </div>
          )}
          {loginMutation.isError && (
            <div className="bg-neon-pink/10 border border-neon-pink/30 rounded-lg p-4 flex items-start space-x-3">
              <AlertCircle className="h-5 w-5 text-neon-pink flex-shrink-0 mt-0.5" />
              <div className="text-sm text-neon-pink">
                Invalid credentials. Please try again.
              </div>
            </div>
          )}

          <div>
            <label htmlFor="identifier" className="block text-sm font-medium text-gray-300 mb-2">
              Username
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
              <input
                id="identifier"
                type="text"
                required
                className="input pl-10 w-full"
                placeholder="username or email"
                value={formData.identifier}
                onChange={(e) => setFormData({ ...formData, identifier: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-300 mb-2">
              Password
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
              <input
                id="password"
                type="password"
                required
                autoComplete="current-password"
                className="input pl-10 w-full"
                placeholder="••••••••"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loginMutation.isPending}
            className="btn-primary w-full"
          >
            {loginMutation.isPending ? 'Signing in...' : 'Sign in'}
          </button>

          <p className="text-center text-sm text-gray-400">
            Don't have an account?{' '}
            <Link to="/register" className="text-neon-cyan hover:text-neon-blue transition-colors">
              Register here
            </Link>
          </p>
          <p className="text-center text-sm text-gray-400">
            <Link to="/reset-password" className="text-gray-500 hover:text-gray-300 transition-colors">
              Forgot your password?
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
