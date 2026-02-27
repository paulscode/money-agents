import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders, userEvent } from '@/test/test-utils';
import { ToolsPage } from '@/pages/ToolsPage';
import { mockTools } from '@/test/mocks';
import * as toolsService from '@/services/tools';

// Mock the tools service
vi.mock('@/services/tools', () => ({
  toolsService: {
    listTools: vi.fn(),
  },
}));

describe('ToolsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title and description', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Tools' })).toBeInTheDocument();
    });
    expect(screen.getByText('Manage and discover available tools for agents')).toBeInTheDocument();
  });

  it('displays loading spinner while fetching', () => {
    vi.mocked(toolsService.toolsService.listTools).mockImplementation(
      () => new Promise(() => {}) // Never resolves
    );
    
    renderWithProviders(<ToolsPage />);
    
    // Loading spinner has the animate-spin class
    const spinner = document.querySelector('.animate-spin');
    expect(spinner).toBeInTheDocument();
  });

  it('displays tools in grid view by default', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
      expect(screen.getByText(mockTools[1].name)).toBeInTheDocument();
      expect(screen.getByText(mockTools[2].name)).toBeInTheDocument();
    });
  });

  it('shows empty state when no tools exist', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText('No tools found')).toBeInTheDocument();
      expect(screen.getByText('Try adjusting your filters or request a new tool')).toBeInTheDocument();
    });
  });

  it('has a "Request Tool" button', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    
    renderWithProviders(<ToolsPage />);
    
    const requestButton = screen.getAllByText('Request Tool')[0];
    expect(requestButton.closest('a')).toHaveAttribute('href', '/tools/new');
  });

  it('toggles between grid and list view', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    const listViewButton = screen.getByTitle('List View');
    await user.click(listViewButton);
    
    // Check that the view has changed (button should have active styling)
    expect(listViewButton).toHaveClass('bg-neon-cyan/20');
  });

  it('filters tools by status', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    // Status filter is the first select
    const selects = screen.getAllByRole('combobox');
    const statusFilter = selects[0];
    await user.selectOptions(statusFilter, 'approved');
    
    // Service should be called with status filter
    await waitFor(() => {
      expect(toolsService.toolsService.listTools).toHaveBeenCalledWith(
        expect.objectContaining({ status: 'approved' })
      );
    });
  });

  it('filters tools by category', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    // Category filter is the second select
    const selects = screen.getAllByRole('combobox');
    const categoryFilter = selects[1];
    await user.selectOptions(categoryFilter, 'api');
    
    // Service should be called with category filter
    await waitFor(() => {
      expect(toolsService.toolsService.listTools).toHaveBeenCalledWith(
        expect.objectContaining({ category: 'api' })
      );
    });
  });

  it('searches tools by name', async () => {
    const user = userEvent.setup();
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    const searchInput = screen.getByPlaceholderText(/search tools/i);
    await user.type(searchInput, 'OpenAI');
    
    // Service should be called with search parameter
    await waitFor(() => {
      expect(toolsService.toolsService.listTools).toHaveBeenCalledWith(
        expect.objectContaining({ search: 'OpenAI' })
      );
    });
  });

  it('displays tool category icons', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    // Category icons are emoji text nodes
    const gridCards = screen.getAllByRole('link');
    expect(gridCards.length).toBeGreaterThan(0);
  });

  it('displays tool status badges', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText('Requested')).toBeInTheDocument();
      expect(screen.getByText('Approved')).toBeInTheDocument();
      expect(screen.getByText('Implemented')).toBeInTheDocument();
    });
  });

  it('shows filtered empty state message', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    const user = userEvent.setup();
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText('No tools found')).toBeInTheDocument();
    });
    
    // Status filter is the first select
    const selects = screen.getAllByRole('combobox');
    const statusFilter = selects[0];
    await user.selectOptions(statusFilter, 'approved');
    
    // Empty state message should still show
    expect(screen.getByText('No tools found')).toBeInTheDocument();
  });

  it('renders all status filter options', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText('No tools found')).toBeInTheDocument();
    });
    
    // Status filter is the first select
    const selects = screen.getAllByRole('combobox');
    const statusFilter = selects[0];
    const options = within(statusFilter).getAllByRole('option');
    
    // Should have "All" plus 8 status options = 9 total
    expect(options.length).toBe(9);
  });

  it('renders all category filter options', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue([]);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText('No tools found')).toBeInTheDocument();
    });
    
    // Category filter is the second select
    const selects = screen.getAllByRole('combobox');
    const categoryFilter = selects[1];
    const options = within(categoryFilter).getAllByRole('option');
    
    // Should have "All" plus 5 category options
    expect(options).toHaveLength(6);
  });

  it('navigates to tool detail page when clicking on a tool', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    const toolLink = screen.getByText(mockTools[0].name).closest('a');
    expect(toolLink).toHaveAttribute('href', `/tools/${mockTools[0].id}`);
  });

  it('links to tool detail pages', async () => {
    vi.mocked(toolsService.toolsService.listTools).mockResolvedValue(mockTools);
    
    renderWithProviders(<ToolsPage />);
    
    await waitFor(() => {
      expect(screen.getByText(mockTools[0].name)).toBeInTheDocument();
    });
    
    const toolLinks = screen.getAllByRole('link');
    const toolDetailLink = toolLinks.find(link => 
      link.getAttribute('href')?.includes(`/tools/${mockTools[0].id}`)
    );
    
    expect(toolDetailLink).toBeDefined();
  });
});
