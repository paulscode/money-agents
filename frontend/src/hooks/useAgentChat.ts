import { useState, useRef, useCallback, useEffect } from 'react';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';
import type { Proposal } from '@/types';

const isDev = import.meta.env.DEV;

export type AgentMessageType = 'user' | 'agent' | 'system';

export interface AgentMessage {
  id: string;
  type: AgentMessageType;
  content: string;
  isStreaming?: boolean;
  model?: string;
  provider?: string;
  tokens?: number;
  latencyMs?: number;
  timestamp: Date;
}

export interface StreamMetadata {
  model: string;
  provider: string;
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
  latencyMs: number;
}

interface UseAgentChatOptions {
  agentType?: 'proposal-writer';
  conversationId?: string;
  proposalContext?: Proposal;
  onMessage?: (message: AgentMessage) => void;
  onError?: (error: string) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

interface UseAgentChatReturn {
  messages: AgentMessage[];
  isConnected: boolean;
  isConnecting: boolean;
  isStreaming: boolean;
  error: string | null;
  sendMessage: (content: string) => void;
  connect: () => void;
  disconnect: () => void;
  clearMessages: () => void;
}

export function useAgentChat(options: UseAgentChatOptions = {}): UseAgentChatReturn {
  const {
    agentType = 'proposal-writer',
    conversationId,
    proposalContext,
  } = options;

  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  const wsRef = useRef<WebSocket | null>(null);
  const currentStreamingMessageRef = useRef<string>('');
  const streamingMessageIdRef = useRef<string | null>(null);
  
  // Use refs for callbacks to avoid recreating connect/disconnect
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const generateId = () => `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

  const addMessage = useCallback((message: AgentMessage) => {
    setMessages(prev => [...prev, message]);
    optionsRef.current.onMessage?.(message);
  }, []);

  const updateStreamingMessage = useCallback((content: string, metadata?: StreamMetadata) => {
    // Capture the ref value synchronously before the batched updater runs,
    // because the caller may null the ref before React flushes state.
    const targetId = streamingMessageIdRef.current;
    setMessages(prev => {
      const lastIndex = prev.findIndex(m => m.id === targetId);
      if (lastIndex === -1) return prev;
      
      const updated = [...prev];
      updated[lastIndex] = {
        ...updated[lastIndex],
        content,
        isStreaming: !metadata,
        ...(metadata && {
          model: metadata.model,
          provider: metadata.provider,
          tokens: metadata.totalTokens,
          latencyMs: metadata.latencyMs,
        }),
      };
      return updated;
    });
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setIsConnecting(true);
    setError(null);

    const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    if (!token) {
      setError('Not authenticated');
      setIsConnecting(false);
      optionsRef.current.onError?.('Not authenticated');
      return;
    }

    // Build WebSocket URL — RT-07: no token in URL (first-message auth)
    const wsProtocol = API_BASE_URL.startsWith('https') ? 'wss' : 'ws';
    const wsHost = API_BASE_URL.replace(/^https?:\/\//, '');
    const wsUrl = `${wsProtocol}://${wsHost}/api/v1/agents/${agentType}/stream`;

    if (isDev) console.log('[AgentChat] Connecting to:', wsUrl);

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isDev) console.log('[AgentChat] WebSocket connected, sending auth...');
      // RT-07: Send auth as first message instead of query param
      ws.send(JSON.stringify({ type: 'auth', token }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (isDev) console.log('[AgentChat] Received:', data.type);

        switch (data.type) {
          case 'auth_result':
            if (data.success) {
              setIsConnected(true);
              setIsConnecting(false);
              setError(null); // Clear any previous errors on successful auth
              if (isDev) console.log('[AgentChat] Authenticated as user:', data.user_id);
              optionsRef.current.onConnect?.();
            } else {
              setError(data.error || 'Authentication failed');
              setIsConnecting(false);
              optionsRef.current.onError?.(data.error || 'Authentication failed');
              ws.close();
            }
            break;

          case 'chunk':
            currentStreamingMessageRef.current += data.content;
            updateStreamingMessage(currentStreamingMessageRef.current);
            break;

          case 'done':
            const metadata: StreamMetadata = {
              model: data.model,
              provider: data.provider,
              promptTokens: data.prompt_tokens,
              completionTokens: data.completion_tokens,
              totalTokens: data.total_tokens,
              latencyMs: data.latency_ms,
            };
            updateStreamingMessage(currentStreamingMessageRef.current, metadata);
            setIsStreaming(false);
            currentStreamingMessageRef.current = '';
            streamingMessageIdRef.current = null;
            break;

          case 'error':
            setError(data.error);
            setIsStreaming(false);
            optionsRef.current.onError?.(data.error);
            break;

          case 'pong':
            // Keepalive response, ignore
            break;

          default:
            if (isDev) console.warn('[AgentChat] Unknown message type:', data.type);
        }
      } catch (err) {
        if (isDev) console.error('[AgentChat] Failed to parse message:', err);
      }
    };

    ws.onerror = (event) => {
      if (isDev) console.error('[AgentChat] WebSocket error:', event);
      // Only set error if we're not already connected (handles Strict Mode race)
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        setError('Connection error');
        setIsConnecting(false);
        optionsRef.current.onError?.('Connection error');
      }
    };

    ws.onclose = (event) => {
      if (isDev) console.log('[AgentChat] WebSocket closed:', event.code, event.reason);
      // Only update state if this is our current WebSocket
      if (wsRef.current === ws) {
        setIsConnected(false);
        setIsConnecting(false);
        setIsStreaming(false);
        wsRef.current = null;
        optionsRef.current.onDisconnect?.();
      }
    };
  }, [agentType, updateStreamingMessage]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    setIsConnected(false);
    setIsStreaming(false);
  }, []);

  const sendMessage = useCallback((content: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setError('Not connected');
      optionsRef.current.onError?.('Not connected');
      return;
    }

    if (!content.trim()) {
      return;
    }

    // Add user message
    const userMessage: AgentMessage = {
      id: generateId(),
      type: 'user',
      content: content.trim(),
      timestamp: new Date(),
    };
    addMessage(userMessage);

    // Create placeholder for agent response
    const agentMessageId = generateId();
    streamingMessageIdRef.current = agentMessageId;
    currentStreamingMessageRef.current = '';
    
    const agentMessage: AgentMessage = {
      id: agentMessageId,
      type: 'agent',
      content: '',
      isStreaming: true,
      timestamp: new Date(),
    };
    addMessage(agentMessage);
    setIsStreaming(true);

    // Send message to WebSocket
    const payload: Record<string, unknown> = {
      type: 'message',
      content: content.trim(),
      ...(conversationId && { conversation_id: conversationId }),
    };
    
    // Include proposal context if available
    if (proposalContext) {
      payload.proposal_context = {
        id: proposalContext.id,
        title: proposalContext.title,
        summary: proposalContext.summary,
        detailed_description: proposalContext.detailed_description,
        status: proposalContext.status,
        initial_budget: proposalContext.initial_budget,
        expected_returns: proposalContext.expected_returns,
        risk_level: proposalContext.risk_level,
        risk_description: proposalContext.risk_description,
        stop_loss_threshold: proposalContext.stop_loss_threshold,
        success_criteria: proposalContext.success_criteria,
        required_tools: proposalContext.required_tools,
        required_inputs: proposalContext.required_inputs,
        implementation_timeline: proposalContext.implementation_timeline,
      };
    }
    
    if (isDev) console.log('[AgentChat] Sending message:', payload);
    wsRef.current.send(JSON.stringify(payload));
  }, [conversationId, addMessage]);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return {
    messages,
    isConnected,
    isConnecting,
    isStreaming,
    error,
    sendMessage,
    connect,
    disconnect,
    clearMessages,
  };
}
