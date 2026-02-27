"""Add tool scout agent definition

Revision ID: a2b3c4d5e6f7
Revises: 9e5f8a7b6c4d
Create Date: 2026-01-30 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, None] = '9e5f8a7b6c4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Insert Tool Scout agent definition
    op.execute("""
        INSERT INTO agent_definitions (id, name, slug, description, schedule_interval_seconds, default_model_tier)
        VALUES 
        (gen_random_uuid(), 'Tool Scout', 'tool_scout', 
         'Discovers and evaluates AI tools and capabilities. Maintains a knowledge base about the tool landscape and processes tool ideas from users.',
         43200, 'quality')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM agent_definitions WHERE slug = 'tool_scout'")
