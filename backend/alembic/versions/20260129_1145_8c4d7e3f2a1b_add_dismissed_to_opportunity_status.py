"""add_dismissed_to_opportunity_status

Revision ID: 8c4d7e3f2a1b
Revises: fcf067eaa4ce
Create Date: 2026-01-29 11:45:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '8c4d7e3f2a1b'
down_revision = 'fcf067eaa4ce'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add 'dismissed' value to opportunity_status enum
    op.execute("ALTER TYPE opportunity_status ADD VALUE IF NOT EXISTS 'dismissed'")
    
    # Add missing values to proposal_status enum
    op.execute("ALTER TYPE proposal_status ADD VALUE IF NOT EXISTS 'proposed'")
    op.execute("ALTER TYPE proposal_status ADD VALUE IF NOT EXISTS 'submitted'")


def downgrade() -> None:
    # Note: PostgreSQL doesn't support removing enum values easily
    # This would require recreating the type and updating all columns
    # For now, we'll leave the values in place during downgrade
    pass
