"""
Nostr Identity Models — Persistent identity storage for Nostr agent tool.

Provides:
- NostrIdentity: Encrypted keypair + profile metadata for each managed Nostr persona
"""
from datetime import datetime
from app.core.datetime_utils import utc_now, ensure_utc
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    String, Boolean, DateTime, Integer, BigInteger, Text,
    Index, ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB

from app.core.database import Base


class NostrIdentity(Base):
    """A managed Nostr identity (keypair + profile) used by agents."""
    __tablename__ = "nostr_identities"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)

    # Ownership
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    campaign_id: Mapped[Optional[UUID]] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    # Nostr identity keys
    pubkey_hex: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    npub: Mapped[str] = mapped_column(String(70), nullable=False)
    encrypted_nsec: Mapped[str] = mapped_column(Text, nullable=False)

    # Profile (cached from kind-0)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    about: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    picture_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    nip05: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    lud16: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Relay configuration
    relay_urls: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=list)

    # Stats (periodically updated)
    follower_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    following_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    post_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_zaps_received_sats: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    last_posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_nostr_identities_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<NostrIdentity {self.npub[:20]}... name={self.display_name}>"
