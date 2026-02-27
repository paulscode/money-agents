"""add_lowercase_enum_values

Revision ID: f18e6e3fe95b
Revises: 6cebfae7cf42
Create Date: 2026-01-29 16:19:58.424690

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f18e6e3fe95b'
down_revision = '6cebfae7cf42'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add lowercase values to all enums for SQLAlchemy values_callable compatibility."""
    
    # risk_level
    op.execute("ALTER TYPE risk_level ADD VALUE IF NOT EXISTS 'low'")
    op.execute("ALTER TYPE risk_level ADD VALUE IF NOT EXISTS 'medium'")
    op.execute("ALTER TYPE risk_level ADD VALUE IF NOT EXISTS 'high'")
    
    # campaign_status
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'initializing'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'waiting_for_inputs'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'active'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'paused'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'completed'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'terminated'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'failed'")
    
    # conversation_type
    op.execute("ALTER TYPE conversation_type ADD VALUE IF NOT EXISTS 'proposal'")
    op.execute("ALTER TYPE conversation_type ADD VALUE IF NOT EXISTS 'campaign'")
    op.execute("ALTER TYPE conversation_type ADD VALUE IF NOT EXISTS 'tool'")
    op.execute("ALTER TYPE conversation_type ADD VALUE IF NOT EXISTS 'general'")
    
    # sender_type
    op.execute("ALTER TYPE sender_type ADD VALUE IF NOT EXISTS 'user'")
    op.execute("ALTER TYPE sender_type ADD VALUE IF NOT EXISTS 'agent'")
    op.execute("ALTER TYPE sender_type ADD VALUE IF NOT EXISTS 'system'")


def downgrade() -> None:
    # Cannot remove enum values in PostgreSQL
    pass
