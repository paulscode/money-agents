/**
 * Tests for useAgentChat WebSocket hook.
 *
 * Covers:
 * - Connection lifecycle (connect/disconnect)
 * - Auth result handling
 * - Sending messages
 * - Streaming chunks and done events
 * - Error handling
 * - clearMessages
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

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
  });

  constructor(_url: string) {
    mockWsInstance = this as any;
    setTimeout(() => {
      this.readyState = MockWebSocket.OPEN;
      this.onopen?.({});
    }, 0);
  }
}

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

import { useAgentChat } from '@/hooks/useAgentChat';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function simulateMessage(data: Record<string, any>) {
  mockWsInstance?.onmessage?.({ data: JSON.stringify(data) });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useAgentChat', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockWsInstance = null;
    tokenStore['money_agents_token'] = 'test-jwt';
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('starts disconnected', () => {
    const { result } = renderHook(() => useAgentChat());
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.messages).toEqual([]);
  });

  it('sets error when no token', () => {
    delete tokenStore['money_agents_token'];
    const onError = vi.fn();

    const { result } = renderHook(() => useAgentChat({ onError }));
    act(() => { result.current.connect(); });

    expect(result.current.error).toBe('Not authenticated');
    expect(onError).toHaveBeenCalledWith('Not authenticated');
  });

  it('connects and authenticates', async () => {
    const onConnect = vi.fn();
    const { result } = renderHook(() => useAgentChat({ onConnect }));

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });

    act(() => {
      simulateMessage({ type: 'auth_result', success: true, user_id: 'u1' });
    });

    expect(result.current.isConnected).toBe(true);
    expect(result.current.isConnecting).toBe(false);
    expect(onConnect).toHaveBeenCalled();
  });

  it('handles auth failure', async () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useAgentChat({ onError }));

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });

    act(() => {
      simulateMessage({ type: 'auth_result', success: false, error: 'Bad token' });
    });

    expect(result.current.isConnected).toBe(false);
    expect(result.current.error).toBe('Bad token');
  });

  it('sends message and creates agent placeholder', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => { result.current.sendMessage('Hello agent'); });

    // Should have 2 messages: user + agent placeholder
    expect(result.current.messages).toHaveLength(2);
    expect(result.current.messages[0].type).toBe('user');
    expect(result.current.messages[0].content).toBe('Hello agent');
    expect(result.current.messages[1].type).toBe('agent');
    expect(result.current.messages[1].isStreaming).toBe(true);
    expect(result.current.isStreaming).toBe(true);

    // Verify WebSocket send (call 0 = auth message, call 1 = user message)
    expect(mockWsInstance?.send).toHaveBeenCalledTimes(2);
    const payload = JSON.parse(mockWsInstance!.send.mock.calls[1][0]);
    expect(payload.type).toBe('message');
    expect(payload.content).toBe('Hello agent');
  });

  it('accumulates streaming chunks', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });
    act(() => { result.current.sendMessage('Hello'); });

    // Stream chunks
    act(() => { simulateMessage({ type: 'chunk', content: 'Hi ' }); });
    act(() => { simulateMessage({ type: 'chunk', content: 'there!' }); });

    const agentMsg = result.current.messages[1];
    expect(agentMsg.content).toBe('Hi there!');
    expect(agentMsg.isStreaming).toBe(true);
  });

  it('finalizes on done event with metadata', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });
    act(() => { result.current.sendMessage('Hello'); });
    act(() => { simulateMessage({ type: 'chunk', content: 'Response' }); });

    // Verify chunk was applied
    expect(result.current.messages[1].content).toBe('Response');
    expect(result.current.isStreaming).toBe(true);

    act(() => {
      simulateMessage({
        type: 'done',
        model: 'gpt-4',
        provider: 'openai',
        prompt_tokens: 100,
        completion_tokens: 50,
        total_tokens: 150,
        latency_ms: 1200,
      });
    });

    // Hook-level streaming flag must be cleared
    expect(result.current.isStreaming).toBe(false);
    // Messages should still be intact
    expect(result.current.messages).toHaveLength(2);

    // Message-level metadata should be applied
    const agentMsg = result.current.messages[1];
    expect(agentMsg.isStreaming).toBeFalsy();
    expect(agentMsg.model).toBe('gpt-4');
    expect(agentMsg.tokens).toBe(150);
  });

  it('does not send empty messages', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => { result.current.sendMessage('   '); });
    expect(result.current.messages).toHaveLength(0);
  });

  it('sets error when sending while disconnected', () => {
    const onError = vi.fn();
    const { result } = renderHook(() => useAgentChat({ onError }));

    act(() => { result.current.sendMessage('Hello'); });
    expect(result.current.error).toBe('Not connected');
    expect(onError).toHaveBeenCalledWith('Not connected');
  });

  it('clearMessages resets messages and error', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });
    act(() => { result.current.sendMessage('Hello'); });

    expect(result.current.messages.length).toBeGreaterThan(0);

    act(() => { result.current.clearMessages(); });
    expect(result.current.messages).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it('disconnect clears connection state', async () => {
    const { result } = renderHook(() => useAgentChat());

    act(() => { result.current.connect(); });
    await act(async () => { vi.advanceTimersByTime(10); });
    act(() => { simulateMessage({ type: 'auth_result', success: true }); });

    act(() => { result.current.disconnect(); });
    expect(result.current.isConnected).toBe(false);
    expect(result.current.isStreaming).toBe(false);
  });
});
