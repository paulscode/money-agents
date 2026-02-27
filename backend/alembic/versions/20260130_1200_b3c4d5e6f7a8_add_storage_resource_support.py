"""Add storage resource support

Adds resource category (compute/capacity) and storage tracking tables.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-01-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add category column to resources table
    # Default to 'compute' for existing resources
    op.add_column(
        'resources',
        sa.Column('category', sa.String(length=50), nullable=False, server_default='compute')
    )
    op.create_index(op.f('ix_resources_category'), 'resources', ['category'], unique=False)
    
    # Update existing storage resources to category 'capacity'
    op.execute("""
        UPDATE resources 
        SET category = 'capacity' 
        WHERE resource_type = 'storage'
    """)
    
    # Create storage_reservations table for space reservations
    op.create_table(
        'storage_reservations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=False),
        sa.Column('purpose', sa.Text(), nullable=True),
        sa.Column('bytes_reserved', sa.BigInteger(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ondelete='CASCADE'),
    )
    op.create_index(
        op.f('ix_storage_reservations_resource_id'), 
        'storage_reservations', 
        ['resource_id'], 
        unique=False
    )
    op.create_index(
        op.f('ix_storage_reservations_expires_at'), 
        'storage_reservations', 
        ['expires_at'], 
        unique=False
    )
    
    # Create storage_files table for tracking stored files
    op.create_table(
        'storage_files',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('agent_name', sa.String(length=100), nullable=True),
        sa.Column('purpose', sa.Text(), nullable=True),
        sa.Column('is_temporary', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_accessed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['resource_id'], ['resources.id'], ondelete='CASCADE'),
    )
    op.create_index(
        op.f('ix_storage_files_resource_id'), 
        'storage_files', 
        ['resource_id'], 
        unique=False
    )
    op.create_index(
        op.f('ix_storage_files_file_path'), 
        'storage_files', 
        ['file_path'], 
        unique=True
    )
    op.create_index(
        op.f('ix_storage_files_agent_name'), 
        'storage_files', 
        ['agent_name'], 
        unique=False
    )


def downgrade() -> None:
    # Drop storage_files table
    op.drop_index(op.f('ix_storage_files_agent_name'), table_name='storage_files')
    op.drop_index(op.f('ix_storage_files_file_path'), table_name='storage_files')
    op.drop_index(op.f('ix_storage_files_resource_id'), table_name='storage_files')
    op.drop_table('storage_files')
    
    # Drop storage_reservations table
    op.drop_index(op.f('ix_storage_reservations_expires_at'), table_name='storage_reservations')
    op.drop_index(op.f('ix_storage_reservations_resource_id'), table_name='storage_reservations')
    op.drop_table('storage_reservations')
    
    # Remove category column from resources
    op.drop_index(op.f('ix_resources_category'), table_name='resources')
    op.drop_column('resources', 'category')
