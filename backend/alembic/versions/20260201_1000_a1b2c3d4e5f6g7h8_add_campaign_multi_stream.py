"""Add campaign multi-stream execution models

This migration adds the multi-stream architecture tables:
- task_streams: Parallel execution tracks within campaigns
- campaign_tasks: Individual tasks with dependencies
- user_input_requests: Consolidated user input requests
- auto_approval_rules: Configurable auto-approval settings

Also adds execution_plan and streams_parallel_execution fields to campaigns.

Revision ID: a1b2c3d4e5f6g7h8
Revises: f1a2b3c4d5e6
Create Date: 2026-02-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6g7h8'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ==========================================================================
    # Create enum types (with checkfirst to handle reruns)
    # ==========================================================================
    
    # TaskStreamStatus enum
    task_stream_status = postgresql.ENUM(
        'pending', 'ready', 'blocked', 'in_progress', 'completed', 'failed', 'cancelled',
        name='task_stream_status',
        create_type=False
    )
    task_stream_status.create(op.get_bind(), checkfirst=True)
    
    # TaskStatus enum (for campaign tasks)
    campaign_task_status = postgresql.ENUM(
        'pending', 'queued', 'running', 'completed', 'failed', 'skipped', 'blocked', 'cancelled',
        name='campaign_task_status',
        create_type=False
    )
    campaign_task_status.create(op.get_bind(), checkfirst=True)
    
    # TaskType enum
    campaign_task_type = postgresql.ENUM(
        'tool_execution', 'llm_reasoning', 'user_input', 'wait', 'checkpoint', 'parallel_gate',
        name='campaign_task_type',
        create_type=False
    )
    campaign_task_type.create(op.get_bind(), checkfirst=True)
    
    # InputType enum
    user_input_type = postgresql.ENUM(
        'credentials', 'text', 'confirmation', 'selection', 'file', 'budget_approval', 'content',
        name='user_input_type',
        create_type=False
    )
    user_input_type.create(op.get_bind(), checkfirst=True)
    
    # InputPriority enum
    user_input_priority = postgresql.ENUM(
        'blocking', 'high', 'medium', 'low',
        name='user_input_priority',
        create_type=False
    )
    user_input_priority.create(op.get_bind(), checkfirst=True)
    
    # InputStatus enum
    user_input_status = postgresql.ENUM(
        'pending', 'provided', 'expired', 'cancelled',
        name='user_input_status',
        create_type=False
    )
    user_input_status.create(op.get_bind(), checkfirst=True)
    
    # ==========================================================================
    # Create task_streams table
    # ==========================================================================
    op.create_table(
        'task_streams',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Stream identification
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        
        # Status - use existing enum type
        sa.Column('status', postgresql.ENUM('pending', 'ready', 'blocked', 'in_progress', 'completed', 'failed', 'cancelled', name='task_stream_status', create_type=False), nullable=False, server_default='pending'),
        sa.Column('blocking_reasons', postgresql.JSONB(), nullable=True, server_default='[]'),
        
        # Dependencies
        sa.Column('depends_on_streams', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('requires_inputs', postgresql.JSONB(), nullable=True, server_default='[]'),
        
        # Progress tracking
        sa.Column('tasks_total', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tasks_completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tasks_failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tasks_blocked', sa.Integer(), nullable=False, server_default='0'),
        
        # Execution settings
        sa.Column('can_run_parallel', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('max_concurrent', sa.Integer(), nullable=False, server_default='1'),
        
        # Timing
        sa.Column('estimated_duration_minutes', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE')
    )
    
    op.create_index('ix_task_streams_campaign', 'task_streams', ['campaign_id'])
    op.create_index('ix_task_streams_status', 'task_streams', ['status'])
    op.create_index('idx_task_stream_campaign_status', 'task_streams', ['campaign_id', 'status'])
    
    # ==========================================================================
    # Create campaign_tasks table
    # ==========================================================================
    op.create_table(
        'campaign_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('stream_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Task identification
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('order_index', sa.Integer(), nullable=False, server_default='0'),
        
        # Task type and execution - use existing enum types
        sa.Column('task_type', postgresql.ENUM('tool_execution', 'llm_reasoning', 'user_input', 'wait', 'checkpoint', 'parallel_gate', name='campaign_task_type', create_type=False), nullable=False),
        sa.Column('tool_slug', sa.String(100), nullable=True),
        sa.Column('tool_params', postgresql.JSONB(), nullable=True),
        sa.Column('llm_prompt', sa.Text(), nullable=True),
        
        # Status - use existing enum type
        sa.Column('status', postgresql.ENUM('pending', 'queued', 'running', 'completed', 'failed', 'skipped', 'blocked', 'cancelled', name='campaign_task_status', create_type=False), nullable=False, server_default='pending'),
        sa.Column('blocked_reason', sa.Text(), nullable=True),
        
        # Dependencies
        sa.Column('depends_on_tasks', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('depends_on_inputs', postgresql.JSONB(), nullable=True, server_default='[]'),
        
        # Execution settings
        sa.Column('estimated_duration_minutes', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('timeout_minutes', sa.Integer(), nullable=False, server_default='30'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='2'),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('is_critical', sa.Boolean(), nullable=False, server_default='true'),
        
        # Results
        sa.Column('result', postgresql.JSONB(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        
        # Timing
        sa.Column('queued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['stream_id'], ['task_streams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE')
    )
    
    op.create_index('ix_campaign_tasks_stream', 'campaign_tasks', ['stream_id'])
    op.create_index('ix_campaign_tasks_campaign', 'campaign_tasks', ['campaign_id'])
    op.create_index('ix_campaign_tasks_status', 'campaign_tasks', ['status'])
    op.create_index('ix_campaign_tasks_type', 'campaign_tasks', ['task_type'])
    op.create_index('idx_campaign_task_stream_status', 'campaign_tasks', ['stream_id', 'status'])
    op.create_index('idx_campaign_task_campaign_status', 'campaign_tasks', ['campaign_id', 'status'])
    
    # ==========================================================================
    # Create user_input_requests table
    # ==========================================================================
    op.create_table(
        'user_input_requests',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Input identification - use existing enum types
        sa.Column('input_key', sa.String(100), nullable=False),
        sa.Column('input_type', postgresql.ENUM('credentials', 'text', 'confirmation', 'selection', 'file', 'budget_approval', 'content', name='user_input_type', create_type=False), nullable=False),
        
        # Request details
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('options', postgresql.JSONB(), nullable=True),
        sa.Column('default_value', sa.Text(), nullable=True),
        sa.Column('validation_rules', postgresql.JSONB(), nullable=True),
        
        # Priority and urgency - use existing enum type
        sa.Column('priority', postgresql.ENUM('blocking', 'high', 'medium', 'low', name='user_input_priority', create_type=False), nullable=False, server_default='medium'),
        sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
        
        # Impact tracking
        sa.Column('blocking_streams', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('blocking_tasks', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('blocking_count', sa.Integer(), nullable=False, server_default='0'),
        
        # Status and value - use existing enum type
        sa.Column('status', postgresql.ENUM('pending', 'provided', 'expired', 'cancelled', name='user_input_status', create_type=False), nullable=False, server_default='pending'),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('value_metadata', postgresql.JSONB(), nullable=True),
        sa.Column('provided_by_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('provided_at', sa.DateTime(timezone=True), nullable=True),
        
        # Smart suggestions
        sa.Column('suggested_value', sa.Text(), nullable=True),
        sa.Column('suggestion_source', sa.String(100), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['provided_by_user_id'], ['users.id'], ondelete='SET NULL')
    )
    
    op.create_index('ix_user_input_requests_campaign', 'user_input_requests', ['campaign_id'])
    op.create_index('ix_user_input_requests_status', 'user_input_requests', ['status'])
    op.create_index('idx_user_input_campaign_status', 'user_input_requests', ['campaign_id', 'status'])
    op.create_index('idx_user_input_campaign_key', 'user_input_requests', ['campaign_id', 'input_key'], unique=True)
    
    # ==========================================================================
    # Create auto_approval_rules table
    # ==========================================================================
    op.create_table(
        'auto_approval_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('campaign_id', postgresql.UUID(as_uuid=True), nullable=True),  # NULL = global default
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        
        # Budget rules
        sa.Column('max_single_spend', sa.Float(), nullable=False, server_default='50.0'),
        sa.Column('daily_spend_limit', sa.Float(), nullable=False, server_default='500.0'),
        
        # Tool execution rules
        sa.Column('approved_tools', postgresql.JSONB(), nullable=True, server_default='[]'),
        sa.Column('tool_rate_limits', postgresql.JSONB(), nullable=True),
        
        # Content rules
        sa.Column('auto_approve_research', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('content_review_threshold', sa.Float(), nullable=False, server_default='100.0'),
        
        # Retry rules
        sa.Column('retry_on_failure', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('max_retries', sa.Integer(), nullable=False, server_default='3'),
        sa.Column('skip_non_critical_failures', sa.Boolean(), nullable=False, server_default='true'),
        
        # Escalation rules
        sa.Column('escalate_after_hours', sa.Integer(), nullable=False, server_default='24'),
        sa.Column('escalate_budget_pct', sa.Float(), nullable=False, server_default='0.8'),
        
        # Active flag
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE')
    )
    
    op.create_index('ix_auto_approval_rules_campaign', 'auto_approval_rules', ['campaign_id'])
    op.create_index('ix_auto_approval_rules_user', 'auto_approval_rules', ['user_id'])
    op.create_index('idx_auto_approval_user_campaign', 'auto_approval_rules', ['user_id', 'campaign_id'])
    
    # ==========================================================================
    # Add columns to campaigns table
    # ==========================================================================
    op.add_column(
        'campaigns',
        sa.Column('execution_plan', postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        'campaigns',
        sa.Column('streams_parallel_execution', sa.Boolean(), nullable=False, server_default='true')
    )


def downgrade() -> None:
    # Remove columns from campaigns
    op.drop_column('campaigns', 'streams_parallel_execution')
    op.drop_column('campaigns', 'execution_plan')
    
    # Drop auto_approval_rules
    op.drop_index('idx_auto_approval_user_campaign', 'auto_approval_rules')
    op.drop_index('ix_auto_approval_rules_user', 'auto_approval_rules')
    op.drop_index('ix_auto_approval_rules_campaign', 'auto_approval_rules')
    op.drop_table('auto_approval_rules')
    
    # Drop user_input_requests
    op.drop_index('idx_user_input_campaign_key', 'user_input_requests')
    op.drop_index('idx_user_input_campaign_status', 'user_input_requests')
    op.drop_index('ix_user_input_requests_status', 'user_input_requests')
    op.drop_index('ix_user_input_requests_campaign', 'user_input_requests')
    op.drop_table('user_input_requests')
    
    # Drop campaign_tasks
    op.drop_index('idx_campaign_task_campaign_status', 'campaign_tasks')
    op.drop_index('idx_campaign_task_stream_status', 'campaign_tasks')
    op.drop_index('ix_campaign_tasks_type', 'campaign_tasks')
    op.drop_index('ix_campaign_tasks_status', 'campaign_tasks')
    op.drop_index('ix_campaign_tasks_campaign', 'campaign_tasks')
    op.drop_index('ix_campaign_tasks_stream', 'campaign_tasks')
    op.drop_table('campaign_tasks')
    
    # Drop task_streams
    op.drop_index('idx_task_stream_campaign_status', 'task_streams')
    op.drop_index('ix_task_streams_status', 'task_streams')
    op.drop_index('ix_task_streams_campaign', 'task_streams')
    op.drop_table('task_streams')
    
    # Drop enum types
    op.execute('DROP TYPE user_input_status')
    op.execute('DROP TYPE user_input_priority')
    op.execute('DROP TYPE user_input_type')
    op.execute('DROP TYPE campaign_task_type')
    op.execute('DROP TYPE campaign_task_status')
    op.execute('DROP TYPE task_stream_status')
