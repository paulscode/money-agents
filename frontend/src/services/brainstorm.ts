import apiClient from '@/lib/api-client';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface BrainstormRequest {
  messages: ChatMessage[];
  provider?: string;
  tier: 'fast' | 'reasoning' | 'quality';
  enable_search: boolean;
  timezone?: string;
  temperature?: number;
  max_tokens?: number;
}

export interface ProviderInfo {
  id: string;
  name: string;
  is_configured: boolean;
  models: Record<string, string>;
}

export interface BrainstormConfig {
  providers: ProviderInfo[];
  default_provider: string | null;
  search_enabled: boolean;
}

export interface StreamEvent {
  type: 'content' | 'search' | 'search_complete' | 'idea_captured' | 'task_actions' | 'done' | 'error';
  content?: string;
  query?: string;
  results_preview?: string;
  ideas?: Array<{ id: string; content: string }>;
  actions?: {
    created: Array<{ id: string; title: string }>;
    completed: Array<{ id: string; title: string; notes?: string }>;
    deferred: Array<{ id: string; title: string; until?: string }>;
  };
  model?: string;
  provider?: string;
  tokens?: {
    prompt: number;
    completion: number;
    total: number;
  };
  latency_ms?: number;
  search_performed?: boolean;
  ideas_captured?: number;
  tasks_created?: number;
  tasks_completed?: number;
  error?: string;
}

export const brainstormService = {
  async getConfig(): Promise<BrainstormConfig> {
    const response = await apiClient.get<BrainstormConfig>('/api/v1/brainstorm/config');
    return response.data;
  },

  async *streamChat(request: BrainstormRequest): AsyncGenerator<StreamEvent> {
    const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    const response = await fetch(`${API_BASE_URL}/api/v1/brainstorm/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
      body: JSON.stringify(request),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            yield data as StreamEvent;
          } catch {
            // Ignore parse errors
          }
        }
      }
    }
  },
};
