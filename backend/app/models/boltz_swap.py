"""
Boltz Swap Models — Reverse Submarine Swap state tracking for cold storage.

Provides:
- BoltzSwap: Persistent record of every Boltz reverse swap (Lightning → On-chain)
- SwapStatus / BoltzStatus enums for lifecycle tracking
"""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import (
    String, DateTime, Enum, Integer, Text,
    Index, ForeignKey, BigInteger,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SwapStatus(str, enum.Enum):
    """Internal swap lifecycle status."""
    CREATED = "created"                  # Swap created with Boltz, invoice not yet paid
    PAYING_INVOICE = "paying_invoice"    # LND is paying the Boltz hold invoice
    INVOICE_PAID = "invoice_paid"        # Invoice paid, waiting for Boltz lockup
    CLAIMING = "claiming"               # Constructing/signing claim transaction
    CLAIMED = "claimed"                 # Claim tx broadcast, awaiting confirmation
    COMPLETED = "completed"             # Claim tx confirmed, swap fully done
    FAILED = "failed"                   # Swap failed (non-recoverable)
    CANCELLED = "cancelled"             # User cancelled before invoice payment
    REFUNDED = "refunded"               # Boltz refunded after timeout (funds lost)


class BoltzSwapDirection(str, enum.Enum):
    """Direction of Boltz swap."""
    REVERSE = "reverse"  # Lightning → On-chain (cold storage)


# ---------------------------------------------------------------------------
# BoltzSwap — persistent swap state
# ---------------------------------------------------------------------------

class BoltzSwap(Base):
    """
    Persistent record of a Boltz reverse submarine swap.

    Stores all data needed to recover a swap after crash/restart:
    preimage, claim key, swap tree, Boltz responses, and status history.

    Sensitive fields (preimage_hex, claim_private_key_hex) should be
    encrypted before storage using the application secret key.
    """
    __tablename__ = "boltz_swaps"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # -- Boltz identifiers --
    boltz_swap_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    direction: Mapped[BoltzSwapDirection] = mapped_column(
        Enum(
            BoltzSwapDirection,
            name="boltz_swap_direction",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=BoltzSwapDirection.REVERSE,
    )

    # -- User context --
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # -- Swap parameters --
    invoice_amount_sats: Mapped[int] = mapped_column(
        BigInteger, nullable=False,
    )
    onchain_amount_sats: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
    )
    destination_address: Mapped[str] = mapped_column(
        String(256), nullable=False,
    )
    fee_percentage: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True,
    )
    miner_fee_sats: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
    )

    # -- Crypto material (should be encrypted at rest) --
    preimage_hex: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    preimage_hash_hex: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    claim_private_key_hex: Mapped[str] = mapped_column(
        Text, nullable=False,
    )
    claim_public_key_hex: Mapped[str] = mapped_column(
        Text, nullable=False,
    )

    # -- Boltz response data --
    boltz_invoice: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    boltz_lockup_address: Mapped[Optional[str]] = mapped_column(
        String(256), nullable=True,
    )
    boltz_refund_public_key_hex: Mapped[Optional[str]] = mapped_column(
        String(66), nullable=True,
    )
    boltz_swap_tree_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True,
    )
    timeout_block_height: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True,
    )
    boltz_blinding_key: Mapped[Optional[str]] = mapped_column(
        String(66), nullable=True,
    )

    # -- LND payment tracking --
    lnd_payment_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
    )
    lnd_payment_status: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True,
    )

    # -- Claim transaction --
    claim_tx_hex: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    claim_txid: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True,
    )

    # -- Status tracking --
    status: Mapped[SwapStatus] = mapped_column(
        Enum(
            SwapStatus,
            name="boltz_swap_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=SwapStatus.CREATED,
    )
    boltz_status: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True,
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    status_history: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list,
    )

    # -- Recovery tracking --
    recovery_attempted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    recovery_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # -- Timestamps --
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        Index("idx_boltz_swaps_status", "status"),
        Index("idx_boltz_swaps_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return f"<BoltzSwap {self.boltz_swap_id} status={self.status.value}>"
