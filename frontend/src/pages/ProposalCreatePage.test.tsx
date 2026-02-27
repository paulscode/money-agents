import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ProposalCreatePage } from '@/pages/ProposalCreatePage';
import { mockProposal } from '@/test/mocks';
import * as proposalsService from '@/services/proposals';

// Mock react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock the proposals service
vi.mock('@/services/proposals', () => ({
  proposalsService: {
    create: vi.fn(),
  },
}));

// Mock MDEditor to avoid rendering issues in tests
vi.mock('@uiw/react-md-editor', () => ({
  default: ({ value, onChange }: any) => (
    <textarea
      data-testid="md-editor"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
  Markdown: ({ source }: any) => <div>{source}</div>,
}));

// Mock CodeMirror for JSON fields - use counter to track field order
let codeMirrorCounter = 0;
const fieldNames = [
  'Recurring Costs', // 1st CodeMirror
  'Expected Returns', // 2nd CodeMirror
  'Stop Loss Threshold', // 3rd CodeMirror
  'Success Criteria', // 4th CodeMirror
  'Required Tools', // 5th CodeMirror
  'Required Inputs', // 6th CodeMirror
  'Implementation Timeline', // 7th CodeMirror
  'Tags', // 8th CodeMirror
];

vi.mock('@uiw/react-codemirror', () => ({
  default: ({ value, onChange }: any) => {
    const fieldIndex = codeMirrorCounter++;
    const ariaLabel = fieldNames[fieldIndex % fieldNames.length] || 'JSON Field';
    
    return (
      <textarea
        data-testid="code-mirror"
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        aria-label={ariaLabel}
      />
    );
  },
}));

describe('ProposalCreatePage', () => {
  beforeEach(() => {
    codeMirrorCounter = 0; // Reset counter before each test
    vi.clearAllMocks();
  });

  it('renders the form with all sections', () => {
    renderWithProviders(<ProposalCreatePage />);
    
    expect(screen.getByText('Create New Proposal')).toBeInTheDocument();
    expect(screen.getByText('Basic Information')).toBeInTheDocument();
    expect(screen.getByText('Financial Details')).toBeInTheDocument();
    expect(screen.getByText('Risk Assessment')).toBeInTheDocument();
    expect(screen.getByText('Success Criteria')).toBeInTheDocument();
    expect(screen.getByText('Requirements')).toBeInTheDocument();
    expect(screen.getByText('Implementation Timeline')).toBeInTheDocument();
  });

  it('has back button that navigates to proposals list', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProposalCreatePage />);
    
    const backButton = screen.getByText('Back to Proposals');
    await user.click(backButton);
    
    expect(mockNavigate).toHaveBeenCalledWith('/proposals');
  });

  it('has cancel button that navigates to proposals list', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProposalCreatePage />);
    
    const cancelButton = screen.getByText('Cancel');
    await user.click(cancelButton);
    
    expect(mockNavigate).toHaveBeenCalledWith('/proposals');
  });

  it('displays all required form fields', () => {
    renderWithProviders(<ProposalCreatePage />);
    
    expect(screen.getByLabelText(/Title/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Summary/)).toBeInTheDocument();
    expect(screen.getByText(/Detailed Description/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Initial Budget/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Risk Level/)).toBeInTheDocument();
    expect(screen.getByText(/Risk Description/)).toBeInTheDocument();
  });

  it('has pre-filled JSON examples', () => {
    renderWithProviders(<ProposalCreatePage />);
    
    const stopLossField = screen.getByLabelText(/Stop Loss Threshold/) as HTMLTextAreaElement;
    expect(stopLossField.value).toContain('max_loss');
    
    const successCriteriaField = screen.getByLabelText(/Success Criteria/) as HTMLTextAreaElement;
    expect(successCriteriaField.value).toContain('min_revenue');
    
    const requiredToolsField = screen.getByLabelText(/Required Tools/) as HTMLTextAreaElement;
    expect(requiredToolsField.value).toContain('openai');
  });

  it('validates required fields', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProposalCreatePage />);
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    // Form should not submit (service not called)
    expect(proposalsService.proposalsService.create).not.toHaveBeenCalled();
  });

  it('submits form with valid data', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields
    await user.type(screen.getByLabelText(/Title/), 'Test Proposal');
    await user.type(screen.getByLabelText(/Summary/), 'Test summary');
    
    // Fill markdown editors - there are 2 editors (detailed_description and risk_description)
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test detailed description' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test risk description' } });
    
    // Risk level should have a default value
    const riskLevelSelect = screen.getByLabelText(/Risk Level/);
    expect(riskLevelSelect).toHaveValue('medium');
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    await waitFor(() => {
      expect(proposalsService.proposalsService.create).toHaveBeenCalled();
      expect(mockNavigate).toHaveBeenCalledWith(`/proposals/${mockProposal.id}`);
    });
  });

  it('shows error for invalid JSON', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields
    await user.type(screen.getByLabelText(/Title/), 'Test Proposal');
    await user.type(screen.getByLabelText(/Summary/), 'Test summary');
    
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test detailed description' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test risk description' } });
    
    // Clear and enter invalid JSON
    const stopLossField = screen.getByLabelText(/Stop Loss Threshold/) as HTMLTextAreaElement;
    await user.clear(stopLossField);
    fireEvent.input(stopLossField, { target: { value: '{invalid json' } });
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    // Should show custom Alert component with specific field name
    await waitFor(() => {
      expect(screen.getByText('Invalid JSON')).toBeInTheDocument();
    });
    
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/field contains invalid JSON/)).toBeInTheDocument();
    expect(proposalsService.proposalsService.create).not.toHaveBeenCalled();
  });

  it('allows selection of different risk levels', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ProposalCreatePage />);
    
    const riskLevelSelect = screen.getByLabelText(/Risk Level/);
    
    await user.selectOptions(riskLevelSelect, 'low');
    expect(riskLevelSelect).toHaveValue('low');
    
    await user.selectOptions(riskLevelSelect, 'high');
    expect(riskLevelSelect).toHaveValue('high');
  });

  it('displays loading state during submission', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields quickly
    await user.type(screen.getByLabelText(/Title/), 'Test');
    await user.type(screen.getByLabelText(/Summary/), 'Test');
    
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test' } });
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    await waitFor(() => {
      expect(screen.getByText('Creating...')).toBeInTheDocument();
    });
  });

  it('handles optional JSON fields correctly', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields
    await user.type(screen.getByLabelText(/Title/), 'Test Proposal');
    await user.type(screen.getByLabelText(/Summary/), 'Test summary');
    
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test detailed description' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test risk description' } });
    
    // Add optional expected returns
    const expectedReturnsField = screen.getByLabelText(/Expected Returns/) as HTMLTextAreaElement;
    fireEvent.input(expectedReturnsField, { target: { value: '{"monthly": 1000}' } });
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    await waitFor(() => {
      expect(proposalsService.proposalsService.create).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Test Proposal',
          expected_returns: { monthly: 1000 },
        })
      );
    });
  });

  it('leaves empty optional JSON fields as undefined', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields only
    await user.type(screen.getByLabelText(/Title/), 'Test Proposal');
    await user.type(screen.getByLabelText(/Summary/), 'Test summary');
    
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test detailed description' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test risk description' } });
    
    // Clear optional fields
    const expectedReturnsField = screen.getByLabelText(/Expected Returns/);
    await user.clear(expectedReturnsField);
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    await waitFor(() => {
      const callArg = vi.mocked(proposalsService.proposalsService.create).mock.calls[0][0];
      expect(callArg.expected_returns).toBeUndefined();
    });
  });

  it('shows specific field name in error for invalid optional JSON', async () => {
    const user = userEvent.setup();
    vi.mocked(proposalsService.proposalsService.create).mockResolvedValue(mockProposal);
    
    renderWithProviders(<ProposalCreatePage />);
    
    // Fill required fields
    await user.type(screen.getByLabelText(/Title/), 'Test Proposal');
    await user.type(screen.getByLabelText(/Summary/), 'Test summary');
    
    const mdEditors = screen.getAllByTestId('md-editor');
    fireEvent.change(mdEditors[0], { target: { value: 'Test detailed description' } });
    
    await user.type(screen.getByLabelText(/Initial Budget/), '500');
    
    fireEvent.change(mdEditors[1], { target: { value: 'Test risk description' } });
    
    // Add invalid JSON to optional Expected Returns field
    const expectedReturnsField = screen.getByLabelText(/Expected Returns/) as HTMLTextAreaElement;
    fireEvent.input(expectedReturnsField, { target: { value: 'not valid json at all' } });
    
    const submitButton = screen.getByText('Create Proposal');
    await user.click(submitButton);
    
    // Should show error with Expected Returns field name
    await waitFor(() => {
      expect(screen.getByText('Invalid JSON')).toBeInTheDocument();
    });
    
    expect(screen.getByText(/Expected Returns.*field contains invalid JSON/)).toBeInTheDocument();
    expect(proposalsService.proposalsService.create).not.toHaveBeenCalled();
  });

});
