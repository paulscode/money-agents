import { useState, useRef, useEffect } from 'react';
import { Bot, User, Send, Loader2, Wifi, WifiOff, Zap, Trash2 } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';
import { SanitizedMarkdown } from '@/components/common/SanitizedMarkdown';
import { useAgentChat, type AgentMessage } from '@/hooks/useAgentChat';
import type { Proposal } from '@/types';
import { logError } from '@/lib/logger';

const isDev = import.meta.env.DEV;

interface AgentChatPanelProps {
  conversationId?: string;
  proposal?: Proposal;
  onClose?: () => void;
}

function AgentMessageBubble({ message }: { message: AgentMessage }) {
  const isUser = message.type === 'user';
  const isAgent = message.type === 'agent';
  
  const alignmentClass = isUser ? 'justify-end' : 'justify-start';
  const bubbleClass = isUser
    ? 'bg-gradient-to-br from-neon-purple/20 to-neon-pink/20 border-neon-purple/40'
    : 'bg-gradient-to-br from-neon-cyan/10 to-neon-blue/10 border-neon-cyan/40';

  const iconColor = isUser ? 'text-neon-purple' : 'text-neon-cyan';
  const Icon = isUser ? User : Bot;
  const displayName = isUser ? 'You' : 'AI Agent';

  return (
    <div className={`flex ${alignmentClass} mb-4 group`}>
      <div className={`max-w-[85%] flex ${isUser ? 'flex-row-reverse' : 'flex-row'} gap-3`}>
        {/* Avatar */}
        <div className={`flex-shrink-0 w-8 h-8 rounded-full ${
          isUser
            ? 'bg-gradient-to-br from-neon-purple/30 to-neon-pink/30 border border-neon-purple/50' 
            : 'bg-gradient-to-br from-neon-cyan/30 to-neon-blue/30 border border-neon-cyan/50'
        } flex items-center justify-center`}>
          <Icon className={`h-4 w-4 ${iconColor}`} />
        </div>

        {/* Message Content */}
        <div className="flex flex-col gap-1 min-w-0">
          {/* Sender Label & Timestamp */}
          <div className={`flex items-center gap-2 text-xs ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
            <span className={`font-medium ${iconColor}`}>
              {displayName}
            </span>
            <span className="text-gray-500">
              {formatDistanceToNow(message.timestamp, { addSuffix: true })}
            </span>
            {message.model && (
              <span className="text-gray-600 text-[10px] px-1.5 py-0.5 rounded bg-gray-800/50">
                {message.model}
              </span>
            )}
            {message.tokens && (
              <span className="text-gray-600 text-[10px] px-1.5 py-0.5 rounded bg-gray-800/50">
                {message.tokens.toLocaleString()} tokens
              </span>
            )}
          </div>

          {/* Message Bubble */}
          <div className={`
            rounded-lg border px-4 py-3 
            ${bubbleClass}
            shadow-lg
            transition-all duration-200
            group-hover:shadow-xl
            ${isAgent ? 'group-hover:shadow-neon-cyan/20' : 'group-hover:shadow-neon-purple/20'}
          `}>
            {message.isStreaming && !message.content ? (
              <div className="flex items-center gap-2 text-neon-cyan">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="text-sm">Thinking...</span>
              </div>
            ) : (
              <div data-color-mode="dark" className="prose prose-invert prose-sm max-w-none">
                <SanitizedMarkdown source={message.content} />
                {message.isStreaming && (
                  <span className="inline-block w-2 h-4 bg-neon-cyan animate-pulse ml-1" />
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function AgentChatPanel({ conversationId, proposal, onClose }: AgentChatPanelProps) {
  const [inputText, setInputText] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const {
    messages,
    isConnected,
    isConnecting,
    isStreaming,
    error,
    sendMessage,
    connect,
    disconnect,
    clearMessages,
  } = useAgentChat({
    agentType: 'proposal-writer',
    conversationId,
    proposalContext: proposal,
    onConnect: () => {
      if (isDev) console.log('Connected to agent');
    },
    onError: (err) => {
      logError('Agent error:', err);
    },
  });

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-connect on mount (only once)
  useEffect(() => {
    connect();
    return () => disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Empty deps - only run on mount/unmount

  const handleSend = () => {
    if (!inputText.trim() || isStreaming) return;
    sendMessage(inputText);
    setInputText('');
    
    // Reset textarea height
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInputText(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${e.target.scrollHeight}px`;
  };

  return (
    <div className="flex flex-col h-full bg-gradient-to-b from-gray-900/50 to-gray-800/30 rounded-lg border border-gray-700/50">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-700/50 bg-gray-800/30">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-gradient-to-br from-neon-cyan/30 to-neon-blue/30 border border-neon-cyan/50 flex items-center justify-center">
            <Bot className="h-4 w-4 text-neon-cyan" />
          </div>
          <div>
            <h3 className="text-sm font-medium text-white">Proposal Writer Agent</h3>
            <div className="flex items-center gap-2">
              {isConnecting ? (
                <span className="text-xs text-yellow-400 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Connecting...
                </span>
              ) : isConnected ? (
                <span className="text-xs text-green-400 flex items-center gap-1">
                  <Wifi className="h-3 w-3" />
                  Connected
                </span>
              ) : (
                <span className="text-xs text-red-400 flex items-center gap-1">
                  <WifiOff className="h-3 w-3" />
                  Disconnected
                </span>
              )}
            </div>
          </div>
        </div>
        
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button
              onClick={clearMessages}
              className="p-2 text-gray-400 hover:text-white hover:bg-gray-700/50 rounded-lg transition-colors"
              title="Clear chat"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
          {!isConnected && !isConnecting && (
            <button
              onClick={connect}
              className="px-3 py-1.5 text-xs bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30 rounded hover:bg-neon-cyan/30 transition-colors"
            >
              Reconnect
            </button>
          )}
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-16 h-16 rounded-full bg-gradient-to-br from-neon-cyan/20 to-neon-blue/20 border border-neon-cyan/30 flex items-center justify-center mb-4">
              <Zap className="h-8 w-8 text-neon-cyan" />
            </div>
            <h3 className="text-lg font-medium text-white mb-2">
              {proposal ? `Discuss "${proposal.title}"` : 'Chat with the AI Agent'}
            </h3>
            <p className="text-gray-400 text-sm max-w-sm">
              {proposal 
                ? "Ask questions, get suggestions, or have the agent help refine this proposal."
                : "Ask questions, get suggestions, or have the agent help refine your proposal. Responses stream in real-time."
              }
            </p>
            
            {/* Quick Start Suggestions */}
            <div className="mt-6 space-y-2 w-full max-w-md">
              <p className="text-xs text-gray-500 uppercase tracking-wider">Try asking:</p>
              {(proposal ? [
                "What are the main risks and how can I mitigate them?",
                "Is the budget realistic for this opportunity?",
                "What's missing from this proposal?",
              ] : [
                "What makes a good proposal?",
                "Help me brainstorm campaign ideas",
                "What tools are available?",
              ]).map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => {
                    if (isConnected && !isStreaming) {
                      sendMessage(suggestion);
                    }
                  }}
                  disabled={!isConnected || isStreaming}
                  className="w-full text-left px-4 py-2 text-sm text-gray-300 bg-gray-800/50 rounded-lg border border-gray-700/50 hover:border-neon-cyan/30 hover:bg-gray-800 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <AgentMessageBubble key={msg.id} message={msg} />
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Area */}
      <div className="p-4 border-t border-gray-700/50 bg-gray-800/30">
        <div className="flex items-end gap-3">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={inputText}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder={isConnected ? "Ask the AI agent anything..." : "Connecting..."}
              disabled={!isConnected || isStreaming}
              rows={1}
              className="w-full px-4 py-3 bg-gray-900/50 border border-gray-700/50 rounded-lg text-white placeholder-gray-500 resize-none focus:outline-none focus:border-neon-cyan/50 focus:ring-1 focus:ring-neon-cyan/30 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              style={{ maxHeight: '150px' }}
            />
          </div>
          <button
            onClick={handleSend}
            disabled={!isConnected || isStreaming || !inputText.trim()}
            className="px-4 py-3 bg-gradient-to-r from-neon-cyan/20 to-neon-blue/20 text-neon-cyan border border-neon-cyan/30 rounded-lg hover:from-neon-cyan/30 hover:to-neon-blue/30 disabled:opacity-50 disabled:cursor-not-allowed transition-all flex items-center gap-2"
          >
            {isStreaming ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5" />
            )}
          </button>
        </div>
        <p className="text-xs text-gray-500 mt-2">
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>
    </div>
  );
}
