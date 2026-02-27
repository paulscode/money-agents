import apiClient from '@/lib/api-client';
import type { Conversation, Message, UnreadCount } from '@/types';

export interface CreateConversationRequest {
  conversation_type: 'proposal' | 'campaign' | 'tool' | 'general';
  related_id?: string;
  title?: string;
}

export interface CreateMessageRequest {
  conversation_id: string;
  sender_type: 'user' | 'agent' | 'system';
  sender_id?: string;
  content: string;
  content_format?: string;
  metadata?: Record<string, any>;
  tokens_used?: number;
  model_used?: string;
}

class ConversationsService {
  /**
   * Create a new conversation or get existing one for proposal
   */
  async create(data: CreateConversationRequest): Promise<Conversation> {
    const response = await apiClient.post('/api/v1/conversations/', data);
    return response.data;
  }

  /**
   * Get all conversations
   */
  async getAll(params?: {
    skip?: number;
    limit?: number;
    conversation_type?: string;
  }): Promise<Conversation[]> {
    const response = await apiClient.get('/api/v1/conversations/', { params });
    return response.data;
  }

  /**
   * Get a conversation by ID
   */
  async getById(id: string): Promise<Conversation> {
    const response = await apiClient.get(`/api/v1/conversations/${id}`);
    return response.data;
  }

  /**
   * Get or create conversation for a specific proposal
   * Now returns the shared conversation for that proposal
   */
  async getForProposal(proposalId: string, proposalTitle: string): Promise<Conversation> {
    // Try to create - backend will return existing if already exists
    return await this.create({
      conversation_type: 'proposal',
      related_id: proposalId,
      title: `Discussion: ${proposalTitle}`,
    });
  }

  /**
   * Get or create conversation for a specific tool
   */
  async getForTool(toolId: string, toolName: string): Promise<Conversation> {
    return await this.create({
      conversation_type: 'tool',
      related_id: toolId,
      title: `Discussion: ${toolName}`,
    });
  }

  /**
   * Get or create conversation for a specific campaign
   */
  async getForCampaign(campaignId: string, campaignTitle: string): Promise<Conversation> {
    return await this.create({
      conversation_type: 'campaign',
      related_id: campaignId,
      title: `Discussion: ${campaignTitle}`,
    });
  }

  /**
   * Create a message in a conversation
   */
  async createMessage(conversationId: string, data: Omit<CreateMessageRequest, 'conversation_id'>): Promise<Message> {
    const response = await apiClient.post(`/api/v1/conversations/${conversationId}/messages`, {
      conversation_id: conversationId,
      ...data,
    });
    return response.data;
  }

  /**
   * Get all messages in a conversation
   */
  async getMessages(conversationId: string, params?: {
    skip?: number;
    limit?: number;
  }): Promise<Message[]> {
    const response = await apiClient.get(`/api/v1/conversations/${conversationId}/messages`, { params });
    return response.data;
  }

  /**
   * Mark messages as read
   */
  async markMessagesRead(conversationId: string, messageIds: string[]): Promise<void> {
    await apiClient.post(`/api/v1/conversations/${conversationId}/messages/mark-read`, {
      message_ids: messageIds,
    });
  }

  /**
   * Update message metadata (used to track applied edits)
   */
  async updateMessageMetadata(
    conversationId: string, 
    messageId: string, 
    metadata: Record<string, any>
  ): Promise<Message> {
    const response = await apiClient.patch(
      `/api/v1/conversations/${conversationId}/messages/${messageId}/metadata`,
      metadata
    );
    return response.data;
  }

  /**
   * Clear all messages in a conversation (keeps conversation, deletes messages)
   */
  async clearConversation(conversationId: string): Promise<void> {
    await apiClient.delete(`/api/v1/conversations/${conversationId}/messages`);
  }

  /**
   * Get unread message count for a proposal
   */
  async getProposalUnreadCount(proposalId: string): Promise<number> {
    const response = await apiClient.get(`/api/v1/conversations/proposals/${proposalId}/unread-count`);
    return response.data.unread_count;
  }

  /**
   * Get unread counts for all proposals
   */
  async getAllProposalsUnreadCounts(): Promise<UnreadCount[]> {
    const response = await apiClient.get('/api/v1/conversations/proposals/unread-counts/all');
    return response.data;
  }

  /**
   * Get unread message count for a tool
   */
  async getToolUnreadCount(toolId: string): Promise<number> {
    const response = await apiClient.get(`/api/v1/conversations/tools/${toolId}/unread-count`);
    return response.data.unread_count;
  }

  /**
   * Get unread counts for all tools
   */
  async getAllToolsUnreadCounts(): Promise<UnreadCount[]> {
    const response = await apiClient.get('/api/v1/conversations/tools/unread-counts/all');
    return response.data;
  }
}

export const conversationsService = new ConversationsService();
