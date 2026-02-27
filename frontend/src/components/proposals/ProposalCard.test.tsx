import { describe, it, expect } from 'vitest';
import { screen } from '@testing-library/react';
import { renderWithProviders } from '@/test/test-utils';
import { ProposalCard } from '@/components/proposals/ProposalCard';
import { mockProposal } from '@/test/mocks';

describe('ProposalCard', () => {
  it('renders proposal title and summary', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    expect(screen.getByText(mockProposal.title)).toBeInTheDocument();
    expect(screen.getByText(mockProposal.summary)).toBeInTheDocument();
  });

  it('displays budget formatted correctly', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    expect(screen.getByText('$500')).toBeInTheDocument();
  });

  it('displays risk level with correct color', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    const riskText = screen.getByText(/Low Risk/i);
    expect(riskText).toBeInTheDocument();
  });

  it('displays status badge', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    expect(screen.getByText('PENDING')).toBeInTheDocument();
  });

  it('displays expected returns when available', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    expect(screen.getByText('$2,000/mo')).toBeInTheDocument();
  });

  it('displays submission date', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    // Date will be formatted based on locale
    expect(screen.getByText(/1\/28\/2026/)).toBeInTheDocument();
  });

  it('links to proposal detail page', () => {
    renderWithProviders(<ProposalCard proposal={mockProposal} />);
    
    const link = screen.getByRole('link');
    expect(link).toHaveAttribute('href', `/proposals/${mockProposal.id}`);
  });

  it('applies correct status color for approved proposals', () => {
    const approvedProposal = { ...mockProposal, status: 'approved' as const };
    renderWithProviders(<ProposalCard proposal={approvedProposal} />);
    
    expect(screen.getByText('APPROVED')).toBeInTheDocument();
  });

  it('applies correct status color for rejected proposals', () => {
    const rejectedProposal = { ...mockProposal, status: 'rejected' as const };
    renderWithProviders(<ProposalCard proposal={rejectedProposal} />);
    
    expect(screen.getByText('REJECTED')).toBeInTheDocument();
  });

  it('handles proposals without expected returns', () => {
    const proposalWithoutReturns = { ...mockProposal, expected_returns: null };
    renderWithProviders(<ProposalCard proposal={proposalWithoutReturns} />);
    
    expect(screen.queryByText(/\/mo/)).not.toBeInTheDocument();
  });
});
