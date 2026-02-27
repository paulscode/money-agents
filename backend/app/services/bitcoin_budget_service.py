"""
Bitcoin Budget Service — budget enforcement, transaction recording, and approval workflow.

Responsibilities:
- Check whether a spend is within campaign/global budget
- Record Bitcoin transactions in the immutable ledger
- Create and manage spend approval requests
- Provide budget summaries for campaigns and global dashboard
"""
import logging
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID

from sqlalchemy import select, func, and_, update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import Campaign
from app.models.bitcoin_budget import (
    BitcoinTransaction,
    BitcoinSpendApproval,
    BitcoinVelocityBreaker,
    TransactionType,
    TransactionStatus,
    SpendApprovalStatus,
    SpendTrigger,
)

logger = logging.getLogger(__name__)

# Budget threshold levels (ascending) — each triggers a notification once per hour
BUDGET_THRESHOLDS = [
    (0.80, "80%", "warning"),    # 80 % — early heads-up
    (0.90, "90%", "critical"),   # 90 % — getting tight
    (0.95, "95%", "danger"),     # 95 % — nearly exhausted
]


class BudgetCheckResult:
    """Result of a pre-spend budget check."""

    def __init__(
        self,
        allowed: bool,
        trigger: Optional[SpendTrigger] = None,
        reason: Optional[str] = None,
        budget_context: Optional[dict] = None,
    ):
        self.allowed = allowed
        self.trigger = trigger
        self.reason = reason
        self.budget_context = budget_context or {}

    def __repr__(self) -> str:
        return f"<BudgetCheckResult allowed={self.allowed} trigger={self.trigger}>"


class BitcoinBudgetService:
    """Core service for Bitcoin budget enforcement and transaction recording."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # Budget checks
    # ------------------------------------------------------------------

    async def check_spend(
        self,
        amount_sats: int,
        campaign_id: Optional[UUID] = None,
        fee_sats: int = 0,
        user_id: Optional[UUID] = None,
    ) -> BudgetCheckResult:
        """
        Check whether a proposed spend is allowed.

        Checks in order:
        1. Global safety limit (LND_MAX_PAYMENT_SATS) — includes fees
        2. Cumulative rate limit (max sats in rolling window)
        3. Campaign budget (if campaign_id provided and campaign has bitcoin_budget_sats)
           - Validates campaign ownership against user_id
           - Acquires row lock (SELECT FOR UPDATE) to prevent TOCTOU races

        Returns a BudgetCheckResult indicating whether the spend is allowed
        or which trigger caused it to require approval.
        """
        total_sats = amount_sats + fee_sats
        context: dict = {
            "amount_sats": amount_sats,
            "fee_sats": fee_sats,
            "total_sats": total_sats,
            "global_limit_sats": settings.lnd_max_payment_sats,
        }

        # 1. Global safety limit (total including fees)
        max_sats = settings.lnd_max_payment_sats
        if max_sats == 0:
            # All transactions require approval
            return BudgetCheckResult(
                allowed=False,
                trigger=SpendTrigger.GLOBAL_LIMIT,
                reason="All transactions require approval (safety limit set to 0)",
                budget_context=context,
            )
        if max_sats > 0 and total_sats > max_sats:
            context["exceeded_by_sats"] = total_sats - max_sats
            return BudgetCheckResult(
                allowed=False,
                trigger=SpendTrigger.GLOBAL_LIMIT,
                reason=(
                    f"Total spend {total_sats} sats (amount {amount_sats} + fee limit {fee_sats}) "
                    f"exceeds global safety limit of {max_sats} sats"
                ),
                budget_context=context,
            )

        # 2. Cumulative rate limit — prevent rapid-fire draining
        rate_limit_sats = settings.lnd_rate_limit_sats
        rate_limit_window = settings.lnd_rate_limit_window_seconds
        if rate_limit_sats > 0 and rate_limit_window > 0:
            window_start = utc_now() - timedelta(seconds=rate_limit_window)
            recent_spend_result = await self.db.execute(
                select(func.coalesce(func.sum(
                    BitcoinTransaction.amount_sats + BitcoinTransaction.fee_sats
                ), 0))
                .where(
                    and_(
                        BitcoinTransaction.tx_type.in_([
                            TransactionType.LIGHTNING_SEND,
                            TransactionType.ONCHAIN_SEND,
                        ]),
                        BitcoinTransaction.status.in_([
                            TransactionStatus.CONFIRMED,
                            TransactionStatus.PENDING,
                        ]),
                        BitcoinTransaction.created_at >= window_start,
                    )
                )
            )
            recent_spent = int(recent_spend_result.scalar() or 0)
            context["rate_limit_sats"] = rate_limit_sats
            context["rate_limit_window_seconds"] = rate_limit_window
            context["recent_window_spent_sats"] = recent_spent

            if recent_spent + total_sats > rate_limit_sats:
                context["rate_limit_remaining_sats"] = max(rate_limit_sats - recent_spent, 0)
                return BudgetCheckResult(
                    allowed=False,
                    trigger=SpendTrigger.GLOBAL_LIMIT,
                    reason=(
                        f"Cumulative spending rate limit reached: "
                        f"{recent_spent} + {total_sats} = {recent_spent + total_sats} sats "
                        f"exceeds {rate_limit_sats} sats in {rate_limit_window}s window"
                    ),
                    budget_context=context,
                )

        # 3. Velocity circuit breaker — block if already tripped, or trip it now
        velocity_max = settings.lnd_velocity_max_txns
        velocity_window = settings.lnd_velocity_window_seconds
        if velocity_max > 0 and velocity_window > 0:
            # Check if breaker is already tripped
            breaker = await self._get_or_create_velocity_breaker()
            if breaker.is_tripped:
                context["velocity_breaker_tripped_at"] = str(breaker.tripped_at)
                return BudgetCheckResult(
                    allowed=False,
                    trigger=SpendTrigger.VELOCITY_BREAKER,
                    reason=(
                        f"Velocity circuit breaker is tripped (since {breaker.tripped_at}). "
                        f"All agent payments are blocked until a human resets it "
                        f"via POST /wallet/reset-velocity-breaker."
                    ),
                    budget_context=context,
                )

            # Count recent send txns in the velocity window.
            # If the breaker was recently reset, only count txns AFTER the reset
            # so that the same burst doesn't immediately re-trip.
            vel_window_start = utc_now() - timedelta(seconds=velocity_window)
            if breaker.reset_at:
                # Use the later of (window_start, reset_at) — ignore pre-reset txns
                vel_window_start = max(vel_window_start, ensure_utc(breaker.reset_at))
            count_result = await self.db.execute(
                select(func.count(BitcoinTransaction.id))
                .where(
                    and_(
                        BitcoinTransaction.tx_type.in_([
                            TransactionType.LIGHTNING_SEND,
                            TransactionType.ONCHAIN_SEND,
                        ]),
                        BitcoinTransaction.status.in_([
                            TransactionStatus.CONFIRMED,
                            TransactionStatus.PENDING,
                        ]),
                        BitcoinTransaction.created_at >= vel_window_start,
                    )
                )
            )
            recent_tx_count = int(count_result.scalar() or 0)
            context["velocity_window_seconds"] = velocity_window
            context["velocity_max_txns"] = velocity_max
            context["velocity_recent_count"] = recent_tx_count

            # The *current* payment will be the (recent_tx_count + 1)th txn.
            # Trip the breaker if that crosses the threshold.
            if recent_tx_count + 1 > velocity_max:
                # Get recent tx IDs for audit context
                recent_ids_result = await self.db.execute(
                    select(BitcoinTransaction.id)
                    .where(
                        and_(
                            BitcoinTransaction.tx_type.in_([
                                TransactionType.LIGHTNING_SEND,
                                TransactionType.ONCHAIN_SEND,
                            ]),
                            BitcoinTransaction.status.in_([
                                TransactionStatus.CONFIRMED,
                                TransactionStatus.PENDING,
                            ]),
                            BitcoinTransaction.created_at >= vel_window_start,
                        )
                    )
                    .order_by(BitcoinTransaction.created_at.desc())
                    .limit(20)
                )
                recent_ids = [str(row[0]) for row in recent_ids_result.fetchall()]

                # Trip the breaker
                breaker.tripped_at = utc_now()
                breaker.trip_context = {
                    "count": recent_tx_count,
                    "window_seconds": velocity_window,
                    "threshold": velocity_max,
                    "recent_tx_ids": recent_ids,
                }
                await self.db.flush()

                logger.warning(
                    "VELOCITY BREAKER TRIPPED: %d txns in %ds window (threshold: %d). "
                    "All agent payments blocked until manual reset.",
                    recent_tx_count, velocity_window, velocity_max,
                )

                # Notify all admin users
                await self._notify_admins_breaker_tripped(
                    recent_tx_count, velocity_window, velocity_max,
                )

                context["velocity_breaker_tripped_at"] = str(breaker.tripped_at)
                return BudgetCheckResult(
                    allowed=False,
                    trigger=SpendTrigger.VELOCITY_BREAKER,
                    reason=(
                        f"Velocity circuit breaker tripped: {recent_tx_count} send transactions "
                        f"in the last {velocity_window}s exceeds threshold of {velocity_max}. "
                        f"All agent payments are now blocked until a human resets the breaker."
                    ),
                    budget_context=context,
                )

        # 4. Campaign budget check
        if campaign_id:
            # Acquire row lock to prevent TOCTOU race conditions.
            # This SELECT ... FOR UPDATE holds a lock on the campaign row until
            # the enclosing transaction commits (after payment + recording).
            try:
                result = await self.db.execute(
                    select(Campaign)
                    .where(Campaign.id == campaign_id)
                    .with_for_update()
                )
                campaign = result.scalar_one_or_none()
            except Exception:
                # Fallback for databases that don't support FOR UPDATE (e.g. SQLite in tests)
                campaign = await self._get_campaign(campaign_id)

            if campaign:
                # Validate campaign ownership — prevent agents from referencing
                # another campaign with a larger budget.
                # If campaign_id is provided but user_id is missing, reject
                # to prevent ownership check bypass.
                if not user_id:
                    return BudgetCheckResult(
                        allowed=False,
                        trigger=SpendTrigger.MANUAL_REVIEW,
                        reason=(
                            f"Campaign {campaign_id} spend requires a user_id "
                            f"for ownership validation."
                        ),
                        budget_context=context,
                    )
                if campaign.user_id != user_id:
                    return BudgetCheckResult(
                        allowed=False,
                        trigger=SpendTrigger.MANUAL_REVIEW,
                        reason=(
                            f"Campaign {campaign_id} does not belong to the requesting user. "
                            f"Agents may only spend from their own campaign's budget."
                        ),
                        budget_context=context,
                    )

                context["campaign_id"] = str(campaign_id)
                context["campaign_budget_sats"] = campaign.bitcoin_budget_sats
                context["campaign_spent_sats"] = campaign.bitcoin_spent_sats
                context["campaign_received_sats"] = campaign.bitcoin_received_sats

                if campaign.bitcoin_budget_sats is None:
                    # No budget set — require approval
                    return BudgetCheckResult(
                        allowed=False,
                        trigger=SpendTrigger.NO_BUDGET,
                        reason="Campaign has no Bitcoin budget set",
                        budget_context=context,
                    )

                remaining = campaign.bitcoin_budget_sats - campaign.bitcoin_spent_sats
                context["campaign_remaining_sats"] = remaining

                if total_sats > remaining:
                    context["over_by_sats"] = total_sats - remaining
                    return BudgetCheckResult(
                        allowed=False,
                        trigger=SpendTrigger.OVER_BUDGET,
                        reason=(
                            f"Spend of {total_sats} sats exceeds remaining "
                            f"campaign budget of {remaining} sats "
                            f"(budget: {campaign.bitcoin_budget_sats}, "
                            f"spent: {campaign.bitcoin_spent_sats})"
                        ),
                        budget_context=context,
                    )

        return BudgetCheckResult(allowed=True, budget_context=context)

    # ------------------------------------------------------------------
    # Transaction recording
    # ------------------------------------------------------------------

    async def record_transaction(
        self,
        user_id: UUID,
        tx_type: TransactionType,
        amount_sats: int,
        campaign_id: Optional[UUID] = None,
        fee_sats: int = 0,
        payment_hash: Optional[str] = None,
        payment_request: Optional[str] = None,
        txid: Optional[str] = None,
        address: Optional[str] = None,
        description: Optional[str] = None,
        agent_tool_execution_id: Optional[UUID] = None,
        approval_id: Optional[UUID] = None,
        status: TransactionStatus = TransactionStatus.PENDING,
    ) -> BitcoinTransaction:
        """Record a Bitcoin transaction in the immutable ledger."""
        tx = BitcoinTransaction(
            campaign_id=campaign_id,
            user_id=user_id,
            tx_type=tx_type,
            status=status,
            amount_sats=amount_sats,
            fee_sats=fee_sats,
            payment_hash=payment_hash,
            payment_request=payment_request,
            txid=txid,
            address=address,
            description=description,
            agent_tool_execution_id=agent_tool_execution_id,
            approval_id=approval_id,
            confirmed_at=utc_now() if status == TransactionStatus.CONFIRMED else None,
        )
        self.db.add(tx)
        await self.db.flush()

        # Update campaign running totals for sends (both PENDING and CONFIRMED).
        # We debit the budget at send-time to prevent double-spending.
        # For on-chain txns that start as PENDING, the budget is reserved immediately;
        # if the tx later fails, fail_transaction() will reverse the debit.
        if campaign_id and tx_type in (TransactionType.LIGHTNING_SEND, TransactionType.ONCHAIN_SEND):
            if status in (TransactionStatus.CONFIRMED, TransactionStatus.PENDING):
                await self._update_campaign_totals(campaign_id, tx_type, amount_sats, fee_sats)
        elif campaign_id and status == TransactionStatus.CONFIRMED:
            # Receives — only credit on confirmation
            await self._update_campaign_totals(campaign_id, tx_type, amount_sats, fee_sats)

        logger.info(
            "Recorded bitcoin transaction: type=%s amount=%d status=%s campaign=%s",
            tx_type.value, amount_sats, status.value, campaign_id,
        )
        return tx

    async def confirm_transaction(
        self,
        tx_id: UUID,
    ) -> Optional[BitcoinTransaction]:
        """Mark a pending transaction as confirmed and update campaign totals.

        For SEND transactions: budget was already debited at PENDING time,
        so confirmation does NOT re-debit.
        For RECEIVE transactions: budget is credited here on confirmation.
        """
        result = await self.db.execute(
            select(BitcoinTransaction).where(BitcoinTransaction.id == tx_id)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return None

        if tx.status != TransactionStatus.PENDING:
            logger.warning("Cannot confirm tx %s — status is %s", tx_id, tx.status.value)
            return tx

        tx.status = TransactionStatus.CONFIRMED
        tx.confirmed_at = utc_now()

        # Only update campaign totals for RECEIVES on confirmation.
        # Sends were already debited when recorded as PENDING.
        if tx.campaign_id and tx.tx_type in (
            TransactionType.LIGHTNING_RECEIVE,
            TransactionType.ONCHAIN_RECEIVE,
        ):
            await self._update_campaign_totals(
                tx.campaign_id, tx.tx_type, tx.amount_sats, tx.fee_sats
            )

        await self.db.flush()
        return tx

    async def fail_transaction(
        self,
        tx_id: UUID,
    ) -> Optional[BitcoinTransaction]:
        """Mark a pending transaction as failed and reverse any budget debit."""
        result = await self.db.execute(
            select(BitcoinTransaction).where(BitcoinTransaction.id == tx_id)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            return None

        if tx.status != TransactionStatus.PENDING:
            return tx

        tx.status = TransactionStatus.FAILED

        # Reverse the budget debit that was applied when the tx was recorded
        if tx.campaign_id and tx.tx_type in (
            TransactionType.LIGHTNING_SEND, TransactionType.ONCHAIN_SEND,
        ):
            campaign = await self._get_campaign(tx.campaign_id)
            if campaign:
                reversal = tx.amount_sats + tx.fee_sats
                campaign.bitcoin_spent_sats = max(
                    (campaign.bitcoin_spent_sats or 0) - reversal, 0
                )
                logger.info(
                    "Reversed budget debit of %d sats for failed tx %s on campaign %s",
                    reversal, tx_id, tx.campaign_id,
                )

        await self.db.flush()
        return tx

    async def cancel_pending_transaction(
        self,
        payment_request: str,
        user_id: UUID,
    ) -> Optional[BitcoinTransaction]:
        """Cancel a PENDING transaction identified by payment_request + user_id.

        Used by the reserve→pay→confirm pattern (SGA3-M7) to rollback a
        budget reservation when the actual payment fails.
        Delegates to fail_transaction() for budget reversal logic.
        """
        result = await self.db.execute(
            select(BitcoinTransaction).where(
                BitcoinTransaction.payment_request == payment_request,
                BitcoinTransaction.user_id == user_id,
                BitcoinTransaction.status == TransactionStatus.PENDING,
            ).order_by(BitcoinTransaction.created_at.desc()).limit(1)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            logger.warning(
                "No pending transaction found for payment_request=%s user=%s",
                payment_request[:20] if payment_request else "?", user_id,
            )
            return None
        return await self.fail_transaction(tx.id)

    async def confirm_pending_transaction(
        self,
        payment_request: str,
        user_id: UUID,
        payment_hash: str = "",
        fee_sats: int = 0,
    ) -> Optional[BitcoinTransaction]:
        """Confirm a PENDING transaction identified by payment_request + user_id.

        Used by the reserve→pay→confirm pattern (SGA3-M7).  Updates the
        reservation row with the actual payment_hash and fee, then
        delegates to confirm_transaction() for status + totals update.
        """
        result = await self.db.execute(
            select(BitcoinTransaction).where(
                BitcoinTransaction.payment_request == payment_request,
                BitcoinTransaction.user_id == user_id,
                BitcoinTransaction.status == TransactionStatus.PENDING,
            ).order_by(BitcoinTransaction.created_at.desc()).limit(1)
        )
        tx = result.scalar_one_or_none()
        if not tx:
            logger.warning(
                "No pending transaction found to confirm for "
                "payment_request=%s user=%s",
                payment_request[:20] if payment_request else "?", user_id,
            )
            return None

        # Patch the reservation row with actual payment details before
        # confirming, so confirm_transaction() has the real values.
        if payment_hash:
            tx.payment_hash = payment_hash
        # If actual fee differs from the estimated fee_limit, adjust the
        # campaign totals: reverse old estimate, apply actual fee.
        old_fee = tx.fee_sats or 0
        if fee_sats != old_fee and tx.campaign_id and tx.tx_type in (
            TransactionType.LIGHTNING_SEND, TransactionType.ONCHAIN_SEND,
        ):
            campaign = await self._get_campaign(tx.campaign_id)
            if campaign:
                fee_delta = fee_sats - old_fee
                campaign.bitcoin_spent_sats = max(
                    (campaign.bitcoin_spent_sats or 0) + fee_delta, 0
                )
        tx.fee_sats = fee_sats
        await self.db.flush()

        return await self.confirm_transaction(tx.id)

    # ------------------------------------------------------------------
    # Spend approval workflow
    # ------------------------------------------------------------------

    async def create_approval_request(
        self,
        requested_by_id: UUID,
        trigger: SpendTrigger,
        amount_sats: int,
        description: str,
        budget_context: dict,
        campaign_id: Optional[UUID] = None,
        fee_estimate_sats: int = 0,
        payment_request: Optional[str] = None,
        destination_address: Optional[str] = None,
    ) -> BitcoinSpendApproval:
        """Create a spend approval request."""
        approval = BitcoinSpendApproval(
            campaign_id=campaign_id,
            requested_by_id=requested_by_id,
            trigger=trigger,
            amount_sats=amount_sats,
            fee_estimate_sats=fee_estimate_sats,
            payment_request=payment_request,
            destination_address=destination_address,
            description=description,
            budget_context=budget_context,
        )
        self.db.add(approval)
        await self.db.flush()

        logger.info(
            "Created spend approval request: id=%s trigger=%s amount=%d campaign=%s",
            approval.id, trigger.value, amount_sats, campaign_id,
        )
        return approval

    async def review_approval(
        self,
        approval_id: UUID,
        reviewed_by_id: UUID,
        approved: bool,
        review_notes: Optional[str] = None,
    ) -> Optional[BitcoinSpendApproval]:
        """Approve or reject a spend approval request."""
        result = await self.db.execute(
            select(BitcoinSpendApproval).where(BitcoinSpendApproval.id == approval_id)
        )
        approval = result.scalar_one_or_none()
        if not approval:
            return None

        if not approval.can_be_reviewed():
            logger.warning(
                "Cannot review approval %s — status=%s expired=%s",
                approval_id, approval.status.value, approval.is_expired(),
            )
            return approval

        approval.status = (
            SpendApprovalStatus.APPROVED if approved else SpendApprovalStatus.REJECTED
        )
        approval.reviewed_by_id = reviewed_by_id
        approval.reviewed_at = utc_now()
        approval.review_notes = review_notes
        await self.db.flush()

        logger.info(
            "Reviewed spend approval %s: %s",
            approval_id,
            "approved" if approved else "rejected",
        )
        return approval

    async def get_pending_approvals(
        self,
        campaign_id: Optional[UUID] = None,
        limit: int = 50,
    ) -> list[BitcoinSpendApproval]:
        """Get pending spend approval requests."""
        query = (
            select(BitcoinSpendApproval)
            .where(BitcoinSpendApproval.status == SpendApprovalStatus.PENDING)
            .order_by(BitcoinSpendApproval.created_at.desc())
            .limit(limit)
        )
        if campaign_id:
            query = query.where(BitcoinSpendApproval.campaign_id == campaign_id)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_approval(
        self,
        approval_id: UUID,
    ) -> Optional[BitcoinSpendApproval]:
        """Get a specific approval request."""
        result = await self.db.execute(
            select(BitcoinSpendApproval).where(BitcoinSpendApproval.id == approval_id)
        )
        return result.scalar_one_or_none()

    async def count_pending_approvals(
        self,
        campaign_id: Optional[UUID] = None,
    ) -> int:
        """Count pending approval requests."""
        query = select(func.count()).select_from(BitcoinSpendApproval).where(
            BitcoinSpendApproval.status == SpendApprovalStatus.PENDING
        )
        if campaign_id:
            query = query.where(BitcoinSpendApproval.campaign_id == campaign_id)
        result = await self.db.execute(query)
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Budget summaries
    # ------------------------------------------------------------------

    async def get_campaign_budget(
        self,
        campaign_id: UUID,
    ) -> dict:
        """Get Bitcoin budget summary for a campaign."""
        campaign = await self._get_campaign(campaign_id)
        if not campaign:
            return {}

        pending_count = await self.count_pending_approvals(campaign_id)

        # Get recent transactions
        result = await self.db.execute(
            select(BitcoinTransaction)
            .where(BitcoinTransaction.campaign_id == campaign_id)
            .order_by(BitcoinTransaction.created_at.desc())
            .limit(20)
        )
        recent_txs = list(result.scalars().all())

        remaining = None
        if campaign.bitcoin_budget_sats is not None:
            remaining = campaign.bitcoin_budget_sats - campaign.bitcoin_spent_sats

        return {
            "campaign_id": str(campaign_id),
            "bitcoin_budget_sats": campaign.bitcoin_budget_sats,
            "bitcoin_spent_sats": campaign.bitcoin_spent_sats,
            "bitcoin_received_sats": campaign.bitcoin_received_sats,
            "bitcoin_remaining_sats": remaining,
            "pending_approvals": pending_count,
            "recent_transactions": recent_txs,
        }

    async def get_global_budget(self) -> dict:
        """Get global Bitcoin budget rollup across all campaigns."""
        # Aggregate from campaigns
        result = await self.db.execute(
            select(
                func.coalesce(func.sum(Campaign.bitcoin_budget_sats), 0).label("total_budget"),
                func.coalesce(func.sum(Campaign.bitcoin_spent_sats), 0).label("total_spent"),
                func.coalesce(func.sum(Campaign.bitcoin_received_sats), 0).label("total_received"),
                func.count().filter(Campaign.bitcoin_budget_sats.isnot(None)).label("with_budget"),
            ).where(
                Campaign.status.notin_(["completed", "terminated", "failed"])
            )
        )
        row = result.one()
        total_budget = int(row.total_budget)
        total_spent = int(row.total_spent)
        total_received = int(row.total_received)
        with_budget = int(row.with_budget)

        # Count over-budget campaigns
        over_budget_result = await self.db.execute(
            select(func.count()).select_from(Campaign).where(
                and_(
                    Campaign.bitcoin_budget_sats.isnot(None),
                    Campaign.bitcoin_spent_sats > Campaign.bitcoin_budget_sats,
                    Campaign.status.notin_(["completed", "terminated", "failed"]),
                )
            )
        )
        over_budget = over_budget_result.scalar() or 0

        # Count near-budget campaigns (≥80% spent but not over budget)
        near_budget_result = await self.db.execute(
            select(func.count()).select_from(Campaign).where(
                and_(
                    Campaign.bitcoin_budget_sats.isnot(None),
                    Campaign.bitcoin_budget_sats > 0,
                    Campaign.bitcoin_spent_sats <= Campaign.bitcoin_budget_sats,
                    Campaign.bitcoin_spent_sats >= Campaign.bitcoin_budget_sats * 0.8,
                    Campaign.status.notin_(["completed", "terminated", "failed"]),
                )
            )
        )
        near_budget = near_budget_result.scalar() or 0

        # Pending approval count
        pending_approvals = await self.count_pending_approvals()

        # Pending (unconfirmed) sends
        pending_sends_result = await self.db.execute(
            select(func.coalesce(func.sum(BitcoinTransaction.amount_sats), 0))
            .where(
                and_(
                    BitcoinTransaction.status == TransactionStatus.PENDING,
                    BitcoinTransaction.tx_type.in_(["lightning_send", "onchain_send"]),
                )
            )
        )
        pending_sats = int(pending_sends_result.scalar() or 0)

        return {
            "total_budget_sats": total_budget,
            "total_spent_sats": total_spent,
            "total_received_sats": total_received,
            "total_remaining_sats": total_budget - total_spent if total_budget > 0 else 0,
            "total_pending_sats": pending_sats,
            "global_limit_sats": settings.lnd_max_payment_sats,
            "campaigns_with_budget": with_budget,
            "campaigns_over_budget": over_budget,
            "campaigns_near_budget": near_budget,
            "pending_approvals": pending_approvals,
        }

    async def get_campaign_transactions(
        self,
        campaign_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BitcoinTransaction]:
        """Get transactions for a specific campaign."""
        result = await self.db.execute(
            select(BitcoinTransaction)
            .where(BitcoinTransaction.campaign_id == campaign_id)
            .order_by(BitcoinTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def get_all_transactions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BitcoinTransaction]:
        """Get all transactions across campaigns."""
        result = await self.db.execute(
            select(BitcoinTransaction)
            .order_by(BitcoinTransaction.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get_campaign(self, campaign_id: UUID) -> Optional[Campaign]:
        """Fetch a campaign by ID."""
        result = await self.db.execute(
            select(Campaign).where(Campaign.id == campaign_id)
        )
        return result.scalar_one_or_none()

    async def _get_or_create_velocity_breaker(self) -> BitcoinVelocityBreaker:
        """Return the singleton velocity breaker row, creating it if absent."""
        result = await self.db.execute(
            select(BitcoinVelocityBreaker).where(BitcoinVelocityBreaker.id == 1)
        )
        breaker = result.scalar_one_or_none()
        if not breaker:
            breaker = BitcoinVelocityBreaker(id=1)
            self.db.add(breaker)
            await self.db.flush()
        return breaker

    async def reset_velocity_breaker(self, user_id: UUID) -> BitcoinVelocityBreaker:
        """Reset a tripped velocity breaker.  Only callable by a human user."""
        breaker = await self._get_or_create_velocity_breaker()
        if not breaker.is_tripped:
            return breaker

        logger.warning(
            "VELOCITY BREAKER RESET by user=%s (was tripped at %s)",
            user_id, breaker.tripped_at,
        )
        breaker.tripped_at = None
        breaker.tripped_by_tx_id = None
        breaker.trip_context = None
        breaker.reset_at = utc_now()
        breaker.reset_by_user_id = user_id
        await self.db.flush()
        return breaker

    async def get_velocity_breaker_status(self) -> dict:
        """Return the current velocity breaker status for the UI."""
        breaker = await self._get_or_create_velocity_breaker()
        return {
            "is_tripped": breaker.is_tripped,
            "tripped_at": str(breaker.tripped_at) if breaker.tripped_at else None,
            "trip_context": breaker.trip_context,
            "reset_at": str(breaker.reset_at) if breaker.reset_at else None,
            "config": {
                "max_txns": settings.lnd_velocity_max_txns,
                "window_seconds": settings.lnd_velocity_window_seconds,
            },
        }

    async def _notify_admins_breaker_tripped(
        self,
        tx_count: int,
        window_seconds: int,
        threshold: int,
    ) -> None:
        """Send an URGENT notification to all admin users when the breaker trips."""
        try:
            from app.models import User, UserRole
            from app.services.notification_service import NotificationService

            result = await self.db.execute(
                select(User.id).where(User.role == UserRole.ADMIN.value)
            )
            admin_ids = [row[0] for row in result.fetchall()]

            if not admin_ids:
                logger.warning("No admin users found to notify about velocity breaker trip")
                return

            notif_svc = NotificationService(self.db)
            for admin_id in admin_ids:
                await notif_svc.notify_velocity_breaker_tripped(
                    user_id=admin_id,
                    tx_count=tx_count,
                    window_seconds=window_seconds,
                    threshold=threshold,
                )
            logger.info("Notified %d admin(s) about velocity breaker trip", len(admin_ids))
        except Exception as exc:
            # Never let notification failure block the breaker trip
            logger.error("Failed to notify admins about velocity breaker: %s", exc)

    async def _update_campaign_totals(
        self,
        campaign_id: UUID,
        tx_type: TransactionType,
        amount_sats: int,
        fee_sats: int,
    ) -> None:
        """Update campaign running totals after a confirmed transaction.
        
        Uses atomic SQL UPDATE to avoid read-modify-write race conditions (SA2-06).
        """
        total = amount_sats + fee_sats

        if tx_type in (TransactionType.LIGHTNING_SEND, TransactionType.ONCHAIN_SEND):
            stmt = (
                sql_update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(
                    bitcoin_spent_sats=func.coalesce(Campaign.bitcoin_spent_sats, 0) + total
                )
            )
        elif tx_type in (TransactionType.LIGHTNING_RECEIVE, TransactionType.ONCHAIN_RECEIVE):
            stmt = (
                sql_update(Campaign)
                .where(Campaign.id == campaign_id)
                .values(
                    bitcoin_received_sats=func.coalesce(Campaign.bitcoin_received_sats, 0) + amount_sats
                )
            )
        else:
            return

        await self.db.execute(stmt)
        await self.db.flush()

        # ── Threshold alerts ──────────────────────────────────────────
        if tx_type in (TransactionType.LIGHTNING_SEND, TransactionType.ONCHAIN_SEND):
            # Re-fetch campaign after atomic update for threshold check
            campaign = await self._get_campaign(campaign_id)
            if campaign:
                await self._check_budget_thresholds(campaign)

    async def _check_budget_thresholds(self, campaign: Campaign) -> None:
        """
        Fire threshold-warning notifications when campaign spend crosses
        80 %, 90 %, or 95 % of its budget.

        Notifications are rate-limited (1 per hour per campaign) by the
        existing NotificationService rule, so even rapid-fire transactions
        won't spam the user.
        """
        budget = campaign.bitcoin_budget_sats
        if not budget or budget <= 0:
            return

        spent = campaign.bitcoin_spent_sats or 0
        pct = spent / budget

        # Find the highest threshold crossed
        crossed = None
        for ratio, label, severity in BUDGET_THRESHOLDS:
            if pct >= ratio:
                crossed = (ratio, label, severity)

        if not crossed:
            return

        ratio, label, severity = crossed
        remaining = max(budget - spent, 0)
        campaign_title = campaign.title or "Unnamed campaign"

        logger.info(
            "Budget threshold %s crossed for campaign %s (%s): spent=%d / budget=%d (%.1f%%)",
            label, campaign.id, campaign_title, spent, budget, pct * 100,
        )

        # ── Persistent notification (rate-limited: 1/hr/campaign) ─────
        try:
            from app.services.notification_service import NotificationService
            from app.models.notification import NotificationType

            notif_svc = NotificationService(self.db)
            await notif_svc.notify_budget_threshold(
                user_id=campaign.user_id,
                campaign_id=campaign.id,
                campaign_title=campaign_title,
                threshold_label=label,
                severity=severity,
                spent_sats=spent,
                budget_sats=budget,
                remaining_sats=remaining,
                percent_used=round(pct * 100, 1),
            )
        except Exception:
            logger.exception("Failed to create budget threshold notification")

        # ── Real-time WebSocket push ──────────────────────────────────
        try:
            from app.services.campaign_progress_service import campaign_progress_service

            await campaign_progress_service.emit_budget_warning(
                campaign_id=campaign.id,
                threshold_label=label,
                severity=severity,
                spent_sats=spent,
                budget_sats=budget,
                remaining_sats=remaining,
                percent_used=round(pct * 100, 1),
            )
        except Exception:
            logger.exception("Failed to emit budget warning WebSocket event")
