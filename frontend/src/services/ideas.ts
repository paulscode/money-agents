/**
 * Ideas API service
 */
import apiClient from '@/lib/api-client';
import type { IdeaCounts, Idea } from '../types';

/**
 * Get idea counts by status for the current user.
 */
export async function getIdeaCounts(): Promise<IdeaCounts> {
  const response = await apiClient.get<IdeaCounts>('/api/v1/ideas/counts');
  return response.data;
}

/**
 * List ideas for the current user.
 */
export async function listIdeas(status?: string): Promise<Idea[]> {
  const params = status ? { status } : {};
  const response = await apiClient.get<Idea[]>('/api/v1/ideas', { params });
  return response.data;
}

/**
 * Create a new idea manually.
 */
export async function createIdea(content: string): Promise<Idea> {
  const response = await apiClient.post<Idea>('/api/v1/ideas', { content, source: 'manual' });
  return response.data;
}

/**
 * Archive an idea.
 */
export async function archiveIdea(ideaId: string): Promise<Idea> {
  const response = await apiClient.post<Idea>(`/api/v1/ideas/${ideaId}/archive`);
  return response.data;
}
