import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ToolDetailPage } from '@/pages/ToolDetailPage';
import { mockTool, mockAdminUser, mockUser } from '@/test/mocks';
import * as toolsService from '@/services/tools';
import * as conversationsService from '@/services/conversations';
import * as authStore from '@/stores/auth';

// Mock services
vi.mock('@/services/tools', () => ({
  toolsService: {
    getTool: vi.fn(),
    approveTool: vi.fn(),
    rejectTool: vi.fn(),
    updateToolStatus: vi.fn(),
    deleteTool: vi.fn(),
  },
}));

vi.mock('@/services/conversations', () => ({
  conversationsService: {
    getToolUnreadCount: vi.fn(),
    getForTool: vi.fn(),
    getMessages: vi.fn(),
  },
}));

vi.mock('@/stores/auth', () => ({
  useAuthStore: vi.fn((selector) => {
    const state = { user: null };
    return selector ? selector(state) : state;
  }),
}));

// Mock react-router
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useParams: () => ({ id: 'tool-123' }),
    useNavigate: () => mockNavigate,
  };
});

// Mock CodeMirror to avoid DOM issues in tests
vi.mock('@uiw/react-codemirror', () => ({
  default: ({ value, onChange }: any) => (
    <div data-testid="code-mirror">{value}</div>
  ),
}));

describe('ToolDetailPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(conversationsService.conversationsService.getToolUnreadCount).mockResolvedValue(0);
    
    // Mock auth store to return regular user by default
    vi.mocked(authStore.useAuthStore).mockImplementation((selector: any) => {
      const state = { user: mockUser };
      return selector ? selector(state) : state;
    });
  });

  it('displays loading spinner while fetching tool', () => {
    vi.mocked(toolsService.toolsService.getTool).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );
    
    renderWithProviders(<ToolDetailPage />);
    
    const spinner = document.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('displays tool not found message for invalid ID', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(null as any);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Tool not found')).toBeInTheDocument();
    });
  });

  it('renders tool name and description', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
      expect(screen.getByText(mockTool.description)).toBeInTheDocument();
    });
  });

  it('displays tool status badge', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Requested')).toBeInTheDocument();
    });
  });

  it('displays tool category', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('API Integration')).toBeInTheDocument();
    });
  });

  it('displays requester and assigned user', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(/Requested by testuser/)).toBeInTheDocument();
      expect(screen.getByText(/Assigned to Unassigned/)).toBeInTheDocument();
    });
  });

  it('has Back to Tools button that navigates', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
    });
    
    const backButton = screen.getByText('Back to Tools').closest('button');
    expect(backButton).toBeInTheDocument();
    
    await user.click(backButton!);
    expect(mockNavigate).toHaveBeenCalledWith('/tools');
  });

  it('has Edit button', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Edit')).toBeInTheDocument();
    });
  });

  it('has Delete button that shows confirmation modal', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
    });
    
    const deleteButton = screen.getByRole('button', { name: /delete/i });
    await user.click(deleteButton);
    
    await waitFor(() => {
      expect(screen.getByText('Delete Tool')).toBeInTheDocument();
      expect(screen.getByText(/Are you sure you want to delete this tool/)).toBeInTheDocument();
    });
  });

  it('shows Details tab by default', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Details')).toBeInTheDocument();
      expect(screen.getByText('Discussion')).toBeInTheDocument();
    });
    
    const detailsTab = screen.getByText('Details').closest('button');
    expect(detailsTab).toHaveClass('text-neon-cyan');
  });

  it('shows unread badge on Discussion tab when messages are unread', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    vi.mocked(conversationsService.conversationsService.getToolUnreadCount).mockResolvedValue(5);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('5')).toBeInTheDocument();
    });
  });

  it('displays review actions for admin when status is requested', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    vi.mocked(authStore.useAuthStore).mockImplementation((selector: any) => {
      const state = { user: mockAdminUser };
      return selector ? selector(state) : state;
    });
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Review Actions')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument();
    });
  });

  it('does not show review actions for non-admin users', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    vi.mocked(authStore.useAuthStore).mockReturnValue({ user: mockUser } as any);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
    });
    
    expect(screen.queryByText('Review Actions')).not.toBeInTheDocument();
  });

  it('approves tool when approve button is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    vi.mocked(toolsService.toolsService.approveTool).mockResolvedValue({
      ...mockTool,
      status: 'approved',
    });
    vi.mocked(authStore.useAuthStore).mockImplementation((selector: any) => {
      const state = { user: mockAdminUser };
      return selector ? selector(state) : state;
    });
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /approve/i })).toBeInTheDocument();
    });
    
    const approveButton = screen.getByRole('button', { name: /approve/i });
    await user.click(approveButton);
    
    await waitFor(() => {
      expect(toolsService.toolsService.approveTool).toHaveBeenCalledWith('tool-123');
    });
  });

  it('displays tags section when tags are present', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Tags')).toBeInTheDocument();
      expect(screen.getByText('ai')).toBeInTheDocument();
      expect(screen.getByText('nlp')).toBeInTheDocument();
      expect(screen.getByText('generation')).toBeInTheDocument();
    });
  });

  it('displays usage instructions section', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Usage Instructions')).toBeInTheDocument();
    });
  });

  it('displays strengths and weaknesses sections', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Strengths')).toBeInTheDocument();
      expect(screen.getByText(/State-of-the-art language understanding/)).toBeInTheDocument();
      expect(screen.getByText('Weaknesses')).toBeInTheDocument();
      expect(screen.getByText(/Can be expensive at scale/)).toBeInTheDocument();
    });
  });

  it('displays best use cases section', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Best Use Cases')).toBeInTheDocument();
      expect(screen.getByText(/Content generation, chatbots/)).toBeInTheDocument();
    });
  });

  it('displays key metrics cards when data is available', async () => {
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Cost Model')).toBeInTheDocument();
      expect(screen.getByText('Pay per token')).toBeInTheDocument();
      expect(screen.getByText('Integration Complexity')).toBeInTheDocument();
      expect(screen.getByText('low')).toBeInTheDocument();
    });
  });

  it('shows status management buttons for approved tools', async () => {
    const approvedTool = { ...mockTool, status: 'approved' as const };
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(approvedTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Status Management')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /start implementing/i })).toBeInTheDocument();
    });
  });

  it('updates tool status when status button is clicked', async () => {
    const user = userEvent.setup();
    const approvedTool = { ...mockTool, status: 'approved' as const };
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(approvedTool);
    vi.mocked(toolsService.toolsService.updateToolStatus).mockResolvedValue({
      ...approvedTool,
      status: 'implementing',
    });
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /start implementing/i })).toBeInTheDocument();
    });
    
    const implementButton = screen.getByRole('button', { name: /start implementing/i });
    await user.click(implementButton);
    
    await waitFor(() => {
      expect(toolsService.toolsService.updateToolStatus).toHaveBeenCalledWith(
        'tool-123',
        'implementing'
      );
    });
  });

  it('deletes tool when confirmed in modal', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    vi.mocked(toolsService.toolsService.deleteTool).mockResolvedValue(undefined);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
    });
    
    // Open delete modal
    const deleteButton = screen.getByRole('button', { name: /delete/i });
    await user.click(deleteButton);
    
    await waitFor(() => {
      expect(screen.getByText('Delete Tool')).toBeInTheDocument();
    });
    
    // Confirm deletion
    const confirmButton = screen.getAllByRole('button', { name: /delete/i })[1];
    await user.click(confirmButton);
    
    await waitFor(() => {
      expect(toolsService.toolsService.deleteTool).toHaveBeenCalledWith('tool-123');
      expect(mockNavigate).toHaveBeenCalledWith('/tools');
    });
  });

  it('cancels deletion when cancel button is clicked', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.getTool).mockResolvedValue(mockTool);
    
    renderWithProviders(<ToolDetailPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTool.name)).toBeInTheDocument();
    });
    
    // Open delete modal
    const deleteButton = screen.getByRole('button', { name: /delete/i });
    await user.click(deleteButton);
    
    await waitFor(() => {
      expect(screen.getByText('Delete Tool')).toBeInTheDocument();
    });
    
    // Cancel deletion
    const cancelButton = screen.getByRole('button', { name: /cancel/i });
    await user.click(cancelButton);
    
    await waitFor(() => {
      expect(screen.queryByText('Delete Tool')).not.toBeInTheDocument();
    });
    
    expect(toolsService.toolsService.deleteTool).not.toHaveBeenCalled();
  });
});
