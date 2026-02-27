"""Add tool scout knowledge base models

Revision ID: 9e5f8a7b6c4d
Revises: 9d5f8e4c3b2a
Create Date: 2026-01-30 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9e5f8a7b6c4d'
down_revision: Union[str, None] = '6f201faa229d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create tool_knowledge table
    op.create_table(
        'tool_knowledge',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(255), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('full_content', sa.Text(), nullable=True),
        sa.Column('category', sa.String(30), nullable=False, server_default='tool'),
        sa.Column('related_tool_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source_url', sa.String(500), nullable=True),
        sa.Column('source_type', sa.String(50), nullable=True),
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('relevance_score', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('last_validated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('validation_count', sa.Float(), server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('keywords', postgresql.JSONB(), server_default='[]'),
        sa.Column('created_by_agent', sa.String(50), nullable=False, server_default='tool_scout'),
        sa.Column('last_updated_by', sa.String(50), nullable=True),
        sa.Column('agent_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['related_tool_id'], ['tools.id'], ondelete='SET NULL'),
    )
    op.create_index('idx_tool_knowledge_status_category', 'tool_knowledge', ['status', 'category'])
    op.create_index('idx_tool_knowledge_relevance', 'tool_knowledge', ['relevance_score'])
    op.create_index('idx_tool_knowledge_discovered', 'tool_knowledge', ['discovered_at'])

    # Create tool_idea_entries table
    op.create_table(
        'tool_idea_entries',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('original_idea_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('use_case', sa.Text(), nullable=True),
        sa.Column('context', sa.Text(), nullable=True),
        sa.Column('relevance_score', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('priority', sa.String(20), nullable=True),
        sa.Column('is_addressed', sa.Boolean(), server_default='false'),
        sa.Column('addressed_by_tool_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('keywords', postgresql.JSONB(), server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['original_idea_id'], ['user_ideas.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['addressed_by_tool_id'], ['tools.id'], ondelete='SET NULL'),
    )
    op.create_index('idx_tool_idea_user', 'tool_idea_entries', ['user_id'])
    op.create_index('idx_tool_idea_relevance', 'tool_idea_entries', ['relevance_score'])
    op.create_index('idx_tool_idea_addressed', 'tool_idea_entries', ['is_addressed'])


def downgrade() -> None:
    op.drop_index('idx_tool_idea_addressed', 'tool_idea_entries')
    op.drop_index('idx_tool_idea_relevance', 'tool_idea_entries')
    op.drop_index('idx_tool_idea_user', 'tool_idea_entries')
    op.drop_table('tool_idea_entries')
    
    op.drop_index('idx_tool_knowledge_discovered', 'tool_knowledge')
    op.drop_index('idx_tool_knowledge_relevance', 'tool_knowledge')
    op.drop_index('idx_tool_knowledge_status_category', 'tool_knowledge')
    op.drop_table('tool_knowledge')
