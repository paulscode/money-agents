"""Add velocity circuit breaker table and update spend_trigger enum

Creates:
- bitcoin_velocity_breaker table  (singleton circuit breaker state)
- Adds 'velocity_breaker' value to spend_trigger enum

Revision ID: k3l4m5n6o7p8
Revises: j2k3l4m5n6o7
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "k3l4m5n6o7p8"
down_revision = "j2k3l4m5n6o7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Add 'velocity_breaker' to the spend_trigger enum
    # ------------------------------------------------------------------
    op.execute("ALTER TYPE spend_trigger ADD VALUE IF NOT EXISTS 'velocity_breaker'")

    # ------------------------------------------------------------------
    # 2. Create bitcoin_velocity_breaker table (singleton: one row, id=1)
    # ------------------------------------------------------------------
    op.create_table(
        "bitcoin_velocity_breaker",
        sa.Column("id", sa.Integer(), primary_key=True, default=1),
        sa.Column("tripped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "tripped_by_tx_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("trip_context", postgresql.JSONB(), nullable=True),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "reset_by_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Insert the singleton row
    op.execute(
        "INSERT INTO bitcoin_velocity_breaker (id) VALUES (1) ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("bitcoin_velocity_breaker")
    # Note: PostgreSQL does not support removing values from an enum type.
    # The 'velocity_breaker' value will remain in the enum but will be unused.
