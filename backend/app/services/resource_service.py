"""Resource management service including system resource auto-detection."""
import subprocess
import logging
import platform
import os
import shutil
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import Resource, ResourceCategory, JobQueue
from app.schemas.resource import ResourceStatus, ResourceType, JobStatus

logger = logging.getLogger(__name__)


# =============================================================================
# System Resource Detection
# =============================================================================

async def detect_gpus() -> List[dict]:
    """
    Detect available GPUs on the system using nvidia-smi.
    
    Returns:
        List of GPU information dictionaries.
    """
    try:
        # Try to run nvidia-smi to detect NVIDIA GPUs
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode != 0:
            logger.info("nvidia-smi not available or no NVIDIA GPUs detected")
            return []
        
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 4:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "memory_mb": int(parts[2]),
                        "driver_version": parts[3],
                    })
                elif len(parts) >= 3:
                    gpus.append({
                        "index": int(parts[0]),
                        "name": parts[1],
                        "memory_mb": int(parts[2])
                    })
        
        logger.info(f"Detected {len(gpus)} GPU(s): {gpus}")
        return gpus
        
    except FileNotFoundError:
        logger.info("nvidia-smi command not found - no NVIDIA GPUs")
        return []
    except subprocess.TimeoutExpired:
        logger.warning("nvidia-smi command timed out")
        return []
    except Exception as e:
        logger.error(f"Error detecting GPUs: {e}")
        return []


async def detect_cpu() -> Dict[str, Any]:
    """
    Detect CPU information.
    
    Returns:
        Dictionary with CPU details.
    """
    cpu_info = {
        "model": "Unknown CPU",
        "cores": os.cpu_count() or 1,
        "threads": os.cpu_count() or 1,
        "architecture": platform.machine(),
        "freq_mhz": None,
    }
    
    # Get CPU model name
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        cpu_info["model"] = line.split(":")[1].strip()
                        break
            
            # Count threads
            with open("/proc/cpuinfo") as f:
                cpu_info["threads"] = sum(1 for line in f if line.startswith("processor"))
            
            # Get frequency
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("cpu MHz"):
                        cpu_info["freq_mhz"] = float(line.split(":")[1].strip())
                        break
        except Exception as e:
            logger.warning(f"Error reading /proc/cpuinfo: {e}")
    
    elif platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                cpu_info["model"] = result.stdout.strip()
        except Exception as e:
            logger.warning(f"Error getting macOS CPU info: {e}")
    
    logger.info(f"Detected CPU: {cpu_info}")
    return cpu_info


async def detect_memory() -> Dict[str, Any]:
    """
    Detect system memory (RAM) information.
    
    Returns:
        Dictionary with memory details.
    """
    mem_info = {
        "total_gb": 0.0,
        "available_gb": 0.0,
        "type": "Unknown",
    }
    
    if platform.system() == "Linux":
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        kb = int(line.split()[1])
                        mem_info["total_gb"] = round(kb / (1024 ** 2), 1)
                    elif line.startswith("MemAvailable"):
                        kb = int(line.split()[1])
                        mem_info["available_gb"] = round(kb / (1024 ** 2), 1)
            
            # Try to get memory type from dmidecode (requires root)
            try:
                result = subprocess.run(
                    ["dmidecode", "-t", "memory"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.split("\n"):
                        if "Type:" in line and "Unknown" not in line:
                            mem_info["type"] = line.split(":")[1].strip()
                            break
            except Exception:
                pass  # dmidecode may not be available or require root
                
        except Exception as e:
            logger.warning(f"Error reading memory info: {e}")
    
    elif platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                bytes_total = int(result.stdout.strip())
                mem_info["total_gb"] = round(bytes_total / (1024 ** 3), 1)
        except Exception as e:
            logger.warning(f"Error getting macOS memory info: {e}")
    
    logger.info(f"Detected Memory: {mem_info}")
    return mem_info


async def detect_storage() -> Dict[str, Any]:
    """
    Detect primary storage information.
    
    Returns:
        Dictionary with storage details.
    """
    storage_info = {
        "total_gb": 0.0,
        "free_gb": 0.0,
        "mount_point": "/",
    }
    
    try:
        total, used, free = shutil.disk_usage("/")
        storage_info["total_gb"] = round(total / (1024 ** 3), 0)
        storage_info["free_gb"] = round(free / (1024 ** 3), 0)
    except Exception as e:
        logger.warning(f"Error getting storage info: {e}")
    
    logger.info(f"Detected Storage: {storage_info}")
    return storage_info


# =============================================================================
# Resource Initialization
# =============================================================================

async def initialize_system_resources(db: AsyncSession) -> Dict[str, int]:
    """
    Auto-detect and create/update all system resources (CPU, RAM, GPUs, Storage).
    System-created resources are marked as disabled by default.
    
    Returns:
        Dictionary with counts of created and updated resources.
    """
    result = {
        "created": 0,
        "updated": 0,
        "types": [],
    }
    
    # Detect CPU
    cpu_info = await detect_cpu()
    cpu_created, cpu_updated = await _upsert_resource(
        db,
        name=f"CPU ({cpu_info['model'][:50]}...)" if len(cpu_info['model']) > 50 else f"CPU ({cpu_info['model']})",
        resource_type=ResourceType.CPU,
        category=ResourceCategory.COMPUTE.value,
        metadata={
            "model": cpu_info["model"],
            "cores": cpu_info["cores"],
            "threads": cpu_info["threads"],
            "architecture": cpu_info["architecture"],
            "freq_mhz": cpu_info["freq_mhz"],
        },
        match_type=ResourceType.CPU,  # Match by type for CPU (only one)
    )
    result["created"] += cpu_created
    result["updated"] += cpu_updated
    if cpu_created or cpu_updated:
        result["types"].append("cpu")
    
    # Detect RAM - compute resource (for memory-intensive operations)
    mem_info = await detect_memory()
    ram_created, ram_updated = await _upsert_resource(
        db,
        name=f"RAM ({mem_info['total_gb']:.0f} GB)",
        resource_type="ram",
        category=ResourceCategory.COMPUTE.value,
        metadata={
            "total_gb": mem_info["total_gb"],
            "type": mem_info["type"],
        },
        match_type="ram",  # Match by type for RAM (only one)
    )
    result["created"] += ram_created
    result["updated"] += ram_updated
    if ram_created or ram_updated:
        result["types"].append("ram")
    
    # Detect GPUs
    gpus = await detect_gpus()
    for gpu_info in gpus:
        gpu_name = f"GPU-{gpu_info['index']} ({gpu_info['name']})"
        gpu_created, gpu_updated = await _upsert_resource(
            db,
            name=gpu_name,
            resource_type=ResourceType.GPU,
            category=ResourceCategory.COMPUTE.value,
            metadata=gpu_info,
            match_name=gpu_name,  # Match by exact name for GPUs
        )
        result["created"] += gpu_created
        result["updated"] += gpu_updated
    if gpus:
        result["types"].append("gpu")
    
    # Detect Primary Storage - capacity resource
    storage_info = await detect_storage()
    storage_created, storage_updated = await _upsert_resource(
        db,
        name=f"Primary Storage ({storage_info['total_gb']:.0f} GB)",
        resource_type="storage",
        category=ResourceCategory.CAPACITY.value,
        metadata={
            "path": storage_info["mount_point"],
            "total_bytes": int(storage_info["total_gb"] * 1024 * 1024 * 1024),
            "min_free_bytes": 10 * 1024 * 1024 * 1024,  # 10GB buffer
        },
        match_name_prefix="Primary Storage",  # Match by name prefix for primary storage
    )
    result["created"] += storage_created
    result["updated"] += storage_updated
    if storage_created or storage_updated:
        result["types"].append("storage")
    
    if result["created"] > 0 or result["updated"] > 0:
        await db.commit()
    
    logger.info(f"System resources initialized: {result}")
    return result


async def _upsert_resource(
    db: AsyncSession,
    name: str,
    resource_type: str,
    category: str,
    metadata: Dict[str, Any],
    match_name: Optional[str] = None,
    match_type: Optional[str] = None,
    match_name_prefix: Optional[str] = None,
) -> tuple[int, int]:
    """
    Create or update a resource.
    
    Args:
        db: Database session
        name: Resource name
        resource_type: Resource type
        category: Resource category string ("compute" or "capacity")
        metadata: Resource metadata
        match_name: Match existing resource by exact name
        match_type: Match existing resource by type (for unique resources like CPU/RAM)
        match_name_prefix: Match existing resource by name prefix (e.g., "Primary Storage")
    
    Returns:
        Tuple of (created_count, updated_count)
    """
    from sqlalchemy import not_
    
    existing = None
    
    # Try to find existing resource
    if match_name:
        result = await db.execute(
            select(Resource).where(Resource.name == match_name)
        )
        existing = result.scalar_one_or_none()
    elif match_name_prefix:
        # Match by name prefix, excluding remote agent resources
        result = await db.execute(
            select(Resource).where(
                Resource.name.like(f'{match_name_prefix}%'),
                Resource.is_system_resource == True,
                not_(Resource.name.like('%: %'))
            )
        )
        existing = result.scalar_one_or_none()
    elif match_type:
        # For type-based matching, we need to exclude remote agent resources
        # Remote agent resources are prefixed with the agent name (e.g., "MyPC: CPU...")
        # Local resources don't have a prefix with colon
        result = await db.execute(
            select(Resource).where(
                Resource.resource_type == match_type,
                Resource.is_system_resource == True,
                # Exclude resources from remote agents (they have a colon prefix pattern)
                not_(Resource.name.like('%: %'))
            )
        )
        existing = result.scalar_one_or_none()
    
    if existing:
        # Update existing resource metadata
        existing.name = name  # Update name (may change with hardware)
        existing.resource_metadata = metadata
        existing.category = category  # Ensure category is set
        logger.info(f"Updated system resource: {name}")
        return (0, 1)
    else:
        # Create new resource (disabled by default)
        resource = Resource(
            id=uuid4(),
            name=name,
            resource_type=resource_type,
            category=category,
            status=ResourceStatus.DISABLED,
            is_system_resource=True,
            resource_metadata=metadata,
        )
        db.add(resource)
        logger.info(f"Created system resource: {name} (disabled)")
        return (1, 0)


# Keep old function for backwards compatibility
async def initialize_gpu_resources(db: AsyncSession) -> int:
    """
    Auto-detect GPUs and create resource records if they don't exist.
    
    DEPRECATED: Use initialize_system_resources() instead.
    
    Returns:
        Number of GPU resources created.
    """
    result = await initialize_system_resources(db)
    return result["created"]


async def get_resource(db: AsyncSession, resource_id: UUID) -> Optional[Resource]:
    """Get a resource by ID."""
    result = await db.execute(
        select(Resource).where(Resource.id == resource_id)
    )
    return result.scalar_one_or_none()


async def get_resources_by_type(db: AsyncSession, resource_type: str) -> List[Resource]:
    """Get all local system resources of a given type (excludes remote agent resources)."""
    from sqlalchemy import not_
    result = await db.execute(
        select(Resource).where(
            Resource.resource_type == resource_type,
            Resource.is_system_resource == True,
            not_(Resource.name.like('%: %'))  # Exclude remote agent resources
        )
    )
    return list(result.scalars().all())


async def get_first_gpu(db: AsyncSession) -> Optional[Resource]:
    """Get the first local GPU resource (GPU-0), or None."""
    gpus = await get_resources_by_type(db, ResourceType.GPU)
    return gpus[0] if gpus else None


async def get_all_resources(db: AsyncSession) -> List[Resource]:
    """Get all resources with job counts."""
    result = await db.execute(
        select(Resource).order_by(Resource.created_at)
    )
    resources = result.scalars().all()
    
    # Add job counts to each resource
    for resource in resources:
        queued = await db.execute(
            select(func.count(JobQueue.id))
            .where(JobQueue.resource_id == resource.id)
            .where(JobQueue.status == JobStatus.QUEUED)
        )
        running = await db.execute(
            select(func.count(JobQueue.id))
            .where(JobQueue.resource_id == resource.id)
            .where(JobQueue.status == JobStatus.RUNNING)
        )
        
        resource.jobs_queued = queued.scalar() or 0
        resource.jobs_running = running.scalar() or 0
    
    return list(resources)


async def create_resource(
    db: AsyncSession, 
    name: str, 
    resource_type: str, 
    metadata: Optional[dict] = None,
    category: Optional[str] = None
) -> Resource:
    """
    Create a new custom resource.
    
    Args:
        db: Database session
        name: Resource name
        resource_type: Type of resource (gpu, cpu, storage, custom)
        metadata: Additional metadata
        category: Resource category string ("compute" or "capacity"). If not provided,
                  defaults to "capacity" for storage, "compute" for others.
    """
    # Auto-determine category if not provided
    if category is None:
        if resource_type.lower() == "storage":
            category = ResourceCategory.CAPACITY.value
        else:
            category = ResourceCategory.COMPUTE.value
    
    resource = Resource(
        id=uuid4(),
        name=name,
        resource_type=resource_type,
        category=category,
        status=ResourceStatus.DISABLED,
        is_system_resource=False,
        resource_metadata=metadata or {}
    )
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return resource


async def update_resource_status(db: AsyncSession, resource_id: UUID, status: str) -> Optional[Resource]:
    """Update resource status (available, in_use, maintenance, disabled)."""
    resource = await get_resource(db, resource_id)
    if not resource:
        return None
    
    resource.status = status
    await db.commit()
    await db.refresh(resource)
    return resource


async def delete_resource(db: AsyncSession, resource_id: UUID) -> bool:
    """
    Delete a resource. System resources cannot be deleted.
    
    Returns:
        True if deleted, False if not found or is system resource.
    """
    resource = await get_resource(db, resource_id)
    if not resource:
        return False
    
    if resource.is_system_resource:
        logger.warning(f"Cannot delete system resource: {resource.name}")
        return False
    
    await db.delete(resource)
    await db.commit()
    return True


async def acquire_resource(db: AsyncSession, resource_id: UUID) -> bool:
    """
    Try to acquire a resource for use.
    
    Returns:
        True if acquired successfully, False if resource is not available.
    """
    resource = await get_resource(db, resource_id)
    if not resource or resource.status != ResourceStatus.AVAILABLE:
        return False
    
    resource.status = ResourceStatus.IN_USE
    await db.commit()
    return True


async def release_resource(db: AsyncSession, resource_id: UUID) -> bool:
    """
    Release a resource back to available status.
    
    Returns:
        True if released successfully.
    """
    resource = await get_resource(db, resource_id)
    if not resource:
        return False
    
    # Only change status if currently in use
    if resource.status == ResourceStatus.IN_USE:
        resource.status = ResourceStatus.AVAILABLE
        await db.commit()
    
    return True
