"""Add max_backlog_size to user_scout_settings

Adds max_backlog_size column (default 200, 0 = disabled).
When unreviewed opportunities >= this limit, the Opportunity Scout
skips its scheduled run to prevent unbounded backlog growth.

Revision ID: m5n6o7p8q9r0
Revises: l4m5n6o7p8q9
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "m5n6o7p8q9r0"
down_revision = "l4m5n6o7p8q9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_scout_settings",
        sa.Column("max_backlog_size", sa.Integer(), nullable=False, server_default="200"),
    )


def downgrade() -> None:
    op.drop_column("user_scout_settings", "max_backlog_size")
