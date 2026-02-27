"""
Tool Approval Service - Manages human-in-loop approval workflow.

Provides:
- Creating approval requests when tools require human review
- Approving/rejecting requests
- Listing pending requests for reviewers
- Expiration handling
- Integration with tool execution
"""
from datetime import datetime, timedelta, timezone
from app.core.datetime_utils import utc_now
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, desc, or_, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Tool,
    User,
    Campaign,
    ToolExecution,
    ToolApprovalRequest,
    ApprovalStatus,
    ApprovalUrgency,
)


class ApprovalServiceError(Exception):
    """Base exception for approval service errors."""
    pass


class ApprovalNotFoundError(ApprovalServiceError):
    """Raised when approval request not found."""
    pass


class ApprovalExpiredError(ApprovalServiceError):
    """Raised when approval request has expired."""
    pass


class ApprovalAlreadyReviewedError(ApprovalServiceError):
    """Raised when approval request has already been reviewed."""
    pass


class ToolApprovalService:
    """
    Service for managing tool approval requests.
    
    Usage:
        service = ToolApprovalService(db)
        
        # Check if tool needs approval
        if await service.requires_approval(tool_id):
            request = await service.create_request(
                tool_id=tool_id,
                user_id=user_id,
                parameters={"query": "hello"},
                reason="Searching for market data",
            )
            # Return approval_request_id to caller, await human decision
        
        # Later, human approves
        await service.approve(request_id, reviewer_id, "Looks good")
        
        # Or rejects
        await service.reject(request_id, reviewer_id, "Parameters invalid")
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    # =========================================================================
    # Request Creation
    # =========================================================================
    
    async def requires_approval(self, tool_id: UUID) -> bool:
        """Check if a tool requires approval for execution."""
        result = await self.db.execute(
            select(Tool.requires_approval).where(Tool.id == tool_id)
        )
        requires = result.scalar_one_or_none()
        return requires is True
    
    async def get_tool_approval_config(self, tool_id: UUID) -> Optional[Dict[str, Any]]:
        """Get approval configuration for a tool."""
        result = await self.db.execute(
            select(
                Tool.requires_approval,
                Tool.approval_urgency,
                Tool.approval_instructions,
                Tool.name,
            ).where(Tool.id == tool_id)
        )
        row = result.one_or_none()
        if not row:
            return None
        return {
            "requires_approval": row[0],
            "urgency": row[1] or "medium",
            "instructions": row[2],
            "tool_name": row[3],
        }
    
    async def create_request(
        self,
        tool_id: UUID,
        user_id: UUID,
        parameters: Dict[str, Any],
        reason: str,
        campaign_id: Optional[UUID] = None,
        urgency: Optional[ApprovalUrgency] = None,
        expected_outcome: Optional[str] = None,
        risk_assessment: Optional[str] = None,
        estimated_cost: Optional[float] = None,
        expires_in: Optional[timedelta] = None,
    ) -> ToolApprovalRequest:
        """
        Create an approval request for a tool execution.
        
        Args:
            tool_id: Tool to execute
            user_id: User requesting execution
            parameters: Tool parameters
            reason: Why this tool is being called
            campaign_id: Optional campaign context
            urgency: Override default urgency
            expected_outcome: What should happen
            risk_assessment: Potential risks
            estimated_cost: Estimated $ cost
            expires_in: Custom expiry (default based on urgency)
        
        Returns:
            Created approval request
        """
        # Get tool config for default urgency
        config = await self.get_tool_approval_config(tool_id)
        if not config:
            raise ApprovalServiceError(f"Tool {tool_id} not found")
        
        # Determine urgency
        if urgency is None:
            urgency_str = config.get("urgency", "medium")
            try:
                urgency = ApprovalUrgency(urgency_str)
            except ValueError:
                urgency = ApprovalUrgency.MEDIUM
        
        # Determine expiry
        if expires_in is None:
            expires_in = ToolApprovalRequest.default_expiry_for_urgency(urgency)
        
        now = utc_now()
        expires_at = now + expires_in
        
        # Create request
        request = ToolApprovalRequest(
            tool_id=tool_id,
            parameters=parameters,
            requested_by_id=user_id,
            campaign_id=campaign_id,
            status=ApprovalStatus.PENDING,
            urgency=urgency,
            reason=reason,
            expected_outcome=expected_outcome,
            risk_assessment=risk_assessment,
            estimated_cost=estimated_cost,
            expires_at=expires_at,
        )
        
        self.db.add(request)
        await self.db.commit()
        await self.db.refresh(request)
        
        return request
    
    # =========================================================================
    # Request Review
    # =========================================================================
    
    async def get_request(
        self,
        request_id: UUID,
        include_relations: bool = True,
    ) -> Optional[ToolApprovalRequest]:
        """Get an approval request by ID."""
        query = select(ToolApprovalRequest).where(ToolApprovalRequest.id == request_id)
        
        if include_relations:
            query = query.options(
                selectinload(ToolApprovalRequest.tool),
                selectinload(ToolApprovalRequest.requested_by),
                selectinload(ToolApprovalRequest.reviewed_by),
                selectinload(ToolApprovalRequest.campaign),
            )
        
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def approve(
        self,
        request_id: UUID,
        reviewer_id: UUID,
        notes: Optional[str] = None,
    ) -> ToolApprovalRequest:
        """
        Approve an execution request.
        
        Args:
            request_id: Request to approve
            reviewer_id: User approving
            notes: Optional reviewer notes
        
        Returns:
            Updated request
            
        Raises:
            ApprovalNotFoundError: Request not found
            ApprovalExpiredError: Request has expired
            ApprovalAlreadyReviewedError: Request already reviewed
        """
        request = await self.get_request(request_id, include_relations=False)
        if not request:
            raise ApprovalNotFoundError(f"Approval request {request_id} not found")
        
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAlreadyReviewedError(
                f"Request already has status: {request.status}"
            )
        
        if request.is_expired():
            request.status = ApprovalStatus.EXPIRED
            await self.db.commit()
            raise ApprovalExpiredError("Request has expired")
        
        request.status = ApprovalStatus.APPROVED
        request.reviewed_by_id = reviewer_id
        request.reviewed_at = utc_now()
        request.review_notes = notes
        
        await self.db.commit()
        await self.db.refresh(request)
        
        return request
    
    async def reject(
        self,
        request_id: UUID,
        reviewer_id: UUID,
        notes: Optional[str] = None,
    ) -> ToolApprovalRequest:
        """
        Reject an execution request.
        
        Args:
            request_id: Request to reject
            reviewer_id: User rejecting
            notes: Optional reviewer notes (recommended for rejections)
        
        Returns:
            Updated request
        """
        request = await self.get_request(request_id, include_relations=False)
        if not request:
            raise ApprovalNotFoundError(f"Approval request {request_id} not found")
        
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAlreadyReviewedError(
                f"Request already has status: {request.status}"
            )
        
        if request.is_expired():
            request.status = ApprovalStatus.EXPIRED
            await self.db.commit()
            raise ApprovalExpiredError("Request has expired")
        
        request.status = ApprovalStatus.REJECTED
        request.reviewed_by_id = reviewer_id
        request.reviewed_at = utc_now()
        request.review_notes = notes
        
        await self.db.commit()
        await self.db.refresh(request)
        
        return request
    
    async def cancel(
        self,
        request_id: UUID,
        user_id: UUID,
    ) -> ToolApprovalRequest:
        """
        Cancel a pending request (requester only).
        
        Args:
            request_id: Request to cancel
            user_id: Must be the original requester
        
        Returns:
            Updated request
        """
        request = await self.get_request(request_id, include_relations=False)
        if not request:
            raise ApprovalNotFoundError(f"Approval request {request_id} not found")
        
        if request.requested_by_id != user_id:
            raise ApprovalServiceError("Only the requester can cancel a request")
        
        if request.status != ApprovalStatus.PENDING:
            raise ApprovalAlreadyReviewedError(
                f"Cannot cancel - request has status: {request.status}"
            )
        
        request.status = ApprovalStatus.CANCELLED
        await self.db.commit()
        await self.db.refresh(request)
        
        return request
    
    async def link_execution(
        self,
        request_id: UUID,
        execution_id: UUID,
    ) -> None:
        """Link an approval request to its execution record."""
        await self.db.execute(
            update(ToolApprovalRequest)
            .where(ToolApprovalRequest.id == request_id)
            .values(execution_id=execution_id)
        )
        await self.db.commit()
    
    async def get_approved_request(
        self,
        request_id: UUID,
    ) -> Optional[ToolApprovalRequest]:
        """
        Get an approved request that hasn't been executed yet.
        
        Used when executing a tool after approval has been granted.
        """
        query = (
            select(ToolApprovalRequest)
            .where(
                and_(
                    ToolApprovalRequest.id == request_id,
                    ToolApprovalRequest.status == ApprovalStatus.APPROVED,
                    ToolApprovalRequest.execution_id.is_(None),  # Not yet executed
                )
            )
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def check_has_approval(
        self,
        tool_id: UUID,
        user_id: UUID,
        approval_request_id: Optional[UUID] = None,
    ) -> Optional[ToolApprovalRequest]:
        """
        Check if there's a valid approval for this tool+user.
        
        If approval_request_id is provided, checks that specific request.
        Otherwise looks for any approved, unused request for this tool+user.
        
        Returns the approval request if valid, None otherwise.
        """
        if approval_request_id:
            # Check specific request
            query = (
                select(ToolApprovalRequest)
                .where(
                    and_(
                        ToolApprovalRequest.id == approval_request_id,
                        ToolApprovalRequest.tool_id == tool_id,
                        ToolApprovalRequest.requested_by_id == user_id,
                        ToolApprovalRequest.status == ApprovalStatus.APPROVED,
                        ToolApprovalRequest.execution_id.is_(None),  # Not yet used
                    )
                )
            )
        else:
            # Look for any valid approval
            query = (
                select(ToolApprovalRequest)
                .where(
                    and_(
                        ToolApprovalRequest.tool_id == tool_id,
                        ToolApprovalRequest.requested_by_id == user_id,
                        ToolApprovalRequest.status == ApprovalStatus.APPROVED,
                        ToolApprovalRequest.execution_id.is_(None),  # Not yet used
                    )
                )
                .order_by(ToolApprovalRequest.reviewed_at.desc())  # Most recent first
                .limit(1)
            )
        
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    # =========================================================================
    # Listing & Queries
    # =========================================================================
    
    async def list_pending(
        self,
        urgency: Optional[ApprovalUrgency] = None,
        campaign_id: Optional[UUID] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ToolApprovalRequest], int]:
        """
        List pending approval requests.
        
        Args:
            urgency: Filter by urgency level
            campaign_id: Filter by campaign
            limit: Max results
            offset: Pagination offset
        
        Returns:
            Tuple of (requests, total_count)
        """
        # Expire old requests first
        await self._expire_old_requests()
        
        # Build query
        conditions = [ToolApprovalRequest.status == ApprovalStatus.PENDING]
        
        if urgency:
            conditions.append(ToolApprovalRequest.urgency == urgency)
        if campaign_id:
            conditions.append(ToolApprovalRequest.campaign_id == campaign_id)
        
        # Count total
        count_query = select(func.count()).select_from(ToolApprovalRequest).where(and_(*conditions))
        total = (await self.db.execute(count_query)).scalar() or 0
        
        # Fetch requests (ordered by urgency then created_at)
        query = (
            select(ToolApprovalRequest)
            .where(and_(*conditions))
            .options(
                selectinload(ToolApprovalRequest.tool),
                selectinload(ToolApprovalRequest.requested_by),
                selectinload(ToolApprovalRequest.campaign),
            )
            .order_by(
                # CRITICAL first, then HIGH, MEDIUM, LOW
                desc(ToolApprovalRequest.urgency == ApprovalUrgency.CRITICAL),
                desc(ToolApprovalRequest.urgency == ApprovalUrgency.HIGH),
                desc(ToolApprovalRequest.urgency == ApprovalUrgency.MEDIUM),
                ToolApprovalRequest.created_at,
            )
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.db.execute(query)
        requests = list(result.scalars().all())
        
        return requests, total
    
    async def list_by_user(
        self,
        user_id: UUID,
        status: Optional[ApprovalStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[ToolApprovalRequest], int]:
        """List approval requests made by a user."""
        conditions = [ToolApprovalRequest.requested_by_id == user_id]
        
        if status:
            conditions.append(ToolApprovalRequest.status == status)
        
        count_query = select(func.count()).select_from(ToolApprovalRequest).where(and_(*conditions))
        total = (await self.db.execute(count_query)).scalar() or 0
        
        query = (
            select(ToolApprovalRequest)
            .where(and_(*conditions))
            .options(
                selectinload(ToolApprovalRequest.tool),
                selectinload(ToolApprovalRequest.reviewed_by),
            )
            .order_by(desc(ToolApprovalRequest.created_at))
            .limit(limit)
            .offset(offset)
        )
        
        result = await self.db.execute(query)
        requests = list(result.scalars().all())
        
        return requests, total
    
    async def list_by_campaign(
        self,
        campaign_id: UUID,
        limit: int = 50,
    ) -> List[ToolApprovalRequest]:
        """List all approval requests for a campaign."""
        query = (
            select(ToolApprovalRequest)
            .where(ToolApprovalRequest.campaign_id == campaign_id)
            .options(
                selectinload(ToolApprovalRequest.tool),
                selectinload(ToolApprovalRequest.reviewed_by),
            )
            .order_by(desc(ToolApprovalRequest.created_at))
            .limit(limit)
        )
        
        result = await self.db.execute(query)
        return list(result.scalars().all())
    
    async def get_pending_count(self) -> Dict[str, int]:
        """Get count of pending requests by urgency."""
        # Expire old requests first
        await self._expire_old_requests()
        
        query = (
            select(
                ToolApprovalRequest.urgency,
                func.count(ToolApprovalRequest.id),
            )
            .where(ToolApprovalRequest.status == ApprovalStatus.PENDING)
            .group_by(ToolApprovalRequest.urgency)
        )
        
        result = await self.db.execute(query)
        counts = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "total": 0,
        }
        
        for urgency, count in result:
            counts[urgency.value] = count
            counts["total"] += count
        
        return counts
    
    # =========================================================================
    # Maintenance
    # =========================================================================
    
    async def _expire_old_requests(self) -> int:
        """Mark expired requests as EXPIRED. Returns count updated."""
        now = utc_now()
        
        result = await self.db.execute(
            update(ToolApprovalRequest)
            .where(
                and_(
                    ToolApprovalRequest.status == ApprovalStatus.PENDING,
                    ToolApprovalRequest.expires_at < now,
                )
            )
            .values(status=ApprovalStatus.EXPIRED)
        )
        
        await self.db.commit()
        return result.rowcount
    
    async def cleanup_old_requests(
        self,
        older_than_days: int = 30,
    ) -> int:
        """
        Delete old resolved requests (for database cleanup).
        
        Args:
            older_than_days: Delete requests older than this
        
        Returns:
            Number of deleted requests
        """
        from sqlalchemy import delete
        
        cutoff = utc_now() - timedelta(days=older_than_days)
        
        # Only delete resolved requests (not pending)
        result = await self.db.execute(
            delete(ToolApprovalRequest).where(
                and_(
                    ToolApprovalRequest.status.in_([
                        ApprovalStatus.APPROVED,
                        ApprovalStatus.REJECTED,
                        ApprovalStatus.EXPIRED,
                        ApprovalStatus.CANCELLED,
                    ]),
                    ToolApprovalRequest.created_at < cutoff,
                )
            )
        )
        
        await self.db.commit()
        return result.rowcount
