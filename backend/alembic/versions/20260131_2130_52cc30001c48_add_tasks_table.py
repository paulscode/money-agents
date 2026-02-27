"""add_tasks_table

Revision ID: 52cc30001c48
Revises: a1b2c3d4e5f6g7h8
Create Date: 2026-01-31 21:30:43.301611

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '52cc30001c48'
down_revision = 'a1b2c3d4e5f6g7h8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create tasks table
    op.create_table('tasks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('task_type', sa.String(length=30), nullable=False),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('estimated_value', sa.Float(), nullable=True),
        sa.Column('estimated_effort_minutes', sa.Integer(), nullable=True),
        sa.Column('priority_score', sa.Float(), nullable=False, server_default='50.0'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='created'),
        sa.Column('blocked_by', sa.Text(), nullable=True),
        sa.Column('blocked_by_task_id', sa.UUID(), nullable=True),
        sa.Column('deferred_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('source_type', sa.String(length=50), nullable=True),
        sa.Column('source_id', sa.UUID(), nullable=True),
        sa.Column('source_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completion_notes', sa.Text(), nullable=True),
        sa.Column('actual_value', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column('last_viewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("(status != 'deferred') OR (deferred_until IS NOT NULL)", name='ck_tasks_deferred_has_until'),
        sa.ForeignKeyConstraint(['blocked_by_task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index('ix_tasks_user_status', 'tasks', ['user_id', 'status'], unique=False)
    op.create_index('ix_tasks_user_due', 'tasks', ['user_id', 'due_date'], unique=False)
    op.create_index('ix_tasks_priority', 'tasks', ['user_id', 'priority_score'], unique=False)
    op.create_index('ix_tasks_source', 'tasks', ['source_type', 'source_id'], unique=False)
    op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)
    op.create_index(op.f('ix_tasks_task_type'), 'tasks', ['task_type'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index(op.f('ix_tasks_task_type'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_status'), table_name='tasks')
    op.drop_index('ix_tasks_source', table_name='tasks')
    op.drop_index('ix_tasks_priority', table_name='tasks')
    op.drop_index('ix_tasks_user_due', table_name='tasks')
    op.drop_index('ix_tasks_user_status', table_name='tasks')
    
    # Drop table
    op.drop_table('tasks')
