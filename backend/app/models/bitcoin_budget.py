"""
Bitcoin Budget Models — spend tracking, approval workflow, and transaction ledger.

Provides:
- BitcoinTransaction: Immutable ledger of all Bitcoin transactions (Lightning + on-chain)
- BitcoinSpendApproval: Approval requests for out-of-budget or over-limit spends
- SpendApprovalStatus / SpendTrigger / TransactionType / TransactionStatus enums
"""
from datetime import datetime, timedelta
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4
import enum

from sqlalchemy import (
    String, Boolean, DateTime, Enum, Integer, Float, Text,
    Index, ForeignKey, BigInteger, CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TransactionType(str, enum.Enum):
    """Type of Bitcoin transaction."""
    LIGHTNING_SEND = "lightning_send"      # Outgoing Lightning payment
    LIGHTNING_RECEIVE = "lightning_receive"  # Incoming Lightning invoice
    ONCHAIN_SEND = "onchain_send"          # Outgoing on-chain tx
    ONCHAIN_RECEIVE = "onchain_receive"    # Incoming on-chain tx


class TransactionStatus(str, enum.Enum):
    """Status of a tracked Bitcoin transaction."""
    PENDING = "pending"        # Submitted, awaiting confirmation/settlement
    CONFIRMED = "confirmed"    # Settled/confirmed
    FAILED = "failed"          # Payment failed, tx dropped, etc.
    EXPIRED = "expired"        # Invoice expired before payment


class SpendApprovalStatus(str, enum.Enum):
    """Status of a Bitcoin spend approval request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class SpendTrigger(str, enum.Enum):
    """Why this spend requires approval."""
    NO_BUDGET = "no_budget"              # Campaign has no Bitcoin budget set
    OVER_BUDGET = "over_budget"          # Spend would exceed campaign budget
    GLOBAL_LIMIT = "global_limit"        # Exceeds LND_MAX_PAYMENT_SATS
    MANUAL_REVIEW = "manual_review"      # Flagged for manual review by config/rule
    VELOCITY_BREAKER = "velocity_breaker"  # Too many txns in short window — breaker tripped


# ---------------------------------------------------------------------------
# BitcoinTransaction — immutable transaction ledger
# ---------------------------------------------------------------------------

class BitcoinTransaction(Base):
    """
    Immutable record of every Bitcoin transaction (Lightning or on-chain).

    Acts as the single source of truth for bitcoin_budget_spent on campaigns.
    Only rows with status=CONFIRMED count toward budget consumption.
    """
    __tablename__ = "bitcoin_transactions"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Link to campaign (nullable for wallet-level transactions)
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Who initiated (user or agent — tracked by user_id)
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Transaction classification
    tx_type: Mapped[TransactionType] = mapped_column(
        Enum(
            TransactionType,
            name="bitcoin_tx_type",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            name="bitcoin_tx_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=TransactionStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Amounts (always in satoshis)
    amount_sats: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_sats: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # LND identifiers (one of these will be set depending on tx type)
    payment_hash: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )  # Lightning payment hash (hex)
    payment_request: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # BOLT11 invoice string
    txid: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )  # On-chain transaction ID
    address: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )  # On-chain destination address

    # Context
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Memo / label / reason
    agent_tool_execution_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )  # If triggered by an agent tool

    # Approval link (if this spend required approval)
    approval_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bitcoin_spend_approvals.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    campaign: Mapped[Optional["Campaign"]] = relationship(
        "Campaign", foreign_keys=[campaign_id]
    )
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
    approval: Mapped[Optional["BitcoinSpendApproval"]] = relationship(
        "BitcoinSpendApproval", foreign_keys=[approval_id], back_populates="transaction"
    )

    __table_args__ = (
        Index("idx_btctx_campaign_status", "campaign_id", "status"),
        Index("idx_btctx_created_at", "created_at"),
        CheckConstraint("amount_sats >= 0", name="ck_btctx_amount_positive"),
        CheckConstraint("fee_sats >= 0", name="ck_btctx_fee_positive"),
    )

    def __repr__(self) -> str:
        return (
            f"<BitcoinTransaction {self.id} "
            f"type={self.tx_type} amount={self.amount_sats} "
            f"status={self.status}>"
        )


# ---------------------------------------------------------------------------
# BitcoinSpendApproval — manual approval for out-of-budget spends
# ---------------------------------------------------------------------------

class BitcoinSpendApproval(Base):
    """
    Approval request triggered when a Bitcoin spend exceeds budget or limits.

    Similar in spirit to ToolApprovalRequest, but specifically for Bitcoin
    payments. Includes spend-specific context (invoice details, budget
    remaining, etc.) and links to the Spend Advisor chat.
    """
    __tablename__ = "bitcoin_spend_approvals"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid4
    )

    # Context: which campaign and who initiated
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requested_by_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Why this was flagged
    trigger: Mapped[SpendTrigger] = mapped_column(
        Enum(
            SpendTrigger,
            name="spend_trigger",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
    )

    # Status
    status: Mapped[SpendApprovalStatus] = mapped_column(
        Enum(
            SpendApprovalStatus,
            name="spend_approval_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        default=SpendApprovalStatus.PENDING,
        nullable=False,
        index=True,
    )

    # Spend details
    amount_sats: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_estimate_sats: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    payment_request: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    destination_address: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)  # Why the spend

    # Budget snapshot at time of request
    budget_context: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    # Example: {
    #   "campaign_budget_sats": 500000,
    #   "campaign_spent_sats": 450000,
    #   "campaign_remaining_sats": 50000,
    #   "global_limit_sats": 1000000,
    #   "decoded_invoice": { ... }
    # }

    # Reviewer decision
    reviewed_by_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Spend Advisor chat context
    advisor_conversation_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    campaign: Mapped[Optional["Campaign"]] = relationship(
        "Campaign", foreign_keys=[campaign_id]
    )
    requested_by: Mapped["User"] = relationship(
        "User", foreign_keys=[requested_by_id]
    )
    reviewed_by: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[reviewed_by_id]
    )
    advisor_conversation: Mapped[Optional["Conversation"]] = relationship(
        "Conversation", foreign_keys=[advisor_conversation_id]
    )
    transaction: Mapped[Optional["BitcoinTransaction"]] = relationship(
        "BitcoinTransaction", back_populates="approval", uselist=False
    )

    __table_args__ = (
        Index("idx_spend_approval_status", "status"),
        Index("idx_spend_approval_created_at", "created_at"),
        Index("idx_spend_approval_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<BitcoinSpendApproval {self.id} "
            f"trigger={self.trigger} amount={self.amount_sats} "
            f"status={self.status}>"
        )

    def is_expired(self) -> bool:
        """Check if this approval has expired."""
        if self.expires_at is None:
            return False
        return utc_now() > ensure_utc(self.expires_at)

    def can_be_reviewed(self) -> bool:
        """Check if this approval can still be reviewed."""
        return self.status == SpendApprovalStatus.PENDING and not self.is_expired()


# ---------------------------------------------------------------------------
# BitcoinVelocityBreaker — global circuit breaker for rapid-fire txns
# ---------------------------------------------------------------------------

class BitcoinVelocityBreaker(Base):
    """
    Persistent circuit breaker that trips when agent sends exceed a
    transaction-count threshold within a rolling window.

    This is a *singleton* table — exactly one row with id=1.
    When ``tripped_at`` is non-NULL the breaker is engaged and
    ALL agent sends are blocked until a human explicitly resets it
    via the wallet API.

    The breaker does NOT auto-reset when the window expires — the
    attacker's strategy is to wait, so a human must acknowledge
    the anomalous pattern.
    """
    __tablename__ = "bitcoin_velocity_breaker"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)

    # NULL = breaker is open (normal), non-NULL = breaker is tripped
    tripped_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tripped_by_tx_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    trip_context: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # {"count": N, "window_seconds": W, "recent_tx_ids": [...]}

    # Last reset
    reset_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reset_by_user_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )

    def __repr__(self) -> str:
        state = "TRIPPED" if self.tripped_at else "OK"
        return f"<BitcoinVelocityBreaker {state}>"

    @property
    def is_tripped(self) -> bool:
        return self.tripped_at is not None
