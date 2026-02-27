"""add_agent_scheduler_models

Revision ID: a1b2c3d4e5f6
Revises: 9d5f8e4c3b2a
Create Date: 2026-01-29 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '9d5f8e4c3b2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create agent_status enum
    agent_status_enum = postgresql.ENUM(
        'idle', 'running', 'paused', 'error', 'budget_exceeded',
        name='agent_status',
        create_type=False
    )
    agent_status_enum.create(op.get_bind(), checkfirst=True)
    
    # Create agent_run_status enum
    agent_run_status_enum = postgresql.ENUM(
        'pending', 'running', 'completed', 'failed', 'cancelled', 'timeout',
        name='agent_run_status',
        create_type=False
    )
    agent_run_status_enum.create(op.get_bind(), checkfirst=True)
    
    # Create budget_period enum
    budget_period_enum = postgresql.ENUM(
        'hourly', 'daily', 'weekly', 'monthly',
        name='budget_period',
        create_type=False
    )
    budget_period_enum.create(op.get_bind(), checkfirst=True)
    
    # Create agent_definitions table
    op.create_table(
        'agent_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(100), unique=True, nullable=False, index=True),
        sa.Column('slug', sa.String(100), unique=True, nullable=False, index=True),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('status', postgresql.ENUM('idle', 'running', 'paused', 'error', 'budget_exceeded', name='agent_status', create_type=False), nullable=False, server_default='idle'),
        sa.Column('status_message', sa.Text(), nullable=True),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('schedule_interval_seconds', sa.Integer(), nullable=False, server_default='3600'),
        sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('budget_limit', sa.Float(), nullable=True),
        sa.Column('budget_period', postgresql.ENUM('hourly', 'daily', 'weekly', 'monthly', name='budget_period', create_type=False), nullable=False, server_default='daily'),
        sa.Column('budget_used', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('budget_reset_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('budget_warning_threshold', sa.Float(), nullable=False, server_default='0.8'),
        sa.Column('default_model_tier', sa.String(50), nullable=False, server_default='fast'),
        sa.Column('config', postgresql.JSONB(), nullable=True),
        sa.Column('total_runs', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('successful_runs', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed_runs', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_agent_definitions_status_enabled', 'agent_definitions', ['status', 'is_enabled'])
    op.create_index('ix_agent_definitions_next_run', 'agent_definitions', ['next_run_at'])
    
    # Create agent_runs table
    op.create_table(
        'agent_runs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_definitions.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('status', postgresql.ENUM('pending', 'running', 'completed', 'failed', 'cancelled', 'timeout', name='agent_run_status', create_type=False), nullable=False, server_default='pending'),
        sa.Column('trigger_type', sa.String(50), nullable=False, server_default='scheduled'),
        sa.Column('trigger_reason', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('items_processed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('items_created', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('items_updated', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tokens_used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('error_traceback', sa.Text(), nullable=True),
        sa.Column('run_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_agent_runs_agent_status', 'agent_runs', ['agent_id', 'status'])
    op.create_index('ix_agent_runs_created_at', 'agent_runs', ['created_at'])
    
    # Create agent_events table
    op.create_table(
        'agent_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('source_type', sa.String(100), nullable=False),
        sa.Column('source_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('target_agent_slug', sa.String(100), nullable=False),
        sa.Column('is_processed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('processed_by_run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('agent_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_data', postgresql.JSONB(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
    )
    op.create_index('ix_agent_events_event_type', 'agent_events', ['event_type'])
    op.create_index('ix_agent_events_source_id', 'agent_events', ['source_id'])
    op.create_index('ix_agent_events_target_agent_slug', 'agent_events', ['target_agent_slug'])
    op.create_index('ix_agent_events_is_processed', 'agent_events', ['is_processed'])
    op.create_index('ix_agent_events_unprocessed', 'agent_events', ['target_agent_slug', 'is_processed'])
    
    # Insert default agent definitions
    op.execute("""
        INSERT INTO agent_definitions (id, name, slug, description, schedule_interval_seconds, default_model_tier)
        VALUES 
        (gen_random_uuid(), 'Opportunity Scout', 'opportunity_scout', 
         'Discovers potential money-making opportunities through web searches and analysis. Runs periodically to find new opportunities.',
         21600, 'fast'),
        (gen_random_uuid(), 'Proposal Writer', 'proposal_writer',
         'Creates detailed campaign proposals from approved opportunities. Triggered when opportunities are approved.',
         300, 'reasoning'),
        (gen_random_uuid(), 'Campaign Manager', 'campaign_manager',
         'Executes approved campaigns and monitors their progress. Triggered when proposals are approved.',
         600, 'reasoning')
    """)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_index('ix_agent_events_unprocessed', table_name='agent_events')
    op.drop_index('ix_agent_events_is_processed', table_name='agent_events')
    op.drop_index('ix_agent_events_target_agent_slug', table_name='agent_events')
    op.drop_index('ix_agent_events_source_id', table_name='agent_events')
    op.drop_index('ix_agent_events_event_type', table_name='agent_events')
    op.drop_table('agent_events')
    
    op.drop_index('ix_agent_runs_created_at', table_name='agent_runs')
    op.drop_index('ix_agent_runs_agent_status', table_name='agent_runs')
    op.drop_table('agent_runs')
    
    op.drop_index('ix_agent_definitions_next_run', table_name='agent_definitions')
    op.drop_index('ix_agent_definitions_status_enabled', table_name='agent_definitions')
    op.drop_table('agent_definitions')
    
    # Drop enums
    op.execute('DROP TYPE IF EXISTS budget_period')
    op.execute('DROP TYPE IF EXISTS agent_run_status')
    op.execute('DROP TYPE IF EXISTS agent_status')
