"""
Cold Storage API endpoints — Lightning-to-On-Chain via Boltz.

Provides endpoints for:
- Getting Boltz swap fees and limits
- Initiating a Lightning-to-cold-storage reverse swap
- Checking swap status
- Cancelling a swap (if early enough)
- Listing recent swaps

All write operations require admin auth.
"""
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models.boltz_swap import SwapStatus
from app.services.boltz_service import boltz_service, BOLTZ_MIN_AMOUNT_SATS, BOLTZ_MAX_AMOUNT_SATS

router = APIRouter()
logger = logging.getLogger(__name__)


def require_lnd():
    """Dependency that checks if LND is enabled."""
    if not settings.use_lnd:
        raise HTTPException(
            status_code=404,
            detail="Bitcoin wallet (LND) is not enabled. Set USE_LND=true in configuration."
        )


# ──────────────────────────────────────────────────────────────────────
# Request/Response Models
# ──────────────────────────────────────────────────────────────────────

class LightningColdStorageRequest(BaseModel):
    """Request to initiate a Lightning-to-cold-storage swap."""
    amount_sats: int = Field(
        ...,
        ge=BOLTZ_MIN_AMOUNT_SATS,
        le=BOLTZ_MAX_AMOUNT_SATS,
        description=f"Amount in sats ({BOLTZ_MIN_AMOUNT_SATS:,} – {BOLTZ_MAX_AMOUNT_SATS:,})",
    )
    destination_address: str = Field(
        ...,
        min_length=26,
        max_length=256,
        description="Bitcoin cold storage address",
    )

    @field_validator("destination_address")
    @classmethod
    def validate_bitcoin_address(cls, v: str) -> str:
        """Validate Bitcoin address format (mainnet only)."""
        # Bech32/Bech32m (bc1...)
        if re.match(r"^bc1[a-zA-HJ-NP-Z0-9]{25,87}$", v):
            return v
        # P2PKH (1...)
        if re.match(r"^1[a-km-zA-HJ-NP-Z1-9]{25,34}$", v):
            return v
        # P2SH (3...)
        if re.match(r"^3[a-km-zA-HJ-NP-Z1-9]{25,34}$", v):
            return v
        raise ValueError(
            "Invalid Bitcoin address. Must be a mainnet address starting with bc1, 1, or 3."
        )
    routing_fee_limit_percent: float = Field(
        default=3.0,
        ge=0.1,
        le=10.0,
        description="Maximum Lightning routing fee as % of amount (default 3%)",
    )


class SwapStatusResponse(BaseModel):
    """Status of a Lightning cold storage swap."""
    id: str
    boltz_swap_id: str
    status: str
    boltz_status: str | None = None
    invoice_amount_sats: int
    onchain_amount_sats: int | None = None
    destination_address: str
    fee_percentage: str | None = None
    miner_fee_sats: int | None = None
    boltz_invoice: str | None = None
    claim_txid: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class SwapFeeInfo(BaseModel):
    """Boltz reverse swap fee information."""
    min_amount_sats: int
    max_amount_sats: int
    fee_percentage: float
    miner_fee_lockup_sats: int
    miner_fee_claim_sats: int
    total_miner_fee_sats: int


def _swap_to_response(swap) -> dict:
    """Convert a BoltzSwap model to API response dict."""
    return {
        "id": str(swap.id),
        "boltz_swap_id": swap.boltz_swap_id,
        "status": swap.status.value,
        "boltz_status": swap.boltz_status,
        "invoice_amount_sats": swap.invoice_amount_sats,
        "onchain_amount_sats": swap.onchain_amount_sats,
        "destination_address": swap.destination_address,
        "fee_percentage": swap.fee_percentage,
        "miner_fee_sats": swap.miner_fee_sats,
        "boltz_invoice": swap.boltz_invoice,
        "claim_txid": swap.claim_txid,
        "error_message": swap.error_message,
        "created_at": swap.created_at.isoformat() if swap.created_at else None,
        "updated_at": swap.updated_at.isoformat() if swap.updated_at else None,
        "completed_at": swap.completed_at.isoformat() if swap.completed_at else None,
    }


# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────

@router.get("/wallet/cold-storage/lightning/fees")
async def get_lightning_cold_storage_fees(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get current Boltz reverse swap fees and limits.

    Returns fee percentages, miner fees, and min/max amount limits
    for Lightning-to-on-chain reverse submarine swaps.
    """
    pair_info, error = await boltz_service.get_reverse_pair_info()
    if error:
        raise HTTPException(status_code=503, detail=f"Unable to fetch Boltz fees: {error}")

    return {
        "min_amount_sats": pair_info["min"],
        "max_amount_sats": pair_info["max"],
        "fee_percentage": pair_info["fees_percentage"],
        "miner_fee_lockup_sats": pair_info["fees_miner_lockup"],
        "miner_fee_claim_sats": pair_info["fees_miner_claim"],
        "total_miner_fee_sats": pair_info["fees_miner_lockup"] + pair_info["fees_miner_claim"],
        "tor_enabled": settings.boltz_use_tor and bool(settings.lnd_tor_proxy),
        "default_routing_fee_limit_percent": 3.0,
    }


@router.post("/wallet/cold-storage/lightning")
@limiter.limit("3/minute")
async def initiate_lightning_cold_storage(
    request: Request,
    req: LightningColdStorageRequest,
    user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
):
    """Start a Boltz reverse swap to send Lightning funds to cold storage.

    1. Creates a reverse swap with Boltz (via Tor)
    2. Returns swap details including the hold invoice to pay
    3. Background task monitors progress and handles claim

    The Celery worker will:
    - Pay the Boltz hold invoice via LND
    - Wait for Boltz to lock on-chain BTC
    - Construct and broadcast the claim transaction
    """
    from app.services.lnd_service import lnd_service

    # Check Lightning balance
    channel_balance = await lnd_service.get_channel_balance()
    if channel_balance:
        local_balance = int(channel_balance.get("local_balance_sat", 0))
        if local_balance < req.amount_sats:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient Lightning balance: {local_balance:,} sats available, "
                       f"{req.amount_sats:,} sats requested.",
            )

    # Create the swap
    swap, error = await boltz_service.create_reverse_swap(
        db=db,
        user_id=user.id,
        invoice_amount_sats=req.amount_sats,
        destination_address=req.destination_address,
    )
    if error:
        raise HTTPException(status_code=502, detail=error)

    # Schedule the swap monitoring task
    from app.tasks.boltz_tasks import process_boltz_swap
    process_boltz_swap.delay(str(swap.id), routing_fee_limit_percent=req.routing_fee_limit_percent)

    logger.info(
        f"Lightning cold storage swap initiated: {swap.boltz_swap_id}, "
        f"amount={req.amount_sats} sats, user={user.id}"
    )

    return _swap_to_response(swap)


@router.get("/wallet/cold-storage/lightning/swaps")
async def list_lightning_cold_storage_swaps(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
    limit: int = 20,
):
    """List recent Lightning cold storage swaps."""
    swaps = await boltz_service.get_swaps_for_user(db, _user.id, min(limit, 50))
    return {
        "swaps": [_swap_to_response(s) for s in swaps],
    }


@router.get("/wallet/cold-storage/lightning/{swap_id}")
async def get_lightning_cold_storage_status(
    swap_id: str,
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
):
    """Get current status of a Lightning cold storage swap.

    Returns full swap state including Boltz status, claim txid, and errors.
    """
    try:
        swap_uuid = UUID(swap_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid swap ID format")

    swap = await boltz_service.get_swap_by_id(db, swap_uuid)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap not found")

    # Verify the requesting user owns this swap
    if swap.user_id != _user.id:
        raise HTTPException(status_code=404, detail="Swap not found")

    return _swap_to_response(swap)


@router.post("/wallet/cold-storage/lightning/{swap_id}/cancel")
async def cancel_lightning_cold_storage(
    swap_id: str,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a swap if still in early stages (before invoice payment).

    Only swaps in 'created' status can be cancelled.
    """
    try:
        swap_uuid = UUID(swap_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid swap ID format")

    swap = await boltz_service.get_swap_by_id(db, swap_uuid)
    if not swap:
        raise HTTPException(status_code=404, detail="Swap not found")

    # Verify the requesting user owns this swap
    if swap.user_id != _user.id:
        raise HTTPException(status_code=404, detail="Swap not found")

    success, error = await boltz_service.cancel_swap(db, swap)
    if not success:
        raise HTTPException(status_code=400, detail=error)

    return _swap_to_response(swap)

