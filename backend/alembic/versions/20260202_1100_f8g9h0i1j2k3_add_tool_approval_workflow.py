"""add tool approval workflow

Revision ID: f8g9h0i1j2k3
Revises: e6f7g8h9i0j1
Create Date: 2026-02-02 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f8g9h0i1j2k3'
down_revision: Union[str, None] = 'e6f7g8h9i0j1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    approval_status = postgresql.ENUM(
        'pending', 'approved', 'rejected', 'expired', 'cancelled',
        name='approval_status',
        create_type=False
    )
    approval_status.create(op.get_bind(), checkfirst=True)
    
    approval_urgency = postgresql.ENUM(
        'low', 'medium', 'high', 'critical',
        name='approval_urgency',
        create_type=False
    )
    approval_urgency.create(op.get_bind(), checkfirst=True)
    
    # Add approval fields to tools table
    op.add_column('tools', sa.Column('requires_approval', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('tools', sa.Column('approval_urgency', sa.String(20), nullable=True))
    op.add_column('tools', sa.Column('approval_instructions', sa.Text(), nullable=True))
    
    # Create tool_approval_requests table
    op.create_table(
        'tool_approval_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tool_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tools.id', ondelete='CASCADE'), nullable=False),
        sa.Column('parameters', postgresql.JSONB(), nullable=False, default=dict),
        sa.Column('requested_by_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('campaigns.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', approval_status, nullable=False, server_default='pending'),
        sa.Column('urgency', approval_urgency, nullable=False, server_default='medium'),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('expected_outcome', sa.Text(), nullable=True),
        sa.Column('risk_assessment', sa.Text(), nullable=True),
        sa.Column('estimated_cost', sa.Float(), nullable=True),
        sa.Column('reviewed_by_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('review_notes', sa.Text(), nullable=True),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tool_executions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    )
    
    # Create indexes
    op.create_index('idx_approval_tool_id', 'tool_approval_requests', ['tool_id'])
    op.create_index('idx_approval_requested_by', 'tool_approval_requests', ['requested_by_id'])
    op.create_index('idx_approval_campaign', 'tool_approval_requests', ['campaign_id'])
    op.create_index('idx_approval_status_urgency', 'tool_approval_requests', ['status', 'urgency'])
    op.create_index('idx_approval_expires_at', 'tool_approval_requests', ['expires_at'])
    op.create_index('idx_approval_created_at', 'tool_approval_requests', ['created_at'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_approval_created_at', table_name='tool_approval_requests')
    op.drop_index('idx_approval_expires_at', table_name='tool_approval_requests')
    op.drop_index('idx_approval_status_urgency', table_name='tool_approval_requests')
    op.drop_index('idx_approval_campaign', table_name='tool_approval_requests')
    op.drop_index('idx_approval_requested_by', table_name='tool_approval_requests')
    op.drop_index('idx_approval_tool_id', table_name='tool_approval_requests')
    
    # Drop table
    op.drop_table('tool_approval_requests')
    
    # Remove columns from tools
    op.drop_column('tools', 'approval_instructions')
    op.drop_column('tools', 'approval_urgency')
    op.drop_column('tools', 'requires_approval')
    
    # Drop enum types
    sa.Enum(name='approval_urgency').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='approval_status').drop(op.get_bind(), checkfirst=True)
