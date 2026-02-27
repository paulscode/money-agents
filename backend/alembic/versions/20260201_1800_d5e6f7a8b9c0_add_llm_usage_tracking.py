"""add_llm_usage_tracking

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7g8h9
Create Date: 2026-02-01 18:00:00.000000

Add comprehensive LLM usage tracking:
1. New columns in messages table for detailed token tracking
2. New llm_usage table for tracking all LLM API calls
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'c4d5e6f7g8h9'  # Points to remove_ollama_tool
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add enhanced token tracking to messages table (idempotent with try/except)
    conn = op.get_bind()
    
    # Check and add columns to messages table
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'messages' AND column_name = 'prompt_tokens'"
    ))
    if not result.fetchone():
        op.add_column('messages', sa.Column('prompt_tokens', sa.Integer(), nullable=True))
    
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'messages' AND column_name = 'completion_tokens'"
    ))
    if not result.fetchone():
        op.add_column('messages', sa.Column('completion_tokens', sa.Integer(), nullable=True))
    
    result = conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'messages' AND column_name = 'cost_usd'"
    ))
    if not result.fetchone():
        op.add_column('messages', sa.Column('cost_usd', sa.Float(), nullable=True))
    
    # Create llm_usage_source enum (check if exists first)
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_type WHERE typname = 'llm_usage_source'"
    ))
    if not result.fetchone():
        op.execute("""
            CREATE TYPE llm_usage_source AS ENUM (
                'brainstorm', 'agent_chat', 'agent_task', 'campaign', 'tool', 'other'
            )
        """)
    
    # Check if llm_usage table exists
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'llm_usage'"
    ))
    if not result.fetchone():
        # Create llm_usage table using raw SQL for full control
        op.execute("""
            CREATE TABLE llm_usage (
                id UUID PRIMARY KEY,
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                source llm_usage_source NOT NULL,
                conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
                message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
                agent_run_id UUID REFERENCES agent_runs(id) ON DELETE SET NULL,
                campaign_id UUID REFERENCES campaigns(id) ON DELETE SET NULL,
                provider VARCHAR(50) NOT NULL,
                model VARCHAR(100) NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                total_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd FLOAT,
                latency_ms INTEGER,
                metadata JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        
        # Create indexes
        op.execute("CREATE INDEX idx_llm_usage_user_id ON llm_usage(user_id)")
        op.execute("CREATE INDEX idx_llm_usage_source ON llm_usage(source)")
        op.execute("CREATE INDEX idx_llm_usage_conversation_id ON llm_usage(conversation_id)")
        op.execute("CREATE INDEX idx_llm_usage_agent_run_id ON llm_usage(agent_run_id)")
        op.execute("CREATE INDEX idx_llm_usage_campaign_id ON llm_usage(campaign_id)")
        op.execute("CREATE INDEX idx_llm_usage_provider ON llm_usage(provider)")
        op.execute("CREATE INDEX idx_llm_usage_model ON llm_usage(model)")
        op.execute("CREATE INDEX idx_llm_usage_created_at ON llm_usage(created_at)")
        op.execute("CREATE INDEX idx_llm_usage_user_created ON llm_usage(user_id, created_at)")
        op.execute("CREATE INDEX idx_llm_usage_source_created ON llm_usage(source, created_at)")
        op.execute("CREATE INDEX idx_llm_usage_model_created ON llm_usage(model, created_at)")


def downgrade() -> None:
    # Drop llm_usage table and indexes
    op.drop_index('idx_llm_usage_model_created', table_name='llm_usage')
    op.drop_index('idx_llm_usage_source_created', table_name='llm_usage')
    op.drop_index('idx_llm_usage_user_created', table_name='llm_usage')
    op.drop_table('llm_usage')
    
    # Drop enum
    op.execute("DROP TYPE llm_usage_source")
    
    # Remove columns from messages
    op.drop_column('messages', 'cost_usd')
    op.drop_column('messages', 'completion_tokens')
    op.drop_column('messages', 'prompt_tokens')
