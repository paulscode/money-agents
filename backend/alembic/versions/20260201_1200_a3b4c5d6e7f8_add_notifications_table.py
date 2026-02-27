"""Add notifications table

Revision ID: a3b4c5d6e7f8
Revises: 52cc30001c48
Create Date: 2026-02-01 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ENUM

# revision identifiers, used by Alembic.
revision = 'a3b4c5d6e7f8'
down_revision = '52cc30001c48'
branch_labels = None
depends_on = None

# Define enums for use in table creation (create_type=False since we create them manually)
notification_type_enum = ENUM(
    'task_created', 'task_due_soon', 'task_overdue', 'task_completed',
    'campaign_started', 'campaign_completed', 'campaign_failed', 'input_required', 'threshold_warning',
    'opportunities_discovered', 'high_value_opportunity',
    'proposal_submitted', 'proposal_approved', 'proposal_needs_review',
    'agent_error', 'system_alert', 'credential_expiring',
    name='notification_type_v2',
    create_type=False
)

notification_priority_enum = ENUM(
    'low', 'medium', 'high', 'urgent',
    name='notification_priority',
    create_type=False
)


def upgrade() -> None:
    # Create notification type enum (IF NOT EXISTS to handle partial migrations)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_type_v2 AS ENUM (
                'task_created',
                'task_due_soon',
                'task_overdue',
                'task_completed',
                'campaign_started',
                'campaign_completed',
                'campaign_failed',
                'input_required',
                'threshold_warning',
                'opportunities_discovered',
                'high_value_opportunity',
                'proposal_submitted',
                'proposal_approved',
                'proposal_needs_review',
                'agent_error',
                'system_alert',
                'credential_expiring'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create notification priority enum (IF NOT EXISTS to handle partial migrations)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notification_priority AS ENUM (
                'low',
                'medium',
                'high',
                'urgent'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create notifications table
    op.create_table(
        'notifications',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        
        # Notification content - use pre-defined ENUM types
        sa.Column('type', notification_type_enum, nullable=False, index=True),
        sa.Column('priority', notification_priority_enum, nullable=False, server_default='medium'),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('message', sa.Text, nullable=False),
        
        # Navigation
        sa.Column('link', sa.String(500), nullable=True),
        sa.Column('link_text', sa.String(100), nullable=True),
        
        # Source tracking
        sa.Column('source_type', sa.String(50), nullable=True),
        sa.Column('source_id', UUID(as_uuid=True), nullable=True),
        
        # Additional data (extra_data instead of metadata to avoid SQLAlchemy reserved name)
        sa.Column('extra_data', JSONB, nullable=True),
        
        # Status tracking
        sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False, index=True),
    )
    
    # Create composite indexes for common queries
    op.create_index('idx_notifications_user_unread', 'notifications', ['user_id', 'read_at'])
    op.create_index('idx_notifications_user_created', 'notifications', ['user_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('idx_notifications_user_created', table_name='notifications')
    op.drop_index('idx_notifications_user_unread', table_name='notifications')
    op.drop_table('notifications')
    op.execute('DROP TYPE IF EXISTS notification_priority')
    op.execute('DROP TYPE IF EXISTS notification_type_v2')
