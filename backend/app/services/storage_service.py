"""
Storage Service - manages capacity-based storage resources.

Provides an interface for agents to:
- Check available space
- Reserve space before operations
- Track stored files
- Find files for cleanup
"""
import logging
import os
import shutil
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select, func, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import (
    Resource,
    ResourceCategory,
    StorageReservation,
    StorageFile,
)
from app.schemas.resource import ResourceStatus

logger = logging.getLogger(__name__)


class StorageService:
    """Service for managing storage resources."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # Space Queries
    # =========================================================================
    
    async def get_storage_info(self, resource_id: UUID) -> Dict[str, Any]:
        """
        Get comprehensive storage information for a resource.
        
        Returns:
            Dict with total, used, reserved, available bytes and file counts.
            
        Raises:
            ValueError: If resource not found or not a storage resource.
        """
        resource = await self._get_storage_resource(resource_id)
        if not resource:
            raise ValueError(f"Storage resource {resource_id} not found")
        
        # Get path from metadata
        path = resource.resource_metadata.get("path", "/") if resource.resource_metadata else "/"
        
        # Get actual disk usage
        try:
            total, used, free = shutil.disk_usage(path)
        except Exception as e:
            logger.error(f"Error getting disk usage for {path}: {e}")
            total, used, free = 0, 0, 0
        
        # Get active reservations
        reserved = await self._get_active_reservations_bytes(resource_id)
        
        # Get tracked files count and size
        tracked_result = await self.db.execute(
            select(
                func.count(StorageFile.id),
                func.coalesce(func.sum(StorageFile.size_bytes), 0)
            ).where(StorageFile.resource_id == resource_id)
        )
        tracked_count, tracked_bytes = tracked_result.one()
        
        # Calculate effective available (accounting for reservations)
        min_free = resource.resource_metadata.get("min_free_bytes", 10 * 1024 * 1024 * 1024) if resource.resource_metadata else 10 * 1024 * 1024 * 1024  # 10GB default
        effective_available = max(0, free - reserved - min_free)
        
        return {
            "resource_id": str(resource_id),
            "name": resource.name,
            "path": path,
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
            "reserved_bytes": reserved,
            "available_bytes": effective_available,
            "min_free_bytes": min_free,
            "tracked_files_count": tracked_count,
            "tracked_files_bytes": tracked_bytes,
            "last_scanned": utc_now().isoformat(),
        }
    
    async def get_available_space(self, resource_id: UUID) -> int:
        """
        Get available space in bytes for a storage resource.
        
        This accounts for:
        - Actual free space on disk
        - Active reservations
        - Minimum free space buffer
        
        Returns:
            Available bytes, or 0 if resource not found.
        """
        info = await self.get_storage_info(resource_id)
        return info["available_bytes"] if info else 0
    
    async def has_space(self, resource_id: UUID, bytes_needed: int) -> bool:
        """Check if a storage resource has enough available space."""
        available = await self.get_available_space(resource_id)
        return available >= bytes_needed
    
    # =========================================================================
    # Reservations
    # =========================================================================
    
    async def reserve_space(
        self,
        resource_id: UUID,
        bytes_needed: int,
        agent_name: str,
        purpose: Optional[str] = None,
        ttl_minutes: int = 60,
    ) -> StorageReservation:
        """
        Reserve space on a storage resource.
        
        Reservations are temporary holds that prevent overcommitting.
        They auto-expire if not released.
        
        Args:
            resource_id: Storage resource ID
            bytes_needed: Amount of space to reserve
            agent_name: Name of the agent making the reservation
            purpose: Optional description of what the space is for
            ttl_minutes: Minutes until reservation auto-expires
            
        Returns:
            StorageReservation if successful.
            
        Raises:
            ValueError: If insufficient space or resource not found.
        """
        # Check resource exists
        resource = await self._get_storage_resource(resource_id)
        if not resource:
            raise ValueError(f"Storage resource {resource_id} not found")
        
        # Check if enough space
        if not await self.has_space(resource_id, bytes_needed):
            available = await self.get_available_space(resource_id)
            raise ValueError(
                f"Insufficient space: {bytes_needed} bytes requested, "
                f"{available} bytes available on {resource.name}"
            )
        
        reservation = StorageReservation(
            id=uuid4(),
            resource_id=resource_id,
            agent_name=agent_name,
            purpose=purpose,
            bytes_reserved=bytes_needed,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
        )
        
        self.db.add(reservation)
        await self.db.commit()
        await self.db.refresh(reservation)
        
        logger.info(
            f"Created reservation {reservation.id}: {bytes_needed} bytes "
            f"for {agent_name} on resource {resource_id}"
        )
        
        return reservation
    
    async def release_reservation(self, reservation_id: UUID) -> bool:
        """
        Release a storage reservation.
        
        Call this after the operation completes (success or failure).
        
        Returns:
            True if released, False if not found.
        """
        result = await self.db.execute(
            select(StorageReservation).where(StorageReservation.id == reservation_id)
        )
        reservation = result.scalar_one_or_none()
        
        if not reservation:
            return False
        
        reservation.released_at = utc_now()
        await self.db.commit()
        
        logger.info(f"Released reservation {reservation_id}")
        return True
    
    async def cleanup_expired_reservations(self) -> int:
        """
        Clean up expired reservations.
        
        Returns:
            Number of reservations cleaned up.
        """
        now = utc_now()
        
        # Find expired, unreleased reservations
        result = await self.db.execute(
            select(StorageReservation).where(
                and_(
                    StorageReservation.expires_at < now,
                    StorageReservation.released_at.is_(None)
                )
            )
        )
        expired = result.scalars().all()
        
        for reservation in expired:
            reservation.released_at = now
            logger.info(
                f"Auto-released expired reservation {reservation.id} "
                f"({reservation.bytes_reserved} bytes for {reservation.agent_name})"
            )
        
        if expired:
            await self.db.commit()
        
        return len(expired)
    
    async def get_active_reservations(self, resource_id: UUID) -> List[StorageReservation]:
        """Get all active (non-expired, non-released) reservations for a resource."""
        now = utc_now()
        result = await self.db.execute(
            select(StorageReservation).where(
                and_(
                    StorageReservation.resource_id == resource_id,
                    StorageReservation.expires_at > now,
                    StorageReservation.released_at.is_(None)
                )
            ).order_by(StorageReservation.created_at)
        )
        return list(result.scalars().all())
    
    # =========================================================================
    # File Tracking
    # =========================================================================
    
    async def register_file(
        self,
        resource_id: UUID,
        file_path: str,
        size_bytes: int,
        agent_name: Optional[str] = None,
        purpose: Optional[str] = None,
        is_temporary: bool = False,
    ) -> StorageFile:
        """
        Register a file stored on a storage resource.
        
        Args:
            resource_id: Storage resource ID
            file_path: Full path to the file
            size_bytes: File size in bytes
            agent_name: Which agent created the file
            purpose: What the file is for
            is_temporary: If True, file can be auto-cleaned
            
        Returns:
            StorageFile record.
            
        Raises:
            ValueError: If resource not found or file already tracked.
        """
        resource = await self._get_storage_resource(resource_id)
        if not resource:
            raise ValueError(f"Storage resource {resource_id} not found")
        
        # Check if file already tracked
        existing = await self.db.execute(
            select(StorageFile).where(StorageFile.file_path == file_path)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"File already tracked: {file_path}")
        
        storage_file = StorageFile(
            id=uuid4(),
            resource_id=resource_id,
            file_path=file_path,
            size_bytes=size_bytes,
            agent_name=agent_name,
            purpose=purpose,
            is_temporary=is_temporary,
            last_accessed_at=utc_now(),
        )
        
        self.db.add(storage_file)
        await self.db.commit()
        await self.db.refresh(storage_file)
        
        logger.info(f"Registered file: {file_path} ({size_bytes} bytes)")
        return storage_file
    
    async def unregister_file(self, file_id_or_path) -> bool:
        """
        Unregister a tracked file (e.g., after deletion).
        
        Args:
            file_id_or_path: UUID or file path string
        
        Returns:
            True if unregistered, False if not found.
        """
        if isinstance(file_id_or_path, UUID):
            result = await self.db.execute(
                select(StorageFile).where(StorageFile.id == file_id_or_path)
            )
        else:
            result = await self.db.execute(
                select(StorageFile).where(StorageFile.file_path == file_id_or_path)
            )
        storage_file = result.scalar_one_or_none()
        
        if not storage_file:
            return False
        
        await self.db.delete(storage_file)
        await self.db.commit()
        
        logger.info(f"Unregistered file: {storage_file.file_path}")
        return True
    
    async def update_file_access(self, file_path: str) -> bool:
        """Update last_accessed_at for a tracked file."""
        result = await self.db.execute(
            select(StorageFile).where(StorageFile.file_path == file_path)
        )
        storage_file = result.scalar_one_or_none()
        
        if not storage_file:
            return False
        
        storage_file.last_accessed_at = utc_now()
        await self.db.commit()
        return True
    
    async def get_tracked_files(
        self,
        resource_id: UUID,
        include_temporary: bool = True,
    ) -> List[StorageFile]:
        """Get all tracked files for a storage resource."""
        query = select(StorageFile).where(StorageFile.resource_id == resource_id)
        
        if not include_temporary:
            query = query.where(StorageFile.is_temporary == False)
        
        result = await self.db.execute(query.order_by(StorageFile.created_at.desc()))
        return list(result.scalars().all())
    
    async def get_tracked_files_stats(self, resource_id: UUID) -> Dict[str, Any]:
        """Get statistics about tracked files for a storage resource."""
        result = await self.db.execute(
            select(
                func.count(StorageFile.id),
                func.coalesce(func.sum(StorageFile.size_bytes), 0)
            ).where(StorageFile.resource_id == resource_id)
        )
        count, total_size = result.one()
        return {
            "count": count,
            "total_size": total_size,
        }
    
    async def find_cleanable_files(
        self,
        resource_id: UUID,
        older_than_days: int = 30,
        temporary_only: bool = True,
        min_size_bytes: int = 0,
    ) -> List[StorageFile]:
        """
        Find files that can be cleaned up.
        
        Args:
            resource_id: Storage resource ID
            older_than_days: Files older than this are candidates
            temporary_only: Only return temporary files
            min_size_bytes: Minimum file size to consider
            
        Returns:
            List of StorageFile records that are cleanup candidates.
        """
        cutoff = utc_now() - timedelta(days=older_than_days)
        
        query = select(StorageFile).where(
            and_(
                StorageFile.resource_id == resource_id,
                or_(
                    StorageFile.last_accessed_at < cutoff,
                    StorageFile.last_accessed_at.is_(None)
                ),
                StorageFile.size_bytes >= min_size_bytes,
            )
        )
        
        if temporary_only:
            query = query.where(StorageFile.is_temporary == True)
        
        result = await self.db.execute(
            query.order_by(StorageFile.size_bytes.desc())
        )
        return list(result.scalars().all())
    
    # =========================================================================
    # Storage Resource Management
    # =========================================================================
    
    async def scan_storage(self, resource_id: UUID) -> Optional[Dict[str, Any]]:
        """
        Scan a storage resource and update its metadata with current values.
        
        Returns:
            Updated storage info, or None if resource not found.
        """
        resource = await self._get_storage_resource(resource_id)
        if not resource:
            return None
        
        # Get current info
        info = await self.get_storage_info(resource_id)
        
        # Update resource metadata
        if resource.resource_metadata is None:
            resource.resource_metadata = {}
        
        resource.resource_metadata.update({
            "total_bytes": info["total_bytes"],
            "last_scanned": info["last_scanned"],
        })
        
        await self.db.commit()
        
        logger.info(f"Scanned storage resource {resource_id}: {info['total_bytes']} bytes total")
        return info
    
    async def refresh_storage_info(self, resource_id: UUID) -> Optional[Dict[str, Any]]:
        """Alias for scan_storage - refresh storage information."""
        return await self.scan_storage(resource_id)
    
    async def get_all_storage_resources(self) -> List[Resource]:
        """Get all storage (capacity) resources."""
        result = await self.db.execute(
            select(Resource).where(
                Resource.category == ResourceCategory.CAPACITY.value
            ).order_by(Resource.name)
        )
        return list(result.scalars().all())
    
    async def create_storage_resource(
        self,
        name: str,
        path: str,
        min_free_gb: float = 10.0,
    ) -> Resource:
        """
        Create a custom storage resource.
        
        Args:
            name: Display name for the resource
            path: Mount point or directory path
            min_free_gb: Minimum free space to maintain (buffer)
            
        Returns:
            Created Resource.
            
        Raises:
            ValueError: If path is invalid or inaccessible.
        """
        # Validate path exists and is accessible
        if not os.path.exists(path):
            raise ValueError(f"Storage path does not exist: {path}")
        
        if not os.path.isdir(path):
            raise ValueError(f"Storage path is not a directory: {path}")
        
        # Get disk info
        try:
            total, used, free = shutil.disk_usage(path)
        except Exception as e:
            raise ValueError(f"Cannot access storage path {path}: {e}")
        
        resource = Resource(
            id=uuid4(),
            name=name,
            resource_type="storage",
            category=ResourceCategory.CAPACITY.value,
            status=ResourceStatus.DISABLED,
            is_system_resource=True,  # Storage resources are real hardware
            resource_metadata={
                "path": path,
                "total_bytes": total,
                "min_free_bytes": int(min_free_gb * 1024 * 1024 * 1024),
                "last_scanned": utc_now().isoformat(),
            },
        )
        
        self.db.add(resource)
        await self.db.commit()
        await self.db.refresh(resource)
        
        logger.info(f"Created storage resource: {name} at {path}")
        return resource
    
    # =========================================================================
    # Helpers
    # =========================================================================
    
    async def _get_storage_resource(self, resource_id: UUID) -> Optional[Resource]:
        """Get a storage resource by ID, validating it's a capacity resource."""
        result = await self.db.execute(
            select(Resource).where(
                and_(
                    Resource.id == resource_id,
                    Resource.category == ResourceCategory.CAPACITY.value
                )
            )
        )
        return result.scalar_one_or_none()
    
    async def _get_active_reservations_bytes(self, resource_id: UUID) -> int:
        """Get total bytes in active reservations for a resource."""
        now = utc_now()
        result = await self.db.execute(
            select(func.coalesce(func.sum(StorageReservation.bytes_reserved), 0)).where(
                and_(
                    StorageReservation.resource_id == resource_id,
                    StorageReservation.expires_at > now,
                    StorageReservation.released_at.is_(None)
                )
            )
        )
        return result.scalar() or 0


# =============================================================================
# Convenience Functions for Agents
# =============================================================================

async def get_storage_for_agent(
    db: AsyncSession,
    bytes_needed: int,
    prefer_resource_id: Optional[UUID] = None,
) -> Optional[UUID]:
    """
    Find a storage resource with enough space for an agent operation.
    
    Args:
        db: Database session
        bytes_needed: How much space is needed
        prefer_resource_id: Optional preferred storage resource
        
    Returns:
        Resource ID of a suitable storage, or None if none available.
    """
    service = StorageService(db)
    
    # Try preferred resource first
    if prefer_resource_id:
        if await service.has_space(prefer_resource_id, bytes_needed):
            return prefer_resource_id
    
    # Find any available storage with enough space
    resources = await service.get_all_storage_resources()
    
    for resource in resources:
        if resource.status != ResourceStatus.AVAILABLE:
            continue
        
        if await service.has_space(resource.id, bytes_needed):
            return resource.id
    
    return None


async def format_storage_for_prompt(db: AsyncSession) -> str:
    """
    Format available storage resources for agent prompts.
    
    Returns a markdown string describing available storage options.
    """
    service = StorageService(db)
    resources = await service.get_all_storage_resources()
    
    if not resources:
        return "No storage resources configured."
    
    lines = ["## Available Storage Resources\n"]
    
    for resource in resources:
        if resource.status == ResourceStatus.DISABLED:
            continue
        
        info = await service.get_storage_info(resource.id)
        if not info:
            continue
        
        total_gb = info["total_bytes"] / (1024 ** 3)
        available_gb = info["available_bytes"] / (1024 ** 3)
        used_pct = (info["used_bytes"] / info["total_bytes"] * 100) if info["total_bytes"] > 0 else 0
        
        lines.append(f"### {resource.name}")
        lines.append(f"- **Path:** `{info['path']}`")
        lines.append(f"- **Total:** {total_gb:.1f} GB")
        lines.append(f"- **Available:** {available_gb:.1f} GB ({100 - used_pct:.0f}% free)")
        lines.append(f"- **Status:** {resource.status}")
        lines.append("")
    
    return "\n".join(lines)
