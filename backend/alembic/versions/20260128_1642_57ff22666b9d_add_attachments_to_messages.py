"""add_attachments_to_messages

Revision ID: 57ff22666b9d
Revises: 58eeb43d02d5
Create Date: 2026-01-28 16:42:34.811218

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '57ff22666b9d'
down_revision = '58eeb43d02d5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add attachments column to messages table
    op.add_column('messages', 
        sa.Column('attachments', postgresql.JSONB, nullable=True, server_default='[]')
    )
    
    # Add index for querying messages with attachments
    op.execute("""
        CREATE INDEX idx_messages_has_attachments 
        ON messages ((jsonb_array_length(attachments) > 0))
        WHERE jsonb_array_length(attachments) > 0
    """)


def downgrade() -> None:
    # Drop index first
    op.drop_index('idx_messages_has_attachments', table_name='messages')
    
    # Drop attachments column
    op.drop_column('messages', 'attachments')
