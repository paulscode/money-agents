"""Pydantic schemas for Opportunity Scout."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from enum import Enum

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums (mirror the SQLAlchemy enums)
# =============================================================================

class OpportunityStatus(str, Enum):
    DISCOVERED = "discovered"
    RESEARCHING = "researching"
    EVALUATED = "evaluated"
    PRESENTED = "presented"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISMISSED = "dismissed"
    EXPIRED = "expired"
    MERGED = "merged"


class OpportunityType(str, Enum):
    ARBITRAGE = "arbitrage"
    CONTENT = "content"
    SERVICE = "service"
    PRODUCT = "product"
    AUTOMATION = "automation"
    AFFILIATE = "affiliate"
    INVESTMENT = "investment"
    OTHER = "other"


class RankingTier(str, Enum):
    TOP_PICK = "top_pick"
    PROMISING = "promising"
    MAYBE = "maybe"
    UNLIKELY = "unlikely"


class TimeSensitivity(str, Enum):
    IMMEDIATE = "immediate"
    SHORT = "short"
    MEDIUM = "medium"
    EVERGREEN = "evergreen"


class EffortLevel(str, Enum):
    MINIMAL = "minimal"
    MODERATE = "moderate"
    SIGNIFICANT = "significant"
    MAJOR = "major"


class StrategyStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    RETIRED = "retired"
    EXPERIMENTAL = "experimental"


class InsightType(str, Enum):
    PRINCIPLE = "principle"
    PATTERN = "pattern"
    ANTI_PATTERN = "anti_pattern"
    HYPOTHESIS = "hypothesis"
    VALIDATED = "validated"


class SummaryType(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


# =============================================================================
# Discovery Strategy Schemas
# =============================================================================

class DiscoveryStrategyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., max_length=10_000)
    strategy_type: str = Field(..., max_length=50, pattern="^(search|monitor|analyze|combine)$")
    search_queries: Optional[List[str]] = []
    source_types: Optional[List[str]] = []
    filters: Optional[Dict[str, Any]] = {}
    schedule: str = Field("on_demand", max_length=100)


class DiscoveryStrategyCreate(DiscoveryStrategyBase):
    created_by: str = Field("agent", max_length=100)


class DiscoveryStrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    description: Optional[str] = Field(None, max_length=10_000)
    search_queries: Optional[List[str]] = None
    source_types: Optional[List[str]] = None
    filters: Optional[Dict[str, Any]] = None
    schedule: Optional[str] = Field(None, max_length=100)
    status: Optional[StrategyStatus] = None
    agent_notes: Optional[str] = Field(None, max_length=10_000)
    improvement_ideas: Optional[List[str]] = None


class DiscoveryStrategyResponse(DiscoveryStrategyBase):
    id: UUID
    created_at: datetime
    updated_at: datetime
    created_by: str
    status: StrategyStatus
    times_executed: int
    last_executed: Optional[datetime]
    opportunities_found: int
    opportunities_approved: int
    opportunities_rejected: int
    effectiveness_score: Optional[float]
    agent_notes: Optional[str]
    improvement_ideas: Optional[List[str]]
    parent_strategy_id: Optional[UUID]
    
    class Config:
        from_attributes = True


# =============================================================================
# Opportunity Schemas
# =============================================================================

class RevenuePotential(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    timeframe: Optional[str] = Field(None, max_length=50)  # "monthly", "yearly", "one-time"
    recurring: bool = False


class CostEstimate(BaseModel):
    upfront: Optional[float] = None
    ongoing: Optional[float] = None
    currency: str = Field("USD", max_length=10)


class OpportunityBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    summary: str = Field(..., max_length=5_000)
    opportunity_type: OpportunityType = OpportunityType.OTHER


class OpportunityCreate(OpportunityBase):
    source_type: str = Field(..., max_length=100)
    source_query: Optional[str] = Field(None, max_length=2_000)
    source_urls: Optional[List[str]] = []
    raw_signal: Optional[str] = Field(None, max_length=50_000)
    discovery_strategy_id: Optional[UUID] = None


class OpportunityUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=300)
    summary: Optional[str] = Field(None, max_length=5_000)
    opportunity_type: Optional[OpportunityType] = None
    status: Optional[OpportunityStatus] = None
    initial_assessment: Optional[str] = Field(None, max_length=50_000)
    detailed_analysis: Optional[str] = Field(None, max_length=50_000)
    confidence_score: Optional[float] = Field(None, ge=0, le=1)
    time_sensitivity: Optional[TimeSensitivity] = None
    estimated_effort: Optional[EffortLevel] = None
    estimated_revenue_potential: Optional[RevenuePotential] = None
    score_breakdown: Optional[Dict[str, float]] = None
    overall_score: Optional[float] = Field(None, ge=0, le=1)
    ranking_tier: Optional[RankingTier] = None
    ranking_factors: Optional[Dict[str, Any]] = None
    required_tools: Optional[List[str]] = None
    required_skills: Optional[List[str]] = None
    estimated_cost: Optional[CostEstimate] = None
    blocking_requirements: Optional[List[str]] = None


class OpportunityResponse(OpportunityBase):
    id: UUID
    discovered_at: datetime
    updated_at: datetime
    discovery_strategy_id: Optional[UUID]
    source_type: str
    source_query: Optional[str]
    source_urls: Optional[List[str]]
    raw_signal: Optional[str]
    status: OpportunityStatus
    initial_assessment: Optional[str]
    detailed_analysis: Optional[str]
    confidence_score: Optional[float]
    time_sensitivity: Optional[TimeSensitivity]
    estimated_effort: Optional[EffortLevel]
    estimated_revenue_potential: Optional[Dict[str, Any]]
    score_breakdown: Optional[Dict[str, float]]
    overall_score: Optional[float]
    ranking_tier: Optional[RankingTier]
    ranking_factors: Optional[Dict[str, Any]]
    rank_position: Optional[int]
    required_tools: Optional[List[str]]
    required_skills: Optional[List[str]]
    estimated_cost: Optional[Dict[str, Any]]
    blocking_requirements: Optional[List[str]]
    presented_at: Optional[datetime]
    user_decision: Optional[str]
    user_feedback: Optional[str]
    proposal_id: Optional[UUID]
    similar_opportunity_ids: Optional[List[str]]
    derived_from_id: Optional[UUID]
    bulk_dismissed: bool
    bulk_dismiss_reason: Optional[str]
    
    class Config:
        from_attributes = True


class OpportunityListResponse(BaseModel):
    """Response for paginated opportunity list."""
    opportunities: List[OpportunityResponse]
    total: int
    hopper_status: Optional["HopperStatus"] = None


class OpportunityDecision(BaseModel):
    """User decision on an opportunity."""
    notes: Optional[str] = Field(None, max_length=5_000)


class BulkDismissRequest(BaseModel):
    """Request to bulk dismiss opportunities."""
    opportunity_ids: Optional[List[UUID]] = None  # Specific IDs
    below_score: Optional[float] = Field(None, ge=0, le=1)  # By score threshold
    tier: Optional[RankingTier] = None  # By tier
    reason: Optional[str] = Field(None, max_length=1_000)


class BulkDismissResponse(BaseModel):
    """Response from bulk dismiss operation."""
    dismissed_count: int
    message: str


# =============================================================================
# Strategy Outcome Schemas
# =============================================================================

class StrategyOutcomeCreate(BaseModel):
    strategy_id: UUID
    opportunity_id: Optional[UUID] = None
    execution_context: Optional[Dict[str, Any]] = None
    queries_run: Optional[List[str]] = []
    results_count: int = 0
    opportunities_discovered: int = 0
    quality_assessment: Optional[str] = Field(None, max_length=10_000)


class StrategyOutcomeUpdate(BaseModel):
    user_decision: Optional[str] = Field(None, max_length=100)
    user_feedback: Optional[str] = Field(None, max_length=10_000)
    what_worked: Optional[str] = Field(None, max_length=10_000)
    what_failed: Optional[str] = Field(None, max_length=10_000)
    suggested_adjustments: Optional[List[str]] = None


class StrategyOutcomeResponse(BaseModel):
    id: UUID
    strategy_id: UUID
    opportunity_id: Optional[UUID]
    executed_at: datetime
    execution_context: Optional[Dict[str, Any]]
    queries_run: Optional[List[str]]
    results_count: int
    opportunities_discovered: int
    quality_assessment: Optional[str]
    user_decision: Optional[str]
    user_feedback: Optional[str]
    what_worked: Optional[str]
    what_failed: Optional[str]
    suggested_adjustments: Optional[List[str]]
    
    class Config:
        from_attributes = True


# =============================================================================
# Agent Insight Schemas
# =============================================================================

class AgentInsightCreate(BaseModel):
    insight_type: InsightType
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., max_length=10_000)
    evidence: Optional[List[str]] = []
    confidence: float = Field(0.5, ge=0, le=1)
    domains: Optional[List[str]] = []
    conditions: Optional[Dict[str, Any]] = None


class AgentInsightUpdate(BaseModel):
    insight_type: Optional[InsightType] = None
    title: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = Field(None, max_length=10_000)
    evidence: Optional[List[str]] = None
    confidence: Optional[float] = Field(None, ge=0, le=1)
    domains: Optional[List[str]] = None
    conditions: Optional[Dict[str, Any]] = None


class AgentInsightResponse(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    last_validated: Optional[datetime]
    insight_type: InsightType
    title: str
    description: str
    evidence: Optional[List[str]]
    confidence: float
    domains: Optional[List[str]]
    conditions: Optional[Dict[str, Any]]
    times_applied: int
    times_confirmed: int
    times_contradicted: int
    parent_insight_id: Optional[UUID]
    superseded_by_id: Optional[UUID]
    
    class Config:
        from_attributes = True


# =============================================================================
# Memory Summary Schemas
# =============================================================================

class MemorySummaryResponse(BaseModel):
    id: UUID
    created_at: datetime
    period_start: datetime
    period_end: datetime
    summary_type: SummaryType
    executive_summary: str
    top_strategies: Optional[List[Dict[str, Any]]]
    failed_strategies: Optional[List[Dict[str, Any]]]
    new_insights: Optional[List[Dict[str, Any]]]
    opportunities_found: int
    opportunities_approved: int
    opportunities_rejected: int
    proposals_created: int
    successful_campaigns: int
    focus_areas: Optional[List[str]]
    avoid_areas: Optional[List[str]]
    experiments_suggested: Optional[List[str]]
    detailed_records_archived: int
    
    class Config:
        from_attributes = True


# =============================================================================
# User Scout Settings Schemas
# =============================================================================

class UserScoutSettingsBase(BaseModel):
    max_active_proposals: int = 10
    hopper_warning_threshold: int = 7
    auto_pause_discovery: bool = False
    max_backlog_size: int = 200
    auto_dismiss_below_score: float = 0.0
    auto_dismiss_types: Optional[List[OpportunityType]] = []
    default_sort: str = Field("score_desc", max_length=50)
    show_unlikely_tier: bool = True
    preferred_types: Optional[List[OpportunityType]] = []
    preferred_domains: Optional[List[str]] = []
    excluded_types: Optional[List[OpportunityType]] = []
    excluded_keywords: Optional[List[str]] = []
    custom_rubric_weights: Optional[Dict[str, float]] = None


class UserScoutSettingsUpdate(BaseModel):
    max_active_proposals: Optional[int] = None
    hopper_warning_threshold: Optional[int] = None
    auto_pause_discovery: Optional[bool] = None
    max_backlog_size: Optional[int] = None
    auto_dismiss_below_score: Optional[float] = None
    auto_dismiss_types: Optional[List[OpportunityType]] = None
    default_sort: Optional[str] = None
    show_unlikely_tier: Optional[bool] = None
    preferred_types: Optional[List[OpportunityType]] = None
    preferred_domains: Optional[List[str]] = None
    excluded_types: Optional[List[OpportunityType]] = None
    excluded_keywords: Optional[List[str]] = None
    custom_rubric_weights: Optional[Dict[str, float]] = None


class UserScoutSettingsResponse(BaseModel):
    id: Optional[UUID] = None
    user_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    max_active_proposals: int = 10
    hopper_warning_threshold: int = 8
    auto_pause_discovery: bool = True
    max_backlog_size: int = 200
    auto_dismiss_below_score: Optional[float] = None
    auto_dismiss_types: List[str] = []
    default_sort: Optional[str] = None
    show_unlikely_tier: bool = True
    preferred_types: List[str] = []
    preferred_domains: List[str] = []
    excluded_types: List[str] = []
    excluded_keywords: List[str] = []
    custom_rubric_weights: Optional[Dict[str, float]] = None
    
    @field_validator(
        'auto_dismiss_types', 
        'preferred_types', 
        'preferred_domains', 
        'excluded_types', 
        'excluded_keywords',
        mode='before'
    )
    @classmethod
    def convert_none_to_list(cls, v):
        """Convert None to empty list for JSONB fields that may be NULL in DB."""
        return v if v is not None else []
    
    class Config:
        from_attributes = True


# =============================================================================
# Scoring Rubric Schemas
# =============================================================================

class ScoringFactor(BaseModel):
    weight: float = Field(..., ge=0, le=1)
    description: str = Field(..., max_length=5_000)
    signals: Optional[List[str]] = []
    interpretation: Optional[str] = Field(None, max_length=5_000)


class ScoringRubricCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=10_000)
    factors: Dict[str, ScoringFactor]


class ScoringRubricResponse(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    version: int
    is_active: bool
    name: str
    description: Optional[str]
    factors: Dict[str, Any]
    opportunities_scored: int
    approval_rate: Optional[float]
    evolved_from_id: Optional[UUID]
    evolution_reason: Optional[str]
    
    class Config:
        from_attributes = True


# =============================================================================
# Hopper Status Schema
# =============================================================================

class HopperStatus(BaseModel):
    """Current status of the proposal hopper."""
    max_capacity: int
    active_proposals: int
    pending_approvals: int
    total_committed: int
    available_slots: int
    status: str  # "available", "warning", "full"
    can_accept_more: bool


# =============================================================================
# List Response Schemas
# =============================================================================

class DiscoveryStrategyListResponse(BaseModel):
    """Paginated list of discovery strategies."""
    strategies: List[DiscoveryStrategyResponse]
    total: int


class AgentInsightListResponse(BaseModel):
    """Paginated list of agent insights."""
    insights: List[AgentInsightResponse]
    total: int


# =============================================================================
# Agent Action Response Schemas
# =============================================================================

class StrategicPlanResponse(BaseModel):
    """Response from agent strategic planning."""
    success: bool
    message: str
    plan: Optional[str] = None
    strategies_created: List[str] = []
    tokens_used: Optional[int] = None
    llm_model: Optional[str] = None


class DiscoveryRunResponse(BaseModel):
    """Response from agent discovery run."""
    success: bool
    message: str
    opportunities_created: int
    strategies_run: int
    opportunity_ids: List[str] = []
    tokens_used: Optional[int] = None


class ReflectionResponse(BaseModel):
    """Response from agent reflection/learning."""
    success: bool
    message: str
    insights_created: int
    reflection: Optional[str] = None
    tokens_used: Optional[int] = None
    llm_model: Optional[str] = None


# =============================================================================
# Statistics Schema
# =============================================================================

class ScoutStatistics(BaseModel):
    """Overall Opportunity Scout statistics."""
    period_days: int
    opportunities: Dict[str, Any]
    strategies: Dict[str, Any]
    discovery_runs: int
    insights_count: int

