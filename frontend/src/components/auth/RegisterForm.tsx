import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import { Zap, Mail, Lock, User, AlertCircle, CheckCircle } from 'lucide-react';
import { authService } from '@/services/auth';
import type { RegisterRequest } from '@/types';

export function RegisterForm() {
  const navigate = useNavigate();
  const [formData, setFormData] = useState<RegisterRequest>({
    email: '',
    username: '',
    password: '',
  });
  const [confirmPassword, setConfirmPassword] = useState('');

  const registerMutation = useMutation({
    mutationFn: authService.register,
    onSuccess: () => {
      // Redirect to login after successful registration
      setTimeout(() => navigate('/login'), 2000);
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    
    if (formData.password !== confirmPassword) {
      return;
    }

    registerMutation.mutate(formData);
  };

  const passwordsMatch = !confirmPassword || formData.password === confirmPassword;

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
          <p className="mt-2 text-gray-400">Create your account</p>
        </div>

        {/* Form */}
        <form className="card space-y-6" onSubmit={handleSubmit}>
          {registerMutation.isError && (
            <div className="bg-neon-pink/10 border border-neon-pink/30 rounded-lg p-4 flex items-start space-x-3">
              <AlertCircle className="h-5 w-5 text-neon-pink flex-shrink-0 mt-0.5" />
              <div className="text-sm text-neon-pink">
                Registration failed. Email or username may already be in use.
              </div>
            </div>
          )}

          {registerMutation.isSuccess && (
            <div className="bg-neon-green/10 border border-neon-green/30 rounded-lg p-4 flex items-start space-x-3">
              <CheckCircle className="h-5 w-5 text-neon-green flex-shrink-0 mt-0.5" />
              <div className="text-sm text-neon-green">
                Account created successfully! Redirecting to login...
              </div>
            </div>
          )}

          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-300 mb-2">
              Email
            </label>
            <div className="relative">
              <Mail className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
              <input
                id="email"
                type="email"
                required
                className="input pl-10 w-full"
                placeholder="you@example.com"
                value={formData.email}
                onChange={(e) => setFormData({ ...formData, email: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label htmlFor="username" className="block text-sm font-medium text-gray-300 mb-2">
              Username
            </label>
            <div className="relative">
              <User className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
              <input
                id="username"
                type="text"
                required
                minLength={3}
                className="input pl-10 w-full"
                placeholder="johndoe"
                value={formData.username}
                onChange={(e) => setFormData({ ...formData, username: e.target.value })}
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
                minLength={8}
                autoComplete="new-password"
                className="input pl-10 w-full"
                placeholder="••••••••"
                value={formData.password}
                onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              />
            </div>
          </div>

          <div>
            <label htmlFor="confirmPassword" className="block text-sm font-medium text-gray-300 mb-2">
              Confirm Password
            </label>
            <div className="relative">
              <Lock className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-500" />
              <input
                id="confirmPassword"
                type="password"
                required
                autoComplete="new-password"
                className={`input pl-10 w-full ${!passwordsMatch ? 'border-neon-pink focus:border-neon-pink focus:ring-neon-pink' : ''}`}
                placeholder="••••••••"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
            </div>
            {!passwordsMatch && (
              <p className="mt-1 text-sm text-neon-pink">Passwords do not match</p>
            )}
          </div>

          <button
            type="submit"
            disabled={registerMutation.isPending || !passwordsMatch || registerMutation.isSuccess}
            className="btn-primary w-full"
          >
            {registerMutation.isPending ? 'Creating account...' : 'Create account'}
          </button>

          <p className="text-center text-sm text-gray-400">
            Already have an account?{' '}
            <Link to="/login" className="text-neon-cyan hover:text-neon-blue transition-colors">
              Sign in here
            </Link>
          </p>
        </form>
      </div>
    </div>
  );
}
