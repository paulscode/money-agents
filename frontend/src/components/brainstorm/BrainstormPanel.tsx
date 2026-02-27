import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { 
  X, Send, Search, CloudLightning, Zap, Loader2, 
  Settings2, Trash2, AlertCircle, Check, Copy, Lightbulb,
  CheckCircle2, Clock, Plus
} from 'lucide-react';
import { brainstormService, type ChatMessage } from '@/services/brainstorm';
import { getIdeaCounts } from '@/services/ideas';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';

interface TaskAction {
  id: string;
  title: string;
  notes?: string;
  until?: string;
}

interface TaskActions {
  created: TaskAction[];
  completed: TaskAction[];
  deferred: TaskAction[];
}

interface BrainstormPanelProps {
  isOpen: boolean;
  onClose: () => void;
}

interface DisplayMessage extends ChatMessage {
  id: string;
  isStreaming?: boolean;
  isSearching?: boolean;
  searchQuery?: string;
  capturedIdeas?: Array<{ id: string; content: string }>;
  taskActions?: TaskActions;
  metadata?: {
    model?: string;
    provider?: string;
    tokens?: { prompt: number; completion: number; total: number };
    latency_ms?: number;
    search_performed?: boolean;
    ideas_captured?: number;
    tasks_created?: number;
    tasks_completed?: number;
  };
}

// Code block with copy button for markdown rendering
function CodeBlockWithCopy({ children, className }: { children?: React.ReactNode; className?: string }) {
  const [copied, setCopied] = useState(false);
  const codeContent = String(children).replace(/\n$/, '');

  const handleCopy = async () => {
    await navigator.clipboard.writeText(codeContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Detect language from className (e.g., "language-python")
  const language = className?.replace('language-', '') || '';

  return (
    <div className="relative group my-3">
      <div className="flex items-center justify-between px-3 py-1.5 bg-gray-800 border-b border-gray-700 rounded-t-lg">
        <span className="text-xs text-gray-400 font-mono">{language || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-1 text-xs text-gray-400 hover:text-white hover:bg-gray-700 rounded transition-colors"
          title="Copy code"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-green-400" />
              <span className="text-green-400">Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <pre className={`${className} !mt-0 !rounded-t-none bg-gray-900 p-4 overflow-x-auto`}>
        <code className="text-sm">{children}</code>
      </pre>
    </div>
  );
}

export function BrainstormPanel({ isOpen, onClose }: BrainstormPanelProps) {
  const queryClient = useQueryClient();
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Settings
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [selectedTier, setSelectedTier] = useState<'fast' | 'reasoning' | 'quality'>('fast');
  const [enableSearch, setEnableSearch] = useState(true);
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const { data: config } = useQuery({
    queryKey: ['brainstorm-config'],
    queryFn: () => brainstormService.getConfig(),
    enabled: isOpen,
  });

  // Fetch idea counts - refetch every 30 seconds
  const { data: ideaCounts, refetch: refetchIdeaCounts } = useQuery({
    queryKey: ['idea-counts'],
    queryFn: getIdeaCounts,
    enabled: isOpen,
    refetchInterval: 30000,
  });

  // Set default provider when config loads
  useEffect(() => {
    if (config?.default_provider && !selectedProvider) {
      setSelectedProvider(config.default_provider);
    }
  }, [config, selectedProvider]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  }, [isOpen]);

  // Handle escape key
  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen) {
        onClose();
      }
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [isOpen, onClose]);

  const handleSubmit = useCallback(async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage: DisplayMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim(),
    };

    const assistantMessage: DisplayMessage = {
      id: (Date.now() + 1).toString(),
      role: 'assistant',
      content: '',
      isStreaming: true,
    };

    setMessages(prev => [...prev, userMessage, assistantMessage]);
    setInput('');
    setIsLoading(true);
    setError(null);

    try {
      const chatHistory: ChatMessage[] = [
        ...messages.map(m => ({ role: m.role, content: m.content })),
        { role: 'user' as const, content: userMessage.content },
      ];

      for await (const event of brainstormService.streamChat({
        messages: chatHistory,
        provider: selectedProvider || undefined,
        tier: selectedTier,
        enable_search: enableSearch,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      })) {
        if (event.type === 'search') {
          // LLM has decided to search - show searching indicator
          setMessages(prev => 
            prev.map(m => 
              m.id === assistantMessage.id 
                ? { ...m, searchQuery: event.query, isSearching: true } 
                : m
            )
          );
        } else if (event.type === 'search_complete') {
          // Search is done, now LLM will provide final answer
          // Clear the current content to make room for the final response
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, content: '', isSearching: false }
                : m
            )
          );
        } else if (event.type === 'idea_captured') {
          // Idea(s) were captured from the response
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantMessage.id
                ? { 
                    ...m, 
                    capturedIdeas: [
                      ...(m.capturedIdeas || []),
                      ...(event.ideas || [])
                    ]
                  }
                : m
            )
          );
          // Refetch idea counts
          refetchIdeaCounts();
        } else if (event.type === 'task_actions') {
          // Task actions were performed from the response
          const actions = event.actions as TaskActions;
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantMessage.id
                ? { 
                    ...m, 
                    taskActions: {
                      created: [...(m.taskActions?.created || []), ...actions.created],
                      completed: [...(m.taskActions?.completed || []), ...actions.completed],
                      deferred: [...(m.taskActions?.deferred || []), ...actions.deferred],
                    }
                  }
                : m
            )
          );
          // Invalidate task queries to refresh task lists
          queryClient.invalidateQueries({ queryKey: ['tasks'] });
          queryClient.invalidateQueries({ queryKey: ['task-counts'] });
        } else if (event.type === 'content') {
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantMessage.id
                ? { ...m, content: m.content + (event.content || '') }
                : m
            )
          );
        } else if (event.type === 'done') {
          setMessages(prev =>
            prev.map(m =>
              m.id === assistantMessage.id
                ? {
                    ...m,
                    isStreaming: false,
                    isSearching: false,
                    metadata: {
                      model: event.model,
                      provider: event.provider,
                      tokens: event.tokens,
                      latency_ms: event.latency_ms,
                      search_performed: event.search_performed,
                      ideas_captured: event.ideas_captured,
                    },
                  }
                : m
            )
          );
        } else if (event.type === 'error') {
          setError(event.error || 'An error occurred');
          setMessages(prev => prev.filter(m => m.id !== assistantMessage.id));
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send message');
      setMessages(prev => prev.filter(m => m.id !== assistantMessage.id));
    } finally {
      setIsLoading(false);
    }
  }, [input, isLoading, messages, selectedProvider, selectedTier, enableSearch]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const clearHistory = () => {
    setMessages([]);
    setError(null);
  };

  const configuredProviders = config?.providers.filter(p => p.is_configured) || [];

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
      />
      
      {/* Panel */}
      <div className="fixed right-0 top-0 h-full w-full max-w-lg bg-navy-900 border-l border-navy-700 shadow-2xl z-50 flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-navy-700 bg-navy-800/50">
          <div className="flex items-center space-x-3">
            <div className="relative">
              <CloudLightning className="h-6 w-6 text-neon-yellow" />
              <div className="absolute inset-0 blur-md bg-neon-yellow/30 animate-pulse" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">Brainstorm</h2>
              <p className="text-xs text-gray-400">Quick AI assistant</p>
            </div>
            {/* Idea Counts */}
            {ideaCounts && ideaCounts.total > 0 && (
              <div className="flex items-center space-x-2 ml-2 pl-3 border-l border-navy-600">
                {ideaCounts.new > 0 && (
                  <span 
                    className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-neon-cyan/20 text-neon-cyan text-xs font-medium"
                    title={`${ideaCounts.new} new idea${ideaCounts.new !== 1 ? 's' : ''} awaiting review`}
                  >
                    <Lightbulb className="h-3 w-3" />
                    <span>{ideaCounts.new}</span>
                  </span>
                )}
                {ideaCounts.opportunity > 0 && (
                  <span 
                    className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-neon-green/20 text-neon-green text-xs font-medium"
                    title={`${ideaCounts.opportunity} idea${ideaCounts.opportunity !== 1 ? 's' : ''} being processed into opportunities`}
                  >
                    <span>🎯</span>
                    <span>{ideaCounts.opportunity}</span>
                  </span>
                )}
                {ideaCounts.tool > 0 && (
                  <span 
                    className="flex items-center space-x-1 px-2 py-0.5 rounded-full bg-neon-purple/20 text-neon-purple text-xs font-medium"
                    title={`${ideaCounts.tool} tool idea${ideaCounts.tool !== 1 ? 's' : ''} awaiting Tool Scout`}
                  >
                    <span>🛠️</span>
                    <span>{ideaCounts.tool}</span>
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center space-x-2">
            <button
              onClick={() => setShowSettings(!showSettings)}
              className={`p-2 rounded-lg transition-colors ${
                showSettings 
                  ? 'bg-neon-cyan/20 text-neon-cyan' 
                  : 'text-gray-400 hover:text-white hover:bg-navy-700'
              }`}
              title="Settings"
            >
              <Settings2 className="h-5 w-5" />
            </button>
            <button
              onClick={clearHistory}
              className="p-2 text-gray-400 hover:text-white hover:bg-navy-700 rounded-lg transition-colors"
              title="Clear history"
            >
              <Trash2 className="h-5 w-5" />
            </button>
            <button
              onClick={onClose}
              className="p-2 text-gray-400 hover:text-white hover:bg-navy-700 rounded-lg transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Settings Panel */}
        {showSettings && (
          <div className="px-4 py-3 border-b border-navy-700 bg-navy-800/30 space-y-3">
            {/* Provider Selection */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">Provider</label>
              <div className="flex flex-wrap gap-2">
                {configuredProviders.map(provider => (
                  <button
                    key={provider.id}
                    onClick={() => setSelectedProvider(provider.id)}
                    className={`px-3 py-1.5 text-sm rounded-lg border transition-colors ${
                      selectedProvider === provider.id
                        ? 'bg-neon-cyan/20 border-neon-cyan/50 text-neon-cyan'
                        : 'border-navy-600 text-gray-400 hover:border-gray-500'
                    }`}
                  >
                    {provider.name}
                  </button>
                ))}
              </div>
            </div>

            {/* Tier Selection */}
            <div>
              <label className="block text-xs font-medium text-gray-400 mb-1">Model Tier</label>
              <div className="flex gap-2">
                {(['fast', 'reasoning', 'quality'] as const).map(tier => (
                  <button
                    key={tier}
                    onClick={() => setSelectedTier(tier)}
                    className={`px-3 py-1.5 text-sm rounded-lg border transition-colors flex items-center space-x-1 ${
                      selectedTier === tier
                        ? tier === 'fast' 
                          ? 'bg-neon-green/20 border-neon-green/50 text-neon-green'
                          : tier === 'reasoning'
                          ? 'bg-neon-purple/20 border-neon-purple/50 text-neon-purple'
                          : 'bg-neon-yellow/20 border-neon-yellow/50 text-neon-yellow'
                        : 'border-navy-600 text-gray-400 hover:border-gray-500'
                    }`}
                  >
                    <Zap className={`h-3 w-3 ${
                      tier === 'fast' ? '' : tier === 'reasoning' ? 'opacity-70' : 'opacity-50'
                    }`} />
                    <span className="capitalize">{tier}</span>
                  </button>
                ))}
              </div>
              {selectedProvider && config?.providers.find(p => p.id === selectedProvider)?.models[selectedTier] && (
                <p className="text-xs text-gray-500 mt-1">
                  Using: {config.providers.find(p => p.id === selectedProvider)?.models[selectedTier]}
                </p>
              )}
            </div>

            {/* Search Toggle */}
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-2">
                <Search className="h-4 w-4 text-gray-400" />
                <span className="text-sm text-gray-400">Web Search</span>
              </div>
              <button
                onClick={() => setEnableSearch(!enableSearch)}
                disabled={!config?.search_enabled}
                className={`relative w-10 h-5 rounded-full transition-colors ${
                  enableSearch && config?.search_enabled
                    ? 'bg-neon-cyan'
                    : 'bg-navy-600'
                } ${!config?.search_enabled ? 'opacity-50 cursor-not-allowed' : ''}`}
              >
                <div className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
                  enableSearch && config?.search_enabled ? 'translate-x-5' : 'translate-x-0.5'
                }`} />
              </button>
            </div>
            {!config?.search_enabled && (
              <p className="text-xs text-gray-500">Search not available (SERPER_API_KEY not configured)</p>
            )}
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-gray-500 py-12">
              <CloudLightning className="h-12 w-12 mx-auto mb-4 opacity-30" />
              <p className="text-sm">Start a conversation</p>
              <p className="text-xs mt-1">Ask anything, brainstorm ideas, or research topics</p>
            </div>
          )}
          
          {messages.map(message => (
            <div
              key={message.id}
              className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-lg px-4 py-2 ${
                  message.role === 'user'
                    ? 'bg-neon-cyan/20 text-white'
                    : 'bg-navy-800 text-gray-200'
                }`}
              >
                {/* Search indicator */}
                {message.searchQuery && (
                  <div className="flex items-center space-x-2 text-xs text-neon-green mb-2 pb-2 border-b border-navy-600">
                    <Search className="h-3 w-3" />
                    <span>Searched: {message.searchQuery}</span>
                  </div>
                )}
                
                {/* Searching in progress indicator */}
                {message.isSearching && (
                  <div className="flex items-center space-x-2 text-neon-cyan mb-2">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    <span className="text-sm">Searching the web...</span>
                  </div>
                )}
                
                {/* Content */}
                <div data-color-mode="dark" className="prose prose-invert prose-sm max-w-none">
                  {message.content ? (
                    <SanitizedMarkdown source={message.content} />
                  ) : (
                    message.isStreaming && !message.isSearching ? '...' : ''
                  )}
                </div>
                
                {/* Streaming indicator */}
                {message.isStreaming && !message.isSearching && (
                  <div className="flex items-center space-x-1 text-neon-cyan mt-2">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    <span className="text-xs">Thinking...</span>
                  </div>
                )}
                
                {/* Captured Ideas */}
                {message.capturedIdeas && message.capturedIdeas.length > 0 && (
                  <div className="mt-3 pt-2 border-t border-navy-600">
                    <div className="flex items-center space-x-1 text-neon-yellow text-xs mb-1">
                      <Lightbulb className="h-3 w-3" />
                      <span>Ideas captured:</span>
                    </div>
                    <ul className="text-xs text-gray-400 space-y-1">
                      {message.capturedIdeas.map((idea, i) => (
                        <li key={i} className="flex items-start space-x-1">
                          <span className="text-neon-yellow">•</span>
                          <span>{idea.content}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                
                {/* Task Actions */}
                {message.taskActions && (
                  (message.taskActions.created.length > 0 || 
                   message.taskActions.completed.length > 0 || 
                   message.taskActions.deferred.length > 0) && (
                  <div className="mt-3 pt-2 border-t border-navy-600 space-y-2">
                    {/* Created Tasks */}
                    {message.taskActions.created.length > 0 && (
                      <div>
                        <div className="flex items-center space-x-1 text-neon-green text-xs mb-1">
                          <Plus className="h-3 w-3" />
                          <span>Tasks created:</span>
                        </div>
                        <ul className="text-xs text-gray-400 space-y-1">
                          {message.taskActions.created.map((task, i) => (
                            <li key={i} className="flex items-start space-x-1">
                              <span className="text-neon-green">•</span>
                              <span>{task.title}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {/* Completed Tasks */}
                    {message.taskActions.completed.length > 0 && (
                      <div>
                        <div className="flex items-center space-x-1 text-neon-cyan text-xs mb-1">
                          <CheckCircle2 className="h-3 w-3" />
                          <span>Tasks completed:</span>
                        </div>
                        <ul className="text-xs text-gray-400 space-y-1">
                          {message.taskActions.completed.map((task, i) => (
                            <li key={i} className="flex items-start space-x-1">
                              <span className="text-neon-cyan">✓</span>
                              <span>{task.title}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    
                    {/* Deferred Tasks */}
                    {message.taskActions.deferred.length > 0 && (
                      <div>
                        <div className="flex items-center space-x-1 text-neon-yellow text-xs mb-1">
                          <Clock className="h-3 w-3" />
                          <span>Tasks deferred:</span>
                        </div>
                        <ul className="text-xs text-gray-400 space-y-1">
                          {message.taskActions.deferred.map((task, i) => (
                            <li key={i} className="flex items-start space-x-1">
                              <span className="text-neon-yellow">⏰</span>
                              <span>{task.title}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                ))}
                
                {/* Metadata */}
                {message.metadata && !message.isStreaming && (
                  <div className="flex items-center space-x-3 text-xs text-gray-500 mt-2 pt-2 border-t border-navy-600">
                    <span>{message.metadata.provider}/{message.metadata.model}</span>
                    <span>•</span>
                    <span>{message.metadata.tokens?.total} tokens</span>
                    <span>•</span>
                    <span>{(message.metadata.latency_ms || 0) / 1000}s</span>
                  </div>
                )}
              </div>
            </div>
          ))}
          
          {/* Error */}
          {error && (
            <div className="flex items-center space-x-2 text-red-400 bg-red-500/10 rounded-lg px-4 py-2">
              <AlertCircle className="h-4 w-4" />
              <span className="text-sm">{error}</span>
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="p-4 border-t border-navy-700 bg-navy-800/30">
          <form onSubmit={handleSubmit} className="flex items-end space-x-2">
            <div className="flex-1 relative">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask anything..."
                rows={1}
                className="w-full bg-navy-800 border border-navy-600 rounded-lg px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:border-neon-cyan resize-none"
                style={{ minHeight: '48px', maxHeight: '120px' }}
                disabled={isLoading}
              />
            </div>
            <button
              type="submit"
              disabled={!input.trim() || isLoading}
              className="p-3 bg-neon-cyan text-navy-900 rounded-lg hover:bg-neon-cyan/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isLoading ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <Send className="h-5 w-5" />
              )}
            </button>
          </form>
          <p className="text-xs text-gray-500 mt-2 text-center">
            Press Enter to send, Shift+Enter for new line
          </p>
        </div>
      </div>
    </>
  );
}
