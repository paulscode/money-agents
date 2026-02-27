"""Campaign Worker Service - Manages campaign worker registration and status.

This service handles worker lifecycle:
- Registration and deregistration
- Heartbeat processing
- Capacity tracking
- Worker discovery for campaign assignment
"""
import logging
import socket
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional, Dict, Any
from uuid import UUID, uuid4

from sqlalchemy import select, and_, or_, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import CampaignWorker, CampaignWorkerStatus, RemoteAgent
from app.services import campaign_lease_service

logger = logging.getLogger(__name__)


# Configuration constants
WORKER_OFFLINE_THRESHOLD_SECONDS = 180  # 3 minutes without heartbeat = offline
DEFAULT_CAMPAIGN_CAPACITY = 3
LOCAL_WORKER_ID = "local-backend"  # Special ID for the local backend worker


class WorkerError(Exception):
    """Base exception for worker operations."""
    pass


class WorkerNotFoundError(WorkerError):
    """Worker doesn't exist."""
    pass


class WorkerCapacityError(WorkerError):
    """Worker has reached capacity."""
    pass


async def register_worker(
    db: AsyncSession,
    hostname: str,
    worker_type: str = "local",
    remote_agent_id: Optional[UUID] = None,
    campaign_capacity: int = DEFAULT_CAMPAIGN_CAPACITY,
    ram_gb: Optional[int] = None,
    cpu_threads: Optional[int] = None,
    preferences: Optional[List[str]] = None,
    worker_id: Optional[str] = None,
) -> CampaignWorker:
    """
    Register a new campaign worker or update existing one.
    
    Args:
        db: Database session
        hostname: Human-readable hostname
        worker_type: 'local' or 'remote'
        remote_agent_id: UUID of RemoteAgent if this is a remote worker
        campaign_capacity: Max concurrent campaigns
        ram_gb: Available RAM in GB
        cpu_threads: Available CPU threads
        preferences: List of preferences (e.g., ["gpu_heavy", "quick_tasks"])
        worker_id: Explicit worker ID. Defaults to "{worker_type}-{hostname}".
        
    Returns:
        CampaignWorker object
    """
    # Use explicit worker_id if provided, otherwise generate one
    if not worker_id:
        worker_id = f"{worker_type}-{hostname}"
    
    # Check if worker already exists
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    now = utc_now()
    
    if worker:
        # Update existing worker
        worker.hostname = hostname
        worker.worker_type = worker_type
        worker.remote_agent_id = remote_agent_id
        worker.campaign_capacity = campaign_capacity
        worker.ram_gb = ram_gb
        worker.cpu_threads = cpu_threads
        worker.preferences = preferences or []
        worker.status = CampaignWorkerStatus.ONLINE.value
        worker.connected_at = now
        worker.last_heartbeat_at = now
        worker.disconnected_at = None
        
        logger.info(f"Worker {worker_id} reconnected")
    else:
        # Create new worker
        worker = CampaignWorker(
            worker_id=worker_id,
            hostname=hostname,
            worker_type=worker_type,
            remote_agent_id=remote_agent_id,
            campaign_capacity=campaign_capacity,
            current_campaign_count=0,
            ram_gb=ram_gb,
            cpu_threads=cpu_threads,
            preferences=preferences or [],
            status=CampaignWorkerStatus.ONLINE.value,
            connected_at=now,
            last_heartbeat_at=now,
        )
        db.add(worker)
        
        logger.info(f"Worker {worker_id} registered (capacity: {campaign_capacity})")
    
    await db.commit()
    await db.refresh(worker)
    return worker


async def register_local_worker(
    db: AsyncSession,
    worker_id: Optional[str] = None,
    max_campaigns: int = DEFAULT_CAMPAIGN_CAPACITY,
) -> CampaignWorker:
    """
    Register the local backend server as a campaign worker.
    
    Args:
        db: Database session
        worker_id: Explicit worker ID. Defaults to socket.gethostname().
        max_campaigns: Maximum concurrent campaigns for this worker.
    
    This should be called on backend startup.
    """
    hostname = socket.gethostname()
    resolved_worker_id = worker_id or hostname
    
    # Try to detect system resources
    ram_gb = None
    cpu_threads = None
    
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total // (1024 ** 3)
        cpu_threads = psutil.cpu_count()
    except ImportError:
        pass
    
    return await register_worker(
        db=db,
        hostname=hostname,
        worker_type="local",
        campaign_capacity=max_campaigns,
        ram_gb=ram_gb,
        cpu_threads=cpu_threads,
        preferences=[],
        worker_id=resolved_worker_id,
    )


async def update_worker_heartbeat(
    db: AsyncSession,
    worker_id: str,
    campaign_ids: Optional[List[UUID]] = None
) -> CampaignWorker:
    """
    Update worker heartbeat and optionally sync campaign list.
    
    Args:
        db: Database session
        worker_id: Worker identifier
        campaign_ids: Optional list of campaign IDs the worker is managing
        
    Returns:
        Updated CampaignWorker object
    """
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    if not worker:
        raise WorkerNotFoundError(f"Worker {worker_id} not found")
    
    now = utc_now()
    worker.last_heartbeat_at = now
    
    # If worker was offline, mark as online
    if worker.status == CampaignWorkerStatus.OFFLINE.value:
        worker.status = CampaignWorkerStatus.ONLINE.value
        worker.connected_at = now
        worker.disconnected_at = None
        logger.info(f"Worker {worker_id} came back online")
    
    # Sync campaign count if provided
    if campaign_ids is not None:
        worker.current_campaign_count = len(campaign_ids)
        
        # Also renew leases for these campaigns
        if campaign_ids:
            renewed, failed = await campaign_lease_service.renew_multiple_leases(
                db, worker_id, campaign_ids
            )
            if failed:
                logger.warning(
                    f"Worker {worker_id} heartbeat: {len(renewed)} leases renewed, "
                    f"{len(failed)} failed: {failed}"
                )
    
    await db.commit()
    await db.refresh(worker)
    return worker


async def disconnect_worker(
    db: AsyncSession,
    worker_id: str,
    release_campaigns: bool = True
) -> CampaignWorker:
    """
    Mark a worker as disconnected.
    
    Args:
        db: Database session
        worker_id: Worker identifier
        release_campaigns: If True, release all campaign leases (sets PAUSED_FAILOVER)
        
    Returns:
        Updated CampaignWorker object
    """
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    if not worker:
        raise WorkerNotFoundError(f"Worker {worker_id} not found")
    
    now = utc_now()
    worker.status = CampaignWorkerStatus.OFFLINE.value
    worker.disconnected_at = now
    
    logger.info(f"Worker {worker_id} disconnected")
    
    # Release campaigns if requested
    if release_campaigns:
        campaigns = await campaign_lease_service.get_worker_campaigns(db, worker_id)
        for campaign in campaigns:
            try:
                await campaign_lease_service.release_lease(
                    db, worker_id, campaign.id,
                    reason="worker_disconnected",
                    new_status=campaign_lease_service.CampaignStatus.PAUSED_FAILOVER
                )
            except Exception as e:
                logger.error(f"Failed to release lease for campaign {campaign.id}: {e}")
        
        worker.current_campaign_count = 0
    
    await db.commit()
    await db.refresh(worker)
    return worker


async def set_worker_draining(
    db: AsyncSession,
    worker_id: str,
    draining: bool = True
) -> CampaignWorker:
    """
    Set a worker to draining mode (won't accept new campaigns).
    
    Args:
        db: Database session
        worker_id: Worker identifier
        draining: True to start draining, False to resume normal operation
        
    Returns:
        Updated CampaignWorker object
    """
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    if not worker:
        raise WorkerNotFoundError(f"Worker {worker_id} not found")
    
    if draining:
        worker.status = CampaignWorkerStatus.DRAINING.value
        logger.info(f"Worker {worker_id} entering drain mode")
    else:
        worker.status = CampaignWorkerStatus.ONLINE.value
        logger.info(f"Worker {worker_id} exiting drain mode")
    
    await db.commit()
    await db.refresh(worker)
    return worker


async def get_available_workers(
    db: AsyncSession,
    min_capacity: int = 1,
    preferences: Optional[List[str]] = None
) -> List[CampaignWorker]:
    """
    Get workers that can accept new campaigns.
    
    Args:
        db: Database session
        min_capacity: Minimum available slots required
        preferences: Optional preferences to filter by
        
    Returns:
        List of available workers, sorted by available capacity (most first)
    """
    result = await db.execute(
        select(CampaignWorker).where(
            and_(
                CampaignWorker.status == CampaignWorkerStatus.ONLINE.value,
                (CampaignWorker.campaign_capacity - CampaignWorker.current_campaign_count) >= min_capacity
            )
        ).order_by(
            # Prefer workers with more available capacity
            (CampaignWorker.campaign_capacity - CampaignWorker.current_campaign_count).desc()
        )
    )
    workers = list(result.scalars().all())
    
    # Filter by preferences if specified
    if preferences:
        workers = [
            w for w in workers
            if any(p in (w.preferences or []) for p in preferences)
        ] or workers  # Fall back to all available if no preference match
    
    return workers


async def get_best_worker_for_campaign(
    db: AsyncSession,
    campaign_complexity: Optional[str] = None,
    resource_requirements: Optional[List[str]] = None,
    worker_affinity: Optional[str] = None
) -> Optional[CampaignWorker]:
    """
    Select the best worker for a campaign based on various criteria.
    
    Args:
        db: Database session
        campaign_complexity: 'light', 'medium', or 'heavy'
        resource_requirements: List of required resources
        worker_affinity: Preferred worker ID
        
    Returns:
        Best matching CampaignWorker or None if no workers available
    """
    # Get all available workers
    workers = await get_available_workers(db)
    
    if not workers:
        return None
    
    # If worker affinity specified and that worker is available, use it
    if worker_affinity:
        for worker in workers:
            if worker.worker_id == worker_affinity:
                return worker
    
    # Score workers based on criteria
    def score_worker(worker: CampaignWorker) -> float:
        score = 0.0
        
        # More available capacity is better
        score += (worker.campaign_capacity - worker.current_campaign_count) * 10
        
        # Prefer workers with more RAM for heavy campaigns
        if campaign_complexity == "heavy" and worker.ram_gb:
            score += worker.ram_gb * 0.5
        
        # Prefer local workers slightly (lower latency)
        if worker.worker_type == "local":
            score += 5
        
        return score
    
    # Sort by score and return best
    workers.sort(key=score_worker, reverse=True)
    return workers[0] if workers else None


async def increment_campaign_count(
    db: AsyncSession,
    worker_id: str
) -> int:
    """
    Increment the campaign count for a worker.
    
    Returns:
        New campaign count
    """
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    if not worker:
        raise WorkerNotFoundError(f"Worker {worker_id} not found")
    
    if not worker.has_capacity:
        raise WorkerCapacityError(f"Worker {worker_id} at capacity")
    
    worker.current_campaign_count += 1
    await db.commit()
    
    return worker.current_campaign_count


async def decrement_campaign_count(
    db: AsyncSession,
    worker_id: str
) -> int:
    """
    Decrement the campaign count for a worker.
    
    Returns:
        New campaign count
    """
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    worker = result.scalar_one_or_none()
    
    if not worker:
        raise WorkerNotFoundError(f"Worker {worker_id} not found")
    
    worker.current_campaign_count = max(0, worker.current_campaign_count - 1)
    await db.commit()
    
    return worker.current_campaign_count


async def detect_offline_workers(db: AsyncSession) -> List[CampaignWorker]:
    """
    Detect and mark workers that haven't sent a heartbeat recently.
    
    Returns:
        List of workers that were marked offline
    """
    now = utc_now()
    threshold = now - timedelta(seconds=WORKER_OFFLINE_THRESHOLD_SECONDS)
    
    # Find workers that are online but haven't heartbeated recently
    result = await db.execute(
        select(CampaignWorker).where(
            and_(
                CampaignWorker.status == CampaignWorkerStatus.ONLINE.value,
                or_(
                    CampaignWorker.last_heartbeat_at < threshold,
                    CampaignWorker.last_heartbeat_at.is_(None)
                )
            )
        )
    )
    stale_workers = list(result.scalars().all())
    
    marked_offline = []
    for worker in stale_workers:
        logger.warning(
            f"Worker {worker.worker_id} offline: no heartbeat since {worker.last_heartbeat_at}"
        )
        await disconnect_worker(db, worker.worker_id, release_campaigns=True)
        marked_offline.append(worker)
    
    return marked_offline


async def get_worker_by_id(
    db: AsyncSession,
    worker_id: str
) -> Optional[CampaignWorker]:
    """Get a worker by its ID."""
    result = await db.execute(
        select(CampaignWorker).where(CampaignWorker.worker_id == worker_id)
    )
    return result.scalar_one_or_none()


async def get_all_workers(db: AsyncSession) -> List[CampaignWorker]:
    """Get all registered workers."""
    result = await db.execute(
        select(CampaignWorker).order_by(CampaignWorker.hostname)
    )
    return list(result.scalars().all())


async def get_worker_stats(db: AsyncSession) -> Dict[str, Any]:
    """
    Get aggregate statistics about workers.
    
    Returns:
        Dict with worker statistics
    """
    workers = await get_all_workers(db)
    
    online = [w for w in workers if w.status == CampaignWorkerStatus.ONLINE.value]
    draining = [w for w in workers if w.status == CampaignWorkerStatus.DRAINING.value]
    offline = [w for w in workers if w.status == CampaignWorkerStatus.OFFLINE.value]
    
    total_capacity = sum(w.campaign_capacity for w in online)
    used_capacity = sum(w.current_campaign_count for w in online)
    
    return {
        "total_workers": len(workers),
        "online_workers": len(online),
        "draining_workers": len(draining),
        "offline_workers": len(offline),
        "total_capacity": total_capacity,
        "used_capacity": used_capacity,
        "available_capacity": total_capacity - used_capacity,
        "utilization_percent": (used_capacity / total_capacity * 100) if total_capacity > 0 else 0,
    }
