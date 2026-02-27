/**
 * Unit tests for AgentConfigModal component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/test-utils';
import { AgentConfigModal } from '../AgentConfigModal';
import type { AgentSummary } from '@/types';

// Mock the hooks
const mockUpdateAgent = vi.fn();
const mockUpdateBudget = vi.fn();

vi.mock('@/hooks/useAgents', () => ({
  useUpdateAgent: vi.fn(() => ({
    mutateAsync: mockUpdateAgent,
    isPending: false,
  })),
  useUpdateAgentBudget: vi.fn(() => ({
    mutateAsync: mockUpdateBudget,
    isPending: false,
  })),
}));

const mockScoutAgent: AgentSummary = {
  id: 'uuid-1',
  name: 'Opportunity Scout',
  slug: 'opportunity_scout',
  description: 'Discovers potential money-making opportunities.',
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

const mockWriterAgent: AgentSummary = {
  ...mockScoutAgent,
  id: 'uuid-2',
  name: 'Proposal Writer',
  slug: 'proposal_writer',
  description: 'Creates detailed campaign proposals.',
  schedule_interval_seconds: 300, // 5 minutes
  default_model_tier: 'reasoning',
  config: null,
};

describe('AgentConfigModal', () => {
  const mockOnClose = vi.fn();
  const user = userEvent.setup();

  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateAgent.mockResolvedValue({});
    mockUpdateBudget.mockResolvedValue({});
  });

  describe('Rendering', () => {
    it('renders modal when isOpen is true', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Configure Opportunity Scout')).toBeInTheDocument();
    });

    it('does not render when isOpen is false', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={false}
          onClose={mockOnClose}
        />
      );

      expect(screen.queryByText('Configure Opportunity Scout')).not.toBeInTheDocument();
    });

    it('displays current schedule value', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // 6 hours preset should be highlighted
      const sixHourButton = screen.getByRole('button', { name: '6 hours' });
      expect(sixHourButton).toHaveClass('bg-neon-cyan/20');
    });

    it('displays all schedule presets', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByRole('button', { name: '5 minutes' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '10 minutes' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '15 minutes' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '30 minutes' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '1 hour' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '6 hours' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '24 hours' })).toBeInTheDocument();
    });

    it('displays budget configuration section', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Budget')).toBeInTheDocument();
      // The label includes the current threshold value
      expect(screen.getByText(/Warning at \d+% of budget/)).toBeInTheDocument();
    });

    it('displays model tier options', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Model Tier')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /fast/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /reasoning/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /quality/i })).toBeInTheDocument();
    });

    it('shows batch size slider only for opportunity_scout', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.getByText('Batch Size')).toBeInTheDocument();
      expect(screen.getByText(/Opportunities per run/i)).toBeInTheDocument();
    });

    it('does not show batch size slider for other agents', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockWriterAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      expect(screen.queryByText('Batch Size')).not.toBeInTheDocument();
    });
  });

  describe('Schedule Presets', () => {
    it('clicking a preset updates the schedule', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const oneHourButton = screen.getByRole('button', { name: '1 hour' });
      await user.click(oneHourButton);

      // The 1 hour button should now be highlighted
      expect(oneHourButton).toHaveClass('bg-neon-cyan/20');
    });

    it('custom schedule input works', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const customInput = screen.getByPlaceholderText(/e\.g\., 45m, 8h, 2d/i);
      await user.clear(customInput);
      await user.type(customInput, '45m');

      // Save button should be enabled now
      const saveButton = screen.getByRole('button', { name: /save/i });
      expect(saveButton).not.toBeDisabled();
    });
  });

  describe('Save Functionality', () => {
    it('Save button is disabled when no changes', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const saveButton = screen.getByRole('button', { name: /save/i });
      expect(saveButton).toBeDisabled();
    });

    it('Save button is enabled after making changes', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // Change model tier
      const reasoningButton = screen.getByRole('button', { name: /reasoning/i });
      await user.click(reasoningButton);

      const saveButton = screen.getByRole('button', { name: /save/i });
      expect(saveButton).not.toBeDisabled();
    });

    it('calls updateAgent and updateBudget on save', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // Make a change
      const qualityButton = screen.getByRole('button', { name: /quality/i });
      await user.click(qualityButton);

      // Click save
      const saveButton = screen.getByRole('button', { name: /save/i });
      await user.click(saveButton);

      await waitFor(() => {
        expect(mockUpdateAgent).toHaveBeenCalled();
        expect(mockUpdateBudget).toHaveBeenCalled();
      });
    });

    it('closes modal after successful save', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // Make a change and save
      const qualityButton = screen.getByRole('button', { name: /quality/i });
      await user.click(qualityButton);
      
      const saveButton = screen.getByRole('button', { name: /save/i });
      await user.click(saveButton);

      await waitFor(() => {
        expect(mockOnClose).toHaveBeenCalled();
      });
    });
  });

  describe('Reset to Defaults', () => {
    it('Reset button is disabled when at defaults', () => {
      // Create agent that's already at defaults
      const defaultAgent = {
        ...mockScoutAgent,
        schedule_interval_seconds: 21600, // 6 hours (default for scout)
        default_model_tier: 'fast',
        budget_limit: null,
        budget_period: 'daily' as const,
      };

      renderWithProviders(
        <AgentConfigModal
          agent={defaultAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const resetButton = screen.getByRole('button', { name: /reset to defaults/i });
      expect(resetButton).toBeDisabled();
    });

    it('Reset button is enabled when not at defaults', () => {
      // Create agent with non-default settings
      const customAgent = {
        ...mockScoutAgent,
        schedule_interval_seconds: 3600, // 1 hour (not default)
        default_model_tier: 'quality', // not default
      };

      renderWithProviders(
        <AgentConfigModal
          agent={customAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const resetButton = screen.getByRole('button', { name: /reset to defaults/i });
      expect(resetButton).not.toBeDisabled();
    });

    it('clicking Reset restores default values', async () => {
      const customAgent = {
        ...mockScoutAgent,
        schedule_interval_seconds: 3600,
        default_model_tier: 'quality',
      };

      renderWithProviders(
        <AgentConfigModal
          agent={customAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const resetButton = screen.getByRole('button', { name: /reset to defaults/i });
      await user.click(resetButton);

      // 6 hours should now be highlighted (default for scout)
      const sixHourButton = screen.getByRole('button', { name: '6 hours' });
      expect(sixHourButton).toHaveClass('bg-neon-cyan/20');

      // Fast should now be highlighted
      const fastButton = screen.getByRole('button', { name: /fast/i });
      expect(fastButton).toHaveClass('bg-orange-500/20');
    });
  });

  describe('Cancel Functionality', () => {
    it('Cancel button closes modal', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      const cancelButton = screen.getByRole('button', { name: /cancel/i });
      await user.click(cancelButton);

      expect(mockOnClose).toHaveBeenCalled();
    });

    it('clicking backdrop closes modal', async () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // Click the backdrop (the dark overlay behind the modal)
      const backdrop = document.querySelector('.bg-black\\/70');
      if (backdrop) {
        await user.click(backdrop);
      }

      expect(mockOnClose).toHaveBeenCalled();
    });
  });

  describe('Budget Warning Threshold', () => {
    it('displays current warning threshold', () => {
      renderWithProviders(
        <AgentConfigModal
          agent={mockScoutAgent}
          isOpen={true}
          onClose={mockOnClose}
        />
      );

      // 0.8 * 100 = 80%, displayed in the label as "Warning at 80% of budget"
      expect(screen.getByText(/Warning at 80% of budget/)).toBeInTheDocument();
    });
  });
});
