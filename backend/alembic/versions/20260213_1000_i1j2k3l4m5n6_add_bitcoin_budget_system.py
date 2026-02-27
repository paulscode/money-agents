"""Add Bitcoin budget system tables and fields

Creates:
- bitcoin_spend_approvals table   (approval requests for out-of-budget spends)
- bitcoin_transactions table      (immutable ledger of all BTC movements)
- proposals.bitcoin_budget_sats   (nullable BigInteger column)
- campaigns.bitcoin_budget_sats   (nullable BigInteger column)
- campaigns.bitcoin_spent_sats    (BigInteger, default 0)
- campaigns.bitcoin_received_sats (BigInteger, default 0)

Revision ID: i1j2k3l4m5n6
Revises: h0i1j2k3l4m5
Create Date: 2025-02-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "i1j2k3l4m5n6"
down_revision = "h0i1j2k3l4m5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create enum types
    # ------------------------------------------------------------------
    bitcoin_tx_type = postgresql.ENUM(
        "lightning_send", "lightning_receive", "onchain_send", "onchain_receive",
        name="bitcoin_tx_type", create_type=False,
    )
    bitcoin_tx_type.create(op.get_bind(), checkfirst=True)

    bitcoin_tx_status = postgresql.ENUM(
        "pending", "confirmed", "failed", "expired",
        name="bitcoin_tx_status", create_type=False,
    )
    bitcoin_tx_status.create(op.get_bind(), checkfirst=True)

    spend_approval_status = postgresql.ENUM(
        "pending", "approved", "rejected", "expired", "cancelled",
        name="spend_approval_status", create_type=False,
    )
    spend_approval_status.create(op.get_bind(), checkfirst=True)

    spend_trigger = postgresql.ENUM(
        "no_budget", "over_budget", "global_limit", "manual_review",
        name="spend_trigger", create_type=False,
    )
    spend_trigger.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. bitcoin_spend_approvals table (must come first — referenced by FK)
    # ------------------------------------------------------------------
    op.create_table(
        "bitcoin_spend_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Context
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Trigger & status
        sa.Column("trigger", spend_trigger, nullable=False),
        sa.Column("status", spend_approval_status, nullable=False,
                  server_default="pending"),
        # Spend details
        sa.Column("amount_sats", sa.BigInteger, nullable=False),
        sa.Column("fee_estimate_sats", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("payment_request", sa.Text, nullable=True),
        sa.Column("destination_address", sa.String(128), nullable=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("budget_context", postgresql.JSONB, nullable=False, server_default="{}"),
        # Reviewer
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        # Spend Advisor chat
        sa.Column("advisor_conversation_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_spend_approval_campaign", "bitcoin_spend_approvals", ["campaign_id"])
    op.create_index("idx_spend_approval_requested_by", "bitcoin_spend_approvals", ["requested_by_id"])
    op.create_index("idx_spend_approval_status", "bitcoin_spend_approvals", ["status"])
    op.create_index("idx_spend_approval_created_at", "bitcoin_spend_approvals", ["created_at"])
    op.create_index("idx_spend_approval_expires_at", "bitcoin_spend_approvals", ["expires_at"])

    # ------------------------------------------------------------------
    # 3. bitcoin_transactions table
    # ------------------------------------------------------------------
    op.create_table(
        "bitcoin_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # Links
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        # Classification
        sa.Column("tx_type", bitcoin_tx_type, nullable=False),
        sa.Column("status", bitcoin_tx_status, nullable=False, server_default="pending"),
        # Amounts (sats)
        sa.Column("amount_sats", sa.BigInteger, nullable=False),
        sa.Column("fee_sats", sa.BigInteger, nullable=False, server_default="0"),
        # LND identifiers
        sa.Column("payment_hash", sa.String(128), nullable=True),
        sa.Column("payment_request", sa.Text, nullable=True),
        sa.Column("txid", sa.String(128), nullable=True),
        sa.Column("address", sa.String(128), nullable=True),
        # Context
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("agent_tool_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("bitcoin_spend_approvals.id", ondelete="SET NULL"), nullable=True),
        # Timestamps
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now()),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        # Check constraints
        sa.CheckConstraint("amount_sats >= 0", name="ck_btctx_amount_positive"),
        sa.CheckConstraint("fee_sats >= 0", name="ck_btctx_fee_positive"),
    )
    op.create_index("idx_btctx_campaign_id", "bitcoin_transactions", ["campaign_id"])
    op.create_index("idx_btctx_user_id", "bitcoin_transactions", ["user_id"])
    op.create_index("idx_btctx_tx_type", "bitcoin_transactions", ["tx_type"])
    op.create_index("idx_btctx_status", "bitcoin_transactions", ["status"])
    op.create_index("idx_btctx_payment_hash", "bitcoin_transactions", ["payment_hash"])
    op.create_index("idx_btctx_txid", "bitcoin_transactions", ["txid"])
    op.create_index("idx_btctx_campaign_status", "bitcoin_transactions", ["campaign_id", "status"])
    op.create_index("idx_btctx_created_at", "bitcoin_transactions", ["created_at"])

    # ------------------------------------------------------------------
    # 4. Add bitcoin budget columns to existing tables
    # ------------------------------------------------------------------
    op.add_column(
        "proposals",
        sa.Column("bitcoin_budget_sats", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("bitcoin_budget_sats", sa.BigInteger, nullable=True),
    )
    op.add_column(
        "campaigns",
        sa.Column("bitcoin_spent_sats", sa.BigInteger, nullable=False, server_default="0"),
    )
    op.add_column(
        "campaigns",
        sa.Column("bitcoin_received_sats", sa.BigInteger, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    # Remove columns from existing tables
    op.drop_column("campaigns", "bitcoin_received_sats")
    op.drop_column("campaigns", "bitcoin_spent_sats")
    op.drop_column("campaigns", "bitcoin_budget_sats")
    op.drop_column("proposals", "bitcoin_budget_sats")

    # Drop indexes and tables
    op.drop_index("idx_btctx_created_at", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_campaign_status", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_txid", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_payment_hash", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_status", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_tx_type", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_user_id", table_name="bitcoin_transactions")
    op.drop_index("idx_btctx_campaign_id", table_name="bitcoin_transactions")
    op.drop_table("bitcoin_transactions")

    op.drop_index("idx_spend_approval_expires_at", table_name="bitcoin_spend_approvals")
    op.drop_index("idx_spend_approval_created_at", table_name="bitcoin_spend_approvals")
    op.drop_index("idx_spend_approval_status", table_name="bitcoin_spend_approvals")
    op.drop_index("idx_spend_approval_requested_by", table_name="bitcoin_spend_approvals")
    op.drop_index("idx_spend_approval_campaign", table_name="bitcoin_spend_approvals")
    op.drop_table("bitcoin_spend_approvals")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS spend_trigger")
    op.execute("DROP TYPE IF EXISTS spend_approval_status")
    op.execute("DROP TYPE IF EXISTS bitcoin_tx_status")
    op.execute("DROP TYPE IF EXISTS bitcoin_tx_type")
