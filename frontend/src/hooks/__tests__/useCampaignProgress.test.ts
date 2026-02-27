/**
 * Tests for useCampaignProgress WebSocket hook.
 *
 * Covers:
 * - Connection lifecycle (connect/disconnect)
 * - Auth result handling
 * - Event type dispatching (initial_state, campaign_status, stream_progress, etc.)
 * - Error handling
 * - Reconnection logic
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

// ---------------------------------------------------------------------------
// Mock WebSocket
// ---------------------------------------------------------------------------

type WSEventHandler = ((event: any) => void) | null;

let mockWsInstance: {
  readyState: number;
  onopen: WSEventHandler;
  onmessage: WSEventHandler;
  onerror: WSEventHandler;
  onclose: WSEventHandler;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
} | null = null;

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  onopen: WSEventHandler = null;
  onmessage: WSEventHandler = null;
  onerror: WSEventHandler = null;
  onclose: WSEventHandler = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    setTimeout(() => this.onclose?.({ code: 1000, reason: 'test' }), 0);
  });

  constructor(_url: string) {
    mockWsInstance = this as any;
    // Simulate async open
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN;
      this.onopen?.({});
    }, 0);
  }
}

// Attach static constants
(MockWebSocket as any).OPEN = 1;
(MockWebSocket as any).CONNECTING = 0;

vi.stubGlobal('WebSocket', MockWebSocket);

// Mock sessionStorage (code uses sessionStorage for auth tokens)
const tokenStore: Record<string, string> = {};
const storageMock = {
  getItem: (key: string) => tokenStore[key] ?? null,
  setItem: (key: string, val: string) => { tokenStore[key] = val; },
  removeItem: (key: string) => { delete tokenStore[key]; },
  clear: () => { Object.keys(tokenStore).forEach(k => delete tokenStore[k]); },
};
vi.stubGlobal('sessionStorage', storageMock);
vi.stubGlobal('localStorage', storageMock);

import { useCampaignProgress } from '@/hooks/useCampaignProgress';

// ---------------------------------------------------------------------------
// Wrapper
// ---------------------------------------------------------------------------

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function simulateMessage(data: Record<string, any>) {
  mockWsInstance?.onmessage?.({ data: JSON.stringify(data) });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useCampaignProgress', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockWsInstance = null;
    tokenStore['money_agents_token'] = 'test-jwt';
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('starts unconnected', () => {
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false }),
      { wrapper: createWrapper() },
    );
    expect(result.current.isConnected).toBe(false);
    expect(result.current.state).toBeNull();
  });

  it('sets error when no token', () => {
    delete tokenStore['money_agents_token'];
    const onError = vi.fn();

    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false, onError }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    expect(result.current.error).toBe('Not authenticated');
  });

  it('connects and authenticates', async () => {
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    // Wait for async WebSocket open
    await act(async () => { vi.advanceTimersByTime(10); });

    // Simulate auth success
    act(() => {
      simulateMessage({ type: 'auth_result', success: true });
    });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.isConnecting).toBe(false);
  });

  it('handles auth failure', async () => {
    const onError = vi.fn();
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false, onError }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });

    act(() => {
      simulateMessage({ type: 'auth_result', success: false, error: 'Invalid token' });
    });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBe('Invalid token');
    expect(onError).toHaveBeenCalledWith('Invalid token');
  });

  it('processes initial_state event', async () => {
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => {
      simulateMessage({
        type: 'initial_state',
        data: {
          status: 'active',
          budget_allocated: 100,
          budget_spent: 25,
          revenue_generated: 10,
          tasks_total: 20,
          tasks_completed: 5,
          current_phase: 'research',
          overall_progress_pct: 25,
          streams: [],
          blocking_inputs: [],
        },
      });
    });

    expect(result.current.state).not.toBeNull();
    expect(result.current.state?.status).toBe('active');
    expect(result.current.state?.overall_progress_pct).toBe(25);
  });

  it('processes campaign_status event', async () => {
    const onStatusChange = vi.fn();
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false, onStatusChange }),
      { wrapper: createWrapper() },
    );

    // Connect and set initial state
    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });
    act(() => {
      simulateMessage({
        type: 'initial_state',
        data: {
          status: 'active', budget_allocated: 0, budget_spent: 0,
          revenue_generated: 0, tasks_total: 0, tasks_completed: 0,
          current_phase: null, overall_progress_pct: 0, streams: [], blocking_inputs: [],
        },
      });
    });

    // Status change
    act(() => {
      simulateMessage({
        type: 'campaign_status',
        data: { old_status: 'active', new_status: 'paused' },
      });
    });

    expect(result.current.state?.status).toBe('paused');
    expect(onStatusChange).toHaveBeenCalledWith('active', 'paused');
  });

  it('processes overall_progress event', async () => {
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });
    act(() => {
      simulateMessage({
        type: 'initial_state',
        data: {
          status: 'active', budget_allocated: 100, budget_spent: 0,
          revenue_generated: 0, tasks_total: 10, tasks_completed: 0,
          current_phase: null, overall_progress_pct: 0, streams: [], blocking_inputs: [],
        },
      });
    });

    act(() => {
      simulateMessage({
        type: 'overall_progress',
        data: {
          overall_progress_pct: 60,
          total_tasks: 10,
          completed_tasks: 6,
          budget_spent: 40,
          revenue_generated: 20,
        },
      });
    });

    expect(result.current.state?.overall_progress_pct).toBe(60);
    expect(result.current.state?.tasks_completed).toBe(6);
  });

  it('calls onTaskCompleted callback', async () => {
    const onTaskCompleted = vi.fn();
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false, onTaskCompleted }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => {
      simulateMessage({
        type: 'task_completed',
        data: { stream_id: 's1', task_name: 'Search' },
      });
    });

    expect(onTaskCompleted).toHaveBeenCalledWith('s1', 'Search');
  });

  it('calls onTaskFailed callback', async () => {
    const onTaskFailed = vi.fn();
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false, onTaskFailed }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => {
      simulateMessage({
        type: 'task_failed',
        data: { stream_id: 's1', task_name: 'Deploy', error: 'Timeout' },
      });
    });

    expect(onTaskFailed).toHaveBeenCalledWith('s1', 'Deploy', 'Timeout');
  });

  it('disconnect clears state', async () => {
    const { result } = renderHook(
      () => useCampaignProgress({ campaignId: 'c1', enabled: false }),
      { wrapper: createWrapper() },
    );

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => { result.current.disconnect(); });

    expect(result.current.isConnected).toBe(false);
  });
});
