"""Resource management API endpoints."""
import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_active_user, get_db, get_current_admin
from app.core.rate_limit import limiter
from app.models import User
from app.models.resource import ResourceCategory
from app.schemas.resource import (
    ResourceCreate,
    ResourceUpdate,
    ResourceResponse,
    ResourceStatus,
    JobQueueResponse,
    StorageResourceCreate,
    StorageReservationCreate,
    StorageReservationResponse,
    StorageFileCreate,
    StorageFileResponse,
    StorageInfoResponse
)
from app.services import resource_service, job_queue_service
from app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_resource_response(r, storage_info: dict = None) -> ResourceResponse:
    """Build a ResourceResponse from a Resource model."""
    response = ResourceResponse(
        id=r.id,
        name=r.name,
        resource_type=r.resource_type,
        status=r.status,
        is_system_resource=r.is_system_resource,
        category=r.category if r.category else "compute",
        metadata=r.resource_metadata if isinstance(r.resource_metadata, dict) else {},
        created_at=r.created_at,
        updated_at=r.updated_at,
        jobs_queued=getattr(r, 'jobs_queued', 0),
        jobs_running=getattr(r, 'jobs_running', 0),
        # Remote agent association
        agent_hostname=r.agent_hostname,
        local_name=r.local_name,
    )
    
    # Add storage info for capacity resources
    if storage_info:
        response.total_bytes = storage_info.get("total_bytes")
        response.used_bytes = storage_info.get("used_bytes")
        response.available_bytes = storage_info.get("available_bytes")
        response.reserved_bytes = storage_info.get("reserved_bytes")
    
    return response


@router.post("/detect", status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def detect_system_resources(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    Auto-detect and initialize system resources (CPU, RAM, GPU, Storage).
    Resources are created with disabled status by default.
    Existing resources are updated with current system values.
    
    Admin only.
    """
    result = await resource_service.initialize_system_resources(db)
    
    types_str = ", ".join(result["types"]) if result["types"] else "none"
    return {
        "message": f"Detected {result['created']} new, updated {result['updated']} existing resource(s). Types: {types_str}",
        "created": result["created"],
        "updated": result["updated"],
        "types": result["types"],
    }


# Keep old endpoint for backwards compatibility
@router.post("/initialize-gpus", status_code=status.HTTP_201_CREATED, include_in_schema=False)
@limiter.limit("10/minute")
async def initialize_gpu_resources(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    DEPRECATED: Use /detect instead.
    Auto-detect and initialize GPU resources (Admin only).
    """
    result = await resource_service.initialize_system_resources(db)
    return {
        "message": f"Initialized {result['created']} resource(s)",
        "count": result["created"]
    }


@router.get("", response_model=List[ResourceResponse])
@limiter.limit("120/minute")
async def list_resources(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> List[ResourceResponse]:
    """Get all resources with job counts (Admin only)."""
    resources = await resource_service.get_all_resources(db)
    storage_service = StorageService(db)
    
    responses = []
    for r in resources:
        storage_info = None
        # Get storage info for capacity resources
        if r.category == ResourceCategory.CAPACITY.value and r.resource_type == "storage":
            try:
                info = await storage_service.get_storage_info(r.id)
                storage_info = {
                    "total_bytes": info["total_bytes"],
                    "used_bytes": info["used_bytes"],
                    "available_bytes": info["available_bytes"],
                    "reserved_bytes": info["reserved_bytes"],
                }
            except Exception:
                pass  # Fall back to no storage info
        
        responses.append(_build_resource_response(r, storage_info))
    
    return responses


@router.get("/{resource_id}", response_model=ResourceResponse)
@limiter.limit("120/minute")
async def get_resource(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ResourceResponse:
    """Get a specific resource (Admin only)."""
    resource = await resource_service.get_resource(db, resource_id)
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )
    
    storage_service = StorageService(db)
    storage_info = None
    
    # Get storage info for capacity resources
    if resource.category == ResourceCategory.CAPACITY.value and resource.resource_type == "storage":
        try:
            info = await storage_service.get_storage_info(resource_id)
            storage_info = {
                "total_bytes": info["total_bytes"],
                "used_bytes": info["used_bytes"],
                "available_bytes": info["available_bytes"],
                "reserved_bytes": info["reserved_bytes"],
            }
        except Exception:
            pass
    
    return _build_resource_response(resource, storage_info)


@router.post("", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_resource(
    request: Request,
    resource_data: ResourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ResourceResponse:
    """Create a new custom resource (Admin only)."""
    try:
        # Category is already a string from the request
        category = resource_data.category
        
        resource = await resource_service.create_resource(
            db,
            name=resource_data.name,
            resource_type=resource_data.resource_type,
            metadata=resource_data.metadata,
            category=category
        )
        return _build_resource_response(resource)
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Failed to create resource")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create resource"
        )


@router.patch("/{resource_id}/status", response_model=ResourceResponse)
@limiter.limit("60/minute")
async def update_resource_status(
    request: Request,
    resource_id: UUID,
    status_update: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ResourceResponse:
    """
    Update resource status (Admin only).
    
    Valid statuses:
    - available: Resource is ready for use
    - disabled: Resource is turned off (cannot be used)
    - maintenance: Resource is temporarily unavailable for maintenance
    - in_use: Resource is currently being used (automatically managed by job queue)
    
    When setting to 'maintenance' or 'disabled':
    - All queued jobs are automatically cancelled
    - Running jobs are allowed to complete (unless force_stop=true)
    
    Optional body parameters:
    - force_stop: bool - If true, also terminate running jobs (default: false)
    """
    from app.services import job_queue_service
    
    new_status = status_update.get("status")
    force_stop = status_update.get("force_stop", False)
    
    if not new_status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status is required"
        )
    
    valid_statuses = [
        ResourceStatus.AVAILABLE,
        ResourceStatus.DISABLED,
        ResourceStatus.MAINTENANCE,
        ResourceStatus.IN_USE
    ]
    
    if new_status not in valid_statuses:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}"
        )
    
    # If going offline (maintenance/disabled), clean up jobs first
    cleanup_result = None
    if new_status in [ResourceStatus.MAINTENANCE, ResourceStatus.DISABLED]:
        reason = f"Resource set to {new_status}"
        cleanup_result = await job_queue_service.cleanup_resource_jobs(
            db=db,
            resource_id=resource_id,
            force_stop_running=force_stop,
            reason=reason
        )
    
    resource = await resource_service.update_resource_status(db, resource_id, new_status)
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )
    
    response = _build_resource_response(resource)
    
    # Add cleanup info to response if applicable
    if cleanup_result:
        # Log cleanup for visibility
        import logging
        logger = logging.getLogger(__name__)
        logger.info(
            f"Resource {resource_id} set to {new_status}: "
            f"cancelled {cleanup_result['queued_cancelled']} queued jobs, "
            f"terminated running: {cleanup_result['running_terminated']}"
        )
    
    return response


@router.delete("/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_resource(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    """
    Delete a custom resource (Admin only).
    System resources (auto-detected GPUs) cannot be deleted.
    """
    success = await resource_service.delete_resource(db, resource_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete resource. It may be a system resource or not found."
        )


@router.get("/{resource_id}/queue", response_model=List[JobQueueResponse])
@limiter.limit("120/minute")
async def get_resource_queue(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> List[JobQueueResponse]:
    """Get the job queue for a resource (Admin only)."""
    resource = await resource_service.get_resource(db, resource_id)
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )
    
    jobs = await job_queue_service.get_resource_queue(db, resource_id)
    return [JobQueueResponse.model_validate(job) for job in jobs]


# ==================== Storage-Specific Endpoints ====================

@router.post("/storage", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_storage_resource(
    request: Request,
    storage_data: StorageResourceCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> ResourceResponse:
    """
    Create a new storage resource with a specific path (Admin only).
    
    Storage resources are capacity-based and track:
    - Total/used/available space
    - File reservations for pending operations
    - Tracked files stored by agents
    """
    storage_service = StorageService(db)
    
    try:
        resource = await storage_service.create_storage_resource(
            name=storage_data.name,
            path=storage_data.path,
            min_free_gb=storage_data.min_free_gb
        )
        
        # Get storage info for response
        info = await storage_service.get_storage_info(resource.id)
        storage_info = {
            "total_bytes": info["total_bytes"],
            "used_bytes": info["used_bytes"],
            "available_bytes": info["available_bytes"],
            "reserved_bytes": info["reserved_bytes"],
        }
        
        return _build_resource_response(resource, storage_info)
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operation failed"
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Failed to create storage resource")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create storage resource"
        )


@router.get("/{resource_id}/storage", response_model=StorageInfoResponse)
@limiter.limit("120/minute")
async def get_storage_info(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> StorageInfoResponse:
    """
    Get detailed storage information for a storage resource (Admin only).
    
    Returns space usage, active reservations, and tracked file statistics.
    """
    storage_service = StorageService(db)
    
    try:
        info = await storage_service.get_storage_info(resource_id)
        reservations = await storage_service.get_active_reservations(resource_id)
        files_stats = await storage_service.get_tracked_files_stats(resource_id)
        
        return StorageInfoResponse(
            resource_id=resource_id,
            name=info["name"],
            path=info["path"],
            total_bytes=info["total_bytes"],
            used_bytes=info["used_bytes"],
            reserved_bytes=info["reserved_bytes"],
            available_bytes=info["available_bytes"],
            min_free_bytes=info["min_free_bytes"],
            active_reservations=[
                StorageReservationResponse.model_validate(r) for r in reservations
            ],
            tracked_files_count=files_stats["count"],
            tracked_files_size=files_stats["total_size"]
        )
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )


@router.post("/{resource_id}/storage/scan")
@limiter.limit("10/minute")
async def scan_storage(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> dict:
    """
    Refresh storage space information for a storage resource (Admin only).
    
    Rescans the filesystem to update total/used/available bytes.
    """
    storage_service = StorageService(db)
    
    try:
        info = await storage_service.refresh_storage_info(resource_id)
        return {
            "message": "Storage scanned successfully",
            "total_bytes": info["total_bytes"],
            "used_bytes": info["used_bytes"],
            "available_bytes": info["available_bytes"],
        }
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )


@router.post("/{resource_id}/storage/reserve", response_model=StorageReservationResponse)
@limiter.limit("60/minute")
async def reserve_storage_space(
    request: Request,
    resource_id: UUID,
    reservation: StorageReservationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> StorageReservationResponse:
    """
    Reserve storage space for an upcoming operation.
    
    Reservations prevent overcommitting storage space. They should be
    released when the operation completes or fails.
    """
    storage_service = StorageService(db)
    
    try:
        result = await storage_service.reserve_space(
            resource_id=resource_id,
            bytes_needed=reservation.bytes_needed,
            agent_name=reservation.agent_name,
            purpose=reservation.purpose,
            ttl_minutes=reservation.ttl_minutes
        )
        return StorageReservationResponse.model_validate(result)
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operation failed"
        )


@router.delete("/{resource_id}/storage/reserve/{reservation_id}")
@limiter.limit("60/minute")
async def release_storage_reservation(
    request: Request,
    resource_id: UUID,
    reservation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """Release a storage reservation after operation completes."""
    storage_service = StorageService(db)
    
    success = await storage_service.release_reservation(reservation_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reservation not found"
        )
    
    return {"message": "Reservation released"}


@router.post("/{resource_id}/storage/files", response_model=StorageFileResponse)
@limiter.limit("60/minute")
async def register_storage_file(
    request: Request,
    resource_id: UUID,
    file_data: StorageFileCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> StorageFileResponse:
    """
    Register a file stored on a storage resource.
    
    Tracking files allows agents to understand what's using space
    and enables cleanup of old/temporary files.
    """
    storage_service = StorageService(db)
    
    try:
        result = await storage_service.register_file(
            resource_id=resource_id,
            file_path=file_data.file_path,
            size_bytes=file_data.size_bytes,
            agent_name=file_data.agent_name,
            purpose=file_data.purpose,
            is_temporary=file_data.is_temporary
        )
        return StorageFileResponse.model_validate(result)
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Operation failed"
        )


@router.get("/{resource_id}/storage/files", response_model=List[StorageFileResponse])
@limiter.limit("120/minute")
async def list_storage_files(
    request: Request,
    resource_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> List[StorageFileResponse]:
    """Get all tracked files on a storage resource (Admin only)."""
    storage_service = StorageService(db)
    
    try:
        files = await storage_service.get_tracked_files(resource_id)
        return [StorageFileResponse.model_validate(f) for f in files]
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )


@router.delete("/{resource_id}/storage/files/{file_id}")
@limiter.limit("60/minute")
async def unregister_storage_file(
    request: Request,
    resource_id: UUID,
    file_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """Unregister a tracked file (when deleted from storage)."""
    storage_service = StorageService(db)
    
    success = await storage_service.unregister_file(file_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found"
        )
    
    return {"message": "File unregistered"}


@router.get("/{resource_id}/storage/cleanable", response_model=List[StorageFileResponse])
@limiter.limit("120/minute")
async def find_cleanable_files(
    request: Request,
    resource_id: UUID,
    older_than_days: int = 30,
    temporary_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
) -> List[StorageFileResponse]:
    """
    Find files that can potentially be cleaned up (Admin only).
    
    Returns files older than the specified number of days,
    optionally filtering to only temporary files.
    """
    storage_service = StorageService(db)
    
    try:
        files = await storage_service.find_cleanable_files(
            resource_id=resource_id,
            older_than_days=older_than_days,
            temporary_only=temporary_only
        )
        return [StorageFileResponse.model_validate(f) for f in files]
    except ValueError as e:
        logger.error("Resource operation failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found"
        )
