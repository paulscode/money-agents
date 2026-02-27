"""remove conflicting ollama tool

Revision ID: c4d5e6f7g8h9
Revises: b1c2d3e4f5g6h7i8
Create Date: 2026-02-01 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7g8h9'
down_revision: Union[str, None] = 'b1c2d3e4f5g6h7i8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Remove the ollama-local-llm tool that conflicts with native Ollama provider.
    
    The Ollama local LLM is now handled as a native LLM provider in llm_service.py,
    not as a tool. Having it as a tool creates confusion and potential conflicts.
    
    Also removes ollama-documentation-generator if it exists (deprecated).
    """
    # Get connection for raw SQL
    conn = op.get_bind()
    
    # First, delete any tool executions referencing these tools
    # This is necessary due to foreign key constraints
    conn.execute(sa.text("""
        DELETE FROM tool_executions 
        WHERE tool_id IN (
            SELECT id FROM tools 
            WHERE slug IN ('ollama-local-llm', 'ollama-documentation-generator')
        )
    """))
    
    # Now delete the tools themselves
    conn.execute(sa.text("""
        DELETE FROM tools 
        WHERE slug IN ('ollama-local-llm', 'ollama-documentation-generator')
    """))


def downgrade() -> None:
    """Re-add the Ollama tools if needed.
    
    Note: This doesn't restore the full tool definitions, just creates placeholders.
    Use init_tools_catalog.py to fully restore if needed.
    """
    conn = op.get_bind()
    
    # Check if tools already exist before inserting
    result = conn.execute(sa.text(
        "SELECT COUNT(*) FROM tools WHERE slug = 'ollama-local-llm'"
    )).scalar()
    
    if result == 0:
        conn.execute(sa.text("""
            INSERT INTO tools (
                name, slug, description, category, 
                implementation_type, status, discovery_strategy,
                created_at, updated_at
            ) VALUES (
                'Ollama Local LLM',
                'ollama-local-llm', 
                'Run inference using locally installed Ollama models (deprecated - use native provider)',
                'llm',
                'local',
                'inactive',
                'manual',
                NOW(), 
                NOW()
            )
        """))
