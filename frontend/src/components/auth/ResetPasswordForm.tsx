import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Zap, KeyRound, Lock, AlertCircle, CheckCircle, Info } from 'lucide-react';
import { authService } from '@/services/auth';
import apiClient from '@/lib/api-client';
import { ENDPOINTS } from '@/lib/config';
import type { ResetPasswordRequest } from '@/types';

function getStartCommand(hostOs: string): string {
  switch (hostOs) {
    case 'windows': return './start.ps1';
    case 'macos':
    case 'linux':
    default:
      return './start.sh';
  }
}

export function ResetPasswordForm() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState<ResetPasswordRequest>({
    code: '',
    new_password: '',
  });
  const [confirmPassword, setConfirmPassword] = useState('');

  const { data: platformData } = useQuery({
    queryKey: ['platform'],
    queryFn: async () => {
      const res = await apiClient.get<{ host_os: string }>(ENDPOINTS.AUTH.PLATFORM);
      return res.data;
    },
    staleTime: Infinity,
  });
  const startCmd = getStartCommand(platformData?.host_os ?? 'linux');

  const resetMutation = useMutation({
    mutationFn: (data: ResetPasswordRequest) => authService.resetPassword(data),
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (formData.new_password !== confirmPassword) {
      return;
    }
    resetMutation.mutate(formData);
  };

  const passwordsMatch = confirmPassword === '' || formData.new_password === confirmPassword;

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
          <p className="mt-2 text-gray-400">Reset your password</p>
        </div>

        {/* Success state */}
        {resetMutation.isSuccess ? (
          <div className="card space-y-6">
            <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4 flex items-start space-x-3">
              <CheckCircle className="h-5 w-5 text-green-400 flex-shrink-0 mt-0.5" />
              <div className="text-sm text-green-300">
                Your password has been reset successfully. You can now sign in with your new password.
              </div>
            </div>
            <button
              onClick={() => navigate('/login')}
              className="btn-primary w-full"
            >
              Go to Sign In
            </button>
          </div>
        ) : (
          /* Form */
          <form className="card space-y-6" onSubmit={handleSubmit}>
            {/* Info banner */}
            <div className="bg-neon-cyan/5 border border-neon-cyan/20 rounded-lg p-4 flex items-start space-x-3">
              <Info className="h-5 w-5 text-neon-cyan flex-shrink-0 mt-0.5" />
              <div className="text-sm text-gray-300">
                <p>Enter the reset code provided by your administrator along with your new password.</p>
                <p className="mt-2 text-gray-500">
                  If you are the administrator, run <code className="text-gray-400 bg-navy-900 px-1.5 py-0.5 rounded text-xs">{startCmd}</code> on the host machine to reset your password.
                </p>
              </div>
            </div>

            {resetMutation.isError && (
              <div className="bg-neon-pink/10 border border-neon-pink/30 rounded-lg p-4 flex items-start space-x-3">
                <AlertCircle className="h-5 w-5 text-neon-pink flex-shrink-0 mt-0.5" />
                <div className="text-sm text-neon-pink">
                  {(resetMutation.error as any)?.response?.data?.detail ||
                    'Failed to reset password. The code may be invalid or expired.'}
                </div>
              </div>
            )}

            <div>
              <label htmlFor="code" className="block text-sm font-medium text-gray-300 mb-2">
                Reset Code
              </label>
              <div className="relative">
                <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
                <input
                  id="code"
                  type="text"
                  required
                  className="input pl-10 w-full uppercase tracking-widest font-mono"
                  placeholder="Enter code"
                  value={formData.code}
                  onChange={(e) => setFormData({ ...formData, code: e.target.value.trim() })}
                  autoComplete="off"
                />
              </div>
            </div>

            <div>
              <label htmlFor="new_password" className="block text-sm font-medium text-gray-300 mb-2">
                New Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
                <input
                  id="new_password"
                  type="password"
                  required
                  minLength={8}
                  className="input pl-10 w-full"
                  placeholder="••••••••"
                  value={formData.new_password}
                  onChange={(e) => setFormData({ ...formData, new_password: e.target.value })}
                />
              </div>
              <p className="mt-1.5 text-xs text-gray-500">
                Must be 8+ characters with uppercase, lowercase, digit, and special character.
              </p>
            </div>

            <div>
              <label htmlFor="confirm_password" className="block text-sm font-medium text-gray-300 mb-2">
                Confirm Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
                <input
                  id="confirm_password"
                  type="password"
                  required
                  minLength={8}
                  className={`input pl-10 w-full ${!passwordsMatch ? 'border-neon-pink/50 focus:border-neon-pink' : ''}`}
                  placeholder="••••••••"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </div>
              {!passwordsMatch && (
                <p className="mt-1.5 text-xs text-neon-pink">Passwords do not match.</p>
              )}
            </div>

            <button
              type="submit"
              disabled={resetMutation.isPending || !passwordsMatch || !formData.code || !formData.new_password}
              className="btn-primary w-full"
            >
              {resetMutation.isPending ? 'Resetting...' : 'Reset Password'}
            </button>

            <p className="text-center text-sm text-gray-400">
              Remember your password?{' '}
              <Link to="/login" className="text-neon-cyan hover:text-neon-blue transition-colors">
                Sign in
              </Link>
            </p>
          </form>
        )}
      </div>
    </div>
  );
}
