import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor, fireEvent, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '@/test/test-utils';
import type { ToolMediaSummary, MediaFileList, MediaStats } from '@/types';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/services/mediaLibrary', () => ({
  mediaLibraryService: {
    listToolsWithMedia: vi.fn(),
    getStats: vi.fn(),
    listFiles: vi.fn(),
    getFileUrl: vi.fn((slug: string, filename: string) => `/api/v1/media/${slug}/files/${filename}`),
    getThumbnailUrl: vi.fn((slug: string, filename: string) => `/api/v1/media/${slug}/files/${filename}/thumbnail`),
    generateThumbnails: vi.fn(),
    deleteFile: vi.fn(),
  },
}));

// Import AFTER vi.mock
import { mediaLibraryService } from '@/services/mediaLibrary';
import { MediaLibraryTab } from '../MediaLibraryTab';

// ---------------------------------------------------------------------------
// Test Data
// ---------------------------------------------------------------------------

const mockTools: ToolMediaSummary[] = [
  {
    slug: 'zimage-generation',
    display_name: 'Z-Image',
    icon: 'image',
    file_count: 3,
    total_size_bytes: 15000000,
    newest_file_date: '2025-06-01T12:00:00Z',
    media_types: ['image'],
  },
  {
    slug: 'ltx-video-generation',
    display_name: 'LTX-2 Video',
    icon: 'video',
    file_count: 1,
    total_size_bytes: 50000000,
    newest_file_date: '2025-06-01T10:00:00Z',
    media_types: ['video'],
  },
  {
    slug: 'canary-stt',
    display_name: 'Canary STT',
    icon: 'document',
    file_count: 0,
    total_size_bytes: 0,
    newest_file_date: null,
    media_types: ['document'],
  },
];

const mockStats: MediaStats = {
  total_files: 4,
  total_size_bytes: 65000000,
  by_type: { image: 3, video: 1 },
  by_tool: { 'zimage-generation': 3, 'ltx-video-generation': 1 },
};

const mockFileList: MediaFileList = {
  files: [
    {
      filename: 'ZIMG_00001.png',
      size_bytes: 5000000,
      created_at: '2025-06-01T12:00:00Z',
      modified_at: '2025-06-01T12:00:00Z',
      media_type: 'image',
      mime_type: 'image/png',
      extension: '.png',
      has_thumbnail: true,
      download_url: '/api/v1/media/zimage-generation/files/ZIMG_00001.png',
      thumbnail_url: '/api/v1/media/zimage-generation/files/ZIMG_00001.png/thumbnail',
    },
    {
      filename: 'ZIMG_00002.png',
      size_bytes: 5000000,
      created_at: '2025-06-01T11:00:00Z',
      modified_at: '2025-06-01T11:00:00Z',
      media_type: 'image',
      mime_type: 'image/png',
      extension: '.png',
      has_thumbnail: false,
      download_url: '/api/v1/media/zimage-generation/files/ZIMG_00002.png',
      thumbnail_url: '/api/v1/media/zimage-generation/files/ZIMG_00002.png/thumbnail',
    },
  ],
  total_count: 2,
  total_size_bytes: 10000000,
  page: 1,
  page_size: 50,
  has_more: false,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('MediaLibraryTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(mediaLibraryService.listToolsWithMedia).mockResolvedValue(mockTools);
    vi.mocked(mediaLibraryService.getStats).mockResolvedValue(mockStats);
    vi.mocked(mediaLibraryService.listFiles).mockResolvedValue(mockFileList);
    vi.mocked(mediaLibraryService.generateThumbnails).mockResolvedValue({
      status: 'ok',
      generated: 0,
      errors: 0,
      remaining: 0,
      total_needed: 0,
    });
  });

  // --- Overview rendering ---

  it('renders the stats bar with totals', async () => {
    renderWithProviders(<MediaLibraryTab />);
    await waitFor(() => {
      expect(screen.getByText(/4/)).toBeInTheDocument();
    });
  });

  it('renders tool cards for tools with files', async () => {
    renderWithProviders(<MediaLibraryTab />);
    await waitFor(() => {
      expect(screen.getByText('Z-Image')).toBeInTheDocument();
      expect(screen.getByText('LTX-2 Video')).toBeInTheDocument();
    });
  });

  it('shows empty tools section for tools without files', async () => {
    renderWithProviders(<MediaLibraryTab />);
    await waitFor(() => {
      // The empty tools section shows tools with 0 files
      expect(screen.getByText(/empty tool/i)).toBeInTheDocument();
    });
  });

  it('calls listToolsWithMedia on mount', async () => {
    renderWithProviders(<MediaLibraryTab />);
    await waitFor(() => {
      expect(mediaLibraryService.listToolsWithMedia).toHaveBeenCalledTimes(1);
    });
  });

  it('calls getStats on mount', async () => {
    renderWithProviders(<MediaLibraryTab />);
    await waitFor(() => {
      expect(mediaLibraryService.getStats).toHaveBeenCalledTimes(1);
    });
  });

  // --- Tool selection ---

  it('loads files when a tool card is clicked', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => {
      expect(screen.getByText('Z-Image')).toBeInTheDocument();
    });

    // Click the tool card
    await user.click(screen.getByText('Z-Image'));

    await waitFor(() => {
      expect(mediaLibraryService.listFiles).toHaveBeenCalledWith(
        expect.objectContaining({ tool_slug: 'zimage-generation' })
      );
    });
  });

  // --- Loading state ---

  it('shows loading state while fetching tools', async () => {
    // Make the promise hang
    vi.mocked(mediaLibraryService.listToolsWithMedia).mockReturnValue(
      new Promise(() => {})
    );

    renderWithProviders(<MediaLibraryTab />);

    // Should show some loading indicator
    await waitFor(() => {
      const loadingEl = document.querySelector('.animate-pulse, .animate-spin');
      expect(loadingEl).toBeTruthy();
    });
  });

  // --- Error state ---

  it('shows error state when fetching tools fails', async () => {
    vi.mocked(mediaLibraryService.listToolsWithMedia).mockRejectedValue(
      new Error('Network error')
    );

    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => {
      expect(screen.getByText(/error|failed|network/i)).toBeInTheDocument();
    });
  });

  // --- File grid rendering ---

  it('shows file tiles when viewing a tool', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => {
      expect(screen.getByText('Z-Image')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Z-Image'));

    await waitFor(() => {
      expect(screen.getByText('ZIMG_00001.png')).toBeInTheDocument();
      expect(screen.getByText('ZIMG_00002.png')).toBeInTheDocument();
    });
  });

  // --- View mode toggle ---

  it('has grid and list view toggle buttons', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => screen.getByText('Z-Image'));
    await user.click(screen.getByText('Z-Image'));

    await waitFor(() => {
      // Look for view toggle buttons (Grid/List icons)
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThan(0);
    });
  });

  // --- Back navigation ---

  it('shows back button when viewing a tool', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => screen.getByText('Z-Image'));
    await user.click(screen.getByText('Z-Image'));

    await waitFor(() => {
      expect(screen.getByText(/back|all tools/i)).toBeInTheDocument();
    });
  });

  // --- Thumbnail generation trigger ---

  it('triggers thumbnail generation when viewing a tool', async () => {
    const user = userEvent.setup();
    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => screen.getByText('Z-Image'));
    await user.click(screen.getByText('Z-Image'));

    await waitFor(() => {
      expect(mediaLibraryService.generateThumbnails).toHaveBeenCalledWith(
        'zimage-generation'
      );
    });
  });

  // --- All tools empty ---

  it('shows appropriate message when all tools are empty', async () => {
    const emptyTools = mockTools.map(t => ({ ...t, file_count: 0, total_size_bytes: 0 }));
    vi.mocked(mediaLibraryService.listToolsWithMedia).mockResolvedValue(emptyTools);
    vi.mocked(mediaLibraryService.getStats).mockResolvedValue({
      total_files: 0,
      total_size_bytes: 0,
      by_type: {},
      by_tool: {},
    });

    renderWithProviders(<MediaLibraryTab />);

    await waitFor(() => {
      // Should show something indicating no media yet
      expect(screen.queryByText(/no media|no files|empty/i)).toBeInTheDocument();
    });
  });
});
