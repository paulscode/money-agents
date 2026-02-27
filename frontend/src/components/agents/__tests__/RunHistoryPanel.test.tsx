/**
 * Unit tests for RunHistoryPanel component
 */
import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/test-utils';
import { RunHistoryPanel } from '../RunHistoryPanel';
import type { AgentRunSummary } from '@/types';

const mockRuns: AgentRunSummary[] = [
  {
    id: 'run-1',
    agent_slug: 'opportunity_scout',
    status: 'completed',
    trigger_type: 'scheduled',
    trigger_reason: null,
    started_at: '2026-01-29T10:00:00Z',
    completed_at: '2026-01-29T10:05:30Z',
    duration_seconds: 330,
    items_processed: 25,
    items_created: 5,
    tokens_used: 1500,
    cost_usd: 0.15,
    model_used: 'gpt-4o-mini',
    error_message: null,
    created_at: '2026-01-29T10:00:00Z',
  },
  {
    id: 'run-2',
    agent_slug: 'opportunity_scout',
    status: 'failed',
    trigger_type: 'manual',
    trigger_reason: 'User triggered test',
    started_at: '2026-01-29T08:00:00Z',
    completed_at: '2026-01-29T08:01:00Z',
    duration_seconds: 60,
    items_processed: 0,
    items_created: 0,
    tokens_used: 200,
    cost_usd: 0.02,
    model_used: 'gpt-4o-mini',
    error_message: 'Connection timeout',
    created_at: '2026-01-29T08:00:00Z',
  },
  {
    id: 'run-3',
    agent_slug: 'opportunity_scout',
    status: 'running',
    trigger_type: 'scheduled',
    trigger_reason: null,
    started_at: '2026-01-29T14:00:00Z',
    completed_at: null,
    duration_seconds: null,
    items_processed: 10,
    items_created: 2,
    tokens_used: 500,
    cost_usd: 0.05,
    model_used: 'gpt-4o-mini',
    error_message: null,
    created_at: '2026-01-29T14:00:00Z',
  },
];

describe('RunHistoryPanel', () => {
  describe('Rendering', () => {
    it('renders run entries', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      // Should show all three runs
      expect(screen.getAllByText(/completed|failed|running/i)).toHaveLength(3);
    });

    it('displays empty state when no runs', () => {
      renderWithProviders(<RunHistoryPanel runs={[]} />);

      expect(screen.getByText('No runs recorded yet')).toBeInTheDocument();
    });

    it('shows status badges correctly', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      expect(screen.getByText('completed')).toBeInTheDocument();
      expect(screen.getByText('failed')).toBeInTheDocument();
      expect(screen.getByText('running')).toBeInTheDocument();
    });

    it('shows trigger type', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      expect(screen.getAllByText('scheduled')).toHaveLength(2);
      expect(screen.getByText('manual')).toBeInTheDocument();
    });

    it('shows duration for completed runs', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      // 330 seconds = 5m 30s
      expect(screen.getByText(/5m 30s/)).toBeInTheDocument();
    });

    it('shows items processed', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      expect(screen.getByText('25')).toBeInTheDocument(); // First run
      expect(screen.getByText('10')).toBeInTheDocument(); // Running
    });

    it('shows cost', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      // Component uses 3 decimal places for cost
      expect(screen.getByText('$0.150')).toBeInTheDocument();
      expect(screen.getByText('$0.020')).toBeInTheDocument();
    });

    it('shows error message for failed runs', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      expect(screen.getByText('Connection timeout')).toBeInTheDocument();
    });
  });

  describe('Compact Mode', () => {
    it('renders compact view when compact=true', () => {
      const { container } = renderWithProviders(<RunHistoryPanel runs={mockRuns} compact={true} />);

      // Compact view uses flex items with colored dots for status
      const statusDots = container.querySelectorAll('.rounded-full');
      expect(statusDots.length).toBeGreaterThan(0);
    });

    it('shows basic info in compact mode', () => {
      const { container } = renderWithProviders(<RunHistoryPanel runs={mockRuns} compact={true} />);

      // Should show status dots with appropriate colors
      const greenDot = container.querySelector('.bg-green-500');
      const redDot = container.querySelector('.bg-red-500');
      expect(greenDot).toBeInTheDocument();
      expect(redDot).toBeInTheDocument();
    });
  });

  describe('Agent Name Display', () => {
    it('shows agent name when showAgentName=true', () => {
      const runsWithDifferentAgents: AgentRunSummary[] = [
        { ...mockRuns[0], agent_slug: 'opportunity_scout' },
        { ...mockRuns[1], id: 'run-4', agent_slug: 'proposal_writer' },
      ];

      renderWithProviders(
        <RunHistoryPanel runs={runsWithDifferentAgents} showAgentName={true} />
      );

      expect(screen.getByText('opportunity_scout')).toBeInTheDocument();
      expect(screen.getByText('proposal_writer')).toBeInTheDocument();
    });

    it('does not show agent name by default', () => {
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      // Should not show agent slug as a label
      const agentLabels = screen.queryAllByText('opportunity_scout');
      // The slug might appear in other places but not as a prominent label
      expect(agentLabels.length).toBe(0);
    });
  });

  describe('Run Status Styling', () => {
    it('applies correct styling for completed status', () => {
      renderWithProviders(<RunHistoryPanel runs={[mockRuns[0]]} />);

      const statusBadge = screen.getByText('completed');
      expect(statusBadge.className).toMatch(/green|success/i);
    });

    it('applies correct styling for failed status', () => {
      renderWithProviders(<RunHistoryPanel runs={[mockRuns[1]]} />);

      const statusBadge = screen.getByText('failed');
      expect(statusBadge.className).toMatch(/red|error|danger/i);
    });

    it('applies correct styling for running status', () => {
      renderWithProviders(<RunHistoryPanel runs={[mockRuns[2]]} />);

      const statusBadge = screen.getByText('running');
      expect(statusBadge.className).toMatch(/cyan|blue|info/i);
    });
  });

  describe('Timestamps', () => {
    it('shows relative time for recent runs', () => {
      // This test is time-sensitive, but we can check that some time display exists
      renderWithProviders(<RunHistoryPanel runs={mockRuns} />);

      // Should show some form of time indication
      // The exact text depends on how recently the mock dates are
      const timeElements = screen.getAllByText(/ago|just now|\d+[hmd]/i);
      expect(timeElements.length).toBeGreaterThan(0);
    });
  });
});
