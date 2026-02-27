"""Resource and JobQueue schemas."""
from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field


class ResourceStatus:
    """Resource status constants."""
    AVAILABLE = "available"
    IN_USE = "in_use"
    MAINTENANCE = "maintenance"
    DISABLED = "disabled"


class ResourceType:
    """Resource type constants."""
    GPU = "gpu"
    CPU = "cpu"
    RAM = "ram"
    STORAGE = "storage"
    CUSTOM = "custom"


class ResourceCategory:
    """Resource category constants."""
    COMPUTE = "compute"  # Queue-based: GPU, CPU, custom compute
    CAPACITY = "capacity"  # Space-based: storage


class ResourceBase(BaseModel):
    """Base resource schema."""
    name: str = Field(..., max_length=255)
    resource_type: str = Field(..., max_length=50)
    status: str = Field(default=ResourceStatus.DISABLED, max_length=50)
    metadata: Optional[dict] = None


class ResourceCreate(ResourceBase):
    """Schema for creating a resource."""
    category: Optional[str] = Field(None, max_length=50)  # Defaults based on type


class StorageResourceCreate(BaseModel):
    """Schema for creating a storage resource."""
    name: str = Field(..., max_length=255, description="Display name for the storage resource")
    path: str = Field(..., max_length=500, description="Filesystem path (e.g., /mnt/storage)")
    min_free_gb: float = Field(default=10.0, description="Minimum GB to keep free as buffer")


class ResourceUpdate(BaseModel):
    """Schema for updating a resource."""
    name: Optional[str] = Field(None, max_length=255)
    resource_type: Optional[str] = Field(None, max_length=50)
    status: Optional[str] = Field(None, max_length=50)
    metadata: Optional[dict] = None


class ResourceResponse(ResourceBase):
    """Schema for resource response."""
    id: UUID
    is_system_resource: bool
    category: Optional[str] = "compute"
    created_at: datetime
    updated_at: datetime
    
    # Remote agent association
    agent_hostname: Optional[str] = None
    local_name: Optional[str] = None
    
    # Additional computed fields (for compute resources)
    jobs_queued: Optional[int] = 0
    jobs_running: Optional[int] = 0
    
    # Storage-specific fields (populated for capacity resources)
    total_bytes: Optional[int] = None
    used_bytes: Optional[int] = None
    available_bytes: Optional[int] = None
    reserved_bytes: Optional[int] = None
    
    class Config:
        from_attributes = True


# Storage-specific schemas
class StorageReservationCreate(BaseModel):
    """Schema for creating a storage reservation."""
    bytes_needed: int = Field(..., gt=0, description="Bytes to reserve")
    agent_name: str = Field(..., max_length=100)
    purpose: Optional[str] = Field(None, max_length=500)
    ttl_minutes: int = Field(default=60, description="Minutes until reservation expires")


class StorageReservationResponse(BaseModel):
    """Schema for storage reservation response."""
    id: UUID
    resource_id: UUID
    agent_name: str
    purpose: Optional[str]
    bytes_reserved: int
    expires_at: datetime
    created_at: datetime
    
    class Config:
        from_attributes = True


class StorageFileCreate(BaseModel):
    """Schema for registering a stored file."""
    file_path: str = Field(..., max_length=1_000, description="Path to the file on storage")
    size_bytes: int = Field(..., gt=0)
    agent_name: str = Field(..., max_length=100)
    purpose: Optional[str] = Field(None, max_length=500)
    is_temporary: bool = Field(default=False, description="Whether file can be auto-cleaned")


class StorageFileResponse(BaseModel):
    """Schema for stored file response."""
    id: UUID
    resource_id: UUID
    file_path: str
    size_bytes: int
    agent_name: str
    purpose: Optional[str]
    is_temporary: bool
    created_at: datetime
    last_accessed: Optional[datetime]
    
    class Config:
        from_attributes = True


class StorageInfoResponse(BaseModel):
    """Schema for storage resource info."""
    resource_id: UUID
    name: str
    path: str
    total_bytes: int
    used_bytes: int
    reserved_bytes: int
    available_bytes: int
    min_free_bytes: int
    active_reservations: List[StorageReservationResponse]
    tracked_files_count: int
    tracked_files_size: int


class JobStatus:
    """Job status constants."""
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobQueueBase(BaseModel):
    """Base job queue schema."""
    tool_id: UUID
    resource_id: UUID
    conversation_id: Optional[UUID] = None  # Optional for test jobs
    message_id: Optional[UUID] = None
    parameters: Optional[dict] = None


class JobQueueCreate(JobQueueBase):
    """Schema for creating a job."""
    pass


class JobQueueResponse(JobQueueBase):
    """Schema for job queue response."""
    id: UUID
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
    queued_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    class Config:
        from_attributes = True
