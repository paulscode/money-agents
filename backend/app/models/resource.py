"""Resource, JobQueue, and ToolExecution models for managing shared resources and job execution."""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text, JSON, BigInteger
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class ResourceCategory(str, enum.Enum):
    """Category of resource - determines behavior."""
    COMPUTE = "compute"  # Queue-based: GPU, CPU, custom compute
    CAPACITY = "capacity"  # Space-based: storage volumes


class ToolExecutionStatus(str, enum.Enum):
    """Status of a tool execution."""
    PENDING = "pending"  # Waiting to start
    RUNNING = "running"  # Currently executing
    COMPLETED = "completed"  # Finished successfully
    FAILED = "failed"  # Failed with error
    TIMEOUT = "timeout"  # Execution timed out
    CANCELLED = "cancelled"  # Cancelled by user/system


class ToolExecution(Base):
    """Track individual tool executions for auditing and debugging."""
    __tablename__ = "tool_executions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Tool and context
    tool_id = Column(PGUUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    message_id = Column(PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    triggered_by_user_id = Column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    campaign_id = Column(PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_name = Column(String(100), nullable=True)  # Which agent triggered this
    
    # Execution details
    status = Column(
        Enum(ToolExecutionStatus, name="tool_execution_status", values_callable=lambda x: [e.value for e in x]),
        default=ToolExecutionStatus.PENDING,
        nullable=False,
        index=True
    )
    input_params = Column(JSON, nullable=True)  # Parameters passed to the tool
    output_result = Column(JSON, nullable=True)  # Result from the tool
    error_message = Column(Text, nullable=True)  # Error details if failed
    
    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)  # Execution duration in milliseconds
    
    # Cost tracking
    cost_units = Column(Integer, nullable=True)  # API calls, tokens, etc.
    cost_details = Column(JSON, nullable=True)  # Detailed cost breakdown
    
    # Metadata
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    
    # Relationships
    tool = relationship("Tool")
    conversation = relationship("Conversation")
    message = relationship("Message")
    triggered_by = relationship("User")
    campaign = relationship("Campaign", foreign_keys=[campaign_id])


class Resource(Base):
    """Model for system resources (GPUs, storage volumes, etc.) that tools may require.
    
    Resources can be local (on the central server) or remote (on agent machines).
    Remote resources are identified by (agent_hostname, local_name) pairs.
    """
    __tablename__ = "resources"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(String(255), nullable=False, unique=True, index=True)
    resource_type = Column(String(50), nullable=False, index=True)  # 'gpu', 'cpu', 'storage', 'custom', etc.
    category = Column(
        String(50),
        nullable=False,
        default=ResourceCategory.COMPUTE.value,
        index=True
    )
    status = Column(String(50), nullable=False, index=True)  # 'available', 'in_use', 'maintenance', 'disabled'
    is_system_resource = Column(Boolean, nullable=False, default=False)  # True for auto-detected resources
    resource_metadata = Column("metadata", JSON, nullable=True)  # Store additional info like GPU model, memory, etc.
    
    # Remote agent association - TWO ways to reference:
    # 1. remote_agent_id: FK to agent's UUID (for joins)
    # 2. agent_hostname: FK to agent's hostname (for human-readable queries and tool mapping)
    remote_agent_id = Column(PGUUID(as_uuid=True), ForeignKey("remote_agents.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_hostname = Column(String(255), ForeignKey("remote_agents.hostname", ondelete="SET NULL"), nullable=True, index=True)
    
    # Local name within the agent scope (e.g., "gpu-0", "nvme-storage")
    # Combined with agent_hostname, this uniquely identifies a remote resource
    local_name = Column(String(100), nullable=True)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    # Relationships
    tools = relationship("Tool", back_populates="resource")
    jobs = relationship("JobQueue", back_populates="resource")
    storage_reservations = relationship("StorageReservation", back_populates="resource", cascade="all, delete-orphan")
    storage_files = relationship("StorageFile", back_populates="resource", cascade="all, delete-orphan")
    remote_agent = relationship("RemoteAgent", back_populates="resources", foreign_keys=[remote_agent_id])
    remote_agent_by_hostname = relationship("RemoteAgent", back_populates="resources_by_hostname", foreign_keys=[agent_hostname], primaryjoin="Resource.agent_hostname == RemoteAgent.hostname")
    
    @property
    def qualified_name(self) -> str:
        """Full name including agent scope (e.g., 'workstation-01/gpu-0')."""
        if self.agent_hostname and self.local_name:
            return f"{self.agent_hostname}/{self.local_name}"
        return self.name
    
    @property
    def is_remote(self) -> bool:
        """True if this resource is on a remote agent."""
        return self.agent_hostname is not None


class JobQueue(Base):
    """Model for queued jobs that require resource locking."""
    __tablename__ = "job_queue"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tool_id = Column(PGUUID(as_uuid=True), ForeignKey("tools.id", ondelete="CASCADE"), nullable=False)
    resource_id = Column(PGUUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id = Column(PGUUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True)
    message_id = Column(PGUUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    
    # Remote agent that will execute this job (if remote)
    remote_agent_id = Column(PGUUID(as_uuid=True), ForeignKey("remote_agents.id", ondelete="SET NULL"), nullable=True, index=True)
    
    status = Column(String(50), nullable=False, index=True)  # 'queued', 'running', 'completed', 'failed', 'cancelled'
    parameters = Column(JSON, nullable=True)  # Tool execution parameters
    result = Column(JSON, nullable=True)  # Tool execution result
    error = Column(Text, nullable=True)  # Error message if failed
    
    # Expected duration for staleness detection
    expected_duration_minutes = Column(Integer, nullable=True)  # How long job should take (used for recovery)
    
    queued_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    resource = relationship("Resource", back_populates="jobs")
    conversation = relationship("Conversation")
    message = relationship("Message")
    remote_agent = relationship("RemoteAgent", back_populates="jobs")


class StorageReservation(Base):
    """
    Track temporary space reservations on storage resources.
    
    Agents reserve space before large operations (downloads, processing)
    to prevent overcommitting. Reservations auto-expire if not released.
    """
    __tablename__ = "storage_reservations"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    resource_id = Column(PGUUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True)
    
    agent_name = Column(String(100), nullable=False)  # Which agent made the reservation
    purpose = Column(Text, nullable=True)  # What the space is for
    bytes_reserved = Column(BigInteger, nullable=False)  # Amount of space reserved
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)  # Auto-expire if not released
    released_at = Column(DateTime(timezone=True), nullable=True)  # When reservation was released
    
    # Relationships
    resource = relationship("Resource", back_populates="storage_reservations")


class StorageFile(Base):
    """
    Track files stored by agents on storage resources.
    
    Enables cleanup of old/temporary files and space accounting.
    """
    __tablename__ = "storage_files"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    resource_id = Column(PGUUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False, index=True)
    
    file_path = Column(Text, nullable=False, unique=True, index=True)  # Full path to the file
    size_bytes = Column(BigInteger, nullable=False)  # File size
    
    agent_name = Column(String(100), nullable=True)  # Which agent created it
    purpose = Column(Text, nullable=True)  # What the file is for
    is_temporary = Column(Boolean, nullable=False, default=False)  # Can be auto-cleaned
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)  # For cleanup decisions
    
    # Relationships
    resource = relationship("Resource", back_populates="storage_files")


class RemoteAgentStatus(str, enum.Enum):
    """Status of a remote resource agent."""
    ONLINE = "online"  # Connected and ready
    OFFLINE = "offline"  # Not connected
    BUSY = "busy"  # Connected but at max capacity
    MAINTENANCE = "maintenance"  # Temporarily disabled
    ERROR = "error"  # In error state


class CampaignWorkerStatus(str, enum.Enum):
    """Status of a campaign worker."""
    ONLINE = "online"  # Connected and ready to accept campaigns
    OFFLINE = "offline"  # Not connected
    DRAINING = "draining"  # Not accepting new campaigns, finishing existing


class CampaignWorker(Base):
    """
    Registry of campaign workers.
    
    Campaign workers manage campaign execution (LLM decisions, state machine,
    tool dispatch). They can be:
    - Local: The backend server itself acts as a worker
    - Remote: A remote agent with campaign_capable=True
    
    Workers claim campaigns via the lease system and must send heartbeats
    to maintain their leases.
    """
    __tablename__ = "campaign_workers"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # Unique identifier for this worker (stable across reconnects)
    worker_id = Column(String(100), nullable=False, unique=True, index=True)
    
    # Human-readable hostname
    hostname = Column(String(255), nullable=False)
    
    # Worker type: 'local' (backend server) or 'remote' (agent)
    worker_type = Column(String(20), nullable=False)
    
    # Link to remote agent (if worker_type == 'remote')
    remote_agent_id = Column(PGUUID(as_uuid=True), ForeignKey("remote_agents.id", ondelete="SET NULL"), nullable=True)
    
    # Capacity - how many campaigns this worker can handle simultaneously
    campaign_capacity = Column(Integer, nullable=False, default=3)
    current_campaign_count = Column(Integer, nullable=False, default=0)
    
    # System resources (for scheduling decisions)
    ram_gb = Column(Integer, nullable=True)
    cpu_threads = Column(Integer, nullable=True)
    
    # Preferences for campaign assignment
    # e.g., ["gpu_heavy", "io_heavy", "quick_tasks"]
    preferences = Column(JSON, nullable=True, default=list)
    
    # Status
    status = Column(String(20), nullable=False, default=CampaignWorkerStatus.OFFLINE.value, index=True)
    
    # Connection tracking
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    
    # Relationships
    remote_agent = relationship("RemoteAgent", backref="campaign_worker")
    
    @property
    def has_capacity(self) -> bool:
        """True if worker can accept more campaigns."""
        return self.current_campaign_count < self.campaign_capacity
    
    @property
    def available_slots(self) -> int:
        """Number of additional campaigns this worker can accept."""
        return max(0, self.campaign_capacity - self.current_campaign_count)
    
    def __repr__(self) -> str:
        return f"<CampaignWorker {self.worker_id} ({self.status})>"


class RemoteAgent(Base):
    """
    Registry of remote resource agents.
    
    Remote agents are lightweight daemons running on worker machines
    (Linux or Windows) that execute jobs on behalf of the central broker.
    
    The hostname is the PRIMARY IDENTIFIER for agents - it's human-readable,
    stable, and unique per machine. display_name is an optional friendly alias.
    """
    __tablename__ = "remote_agents"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    
    # PRIMARY IDENTIFIER: hostname is the canonical way to reference an agent
    hostname = Column(String(255), nullable=False, unique=True, index=True)
    
    # Optional friendly display name (if None, use hostname)
    display_name = Column(String(100), nullable=True)
    
    api_key_hash = Column(String(255), nullable=False)  # Hashed API key for auth
    description = Column(Text, nullable=True)
    tags = Column(JSON, nullable=True, default=list)  # Searchable tags
    
    status = Column(String(50), nullable=False, default=RemoteAgentStatus.OFFLINE.value, index=True)
    max_concurrent_jobs = Column(Integer, nullable=False, default=1)
    
    # Capabilities snapshot (updated when agent connects)
    # Contains: cpu, memory, gpus, storage, platform info
    capabilities = Column(JSON, nullable=True)
    
    # Live stats (updated on heartbeat)
    # Contains: cpu_percent, memory usage, gpu stats
    live_stats = Column(JSON, nullable=True)
    
    # Connection tracking
    last_seen_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    connected_at = Column(DateTime(timezone=True), nullable=True)
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    
    # Network info
    ip_address = Column(String(50), nullable=True)
    
    # Admin
    is_enabled = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    
    # Relationships
    resources = relationship("Resource", back_populates="remote_agent", foreign_keys="[Resource.remote_agent_id]")
    resources_by_hostname = relationship("Resource", back_populates="remote_agent_by_hostname", foreign_keys="[Resource.agent_hostname]", primaryjoin="RemoteAgent.hostname == Resource.agent_hostname")
    jobs = relationship("JobQueue", back_populates="remote_agent")
    
    @property
    def name(self) -> str:
        """Display name, falling back to hostname."""
        return self.display_name or self.hostname
    
    def __repr__(self) -> str:
        return f"<RemoteAgent {self.hostname} ({self.status})>"
