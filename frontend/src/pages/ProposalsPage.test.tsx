import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ProposalsPage } from '@/pages/ProposalsPage';
import { mockProposals } from '@/test/mocks';
import * as proposalsService from '@/services/proposals';

// Mock the proposals service
vi.mock('@/services/proposals', () => ({
  proposalsService: {
    list: vi.fn(),
  },
}));

// Mock the conversations service
vi.mock('@/services/conversations', () => ({
  conversationsService: {
    getAllProposalsUnreadCounts: vi.fn().mockResolvedValue([]),
  },
}));

describe('ProposalsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title and description', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Proposals' })).toBeInTheDocument();
    });
    expect(screen.getByText('AI-generated money-making opportunities')).toBeInTheDocument();
  });

  it('displays loading spinner while fetching', () => {
    vi.mocked(proposalsService.proposalsService.list).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );
    
    renderWithProviders(<ProposalsPage />);
    
    // Loading spinner has the animate-spin class
    const spinner = document.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('displays proposals in grid view by default', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue(mockProposals);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockProposals[0].title)).toBeInTheDocument();
      expect(screen.getByText(mockProposals[1].title)).toBeInTheDocument();
      expect(screen.getByText(mockProposals[2].title)).toBeInTheDocument();
    });
  });

  it('shows empty state when no proposals exist with "needs_action" filter', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      // Default filter is 'needs_action', so shows this message
      expect(screen.getByText('No proposals awaiting action. All proposals have campaigns!')).toBeInTheDocument();
    });
  });

  it('shows generic empty state when viewing all proposals', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    const user = userEvent.setup();
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0);
    });
    
    // Change campaign filter to 'all'
    const [campaignFilter] = screen.getAllByRole('combobox');
    await user.selectOptions(campaignFilter, 'all');
    
    await waitFor(() => {
      expect(screen.getByText('No proposals yet. Create your first one!')).toBeInTheDocument();
    });
  });

  it('has a "New Proposal" button', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    
    renderWithProviders(<ProposalsPage />);
    
    const newButton = screen.getAllByText('New Proposal')[0];
    expect(newButton.closest('a')).toHaveAttribute('href', '/proposals/new');
  });

  it('toggles between grid and list view', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue(mockProposals);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockProposals[0].title)).toBeInTheDocument();
    });
    
    const listViewButton = screen.getByTitle('List View');
    await user.click(listViewButton);
    
    // Check that the view has changed (button should have active styling)
    expect(listViewButton).toHaveClass('bg-neon-cyan/20');
  });

  it('filters proposals by status', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue(mockProposals);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockProposals[0].title)).toBeInTheDocument();
    });
    
    // There are two comboboxes: campaign filter and status filter
    const comboboxes = screen.getAllByRole('combobox');
    expect(comboboxes).toHaveLength(2);
    
    // Status filter is the second one
    const statusFilter = comboboxes[1];
    await user.selectOptions(statusFilter, 'approved');
    
    // Service should be called with status filter (has_campaign=false is default)
    await waitFor(() => {
      expect(proposalsService.proposalsService.list).toHaveBeenCalledWith({ 
        status: 'approved', 
        has_campaign: false 
      });
    });
  });

  it('shows filtered empty state message', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    const user = userEvent.setup();
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      expect(screen.getAllByRole('combobox').length).toBe(2);
    });
    
    // Change status filter to 'approved' (second combobox)
    const comboboxes = screen.getAllByRole('combobox');
    await user.selectOptions(comboboxes[1], 'approved');
    
    await waitFor(() => {
      expect(screen.getByText(/No matching proposals found/)).toBeInTheDocument();
    });
  });

  it('renders both filter dropdowns', async () => {
    vi.mocked(proposalsService.proposalsService.list).mockResolvedValue([]);
    
    renderWithProviders(<ProposalsPage />);
    
    await waitFor(() => {
      const comboboxes = screen.getAllByRole('combobox');
      expect(comboboxes).toHaveLength(2);
    });
    
    // Campaign filter options: needs_action, in_progress, all (3 options)
    // Status filter options: all + 9 statuses (10 options)
    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(13); // 3 campaign + 10 status options
  });
});
