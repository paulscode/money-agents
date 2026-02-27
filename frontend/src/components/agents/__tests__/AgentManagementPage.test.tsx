/**
 * Integration tests for AgentManagementPage
 * 
 * Tests the full page behavior with mocked hooks.
 * Uses vi.hoisted() to create mutable mock state that works with Vitest's mock hoisting.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';
import type { AgentSummary, AgentRun } from '../../../types';

// Use vi.hoisted to create mock state that survives hoisting
const { 
  mockState, 
  mockPauseAgent, 
  mockResumeAgent, 
  mockTriggerAgent, 
  mockRefetch 
} = vi.hoisted(() => {
  return {
    mockState: {
      agents: undefined as AgentSummary[] | undefined,
      recentRuns: undefined as AgentRun[] | undefined,
      agentRuns: [] as AgentRun[],
      error: null as Error | null,
      isLoading: false,
    },
    mockPauseAgent: vi.fn(),
    mockResumeAgent: vi.fn(),
    mockTriggerAgent: vi.fn(),
    mockRefetch: vi.fn(),
  };
});

// Mock the hooks module
vi.mock('@/hooks/useAgents', () => ({
  useAgents: () => ({
    data: mockState.agents,
    isLoading: mockState.isLoading,
    error: mockState.error,
    refetch: mockRefetch,
  }),
  useRecentRuns: () => ({
    data: mockState.recentRuns,
    isLoading: false,
  }),
  useAgentRuns: () => ({
    data: mockState.agentRuns,
    isLoading: false,
  }),
  useAgentStatistics: () => ({
    data: null,
    isLoading: false,
  }),
  usePauseAgent: () => ({
    mutate: (data: { slug: string }) => mockPauseAgent(data.slug),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useResumeAgent: () => ({
    mutate: (slug: string) => mockResumeAgent(slug),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useTriggerAgent: () => ({
    mutate: (data: { slug: string }) => mockTriggerAgent(data.slug),
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useUpdateAgent: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useUpdateAgentBudget: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
}));

// Mock the agentService module
vi.mock('@/services/agentService', () => ({
  getAgents: vi.fn(),
  getRecentRuns: vi.fn(),
  pauseAgent: vi.fn(),
  resumeAgent: vi.fn(),
  triggerAgent: vi.fn(),
  getAgent: vi.fn(),
  getAgentRuns: vi.fn(),
  getAgentStatistics: vi.fn(),
  getAgentBudget: vi.fn(),
  updateAgent: vi.fn(),
  updateAgentBudget: vi.fn(),
  formatDuration: (seconds: number) => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
    return `${Math.floor(seconds / 86400)}d`;
  },
  parseDuration: vi.fn(),
  SCHEDULE_PRESETS: [
    { label: '5 minutes', seconds: 300 },
    { label: '10 minutes', seconds: 600 },
    { label: '30 minutes', seconds: 1800 },
    { label: '1 hour', seconds: 3600 },
    { label: '6 hours', seconds: 21600 },
  ],
  getStatusColor: (status: string) => {
    switch (status) {
      case 'running': return 'text-neon-cyan';
      case 'idle': return 'text-green-500';
      case 'paused': return 'text-yellow-500';
      case 'error': return 'text-red-500';
      default: return 'text-gray-500';
    }
  },
  getStatusBadgeClasses: (status: string) => {
    switch (status) {
      case 'running': return 'bg-neon-cyan/20 text-neon-cyan';
      case 'idle': return 'bg-green-500/20 text-green-500';
      case 'paused': return 'bg-yellow-500/20 text-yellow-500';
      case 'error': return 'bg-red-500/20 text-red-500';
      default: return 'bg-gray-500/20 text-gray-500';
    }
  },
  getRunStatusBadgeClasses: (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-500/20 text-green-500';
      case 'running': return 'bg-neon-cyan/20 text-neon-cyan';
      case 'failed': return 'bg-red-500/20 text-red-500';
      default: return 'bg-gray-500/20 text-gray-500';
    }
  },
}));

// Import the page AFTER mocks are set up
import AgentManagementPage from '../../../pages/AgentManagementPage';

// Mock data
const mockAgents: AgentSummary[] = [
  {
    id: 'uuid-1',
    name: 'Opportunity Scout',
    slug: 'opportunity_scout',
    description: 'Monitors job boards and LinkedIn for opportunities',
    is_enabled: true,
    status: 'idle',
    status_message: null,
    schedule_interval_seconds: 21600,
    last_run_at: '2024-01-15T10:00:00Z',
    next_run_at: '2024-01-15T16:00:00Z',
    default_model_tier: 'fast',
    config: { batch_size: 10 },
    budget_period: 'daily',
    budget_limit: 5.0,
    budget_used: 1.5,
    budget_remaining: 3.5,
    budget_percentage_used: 30,
    budget_warning: false,
    budget_warning_threshold: 0.8,
    total_runs: 10,
    successful_runs: 9,
    failed_runs: 1,
    success_rate: 90,
    total_tokens_used: 10000,
    total_cost_usd: 1.5,
  },
  {
    id: 'uuid-2',
    name: 'Proposal Writer',
    slug: 'proposal_writer',
    description: 'Drafts proposals for approved opportunities',
    is_enabled: true,
    status: 'running',
    status_message: 'Processing 5 opportunities',
    schedule_interval_seconds: 300,
    last_run_at: '2024-01-15T12:00:00Z',
    next_run_at: null,
    default_model_tier: 'reasoning',
    config: {},
    budget_period: 'weekly',
    budget_limit: 20.0,
    budget_used: 15.0,
    budget_remaining: 5.0,
    budget_percentage_used: 75,
    budget_warning: true,
    budget_warning_threshold: 0.8,
    total_runs: 50,
    successful_runs: 44,
    failed_runs: 6,
    success_rate: 88,
    total_tokens_used: 50000,
    total_cost_usd: 15.0,
  },
  {
    id: 'uuid-3',
    name: 'Campaign Manager',
    slug: 'campaign_manager',
    description: 'Manages outreach campaigns',
    is_enabled: true,
    status: 'paused',
    status_message: 'Paused by admin',
    schedule_interval_seconds: 600,
    last_run_at: null,
    next_run_at: null,
    default_model_tier: 'reasoning',
    config: {},
    budget_period: 'monthly',
    budget_limit: 100.0,
    budget_used: 0,
    budget_remaining: 100.0,
    budget_percentage_used: 0,
    budget_warning: false,
    budget_warning_threshold: 0.8,
    total_runs: 0,
    successful_runs: 0,
    failed_runs: 0,
    success_rate: 0,
    total_tokens_used: 0,
    total_cost_usd: 0,
  },
];

const mockRuns: AgentRun[] = [
  {
    id: '1',
    agent_slug: 'opportunity_scout',
    trigger_type: 'scheduled',
    status: 'completed',
    started_at: '2024-01-15T10:00:00Z',
    completed_at: '2024-01-15T10:02:00Z',
    duration_seconds: 120,
    tokens_used: 1000,
    cost_usd: 0.15,
    error_message: null,
    result_summary: { items_found: 5 },
    items_processed: 15,
  },
];

// Helper to render with providers
function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return {
    ...render(
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>{ui}</BrowserRouter>
      </QueryClientProvider>
    ),
    queryClient,
  };
}

describe('AgentManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Set up default mock state
    mockState.agents = mockAgents;
    mockState.recentRuns = mockRuns;
    mockState.agentRuns = mockRuns;
    mockState.error = null;
    mockState.isLoading = false;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // =========================================================================
  // Page Structure Tests
  // =========================================================================

  describe('Page Structure', () => {
    it('renders page title', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Agent Control Center')).toBeInTheDocument();
      });
    });

    it('renders all agent cards', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
        expect(screen.getByText('Proposal Writer')).toBeInTheDocument();
        expect(screen.getByText('Campaign Manager')).toBeInTheDocument();
      });
    });

    it('shows loading state when isLoading is true', () => {
      mockState.agents = undefined;
      mockState.isLoading = true;

      renderWithProviders(<AgentManagementPage />);

      const loadingIndicator = document.querySelector('.animate-spin');
      expect(loadingIndicator).toBeInTheDocument();
    });
  });

  // =========================================================================
  // Agent Cards Display Tests
  // =========================================================================

  describe('Agent Cards Display', () => {
    it('displays agent status badges correctly', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        // Use getAllByText since there may be multiple "idle" texts (badge + overview)
        expect(screen.getAllByText(/idle/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/running/i).length).toBeGreaterThan(0);
        expect(screen.getAllByText(/paused/i).length).toBeGreaterThan(0);
      });
    });

    it('displays agent descriptions', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText(/Monitors job boards/)).toBeInTheDocument();
        expect(screen.getByText(/Drafts proposals/)).toBeInTheDocument();
      });
    });

    it('displays budget information', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getAllByText(/\$1\.50/).length).toBeGreaterThan(0);
      });
    });
  });

  // =========================================================================
  // Agent Actions Tests
  // =========================================================================

  describe('Agent Actions', () => {
    it('shows run now button for idle agent', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      expect(screen.getByTitle('Run now')).toBeInTheDocument();
    });

    it('shows resume button for paused agent', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Campaign Manager')).toBeInTheDocument();
      });

      expect(screen.getByTitle('Resume agent')).toBeInTheDocument();
    });

    it('shows pause button for running agent', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Proposal Writer')).toBeInTheDocument();
      });

      expect(screen.getByTitle('Pause agent')).toBeInTheDocument();
    });

    it('can trigger a manual run on idle agent', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const runButton = screen.getByTitle('Run now');
      await user.click(runButton);

      await waitFor(() => {
        expect(mockTriggerAgent).toHaveBeenCalledWith('opportunity_scout');
      });
    });

    it('can pause a running agent', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Proposal Writer')).toBeInTheDocument();
      });

      const pauseButton = screen.getByTitle('Pause agent');
      await user.click(pauseButton);

      await waitFor(() => {
        expect(mockPauseAgent).toHaveBeenCalledWith('proposal_writer');
      });
    });

    it('can resume a paused agent', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Campaign Manager')).toBeInTheDocument();
      });

      const resumeButton = screen.getByTitle('Resume agent');
      await user.click(resumeButton);

      await waitFor(() => {
        expect(mockResumeAgent).toHaveBeenCalledWith('campaign_manager');
      });
    });
  });

  // =========================================================================
  // Configuration Modal Tests
  // =========================================================================

  describe('Configuration Modal', () => {
    it('opens config modal when configure button is clicked', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      // Configure button has title="Configure"
      const configButtons = screen.getAllByTitle('Configure');
      await user.click(configButtons[0]);

      await waitFor(() => {
        expect(screen.getByText(/Configure.*Opportunity Scout/i)).toBeInTheDocument();
      });
    });

    it('closes modal when clicking X button', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const configButtons = screen.getAllByTitle('Configure');
      await user.click(configButtons[0]);

      await waitFor(() => {
        expect(screen.getByText(/Configure.*Opportunity Scout/i)).toBeInTheDocument();
      });

      // Click backdrop to close
      const backdrop = document.querySelector('.bg-black\\/70');
      if (backdrop) {
        await user.click(backdrop);
      }

      await waitFor(() => {
        expect(screen.queryByText(/Configure.*Opportunity Scout/i)).not.toBeInTheDocument();
      });
    });
  });

  // =========================================================================
  // Error Handling Tests
  // =========================================================================

  describe('Error Handling', () => {
    it('shows error state when agent fetch fails', async () => {
      mockState.agents = undefined;
      mockState.error = new Error('Network error');
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText(/Failed to load agents/i)).toBeInTheDocument();
      });
    });

    it('shows try again button on error', async () => {
      mockState.agents = undefined;
      mockState.error = new Error('Network error');
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
      });
    });
  });

  // =========================================================================
  // Refresh Tests
  // =========================================================================

  describe('Data Refresh', () => {
    it('has refresh button', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const refreshButton = screen.getByRole('button', { name: /refresh/i });
      expect(refreshButton).toBeInTheDocument();
    });

    it('calls refetch when refresh is clicked', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const refreshButton = screen.getByRole('button', { name: /refresh/i });
      await user.click(refreshButton);

      await waitFor(() => {
        expect(mockRefetch).toHaveBeenCalled();
      });
    });
  });

  // =========================================================================
  // Expanded State Tests
  // =========================================================================

  describe('Expanded State', () => {
    it('can expand an agent card', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const expandButtons = screen.getAllByTitle('Expand');
      await user.click(expandButtons[0]);

      await waitFor(() => {
        expect(screen.getByText(/Performance \(Last 7 Days\)/i)).toBeInTheDocument();
      });
    });

    it('can collapse an expanded agent card', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      // Expand first
      const expandButtons = screen.getAllByTitle('Expand');
      await user.click(expandButtons[0]);

      await waitFor(() => {
        expect(screen.getByText(/Performance \(Last 7 Days\)/i)).toBeInTheDocument();
      });

      // Collapse
      const collapseButton = screen.getByTitle('Collapse');
      await user.click(collapseButton);

      await waitFor(() => {
        expect(screen.queryByText(/Performance \(Last 7 Days\)/i)).not.toBeInTheDocument();
      });
    });
  });

  // =========================================================================
  // Accessibility Tests
  // =========================================================================

  describe('Accessibility', () => {
    it('has proper heading hierarchy', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Agent Control Center')).toBeInTheDocument();
      });

      const h1 = screen.getByRole('heading', { level: 1 });
      expect(h1).toHaveTextContent('Agent Control Center');
    });

    it('all action buttons have accessible names via title', async () => {
      renderWithProviders(<AgentManagementPage />);

      await waitFor(() => {
        expect(screen.getByText('Opportunity Scout')).toBeInTheDocument();
      });

      const buttons = screen.getAllByRole('button');
      buttons.forEach(button => {
        const hasAccessibleName = 
          button.textContent || 
          button.getAttribute('aria-label') || 
          button.getAttribute('title');
        expect(hasAccessibleName).toBeTruthy();
      });
    });
  });
});
