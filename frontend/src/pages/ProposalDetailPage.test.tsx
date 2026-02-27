import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ProposalDetailPage } from '@/pages/ProposalDetailPage';
import { mockProposal } from '@/test/mocks';
import * as proposalsService from '@/services/proposals';

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: mockProposal.id }),
    useNavigate: () => mockNavigate,
  };
});

// Mock the proposals service
vi.mock('@/services/proposals', () => ({
  proposalsService: {
    getById: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('ProposalDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('displays loading spinner while fetching', () => {
    vi.mocked(proposalsService.proposalsService.getById).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );
    
    renderWithProviders(<ProposalDetailPage />);
    
    const spinner = document.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('renders proposal details', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockProposal.title)).toBeInTheDocument();
      expect(screen.getByText(mockProposal.summary)).toBeInTheDocument();
      expect(screen.getByText(mockProposal.detailed_description)).toBeInTheDocument();
    });
  });

  it('displays key metrics cards', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Initial Budget')).toBeInTheDocument();
    });
    
    expect(screen.getByText('$500')).toBeInTheDocument();
    expect(screen.getByText('Expected Returns')).toBeInTheDocument();
    expect(screen.getByText('$2,000/mo')).toBeInTheDocument();
    expect(screen.getByText('Risk Level')).toBeInTheDocument();
    
    // Find the risk level value by looking for capitalized risk level text
    const riskLevelValue = screen.getByText((content, element) => {
      return element?.classList.contains('capitalize') && /low/i.test(content);
    });
    expect(riskLevelValue).toBeInTheDocument();
  });

  it('displays status badge', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('PENDING')).toBeInTheDocument();
    });
  });

  it('shows review actions for pending proposals', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Review Actions')).toBeInTheDocument();
      expect(screen.getByText('Approve')).toBeInTheDocument();
      expect(screen.getByText('Review')).toBeInTheDocument();
      expect(screen.getByText('Defer')).toBeInTheDocument();
      expect(screen.getByText('Reject')).toBeInTheDocument();
    });
  });

  it('does not show review actions for approved proposals', async () => {
    const approvedProposal = { ...mockProposal, status: 'approved' as const };
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(approvedProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('APPROVED')).toBeInTheDocument();
    });
    
    expect(screen.queryByText('Review Actions')).not.toBeInTheDocument();
  });

  it('updates status when approve button clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    vi.mocked(proposalsService.proposalsService.update).mockResolvedValue({
      ...mockProposal,
      status: 'approved',
    });
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Approve')).toBeInTheDocument();
    });
    
    const approveButton = screen.getByText('Approve');
    await user.click(approveButton);
    
    expect(proposalsService.proposalsService.update).toHaveBeenCalledWith(
      mockProposal.id,
      { status: 'approved' }
    );
  });

  it('has back button that navigates to proposals list', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Back to Proposals')).toBeInTheDocument();
    });
    
    const backButton = screen.getByText('Back to Proposals');
    await user.click(backButton);
    
    expect(mockNavigate).toHaveBeenCalledWith('/proposals');
  });

  it('opens delete confirmation modal', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Delete')).toBeInTheDocument();
    });
    
    const deleteButton = screen.getByText('Delete');
    await user.click(deleteButton);
    
    await waitFor(() => {
      expect(screen.getByText('Delete Proposal')).toBeInTheDocument();
      expect(screen.getByText(/Are you sure you want to delete/)).toBeInTheDocument();
    });
  });

  it('cancels delete when cancel clicked in modal', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Delete')).toBeInTheDocument();
    });
    
    // Open modal
    const deleteButton = screen.getByText('Delete');
    await user.click(deleteButton);
    
    // Click cancel
    const cancelButton = screen.getByText('Cancel');
    await user.click(cancelButton);
    
    // Modal should close
    await waitFor(() => {
      expect(screen.queryByText('Delete Proposal')).not.toBeInTheDocument();
    });
    
    expect(proposalsService.proposalsService.delete).not.toHaveBeenCalled();
  });

  it('deletes proposal when confirmed', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    vi.mocked(proposalsService.proposalsService.delete).mockResolvedValue();
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockProposal.title)).toBeInTheDocument();
    });
    
    // Click the first Delete button (in header)
    const deleteButtons = screen.getAllByRole('button', { name: /delete/i });
    await user.click(deleteButtons[0]);
    
    // Confirm delete in modal
    await waitFor(() => {
      expect(screen.getByText('Delete Proposal')).toBeInTheDocument();
    });
    
    // Click confirm button in modal
    const modalDeleteButtons = screen.getAllByRole('button', { name: /delete/i });
    const confirmButton = modalDeleteButtons[modalDeleteButtons.length - 1];
    await user.click(confirmButton);
    
    expect(proposalsService.proposalsService.delete).toHaveBeenCalledWith(mockProposal.id);
    expect(mockNavigate).toHaveBeenCalledWith('/proposals');
  });

  it('displays "not found" message for invalid proposal', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(null as any);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Proposal not found')).toBeInTheDocument();
    });
  });

  it('displays all JSON sections correctly', async () => {
    vi.mocked(proposalsService.proposalsService.getById).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Success Criteria')).toBeInTheDocument();
      expect(screen.getByText('Required Tools')).toBeInTheDocument();
      expect(screen.getByText('Required Inputs')).toBeInTheDocument();
      expect(screen.getByText('Implementation Timeline')).toBeInTheDocument();
    });
  });
});
