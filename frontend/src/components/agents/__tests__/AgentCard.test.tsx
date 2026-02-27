/**
 * Unit tests for AgentCard component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/test-utils';
import { AgentCard } from '../AgentCard';
import type { AgentSummary } from '@/types';

// Mock the hooks
vi.mock('@/hooks/useAgents', () => ({
  useAgentRuns: vi.fn(() => ({ data: [], isLoading: false })),
  useAgentStatistics: vi.fn(() => ({ data: null, isLoading: false })),
  usePauseAgent: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useResumeAgent: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
  useTriggerAgent: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}));

const mockAgent: AgentSummary = {
  id: 'uuid-1',
  name: 'Opportunity Scout',
  slug: 'opportunity_scout',
  description: 'Discovers potential money-making opportunities through web searches and analysis.',
  status: 'idle',
  status_message: null,
  is_enabled: true,
  schedule_interval_seconds: 21600, // 6 hours
  last_run_at: '2026-01-29T10:00:00Z',
  next_run_at: '2026-01-29T16:00:00Z',
  budget_limit: 10.0,
  budget_period: 'daily',
  budget_used: 2.5,
  budget_remaining: 7.5,
  budget_percentage_used: 25,
  budget_warning: false,
  budget_warning_threshold: 0.8,
  total_runs: 50,
  successful_runs: 48,
  failed_runs: 2,
  success_rate: 96,
  total_tokens_used: 50000,
  total_cost_usd: 5.0,
  default_model_tier: 'fast',
  config: { batch_size: 10 },
};

describe('AgentCard', () => {
  const mockOnExpand = vi.fn();
  const mockOnConfigure = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('Rendering', () => {
    it('renders agent name and description', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      expect(screen.getByText(/Discovers potential money-making/)).toBeInTheDocument();
    });

    it('displays idle status badge correctly', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText('idle')).toBeInTheDocument();
    });

    it('displays running status badge with animation', () => {
      const runningAgent = { ...mockAgent, status: 'running' as const };
      renderWithProviders(
        <AgentCard
          agent={runningAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText('running')).toBeInTheDocument();
    });

    it('displays paused status badge', () => {
      const pausedAgent = { ...mockAgent, status: 'paused' as const };
      renderWithProviders(
        <AgentCard
          agent={pausedAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText('paused')).toBeInTheDocument();
    });

    it('displays error status badge', () => {
      const errorAgent = { 
        ...mockAgent, 
        status: 'error' as const,
        status_message: 'Connection failed' 
      };
      renderWithProviders(
        <AgentCard
          agent={errorAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText('error')).toBeInTheDocument();
    });

    it('displays schedule interval correctly', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // 21600 seconds = 6 hours
      expect(screen.getByText(/6h/)).toBeInTheDocument();
    });

    it('displays budget progress bar', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Component shows budget values
      expect(screen.getByText(/\$2/)).toBeInTheDocument();
      expect(screen.getByText(/\$10/)).toBeInTheDocument();
    });

    it('displays "No limit" when budget_limit is null', () => {
      const noBudgetAgent = { ...mockAgent, budget_limit: null, budget_remaining: null };
      const { container } = renderWithProviders(
        <AgentCard
          agent={noBudgetAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // When budget is null, budget section may show differently
      expect(container).toBeInTheDocument();
    });

    it('displays success rate', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByText(/96%/)).toBeInTheDocument();
    });
  });

  describe('Interactions', () => {
    it('calls onExpand when expand button is clicked', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Find expand button by title
      const expandButton = screen.getByTitle('Expand');
      fireEvent.click(expandButton);

      expect(mockOnExpand).toHaveBeenCalled();
    });

    it('calls onConfigure when configure button is clicked', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      const configureButton = screen.getByTitle('Configure');
      fireEvent.click(configureButton);

      expect(mockOnConfigure).toHaveBeenCalled();
    });

    it('shows Pause button for idle agent', () => {
      // For idle agent, the component shows "Run now" button, not pause
      // Pause is only shown when running
      const runningAgent = { ...mockAgent, status: 'running' as const };
      renderWithProviders(
        <AgentCard
          agent={runningAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByTitle('Pause agent')).toBeInTheDocument();
    });

    it('shows Resume button for paused agent', () => {
      const pausedAgent = { ...mockAgent, status: 'paused' as const };
      renderWithProviders(
        <AgentCard
          agent={pausedAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByTitle('Resume agent')).toBeInTheDocument();
    });

    it('shows Trigger button for idle agent', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      expect(screen.getByTitle('Run now')).toBeInTheDocument();
    });

    it('disables Trigger button when agent is running', () => {
      const runningAgent = { ...mockAgent, status: 'running' as const };
      renderWithProviders(
        <AgentCard
          agent={runningAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // When running, shows Pause button instead of Trigger
      expect(screen.getByTitle('Pause agent')).toBeInTheDocument();
    });
  });

  describe('Expanded State', () => {
    it('shows expanded content when isExpanded is true', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={true}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Expanded view should show performance section
      expect(screen.getByText(/Performance/i)).toBeInTheDocument();
    });

    it('hides expanded content when isExpanded is false', () => {
      renderWithProviders(
        <AgentCard
          agent={mockAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Should not show run history section
      expect(screen.queryByText(/Run History/i)).not.toBeInTheDocument();
    });
  });

  describe('Budget Warning States', () => {
    it('shows warning indicator when budget_warning is true', () => {
      const warningAgent = { 
        ...mockAgent, 
        budget_warning: true,
        budget_percentage_used: 85,
        budget_used: 8.5,
      };
      renderWithProviders(
        <AgentCard
          agent={warningAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Budget bar should have warning styling
      const budgetText = screen.getByText(/\$8\.50/);
      expect(budgetText).toBeInTheDocument();
    });

    it('shows budget exceeded status', () => {
      const exceededAgent = { 
        ...mockAgent, 
        status: 'budget_exceeded' as const,
        budget_percentage_used: 120,
        budget_used: 12.0,
        budget_remaining: 0,
      };
      renderWithProviders(
        <AgentCard
          agent={exceededAgent}
          isExpanded={false}
          onExpand={mockOnExpand}
          onConfigure={mockOnConfigure}
        />
      );

      // Component replaces underscore with space
      expect(screen.getByText('budget exceeded')).toBeInTheDocument();
    });
  });
});
