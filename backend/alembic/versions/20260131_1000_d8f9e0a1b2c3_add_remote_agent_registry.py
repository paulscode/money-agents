"""Add remote agent registry

Revision ID: d8f9e0a1b2c3
Revises: a25843b26383
Create Date: 2026-01-31 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8f9e0a1b2c3'
down_revision: Union[str, None] = 'a25843b26383'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create remote_agents table
    op.create_table(
        'remote_agents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('api_key_hash', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('tags', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('status', sa.String(50), nullable=False, server_default='offline'),
        sa.Column('max_concurrent_jobs', sa.Integer(), nullable=False, server_default='1'),
        
        # Capabilities snapshot (updated on connect)
        sa.Column('capabilities', postgresql.JSONB(), nullable=True),
        
        # Live stats (updated on heartbeat)
        sa.Column('live_stats', postgresql.JSONB(), nullable=True),
        
        # Connection tracking
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disconnected_at', sa.DateTime(timezone=True), nullable=True),
        
        # Network info
        sa.Column('ip_address', sa.String(50), nullable=True),
        sa.Column('hostname', sa.String(255), nullable=True),
        
        # Admin
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index('ix_remote_agents_name', 'remote_agents', ['name'], unique=True)
    op.create_index('ix_remote_agents_status', 'remote_agents', ['status'])
    op.create_index('ix_remote_agents_last_seen', 'remote_agents', ['last_seen_at'])
    
    # Add remote_agent_id to job_queue for routing
    op.add_column(
        'job_queue',
        sa.Column('remote_agent_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_job_queue_remote_agent',
        'job_queue',
        'remote_agents',
        ['remote_agent_id'],
        ['id'],
        ondelete='SET NULL'
    )
    op.create_index('ix_job_queue_remote_agent', 'job_queue', ['remote_agent_id'])
    
    # Add is_remote flag to resources to indicate managed by remote agent
    op.add_column(
        'resources',
        sa.Column('remote_agent_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        'fk_resources_remote_agent',
        'resources',
        'remote_agents',
        ['remote_agent_id'],
        ['id'],
        ondelete='SET NULL'
    )


def downgrade() -> None:
    # Remove foreign keys and columns from resources
    op.drop_constraint('fk_resources_remote_agent', 'resources', type_='foreignkey')
    op.drop_column('resources', 'remote_agent_id')
    
    # Remove foreign keys and columns from job_queue
    op.drop_index('ix_job_queue_remote_agent', 'job_queue')
    op.drop_constraint('fk_job_queue_remote_agent', 'job_queue', type_='foreignkey')
    op.drop_column('job_queue', 'remote_agent_id')
    
    # Drop remote_agents table
    op.drop_index('ix_remote_agents_last_seen', 'remote_agents')
    op.drop_index('ix_remote_agents_status', 'remote_agents')
    op.drop_index('ix_remote_agents_name', 'remote_agents')
    op.drop_table('remote_agents')
