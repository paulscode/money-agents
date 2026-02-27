import { useState, useRef, useCallback, useEffect } from 'react';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';
import { useQueryClient } from '@tanstack/react-query';

const isDev = import.meta.env.DEV;

/**
 * Event types received from the WebSocket
 */
export type CampaignProgressEventType =
  | 'auth_result'
  | 'initial_state'
  | 'campaign_status'
  | 'stream_progress'
  | 'task_completed'
  | 'task_failed'
  | 'input_required'
  | 'input_provided'
  | 'overall_progress'
  | 'pong'
  | 'error';

export interface CampaignProgressEvent {
  type: CampaignProgressEventType;
  campaign_id?: string;
  timestamp?: string;
  data?: Record<string, any>;
  success?: boolean;
  error?: string;
}

export interface StreamProgress {
  stream_id: string;
  stream_name: string;
  tasks_total: number;
  tasks_completed: number;
  tasks_failed: number;
  progress_pct: number;
  status: string;
}

export interface CampaignProgressState {
  status: string;
  budget_allocated: number;
  budget_spent: number;
  revenue_generated: number;
  tasks_total: number;
  tasks_completed: number;
  current_phase: string | null;
  overall_progress_pct: number;
  streams: StreamProgress[];
  blocking_inputs: Array<{
    key: string;
    title: string;
    blocking_count: number;
  }>;
}

interface UseCampaignProgressOptions {
  campaignId: string;
  enabled?: boolean;
  onStatusChange?: (oldStatus: string, newStatus: string) => void;
  onTaskCompleted?: (streamId: string, taskName: string) => void;
  onTaskFailed?: (streamId: string, taskName: string, error: string) => void;
  onInputRequired?: (inputKey: string, title: string) => void;
  onInputProvided?: (inputKey: string, unblockedTasks: number) => void;
  onError?: (error: string) => void;
}

interface UseCampaignProgressReturn {
  state: CampaignProgressState | null;
  isConnected: boolean;
  isConnecting: boolean;
  error: string | null;
  lastEvent: CampaignProgressEvent | null;
  connect: () => void;
  disconnect: () => void;
}

const PING_INTERVAL = 30000; // 30 seconds
const RECONNECT_DELAY = 3000; // 3 seconds
const MAX_RECONNECT_ATTEMPTS = 5;

export function useCampaignProgress(options: UseCampaignProgressOptions): UseCampaignProgressReturn {
  const {
    campaignId,
    enabled = true,
    onStatusChange,
    onTaskCompleted,
    onTaskFailed,
    onInputRequired,
    onInputProvided,
    onError,
  } = options;

  const queryClient = useQueryClient();
  
  const [state, setState] = useState<CampaignProgressState | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastEvent, setLastEvent] = useState<CampaignProgressEvent | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const connectDelayRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);
  const connectionStartTimeRef = useRef<number>(0);

  const clearPingInterval = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
  }, []);

  const clearReconnectTimeout = useCallback(() => {
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }
  }, []);

  const clearConnectDelay = useCallback(() => {
    if (connectDelayRef.current) {
      clearTimeout(connectDelayRef.current);
      connectDelayRef.current = null;
    }
  }, []);

  const disconnect = useCallback(() => {
    clearPingInterval();
    clearReconnectTimeout();
    clearConnectDelay();
    reconnectAttemptRef.current = MAX_RECONNECT_ATTEMPTS; // Prevent reconnect
    
    if (wsRef.current) {
      wsRef.current.close(1000, 'User disconnect');
      wsRef.current = null;
    }
    
    if (mountedRef.current) {
      setIsConnected(false);
      setIsConnecting(false);
    }
  }, [clearPingInterval, clearReconnectTimeout, clearConnectDelay]);

  const connect = useCallback(() => {
    // Don't connect if already connected/connecting or no campaign ID
    if (wsRef.current?.readyState === WebSocket.OPEN || !campaignId) {
      return;
    }

    // Clear any pending reconnect
    clearReconnectTimeout();

    // Get auth token
    const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    if (!token) {
      setError('Not authenticated');
      onError?.('Not authenticated');
      return;
    }

    setIsConnecting(true);
    setError(null);
    reconnectAttemptRef.current = 0;
    connectionStartTimeRef.current = Date.now();

    // Build WebSocket URL — RT-07: no token in URL (first-message auth)
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const apiUrl = new URL(API_BASE_URL);
    const wsUrl = `${wsProtocol}//${apiUrl.host}/api/v1/campaigns/${campaignId}/progress`;

    if (isDev) console.log('[CampaignProgress] Connecting to WebSocket...');
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isDev) console.log('[CampaignProgress] WebSocket connected, sending auth...');
      // RT-07: Send auth as first message instead of query param
      ws.send(JSON.stringify({ type: 'auth', token }));
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;

      try {
        const message: CampaignProgressEvent = JSON.parse(event.data);
        setLastEvent(message);

        switch (message.type) {
          case 'auth_result':
            if (message.success) {
              if (isDev) console.log('[CampaignProgress] Authenticated successfully');
              setIsConnected(true);
              setIsConnecting(false);
              reconnectAttemptRef.current = 0;

              // Start ping interval
              clearPingInterval();
              pingIntervalRef.current = setInterval(() => {
                if (wsRef.current?.readyState === WebSocket.OPEN) {
                  wsRef.current.send(JSON.stringify({ type: 'ping' }));
                }
              }, PING_INTERVAL);
            } else {
              if (isDev) console.error('[CampaignProgress] Auth failed:', message.error);
              setError(message.error || 'Authentication failed');
              setIsConnecting(false);
              onError?.(message.error || 'Authentication failed');
              ws.close();
            }
            break;

          case 'initial_state':
            if (isDev) console.log('[CampaignProgress] Received initial state');
            if (message.data) {
              setState({
                status: message.data.status,
                budget_allocated: message.data.budget_allocated,
                budget_spent: message.data.budget_spent,
                revenue_generated: message.data.revenue_generated,
                tasks_total: message.data.tasks_total,
                tasks_completed: message.data.tasks_completed,
                current_phase: message.data.current_phase,
                overall_progress_pct: message.data.overall_progress_pct,
                streams: message.data.streams || [],
                blocking_inputs: message.data.blocking_inputs || [],
              });
            }
            break;

          case 'campaign_status':
            if (isDev) console.log('[CampaignProgress] Status changed:', message.data);
            if (message.data) {
              setState(prev => prev ? { ...prev, status: message.data!.new_status } : null);
              onStatusChange?.(message.data.old_status, message.data.new_status);
              // Invalidate campaign query to refresh full data
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId] });
            }
            break;

          case 'stream_progress':
            if (isDev) console.log('[CampaignProgress] Stream progress:', message.data);
            if (message.data) {
              setState(prev => {
                if (!prev) return null;
                const streamIndex = prev.streams.findIndex(s => s.stream_id === message.data!.stream_id);
                const updatedStreams = [...prev.streams];
                const streamUpdate: StreamProgress = {
                  stream_id: message.data!.stream_id,
                  stream_name: message.data!.stream_name,
                  tasks_total: message.data!.tasks_total,
                  tasks_completed: message.data!.tasks_completed,
                  tasks_failed: message.data!.tasks_failed,
                  progress_pct: message.data!.progress_pct,
                  status: message.data!.status,
                };
                if (streamIndex >= 0) {
                  updatedStreams[streamIndex] = streamUpdate;
                } else {
                  updatedStreams.push(streamUpdate);
                }
                return { ...prev, streams: updatedStreams };
              });
            }
            break;

          case 'task_completed':
            if (isDev) console.log('[CampaignProgress] Task completed:', message.data);
            if (message.data) {
              onTaskCompleted?.(message.data.stream_id, message.data.task_name);
              // Invalidate streams query
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId, 'streams'] });
            }
            break;

          case 'task_failed':
            if (isDev) console.log('[CampaignProgress] Task failed:', message.data);
            if (message.data) {
              onTaskFailed?.(message.data.stream_id, message.data.task_name, message.data.error);
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId, 'streams'] });
            }
            break;

          case 'input_required':
            if (isDev) console.log('[CampaignProgress] Input required:', message.data);
            if (message.data) {
              onInputRequired?.(message.data.input_key, message.data.title);
              // Invalidate to refresh inputs list
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId] });
            }
            break;

          case 'input_provided':
            if (isDev) console.log('[CampaignProgress] Input provided:', message.data);
            if (message.data) {
              onInputProvided?.(message.data.input_key, message.data.unblocked_tasks);
              // Invalidate to refresh state
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId] });
              queryClient.invalidateQueries({ queryKey: ['campaigns', campaignId, 'streams'] });
            }
            break;

          case 'overall_progress':
            if (isDev) console.log('[CampaignProgress] Overall progress:', message.data);
            if (message.data) {
              setState(prev => prev ? {
                ...prev,
                overall_progress_pct: message.data!.overall_progress_pct,
                tasks_total: message.data!.total_tasks,
                tasks_completed: message.data!.completed_tasks,
                budget_spent: message.data!.budget_spent,
                revenue_generated: message.data!.revenue_generated,
              } : null);
            }
            break;

          case 'pong':
            // Heartbeat response - no action needed
            break;

          case 'error':
            if (isDev) console.error('[CampaignProgress] Server error:', message.error);
            setError(message.error || 'Unknown error');
            onError?.(message.error || 'Unknown error');
            break;

          default:
            if (isDev) console.warn('[CampaignProgress] Unknown message type:', message.type);
        }
      } catch (e) {
        if (isDev) console.error('[CampaignProgress] Failed to parse message:', e);
      }
    };

    ws.onerror = (event) => {
      // Only log errors if connection was established for more than 100ms
      // This suppresses errors from React StrictMode's double-invoke unmount
      const connectionDuration = Date.now() - connectionStartTimeRef.current;
      if (connectionDuration > 100) {
        if (isDev) console.error('[CampaignProgress] WebSocket error:', event);
        if (mountedRef.current) {
          setError('WebSocket connection error');
          onError?.('WebSocket connection error');
        }
      }
    };

    ws.onclose = (event) => {
      // Only log if connection was open for more than 100ms (suppresses StrictMode noise)
      const connectionDuration = Date.now() - connectionStartTimeRef.current;
      if (connectionDuration > 100) {
        if (isDev) console.log('[CampaignProgress] WebSocket closed:', event.code, event.reason);
      }
      clearPingInterval();
      
      if (!mountedRef.current) return;
      
      setIsConnected(false);
      setIsConnecting(false);
      
      // Only attempt reconnect if not a clean close and we haven't exceeded attempts
      if (event.code !== 1000 && reconnectAttemptRef.current < MAX_RECONNECT_ATTEMPTS && enabled) {
        reconnectAttemptRef.current++;
        if (isDev) console.log(`[CampaignProgress] Reconnecting in ${RECONNECT_DELAY}ms (attempt ${reconnectAttemptRef.current}/${MAX_RECONNECT_ATTEMPTS})`);
        reconnectTimeoutRef.current = setTimeout(() => {
          if (mountedRef.current && enabled) {
            connect();
          }
        }, RECONNECT_DELAY);
      }
    };
  }, [campaignId, enabled, clearPingInterval, clearReconnectTimeout, onStatusChange, onTaskCompleted, onTaskFailed, onInputRequired, onInputProvided, onError, queryClient]);

  // Connect/disconnect based on enabled state
  // Use a small delay to avoid React StrictMode double-invoke issues
  useEffect(() => {
    mountedRef.current = true;

    if (enabled && campaignId) {
      // Small delay allows StrictMode's unmount to happen before we connect
      connectDelayRef.current = setTimeout(() => {
        if (mountedRef.current) {
          connect();
        }
      }, 50);
    }

    return () => {
      mountedRef.current = false;
      clearConnectDelay();
      disconnect();
    };
  }, [enabled, campaignId, connect, disconnect, clearConnectDelay]);

  return {
    state,
    isConnected,
    isConnecting,
    error,
    lastEvent,
    connect,
    disconnect,
  };
}
