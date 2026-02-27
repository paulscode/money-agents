"""Campaign Lease Service - Manages campaign leasing for distributed workers.

This service handles the core lease operations that allow multiple workers
to coordinate campaign execution without conflicts.

Key concepts:
- Lease: A time-limited claim on a campaign by a worker
- TTL: Time-to-live for leases (default 5 minutes)
- Heartbeat: Periodic renewal to extend the lease
- Grace period: Extra time before expired leases become claimable
"""
import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, and_, or_, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, CampaignStatus

logger = logging.getLogger(__name__)


# Configuration constants
LEASE_TTL_SECONDS = 300  # 5 minutes
HEARTBEAT_INTERVAL_SECONDS = 60  # Worker should heartbeat every 60 seconds
LEASE_GRACE_PERIOD_SECONDS = 120  # 2 minutes grace after expiry before reclaim


class LeaseError(Exception):
    """Base exception for lease operations."""
    pass


class LeaseNotAvailableError(LeaseError):
    """Campaign is already leased to another worker."""
    pass


class LeaseNotHeldError(LeaseError):
    """Worker doesn't hold the lease for this campaign."""
    pass


class CampaignNotFoundError(LeaseError):
    """Campaign doesn't exist."""
    pass


async def acquire_lease(
    db: AsyncSession,
    worker_id: str,
    campaign_id: UUID,
    ttl_seconds: int = LEASE_TTL_SECONDS
) -> Campaign:
    """
    Attempt to acquire a lease on a campaign.
    
    Args:
        db: Database session
        worker_id: Unique identifier of the requesting worker
        campaign_id: ID of the campaign to lease
        ttl_seconds: How long the lease should last
        
    Returns:
        The Campaign object with lease acquired
        
    Raises:
        CampaignNotFoundError: Campaign doesn't exist
        LeaseNotAvailableError: Campaign is already leased to another worker
    """
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    
    # Get the campaign with FOR UPDATE lock to prevent race conditions
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found")
    
    # Check if campaign is claimable
    if not campaign.is_claimable():
        # If already leased by this worker, just renew
        if campaign.leased_by == worker_id:
            logger.debug(f"Worker {worker_id} renewing existing lease on campaign {campaign_id}")
            campaign.lease_expires_at = expires_at
            campaign.lease_heartbeat_at = now
            await db.commit()
            await db.refresh(campaign)
            return campaign
        
        # Leased by another worker
        if campaign.is_leased():
            raise LeaseNotAvailableError(
                f"Campaign {campaign_id} is leased to worker {campaign.leased_by} "
                f"until {campaign.lease_expires_at}"
            )
        
        # Not in a claimable status
        raise LeaseNotAvailableError(
            f"Campaign {campaign_id} is in status {campaign.status.value} and cannot be claimed"
        )
    
    # Acquire the lease
    campaign.leased_by = worker_id
    campaign.lease_acquired_at = now
    campaign.lease_expires_at = expires_at
    campaign.lease_heartbeat_at = now
    
    logger.info(f"Worker {worker_id} acquired lease on campaign {campaign_id}, expires {expires_at}")
    
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def renew_lease(
    db: AsyncSession,
    worker_id: str,
    campaign_id: UUID,
    ttl_seconds: int = LEASE_TTL_SECONDS
) -> Campaign:
    """
    Renew an existing lease (heartbeat).
    
    Args:
        db: Database session
        worker_id: Worker that holds the lease
        campaign_id: Campaign ID
        ttl_seconds: New TTL from now
        
    Returns:
        Updated Campaign object
        
    Raises:
        LeaseNotHeldError: Worker doesn't hold this lease
    """
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found")
    
    if campaign.leased_by != worker_id:
        raise LeaseNotHeldError(
            f"Worker {worker_id} does not hold lease for campaign {campaign_id} "
            f"(held by {campaign.leased_by})"
        )
    
    campaign.lease_expires_at = expires_at
    campaign.lease_heartbeat_at = now
    
    logger.debug(f"Worker {worker_id} renewed lease on campaign {campaign_id}")
    
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def renew_multiple_leases(
    db: AsyncSession,
    worker_id: str,
    campaign_ids: List[UUID],
    ttl_seconds: int = LEASE_TTL_SECONDS
) -> Tuple[List[UUID], List[UUID]]:
    """
    Renew leases for multiple campaigns (batch heartbeat).
    
    Args:
        db: Database session
        worker_id: Worker that holds the leases
        campaign_ids: List of campaign IDs to renew
        ttl_seconds: New TTL from now
        
    Returns:
        Tuple of (renewed_ids, failed_ids)
    """
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    
    renewed_ids = []
    failed_ids = []
    
    for campaign_id in campaign_ids:
        try:
            await renew_lease(db, worker_id, campaign_id, ttl_seconds)
            renewed_ids.append(campaign_id)
        except (LeaseNotHeldError, CampaignNotFoundError) as e:
            logger.warning(f"Failed to renew lease for campaign {campaign_id}: {e}")
            failed_ids.append(campaign_id)
    
    return renewed_ids, failed_ids


async def release_lease(
    db: AsyncSession,
    worker_id: str,
    campaign_id: UUID,
    reason: str = "completed",
    new_status: Optional[CampaignStatus] = None
) -> Campaign:
    """
    Release a lease on a campaign.
    
    Args:
        db: Database session
        worker_id: Worker releasing the lease
        campaign_id: Campaign ID
        reason: Why the lease is being released (for logging)
        new_status: Optionally update campaign status when releasing
        
    Returns:
        Updated Campaign object
        
    Raises:
        LeaseNotHeldError: Worker doesn't hold this lease
    """
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found")
    
    if campaign.leased_by != worker_id:
        raise LeaseNotHeldError(
            f"Worker {worker_id} does not hold lease for campaign {campaign_id}"
        )
    
    # Clear lease fields
    campaign.leased_by = None
    campaign.lease_acquired_at = None
    campaign.lease_expires_at = None
    campaign.lease_heartbeat_at = None
    
    if new_status:
        campaign.status = new_status
    
    logger.info(f"Worker {worker_id} released lease on campaign {campaign_id} (reason: {reason})")
    
    await db.commit()
    await db.refresh(campaign)
    return campaign


async def get_claimable_campaigns(
    db: AsyncSession,
    limit: int = 10,
    worker_preferences: Optional[List[str]] = None,
    exclude_worker_affinity: Optional[str] = None
) -> List[Campaign]:
    """
    Get campaigns that are available for claiming.
    
    A campaign is claimable if:
    - It's in a claimable status (initializing, executing, etc.)
    - It's not currently leased OR the lease has expired (with grace period)
    
    Args:
        db: Database session
        limit: Maximum number of campaigns to return
        worker_preferences: Optional preferences to prioritize matching campaigns
        exclude_worker_affinity: Don't return campaigns with affinity to this worker
        
    Returns:
        List of claimable Campaign objects, ordered by priority
    """
    now = utc_now()
    grace_cutoff = now - timedelta(seconds=LEASE_GRACE_PERIOD_SECONDS)
    
    # Claimable statuses
    claimable_statuses = [
        CampaignStatus.INITIALIZING,
        CampaignStatus.REQUIREMENTS_GATHERING,
        CampaignStatus.EXECUTING,
        CampaignStatus.MONITORING,
        CampaignStatus.WAITING_FOR_INPUTS,
        CampaignStatus.PAUSED_FAILOVER,
    ]
    
    # Build query
    query = select(Campaign).where(
        and_(
            Campaign.status.in_(claimable_statuses),
            or_(
                # Never been leased
                Campaign.leased_by.is_(None),
                # Lease expired past grace period
                Campaign.lease_expires_at < grace_cutoff
            )
        )
    )
    
    # Exclude campaigns with affinity to specific worker (for load balancing)
    if exclude_worker_affinity:
        query = query.where(
            or_(
                Campaign.worker_affinity.is_(None),
                Campaign.worker_affinity != exclude_worker_affinity
            )
        )
    
    # Order by: failover first, then oldest, then by affinity match
    query = query.order_by(
        # PAUSED_FAILOVER campaigns get priority
        (Campaign.status == CampaignStatus.PAUSED_FAILOVER).desc(),
        # Then oldest campaigns
        Campaign.created_at.asc()
    ).limit(limit)
    
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_expired_leases(
    db: AsyncSession,
    include_grace_period: bool = True
) -> List[Campaign]:
    """
    Get campaigns with expired leases.
    
    Args:
        db: Database session
        include_grace_period: If True, only return leases past grace period
        
    Returns:
        List of campaigns with expired leases
    """
    now = utc_now()
    
    if include_grace_period:
        cutoff = now - timedelta(seconds=LEASE_GRACE_PERIOD_SECONDS)
    else:
        cutoff = now
    
    result = await db.execute(
        select(Campaign).where(
            and_(
                Campaign.leased_by.isnot(None),
                Campaign.lease_expires_at < cutoff
            )
        )
    )
    return list(result.scalars().all())


async def force_release_expired_leases(
    db: AsyncSession,
    set_failover_status: bool = True
) -> int:
    """
    Force release all expired leases (for crash recovery).
    
    Args:
        db: Database session
        set_failover_status: If True, set status to PAUSED_FAILOVER
        
    Returns:
        Number of leases released
    """
    now = utc_now()
    grace_cutoff = now - timedelta(seconds=LEASE_GRACE_PERIOD_SECONDS)
    
    # Get expired leases
    expired = await get_expired_leases(db, include_grace_period=True)
    
    count = 0
    for campaign in expired:
        old_worker = campaign.leased_by
        
        # Clear lease
        campaign.leased_by = None
        campaign.lease_acquired_at = None
        campaign.lease_expires_at = None
        campaign.lease_heartbeat_at = None
        
        # Set failover status if requested and campaign was active
        if set_failover_status and campaign.status in (
            CampaignStatus.EXECUTING,
            CampaignStatus.MONITORING,
            CampaignStatus.REQUIREMENTS_GATHERING,
        ):
            campaign.status = CampaignStatus.PAUSED_FAILOVER
        
        logger.warning(
            f"Force-released expired lease on campaign {campaign.id} "
            f"(was held by {old_worker})"
        )
        count += 1
    
    if count > 0:
        await db.commit()
    
    return count


async def get_worker_campaigns(
    db: AsyncSession,
    worker_id: str
) -> List[Campaign]:
    """
    Get all campaigns currently leased to a worker.
    
    Args:
        db: Database session
        worker_id: Worker ID to query
        
    Returns:
        List of campaigns leased to the worker
    """
    result = await db.execute(
        select(Campaign).where(
            Campaign.leased_by == worker_id
        )
    )
    return list(result.scalars().all())


async def count_worker_campaigns(
    db: AsyncSession,
    worker_id: str
) -> int:
    """
    Count campaigns currently leased to a worker.
    
    Args:
        db: Database session
        worker_id: Worker ID to query
        
    Returns:
        Number of campaigns
    """
    result = await db.execute(
        select(func.count(Campaign.id)).where(
            Campaign.leased_by == worker_id
        )
    )
    return result.scalar() or 0


async def transfer_lease(
    db: AsyncSession,
    campaign_id: UUID,
    from_worker_id: str,
    to_worker_id: str,
    ttl_seconds: int = LEASE_TTL_SECONDS
) -> Campaign:
    """
    Transfer a lease from one worker to another (admin operation).
    
    Args:
        db: Database session
        campaign_id: Campaign to transfer
        from_worker_id: Current worker (or None to force transfer)
        to_worker_id: New worker
        ttl_seconds: TTL for new lease
        
    Returns:
        Updated Campaign object
    """
    now = utc_now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    
    result = await db.execute(
        select(Campaign)
        .where(Campaign.id == campaign_id)
        .with_for_update()
    )
    campaign = result.scalar_one_or_none()
    
    if not campaign:
        raise CampaignNotFoundError(f"Campaign {campaign_id} not found")
    
    # Verify current holder if specified
    if from_worker_id and campaign.leased_by != from_worker_id:
        raise LeaseNotHeldError(
            f"Campaign {campaign_id} is not held by {from_worker_id}"
        )
    
    old_worker = campaign.leased_by
    
    # Transfer the lease
    campaign.leased_by = to_worker_id
    campaign.lease_acquired_at = now
    campaign.lease_expires_at = expires_at
    campaign.lease_heartbeat_at = now
    
    logger.info(
        f"Transferred lease on campaign {campaign_id} "
        f"from {old_worker} to {to_worker_id}"
    )
    
    await db.commit()
    await db.refresh(campaign)
    return campaign
