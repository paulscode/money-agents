/**
 * React Query hooks for Agent Management.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import * as agentService from '@/services/agentService';
import type {
  AgentUpdateRequest,
  BudgetUpdateRequest,
} from '@/types';

// =============================================================================
// Query Keys
// =============================================================================

export const agentKeys = {
  all: ['agents'] as const,
  list: () => [...agentKeys.all, 'list'] as const,
  detail: (slug: string) => [...agentKeys.all, 'detail', slug] as const,
  runs: (slug: string) => [...agentKeys.all, 'runs', slug] as const,
  stats: (slug: string, days: number) => [...agentKeys.all, 'stats', slug, days] as const,
  budget: (slug: string) => [...agentKeys.all, 'budget', slug] as const,
  recentRuns: () => [...agentKeys.all, 'recentRuns'] as const,
};

// =============================================================================
// Query Hooks
// =============================================================================

export function useAgents(includeDisabled = false) {
  return useQuery({
    queryKey: agentKeys.list(),
    queryFn: () => agentService.getAgents(includeDisabled),
    staleTime: 10_000, // 10 seconds
    refetchInterval: 30_000, // Auto-refresh every 30s
  });
}

export function useAgent(slug: string) {
  return useQuery({
    queryKey: agentKeys.detail(slug),
    queryFn: () => agentService.getAgent(slug),
    staleTime: 10_000,
    refetchInterval: 15_000, // More frequent for individual agent view
    enabled: Boolean(slug),
  });
}

export function useAgentRuns(slug: string, limit = 20) {
  return useQuery({
    queryKey: agentKeys.runs(slug),
    queryFn: () => agentService.getAgentRuns(slug, limit),
    staleTime: 10_000,
    refetchInterval: 15_000,
    enabled: Boolean(slug),
  });
}

export function useAgentStatistics(slug: string, days = 7) {
  return useQuery({
    queryKey: agentKeys.stats(slug, days),
    queryFn: () => agentService.getAgentStatistics(slug, days),
    staleTime: 60_000, // Stats don't change as frequently
    enabled: Boolean(slug),
  });
}

export function useAgentBudget(slug: string) {
  return useQuery({
    queryKey: agentKeys.budget(slug),
    queryFn: () => agentService.getAgentBudget(slug),
    staleTime: 30_000,
    enabled: Boolean(slug),
  });
}

export function useRecentRuns(limit = 50) {
  return useQuery({
    queryKey: agentKeys.recentRuns(),
    queryFn: () => agentService.getRecentRuns(limit),
    staleTime: 10_000,
    refetchInterval: 15_000,
  });
}

// =============================================================================
// Mutation Hooks
// =============================================================================

export function useUpdateAgent() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ slug, data }: { slug: string; data: AgentUpdateRequest }) =>
      agentService.updateAgent(slug, data),
    onSuccess: (data) => {
      // Update the specific agent in cache
      queryClient.setQueryData(agentKeys.detail(data.slug), data);
      // Invalidate the list to refresh
      queryClient.invalidateQueries({ queryKey: agentKeys.list() });
    },
  });
}

export function usePauseAgent() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ slug, reason }: { slug: string; reason?: string }) =>
      agentService.pauseAgent(slug, reason),
    onSuccess: (_, { slug }) => {
      queryClient.invalidateQueries({ queryKey: agentKeys.detail(slug) });
      queryClient.invalidateQueries({ queryKey: agentKeys.list() });
    },
  });
}

export function useResumeAgent() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: (slug: string) => agentService.resumeAgent(slug),
    onSuccess: (_, slug) => {
      queryClient.invalidateQueries({ queryKey: agentKeys.detail(slug) });
      queryClient.invalidateQueries({ queryKey: agentKeys.list() });
    },
  });
}

export function useTriggerAgent() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ slug, reason }: { slug: string; reason?: string }) =>
      agentService.triggerAgent(slug, reason),
    onSuccess: (_, { slug }) => {
      queryClient.invalidateQueries({ queryKey: agentKeys.detail(slug) });
      queryClient.invalidateQueries({ queryKey: agentKeys.runs(slug) });
      queryClient.invalidateQueries({ queryKey: agentKeys.list() });
    },
  });
}

export function useUpdateAgentBudget() {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ slug, data }: { slug: string; data: BudgetUpdateRequest }) =>
      agentService.updateAgentBudget(slug, data),
    onSuccess: (data) => {
      queryClient.setQueryData(agentKeys.detail(data.slug), data);
      queryClient.invalidateQueries({ queryKey: agentKeys.budget(data.slug) });
      queryClient.invalidateQueries({ queryKey: agentKeys.list() });
    },
  });
}
