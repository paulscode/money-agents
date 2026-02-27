"""Add user profile fields

Revision ID: 9d5f8e4c3b2a
Revises: 8c4d7e3f2a1b
Create Date: 2026-01-29 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d5f8e4c3b2a'
down_revision: Union[str, None] = '8c4d7e3f2a1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add display_name and avatar_url columns to users table."""
    op.add_column('users', sa.Column('display_name', sa.String(100), nullable=True))
    op.add_column('users', sa.Column('avatar_url', sa.String(500), nullable=True))


def downgrade() -> None:
    """Remove display_name and avatar_url columns from users table."""
    op.drop_column('users', 'avatar_url')
    op.drop_column('users', 'display_name')
