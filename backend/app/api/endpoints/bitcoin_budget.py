"""
Bitcoin Budget & Spend Approval endpoints.

Provides:
- Spend approval CRUD (create, review, list, cancel)
- Budget summaries (per-campaign and global)
- Transaction history
- Spend Advisor chat (REST + WebSocket)
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_admin, get_db
from app.core.config import settings
from app.core.database import get_db_context
from app.core.rate_limit import limiter
from app.models import User, ConversationType
from app.services.llm_usage_service import llm_usage_service
from app.models.llm_usage import LLMUsageSource
from app.models.bitcoin_budget import (
    SpendApprovalStatus,
    SpendTrigger,
)
from app.schemas.bitcoin_budget import (
    SpendApprovalCreate,
    SpendApprovalReview,
    SpendApprovalResponse,
    BitcoinTransactionResponse,
    CampaignBitcoinBudget,
    GlobalBitcoinBudget,
)
from app.services.bitcoin_budget_service import BitcoinBudgetService

router = APIRouter()


def _require_lnd():
    """Dependency that checks if LND is enabled."""
    if not settings.use_lnd:
        raise HTTPException(
            status_code=404,
            detail="Bitcoin wallet (LND) is not enabled. Set USE_LND=true."
        )


# ──────────────────────────────────────────────────────────────────────────
# Spend approval endpoints
# ──────────────────────────────────────────────────────────────────────────

@router.get("/approvals")
async def list_pending_approvals(
    campaign_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """List pending Bitcoin spend approval requests."""
    service = BitcoinBudgetService(db)
    approvals = await service.get_pending_approvals(
        campaign_id=campaign_id, limit=limit
    )
    return {
        "approvals": [
            SpendApprovalResponse.model_validate(a) for a in approvals
        ],
        "total": len(approvals),
    }


@router.get("/approvals/count")
async def count_pending_approvals(
    campaign_id: Optional[UUID] = Query(None),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Get count of pending approval requests."""
    service = BitcoinBudgetService(db)
    count = await service.count_pending_approvals(campaign_id=campaign_id)
    return {"pending_count": count}


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Get a specific spend approval request."""
    service = BitcoinBudgetService(db)
    approval = await service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")
    return SpendApprovalResponse.model_validate(approval)


@router.post("/approvals/{approval_id}/review")
@limiter.limit("10/minute")
async def review_approval(
    request: Request,
    approval_id: UUID,
    review: SpendApprovalReview,
    user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Approve or reject a spend approval request (admin only)."""
    service = BitcoinBudgetService(db)

    approved = review.action == "approved"
    result = await service.review_approval(
        approval_id=approval_id,
        reviewed_by_id=user.id,
        approved=approved,
        review_notes=review.review_notes,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if result.status == SpendApprovalStatus.PENDING:
        # Couldn't transition — already expired or not pending
        raise HTTPException(
            status_code=409,
            detail="Approval request cannot be reviewed (expired or already resolved)"
        )

    await db.commit()
    return SpendApprovalResponse.model_validate(result)


@router.post("/approvals/{approval_id}/cancel")
async def cancel_approval(
    approval_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Cancel a pending spend approval request (requestor only)."""
    service = BitcoinBudgetService(db)
    approval = await service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval request not found")

    if approval.requested_by_id != user.id:
        raise HTTPException(status_code=403, detail="Only requestor can cancel")

    if approval.status != SpendApprovalStatus.PENDING:
        raise HTTPException(status_code=409, detail="Only pending approvals can be cancelled")

    approval.status = SpendApprovalStatus.CANCELLED
    await db.commit()
    return SpendApprovalResponse.model_validate(approval)


# ──────────────────────────────────────────────────────────────────────────
# Budget summary endpoints
# ──────────────────────────────────────────────────────────────────────────

@router.get("/budget/global")
async def get_global_budget(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Get global Bitcoin budget summary across all campaigns."""
    service = BitcoinBudgetService(db)
    summary = await service.get_global_budget()

    # Optionally include LND wallet balance
    try:
        from app.services.lnd_service import lnd_service
        wallet = await lnd_service.get_wallet_balance()
        channel = await lnd_service.get_channel_balance()
        wallet_sats = 0
        if wallet:
            wallet_sats += wallet.get("confirmed_balance", 0)
        if channel:
            wallet_sats += channel.get("local_balance_sat", 0)
        summary["wallet_balance_sats"] = wallet_sats
    except Exception:
        summary["wallet_balance_sats"] = None

    return GlobalBitcoinBudget(**summary)


@router.get("/budget/campaign/{campaign_id}")
async def get_campaign_budget(
    campaign_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Get Bitcoin budget summary for a specific campaign."""
    service = BitcoinBudgetService(db)
    budget = await service.get_campaign_budget(campaign_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Campaign not found")

    # Convert recent_transactions to response schemas
    budget["recent_transactions"] = [
        BitcoinTransactionResponse.model_validate(tx)
        for tx in budget.get("recent_transactions", [])
    ]

    return CampaignBitcoinBudget(**budget)


# ──────────────────────────────────────────────────────────────────────────
# Transaction history endpoints
# ──────────────────────────────────────────────────────────────────────────

@router.get("/transactions")
async def list_transactions(
    campaign_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """List Bitcoin transactions, optionally filtered by campaign."""
    service = BitcoinBudgetService(db)
    if campaign_id:
        txs = await service.get_campaign_transactions(
            campaign_id=campaign_id, limit=limit, offset=offset
        )
    else:
        txs = await service.get_all_transactions(limit=limit, offset=offset)

    return {
        "transactions": [
            BitcoinTransactionResponse.model_validate(tx) for tx in txs
        ],
        "total": len(txs),
    }


# ──────────────────────────────────────────────────────────────────────────
# Spend Advisor — one-shot analysis
# ──────────────────────────────────────────────────────────────────────────

@router.get("/approvals/{approval_id}/analysis")
async def get_spend_analysis(
    approval_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    _lnd=Depends(_require_lnd),
):
    """Get an AI analysis of a spend approval request.

    Uses the Spend Advisor agent (quality tier) to analyze the request
    with a skeptical, careful approach.
    """
    from app.agents.spend_advisor import spend_advisor_agent

    service = BitcoinBudgetService(db)
    approval = await service.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    # Build context for the advisor
    approval_data = {
        "amount_sats": approval.amount_sats,
        "fee_estimate_sats": approval.fee_estimate_sats,
        "trigger": approval.trigger.value if approval.trigger else "unknown",
        "description": approval.description,
        "status": approval.status.value if approval.status else "unknown",
        "payment_request": approval.payment_request,
        "destination_address": approval.destination_address,
    }

    budget_context = approval.budget_context or {}

    campaign_data = None
    if approval.campaign_id:
        from app.models import Campaign
        from sqlalchemy import select
        result = await db.execute(
            select(Campaign).where(Campaign.id == approval.campaign_id)
        )
        campaign = result.scalar_one_or_none()
        if campaign:
            campaign_data = {
                "id": str(campaign.id),
                "title": getattr(campaign, "current_phase", "Unknown"),
                "status": campaign.status.value if campaign.status else "unknown",
                "budget_allocated": float(campaign.budget_allocated),
                "budget_spent": float(campaign.budget_spent),
            }

    # Decode invoice if available
    decoded_invoice = None
    if approval.payment_request:
        try:
            from app.services.lnd_service import lnd_service
            decoded, _ = await lnd_service.decode_payment_request(approval.payment_request)
            decoded_invoice = decoded
        except Exception:
            pass

    analysis = await spend_advisor_agent.analyze_spend(
        approval_data=approval_data,
        budget_context=budget_context,
        campaign_data=campaign_data,
        decoded_invoice=decoded_invoice,
    )

    return {
        "approval_id": str(approval_id),
        "analysis": analysis,
        "model_tier": "quality",
    }


# ──────────────────────────────────────────────────────────────────────────
# Spend Advisor — WebSocket streaming chat
# ──────────────────────────────────────────────────────────────────────────

@router.websocket("/advisor/stream")
async def websocket_spend_advisor_stream(websocket: WebSocket):
    """
    WebSocket endpoint for streaming chat with the Spend Advisor agent.

    Protocol:
    1. Connect to WebSocket
    2. Send auth: {"type": "auth", "token": "<access_token>"}
    3. Receive: {"type": "auth_result", "success": true/false}
    4. Send: {"type": "message", "content": "<question>", "approval_id": "<required>", "conversation_id": "<optional>"}
    5. Receive streamed:
       - {"type": "chunk", "content": "<text>"}
       - {"type": "done", "model": "...", "tokens": N}
    """
    import logging
    from app.agents.base import AgentContext
    from app.agents.spend_advisor import spend_advisor_agent
    from app.api.websocket_security import (
        authenticate_websocket,
        ws_receive_validated,
        WSConnectionGuard,
    )

    logger = logging.getLogger(__name__)
    await websocket.accept()

    try:
        user = await authenticate_websocket(websocket)
        if not user:
            await websocket.send_json({
                "type": "auth_result",
                "success": False,
                "error": "Authentication failed",
            })
            await websocket.close(code=4001, reason="Authentication failed")
            return

        await websocket.send_json({
            "type": "auth_result",
            "success": True,
            "user_id": str(user.id),
        })

        # SGA-M2/L3: Use WSConnectionGuard and ws_receive_validated
        async with WSConnectionGuard(str(user.id)) as guard:
            if guard.rejected:
                await websocket.send_json({"type": "error", "error": "Too many connections"})
                await websocket.close(code=4008, reason="Too many connections")
                return

            rate_state: dict = {}
            while True:
                try:
                    data = await ws_receive_validated(websocket, rate_state=rate_state)
                    if data.get("type") == "_oversized":
                        await websocket.send_json({"type": "error", "error": "Message too large"})
                        continue
                    if data.get("type") == "_rate_limited":
                        continue
                except WebSocketDisconnect:
                    break

                msg_type = data.get("type")

                if msg_type == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue

                if msg_type != "message":
                    await websocket.send_json({
                        "type": "error",
                        "error": f"Unknown message type: {msg_type}",
                    })
                    continue

                content = data.get("content", "").strip()
                if not content:
                    await websocket.send_json({
                        "type": "error",
                        "error": "Empty message content",
                    })
                    continue

                approval_id = data.get("approval_id")
                if not approval_id:
                    await websocket.send_json({
                        "type": "error",
                        "error": "approval_id is required for Spend Advisor chat",
                    })
                    continue

                conversation_id = data.get("conversation_id")

                try:
                    async with get_db_context() as db:
                        # Load approval context
                        service = BitcoinBudgetService(db)
                        approval = await service.get_approval(UUID(approval_id))
                        if not approval:
                            await websocket.send_json({
                                "type": "error",
                                "error": "Approval not found",
                            })
                            continue

                        # Build rich context
                        spend_context = {
                            "approval": {
                                "amount_sats": approval.amount_sats,
                                "fee_estimate_sats": approval.fee_estimate_sats,
                                "trigger": approval.trigger.value,
                                "description": approval.description,
                                "status": approval.status.value,
                                "payment_request": approval.payment_request,
                                "destination_address": approval.destination_address,
                            },
                            "budget_context": approval.budget_context or {},
                        }

                        # Load campaign if linked
                        if approval.campaign_id:
                            from app.models import Campaign
                            from sqlalchemy import select
                            result = await db.execute(
                                select(Campaign).where(Campaign.id == approval.campaign_id)
                            )
                            campaign = result.scalar_one_or_none()
                            if campaign:
                                spend_context["campaign"] = {
                                    "id": str(campaign.id),
                                    "status": campaign.status.value,
                                    "budget_allocated": float(campaign.budget_allocated),
                                    "budget_spent": float(campaign.budget_spent),
                                }

                        # Decode invoice
                        if approval.payment_request:
                            try:
                                from app.services.lnd_service import lnd_service
                                decoded, _ = await lnd_service.decode_payment_request(
                                    approval.payment_request
                                )
                                if decoded:
                                    spend_context["decoded_invoice"] = decoded
                            except Exception:
                                pass

                        context = AgentContext(
                            db=db,
                            conversation_id=UUID(conversation_id) if conversation_id else None,
                            related_id=UUID(approval_id),
                            user_id=user.id,
                            extra={"spend_advisor_context": spend_context},
                        )

                        full_content = []

                        async for chunk in spend_advisor_agent.respond_to_message_stream(
                            context=context,
                            user_message=content,
                        ):
                            if chunk.is_final:
                                # Track LLM usage
                                await llm_usage_service.track(
                                    db=db,
                                    source=LLMUsageSource.AGENT_CHAT,
                                    provider=chunk.provider or "",
                                    model=chunk.model or "",
                                    prompt_tokens=chunk.prompt_tokens,
                                    completion_tokens=chunk.completion_tokens,
                                    user_id=user.id,
                                    conversation_id=context.conversation_id,
                                    latency_ms=chunk.latency_ms,
                                    meta_data={"agent": "spend_advisor"},
                                )
                                
                                await websocket.send_json({
                                    "type": "done",
                                    "model": chunk.model,
                                    "provider": chunk.provider,
                                    "prompt_tokens": chunk.prompt_tokens,
                                    "completion_tokens": chunk.completion_tokens,
                                    "total_tokens": chunk.total_tokens,
                                    "latency_ms": chunk.latency_ms,
                                })

                                # Save to conversation
                                if context.conversation_id:
                                    full_text = "".join(full_content)
                                    await spend_advisor_agent.send_message(
                                        context=context,
                                        content=full_text,
                                        tokens_used=chunk.total_tokens,
                                        model_used=chunk.model,
                                        prompt_tokens=chunk.prompt_tokens,
                                        completion_tokens=chunk.completion_tokens,
                                    )
                                    await db.commit()
                            else:
                                full_content.append(chunk.content)
                                await websocket.send_json({
                                    "type": "chunk",
                                    "content": chunk.content,
                                })

                except Exception as e:
                    logger.exception(f"Error in Spend Advisor stream for user {user.id}")
                    await websocket.send_json({
                        "type": "error",
                        "error": "An internal error occurred. Please try again.",
                    })

    except WebSocketDisconnect:
        logger.info("Spend Advisor WebSocket disconnected")
    except Exception as e:
        logger.exception("Spend Advisor WebSocket error")
        try:
            await websocket.send_json({"type": "error", "error": "An internal error occurred."})
        except Exception:
            pass
