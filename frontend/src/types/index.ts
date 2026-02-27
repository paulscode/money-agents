export type UserRole = 'admin' | 'user' | 'pending';

export interface User {
  id: string;
  email: string;
  username: string;
  role: UserRole;
  is_active: boolean;
  is_superuser: boolean;
  display_name: string | null;
  avatar_url: string | null;
  last_login: string | null;
  disclaimer_acknowledged_at: string | null;
  show_disclaimer_on_login: boolean;
  created_at: string;
  updated_at: string;
}

export interface UserUpdate {
  email?: string;
  username?: string;
  password?: string;
  display_name?: string;
  avatar_url?: string;
}

export interface LoginRequest {
  identifier: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  username: string;
  password: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
}

export interface ResetPasswordRequest {
  code: string;
  new_password: string;
}

export interface ResetCodeResponse {
  code: string;
  expires_at: string;
  username: string;
}

export interface DisclaimerStatus {
  requires_disclaimer: boolean;
  disclaimer_text: string;
  is_initial_admin: boolean;
  agents_enabled: boolean;
  acknowledged_at: string | null;
  show_on_login: boolean;
}

export interface DisclaimerAcknowledgeResponse {
  acknowledged: boolean;
  acknowledged_at: string;
  show_on_login: boolean;
  agents_enabled: boolean;
  agents_just_enabled: boolean;
}

export type ProposalStatus = 
  | 'draft_from_scout'  // Auto-created from approved opportunity, awaiting refinement
  | 'pending'
  | 'proposed'
  | 'under_review'
  | 'approved'
  | 'rejected'
  | 'deferred'
  | 'submitted'
  | 'changes_requested';

export type RiskLevel = 'low' | 'medium' | 'high';

export interface Proposal {
  id: string;
  user_id: string;
  agent_id: string | null;
  title: string;
  summary: string;
  detailed_description: string;
  status: ProposalStatus;
  initial_budget: number;
  recurring_costs: Record<string, any> | null;
  expected_returns: Record<string, any> | null;
  risk_level: RiskLevel;
  risk_description: string;
  stop_loss_threshold: Record<string, any>;
  success_criteria: Record<string, any>;
  required_tools: Record<string, any>;
  required_inputs: Record<string, any>;
  implementation_timeline: Record<string, any> | null;
  research_context: Record<string, any> | null;  // Context from Opportunity Scout
  source_opportunity_id: string | null;  // Link to source opportunity
  similar_proposals: Record<string, any> | null;
  similarity_score: number | null;
  source: string | null;
  tags: Record<string, any> | null;
  meta_data: Record<string, any> | null;
  submitted_at: string;
  reviewed_at: string | null;
  created_at: string;
  updated_at: string;
  bitcoin_budget_sats: number | null;
  bitcoin_budget_rationale: string | null;
  has_campaign: boolean | null;  // Whether this proposal has been converted to a campaign
  campaign_id: string | null;  // ID of the associated campaign (if any)
}

export interface ProposalCreate {
  title: string;
  summary: string;
  detailed_description: string;
  initial_budget: number;
  recurring_costs?: Record<string, any>;
  expected_returns?: Record<string, any>;
  risk_level: RiskLevel;
  risk_description: string;
  stop_loss_threshold: Record<string, any>;
  success_criteria: Record<string, any>;
  required_tools: Record<string, any>;
  required_inputs: Record<string, any>;
  implementation_timeline?: Record<string, any>;
  bitcoin_budget_sats?: number;
  bitcoin_budget_rationale?: string;
  source?: string;
  tags?: Record<string, any>;
  metadata?: Record<string, any>;
}

export interface ProposalUpdate {
  title?: string;
  summary?: string;
  detailed_description?: string;
  initial_budget?: number;
  recurring_costs?: Record<string, any>;
  expected_returns?: Record<string, any>;
  risk_level?: RiskLevel;
  risk_description?: string;
  stop_loss_threshold?: Record<string, any>;
  success_criteria?: Record<string, any>;
  required_tools?: Record<string, any>;
  required_inputs?: Record<string, any>;
  implementation_timeline?: Record<string, any>;
  bitcoin_budget_sats?: number;
  bitcoin_budget_rationale?: string;
  status?: ProposalStatus;
  source?: string;
  tags?: Record<string, any>;
  metadata?: Record<string, any>;
}

export type CampaignStatus =
  | 'initializing'
  | 'waiting_for_inputs'
  | 'active'
  | 'paused'
  | 'completed'
  | 'terminated'
  | 'failed';

export interface Campaign {
  id: string;
  proposal_id: string;
  proposal_title: string | null;
  user_id: string;
  agent_id: string | null;
  status: CampaignStatus;
  budget_allocated: number;
  budget_spent: number;
  revenue_generated: number;
  bitcoin_budget_sats: number | null;
  bitcoin_spent_sats: number;
  bitcoin_received_sats: number;
  success_metrics: Record<string, any>;
  performance_data: Record<string, any> | null;
  tasks_total: number;
  tasks_completed: number;
  current_phase: string | null;
  requirements_checklist: Record<string, any>;
  all_requirements_met: boolean;
  start_date: string | null;
  end_date: string | null;
  last_activity_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignCreate {
  proposal_id: string;
  budget_allocated: number;
  success_metrics: Record<string, any>;
  requirements_checklist: Record<string, any>;
}

// Task Stream Types
export type TaskStreamStatus = 'pending' | 'blocked' | 'ready' | 'running' | 'completed' | 'failed';
export type TaskStatus = 'pending' | 'queued' | 'running' | 'completed' | 'failed' | 'blocked' | 'skipped';
export type TaskType = 'tool_execution' | 'llm_reasoning' | 'user_input' | 'wait' | 'checkpoint';
export type InputType = 'credentials' | 'text' | 'file' | 'confirmation' | 'selection' | 'number' | 'url';
export type InputPriority = 'blocking' | 'high' | 'medium' | 'low';
export type InputStatus = 'pending' | 'provided' | 'expired' | 'skipped';

export interface TaskStream {
  id: string;
  name: string;
  description: string | null;
  status: TaskStreamStatus;
  tasks_total: number;
  tasks_completed: number;
  tasks_failed: number;
  tasks_blocked: number;
  progress_pct: number;
  blocking_reasons: string[];
}

export interface CampaignTask {
  id: string;
  stream_id: string;
  name: string;
  description: string;
  task_type: TaskType;
  status: TaskStatus;
  tool_slug: string | null;
  estimated_duration_minutes: number;
  is_critical: boolean;
  result: Record<string, any> | null;
  error: string | null;
}

export interface UserInputRequest {
  id: string;
  input_key: string;
  input_type: InputType;
  title: string;
  description: string;
  priority: InputPriority;
  status: InputStatus;
  options: string[] | null;
  default_value: string | null;
  blocking_count: number;
  suggested_value: string | null;
}

export interface CampaignStreamsResponse {
  streams: TaskStream[];
  blocking_inputs: UserInputRequest[];
  total_streams: number;
  completed_streams: number;
  ready_streams: number;
  blocked_streams: number;
  total_tasks: number;
  completed_tasks: number;
  overall_progress_pct: number;
}

export interface CampaignInputsResponse {
  inputs: UserInputRequest[];
  blocking_count: number;
  high_priority_count: number;
}

export interface InputProvideRequest {
  input_key: string;
  value: string;
}

export interface BulkInputProvideRequest {
  inputs: InputProvideRequest[];
}

export type ConversationType = 'proposal' | 'campaign' | 'tool' | 'general';

export interface Conversation {
  id: string;
  created_by_user_id: string;
  conversation_type: ConversationType;
  related_id: string | null;
  title: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  unread_count?: number;
}

export type SenderType = 'user' | 'agent' | 'system';

export interface FileAttachment {
  id: string;
  filename: string;
  size: number;
  mime_type: string;
  uploaded_at: string;
  thumbnail_url?: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  sender_type: SenderType;
  sender_id: string | null;
  sender_username?: string | null;
  content: string;
  content_format: string;
  metadata: Record<string, any> | null;
  tokens_used: number | null;
  model_used: string | null;
  mentioned_user_ids: string[] | null;
  attachments: FileAttachment[] | null;
  is_read: boolean;
  created_at: string;
}

export interface UnreadCount {
  proposal_id?: string;
  tool_id?: string;
  unread_count: number;
}

// Tool types
export type ToolStatus =
  | 'requested'
  | 'under_review'
  | 'changes_requested'
  | 'approved'
  | 'rejected'
  | 'implementing'
  | 'testing'
  | 'blocked'
  | 'on_hold'
  | 'implemented'
  | 'deprecated'
  | 'retired';

export type ToolCategory =
  | 'api'
  | 'data_source'
  | 'automation'
  | 'analysis'
  | 'communication';

export interface Tool {
  id: string;
  name: string;
  slug: string;
  category: ToolCategory;
  description: string;
  tags: string[] | null;
  status: ToolStatus;
  requester_id: string;
  assigned_to_id: string | null;
  approved_by_id: string | null;
  implementation_notes: string | null;
  blockers: string | null;
  dependencies: string[] | null;
  requested_at: string;
  approved_at: string | null;
  implemented_at: string | null;
  estimated_completion_date: string | null;
  usage_instructions: string | null;
  example_code: string | null;
  required_environment_variables: Record<string, any> | null;
  integration_complexity: string | null;
  cost_model: string | null;
  cost_details: Record<string, any> | null;
  shared_resources: Record<string, any> | null;
  resource_ids: string[] | null;
  strengths: string | null;
  weaknesses: string | null;
  best_use_cases: string | null;
  external_documentation_url: string | null;
  version: string | null;
  priority: string | null;
  // Dynamic execution interface
  interface_type: string | null;
  interface_config: Record<string, any> | null;
  input_schema: Record<string, any> | null;
  output_schema: Record<string, any> | null;
  timeout_seconds: number | null;
  // Distributed execution
  available_on_agents: string[] | null; // null=local, []=disabled, ['*']=all, ['host1']=specific
  agent_resource_map: Record<string, string[]> | null; // hostname -> local resource names
  // Health status
  health_status: HealthStatus | null;
  last_health_check: string | null;
  health_message: string | null;
  health_response_ms: number | null;
  health_check_enabled: boolean;
  health_check_interval_minutes: number;
  // Timestamps
  created_at: string;
  updated_at: string;
  requester_username?: string | null;
  assigned_to_username?: string | null;
  unread_count?: number;
}

export interface ToolCreate {
  name: string;
  slug: string;
  category: ToolCategory;
  description: string;
  tags?: string[];
  implementation_notes?: string;
  blockers?: string;
  dependencies?: string[];
  estimated_completion_date?: string;
  usage_instructions?: string;
  example_code?: string;
  required_environment_variables?: Record<string, any>;
  integration_complexity?: string;
  cost_model?: string;
  cost_details?: Record<string, any>;
  shared_resources?: Record<string, any>;
  resource_ids?: string[];
  strengths?: string;
  weaknesses?: string;
  best_use_cases?: string;
  external_documentation_url?: string;
  version?: string;
  priority?: string;
}

export interface ToolUpdate {
  name?: string;
  slug?: string;
  category?: ToolCategory;
  description?: string;
  tags?: string[];
  implementation_notes?: string | null;
  blockers?: string | null;
  dependencies?: string[];
  estimated_completion_date?: string | null;
  usage_instructions?: string | null;
  example_code?: string | null;
  required_environment_variables?: Record<string, any> | null;
  integration_complexity?: string | null;
  cost_model?: string | null;
  cost_details?: Record<string, any> | null;
  shared_resources?: Record<string, any> | null;
  resource_ids?: string[];
  strengths?: string | null;
  weaknesses?: string | null;
  best_use_cases?: string | null;
  external_documentation_url?: string | null;
  version?: string | null;
  priority?: string | null;
  // Dynamic execution interface
  interface_type?: string | null;
  interface_config?: Record<string, any> | null;
  input_schema?: Record<string, any> | null;
  output_schema?: Record<string, any> | null;
  timeout_seconds?: number | null;
  // Distributed execution
  available_on_agents?: string[] | null;
  agent_resource_map?: Record<string, string[]> | null;
}

export interface AssignToolRequest {
  user_id: string;
}

export interface UpdateToolStatusRequest {
  status: ToolStatus;
  notes?: string;
}

// Tool Execution types
export type ToolExecutionStatus = 'pending' | 'running' | 'completed' | 'failed' | 'timeout' | 'cancelled';

export interface ToolExecuteRequest {
  params: Record<string, any>;
  conversation_id?: string;
  queue_timeout?: number;
  wait_for_resource?: boolean;
}

export interface ToolExecution {
  id: string;
  tool_id: string;
  tool_name: string;
  status: ToolExecutionStatus;
  success: boolean;
  output: Record<string, any> | null;
  error: string | null;
  duration_ms: number | null;
  cost_units: number | null;
  job_id: string | null;
  queue_position: number | null;
}

// Resource types
export type ResourceStatus = 'available' | 'in_use' | 'maintenance' | 'disabled';
export type ResourceType = 'gpu' | 'cpu' | 'ram' | 'storage' | 'custom';
export type ResourceCategory = 'compute' | 'capacity';

export interface Resource {
  id: string;
  name: string;
  resource_type: ResourceType;
  category: ResourceCategory;
  status: ResourceStatus;
  is_system_resource: boolean;
  metadata: Record<string, any> | null;
  created_at: string;
  updated_at: string;
  // Remote agent association
  agent_hostname: string | null;
  local_name: string | null;
  // Compute resource fields
  jobs_queued: number;
  jobs_running: number;
  // Storage (capacity) resource fields
  total_bytes?: number;
  used_bytes?: number;
  available_bytes?: number;
  reserved_bytes?: number;
}

export interface ResourceCreate {
  name: string;
  resource_type: ResourceType;
  category?: ResourceCategory;
  metadata?: Record<string, any>;
}

export interface StorageResourceCreate {
  name: string;
  path: string;
  min_free_gb?: number;
}

export interface ResourceUpdate {
  name?: string;
  resource_type?: ResourceType;
  status?: ResourceStatus;
  metadata?: Record<string, any>;
}

export interface StorageReservation {
  id: string;
  resource_id: string;
  agent_name: string;
  purpose: string | null;
  bytes_reserved: number;
  expires_at: string;
  created_at: string;
}

export interface StorageFile {
  id: string;
  resource_id: string;
  file_path: string;
  size_bytes: number;
  agent_name: string;
  purpose: string | null;
  is_temporary: boolean;
  created_at: string;
  last_accessed: string | null;
}

export interface StorageInfo {
  resource_id: string;
  name: string;
  path: string;
  total_bytes: number;
  used_bytes: number;
  reserved_bytes: number;
  available_bytes: number;
  min_free_bytes: number;
  active_reservations: StorageReservation[];
  tracked_files_count: number;
  tracked_files_size: number;
}

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Job {
  id: string;
  tool_id: string;
  resource_id: string;
  conversation_id: string;
  message_id: string | null;
  status: JobStatus;
  parameters: Record<string, any> | null;
  result: Record<string, any> | null;
  error: string | null;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
}

// =============================================================================
// Agent Management Types
// =============================================================================

export type AgentStatus = 'idle' | 'running' | 'paused' | 'error' | 'budget_exceeded';
export type AgentRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'timeout';
export type BudgetPeriod = 'hourly' | 'daily' | 'weekly' | 'monthly';

export interface AgentSummary {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: AgentStatus;
  status_message: string | null;
  is_enabled: boolean;
  
  // Scheduling
  schedule_interval_seconds: number;
  last_run_at: string | null;
  next_run_at: string | null;
  
  // Budget
  budget_limit: number | null;
  budget_period: BudgetPeriod;
  budget_used: number;
  budget_remaining: number | null;
  budget_percentage_used: number;
  budget_warning: boolean;
  budget_warning_threshold: number;
  
  // Statistics
  total_runs: number;
  successful_runs: number;
  failed_runs: number;
  success_rate: number;
  total_tokens_used: number;
  total_cost_usd: number;
  
  // Configuration
  default_model_tier: string;
  config: Record<string, any> | null;
  expected_run_duration_minutes: number | null;  // For staleness detection
}

export interface AgentRunSummary {
  id: string;
  agent_slug: string;
  status: AgentRunStatus;
  trigger_type: string;
  trigger_reason: string | null;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  items_processed: number;
  items_created: number;
  tokens_used: number;
  cost_usd: number;
  model_used: string | null;
  error_message: string | null;
  created_at: string;
}

export interface RunStatistics {
  agent_slug: string;
  total_runs: number;
  completed_runs: number;
  failed_runs: number;
  avg_duration_seconds: number | null;
  min_duration_seconds: number | null;
  max_duration_seconds: number | null;
  avg_items_processed: number;
  total_tokens_used: number;
  total_cost_usd: number;
  schedule_interval_seconds: number;
  avg_utilization_percent: number | null;
}

export interface AgentUpdateRequest {
  is_enabled?: boolean;
  schedule_interval_seconds?: number;
  default_model_tier?: string;
  config?: Record<string, any>;
  expected_run_duration_minutes?: number;
}

export interface BudgetUpdateRequest {
  budget_limit?: number;
  budget_period?: BudgetPeriod;
  warning_threshold?: number;
}

// Task types
export type TaskType = 
  | 'campaign_action'
  | 'review_required'
  | 'follow_up'
  | 'personal'
  | 'system'
  | 'idea_action';

export type TaskStatus = 
  | 'created'
  | 'ready'
  | 'blocked'
  | 'deferred'
  | 'in_progress'
  | 'completed'
  | 'cancelled'
  | 'delegated';

export type TaskSortBy = 
  | 'priority'
  | 'due_date'
  | 'value'
  | 'value_per_hour'
  | 'created'
  | 'updated';

export interface Task {
  id: string;
  user_id: string;
  title: string;
  description: string | null;
  task_type: TaskType;
  due_date: string | null;
  estimated_value: number | null;
  estimated_effort_minutes: number | null;
  priority_score: number;
  status: TaskStatus;
  blocked_by: string | null;
  blocked_by_task_id: string | null;
  deferred_until: string | null;
  source_type: string | null;
  source_id: string | null;
  source_context: Record<string, any> | null;
  completed_at: string | null;
  completion_notes: string | null;
  actual_value: number | null;
  created_at: string;
  updated_at: string;
  last_viewed_at: string | null;
  // Computed properties
  is_overdue: boolean;
  is_actionable: boolean;
  value_per_hour: number | null;
}

export interface TaskCreate {
  title: string;
  description?: string;
  task_type?: TaskType;
  due_date?: string;
  estimated_value?: number;
  estimated_effort_minutes?: number;
  source_type?: string;
  source_id?: string;
  source_context?: Record<string, any>;
}

export interface TaskUpdate {
  title?: string;
  description?: string;
  task_type?: TaskType;
  due_date?: string;
  estimated_value?: number;
  estimated_effort_minutes?: number;
  status?: TaskStatus;
  blocked_by?: string;
  blocked_by_task_id?: string;
  deferred_until?: string;
  source_context?: Record<string, any>;
}

export interface TaskCounts {
  created: number;
  ready: number;
  blocked: number;
  deferred: number;
  in_progress: number;
  completed: number;
  cancelled: number;
  delegated: number;
  overdue: number;
  due_today: number;
  active: number;
}

export interface TaskListResponse {
  tasks: Task[];
  total: number;
  limit: number;
  offset: number;
}

export interface TaskSummary {
  counts: TaskCounts;
  top_tasks: Task[];
  overdue_tasks: Task[];
  due_soon_tasks: Task[];
}

// Ideas types
export interface IdeaCounts {
  new: number;
  opportunity: number;
  tool: number;
  processed: number;
  total: number;
}

export interface Idea {
  id: string;
  original_content: string;
  reformatted_content: string;
  distilled_content: string | null;
  status: 'new' | 'opportunity' | 'tool' | 'processed' | 'archived';
  source: 'brainstorm' | 'conversation' | 'manual';
  created_at: string;
  reviewed_at: string | null;
  reviewed_by_agent: string | null;
  review_notes: string | null;
  processed_at: string | null;
}

// =============================================================================
// Campaign Learning Types (Phase 5: Agent Intelligence)
// =============================================================================

export type PatternType = 
  | 'execution_sequence'
  | 'input_collection'
  | 'tool_combination'
  | 'error_recovery'
  | 'optimization'
  | 'timing';

export type PatternStatus = 'active' | 'deprecated' | 'experimental';

export type LessonCategory = 
  | 'failure'
  | 'inefficiency'
  | 'user_friction'
  | 'budget_issue'
  | 'timing'
  | 'tool_issue';

export type RevisionTrigger = 
  | 'task_failure'
  | 'stream_blocked'
  | 'budget_concern'
  | 'user_feedback'
  | 'new_information'
  | 'optimization'
  | 'external_change';

export type SuggestionType = 
  | 'optimization'
  | 'warning'
  | 'opportunity'
  | 'cost_saving'
  | 'time_saving'
  | 'risk_mitigation';

export type SuggestionStatus = 
  | 'pending'
  | 'accepted'
  | 'rejected'
  | 'auto_applied'
  | 'expired';

export interface CampaignPattern {
  id: string;
  name: string;
  description: string;
  pattern_type: PatternType;
  status: PatternStatus;
  confidence_score: number;
  pattern_data: Record<string, any>;
  applicability_conditions: Record<string, any>;
  times_applied: number;
  times_successful: number;
  success_rate: number;
  last_applied_at: string | null;
  source_campaign_id: string | null;
  is_global: boolean;
  tags: string[] | null;
  created_at: string;
  updated_at: string;
}

export interface PatternListResponse {
  patterns: CampaignPattern[];
  total: number;
  limit: number;
  offset: number;
}

export interface CampaignLesson {
  id: string;
  title: string;
  description: string;
  category: LessonCategory;
  context: Record<string, any>;
  trigger_event: string;
  impact_severity: 'low' | 'medium' | 'high' | 'critical';
  budget_impact: number | null;
  time_impact_minutes: number | null;
  prevention_steps: string[];
  detection_signals: string[];
  source_campaign_id: string;
  times_applied: number;
  tags: string[] | null;
  created_at: string;
}

export interface LessonListResponse {
  lessons: CampaignLesson[];
  total: number;
  limit: number;
  offset: number;
}

export interface PlanRevision {
  id: string;
  campaign_id: string;
  revision_number: number;
  trigger: RevisionTrigger;
  trigger_details: string;
  plan_before: Record<string, any>;
  plan_after: Record<string, any>;
  changes_summary: string;
  tasks_added: number;
  tasks_removed: number;
  tasks_modified: number;
  streams_added: number;
  streams_removed: number;
  reasoning: string;
  expected_improvement: string | null;
  outcome_assessed: boolean;
  outcome_success: boolean | null;
  outcome_notes: string | null;
  initiated_by: 'agent' | 'user';
  approved_by_user: boolean;
  created_at: string;
  outcome_assessed_at: string | null;
}

export interface ProactiveSuggestion {
  id: string;
  campaign_id: string;
  suggestion_type: SuggestionType;
  title: string;
  description: string;
  status: SuggestionStatus;
  urgency: 'low' | 'medium' | 'high' | 'critical';
  confidence: number;
  evidence: Record<string, any>;
  based_on_patterns: string[] | null;
  based_on_lessons: string[] | null;
  recommended_action: Record<string, any>;
  estimated_benefit: string | null;
  estimated_cost: number | null;
  can_auto_apply: boolean;
  user_feedback: string | null;
  outcome_tracked: boolean;
  actual_benefit: string | null;
  expires_at: string | null;
  is_expired: boolean;
  created_at: string;
}

export interface SuggestionListResponse {
  suggestions: ProactiveSuggestion[];
  total: number;
  pending_count: number;
  limit: number;
  offset: number;
}

export interface LearningStats {
  total_patterns: number;
  active_patterns: number;
  avg_pattern_success_rate: number;
  total_lessons: number;
  lessons_by_category: Record<string, number>;
  total_suggestions: number;
  suggestions_accepted: number;
  suggestions_rejected: number;
  acceptance_rate: number;
}

// =============================================================================
// Tool Health Types
// =============================================================================

export type HealthStatus = 'healthy' | 'degraded' | 'unhealthy' | 'unknown';

export interface ToolHealthCheck {
  id: string;
  tool_id: string;
  status: HealthStatus;
  message: string | null;
  response_time_ms: number | null;
  check_type: string;
  details: Record<string, any> | null;
  is_automatic: boolean;
  checked_at: string;
}

export interface ToolHealthStatus {
  tool_id: string;
  tool_name: string;
  status: HealthStatus;
  message: string | null;
  response_time_ms: number | null;
  last_checked: string | null;
  health_check_enabled: boolean;
  health_check_interval_minutes: number;
}

export interface ToolHealthSummary {
  total_tools: number;
  healthy: number;
  degraded: number;
  unhealthy: number;
  unknown: number;
  health_checks_enabled: number;
  last_updated: string;
}

export interface ToolHealthSettings {
  health_check_enabled: boolean;
  health_check_interval_minutes: number;
}

export interface ToolHealthCheckResult {
  tool_id: string;
  tool_name: string;
  status: HealthStatus;
  message: string;
  response_time_ms: number | null;
  details: Record<string, any> | null;
  checked_at: string;
}

// =============================================================================
// Media Library
// =============================================================================

export interface ToolMediaSummary {
  slug: string;
  display_name: string;
  icon: string;
  file_count: number;
  total_size_bytes: number;
  newest_file_date: string | null;
  media_types: string[];
}

export interface MediaFile {
  filename: string;
  size_bytes: number;
  created_at: string;
  modified_at: string;
  media_type: string;
  mime_type: string;
  extension: string;
  has_thumbnail: boolean;
  download_url: string;
  thumbnail_url: string | null;
}

export interface MediaFileList {
  files: MediaFile[];
  total_count: number;
  total_size_bytes: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface MediaStats {
  total_files: number;
  total_size_bytes: number;
  by_type: Record<string, number>;
  by_tool: Record<string, number>;
}
