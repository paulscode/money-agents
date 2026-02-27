"""Add campaign lease infrastructure

Revision ID: f1a2b3c4d5e6
Revises: e9f0a1b2c3d4
Create Date: 2026-01-31 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'e9f0a1b2c3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new campaign status values to enum
    # Note: PostgreSQL ENUM types require ADD VALUE to be run outside of transaction
    # but we can use IF NOT EXISTS for idempotency
    
    # Must be done outside transaction block - these are inherently non-transactional
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'paused_failover'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'requirements_gathering'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'executing'")
    op.execute("ALTER TYPE campaign_status ADD VALUE IF NOT EXISTS 'monitoring'")
    
    # Add lease fields to campaigns table
    op.add_column('campaigns', sa.Column('leased_by', sa.String(100), nullable=True))
    op.add_column('campaigns', sa.Column('lease_acquired_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('campaigns', sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('campaigns', sa.Column('lease_heartbeat_at', sa.DateTime(timezone=True), nullable=True))
    
    # Campaign assignment hints
    op.add_column('campaigns', sa.Column('worker_affinity', sa.String(100), nullable=True))
    op.add_column('campaigns', sa.Column('resource_requirements', postgresql.JSONB, nullable=True, server_default='[]'))
    op.add_column('campaigns', sa.Column('estimated_complexity', sa.String(20), nullable=True, server_default='medium'))
    
    # Simple indexes (don't use partial indexes with new enum values - PostgreSQL limitation)
    op.create_index('idx_campaigns_leased_by', 'campaigns', ['leased_by'])
    op.create_index('idx_campaigns_lease_expires', 'campaigns', ['lease_expires_at'])
    
    # Create campaign_workers table
    op.create_table(
        'campaign_workers',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('worker_id', sa.String(100), nullable=False, unique=True),
        sa.Column('hostname', sa.String(255), nullable=False),
        sa.Column('worker_type', sa.String(20), nullable=False),  # 'local' or 'remote'
        sa.Column('remote_agent_id', postgresql.UUID(as_uuid=True), 
                  sa.ForeignKey('remote_agents.id', ondelete='SET NULL'), nullable=True),
        
        # Capacity
        sa.Column('campaign_capacity', sa.Integer, nullable=False, server_default='3'),
        sa.Column('current_campaign_count', sa.Integer, nullable=False, server_default='0'),
        sa.Column('ram_gb', sa.Integer, nullable=True),
        sa.Column('cpu_threads', sa.Integer, nullable=True),
        
        # Preferences
        sa.Column('preferences', postgresql.JSONB, nullable=True, server_default='[]'),
        
        # Status
        sa.Column('status', sa.String(20), nullable=False, server_default='offline'),  # online, offline, draining
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('disconnected_at', sa.DateTime(timezone=True), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    
    op.create_index('idx_campaign_workers_status', 'campaign_workers', ['status'])
    op.create_index('idx_campaign_workers_worker_id', 'campaign_workers', ['worker_id'])
    op.create_index(
        'idx_campaign_workers_capacity',
        'campaign_workers',
        ['status', 'current_campaign_count', 'campaign_capacity'],
        postgresql_where=sa.text("status = 'online'")
    )


def downgrade() -> None:
    # Drop campaign_workers table
    op.drop_index('idx_campaign_workers_capacity')
    op.drop_index('idx_campaign_workers_worker_id')
    op.drop_index('idx_campaign_workers_status')
    op.drop_table('campaign_workers')
    
    # Remove lease fields from campaigns
    op.drop_index('idx_campaigns_leased_by')
    op.drop_index('idx_campaigns_lease_expires')
    op.drop_column('campaigns', 'estimated_complexity')
    op.drop_column('campaigns', 'resource_requirements')
    op.drop_column('campaigns', 'worker_affinity')
    op.drop_column('campaigns', 'lease_heartbeat_at')
    op.drop_column('campaigns', 'lease_expires_at')
    op.drop_column('campaigns', 'lease_acquired_at')
    op.drop_column('campaigns', 'leased_by')
    
    # Note: Cannot easily remove ENUM values in PostgreSQL
    # The new status values will remain in the type
