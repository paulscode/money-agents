"""
Wallet API endpoints

Provides wallet balance, channel, and transaction data from the connected LND node.
Also provides payment operations: create invoices, send payments, and estimate fees.
Requires USE_LND=true in configuration.
"""

import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.deps import get_current_user, get_current_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.services.lnd_service import lnd_service
from app.services.mempool_fee_service import mempool_fee_service

logger = logging.getLogger(__name__)

router = APIRouter()


def require_lnd():
    """Dependency that checks if LND is enabled."""
    if not settings.use_lnd:
        raise HTTPException(
            status_code=404,
            detail="Bitcoin wallet (LND) is not enabled. Set USE_LND=true in configuration."
        )


@router.get("/wallet/config")
async def get_wallet_config(
    _user=Depends(get_current_user),
):
    """Get wallet configuration status (does not require LND to be enabled).
    
    Non-admin users receive a reduced view (no max_payment_sats or
    connection details) to limit information disclosure (GAP-6).
    """
    base = {
        "enabled": settings.use_lnd,
        "mempool_url": settings.lnd_mempool_url.rstrip("/"),
    }
    if _user.role == "admin":
        base.update({
            "rest_url_configured": bool(settings.lnd_rest_url and settings.lnd_rest_url != "https://host.docker.internal:8080"),
            "macaroon_configured": bool(settings.lnd_macaroon_hex.get_secret_value()),
            "max_payment_sats": settings.lnd_max_payment_sats,
        })
    return base


@router.get("/wallet/fees")
async def get_recommended_fees(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get recommended fee rates from the configured Mempool Explorer.

    Returns current fee estimates in sat/vByte with priority labels.
    """
    fees = await mempool_fee_service.get_recommended_fees()
    if not fees:
        # Return a response indicating fees are unavailable rather than blocking the page
        return {
            "priorities": None,
            "economy": None,
            "minimum": None,
            "raw": None,
            "mempool_url": settings.lnd_mempool_url.rstrip("/"),
            "unavailable": True,
            "message": "Unable to fetch fee estimates from Mempool Explorer. Check that the URL is reachable.",
        }
    return {
        "priorities": {
            "low": {
                "label": "Low (~1 hour)",
                "sat_per_vbyte": fees.get("hourFee"),
            },
            "medium": {
                "label": "Medium (~30 min)",
                "sat_per_vbyte": fees.get("halfHourFee"),
            },
            "high": {
                "label": "High (next block)",
                "sat_per_vbyte": fees.get("fastestFee"),
            },
        },
        "economy": fees.get("economyFee"),
        "minimum": fees.get("minimumFee"),
        "raw": fees,
        "mempool_url": settings.lnd_mempool_url.rstrip("/"),
    }


@router.get("/wallet/summary")
async def get_wallet_summary(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get combined wallet summary for dashboard widget.
    
    Returns on-chain balance, lightning balance, node info, and totals.
    """
    summary = await lnd_service.get_wallet_summary()
    if not summary:
        raise HTTPException(
            status_code=503,
            detail="Unable to connect to LND node. Check your LND configuration."
        )
    return summary


@router.get("/wallet/info")
async def get_node_info(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get LND node info (alias, pubkey, sync status)."""
    info = await lnd_service.get_info()
    if not info:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return info


@router.get("/wallet/balance")
async def get_balance(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get combined on-chain and lightning balance."""
    import asyncio
    wallet, channel = await asyncio.gather(
        lnd_service.get_wallet_balance(),
        lnd_service.get_channel_balance(),
    )
    
    if wallet is None and channel is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    
    return {
        "onchain": wallet,
        "lightning": channel,
    }


@router.get("/wallet/channels")
async def get_channels(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get list of open lightning channels."""
    channels = await lnd_service.get_channels()
    if channels is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return {"channels": channels}


@router.get("/wallet/channels/pending")
async def get_pending_channels(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get pending channels (opening, closing, force-closing)."""
    pending = await lnd_service.get_pending_channels()
    if pending is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return pending


@router.get("/wallet/payments")
async def get_payments(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    limit: int = 20,
):
    """Get recent outgoing lightning payments."""
    payments = await lnd_service.get_recent_payments(max_payments=min(limit, 100))
    if payments is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return {"payments": payments}


@router.get("/wallet/invoices")
async def get_invoices(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    limit: int = 20,
):
    """Get recent incoming lightning invoices."""
    invoices = await lnd_service.get_recent_invoices(num_max_invoices=min(limit, 100))
    if invoices is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return {"invoices": invoices}


@router.get("/wallet/transactions")
async def get_transactions(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    limit: int = 20,
):
    """Get recent on-chain transactions."""
    txns = await lnd_service.get_onchain_transactions(max_txns=min(limit, 100))
    if txns is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return {"transactions": txns}


# ──────────────────────────────────────────────────────────────────────
# Payment operations (Phase 2)
# ──────────────────────────────────────────────────────────────────────

class NewAddressRequest(BaseModel):
    address_type: str = Field("p2tr", description="Address type: p2wkh (native segwit), np2wkh (nested segwit), p2tr (taproot)")


class CreateInvoiceRequest(BaseModel):
    amount_sats: int = Field(..., ge=0, description="Invoice amount in sats (0 = any-amount)")
    memo: str = Field("", max_length=256, description="Description on the invoice")
    expiry: int = Field(3600, ge=60, le=86400, description="Seconds until expiry")


class DecodePaymentRequest(BaseModel):
    payment_request: str = Field(..., min_length=1, description="BOLT11 payment request string")


class SendPaymentRequest(BaseModel):
    payment_request: str = Field(..., min_length=1, description="BOLT11 payment request to pay")
    fee_limit_sats: Optional[int] = Field(None, ge=0, description="Max routing fee in sats")
    timeout_seconds: int = Field(60, ge=5, le=300, description="Payment timeout")


class SendCoinsRequest(BaseModel):
    address: str = Field(..., min_length=1, description="Bitcoin address to send to")
    amount_sats: int = Field(..., gt=0, description="Amount in satoshis")
    sat_per_vbyte: Optional[int] = Field(None, ge=1, description="Fee rate (None = automatic)")
    label: str = Field("", max_length=256, description="Optional label")

    # SA3-L4: Validate Bitcoin address format (same rules as cold_storage)
    @field_validator("address")
    @classmethod
    def validate_bitcoin_address(cls, v: str) -> str:
        """Validate Bitcoin address format (mainnet only)."""
        if re.match(r"^bc1[a-zA-HJ-NP-Z0-9]{25,87}$", v):  # Bech32/Bech32m
            return v
        if re.match(r"^1[a-km-zA-HJ-NP-Z1-9]{25,34}$", v):  # P2PKH
            return v
        if re.match(r"^3[a-km-zA-HJ-NP-Z1-9]{25,34}$", v):  # P2SH
            return v
        raise ValueError(
            "Invalid Bitcoin address. Must be a mainnet address starting with bc1, 1, or 3."
        )


class FeeEstimateRequest(BaseModel):
    address: str = Field(..., min_length=1, description="Target Bitcoin address")
    amount_sats: int = Field(..., gt=0, description="Amount in satoshis")
    target_conf: int = Field(6, ge=1, le=144, description="Target confirmations")


class UpdateMaxPaymentRequest(BaseModel):
    max_payment_sats: int = Field(..., ge=-1, description="Max sats per single payment (-1 = no limit, 0 = all require approval)")


_SAFETY_LIMIT_REDIS_KEY = "wallet:safety_limit:max_payment_sats"


def _get_safety_redis():
    """Get Redis client for safety-limit persistence (DB 5, shared with action anti-replay)."""
    import os
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        return None
    try:
        import redis as _redis_mod
        base = redis_url.rsplit("/", 1)[0]
        client = _redis_mod.Redis.from_url(
            f"{base}/5", decode_responses=True, socket_connect_timeout=2,
        )
        client.ping()
        return client
    except Exception:
        return None


@router.get("/wallet/safety-limit")
async def get_safety_limit(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get current per-transaction safety limit.
    
    Reads from Redis first (cross-process), then falls back to in-memory.
    """
    r = _get_safety_redis()
    if r:
        try:
            val = r.get(_SAFETY_LIMIT_REDIS_KEY)
            if val is not None:
                return {"max_payment_sats": int(val)}
        except Exception:
            pass
    return {"max_payment_sats": settings.lnd_max_payment_sats}


@router.put("/wallet/safety-limit")
@limiter.limit("5/minute")
async def update_safety_limit(
    request: Request,
    req: UpdateMaxPaymentRequest,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
):
    """Update per-transaction safety limit.
    
    Persists to Redis for cross-process consistency (GAP-15).
    Falls back to in-memory only when Redis is unavailable.
    """
    import logging
    audit_logger = logging.getLogger("bitcoin.audit")
    old_value = settings.lnd_max_payment_sats
    settings.lnd_max_payment_sats = req.max_payment_sats
    
    # Persist to Redis for cross-process visibility
    r = _get_safety_redis()
    if r:
        try:
            r.set(_SAFETY_LIMIT_REDIS_KEY, str(req.max_payment_sats))
        except Exception as exc:
            audit_logger.warning("Failed to persist safety limit to Redis: %s", exc)
    
    audit_logger.warning(
        "SAFETY LIMIT CHANGED: %d → %d sats by user=%s",
        old_value, req.max_payment_sats, _user.id,
    )
    return {"max_payment_sats": settings.lnd_max_payment_sats}


# ---------------------------------------------------------------------------
# Velocity circuit breaker endpoints
# ---------------------------------------------------------------------------

@router.get("/wallet/velocity-breaker")
async def get_velocity_breaker_status(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
):
    """Get the current velocity circuit breaker status.

    Returns whether the breaker is tripped, when it was tripped,
    the context that triggered it, and the current configuration.
    """
    from app.services.bitcoin_budget_service import BitcoinBudgetService
    svc = BitcoinBudgetService(db)
    return await svc.get_velocity_breaker_status()


@router.post("/wallet/velocity-breaker/reset")
@limiter.limit("3/minute")
async def reset_velocity_breaker(
    request: Request,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
    db: AsyncSession = Depends(get_db),
):
    """Reset the velocity circuit breaker after reviewing recent transactions.

    Admin-only. When the breaker is tripped, ALL agent payments are blocked.
    A human must review the recent transaction burst and explicitly reset
    the breaker to re-enable agent payments.
    """
    import logging
    from app.services.bitcoin_budget_service import BitcoinBudgetService

    svc = BitcoinBudgetService(db)
    breaker = await svc.reset_velocity_breaker(_user.id)
    await db.commit()

    audit_logger = logging.getLogger("bitcoin.audit")
    audit_logger.warning(
        "VELOCITY BREAKER RESET by user=%s", _user.id,
    )

    return {
        "is_tripped": breaker.is_tripped,
        "reset_at": str(breaker.reset_at) if breaker.reset_at else None,
        "reset_by_user_id": str(breaker.reset_by_user_id) if breaker.reset_by_user_id else None,
    }


def _check_max_payment(amount_sats: int) -> None:
    """Enforce global max payment safety limit."""
    max_sats = settings.lnd_max_payment_sats
    if max_sats == 0:
        raise HTTPException(
            status_code=400,
            detail="All transactions require approval (safety limit set to 0). "
                   "Use the Budget page to approve spend requests."
        )
    if max_sats > 0 and amount_sats > max_sats:
        raise HTTPException(
            status_code=400,
            detail=f"Amount {amount_sats} sats exceeds global safety limit of {max_sats} sats. "
                   f"Adjust LND_MAX_PAYMENT_SATS to increase."
        )


@router.post("/wallet/address/new")
@limiter.limit("10/minute")
async def new_address(
    request: Request,
    req: NewAddressRequest,
    _user=Depends(get_current_admin),  # RT-35: admin-only
    _lnd=Depends(require_lnd),
):
    """Generate a new on-chain receive address.

    Returns a fresh Bitcoin address for receiving funds to the wallet.
    Address types: p2tr (taproot, default), p2wkh (native segwit), np2wkh (nested segwit).
    """
    if req.address_type not in ("p2wkh", "np2wkh", "p2tr"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid address type '{req.address_type}'. Must be p2wkh, np2wkh, or p2tr.",
        )
    data, error = await lnd_service.new_address(address_type=req.address_type)
    if error:
        logger.error("LND new_address error: %s", error)
        raise HTTPException(status_code=502, detail="Failed to generate address")
    return data


@router.post("/wallet/invoices/create")
@limiter.limit("10/minute")
async def create_invoice(
    request: Request,
    req: CreateInvoiceRequest,
    _user=Depends(get_current_admin),  # RT-35: admin-only
    _lnd=Depends(require_lnd),
):
    """Create a Lightning invoice (BOLT11 payment request)."""
    data, error = await lnd_service.create_invoice(
        amount_sats=req.amount_sats,
        memo=req.memo,
        expiry=req.expiry,
    )
    if error:
        logger.error("LND create_invoice error: %s", error)
        raise HTTPException(status_code=502, detail="Failed to create invoice")
    return data


@router.post("/wallet/decode")
async def decode_invoice(
    req: DecodePaymentRequest,
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Decode a BOLT11 Lightning payment request."""
    data, error = await lnd_service.decode_payment_request(req.payment_request)
    if error:
        logger.error("LND decode error: %s", error)
        raise HTTPException(status_code=502, detail="Failed to decode payment request")
    return data


@router.post("/wallet/payments/send")
@limiter.limit("5/minute")
async def send_payment(
    request: Request,
    req: SendPaymentRequest,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
):
    """Pay a Lightning invoice.
    
    Admin-only. The global safety limit (LND_MAX_PAYMENT_SATS) is enforced
    as a sanity check.  Fine-grained budget controls are in bitcoin_budget_service
    for agent tool calls.
    """
    # Decode invoice to get amount for safety check
    decoded, decode_error = await lnd_service.decode_payment_request(req.payment_request)
    if decode_error:
        raise HTTPException(status_code=400, detail=f"Cannot decode invoice: {decode_error}")
    amount_sats = int(decoded.get("num_satoshis", 0) or 0)
    if amount_sats > 0:
        _check_max_payment(amount_sats)

    data, error = await lnd_service.send_payment_sync(
        payment_request=req.payment_request,
        fee_limit_sats=req.fee_limit_sats,
        timeout_seconds=req.timeout_seconds,
    )
    if error:
        raise HTTPException(status_code=502, detail=f"Payment failed: {error}")
    return data


@router.post("/wallet/send")
@limiter.limit("3/minute")
async def send_onchain(
    request: Request,
    req: SendCoinsRequest,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
):
    """Send on-chain Bitcoin to an address.
    
    Admin-only. The global safety limit (LND_MAX_PAYMENT_SATS) is enforced
    as a sanity check.
    """
    _check_max_payment(req.amount_sats)

    data, error = await lnd_service.send_coins(
        address=req.address,
        amount_sats=req.amount_sats,
        sat_per_vbyte=req.sat_per_vbyte,
        label=req.label,
    )
    if error:
        raise HTTPException(status_code=502, detail=f"Send failed: {error}")
    return data


@router.get("/wallet/fee-estimate")
async def get_fee_estimate(
    address: str,
    amount_sats: int,
    target_conf: int = 6,
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Estimate on-chain transaction fee."""
    data, error = await lnd_service.estimate_fee(
        address=address,
        amount_sats=amount_sats,
        target_conf=target_conf,
    )
    if error:
        raise HTTPException(status_code=502, detail=f"Fee estimate failed: {error}")
    return data


@router.get("/wallet/invoice/{r_hash_hex}")
async def lookup_invoice(
    r_hash_hex: str,
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Look up a specific invoice by payment hash."""
    data, error = await lnd_service.lookup_invoice(r_hash_hex)
    if error:
        raise HTTPException(status_code=502, detail=f"Invoice lookup failed: {error}")
    return data


# ──────────────────────────────────────────────────────────────────────
# Channel management
# ──────────────────────────────────────────────────────────────────────

class OpenChannelRequest(BaseModel):
    node_address: str = Field(
        ...,
        min_length=1,
        description="Node address in pubkey@host:port format",
    )
    local_funding_amount: int = Field(
        ...,
        gt=0,
        description="Channel capacity in sats (our side funds it)",
    )
    sat_per_vbyte: Optional[int] = Field(
        None,
        ge=1,
        description="Fee rate for the funding tx (None = LND default)",
    )


def _parse_node_address(node_address: str) -> tuple[str, str]:
    """Parse 'pubkey@host:port' into (pubkey, host_with_port).

    Supports IPv4, IPv6 (bracketed), and .onion addresses.
    Raises HTTPException on invalid format.
    """
    if "@" not in node_address:
        raise HTTPException(
            status_code=400,
            detail="Invalid node address format. Expected pubkey@host:port",
        )
    pubkey, host = node_address.split("@", 1)

    # Validate pubkey is 66 hex chars
    if len(pubkey) != 66:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pubkey length ({len(pubkey)}). Expected 66 hex characters.",
        )
    try:
        bytes.fromhex(pubkey)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid pubkey — must be hexadecimal.",
        )

    if not host:
        raise HTTPException(
            status_code=400,
            detail="Missing host:port after @",
        )

    return pubkey, host


@router.post("/wallet/channels/open")
@limiter.limit("2/minute")
async def open_channel(
    request: Request,
    req: OpenChannelRequest,
    _user=Depends(get_current_admin),
    _lnd=Depends(require_lnd),
):
    """Open a new Lightning channel.

    Steps:
    1. Parse and validate the node address
    2. Connect to the peer (idempotent)
    3. Open the channel with the specified funding amount

    Returns the funding transaction ID that can be tracked on-chain.
    """
    import logging
    audit_logger = logging.getLogger("bitcoin.audit")

    pubkey, host = _parse_node_address(req.node_address)

    # Step 1: Connect to peer
    _, peer_error = await lnd_service.connect_peer(pubkey, host)
    if peer_error:
        logger.error("LND connect_peer error: %s", peer_error)
        raise HTTPException(
            status_code=502,
            detail="Failed to connect to peer",
        )

    # Step 2: Open channel
    data, error = await lnd_service.open_channel(
        node_pubkey_hex=pubkey,
        local_funding_amount=req.local_funding_amount,
        sat_per_vbyte=req.sat_per_vbyte,
    )
    if error:
        raise HTTPException(
            status_code=502,
            detail=f"{error}",
        )

    audit_logger.warning(
        "CHANNEL OPENED: %d sats to %s by user=%s, funding_txid=%s",
        req.local_funding_amount,
        pubkey[:16] + "...",
        _user.id,
        data.get("funding_txid", "?"),
    )

    return data


@router.get("/wallet/channels/pending/detail")
async def get_pending_channels_detail(
    _user=Depends(get_current_user),
    _lnd=Depends(require_lnd),
):
    """Get detailed pending channel information.

    Returns individual pending channels with capacity, peer, and status
    (pending_open, pending_close, force_closing).
    """
    channels = await lnd_service.get_pending_channels_detail()
    if channels is None:
        raise HTTPException(status_code=503, detail="Unable to connect to LND node.")
    return {"pending_channels": channels}
