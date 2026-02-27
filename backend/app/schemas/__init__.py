from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from typing import Optional
from datetime import datetime
from uuid import UUID
import re
from app.models import ProposalStatus, RiskLevel, CampaignStatus, ConversationType, SenderType, UserRole, ToolStatus, ToolCategory


# ===== User Schemas =====

class UserBase(BaseModel):
    """Base user schema."""
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)


class UserCreate(UserBase):
    """Schema for creating a user."""
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator('password')
    @classmethod
    def password_complexity(cls, v: str) -> str:
        """Require at least one uppercase, one lowercase, one digit, and one special char."""
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one digit')
        if not re.search(r'[^A-Za-z0-9]', v):
            raise ValueError('Password must contain at least one special character')
        return v


class UserUpdate(BaseModel):
    """Schema for updating a user."""
    email: Optional[EmailStr] = None
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    password: Optional[str] = Field(None, min_length=8, max_length=128)
    current_password: Optional[str] = Field(None, min_length=1)
    display_name: Optional[str] = Field(None, max_length=100)
    avatar_url: Optional[str] = Field(None, max_length=500)

    @field_validator('password')
    @classmethod
    def password_complexity(cls, v: Optional[str]) -> Optional[str]:
        """Require at least one uppercase, one lowercase, one digit, and one special char."""
        if v is None:
            return v
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one digit')
        if not re.search(r'[^A-Za-z0-9]', v):
            raise ValueError('Password must contain at least one special character')
        return v


class UserResponse(UserBase):
    """Schema for user response (includes email — only for own profile or admin use)."""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    role: str
    is_active: bool
    is_superuser: bool
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    last_login: Optional[datetime]
    disclaimer_acknowledged_at: Optional[datetime] = None
    show_disclaimer_on_login: bool = True
    created_at: datetime
    updated_at: datetime


class UserPublicResponse(BaseModel):
    """Schema for public user info (no email — SA2-08)."""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    username: str
    role: str
    is_active: bool
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime


# ===== Auth Schemas =====

class Token(BaseModel):
    """Token response schema."""
    access_token: str
    token_type: str = "bearer"
    refresh_token: Optional[str] = None


class TokenData(BaseModel):
    """Token payload data."""
    user_id: Optional[UUID] = None


class LoginRequest(BaseModel):
    """Login request schema."""
    identifier: str = Field(..., max_length=255, description="Email or username")
    password: str = Field(..., min_length=1, max_length=128)


class ResetPasswordRequest(BaseModel):
    """Request to reset password using an admin-generated code."""
    code: str = Field(..., min_length=1, max_length=32, description="Admin-generated reset code")
    new_password: str = Field(..., min_length=8, max_length=128)

    @field_validator('new_password')
    @classmethod
    def password_complexity(cls, v: str) -> str:
        """Require at least one uppercase, one lowercase, one digit, and one special char."""
        if not re.search(r'[A-Z]', v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', v):
            raise ValueError('Password must contain at least one lowercase letter')
        if not re.search(r'[0-9]', v):
            raise ValueError('Password must contain at least one digit')
        if not re.search(r'[^A-Za-z0-9]', v):
            raise ValueError('Password must contain at least one special character')
        return v


class ResetCodeResponse(BaseModel):
    """Response after generating a password reset code."""
    code: str
    expires_at: datetime
    username: str


# ===== Tool Schemas =====

class ToolBase(BaseModel):
    """Base tool schema."""
    name: str = Field(..., min_length=1, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    category: ToolCategory
    description: str = Field(..., max_length=10_000)
    tags: Optional[list[str]] = Field(default_factory=list)
    
    # Implementation
    implementation_notes: Optional[str] = Field(None, max_length=10_000)
    blockers: Optional[str] = Field(None, max_length=5_000)
    dependencies: Optional[list[str]] = Field(default_factory=list)
    estimated_completion_date: Optional[datetime] = None
    
    # Usage & Integration
    usage_instructions: Optional[str] = Field(None, max_length=50_000)
    example_code: Optional[str] = Field(None, max_length=50_000)
    required_environment_variables: Optional[dict] = None
    integration_complexity: Optional[str] = Field(None, max_length=50)
    
    # Resources & Costs
    cost_model: Optional[str] = Field(None, max_length=100)
    cost_details: Optional[dict] = None
    shared_resources: Optional[dict] = None
    resource_ids: Optional[list[str]] = Field(default_factory=list, description="List of resource UUIDs this tool requires")
    
    # Documentation
    strengths: Optional[str] = Field(None, max_length=10_000)
    weaknesses: Optional[str] = Field(None, max_length=10_000)
    best_use_cases: Optional[str] = Field(None, max_length=10_000)
    external_documentation_url: Optional[str] = Field(None, max_length=2_000)
    
    # Metadata
    version: Optional[str] = Field(None, max_length=50)
    priority: Optional[str] = Field(None, max_length=50)
    
    # Dynamic execution interface
    interface_type: Optional[str] = Field(None, max_length=50)
    interface_config: Optional[dict] = None  # Type-specific configuration
    input_schema: Optional[dict] = None  # JSON Schema for inputs
    output_schema: Optional[dict] = None  # JSON Schema for outputs
    timeout_seconds: Optional[int] = 30  # Execution timeout
    
    # Distributed execution - agent availability
    # null = local only, [] = disabled everywhere, ["*"] = all agents, ["pc1", "pc2"] = specific agents
    available_on_agents: Optional[list[str]] = Field(
        default=None, 
        description="Agent hostnames where this tool can run. null=local only, ['*']=all, ['host1','host2']=specific"
    )
    
    # Per-agent resource requirements - maps hostname to list of local resource names
    # e.g., {"workstation-01": ["gpu-0"], "minipc-01": ["gpu-0", "storage"]}
    agent_resource_map: Optional[dict[str, list[str]]] = Field(
        default=None,
        description="Per-agent resource requirements. Keys are hostnames, values are lists of local resource names."
    )


class ToolCreate(ToolBase):
    """Schema for creating a tool request."""
    pass


class ToolUpdate(BaseModel):
    """Schema for updating a tool."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    slug: Optional[str] = Field(None, min_length=1, max_length=100)
    category: Optional[ToolCategory] = None
    description: Optional[str] = None
    tags: Optional[list[str]] = None
    
    # Implementation
    implementation_notes: Optional[str] = Field(None, max_length=10_000)
    blockers: Optional[str] = Field(None, max_length=5_000)
    dependencies: Optional[list[str]] = None
    estimated_completion_date: Optional[datetime] = None
    
    # Usage & Integration
    usage_instructions: Optional[str] = Field(None, max_length=50_000)
    example_code: Optional[str] = Field(None, max_length=50_000)
    required_environment_variables: Optional[dict] = None
    integration_complexity: Optional[str] = Field(None, max_length=50)
    
    # Resources & Costs
    cost_model: Optional[str] = Field(None, max_length=100)
    cost_details: Optional[dict] = None
    shared_resources: Optional[dict] = None
    resource_ids: Optional[list[str]] = None
    
    # Documentation
    strengths: Optional[str] = Field(None, max_length=10_000)
    weaknesses: Optional[str] = Field(None, max_length=10_000)
    best_use_cases: Optional[str] = Field(None, max_length=10_000)
    external_documentation_url: Optional[str] = Field(None, max_length=2_000)
    
    # Metadata
    version: Optional[str] = Field(None, max_length=50)
    priority: Optional[str] = Field(None, max_length=50)
    
    # Dynamic execution interface
    interface_type: Optional[str] = Field(None, max_length=50)
    interface_config: Optional[dict] = None
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    timeout_seconds: Optional[int] = None
    
    # Distributed execution - agent availability
    available_on_agents: Optional[list[str]] = None
    agent_resource_map: Optional[dict[str, list[str]]] = None
    
    # Status updates
    status: Optional[ToolStatus] = None


class ToolResponse(ToolBase):
    """Schema for tool response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    status: ToolStatus
    requester_id: UUID
    assigned_to_id: Optional[UUID]
    approved_by_id: Optional[UUID]
    requested_at: datetime
    approved_at: Optional[datetime]
    implemented_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    
    # Optional user details (populated via joins if needed)
    requester_username: Optional[str] = None
    assigned_to_username: Optional[str] = None
    unread_count: Optional[int] = 0


class AssignToolRequest(BaseModel):
    """Schema for assigning/reassigning a tool."""
    user_id: UUID


class UpdateToolStatusRequest(BaseModel):
    """Schema for updating tool status."""
    status: ToolStatus
    notes: Optional[str] = Field(None, max_length=5_000)


# ===== Proposal Schemas =====

class ProposalBase(BaseModel):
    """Base proposal schema."""
    title: str = Field(..., max_length=255)
    summary: str = Field(..., max_length=5_000)
    detailed_description: str = Field(..., max_length=50_000)
    initial_budget: float = Field(..., gt=0)
    bitcoin_budget_sats: Optional[int] = Field(None, ge=0, description="Bitcoin budget in satoshis")
    recurring_costs: Optional[dict] = None
    expected_returns: Optional[dict] = None
    risk_level: RiskLevel
    risk_description: str = Field(..., max_length=10_000)
    stop_loss_threshold: dict
    success_criteria: dict
    required_tools: dict
    required_inputs: dict
    implementation_timeline: Optional[dict] = None
    source: Optional[str] = Field(None, max_length=100)
    tags: Optional[dict] = None


class ProposalCreate(ProposalBase):
    """Schema for creating a proposal."""
    model_config = ConfigDict(populate_by_name=True)
    
    meta_data: Optional[dict] = Field(None, alias='metadata')


class ProposalUpdate(BaseModel):
    """Schema for updating a proposal."""
    model_config = ConfigDict(populate_by_name=True)
    
    title: Optional[str] = Field(None, max_length=255)
    summary: Optional[str] = Field(None, max_length=5_000)
    detailed_description: Optional[str] = Field(None, max_length=50_000)
    initial_budget: Optional[float] = Field(None, gt=0)
    bitcoin_budget_sats: Optional[int] = Field(None, ge=0)
    recurring_costs: Optional[dict] = None
    expected_returns: Optional[dict] = None
    risk_level: Optional[RiskLevel] = None
    risk_description: Optional[str] = Field(None, max_length=10_000)
    stop_loss_threshold: Optional[dict] = None
    success_criteria: Optional[dict] = None
    required_tools: Optional[dict] = None
    required_inputs: Optional[dict] = None
    implementation_timeline: Optional[dict] = None
    status: Optional[ProposalStatus] = None
    source: Optional[str] = Field(None, max_length=100)
    tags: Optional[dict] = None
    meta_data: Optional[dict] = Field(None, alias='metadata')


class ProposalResponse(ProposalBase):
    """Schema for proposal response."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    
    id: UUID
    user_id: UUID
    agent_id: Optional[UUID]
    status: ProposalStatus
    similar_proposals: Optional[dict]
    similarity_score: Optional[float]
    research_context: Optional[dict] = None  # Context from Opportunity Scout
    source_opportunity_id: Optional[UUID] = None  # Link to source opportunity
    meta_data: Optional[dict] = Field(None, serialization_alias='metadata')
    submitted_at: datetime
    reviewed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    has_campaign: Optional[bool] = None  # Populated from joined campaigns relationship
    campaign_id: Optional[UUID] = None  # ID of first associated campaign (if any)


# ===== Campaign Schemas =====

class CampaignBase(BaseModel):
    """Base campaign schema."""
    budget_allocated: float = Field(..., gt=0)
    bitcoin_budget_sats: Optional[int] = Field(None, ge=0, description="Bitcoin budget in satoshis")
    success_metrics: dict
    requirements_checklist: list


class CampaignCreate(CampaignBase):
    """Schema for creating a campaign."""
    proposal_id: UUID


class CampaignUpdate(BaseModel):
    """Schema for updating a campaign."""
    status: Optional[CampaignStatus] = None
    budget_spent: Optional[float] = Field(None, ge=0)
    revenue_generated: Optional[float] = Field(None, ge=0)
    bitcoin_budget_sats: Optional[int] = Field(None, ge=0)
    bitcoin_spent_sats: Optional[int] = Field(None, ge=0)
    bitcoin_received_sats: Optional[int] = Field(None, ge=0)
    success_metrics: Optional[dict] = None
    performance_data: Optional[dict] = None
    tasks_total: Optional[int] = Field(None, ge=0)
    tasks_completed: Optional[int] = Field(None, ge=0)
    current_phase: Optional[str] = Field(None, max_length=100)
    requirements_checklist: Optional[list] = None
    all_requirements_met: Optional[bool] = None


class CampaignResponse(CampaignBase):
    """Schema for campaign response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    proposal_id: UUID
    proposal_title: Optional[str] = None  # Populated from joined proposal
    user_id: UUID
    agent_id: Optional[UUID]
    status: CampaignStatus
    budget_spent: float
    revenue_generated: float
    bitcoin_budget_sats: Optional[int] = None
    bitcoin_spent_sats: int = 0
    bitcoin_received_sats: int = 0
    performance_data: Optional[dict]
    tasks_total: int
    tasks_completed: int
    current_phase: Optional[str]
    all_requirements_met: bool
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    last_activity_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


# ===== Conversation Schemas =====

class ConversationBase(BaseModel):
    """Base conversation schema."""
    conversation_type: ConversationType
    related_id: Optional[UUID] = None
    title: Optional[str] = Field(None, max_length=255)


class ConversationCreate(ConversationBase):
    """Schema for creating a conversation."""
    pass


class ConversationResponse(ConversationBase):
    """Schema for conversation response."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    
    id: UUID
    created_by_user_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
    unread_count: int = 0  # Will be populated by endpoint


# ===== Message Schemas =====

class MessageBase(BaseModel):
    """Base message schema."""
    model_config = ConfigDict(protected_namespaces=())
    
    content: str = Field(..., max_length=100_000)
    content_format: str = Field("markdown", max_length=50)
    metadata: Optional[dict] = None


class MessageCreate(MessageBase):
    """Schema for creating a message."""
    conversation_id: UUID
    sender_type: SenderType
    sender_id: Optional[UUID] = None
    tokens_used: Optional[int] = None
    model_used: Optional[str] = Field(None, max_length=100)


class MessageResponse(MessageBase):
    """Schema for message response."""
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    
    id: UUID
    conversation_id: UUID
    sender_type: SenderType
    sender_id: Optional[UUID]
    sender_username: Optional[str] = None  # Will be populated by endpoint
    tokens_used: Optional[int]
    model_used: Optional[str]
    created_at: datetime
    mentioned_user_ids: Optional[list[UUID]] = None
    attachments: Optional[list[dict]] = None
    is_read: bool = False  # Will be populated by endpoint
    
    # Map meta_data from model to metadata in response
    metadata: Optional[dict] = Field(None, validation_alias="meta_data")


class MarkMessagesReadRequest(BaseModel):
    """Schema for marking messages as read."""
    message_ids: list[UUID]


class UnreadCountResponse(BaseModel):
    """Schema for unread message count."""
    proposal_id: UUID | None = None
    tool_id: UUID | None = None
    unread_count: int


# ===== Common Response Schemas =====

class GenericMessageResponse(BaseModel):
    """Generic API message response."""
    message: str
    detail: Optional[str] = None


class PaginatedResponse(BaseModel):
    """Generic paginated response."""
    items: list
    total: int
    page: int
    page_size: int
    total_pages: int
