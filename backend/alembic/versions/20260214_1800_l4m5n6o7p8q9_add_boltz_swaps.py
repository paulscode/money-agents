"""Add boltz_swaps table for cold storage reverse swaps

Creates:
- boltz_swap_direction enum
- boltz_swap_status enum
- boltz_swaps table (persistent swap state for Boltz reverse submarine swaps)

Revision ID: l4m5n6o7p8q9
Revises: k3l4m5n6o7p8
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "l4m5n6o7p8q9"
down_revision = "k3l4m5n6o7p8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create enums
    # ------------------------------------------------------------------
    boltz_swap_direction = postgresql.ENUM(
        "reverse",
        name="boltz_swap_direction",
        create_type=True,
    )
    boltz_swap_direction.create(op.get_bind(), checkfirst=True)

    boltz_swap_status = postgresql.ENUM(
        "created", "paying_invoice", "invoice_paid", "claiming",
        "claimed", "completed", "failed", "cancelled", "refunded",
        name="boltz_swap_status",
        create_type=True,
    )
    boltz_swap_status.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. Create boltz_swaps table
    # ------------------------------------------------------------------
    op.create_table(
        "boltz_swaps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("boltz_swap_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "direction",
            postgresql.ENUM("reverse", name="boltz_swap_direction", create_type=False),
            nullable=False,
            server_default="reverse",
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Swap parameters
        sa.Column("invoice_amount_sats", sa.BigInteger(), nullable=False),
        sa.Column("onchain_amount_sats", sa.BigInteger(), nullable=True),
        sa.Column("destination_address", sa.String(256), nullable=False),
        sa.Column("fee_percentage", sa.String(10), nullable=True),
        sa.Column("miner_fee_sats", sa.BigInteger(), nullable=True),
        # Crypto material
        sa.Column("preimage_hex", sa.String(64), nullable=False),
        sa.Column("preimage_hash_hex", sa.String(64), nullable=False),
        sa.Column("claim_private_key_hex", sa.String(64), nullable=False),
        sa.Column("claim_public_key_hex", sa.String(66), nullable=False),
        # Boltz response
        sa.Column("boltz_invoice", sa.Text(), nullable=True),
        sa.Column("boltz_lockup_address", sa.String(256), nullable=True),
        sa.Column("boltz_refund_public_key_hex", sa.String(66), nullable=True),
        sa.Column("boltz_swap_tree_json", postgresql.JSONB(), nullable=True),
        sa.Column("timeout_block_height", sa.BigInteger(), nullable=True),
        sa.Column("boltz_blinding_key", sa.String(66), nullable=True),
        # LND payment
        sa.Column("lnd_payment_hash", sa.String(64), nullable=True),
        sa.Column("lnd_payment_status", sa.String(20), nullable=True),
        # Claim tx
        sa.Column("claim_tx_hex", sa.Text(), nullable=True),
        sa.Column("claim_txid", sa.String(64), nullable=True),
        # Status
        sa.Column(
            "status",
            postgresql.ENUM(
                "created", "paying_invoice", "invoice_paid", "claiming",
                "claimed", "completed", "failed", "cancelled", "refunded",
                name="boltz_swap_status",
                create_type=False,
            ),
            nullable=False,
            server_default="created",
        ),
        sa.Column("boltz_status", sa.String(40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("status_history", postgresql.JSONB(), nullable=True),
        # Recovery
        sa.Column("recovery_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Indexes
    op.create_index("idx_boltz_swaps_boltz_id", "boltz_swaps", ["boltz_swap_id"], unique=True)
    op.create_index("idx_boltz_swaps_status", "boltz_swaps", ["status"])
    op.create_index("idx_boltz_swaps_user_id", "boltz_swaps", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_boltz_swaps_user_id", table_name="boltz_swaps")
    op.drop_index("idx_boltz_swaps_status", table_name="boltz_swaps")
    op.drop_index("idx_boltz_swaps_boltz_id", table_name="boltz_swaps")
    op.drop_table("boltz_swaps")

    op.execute("DROP TYPE IF EXISTS boltz_swap_status")
    op.execute("DROP TYPE IF EXISTS boltz_swap_direction")
