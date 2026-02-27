import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ToolCreatePage } from '@/pages/ToolCreatePage';
import { mockTool } from '@/test/mocks';
import * as toolsService from '@/services/tools';

// Mock the tools service
vi.mock('@/services/tools', () => ({
  toolsService: {
    createTool: vi.fn(),
  },
}));

// Mock react-router
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

// Mock MDEditor
vi.mock('@uiw/react-md-editor', () => ({
  default: ({ value, onChange }: any) => (
    <textarea
      data-testid="md-editor"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

// Mock CodeMirror to avoid DOM issues in tests
vi.mock('@uiw/react-codemirror', () => ({
  default: ({ value, onChange }: any) => (
    <textarea
      data-testid="code-mirror"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

describe('ToolCreatePage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title', () => {
    renderWithProviders(<ToolCreatePage />);
    
    expect(screen.getByRole('heading', { name: 'Request New Tool' })).toBeInTheDocument();
  });

  it('renders all required form fields', () => {
    renderWithProviders(<ToolCreatePage />);
    
    expect(screen.getByLabelText(/tool name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/category/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/tags/i)).toBeInTheDocument();
  });

  it('renders optional documentation fields', () => {
    renderWithProviders(<ToolCreatePage />);
    
    expect(screen.getByText(/usage instructions/i)).toBeInTheDocument();
    expect(screen.getByText('Strengths')).toBeInTheDocument();
    expect(screen.getByText(/weaknesses/i)).toBeInTheDocument();
    expect(screen.getByText(/best use cases/i)).toBeInTheDocument();
  });

  it('has submit button', () => {
    renderWithProviders(<ToolCreatePage />);
    
    expect(screen.getByRole('button', { name: /submit request/i })).toBeInTheDocument();
  });

  it('has cancel button that navigates back', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ToolCreatePage />);
    
    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    await user.click(cancelButton);
    
    expect(mockNavigate).toHaveBeenCalledWith('/tools');
  });

  it('submits form with valid data', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolCreatePage />);
    
    // Fill out form
    const nameInput = screen.getByLabelText(/tool name/i);
    await user.type(nameInput, mockTool.name);
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), mockTool.description);
    
    // Add tags by pressing Enter after each one
    const tagsInput = screen.getByLabelText(/tags/i);
    await user.type(tagsInput, 'ai');
    await user.keyboard('{Enter}');
    await user.type(tagsInput, 'nlp');
    await user.keyboard('{Enter}');
    
    // Submit
    const submitButton = screen.getByRole('button', { name: /submit request/i });
    await user.click(submitButton);
    
    await waitFor(() => {
      expect(toolsService.toolsService.createTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: mockTool.name,
          slug: 'openai-gpt-4-api',
          category: 'api',
          description: mockTool.description,
          tags: ['ai', 'nlp'],
        })
      );
    });
  });

  it('navigates to tools list after successful creation', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolCreatePage />);
    
    // Fill required fields
    await user.type(screen.getByLabelText(/tool name/i), mockTool.name);
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), mockTool.description);
    
    // Submit
    await user.click(screen.getByRole('button', { name: /submit request/i }));
    
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/tools');
    });
  });

  it('shows validation error for missing required fields', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ToolCreatePage />);
    
    // Try to submit without filling required fields
    const submitButton = screen.getByRole('button', { name: /submit request/i });
    await user.click(submitButton);
    
    // Should not call the service
    expect(toolsService.toolsService.createTool).not.toHaveBeenCalled();
  });

  it('displays all category options', () => {
    renderWithProviders(<ToolCreatePage />);
    
    const categorySelect = screen.getByLabelText(/category/i);
    const options = within(categorySelect).getAllByRole('option');
    
    // Should have 5 categories
    expect(options).toHaveLength(5);
    expect(screen.getByRole('option', { name: /api integration/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /data source/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /automation/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /analysis/i })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /communication/i })).toBeInTheDocument();
  });

  it('allows entering usage instructions in markdown', async () => {
    const user = userEvent.setup();
    renderWithProviders(<ToolCreatePage />);
    
    // There's only one MD editor (usage instructions)
    const usageEditor = screen.getByTestId('md-editor');
    await user.type(usageEditor, '# Usage Instructions\n\nStep 1: Install the tool');
    
    expect(usageEditor).toHaveValue('# Usage Instructions\n\nStep 1: Install the tool');
  });

  it('adds tags when Enter key is pressed', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolCreatePage />);
    
    await user.type(screen.getByLabelText(/tool name/i), 'Test Tool');
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), 'Test description');
    
    // Add tags by pressing Enter after each one
    const tagsInput = screen.getByLabelText(/tags/i);
    await user.type(tagsInput, 'tag1');
    await user.keyboard('{Enter}');
    await user.type(tagsInput, 'tag2');
    await user.keyboard('{Enter}');
    await user.type(tagsInput, 'tag3');
    await user.keyboard('{Enter}');
    
    await user.click(screen.getByRole('button', { name: /submit request/i }));
    
    await waitFor(() => {
      expect(toolsService.toolsService.createTool).toHaveBeenCalledWith(
        expect.objectContaining({
          tags: ['tag1', 'tag2', 'tag3'],
        })
      );
    });
  });

  it('trims whitespace from tags', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolCreatePage />);
    
    await user.type(screen.getByLabelText(/tool name/i), 'Test Tool');
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), 'Test description');
    
    // Tags should be trimmed when added
    const tagsInput = screen.getByLabelText(/tags/i);
    await user.type(tagsInput, '  tag1  ');
    await user.keyboard('{Enter}');
    await user.type(tagsInput, '  tag2  ');
    await user.keyboard('{Enter}');
    
    await user.click(screen.getByRole('button', { name: /submit request/i }));
    
    await waitFor(() => {
      expect(toolsService.toolsService.createTool).toHaveBeenCalledWith(
        expect.objectContaining({
          tags: ['tag1', 'tag2'],
        })
      );
    });
  });

  it('displays error message on submission failure', async () => {
    const user = userEvent.setup();
    const errorMessage = 'Failed to create tool';
    vi.mocked(toolsService.toolsService.createTool).mockRejectedValue(new Error(errorMessage));
    
    renderWithProviders(<ToolCreatePage />);
    
    await user.type(screen.getByLabelText(/tool name/i), 'Test Tool');
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), 'Test description');
    
    await user.click(screen.getByRole('button', { name: /submit request/i }));
    
    await waitFor(() => {
      // Should show error alert with the message
      const errorElements = screen.getAllByText(/failed to create/i);
      expect(errorElements.length).toBeGreaterThan(0);
    });
  });

  it('disables submit button while submitting', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockImplementation(
      () => new Promise((resolve) => setTimeout(() => resolve(mockTool), 100))
    );
    
    renderWithProviders(<ToolCreatePage />);
    
    await user.type(screen.getByLabelText(/tool name/i), 'Test Tool');
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), 'Test description');
    
    const submitButton = screen.getByRole('button', { name: /submit request/i });
    await user.click(submitButton);
    
    expect(submitButton).toBeDisabled();
    
    await waitFor(() => {
      expect(submitButton).not.toBeDisabled();
    });
  });

  it('includes all optional fields in submission when filled', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.createTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolCreatePage />);
    
    // Required fields
    await user.type(screen.getByLabelText(/tool name/i), 'Test Tool');
    await user.selectOptions(screen.getByLabelText(/category/i), 'api');
    await user.type(screen.getByLabelText(/description/i), 'Test description');
    
    // Optional fields - use placeholder text since labels don't have htmlFor
    await user.type(screen.getByPlaceholderText(/advantages of this tool/i), 'Very powerful');
    await user.type(screen.getByPlaceholderText(/limitations or drawbacks/i), 'Expensive');
    await user.type(screen.getByPlaceholderText(/when is this tool most useful/i), 'Large scale projects');
    
    await user.click(screen.getByRole('button', { name: /submit request/i }));
    
    await waitFor(() => {
      expect(toolsService.toolsService.createTool).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'Test Tool',
          category: 'api',
          description: 'Test description',
          strengths: 'Very powerful',
          weaknesses: 'Expensive',
          best_use_cases: 'Large scale projects',
        })
      );
    });
  });
});
