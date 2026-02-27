"""add tool health checks

Revision ID: g9h0i1j2k3l4
Revises: f8g9h0i1j2k3
Create Date: 2026-02-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'g9h0i1j2k3l4'
down_revision: Union[str, None] = 'f8g9h0i1j2k3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add health check fields to tools table
    op.add_column('tools', sa.Column('health_status', sa.String(20), nullable=True, server_default='unknown'))
    op.add_column('tools', sa.Column('last_health_check', sa.DateTime(timezone=True), nullable=True))
    op.add_column('tools', sa.Column('health_message', sa.Text(), nullable=True))
    op.add_column('tools', sa.Column('health_response_ms', sa.Integer(), nullable=True))
    op.add_column('tools', sa.Column('health_check_enabled', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('tools', sa.Column('health_check_interval_minutes', sa.Integer(), nullable=True, server_default='60'))
    
    # Create tool_health_checks table for history
    op.create_table(
        'tool_health_checks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('tool_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('tools.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('check_type', sa.String(50), nullable=False),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('is_automatic', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('triggered_by_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    
    # Create indexes
    op.create_index('idx_health_check_tool_time', 'tool_health_checks', ['tool_id', 'checked_at'])
    op.create_index('idx_health_check_status', 'tool_health_checks', ['status'])
    op.create_index('idx_tools_health_status', 'tools', ['health_status'])


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_tools_health_status', table_name='tools')
    op.drop_index('idx_health_check_status', table_name='tool_health_checks')
    op.drop_index('idx_health_check_tool_time', table_name='tool_health_checks')
    
    # Drop table
    op.drop_table('tool_health_checks')
    
    # Remove columns from tools
    op.drop_column('tools', 'health_check_interval_minutes')
    op.drop_column('tools', 'health_check_enabled')
    op.drop_column('tools', 'health_response_ms')
    op.drop_column('tools', 'health_message')
    op.drop_column('tools', 'last_health_check')
    op.drop_column('tools', 'health_status')
