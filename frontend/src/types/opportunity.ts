// Opportunity Scout types

export type OpportunityStatus =
  | 'discovered'
  | 'researching'
  | 'evaluated'
  | 'presented'
  | 'approved'
  | 'rejected'
  | 'dismissed'
  | 'expired'
  | 'merged';

export type OpportunityType =
  | 'arbitrage'
  | 'content'
  | 'service'
  | 'product'
  | 'automation'
  | 'affiliate'
  | 'investment'
  | 'other';

export type RankingTier = 'top_pick' | 'promising' | 'maybe' | 'unlikely';

export type TimeSensitivity = 'immediate' | 'short' | 'medium' | 'evergreen';

export type EffortLevel = 'minimal' | 'moderate' | 'significant' | 'major';

export type StrategyStatus = 'active' | 'paused' | 'retired' | 'experimental';

export type InsightType =
  | 'principle'
  | 'pattern'
  | 'anti_pattern'
  | 'hypothesis'
  | 'validated';

export interface RevenuePotential {
  min?: number;
  max?: number;
  timeframe?: string; // "monthly", "yearly", "one-time"
  recurring?: boolean;
}

export interface CostEstimate {
  upfront?: number;
  ongoing?: number;
  currency?: string;
}

export interface Opportunity {
  id: string;
  title: string;
  summary: string;
  opportunity_type: OpportunityType;
  discovered_at: string;
  updated_at: string;
  discovery_strategy_id?: string;
  source_type: string;
  source_query?: string;
  source_urls?: string[];
  raw_signal?: string;
  status: OpportunityStatus;
  initial_assessment?: string;
  detailed_analysis?: string;
  confidence_score?: number;
  time_sensitivity?: TimeSensitivity;
  estimated_effort?: EffortLevel;
  estimated_revenue_potential?: RevenuePotential;
  score_breakdown?: Record<string, number>;
  overall_score?: number;
  ranking_tier?: RankingTier;
  ranking_factors?: Record<string, any>;
  rank_position?: number;
  required_tools?: string[];
  required_skills?: string[];
  estimated_cost?: CostEstimate;
  blocking_requirements?: string[];
  presented_at?: string;
  user_decision?: string;
  user_feedback?: string;
  proposal_id?: string;
  similar_opportunity_ids?: string[];
  derived_from_id?: string;
  bulk_dismissed: boolean;
  bulk_dismiss_reason?: string;
}

export interface HopperStatus {
  max_capacity: number;
  active_proposals: number;
  pending_approvals: number;
  total_committed: number;
  available_slots: number;
  status: 'available' | 'warning' | 'full';
  can_accept_more: boolean;
}

export interface OpportunityListResponse {
  opportunities: Opportunity[];
  total: number;
  hopper_status?: HopperStatus;
}

export interface OpportunityDecision {
  notes?: string;
}

export interface BulkDismissRequest {
  opportunity_ids?: string[];
  below_score?: number;
  tier?: RankingTier;
  reason?: string;
}

export interface BulkDismissResponse {
  dismissed_count: number;
  message: string;
}

export interface DiscoveryStrategy {
  id: string;
  name: string;
  description: string;
  strategy_type: string;
  search_queries?: string[];
  source_types?: string[];
  filters?: Record<string, any>;
  schedule: string;
  created_at: string;
  updated_at: string;
  created_by: string;
  status: StrategyStatus;
  times_executed: number;
  last_executed?: string;
  opportunities_found: number;
  opportunities_approved: number;
  opportunities_rejected: number;
  effectiveness_score?: number;
  agent_notes?: string;
  improvement_ideas?: string[];
  parent_strategy_id?: string;
}

export interface AgentInsight {
  id: string;
  created_at: string;
  updated_at: string;
  last_validated?: string;
  insight_type: InsightType;
  title: string;
  description: string;
  evidence?: string[];
  confidence: number;
  domains?: string[];
  conditions?: Record<string, any>;
  times_applied: number;
  times_confirmed: number;
  times_contradicted: number;
  parent_insight_id?: string;
  superseded_by_id?: string;
}

export interface UserScoutSettings {
  id?: string;
  user_id?: string;
  created_at?: string;
  updated_at?: string;
  max_active_proposals: number;
  hopper_warning_threshold: number;
  auto_pause_discovery: boolean;
  max_backlog_size: number;
  auto_dismiss_below_score?: number;
  auto_dismiss_types: string[];
  default_sort?: string;
  show_unlikely_tier: boolean;
  preferred_types: string[];
  preferred_domains: string[];
  excluded_types: string[];
  excluded_keywords: string[];
  custom_rubric_weights?: Record<string, number>;
}

export interface ScoutStatistics {
  period_days: number;
  opportunities: {
    total: number;
    by_status: Record<string, number>;
    by_tier: Record<string, number>;
    approval_rate: number;
  };
  strategies: {
    total: number;
    active: number;
    by_effectiveness: Record<string, number>;
  };
  discovery_runs: number;
  insights_count: number;
}
