/**
 * Unit tests for WalletWidget component
 *
 * Tests: auto-hide when LND disabled, loading state, error state, data display.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '@/test/test-utils';
import { WalletWidget } from '@/components/dashboard/WalletWidget';

// Mock wallet service
vi.mock('@/services/wallet', () => ({
  walletService: {
    getConfig: vi.fn(),
    getSummary: vi.fn(),
  },
}));

// Mock useNavigate
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

import { walletService } from '@/services/wallet';

const mockSummary = {
  connected: true,
  node_info: { alias: 'TestNode', identity_pubkey: '03abc...' },
  onchain: { confirmed_balance: 100000 },
  lightning: { local_balance_sat: 200000 },
  pending_channels: null,
  totals: {
    total_balance_sats: 300000,
    onchain_sats: 100000,
    lightning_local_sats: 200000,
    lightning_remote_sats: 50000,
    unconfirmed_sats: 0,
    num_active_channels: 3,
    num_pending_channels: 0,
    synced: true,
  },
};

describe('WalletWidget', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when LND is disabled', async () => {
    vi.mocked(walletService.getConfig).mockResolvedValue({
      enabled: false,
      rest_url_configured: false,
      macaroon_configured: false,
      mempool_url: '',
    });

    const { container } = renderWithProviders(<WalletWidget />);

    await waitFor(() => {
      expect(walletService.getConfig).toHaveBeenCalled();
    });

    // Widget should not render any visible content
    await waitFor(() => {
      expect(container.querySelector('.relative')).toBeNull();
    });
  });

  it('renders wallet data when LND is enabled', async () => {
    vi.mocked(walletService.getConfig).mockResolvedValue({
      enabled: true,
      rest_url_configured: true,
      macaroon_configured: true,
      mempool_url: 'https://mempool.space',
    });
    vi.mocked(walletService.getSummary).mockResolvedValue(mockSummary as any);

    renderWithProviders(<WalletWidget />);

    await waitFor(() => {
      expect(screen.getByText('Wallet')).toBeInTheDocument();
    });

    // Should show node alias
    await waitFor(() => {
      expect(screen.getByText('TestNode')).toBeInTheDocument();
    });
  });

  it('shows channel count from summary', async () => {
    vi.mocked(walletService.getConfig).mockResolvedValue({
      enabled: true,
      rest_url_configured: true,
      macaroon_configured: true,
      mempool_url: '',
    });
    vi.mocked(walletService.getSummary).mockResolvedValue(mockSummary as any);

    renderWithProviders(<WalletWidget />);

    await waitFor(() => {
      expect(screen.getByText('3 ch')).toBeInTheDocument();
    });
  });

  it('shows error state when summary fails', async () => {
    vi.mocked(walletService.getConfig).mockResolvedValue({
      enabled: true,
      rest_url_configured: true,
      macaroon_configured: true,
      mempool_url: '',
    });
    vi.mocked(walletService.getSummary).mockRejectedValue(new Error('Connection refused'));

    renderWithProviders(<WalletWidget />);

    await waitFor(() => {
      expect(screen.getByText('Node offline')).toBeInTheDocument();
    }, { timeout: 5000 });
  });
});
