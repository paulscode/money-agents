from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import String, Boolean, DateTime, Enum, DECIMAL, Integer, BigInteger, Text, Index, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class UserRole(str, enum.Enum):
    """User role enum."""
    ADMIN = "admin"
    USER = "user"
    PENDING = "pending"


class ToolStatus(str, enum.Enum):
    """Tool request and implementation status."""
    # Request phase
    REQUESTED = "requested"  # Initial request submitted
    UNDER_REVIEW = "under_review"  # Admin reviewing
    CHANGES_REQUESTED = "changes_requested"  # Admin requested changes
    APPROVED = "approved"  # Approved for implementation
    REJECTED = "rejected"  # Request rejected
    
    # Implementation phase
    IMPLEMENTING = "implementing"  # Being built
    TESTING = "testing"  # In testing
    BLOCKED = "blocked"  # Blocked by dependency/issue
    ON_HOLD = "on_hold"  # Paused
    
    # Completion phase
    IMPLEMENTED = "implemented"  # Live and available
    DEPRECATED = "deprecated"  # Still available but discouraged
    RETIRED = "retired"  # No longer available


class ToolCategory(str, enum.Enum):
    """Tool category classification."""
    API = "api"  # External API integration
    DATA_SOURCE = "data_source"  # Data fetching/storage
    AUTOMATION = "automation"  # Task automation
    ANALYSIS = "analysis"  # Data analysis/processing
    COMMUNICATION = "communication"  # Messaging/notifications


class User(Base):
    """User model."""
    __tablename__ = "users"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=UserRole.PENDING.value, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Profile fields
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Disclaimer acknowledgement
    disclaimer_acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    show_disclaimer_on_login: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, server_default="true")
    
    # Security: password change tracking (SA2-13) and account lockout (SA2-09)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False, server_default="0")
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    proposals: Mapped[list["Proposal"]] = relationship("Proposal", back_populates="user")
    campaigns: Mapped[list["Campaign"]] = relationship("Campaign", back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="created_by", foreign_keys="[Conversation.created_by_user_id]")
    requested_tools: Mapped[list["Tool"]] = relationship("Tool", back_populates="requester", foreign_keys="[Tool.requester_id]")
    assigned_tools: Mapped[list["Tool"]] = relationship("Tool", back_populates="assigned_to_user", foreign_keys="[Tool.assigned_to_id]")
    ideas: Mapped[list["UserIdea"]] = relationship("UserIdea", back_populates="user")
    strategic_context: Mapped[list["StrategicContextEntry"]] = relationship("StrategicContextEntry", back_populates="user")
    tasks: Mapped[list["Task"]] = relationship("Task", back_populates="user")
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="user")
    
    def __repr__(self) -> str:
        return f"<User {self.username}>"


class PasswordResetCode(Base):
    """Admin-generated password reset codes."""
    __tablename__ = "password_reset_codes"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    # Relationships
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    created_by: Mapped["User"] = relationship("User", foreign_keys=[created_by_id])

    def __repr__(self) -> str:
        return f"<PasswordResetCode user_id={self.user_id} expires_at={self.expires_at}>"


class SystemSetting(Base):
    """Global system settings (key-value store)."""
    __tablename__ = "system_settings"
    
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    
    def __repr__(self) -> str:
        return f"<SystemSetting {self.key}={self.value}>"


class Tool(Base):
    """Tool catalog model."""
    __tablename__ = "tools"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Basic Information
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    category: Mapped[ToolCategory] = mapped_column(
        Enum(ToolCategory, name="tool_category", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[Optional[list]] = mapped_column(JSONB, default=list, nullable=True)
    
    # Lifecycle & Status
    status: Mapped[ToolStatus] = mapped_column(
        Enum(ToolStatus, name="tool_status", values_callable=lambda x: [e.value for e in x]),
        default=ToolStatus.REQUESTED,
        nullable=False,
        index=True
    )
    
    # Assignment
    requester_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id"), 
        nullable=False,
        index=True
    )
    assigned_to_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id"), 
        nullable=True,
        index=True
    )
    approved_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("users.id"), 
        nullable=True
    )
    
    # Implementation Details
    implementation_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blockers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dependencies: Mapped[Optional[list]] = mapped_column(JSONB, default=list, nullable=True)
    
    # Timeline
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    implemented_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_completion_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Usage & Integration
    usage_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    example_code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    required_environment_variables: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    integration_complexity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low, medium, high
    
    # Resources & Costs
    cost_model: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # free, per_use, subscription, etc
    cost_details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    shared_resources: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Resource requirements (optional, can require multiple resources)
    # Single resource FK kept for backwards compatibility
    resource_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("resources.id", ondelete="SET NULL"), 
        nullable=True,
        index=True
    )
    # Multiple resource requirements stored as list of UUIDs
    # e.g., ["gpu-uuid", "storage-uuid"] for tools needing GPU + storage
    resource_ids: Mapped[Optional[list]] = mapped_column(JSONB, default=list, nullable=True)
    
    # Documentation
    strengths: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    weaknesses: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    best_use_cases: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_documentation_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    
    # Dynamic Execution Interface
    # interface_type: How to execute this tool (rest_api, cli, python_sdk, internal, mcp)
    # interface_config: Type-specific configuration (endpoints, auth, command templates, etc.)
    # input_schema: JSON Schema for validating inputs
    # output_schema: JSON Schema for validating/parsing outputs
    interface_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    interface_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    input_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    output_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=30)
    
    # =========================================================================
    # DISTRIBUTED EXECUTION - Agent availability and per-agent resource mapping
    # =========================================================================
    
    # Which agents can run this tool:
    # - null = local only (not distributed, runs on central server)
    # - [] = explicitly disabled everywhere
    # - ["pc1", "pc2"] = available on these specific agents (by hostname)
    # - ["*"] = available on all connected agents
    available_on_agents: Mapped[Optional[list]] = mapped_column(JSONB, default=None, nullable=True)
    
    # Per-agent resource requirements (by hostname and local resource name):
    # Keys are agent hostnames, values are lists of local resource names
    # Example: {"workstation-01": ["gpu-0"], "minipc-01": ["gpu-0", "storage"]}
    # 
    # This allows the same tool to have DIFFERENT resource requirements on
    # different machines. For example, Ollama on PC1 needs PC1's GPU, 
    # and on PC2 needs PC2's GPU - not the same global resource ID.
    agent_resource_map: Mapped[Optional[dict]] = mapped_column(JSONB, default=None, nullable=True)
    
    # =========================================================================
    # HUMAN-IN-LOOP APPROVAL
    # =========================================================================
    
    # If true, tool execution requires human approval before running
    # Used for high-risk operations (financial transactions, publishing, etc.)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Default urgency level for approval requests from this tool
    # LOW = can wait hours/days, CRITICAL = immediate attention
    approval_urgency: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low, medium, high, critical
    
    # Custom approval instructions for reviewers
    approval_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # =========================================================================
    # HEALTH CHECK & VALIDATION
    # =========================================================================
    
    # Current health status: healthy, degraded, unhealthy, unknown
    health_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="unknown")
    
    # Last health check timestamp
    last_health_check: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Last health check message (error details if unhealthy)
    health_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Response time from last health check (ms)
    health_response_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Enable automatic health checks (disabled by default)
    health_check_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Health check interval in minutes (default 60)
    health_check_interval_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=60)
    
    # Metadata
    version: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # low, medium, high, critical
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    requester: Mapped["User"] = relationship(
        "User", 
        back_populates="requested_tools",
        foreign_keys=[requester_id]
    )
    assigned_to_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="assigned_tools",
        foreign_keys=[assigned_to_id]
    )
    resource: Mapped[Optional["Resource"]] = relationship("Resource", back_populates="tools")
    
    __table_args__ = (
        Index('idx_tools_status_category', 'status', 'category'),
        Index('idx_tools_created_at', 'created_at'),
    )
    
    def __repr__(self) -> str:
        return f"<Tool {self.name} ({self.status})>"
    
    # =========================================================================
    # Helper methods for distributed execution
    # =========================================================================
    
    def is_distributed(self) -> bool:
        """True if this tool can run on remote agents."""
        return self.available_on_agents is not None
    
    def is_available_on_agent(self, hostname: str) -> bool:
        """Check if this tool can run on the given agent (by hostname)."""
        if self.available_on_agents is None:
            return False  # Local only
        if not self.available_on_agents:
            return False  # Explicitly disabled everywhere
        if "*" in self.available_on_agents:
            return True  # Available on all agents
        return hostname in self.available_on_agents
    
    def get_required_resources_for_agent(self, hostname: str) -> list[str]:
        """Get the local resource names required to run this tool on a specific agent.
        
        Returns:
            List of local resource names (e.g., ["gpu-0", "storage"])
        """
        if not self.agent_resource_map:
            return []
        return self.agent_resource_map.get(hostname, [])
    
    def get_available_agent_hostnames(self) -> list[str]:
        """Get list of agent hostnames this tool is available on.
        
        Returns:
            - Empty list if local only or disabled
            - ["*"] if available everywhere
            - List of specific hostnames otherwise
        """
        if self.available_on_agents is None:
            return []
        return list(self.available_on_agents)


class ProposalStatus(str, enum.Enum):
    """Proposal status enum."""
    DRAFT_FROM_SCOUT = "draft_from_scout"  # Auto-created from approved opportunity, awaiting refinement
    PENDING = "pending"
    PROPOSED = "proposed"           # Proposed to user for review
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUBMITTED = "submitted"         # Submitted for execution
    CHANGES_REQUESTED = "changes_requested"


class RiskLevel(str, enum.Enum):
    """Risk level enum."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Proposal(Base):
    """Proposal model."""
    __tablename__ = "proposals"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    agent_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    
    # Core Content
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detailed_description: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Status
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, name="proposal_status", values_callable=lambda x: [e.value for e in x]),
        default=ProposalStatus.PENDING,
        index=True
    )
    
    # Financial
    initial_budget: Mapped[float] = mapped_column(DECIMAL(12, 2), nullable=False)
    bitcoin_budget_sats: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # Bitcoin budget in satoshis
    recurring_costs: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    expected_returns: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Risk Assessment
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, name="risk_level", values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    risk_description: Mapped[str] = mapped_column(Text, nullable=False)
    stop_loss_threshold: Mapped[dict] = mapped_column(JSONB, nullable=False)
    
    # Success Criteria
    success_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False)
    
    # Requirements
    required_tools: Mapped[dict] = mapped_column(JSONB, nullable=False)
    required_inputs: Mapped[dict] = mapped_column(JSONB, nullable=False)
    
    # Implementation
    implementation_timeline: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Research Context (populated from Opportunity Scout when auto-created)
    research_context: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)  # Contains opportunity data, sources, agent analysis
    source_opportunity_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), 
        ForeignKey("opportunities.id", ondelete="SET NULL"), 
        nullable=True,
        index=True
    )
    
    # Similarity & Deduplication
    similar_proposals: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    similarity_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    
    # Metadata
    source: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    meta_data: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    
    # Timestamps
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships (user_id foreign key will be added in migration)
    user: Mapped["User"] = relationship("User", back_populates="proposals")
    campaigns: Mapped[list["Campaign"]] = relationship("Campaign", back_populates="proposal")
    source_opportunity: Mapped[Optional["Opportunity"]] = relationship(
        "Opportunity",
        foreign_keys=[source_opportunity_id],
        backref="derived_proposals"
    )
    
    __table_args__ = (
        Index('idx_proposals_submitted_at', 'submitted_at'),
    )
    
    def __repr__(self) -> str:
        return f"<Proposal {self.title}>"


class CampaignStatus(str, enum.Enum):
    """Campaign status enum."""
    INITIALIZING = "initializing"
    REQUIREMENTS_GATHERING = "requirements_gathering"  # Collecting required inputs
    EXECUTING = "executing"  # Actively running tasks
    MONITORING = "monitoring"  # Watching for results/thresholds
    WAITING_FOR_INPUTS = "waiting_for_inputs"
    ACTIVE = "active"
    PAUSED = "paused"
    PAUSED_FAILOVER = "paused_failover"  # Paused due to worker failure
    COMPLETED = "completed"
    TERMINATED = "terminated"
    FAILED = "failed"


class CampaignWorkerStatus(str, enum.Enum):
    """Campaign worker status enum."""
    ONLINE = "online"
    OFFLINE = "offline"
    DRAINING = "draining"  # Not accepting new campaigns, finishing existing


class Campaign(Base):
    """Campaign model."""
    __tablename__ = "campaigns"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("proposals.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    agent_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    
    # Status
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="campaign_status", values_callable=lambda x: [e.value for e in x]),
        default=CampaignStatus.INITIALIZING,
        index=True
    )
    
    # =========================================================================
    # DISTRIBUTED CAMPAIGN LEASING
    # =========================================================================
    
    # Lease tracking - which worker owns this campaign
    leased_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    lease_acquired_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Campaign assignment hints
    worker_affinity: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # Preferred worker
    resource_requirements: Mapped[Optional[list]] = mapped_column(JSONB, default=list, nullable=True)  # Required resources
    estimated_complexity: Mapped[Optional[str]] = mapped_column(String(20), default="medium", nullable=True)  # light/medium/heavy
    
    # =========================================================================
    
    # Financial Tracking
    budget_allocated: Mapped[float] = mapped_column(DECIMAL(12, 2), nullable=False)
    budget_spent: Mapped[float] = mapped_column(DECIMAL(12, 2), default=0)
    revenue_generated: Mapped[float] = mapped_column(DECIMAL(12, 2), default=0)
    
    # Bitcoin Budget (satoshis) — set from proposal.bitcoin_budget_sats
    bitcoin_budget_sats: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bitcoin_spent_sats: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    bitcoin_received_sats: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    
    # Metrics
    success_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False)
    performance_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    
    # Progress
    tasks_total: Mapped[int] = mapped_column(Integer, default=0)
    tasks_completed: Mapped[int] = mapped_column(Integer, default=0)
    current_phase: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Requirements Checklist
    requirements_checklist: Mapped[dict] = mapped_column(JSONB, nullable=False)
    all_requirements_met: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Multi-stream execution plan (generated by LLM from proposal)
    execution_plan: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    streams_parallel_execution: Mapped[bool] = mapped_column(Boolean, default=True)  # Enable parallel streams
    
    # Timestamps
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships (foreign keys will be added in migration)
    proposal: Mapped["Proposal"] = relationship("Proposal", back_populates="campaigns")
    user: Mapped["User"] = relationship("User", back_populates="campaigns")
    
    # Multi-stream execution relationships
    task_streams: Mapped[list["TaskStream"]] = relationship(
        "TaskStream", 
        back_populates="campaign", 
        cascade="all, delete-orphan",
        order_by="TaskStream.order_index"
    )
    tasks: Mapped[list["CampaignTask"]] = relationship(
        "CampaignTask", 
        back_populates="campaign", 
        cascade="all, delete-orphan"
    )
    input_requests: Mapped[list["UserInputRequest"]] = relationship(
        "UserInputRequest", 
        back_populates="campaign", 
        cascade="all, delete-orphan"
    )
    auto_approval_rules: Mapped[list["AutoApprovalRule"]] = relationship(
        "AutoApprovalRule", 
        back_populates="campaign", 
        cascade="all, delete-orphan"
    )
    
    # =========================================================================
    # Lease Helper Methods
    # =========================================================================
    
    def is_leased(self) -> bool:
        """True if campaign is currently leased to a worker."""
        if not self.leased_by or not self.lease_expires_at:
            return False
        return utc_now() < self.lease_expires_at
    
    def is_lease_expired(self) -> bool:
        """True if the lease has expired (past expiry time)."""
        if not self.lease_expires_at:
            return False
        return utc_now() > self.lease_expires_at
    
    def is_claimable(self) -> bool:
        """True if this campaign can be claimed by a worker."""
        claimable_statuses = {
            CampaignStatus.INITIALIZING,
            CampaignStatus.REQUIREMENTS_GATHERING,
            CampaignStatus.EXECUTING,
            CampaignStatus.MONITORING,
            CampaignStatus.WAITING_FOR_INPUTS,
            CampaignStatus.PAUSED_FAILOVER,
        }
        return self.status in claimable_statuses and not self.is_leased()
    
    def __repr__(self) -> str:
        return f"<Campaign {self.id} - {self.status.value}>"


class ConversationType(str, enum.Enum):
    """Conversation type enum."""
    PROPOSAL = "proposal"
    CAMPAIGN = "campaign"
    TOOL = "tool"
    GENERAL = "general"
    SPEND_ADVISOR = "spend_advisor"


class Conversation(Base):
    """Conversation model - shared by multiple users for collaboration."""
    __tablename__ = "conversations"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    created_by_user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    
    # What this conversation is about
    conversation_type: Mapped[ConversationType] = mapped_column(
        Enum(ConversationType, name="conversation_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        index=True
    )
    related_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    
    # Metadata
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=utc_now, 
        onupdate=utc_now
    )
    
    # Relationships
    created_by: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index('idx_conversation_type_related_id', 'conversation_type', 'related_id'),
    )
    
    def __repr__(self) -> str:
        return f"<Conversation {self.title or self.id}>"


class SenderType(str, enum.Enum):
    """Sender type enum."""
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class Message(Base):
    """Message model."""
    __tablename__ = "messages"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("conversations.id"), nullable=False, index=True)
    
    # Sender
    sender_type: Mapped[SenderType] = mapped_column(
        Enum(SenderType, name="sender_type", values_callable=lambda x: [e.value for e in x]),
        nullable=False
    )
    sender_id: Mapped[Optional[UUID]] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    
    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_format: Mapped[str] = mapped_column(String(20), default="markdown")
    
    # Attachments - file uploads associated with this message
    attachments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, server_default='[]')
    
    # Mentions - store extracted @mentions for efficient querying
    mentioned_user_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    
    # Metadata
    meta_data: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Enhanced token tracking for cost analysis
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    
    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    sender: Mapped[Optional["User"]] = relationship("User", foreign_keys=[sender_id])
    message_reads: Mapped[list["MessageRead"]] = relationship("MessageRead", back_populates="message", cascade="all, delete-orphan")
    
    def __repr__(self) -> str:
        return f"<Message {self.id} from {self.sender_type.value}>"


class MessageRead(Base):
    """Track which users have read which messages."""
    __tablename__ = "message_reads"
    
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("messages.id"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    
    # Relationships
    message: Mapped["Message"] = relationship("Message", back_populates="message_reads")
    user: Mapped["User"] = relationship("User")
    
    __table_args__ = (
        Index('idx_message_user_read', 'message_id', 'user_id', unique=True),
    )
    
    def __repr__(self) -> str:
        return f"<MessageRead {self.user_id} read {self.message_id}>"


# Import Resource and JobQueue models
from app.models.resource import Resource, JobQueue, ToolExecution, ToolExecutionStatus

# Import Opportunity Scout models
from app.models.opportunity import (
    OpportunityStatus,
    OpportunityType,
    RankingTier,
    TimeSensitivity,
    EffortLevel,
    StrategyStatus,
    InsightType,
    SummaryType,
    DiscoveryStrategy,
    Opportunity,
    StrategyOutcome,
    AgentInsight,
    MemorySummary,
    UserScoutSettings,
    ScoringRubric,
)

# Import Agent Scheduler models
from app.models.agent_scheduler import (
    AgentStatus,
    AgentRunStatus,
    BudgetPeriod,
    AgentDefinition,
    AgentRun,
    AgentEvent,
)

# Import Ideas models
from app.models.ideas import (
    IdeaStatus,
    IdeaSource,
    StrategicContextCategory,
    UserIdea,
    StrategicContextEntry,
)

# Import Tool Scout models
from app.models.tool_scout import (
    ToolKnowledgeCategory,
    ToolKnowledgeStatus,
    ToolKnowledge,
    ToolIdeaEntry,
    ToolStrategyStatus,
    ToolDiscoveryStrategy,
)

# Import Resource models
from app.models.resource import (
    ResourceCategory,
    ToolExecutionStatus,
    ToolExecution,
    Resource,
    JobQueue,
    StorageReservation,
    StorageFile,
    RemoteAgentStatus,
    RemoteAgent,
    CampaignWorkerStatus,
    CampaignWorker,
)

# Import Campaign Stream models (multi-stream execution)
from app.models.campaign_stream import (
    TaskStreamStatus,
    TaskStatus,
    TaskType,
    InputType,
    InputPriority,
    InputStatus,
    TaskStream,
    CampaignTask,
    UserInputRequest,
    AutoApprovalRule,
)

# Import Task models (user task management)
from app.models.task import (
    TaskType as UserTaskType,  # Renamed to avoid conflict with campaign TaskType
    TaskStatus as UserTaskStatus,
    Task,
)

# Import Notification models
from app.models.notification import (
    NotificationType,
    NotificationPriority,
    Notification,
)
# Import Campaign Learning models (Phase 5: Agent Intelligence)
from app.models.campaign_learning import (
    PatternType,
    PatternStatus,
    LessonCategory,
    RevisionTrigger,
    SuggestionType,
    SuggestionStatus,
    CampaignPattern,
    CampaignLesson,
    PlanRevision,
    ProactiveSuggestion,
)

# Import LLM Usage tracking model
from app.models.llm_usage import (
    LLMUsageSource,
    LLMUsage,
)

# Import Rate Limit models
from app.models.rate_limit import (
    RateLimitScope,
    RateLimitPeriod,
    ToolRateLimit,
    RateLimitViolation,
)

# Import Tool Approval models (human-in-loop)
from app.models.tool_approval import (
    ApprovalStatus,
    ApprovalUrgency,
    ToolApprovalRequest,
)

# Import Tool Health Check models
from app.models.tool_health import (
    HealthStatus,
    ToolHealthCheck,
)

# Import Bitcoin Budget models (Phase 2: Bitcoin Budget System)
from app.models.bitcoin_budget import (
    TransactionType,
    TransactionStatus,
    SpendApprovalStatus,
    SpendTrigger,
    BitcoinTransaction,
    BitcoinSpendApproval,
)