"""make_conversation_id_nullable_in_job_queue

Revision ID: 2480478e0ee0
Revises: 4720000c2f76
Create Date: 2026-01-29 00:25:29.458000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2480478e0ee0'
down_revision = '4720000c2f76'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make conversation_id nullable to allow test jobs without conversations
    op.alter_column('job_queue', 'conversation_id',
                    existing_type=sa.UUID(),
                    nullable=True)


def downgrade() -> None:
    # Make conversation_id NOT NULL again
    op.alter_column('job_queue', 'conversation_id',
                    existing_type=sa.UUID(),
                    nullable=False)
