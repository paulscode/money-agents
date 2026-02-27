"""
Campaign Worker - Lease-based campaign execution loop.

This module implements the worker side of the distributed campaign architecture.
Workers claim campaigns via the lease system and process them in a loop.

Key responsibilities:
- Claim available campaigns up to worker capacity
- Process each claimed campaign's pending work  
- Handle user input for owned campaigns
- Send periodic heartbeats to renew leases
- Release leases on completion/pause/error
"""
import asyncio
import logging
import socket
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.datetime_utils import utc_now
from app.models import Campaign, CampaignStatus, Proposal
from app.services.campaign_lease_service import (
    HEARTBEAT_INTERVAL_SECONDS,
    acquire_lease,
    renew_lease,
    release_lease,
    get_claimable_campaigns,
)
from app.services.campaign_worker_service import (
    register_local_worker,
    update_worker_heartbeat,
    get_worker_by_id,
    increment_campaign_count,
    decrement_campaign_count,
    CampaignWorkerStatus,
)

logger = logging.getLogger(__name__)


def get_worker_id() -> str:
    """
    Get the worker ID for this process.
    
    Uses hostname as the base identifier. In the future, this could
    incorporate a process ID or UUID for multiple workers per host.
    """
    return socket.gethostname()


class CampaignWorkerLoop:
    """
    Campaign worker that claims and processes campaigns via the lease system.
    
    This is the core execution loop for distributed campaign management.
    Each worker instance (local or remote) runs one of these loops.
    """
    
    def __init__(
        self,
        worker_id: Optional[str] = None,
        max_campaigns: int = 3,
    ):
        """
        Initialize the campaign worker.
        
        Args:
            worker_id: Unique identifier for this worker. Defaults to hostname.
            max_campaigns: Maximum campaigns this worker can process simultaneously.
        """
        self.worker_id = worker_id or get_worker_id()
        self.max_campaigns = max_campaigns
        
        # Track currently held campaign IDs
        self._held_campaigns: Set[UUID] = set()
        
        # Last heartbeat time
        self._last_heartbeat: Optional[datetime] = None
        
        # Flag for graceful shutdown
        self._shutting_down = False
    
    @property
    def current_campaign_count(self) -> int:
        """Number of campaigns currently held by this worker."""
        return len(self._held_campaigns)
    
    @property
    def available_slots(self) -> int:
        """Number of additional campaigns this worker can accept."""
        return max(0, self.max_campaigns - self.current_campaign_count)
    
    async def register(self, db: AsyncSession) -> bool:
        """
        Register this worker with the system.
        
        Should be called once on worker startup.
        """
        try:
            worker = await register_local_worker(
                db,
                worker_id=self.worker_id,
                max_campaigns=self.max_campaigns,
            )
            if worker:
                logger.info(f"Worker {self.worker_id} registered successfully")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to register worker {self.worker_id}: {e}")
            return False
    
    async def claim_campaigns(self, db: AsyncSession) -> List[UUID]:
        """
        Attempt to claim available campaigns up to capacity.
        
        Returns list of newly claimed campaign IDs.
        """
        if self._shutting_down:
            logger.debug("Worker shutting down, not claiming new campaigns")
            return []
        
        slots = self.available_slots
        if slots <= 0:
            return []
        
        # Get claimable campaigns
        claimable = await get_claimable_campaigns(db, limit=slots)
        
        claimed = []
        for campaign in claimable:
            success = await acquire_lease(db, self.worker_id, campaign.id)
            if success:
                self._held_campaigns.add(campaign.id)
                await increment_campaign_count(db, self.worker_id)
                claimed.append(campaign.id)
                logger.info(f"Worker {self.worker_id} claimed campaign {campaign.id}")
            else:
                # Another worker got it first, that's fine
                logger.debug(f"Failed to claim campaign {campaign.id} (likely claimed by another worker)")
        
        return claimed
    
    async def release_campaign(
        self,
        db: AsyncSession,
        campaign_id: UUID,
        new_status: Optional[CampaignStatus] = None,
        reason: str = "worker_release",
    ) -> bool:
        """
        Release a campaign lease.
        
        Args:
            campaign_id: The campaign to release
            new_status: Optional new status for the campaign
            reason: Reason for release (for logging)
        """
        if campaign_id not in self._held_campaigns:
            logger.warning(f"Attempted to release campaign {campaign_id} not held by this worker")
            return False
        
        success = await release_lease(db, self.worker_id, campaign_id, new_status)
        if success:
            self._held_campaigns.discard(campaign_id)
            await decrement_campaign_count(db, self.worker_id)
            logger.info(f"Worker {self.worker_id} released campaign {campaign_id} ({reason})")
        else:
            logger.error(f"Failed to release campaign {campaign_id}")
        
        return success
    
    async def send_heartbeat(self, db: AsyncSession) -> int:
        """
        Send heartbeat to renew all held campaign leases.
        
        Returns number of successfully renewed leases.
        """
        renewed = 0
        failed = []
        
        for campaign_id in list(self._held_campaigns):
            success = await renew_lease(db, self.worker_id, campaign_id)
            if success:
                renewed += 1
            else:
                # Lease lost - maybe expired or taken by another worker
                logger.warning(f"Failed to renew lease for campaign {campaign_id}")
                failed.append(campaign_id)
        
        # Remove campaigns we lost
        for campaign_id in failed:
            self._held_campaigns.discard(campaign_id)
            await decrement_campaign_count(db, self.worker_id)
        
        # Update worker heartbeat
        await update_worker_heartbeat(
            db,
            self.worker_id,
            campaign_ids=list(self._held_campaigns),
        )
        
        self._last_heartbeat = utc_now()
        
        if renewed > 0 or failed:
            logger.debug(f"Heartbeat: renewed {renewed}, lost {len(failed)} leases")
        
        return renewed
    
    async def process_campaign(
        self,
        db: AsyncSession,
        campaign_id: UUID,
    ) -> Dict[str, Any]:
        """
        Process a single campaign step.
        
        This is called for each campaign in the work loop.
        Returns processing result dict.
        """
        from app.agents.campaign_manager import CampaignManagerAgent
        from app.agents.base import AgentContext
        
        # Verify we still hold this campaign
        if campaign_id not in self._held_campaigns:
            return {"error": "Campaign not held by this worker"}
        
        # Fetch campaign
        result = await db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        
        if not campaign:
            self._held_campaigns.discard(campaign_id)
            return {"error": "Campaign not found"}
        
        # Check if campaign is in a processable state
        processable_statuses = [
            CampaignStatus.ACTIVE,
            CampaignStatus.INITIALIZING,
            CampaignStatus.REQUIREMENTS_GATHERING,
            CampaignStatus.EXECUTING,
            CampaignStatus.MONITORING,
        ]
        
        if campaign.status not in processable_statuses:
            # Campaign is paused/completed/terminated - release it
            await self.release_campaign(
                db,
                campaign_id,
                reason=f"status_{campaign.status.value}",
            )
            return {"released": True, "reason": f"Campaign status: {campaign.status.value}"}
        
        # Process the campaign
        try:
            manager = CampaignManagerAgent()
            context = AgentContext(db=db, user_id=campaign.user_id)
            
            # Honor the configured model tier from agent_definitions
            from app.models.agent_scheduler import AgentDefinition
            agent_def_result = await db.execute(
                select(AgentDefinition).where(AgentDefinition.slug == "campaign_manager")
            )
            agent_def = agent_def_result.scalar_one_or_none()
            if agent_def and agent_def.default_model_tier:
                manager.model_tier = agent_def.default_model_tier
            
            result = await manager.execute_campaign_step(
                context=context,
                campaign_id=campaign_id,
            )
            
            return {
                "success": result.success,
                "message": result.message,
                "data": result.data,
                "tokens_used": result.tokens_used,
            }
            
        except Exception as e:
            logger.exception(f"Error processing campaign {campaign_id}: {e}")
            return {"error": str(e)}
    
    async def run_work_loop_iteration(
        self,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Run one iteration of the work loop.
        
        This claims campaigns, processes them, and handles heartbeats.
        Should be called periodically (e.g., by Celery task).
        """
        results = {
            "worker_id": self.worker_id,
            "claimed": [],
            "processed": [],
            "released": [],
            "errors": [],
            "heartbeat_renewed": 0,
        }
        
        # 1. Send heartbeat to renew existing leases
        results["heartbeat_renewed"] = await self.send_heartbeat(db)
        
        # 2. Claim new campaigns if we have capacity
        claimed = await self.claim_campaigns(db)
        results["claimed"] = [str(cid) for cid in claimed]
        
        # 3. Process each held campaign
        for campaign_id in list(self._held_campaigns):
            process_result = await self.process_campaign(db, campaign_id)
            
            if "error" in process_result:
                results["errors"].append({
                    "campaign_id": str(campaign_id),
                    "error": process_result["error"],
                })
            elif process_result.get("released"):
                results["released"].append({
                    "campaign_id": str(campaign_id),
                    "reason": process_result.get("reason"),
                })
            else:
                results["processed"].append({
                    "campaign_id": str(campaign_id),
                    "success": process_result.get("success"),
                    "message": process_result.get("message"),
                })
        
        logger.info(
            f"Work loop: claimed={len(results['claimed'])}, "
            f"processed={len(results['processed'])}, "
            f"released={len(results['released'])}, "
            f"errors={len(results['errors'])}, "
            f"held={self.current_campaign_count}"
        )
        
        return results
    
    async def graceful_shutdown(self, db: AsyncSession) -> None:
        """
        Gracefully shut down the worker.
        
        Releases all held campaigns and marks worker as offline.
        """
        self._shutting_down = True
        logger.info(f"Worker {self.worker_id} shutting down, releasing {len(self._held_campaigns)} campaigns")
        
        for campaign_id in list(self._held_campaigns):
            # Release without changing status - another worker will pick up
            await self.release_campaign(db, campaign_id, reason="worker_shutdown")
        
        # Mark worker as draining/offline
        from app.services.campaign_worker_service import disconnect_worker
        await disconnect_worker(db, self.worker_id, graceful=True)


# Global worker instance (singleton per process)
_worker_instance: Optional[CampaignWorkerLoop] = None


def get_worker_instance() -> CampaignWorkerLoop:
    """
    Get or create the worker instance for this process.
    
    The worker is a singleton per process to ensure consistent
    campaign tracking and avoid duplicate claims.
    """
    global _worker_instance
    if _worker_instance is None:
        _worker_instance = CampaignWorkerLoop()
    return _worker_instance


async def run_campaign_worker_iteration(db: AsyncSession) -> Dict[str, Any]:
    """
    Run one iteration of the campaign worker loop.
    
    This is the main entry point called by Celery tasks.
    It ensures the worker is registered and runs the work loop.
    """
    worker = get_worker_instance()
    
    # Ensure worker is registered
    existing = await get_worker_by_id(db, worker.worker_id)
    if not existing:
        await worker.register(db)
    elif existing.status == CampaignWorkerStatus.OFFLINE:
        # Reconnect worker that was marked offline
        await worker.register(db)
    
    # Run work loop
    return await worker.run_work_loop_iteration(db)
