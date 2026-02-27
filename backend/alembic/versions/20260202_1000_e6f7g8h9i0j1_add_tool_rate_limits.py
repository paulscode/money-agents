"""add_tool_rate_limits

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7a8b9c0
Create Date: 2026-02-02 10:00:00.000000

Add rate limiting for tool executions:
1. tool_rate_limits table for configuring rate limits
2. rate_limit_violations table for tracking violations
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e6f7g8h9i0j1'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums using raw SQL with IF NOT EXISTS
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE rate_limit_scope AS ENUM ('global', 'user', 'tool', 'user_tool');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE rate_limit_period AS ENUM ('minute', 'hour', 'day', 'week', 'month');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create tool_rate_limits table using raw SQL for enum columns
    op.execute("""
        CREATE TABLE tool_rate_limits (
            id UUID NOT NULL PRIMARY KEY,
            scope rate_limit_scope NOT NULL,
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            tool_id UUID REFERENCES tools(id) ON DELETE CASCADE,
            max_executions INTEGER NOT NULL,
            period rate_limit_period NOT NULL,
            max_cost_units INTEGER,
            allow_burst BOOLEAN NOT NULL DEFAULT FALSE,
            burst_multiplier INTEGER DEFAULT 2,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            name VARCHAR(255),
            description TEXT,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            created_by_id UUID REFERENCES users(id) ON DELETE SET NULL
        );
    """)
    
    op.create_index(op.f('ix_tool_rate_limits_scope'), 'tool_rate_limits', ['scope'], unique=False)
    op.create_index(op.f('ix_tool_rate_limits_user_id'), 'tool_rate_limits', ['user_id'], unique=False)
    op.create_index(op.f('ix_tool_rate_limits_tool_id'), 'tool_rate_limits', ['tool_id'], unique=False)
    op.create_index(op.f('ix_tool_rate_limits_is_active'), 'tool_rate_limits', ['is_active'], unique=False)
    
    # Create rate_limit_violations table
    op.execute("""
        CREATE TABLE rate_limit_violations (
            id UUID NOT NULL PRIMARY KEY,
            rate_limit_id UUID NOT NULL REFERENCES tool_rate_limits(id) ON DELETE CASCADE,
            user_id UUID REFERENCES users(id) ON DELETE SET NULL,
            tool_id UUID REFERENCES tools(id) ON DELETE SET NULL,
            current_count INTEGER NOT NULL,
            limit_count INTEGER NOT NULL,
            period_start TIMESTAMP WITH TIME ZONE NOT NULL,
            agent_name VARCHAR(100),
            request_context JSONB,
            violated_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
    """)
    
    op.create_index(op.f('ix_rate_limit_violations_rate_limit_id'), 'rate_limit_violations', ['rate_limit_id'], unique=False)
    op.create_index(op.f('ix_rate_limit_violations_user_id'), 'rate_limit_violations', ['user_id'], unique=False)
    op.create_index(op.f('ix_rate_limit_violations_tool_id'), 'rate_limit_violations', ['tool_id'], unique=False)
    op.create_index(op.f('ix_rate_limit_violations_violated_at'), 'rate_limit_violations', ['violated_at'], unique=False)


def downgrade() -> None:
    # Drop tables
    op.drop_index(op.f('ix_rate_limit_violations_violated_at'), table_name='rate_limit_violations')
    op.drop_index(op.f('ix_rate_limit_violations_tool_id'), table_name='rate_limit_violations')
    op.drop_index(op.f('ix_rate_limit_violations_user_id'), table_name='rate_limit_violations')
    op.drop_index(op.f('ix_rate_limit_violations_rate_limit_id'), table_name='rate_limit_violations')
    op.drop_table('rate_limit_violations')
    
    op.drop_index(op.f('ix_tool_rate_limits_is_active'), table_name='tool_rate_limits')
    op.drop_index(op.f('ix_tool_rate_limits_tool_id'), table_name='tool_rate_limits')
    op.drop_index(op.f('ix_tool_rate_limits_user_id'), table_name='tool_rate_limits')
    op.drop_index(op.f('ix_tool_rate_limits_scope'), table_name='tool_rate_limits')
    op.drop_table('tool_rate_limits')
    
    # Drop enums
    op.execute("DROP TYPE IF EXISTS rate_limit_period")
    op.execute("DROP TYPE IF EXISTS rate_limit_scope")
