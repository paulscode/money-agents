import { useState, useRef, useEffect, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { conversationsService } from '@/services/conversations';
import { proposalsService } from '@/services/proposals';
import { toolsService } from '@/services/tools';
import { brainstormService } from '@/services/brainstorm';
import type { User, Proposal, ProposalUpdate, Tool } from '@/types';
import { MessageBubble } from './MessageBubble';
import FileUpload from './FileUpload';
import { Send, Loader2, Sparkles, MessageSquare, Zap, Paperclip, X, Wifi, WifiOff, Bot, Trash2, Settings2 } from 'lucide-react';
import { useAuthStore } from '@/stores/auth';
import apiClient from '@/lib/api-client';
import { API_BASE_URL, STORAGE_KEYS } from '@/lib/config';
import { logError } from '@/lib/logger';
import type { Campaign } from '@/types';

const isDev = import.meta.env.DEV;

interface ConversationPanelProps {
  proposalId?: string;
  proposalTitle?: string;
  toolId?: string;
  toolName?: string;
  campaignId?: string;
  campaignTitle?: string;
  proposal?: Proposal; // Full proposal for agent context
  tool?: Tool; // Full tool for agent context
  campaign?: Campaign; // Full campaign for agent context
}

// Streaming message state for agent responses
interface StreamingMessage {
  id: string;
  content: string;
  isStreaming: boolean;
  model?: string;
  tokens?: number;
}

// Campaign action from AI response (Phase 2)
interface CampaignAction {
  action_id: string;
  action_type: string;
  content: string;
  attributes: Record<string, string>;
  preview: string;
  status: 'pending' | 'applied' | 'rejected' | 'failed';
  error_message?: string;
}

export function ConversationPanel({ proposalId, proposalTitle, toolId, toolName, campaignId, campaignTitle, proposal, tool, campaign }: ConversationPanelProps) {
  const [messageText, setMessageText] = useState('');
  const [isInitializing, setIsInitializing] = useState(false);
  const [mentionSearch, setMentionSearch] = useState<string | null>(null);
  const [mentionPosition, setMentionPosition] = useState<number>(0);
  const [selectedMentionIndex, setSelectedMentionIndex] = useState(0);
  const [showFileUpload, setShowFileUpload] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  
  // WebSocket state for @agent mentions
  const [isAgentConnected, setIsAgentConnected] = useState(false);
  const [isAgentConnecting, setIsAgentConnecting] = useState(false);
  const [isAgentStreaming, setIsAgentStreaming] = useState(false);
  const [streamingMessage, setStreamingMessage] = useState<StreamingMessage | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  
  // Campaign actions state (Phase 2)
  const [pendingActions, setPendingActions] = useState<CampaignAction[]>([]);
  const [isExecutingActions, setIsExecutingActions] = useState(false);
  
  // Clear conversation confirmation state
  const [showClearConfirm, setShowClearConfirm] = useState(false);
  
  // LLM settings state
  const [showSettings, setShowSettings] = useState(false);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [selectedTier, setSelectedTier] = useState<'fast' | 'reasoning' | 'quality'>('reasoning');
  
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mentionDropdownRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const streamingContentRef = useRef<string>('');
  
  const queryClient = useQueryClient();
  const { user } = useAuthStore();

  // Get or create conversation for this proposal, tool, or campaign (shared by all users)
  const entityType = proposalId ? 'proposal' : toolId ? 'tool' : 'campaign';
  const entityId = proposalId || toolId || campaignId;
  
  const { data: conversation, isLoading: conversationLoading } = useQuery({
    queryKey: ['conversations', entityType, entityId],
    queryFn: async () => {
      setIsInitializing(true);
      let conv;
      if (proposalId) {
        conv = await conversationsService.getForProposal(proposalId, proposalTitle!);
      } else if (toolId) {
        conv = await conversationsService.getForTool(toolId!, toolName!);
      } else if (campaignId) {
        conv = await conversationsService.getForCampaign(campaignId, campaignTitle!);
      }
      setIsInitializing(false);
      return conv;
    },
    enabled: !!(proposalId || toolId || campaignId),
    staleTime: 1000 * 60 * 5, // 5 minutes
  });

  // Get messages for this conversation
  const { data: messages = [] } = useQuery({
    queryKey: ['messages', conversation?.id],
    queryFn: () => conversationsService.getMessages(conversation!.id),
    enabled: !!conversation,
    refetchInterval: 5000, // Poll every 5 seconds for new messages
  });

  // Search users for @mentions
  const { data: mentionUsers = [] } = useQuery({
    queryKey: ['users', 'mention', mentionSearch],
    queryFn: async () => {
      const response = await apiClient.get<User[]>('/api/v1/users', {
        params: { search: mentionSearch, limit: 10 }
      });
      return response.data;
    },
    enabled: mentionSearch !== null && mentionSearch.length > 0,
  });

  // Get LLM config for provider/tier settings
  const { data: llmConfig } = useQuery({
    queryKey: ['brainstorm-config'],
    queryFn: () => brainstormService.getConfig(),
    staleTime: 1000 * 60 * 10, // 10 minutes
  });

  // Set default provider when config loads
  useEffect(() => {
    if (llmConfig?.default_provider && !selectedProvider) {
      setSelectedProvider(llmConfig.default_provider);
    }
  }, [llmConfig, selectedProvider]);

  const configuredProviders = llmConfig?.providers.filter(p => p.is_configured) || [];

  // Auto-mark unread messages as read when they're displayed
  useEffect(() => {
    if (!conversation || !messages.length) return;

    // Find unread messages (excluding own messages which are already marked read)
    const unreadMessageIds = messages
      .filter(msg => !msg.is_read && msg.sender_id !== user?.id)
      .map(msg => msg.id);

    if (unreadMessageIds.length > 0) {
      // Mark messages as read
      conversationsService.markMessagesRead(conversation.id, unreadMessageIds)
        .then(() => {
          // Invalidate unread count queries to update badges
          queryClient.invalidateQueries({ queryKey: ['unread-counts'] });
          queryClient.invalidateQueries({ queryKey: ['messages', conversation.id] });
        })
        .catch(err => {
          logError('Failed to mark messages as read:', err);
        });
    }
  }, [conversation, messages, user?.id, queryClient]);

  // WebSocket connection for @agent mentions
  const connectToAgent = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    setIsAgentConnecting(true);
    setAgentError(null);

    const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
    if (!token) {
      setAgentError('Not authenticated');
      setIsAgentConnecting(false);
      return;
    }

    const wsProtocol = API_BASE_URL.startsWith('https') ? 'wss' : 'ws';
    const wsHost = API_BASE_URL.replace(/^https?:\/\//, '');
    
    // Connect to the appropriate agent based on context
    const agentEndpoint = campaignId ? 'campaign-discussion' : toolId ? 'tool-scout' : 'proposal-writer';
    const wsUrl = `${wsProtocol}://${wsHost}/api/v1/agents/${agentEndpoint}/stream`;

    if (isDev) console.log(`[Discussion] Connecting to ${agentEndpoint} agent...`);
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (isDev) console.log('[Discussion] WebSocket connected, sending auth...');
      // Send token as first message instead of in URL query params
      ws.send(JSON.stringify({ type: 'auth', token }));
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (isDev) console.log('[Discussion] Received:', data.type);

        switch (data.type) {
          case 'auth_result':
            if (data.success) {
              setIsAgentConnected(true);
              setIsAgentConnecting(false);
              setAgentError(null);
              if (isDev) console.log('[Discussion] Agent authenticated');
            } else {
              setAgentError(data.error || 'Authentication failed');
              setIsAgentConnecting(false);
              ws.close();
            }
            break;

          case 'chunk':
            streamingContentRef.current += data.content;
            setStreamingMessage(prev => prev ? {
              ...prev,
              content: streamingContentRef.current,
            } : null);
            break;

          case 'actions':
            // Campaign actions parsed from response (Phase 2)
            if (isDev) console.log('[Discussion] Actions received:', data.actions);
            setPendingActions(data.actions || []);
            // Update streaming message with clean content (action tags removed)
            if (data.clean_content) {
              streamingContentRef.current = data.clean_content;
              setStreamingMessage(prev => prev ? {
                ...prev,
                content: data.clean_content,
              } : null);
            }
            break;

          case 'action_results':
            // Results from executing actions
            if (isDev) console.log('[Discussion] Action results:', data.results);
            setIsExecutingActions(false);
            // Update pending actions with results
            setPendingActions(prev => prev.map(action => {
              const result = data.results?.find((r: { action_id: string }) => r.action_id === action.action_id);
              if (result) {
                return {
                  ...action,
                  status: result.success ? 'applied' : 'failed',
                  error_message: result.success ? undefined : result.message,
                };
              }
              return action;
            }));
            // Refresh campaign data if we're in a campaign context
            if (campaignId) {
              queryClient.invalidateQueries({ queryKey: ['campaign', campaignId] });
              queryClient.invalidateQueries({ queryKey: ['campaign-streams', campaignId] });
              queryClient.invalidateQueries({ queryKey: ['campaign-inputs', campaignId] });
            }
            break;

          case 'done':
            // Agent finished streaming - refresh messages to get the saved one
            setStreamingMessage(prev => prev ? {
              ...prev,
              isStreaming: false,
              model: data.model,
              tokens: data.total_tokens,
            } : null);
            setIsAgentStreaming(false);
            streamingContentRef.current = '';
            
            // Small delay then refresh messages to show the saved agent response
            setTimeout(() => {
              queryClient.invalidateQueries({ queryKey: ['messages', conversation?.id] });
              setStreamingMessage(null);
            }, 500);
            break;

          case 'error':
            setAgentError(data.error);
            setIsAgentStreaming(false);
            setStreamingMessage(null);
            setIsExecutingActions(false);
            break;

          case 'pong':
            break;
        }
      } catch (err) {
        logError('[Discussion] Failed to parse message:', err);
      }
    };

    ws.onerror = () => {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        setAgentError('Connection error');
        setIsAgentConnecting(false);
      }
    };

    ws.onclose = (event) => {
      if (isDev) console.log('[Discussion] WebSocket closed:', event.code);
      if (wsRef.current === ws) {
        setIsAgentConnected(false);
        setIsAgentConnecting(false);
        setIsAgentStreaming(false);
        wsRef.current = null;
      }
    };
  }, [conversation?.id, queryClient, toolId, campaignId]);

  // Auto-connect to agent on mount
  useEffect(() => {
    connectToAgent();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only on mount

  // Check if message contains @username mention (human-only, skip agent)
  const hasUserMention = (text: string): boolean => {
    // Match @username but NOT @agent
    return /@(?!agent\b)[a-zA-Z0-9_]+/i.test(text);
  };

  // Send message to agent via WebSocket
  const sendToAgent = useCallback((content: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setAgentError('Agent not connected');
      return false;
    }

    // Create streaming placeholder
    const streamId = `stream_${Date.now()}`;
    streamingContentRef.current = '';
    setStreamingMessage({
      id: streamId,
      content: '',
      isStreaming: true,
    });
    setIsAgentStreaming(true);

    // Build payload with appropriate context
    const payload: Record<string, unknown> = {
      type: 'message',
      content: content,
      conversation_id: conversation?.id,
    };

    // Add proposal context for Proposal Writer
    if (proposal) {
      payload.proposal_context = {
        id: proposal.id,
        title: proposal.title,
        summary: proposal.summary,
        detailed_description: proposal.detailed_description,
        status: proposal.status,
        initial_budget: proposal.initial_budget,
        expected_returns: proposal.expected_returns,
        risk_level: proposal.risk_level,
        risk_description: proposal.risk_description,
        stop_loss_threshold: proposal.stop_loss_threshold,
        success_criteria: proposal.success_criteria,
        required_tools: proposal.required_tools,
        required_inputs: proposal.required_inputs,
        implementation_timeline: proposal.implementation_timeline,
      };
    }

    // Add tool context for Tool Scout
    if (tool) {
      payload.tool_context = {
        id: tool.id,
        name: tool.name,
        slug: tool.slug,
        category: tool.category,
        description: tool.description,
        tags: tool.tags,
        status: tool.status,
        implementation_notes: tool.implementation_notes,
        blockers: tool.blockers,
        dependencies: tool.dependencies,
        usage_instructions: tool.usage_instructions,
        example_code: tool.example_code,
        required_environment_variables: tool.required_environment_variables,
        integration_complexity: tool.integration_complexity,
        cost_model: tool.cost_model,
        cost_details: tool.cost_details,
        resource_ids: tool.resource_ids,
        strengths: tool.strengths,
        weaknesses: tool.weaknesses,
        best_use_cases: tool.best_use_cases,
        external_documentation_url: tool.external_documentation_url,
        version: tool.version,
        priority: tool.priority,
        // Dynamic execution interface fields
        interface_type: tool.interface_type,
        interface_config: tool.interface_config,
        input_schema: tool.input_schema,
        output_schema: tool.output_schema,
        timeout_seconds: tool.timeout_seconds,
        // Distributed execution fields
        available_on_agents: tool.available_on_agents,
        agent_resource_map: tool.agent_resource_map,
      };
    }

    // Add campaign context for Campaign Discussion
    if (campaign) {
      // Campaign ID is required for the backend to build context
      payload.campaign_id = campaign.id;
    }

    // Add LLM settings if configured
    if (selectedProvider) {
      payload.provider = selectedProvider;
    }
    if (selectedTier) {
      payload.tier = selectedTier;
    }

    if (isDev) console.log('[Discussion] Sending to agent:', content.substring(0, 50) + '...');
    wsRef.current.send(JSON.stringify(payload));
    return true;
  }, [conversation?.id, proposal, tool, campaign, selectedProvider, selectedTier]);

  // Execute campaign actions via WebSocket (Phase 2)
  const executeActions = useCallback((actionIds: string[]) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      setAgentError('Agent not connected - cannot execute actions');
      return;
    }
    
    setIsExecutingActions(true);
    wsRef.current.send(JSON.stringify({
      type: 'execute_actions',
      action_ids: actionIds,
    }));
  }, []);

  // Clear pending actions (reject all)
  const clearPendingActions = useCallback(() => {
    setPendingActions([]);
  }, []);

  // Send message mutation
  const sendMessageMutation = useMutation({
    mutationFn: async (content: string) => {
      if (!conversation) throw new Error('No conversation found');
      
      if (isDev) console.log('Sending message:', { conversationId: conversation.id, content, hasFile: !!selectedFile });
      
      // If there's a file, use the upload endpoint
      if (selectedFile) {
        const formData = new FormData();
        formData.append('content', content);
        formData.append('file', selectedFile);
        
        const token = sessionStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
        const response = await fetch(`${API_BASE_URL}/api/v1/conversations/${conversation.id}/messages/upload`, {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`
          },
          body: formData
        });
        
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.detail || 'Failed to upload file');
        }
        
        return response.json();
      }
      
      // Otherwise, use the normal message endpoint
      return conversationsService.createMessage(conversation.id, {
        sender_type: 'user',
        sender_id: user?.id,
        content,
        content_format: 'markdown',
      });
    },
    onSuccess: (data) => {
      if (isDev) console.log('Message sent successfully:', data);
      // Refresh messages
      queryClient.invalidateQueries({ queryKey: ['messages', conversation?.id] });
      setMessageText('');
      setSelectedFile(null);
      setShowFileUpload(false);
      
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    },
    onError: (error) => {
      logError('Error sending message:', error);
      // SA3-L8: Generic message — don't leak server error details to UI
      alert('Failed to send message. Please try again.');
    },
  });

  // Clear conversation mutation
  const clearConversationMutation = useMutation({
    mutationFn: async () => {
      if (!conversation) throw new Error('No conversation found');
      return conversationsService.clearConversation(conversation.id);
    },
    onSuccess: () => {
      // Refresh messages
      queryClient.invalidateQueries({ queryKey: ['messages', conversation?.id] });
      setShowClearConfirm(false);
    },
    onError: (error) => {
      logError('Error clearing conversation:', error);
      // SA3-L8: Generic message — don't leak server error details to UI
      alert('Failed to clear conversation. Please try again.');
    },
  });

  // Apply proposal edit from agent suggestion
  const handleApplyEdit = useCallback(async (field: string, value: string | number) => {
    if (!proposalId) {
      throw new Error('No proposal to edit');
    }
    
    const updateData: ProposalUpdate = {
      [field]: value,
    };
    
    await proposalsService.update(proposalId, updateData);
    
    // Invalidate proposal query to refresh the data
    queryClient.invalidateQueries({ queryKey: ['proposals', proposalId] });
  }, [proposalId, queryClient]);

  // Apply tool edit from agent suggestion
  const handleToolEdit = useCallback(async (field: string, value: string | number) => {
    if (!toolId) {
      throw new Error('No tool to edit');
    }
    
    // Handle JSON fields - parse string values
    let processedValue = value;
    const jsonFields = ['tags', 'dependencies', 'resource_ids', 'cost_details', 'required_environment_variables'];
    if (jsonFields.includes(field) && typeof value === 'string') {
      try {
        processedValue = JSON.parse(value);
      } catch {
        // Keep as string if parse fails
        console.warn(`Failed to parse JSON for field ${field}, using as string`);
      }
    }
    
    const updateData = {
      [field]: processedValue,
    };
    
    await toolsService.updateTool(toolId, updateData);
    
    // Invalidate tool query to refresh the data
    queryClient.invalidateQueries({ queryKey: ['tool', toolId] });
    queryClient.invalidateQueries({ queryKey: ['tools'] });
  }, [toolId, queryClient]);

  // Handler to persist applied edit(s) to message metadata
  // Accepts single field or array of fields for bulk operations
  const handleEditApplied = useCallback(async (messageId: string, fields: string | string[]) => {
    if (!conversation) return;
    
    const fieldsArray = Array.isArray(fields) ? fields : [fields];
    
    // Get current message to check existing applied_edits
    const currentMessage = messages.find(m => m.id === messageId);
    const existingApplied = currentMessage?.metadata?.applied_edits || [];
    const updatedApplied = [...new Set([...existingApplied, ...fieldsArray])];
    
    try {
      await conversationsService.updateMessageMetadata(
        conversation.id,
        messageId,
        { applied_edits: updatedApplied }
      );
      // Refresh messages to get updated metadata
      queryClient.invalidateQueries({ queryKey: ['messages', conversation.id] });
    } catch (error) {
      logError('Failed to persist applied edit:', error);
      // Don't throw - the edit was applied, just metadata save failed
    }
  }, [conversation, messages, queryClient]);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    // Scroll within the messages container, not the page
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
    }
  }, [messages]);

  // Detect @mentions in text
  const detectMention = (text: string, cursorPosition: number) => {
    // Find the last @ before cursor
    const textBeforeCursor = text.substring(0, cursorPosition);
    const lastAtIndex = textBeforeCursor.lastIndexOf('@');
    
    if (lastAtIndex === -1) {
      setMentionSearch(null);
      return;
    }
    
    // Check if there's a space between @ and cursor (which would end the mention)
    const textAfterAt = textBeforeCursor.substring(lastAtIndex + 1);
    if (textAfterAt.includes(' ')) {
      setMentionSearch(null);
      return;
    }
    
    // Set mention search term
    setMentionSearch(textAfterAt);
    setMentionPosition(lastAtIndex);
    setSelectedMentionIndex(0);
  };

  // Auto-resize textarea
  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newText = e.target.value;
    const cursorPos = e.target.selectionStart;
    
    setMessageText(newText);
    
    // Auto-resize
    e.target.style.height = 'auto';
    e.target.style.height = `${e.target.scrollHeight}px`;
    
    // Detect @mentions
    detectMention(newText, cursorPos);
  };

  // Insert mention into text
  const insertMention = (username: string) => {
    if (!textareaRef.current) return;
    
    const beforeMention = messageText.substring(0, mentionPosition);
    const afterMention = messageText.substring(textareaRef.current.selectionStart);
    const newText = `${beforeMention}@${username} ${afterMention}`;
    
    setMessageText(newText);
    setMentionSearch(null);
    
    // Focus back on textarea
    setTimeout(() => {
      if (textareaRef.current) {
        const newCursorPos = mentionPosition + username.length + 2; // +2 for @ and space
        textareaRef.current.focus();
        textareaRef.current.setSelectionRange(newCursorPos, newCursorPos);
      }
    }, 0);
  };

  // Handle send
  const handleSend = async () => {
    const trimmed = messageText.trim();
    if ((!trimmed && !selectedFile) || sendMessageMutation.isPending || isAgentStreaming) return;
    
    // If there's a file, require at least some message text
    if (selectedFile && !trimmed) {
      alert('Please add a message to accompany the file');
      return;
    }
    
    const messageContent = trimmed || '(file attached)';
    
    // Check for @username mention (human-only message, skip agent)
    if (hasUserMention(messageContent)) {
      // Human-only message - just save to conversation, no agent response
      sendMessageMutation.mutate(messageContent);
    } else {
      // Default: send to agent for a response
      try {
        await sendMessageMutation.mutateAsync(messageContent);
        // Then send to the agent for a response
        sendToAgent(messageContent);
      } catch (error) {
        logError('Failed to save message before sending to agent:', error);
      }
    }
  };

  // Handle Enter key (Shift+Enter for new line)
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Handle mention dropdown navigation
    if (mentionSearch !== null && mentionUsers.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSelectedMentionIndex((prev) => 
          prev < mentionUsers.length - 1 ? prev + 1 : prev
        );
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSelectedMentionIndex((prev) => (prev > 0 ? prev - 1 : prev));
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        insertMention(mentionUsers[selectedMentionIndex].username);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMentionSearch(null);
        return;
      }
    }
    
    // Normal enter key handling
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isLoading = conversationLoading || isInitializing;
  const isEmpty = messages.length === 0;

  return (
    <div className="flex flex-col h-full bg-gray-900/50 rounded-lg border border-gray-800 overflow-hidden">
      {/* Header */}
      <div className="bg-gradient-to-r from-neon-cyan/10 via-neon-blue/10 to-neon-purple/10 border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="relative">
              <MessageSquare className="h-6 w-6 text-neon-cyan" />
              <Zap className="h-3 w-3 text-neon-yellow absolute -top-1 -right-1 animate-pulse" />
            </div>
            <div>
              <h3 className="text-lg font-semibold text-white flex items-center gap-2">
                Discussion
                <Sparkles className="h-4 w-4 text-neon-pink animate-pulse" />
              </h3>
              <p className="text-sm text-gray-400">
                AI-assisted • Use <span className="text-neon-purple font-mono">@username</span> for human-only messages
              </p>
            </div>
          </div>
          
          {/* Agent Connection Status */}
          <div className="flex items-center gap-2">
            <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
              isAgentConnecting
                ? 'bg-yellow-500/10 text-yellow-400 border border-yellow-500/30'
                : isAgentConnected
                ? 'bg-green-500/10 text-green-400 border border-green-500/30'
                : 'bg-red-500/10 text-red-400 border border-red-500/30'
            }`}>
              {isAgentConnecting ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Connecting...
                </>
              ) : isAgentConnected ? (
                <>
                  <Wifi className="h-3 w-3" />
                  <Bot className="h-3 w-3" />
                  Agent Ready
                </>
              ) : (
                <>
                  <WifiOff className="h-3 w-3" />
                  Agent Offline
                </>
              )}
            </div>
            {!isAgentConnected && !isAgentConnecting && (
              <button
                onClick={connectToAgent}
                className="px-2 py-1 text-xs bg-neon-cyan/20 text-neon-cyan border border-neon-cyan/30 rounded hover:bg-neon-cyan/30 transition-colors"
              >
                Reconnect
              </button>
            )}
            {/* Settings Button */}
            <button
              onClick={() => setShowSettings(!showSettings)}
              className={`p-1.5 rounded transition-colors ${
                showSettings 
                  ? 'bg-neon-cyan/20 text-neon-cyan' 
                  : 'text-gray-400 hover:text-white hover:bg-gray-700'
              }`}
              title="LLM Settings"
            >
              <Settings2 className="h-4 w-4" />
            </button>
            {/* Clear Conversation Button */}
            {messages.length > 0 && (
              <button
                onClick={() => setShowClearConfirm(true)}
                className="px-2 py-1 text-xs bg-red-500/10 text-red-400 border border-red-500/30 rounded hover:bg-red-500/20 transition-colors flex items-center gap-1"
                title="Clear all messages"
              >
                <Trash2 className="h-3 w-3" />
                Clear
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Settings Panel */}
      {showSettings && (
        <div className="px-4 py-3 border-b border-gray-700 bg-gray-800/30 space-y-3">
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
                      : 'border-gray-600 text-gray-400 hover:border-gray-500'
                  }`}
                >
                  {provider.name}
                </button>
              ))}
              {configuredProviders.length === 0 && (
                <span className="text-gray-500 text-sm">No providers configured</span>
              )}
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
                      : 'border-gray-600 text-gray-400 hover:border-gray-500'
                  }`}
                >
                  <Zap className={`h-3 w-3 ${
                    tier === 'fast' ? '' : tier === 'reasoning' ? 'opacity-70' : 'opacity-50'
                  }`} />
                  <span className="capitalize">{tier}</span>
                </button>
              ))}
            </div>
            {selectedProvider && llmConfig?.providers.find(p => p.id === selectedProvider)?.models[selectedTier] && (
              <p className="text-xs text-gray-500 mt-1">
                Using: {llmConfig.providers.find(p => p.id === selectedProvider)?.models[selectedTier]}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Agent Error Banner */}
      {agentError && (
        <div className="px-4 py-2 bg-red-500/10 border-b border-red-500/30 text-red-400 text-sm flex justify-between items-center">
          <span>{agentError}</span>
          <button onClick={() => setAgentError(null)} className="hover:text-red-300">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      {/* Messages Area */}
      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-1">
        {isLoading ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-3">
              <Loader2 className="h-8 w-8 animate-spin text-neon-cyan mx-auto" />
              <p className="text-gray-400 text-sm">Initializing conversation...</p>
            </div>
          </div>
        ) : isEmpty ? (
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-4 max-w-md">
              <div className="relative inline-block">
                <MessageSquare className="h-16 w-16 text-neon-cyan/30 mx-auto" />
                <Sparkles className="h-6 w-6 text-neon-pink absolute -top-1 -right-1 animate-pulse" />
              </div>
              <div className="space-y-2">
                <h4 className="text-lg font-medium text-gray-300">Start the Conversation</h4>
                <p className="text-gray-500 text-sm leading-relaxed">
                  Ask questions, request changes, or discuss implementation details. 
                  The AI will help refine this proposal to perfection.
                </p>
              </div>
              <div className="flex flex-wrap gap-2 justify-center pt-2">
                <button
                  onClick={() => setMessageText("What are the main risks here?")}
                  className="px-3 py-1.5 text-xs bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded text-gray-300 transition-colors"
                >
                  Explain risks
                </button>
                <button
                  onClick={() => setMessageText("What tools would we need for this?")}
                  className="px-3 py-1.5 text-xs bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded text-gray-300 transition-colors"
                >
                  Required tools
                </button>
                <button
                  onClick={() => setMessageText("Is the budget realistic?")}
                  className="px-3 py-1.5 text-xs bg-gray-800/50 hover:bg-gray-700/50 border border-gray-700 rounded text-gray-300 transition-colors"
                >
                  Budget check
                </button>
              </div>
            </div>
          </div>
        ) : (
          <>
            {messages.map((message) => (
              <MessageBubble 
                key={message.id} 
                message={message}
                proposal={proposal}
                tool={tool}
                onApplyEdit={proposalId ? handleApplyEdit : (toolId ? handleToolEdit : undefined)}
                onAttachmentDeleted={() => {
                  queryClient.invalidateQueries({ queryKey: ['messages', conversation?.id] });
                }}
                onEditApplied={(field) => handleEditApplied(message.id, field)}
              />
            ))}
            
            {/* Streaming Agent Response */}
            {streamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="max-w-[85%] flex flex-row gap-3">
                  {/* Avatar */}
                  <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-neon-cyan/30 to-neon-blue/30 border border-neon-cyan/50 flex items-center justify-center">
                    <Bot className="h-4 w-4 text-neon-cyan" />
                  </div>
                  
                  {/* Message Content */}
                  <div className="flex flex-col gap-1 min-w-0">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="font-medium text-neon-cyan">AI Agent</span>
                      {streamingMessage.isStreaming && (
                        <span className="text-yellow-400 flex items-center gap-1">
                          <Loader2 className="h-3 w-3 animate-spin" />
                          Thinking...
                        </span>
                      )}
                      {streamingMessage.model && (
                        <span className="text-gray-600 text-[10px] px-1.5 py-0.5 rounded bg-gray-800/50">
                          {streamingMessage.model}
                        </span>
                      )}
                    </div>
                    
                    <div className="rounded-lg border px-4 py-3 bg-gradient-to-br from-neon-cyan/10 to-neon-blue/10 border-neon-cyan/40 shadow-lg">
                      {streamingMessage.content ? (
                        <div className="prose prose-invert prose-sm max-w-none text-gray-200">
                          {streamingMessage.content}
                          {streamingMessage.isStreaming && (
                            <span className="inline-block w-2 h-4 bg-neon-cyan animate-pulse ml-1" />
                          )}
                        </div>
                      ) : (
                        <div className="flex items-center gap-2 text-neon-cyan">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          <span className="text-sm">Thinking...</span>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            )}
            
            {/* Pending Campaign Actions (Phase 2) */}
            {pendingActions.length > 0 && (
              <div className="mt-4 p-4 bg-gradient-to-br from-neon-purple/10 to-neon-pink/10 border border-neon-purple/30 rounded-lg">
                <div className="flex items-center justify-between mb-3">
                  <h4 className="text-sm font-medium text-neon-purple flex items-center gap-2">
                    <Zap className="h-4 w-4" />
                    Suggested Actions
                  </h4>
                  <span className="text-xs text-gray-400">
                    {pendingActions.filter(a => a.status === 'pending').length} pending
                  </span>
                </div>
                
                <div className="space-y-2">
                  {pendingActions.map((action) => (
                    <div 
                      key={action.action_id}
                      className={`p-3 rounded border ${
                        action.status === 'applied' 
                          ? 'bg-green-500/10 border-green-500/30'
                          : action.status === 'failed'
                          ? 'bg-red-500/10 border-red-500/30'
                          : 'bg-gray-800/50 border-gray-700'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 mb-1">
                            <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-neon-purple/20 text-neon-purple">
                              {action.action_type}
                            </span>
                            {action.status === 'applied' && (
                              <span className="text-xs text-green-400">✓ Applied</span>
                            )}
                            {action.status === 'failed' && (
                              <span className="text-xs text-red-400">✗ Failed</span>
                            )}
                          </div>
                          <p className="text-sm text-gray-300">{action.preview}</p>
                          {action.error_message && (
                            <p className="text-xs text-red-400 mt-1">{action.error_message}</p>
                          )}
                        </div>
                        
                        {action.status === 'pending' && (
                          <button
                            onClick={() => executeActions([action.action_id])}
                            disabled={isExecutingActions}
                            className="flex-shrink-0 px-3 py-1 text-xs bg-neon-purple/20 text-neon-purple border border-neon-purple/30 rounded hover:bg-neon-purple/30 transition-colors disabled:opacity-50"
                          >
                            {isExecutingActions ? (
                              <Loader2 className="h-3 w-3 animate-spin" />
                            ) : (
                              'Apply'
                            )}
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                
                {/* Bulk actions */}
                {pendingActions.some(a => a.status === 'pending') && (
                  <div className="flex gap-2 mt-3 pt-3 border-t border-gray-700">
                    <button
                      onClick={() => executeActions(pendingActions.filter(a => a.status === 'pending').map(a => a.action_id))}
                      disabled={isExecutingActions}
                      className="flex-1 px-3 py-2 text-sm bg-neon-purple/20 text-neon-purple border border-neon-purple/30 rounded hover:bg-neon-purple/30 transition-colors disabled:opacity-50"
                    >
                      {isExecutingActions ? (
                        <span className="flex items-center justify-center gap-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Applying...
                        </span>
                      ) : (
                        `Apply All (${pendingActions.filter(a => a.status === 'pending').length})`
                      )}
                    </button>
                    <button
                      onClick={clearPendingActions}
                      disabled={isExecutingActions}
                      className="px-3 py-2 text-sm text-gray-400 border border-gray-700 rounded hover:bg-gray-800 transition-colors disabled:opacity-50"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
              </div>
            )}
            
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input Area */}
      <div className="border-t border-gray-800 bg-gray-900/80 backdrop-blur-sm p-4">
        {/* File Upload Section */}
        {showFileUpload && (
          <div className="mb-4 p-4 bg-gray-800/50 border border-gray-700 rounded-lg">
            <div className="flex justify-between items-center mb-3">
              <h4 className="text-sm font-medium text-gray-300 flex items-center gap-2">
                <Paperclip className="h-4 w-4" />
                Attach File
              </h4>
              <button
                onClick={() => {
                  setShowFileUpload(false);
                  setSelectedFile(null);
                }}
                className="text-gray-400 hover:text-gray-200 transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <FileUpload
              onFileSelect={setSelectedFile}
              maxSizeMB={50}
              disabled={sendMessageMutation.isPending}
            />
          </div>
        )}
        
        <div className="flex gap-3 items-end">
          <div className="flex-1 relative">
            {/* @Mention Dropdown */}
            {mentionSearch !== null && mentionUsers.length > 0 && (
              <div 
                ref={mentionDropdownRef}
                className="absolute bottom-full left-0 mb-2 w-64 bg-gray-800 border border-gray-700 rounded-lg shadow-xl max-h-48 overflow-y-auto z-50"
              >
                {mentionUsers.map((mentionUser, index) => (
                  <button
                    key={mentionUser.id}
                    onClick={() => insertMention(mentionUser.username)}
                    className={`w-full px-4 py-2 text-left hover:bg-gray-700 transition-colors flex items-center gap-2 ${
                      index === selectedMentionIndex ? 'bg-gray-700' : ''
                    }`}
                  >
                    <div className="flex-1">
                      <div className="text-sm font-medium text-gray-200">
                        @{mentionUser.username}
                      </div>
                      <div className="text-xs text-gray-500">{mentionUser.email}</div>
                    </div>
                  </button>
                ))}
              </div>
            )}
            
            <textarea
              ref={textareaRef}
              value={messageText}
              onChange={handleTextareaChange}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question or request changes... (Type @ to mention users)"
              disabled={isLoading || sendMessageMutation.isPending}
              rows={1}
              className="w-full bg-gray-800/50 border border-gray-700 rounded-lg px-4 py-3 text-gray-200 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-neon-cyan/50 focus:border-neon-cyan/50 resize-none min-h-[44px] max-h-[200px] transition-all"
            />
            <div className="absolute bottom-2 right-2 text-xs text-gray-600">
              {messageText.length > 0 && `${messageText.length} chars`}
            </div>
          </div>
          
          {/* File Upload Toggle Button */}
          <button
            onClick={() => setShowFileUpload(!showFileUpload)}
            disabled={isLoading || sendMessageMutation.isPending}
            className={`flex-shrink-0 h-[44px] w-[44px] ${
              showFileUpload || selectedFile
                ? 'bg-gradient-to-br from-neon-purple to-neon-pink'
                : 'bg-gray-700 hover:bg-gray-600'
            } disabled:bg-gray-800 disabled:cursor-not-allowed text-white rounded-lg transition-all flex items-center justify-center shadow-lg ${
              showFileUpload || selectedFile ? 'shadow-neon-purple/20' : 'shadow-gray-700/20'
            } hover:shadow-neon-purple/40 disabled:shadow-none group`}
            title={showFileUpload ? 'Hide file upload' : 'Attach file'}
          >
            <Paperclip className={`h-5 w-5 ${showFileUpload ? 'rotate-45' : ''} transition-transform`} />
          </button>
          
          <button
            onClick={handleSend}
            disabled={(!messageText.trim() && !selectedFile) || isLoading || sendMessageMutation.isPending || isAgentStreaming}
            className="flex-shrink-0 h-[44px] w-[44px] bg-gradient-to-br from-neon-cyan to-neon-blue hover:from-neon-cyan/90 hover:to-neon-blue/90 disabled:from-gray-700 disabled:to-gray-600 disabled:cursor-not-allowed text-white rounded-lg transition-all flex items-center justify-center shadow-lg shadow-neon-cyan/20 hover:shadow-neon-cyan/40 disabled:shadow-none group"
          >
            {sendMessageMutation.isPending || isAgentStreaming ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <Send className="h-5 w-5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform" />
            )}
          </button>
        </div>
        
        <p className="text-xs text-gray-600 mt-2 flex items-center gap-1">
          <Sparkles className="h-3 w-3" />
          AI responds by default • <span className="text-neon-purple">@username</span> for human-only • {selectedFile ? `File: ${selectedFile.name} • ` : ''}Markdown supported
        </p>
      </div>

      {/* Clear Conversation Confirmation Modal */}
      {showClearConfirm && (
        <div className="absolute inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-6 max-w-sm mx-4 shadow-xl">
            <h4 className="text-lg font-semibold text-white mb-2">Clear Conversation?</h4>
            <p className="text-gray-400 text-sm mb-4">
              This will permanently delete all {messages.length} messages in this discussion. This cannot be undone.
            </p>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowClearConfirm(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-white transition-colors"
                disabled={clearConversationMutation.isPending}
              >
                Cancel
              </button>
              <button
                onClick={() => clearConversationMutation.mutate()}
                disabled={clearConversationMutation.isPending}
                className="px-4 py-2 text-sm bg-red-600 hover:bg-red-700 text-white rounded-lg transition-colors flex items-center gap-2 disabled:opacity-50"
              >
                {clearConversationMutation.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Clearing...
                  </>
                ) : (
                  <>
                    <Trash2 className="h-4 w-4" />
                    Clear All
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
