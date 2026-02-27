"""
Campaign Progress WebSocket Service.

Manages real-time progress updates for campaigns via WebSocket connections.
Provides a pub/sub mechanism where:
- Frontend clients subscribe to campaign updates
- Backend services emit progress events when state changes

Event Types:
- campaign_status: Campaign status changed (active, paused, completed, etc.)
- stream_progress: Task stream progress updated
- task_completed: Individual task completed
- task_failed: Individual task failed
- input_required: New input request created
- input_provided: User input provided
- overall_progress: Overall campaign progress percentage changed
"""

import asyncio
import json
import logging
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Dict, List, Optional, Any
from uuid import UUID
from dataclasses import dataclass, field
from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class CampaignSubscription:
    """Tracks a WebSocket subscription to a campaign."""
    websocket: WebSocket
    user_id: UUID
    campaign_id: UUID
    connected_at: datetime = field(default_factory=utc_now)
    last_event_at: Optional[datetime] = None


class CampaignProgressService:
    """
    Manages WebSocket connections for campaign progress updates.
    
    This is a singleton service that tracks all active subscriptions
    and broadcasts events to relevant clients.
    """
    
    def __init__(self):
        # Map campaign_id -> list of subscriptions (use list since CampaignSubscription isn't hashable)
        self._subscriptions: Dict[UUID, list] = {}
        # Map websocket -> subscription for quick lookup
        self._websocket_map: Dict[WebSocket, CampaignSubscription] = {}
        self._lock = asyncio.Lock()
    
    async def subscribe(
        self,
        websocket: WebSocket,
        user_id: UUID,
        campaign_id: UUID
    ) -> CampaignSubscription:
        """
        Subscribe a WebSocket connection to campaign updates.
        
        Args:
            websocket: The WebSocket connection
            user_id: The authenticated user's ID
            campaign_id: The campaign to subscribe to
            
        Returns:
            CampaignSubscription object
        """
        async with self._lock:
            subscription = CampaignSubscription(
                websocket=websocket,
                user_id=user_id,
                campaign_id=campaign_id,
            )
            
            if campaign_id not in self._subscriptions:
                self._subscriptions[campaign_id] = []
            
            self._subscriptions[campaign_id].append(subscription)
            self._websocket_map[websocket] = subscription
            
            logger.info(
                f"User {user_id} subscribed to campaign {campaign_id} progress. "
                f"Total subscribers: {len(self._subscriptions[campaign_id])}"
            )
            
            return subscription
    
    async def unsubscribe(self, websocket: WebSocket) -> None:
        """
        Remove a WebSocket subscription.
        
        Args:
            websocket: The WebSocket to unsubscribe
        """
        async with self._lock:
            subscription = self._websocket_map.pop(websocket, None)
            if subscription:
                campaign_subs = self._subscriptions.get(subscription.campaign_id)
                if campaign_subs:
                    # Remove subscription from list
                    self._subscriptions[subscription.campaign_id] = [
                        s for s in campaign_subs if s.websocket != websocket
                    ]
                    if not self._subscriptions[subscription.campaign_id]:
                        del self._subscriptions[subscription.campaign_id]
                
                logger.info(
                    f"User {subscription.user_id} unsubscribed from campaign "
                    f"{subscription.campaign_id} progress"
                )
    
    async def emit(
        self,
        campaign_id: UUID,
        event_type: str,
        data: Dict[str, Any],
        user_id: Optional[UUID] = None
    ) -> int:
        """
        Emit an event to all subscribers of a campaign.
        
        Args:
            campaign_id: The campaign ID
            event_type: Type of event (campaign_status, stream_progress, etc.)
            data: Event payload
            user_id: Optional - only send to this user (for user-specific events)
            
        Returns:
            Number of clients notified
        """
        async with self._lock:
            subscriptions = self._subscriptions.get(campaign_id, set()).copy()
        
        if not subscriptions:
            return 0
        
        message = {
            "type": event_type,
            "campaign_id": str(campaign_id),
            "timestamp": utc_now().isoformat(),
            "data": data,
        }
        
        notified = 0
        failed = []
        
        for subscription in subscriptions:
            # Skip if user_id filter is set and doesn't match
            if user_id and subscription.user_id != user_id:
                continue
            
            try:
                await subscription.websocket.send_json(message)
                subscription.last_event_at = utc_now()
                notified += 1
            except Exception as e:
                logger.warning(
                    f"Failed to send event to user {subscription.user_id}: {e}"
                )
                failed.append(subscription)
        
        # Clean up failed connections
        if failed:
            async with self._lock:
                for subscription in failed:
                    self._websocket_map.pop(subscription.websocket, None)
                    campaign_subs = self._subscriptions.get(subscription.campaign_id)
                    if campaign_subs and subscription in campaign_subs:
                        campaign_subs.remove(subscription)
        
        if notified > 0:
            logger.debug(
                f"Emitted {event_type} to {notified} subscribers for campaign {campaign_id}"
            )
        
        return notified
    
    async def emit_status_change(
        self,
        campaign_id: UUID,
        old_status: str,
        new_status: str,
        reason: Optional[str] = None
    ) -> int:
        """Emit a campaign status change event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="campaign_status",
            data={
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            }
        )
    
    async def emit_stream_progress(
        self,
        campaign_id: UUID,
        stream_id: str,
        stream_name: str,
        tasks_total: int,
        tasks_completed: int,
        tasks_failed: int,
        status: str
    ) -> int:
        """Emit a stream progress update."""
        progress_pct = (tasks_completed / tasks_total * 100) if tasks_total > 0 else 0
        return await self.emit(
            campaign_id=campaign_id,
            event_type="stream_progress",
            data={
                "stream_id": stream_id,
                "stream_name": stream_name,
                "tasks_total": tasks_total,
                "tasks_completed": tasks_completed,
                "tasks_failed": tasks_failed,
                "progress_pct": round(progress_pct, 1),
                "status": status,
            }
        )
    
    async def emit_task_completed(
        self,
        campaign_id: UUID,
        stream_id: str,
        task_id: str,
        task_name: str,
        result_summary: Optional[str] = None
    ) -> int:
        """Emit a task completion event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="task_completed",
            data={
                "stream_id": stream_id,
                "task_id": task_id,
                "task_name": task_name,
                "result_summary": result_summary,
            }
        )
    
    async def emit_task_failed(
        self,
        campaign_id: UUID,
        stream_id: str,
        task_id: str,
        task_name: str,
        error: str
    ) -> int:
        """Emit a task failure event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="task_failed",
            data={
                "stream_id": stream_id,
                "task_id": task_id,
                "task_name": task_name,
                "error": error,
            }
        )
    
    async def emit_input_required(
        self,
        campaign_id: UUID,
        input_key: str,
        input_type: str,
        title: str,
        priority: str,
        blocking_count: int
    ) -> int:
        """Emit an input required event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="input_required",
            data={
                "input_key": input_key,
                "input_type": input_type,
                "title": title,
                "priority": priority,
                "blocking_count": blocking_count,
            }
        )
    
    async def emit_input_provided(
        self,
        campaign_id: UUID,
        input_key: str,
        unblocked_tasks: int
    ) -> int:
        """Emit an input provided event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="input_provided",
            data={
                "input_key": input_key,
                "unblocked_tasks": unblocked_tasks,
            }
        )
    
    async def emit_overall_progress(
        self,
        campaign_id: UUID,
        overall_progress_pct: float,
        total_tasks: int,
        completed_tasks: int,
        budget_spent: float,
        revenue_generated: float
    ) -> int:
        """Emit an overall progress update."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="overall_progress",
            data={
                "overall_progress_pct": round(overall_progress_pct, 1),
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "budget_spent": budget_spent,
                "revenue_generated": revenue_generated,
            }
        )
    
    def get_subscriber_count(self, campaign_id: UUID) -> int:
        """Get the number of subscribers for a campaign."""
        return len(self._subscriptions.get(campaign_id, set()))

    async def emit_budget_warning(
        self,
        campaign_id: UUID,
        threshold_label: str,
        severity: str,
        spent_sats: int,
        budget_sats: int,
        remaining_sats: int,
        percent_used: float,
    ) -> int:
        """Emit a budget threshold warning event."""
        return await self.emit(
            campaign_id=campaign_id,
            event_type="budget_warning",
            data={
                "threshold_label": threshold_label,
                "severity": severity,
                "spent_sats": spent_sats,
                "budget_sats": budget_sats,
                "remaining_sats": remaining_sats,
                "percent_used": percent_used,
            },
        )
    
    def get_total_connections(self) -> int:
        """Get total number of active WebSocket connections."""
        return len(self._websocket_map)


# Singleton instance
campaign_progress_service = CampaignProgressService()
