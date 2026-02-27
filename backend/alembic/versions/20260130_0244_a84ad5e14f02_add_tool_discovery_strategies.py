"""add_tool_discovery_strategies

Revision ID: a84ad5e14f02
Revises: c4d5e6f7a8b9
Create Date: 2026-01-30 02:44:17.416827

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'a84ad5e14f02'
down_revision = 'c4d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create tool_discovery_strategies table only
    op.create_table('tool_discovery_strategies',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('focus_area', sa.String(length=50), nullable=False),
    sa.Column('search_queries', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('target_categories', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('priority_keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('times_executed', sa.Float(), nullable=True),
    sa.Column('knowledge_entries_found', sa.Float(), nullable=True),
    sa.Column('tools_proposed', sa.Float(), nullable=True),
    sa.Column('tools_approved', sa.Float(), nullable=True),
    sa.Column('effectiveness_score', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_executed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_index('idx_tool_strategy_effectiveness', 'tool_discovery_strategies', ['effectiveness_score'], unique=False)
    op.create_index('idx_tool_strategy_status', 'tool_discovery_strategies', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_tool_strategy_status', table_name='tool_discovery_strategies')
    op.drop_index('idx_tool_strategy_effectiveness', table_name='tool_discovery_strategies')
    op.drop_table('tool_discovery_strategies')
