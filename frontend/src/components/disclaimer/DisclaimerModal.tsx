import { useState, useCallback } from 'react';
import { useMutation } from '@tanstack/react-query';
import { AlertTriangle, ShieldAlert, CheckCircle, LogOut } from 'lucide-react';
import { disclaimerService } from '@/services/disclaimer';
import { useAuthStore } from '@/stores/auth';
import { authService } from '@/services/auth';
import type { DisclaimerStatus } from '@/types';

interface DisclaimerModalProps {
  status: DisclaimerStatus;
  onAcknowledged: () => void;
}

export function DisclaimerModal({ status, onAcknowledged }: DisclaimerModalProps) {
  const { clearAuth, updateUser } = useAuthStore();
  const [showOnLogin, setShowOnLogin] = useState(true);
  const [hasScrolledToBottom, setHasScrolledToBottom] = useState(false);

  // Ref callback: when the scrollable container mounts, check if the content
  // fits without a scrollbar. If so, treat it as "already scrolled to bottom".
  const scrollRef = useCallback((node: HTMLDivElement | null) => {
    if (node && node.scrollHeight <= node.clientHeight + 5) {
      setHasScrolledToBottom(true);
    }
  }, []);

  const acknowledgeMutation = useMutation({
    mutationFn: () => disclaimerService.acknowledge(showOnLogin),
    onSuccess: async () => {
      // Refresh the user to get updated disclaimer fields
      try {
        const updatedUser = await authService.getCurrentUser();
        updateUser(updatedUser);
      } catch {
        // If refresh fails, still proceed — the acknowledgement was recorded
      }
      onAcknowledged();
    },
  });

  const handleDismiss = () => {
    // Dismissing = logout
    clearAuth();
    window.location.href = '/login';
  };

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const target = e.currentTarget;
    const isAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 30;
    if (isAtBottom) {
      setHasScrolledToBottom(true);
    }
  };

  const isInitialAdmin = status.is_initial_admin;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4">
      <div className="bg-navy-900 border border-navy-700 rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center gap-3 p-6 border-b border-navy-700 flex-shrink-0">
          <div className="p-2 rounded-lg bg-amber-500/10">
            <ShieldAlert className="h-6 w-6 text-amber-400" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white">
              {isInitialAdmin ? 'Risk Acknowledgement Required' : 'Risk Disclaimer'}
            </h2>
            <p className="text-sm text-gray-400 mt-0.5">
              {isInitialAdmin
                ? 'You must acknowledge this disclaimer before agents can operate'
                : 'Please review the following disclaimer'}
            </p>
          </div>
        </div>

        {/* Initial Admin Banner */}
        {isInitialAdmin && (
          <div className="mx-6 mt-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 flex items-start gap-3 flex-shrink-0">
            <AlertTriangle className="h-5 w-5 text-amber-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-amber-300">
              <strong>All agents are currently disabled.</strong> They will remain disabled
              until you acknowledge this disclaimer. No automated actions that could incur
              costs will be performed until you proceed.
            </div>
          </div>
        )}

        {/* Disclaimer Text */}
        <div
          ref={scrollRef}
          className="p-6 overflow-y-auto flex-1 min-h-0"
          onScroll={handleScroll}
        >
          <div className="prose prose-invert prose-sm max-w-none">
            {status.disclaimer_text.split('\n\n').map((paragraph, i) => (
              <p key={i} className="text-gray-300 leading-relaxed mb-4 last:mb-0">
                {paragraph}
              </p>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-navy-700 p-6 flex-shrink-0 space-y-4">
          {/* Show on login checkbox */}
          <label className="flex items-center gap-3 cursor-pointer group">
            <input
              type="checkbox"
              checked={showOnLogin}
              onChange={(e) => setShowOnLogin(e.target.checked)}
              className="h-4 w-4 rounded border-navy-600 bg-navy-800 text-neon-cyan focus:ring-neon-cyan/50 focus:ring-offset-0 cursor-pointer"
            />
            <span className="text-sm text-gray-400 group-hover:text-gray-300 transition-colors">
              Show this message on startup
            </span>
          </label>

          {/* Action buttons */}
          <div className="flex items-center justify-between">
            <button
              onClick={handleDismiss}
              className="flex items-center gap-2 px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
            >
              <LogOut className="h-4 w-4" />
              {isInitialAdmin ? 'Cancel & Sign Out' : 'Sign Out'}
            </button>

            <button
              onClick={() => acknowledgeMutation.mutate()}
              disabled={acknowledgeMutation.isPending || (!hasScrolledToBottom && !status.acknowledged_at)}
              className="flex items-center gap-2 px-6 py-2.5 rounded-lg font-medium text-sm transition-all
                bg-neon-cyan/10 text-neon-cyan border border-neon-cyan/30
                hover:bg-neon-cyan/20 hover:border-neon-cyan/50
                disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-neon-cyan/10"
              title={
                !hasScrolledToBottom && !status.acknowledged_at
                  ? 'Please scroll to the bottom of the disclaimer to continue'
                  : undefined
              }
            >
              {acknowledgeMutation.isPending ? (
                <>
                  <div className="animate-spin h-4 w-4 border-2 border-neon-cyan border-t-transparent rounded-full" />
                  Processing...
                </>
              ) : (
                <>
                  <CheckCircle className="h-4 w-4" />
                  {isInitialAdmin ? 'I Acknowledge — Enable Agents' : 'I Acknowledge'}
                </>
              )}
            </button>
          </div>

          {!hasScrolledToBottom && !status.acknowledged_at && (
            <p className="text-xs text-gray-500 text-center">
              Please scroll to the bottom of the disclaimer to enable the acknowledge button
            </p>
          )}

          {acknowledgeMutation.isError && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-sm text-red-400">
              Failed to save acknowledgement. Please try again.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
