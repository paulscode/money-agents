import apiClient from '@/lib/api-client';
import { API_V1_PREFIX } from '@/lib/config';
import type {
  CampaignPattern,
  PatternListResponse,
  CampaignLesson,
  LessonListResponse,
  PlanRevision,
  ProactiveSuggestion,
  SuggestionListResponse,
  LearningStats,
} from '@/types';

const LEARNING_PREFIX = `${API_V1_PREFIX}/learning`;

export interface PatternFilters {
  pattern_type?: string;
  status_filter?: string;
  min_confidence?: number;
  include_global?: boolean;
  limit?: number;
  offset?: number;
}

export interface LessonFilters {
  category?: string;
  severity?: string;
  campaign_id?: string;
  limit?: number;
  offset?: number;
}

export interface SuggestionFilters {
  status_filter?: string;
  suggestion_type?: string;
  limit?: number;
  offset?: number;
}

export interface SuggestionAction {
  action: 'accept' | 'reject';
  feedback?: string;
}

export const learningService = {
  // ==========================================================================
  // Patterns
  // ==========================================================================
  
  /**
   * List campaign patterns with optional filtering.
   */
  async listPatterns(filters?: PatternFilters): Promise<PatternListResponse> {
    const response = await apiClient.get<PatternListResponse>(
      `${LEARNING_PREFIX}/patterns`,
      { params: filters }
    );
    return response.data;
  },

  /**
   * Get a specific pattern by ID.
   */
  async getPattern(patternId: string): Promise<CampaignPattern> {
    const response = await apiClient.get<CampaignPattern>(
      `${LEARNING_PREFIX}/patterns/${patternId}`
    );
    return response.data;
  },

  // ==========================================================================
  // Lessons
  // ==========================================================================

  /**
   * List campaign lessons with optional filtering.
   */
  async listLessons(filters?: LessonFilters): Promise<LessonListResponse> {
    const response = await apiClient.get<LessonListResponse>(
      `${LEARNING_PREFIX}/lessons`,
      { params: filters }
    );
    return response.data;
  },

  /**
   * Get a specific lesson by ID.
   */
  async getLesson(lessonId: string): Promise<CampaignLesson> {
    const response = await apiClient.get<CampaignLesson>(
      `${LEARNING_PREFIX}/lessons/${lessonId}`
    );
    return response.data;
  },

  // ==========================================================================
  // Campaign-Specific
  // ==========================================================================

  /**
   * Get all plan revisions for a specific campaign.
   */
  async getCampaignRevisions(campaignId: string): Promise<PlanRevision[]> {
    const response = await apiClient.get<PlanRevision[]>(
      `${LEARNING_PREFIX}/campaigns/${campaignId}/revisions`
    );
    return response.data;
  },

  /**
   * Get proactive suggestions for a specific campaign.
   */
  async getCampaignSuggestions(
    campaignId: string,
    filters?: SuggestionFilters
  ): Promise<SuggestionListResponse> {
    const response = await apiClient.get<SuggestionListResponse>(
      `${LEARNING_PREFIX}/campaigns/${campaignId}/suggestions`,
      { params: filters }
    );
    return response.data;
  },

  /**
   * Accept or reject a proactive suggestion.
   */
  async respondToSuggestion(
    suggestionId: string,
    action: SuggestionAction
  ): Promise<ProactiveSuggestion> {
    const response = await apiClient.post<ProactiveSuggestion>(
      `${LEARNING_PREFIX}/suggestions/${suggestionId}/respond`,
      action
    );
    return response.data;
  },

  // ==========================================================================
  // Statistics
  // ==========================================================================

  /**
   * Get aggregate statistics about campaign learning.
   */
  async getStats(): Promise<LearningStats> {
    const response = await apiClient.get<LearningStats>(
      `${LEARNING_PREFIX}/stats`
    );
    return response.data;
  },
};
