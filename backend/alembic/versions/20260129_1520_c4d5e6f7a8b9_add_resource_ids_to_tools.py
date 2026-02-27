"""Add resource_ids to tools

Adds resource_ids JSONB column to tools table for multiple resource requirements.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-01-29 15:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add resource_ids column to tools table
    # This allows tools to require multiple resources (e.g., GPU + Storage)
    op.add_column(
        'tools',
        sa.Column('resource_ids', JSONB, nullable=True, default=list)
    )
    
    # Migrate existing resource_id to resource_ids if set
    op.execute("""
        UPDATE tools 
        SET resource_ids = jsonb_build_array(resource_id::text)
        WHERE resource_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_column('tools', 'resource_ids')
