"""Add nostr_identities table

Creates the nostr_identities table for managing encrypted Nostr
keypairs and profile metadata used by the agent Nostr tool.

Revision ID: n7o8p9q0r1s2
Revises: n6o7p8q9r0s1
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers
revision = "n7o8p9q0r1s2"
down_revision = "n6o7p8q9r0s1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nostr_identities",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("campaign_id", UUID(as_uuid=True), sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pubkey_hex", sa.String(64), unique=True, nullable=False),
        sa.Column("npub", sa.String(70), nullable=False),
        sa.Column("encrypted_nsec", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("about", sa.Text(), nullable=True),
        sa.Column("picture_url", sa.String(500), nullable=True),
        sa.Column("nip05", sa.String(200), nullable=True),
        sa.Column("lud16", sa.String(200), nullable=True),
        sa.Column("relay_urls", JSONB(), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("following_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("post_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_zaps_received_sats", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index("ix_nostr_identities_user_id", "nostr_identities", ["user_id"])
    op.create_index("ix_nostr_identities_campaign_id", "nostr_identities", ["campaign_id"])
    op.create_index("idx_nostr_identities_active", "nostr_identities", ["is_active"])


def downgrade() -> None:
    op.drop_index("idx_nostr_identities_active", table_name="nostr_identities")
    op.drop_index("ix_nostr_identities_campaign_id", table_name="nostr_identities")
    op.drop_index("ix_nostr_identities_user_id", table_name="nostr_identities")
    op.drop_table("nostr_identities")
