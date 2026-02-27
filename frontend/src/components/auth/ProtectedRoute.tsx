import { useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '@/stores/auth';
import { DisclaimerModal } from '@/components/disclaimer/DisclaimerModal';
import { disclaimerService } from '@/services/disclaimer';
import { isDisclaimerResolved, setDisclaimerResolved } from '@/lib/disclaimer-state';
import type { DisclaimerStatus } from '@/types';

// Re-export for backwards compatibility
export { resetDisclaimerState } from '@/lib/disclaimer-state';

interface ProtectedRouteProps {
  children: React.ReactNode;
}

export function ProtectedRoute({ children }: ProtectedRouteProps) {
  const { user, token } = useAuthStore();
  const queryClient = useQueryClient();
  // Local state to force re-render when disclaimer is resolved
  const [, forceUpdate] = useState(0);

  // Only fetch disclaimer status when we have an authenticated user
  // and haven't already resolved the disclaimer this session
  const resolved = isDisclaimerResolved();
  const { data: disclaimerStatus, isLoading: disclaimerLoading } = useQuery<DisclaimerStatus>({
    queryKey: ['disclaimer-status'],
    queryFn: disclaimerService.getStatus,
    enabled: !!user && !!token && !resolved,
    retry: 1,
    staleTime: Infinity, // Don't re-fetch during session — once resolved, it's done
  });

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  // If already resolved this session, render children immediately
  if (resolved) {
    return <>{children}</>;
  }

  // While loading disclaimer status, show nothing (prevents flash)
  if (disclaimerLoading) {
    return null;
  }

  // If the API says disclaimer is not required, mark resolved and proceed
  if (disclaimerStatus && !disclaimerStatus.requires_disclaimer) {
    setDisclaimerResolved(true);
    return <>{children}</>;
  }

  // Show disclaimer modal if required
  if (disclaimerStatus?.requires_disclaimer) {
    return (
      <DisclaimerModal
        status={disclaimerStatus}
        onAcknowledged={() => {
          setDisclaimerResolved(true);
          queryClient.invalidateQueries({ queryKey: ['disclaimer-status'] });
          forceUpdate(n => n + 1);
        }}
      />
    );
  }

  return <>{children}</>;
}
