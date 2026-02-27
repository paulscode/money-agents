"""add_tools_catalog

Revision ID: 66124bcab6bf
Revises: 723ca881044e
Create Date: 2026-01-28 13:45:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '66124bcab6bf'
down_revision = '723ca881044e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create tool_status enum
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tool_status AS ENUM (
                'requested', 'under_review', 'changes_requested', 'approved', 'rejected',
                'implementing', 'testing', 'blocked', 'on_hold',
                'implemented', 'deprecated', 'retired'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create tool_category enum
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE tool_category AS ENUM (
                'api', 'data_source', 'automation', 'analysis', 'communication'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Create tools table  
    # Note: We reference the enum types by name using postgresql.ENUM since we created them manually above
    tool_category_type = postgresql.ENUM('api', 'data_source', 'automation', 'analysis', 'communication', name='tool_category', create_type=False)
    tool_status_type = postgresql.ENUM('requested', 'under_review', 'changes_requested', 'approved', 'rejected', 'implementing', 'testing', 'blocked', 'on_hold', 'implemented', 'deprecated', 'retired', name='tool_status', create_type=False)
    
    op.create_table('tools',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('category', tool_category_type, nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('tags', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('status', tool_status_type, nullable=False),
        sa.Column('requester_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('assigned_to_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('approved_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('implementation_notes', sa.Text(), nullable=True),
        sa.Column('blockers', sa.Text(), nullable=True),
        sa.Column('dependencies', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('implemented_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('estimated_completion_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('usage_instructions', sa.Text(), nullable=True),
        sa.Column('example_code', sa.Text(), nullable=True),
        sa.Column('required_environment_variables', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('integration_complexity', sa.String(length=20), nullable=True),
        sa.Column('cost_model', sa.String(length=50), nullable=True),
        sa.Column('cost_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('shared_resources', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('strengths', sa.Text(), nullable=True),
        sa.Column('weaknesses', sa.Text(), nullable=True),
        sa.Column('best_use_cases', sa.Text(), nullable=True),
        sa.Column('external_documentation_url', sa.String(length=500), nullable=True),
        sa.Column('version', sa.String(length=20), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['requester_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['assigned_to_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['approved_by_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes
    op.create_index(op.f('ix_tools_name'), 'tools', ['name'], unique=True)
    op.create_index(op.f('ix_tools_slug'), 'tools', ['slug'], unique=True)
    op.create_index(op.f('ix_tools_category'), 'tools', ['category'], unique=False)
    op.create_index(op.f('ix_tools_status'), 'tools', ['status'], unique=False)
    op.create_index(op.f('ix_tools_requester_id'), 'tools', ['requester_id'], unique=False)
    op.create_index(op.f('ix_tools_assigned_to_id'), 'tools', ['assigned_to_id'], unique=False)
    op.create_index('idx_tools_status_category', 'tools', ['status', 'category'], unique=False)
    op.create_index('idx_tools_created_at', 'tools', ['created_at'], unique=False)


def downgrade() -> None:
    # Drop indexes
    op.drop_index('idx_tools_created_at', table_name='tools')
    op.drop_index('idx_tools_status_category', table_name='tools')
    op.drop_index(op.f('ix_tools_assigned_to_id'), table_name='tools')
    op.drop_index(op.f('ix_tools_requester_id'), table_name='tools')
    op.drop_index(op.f('ix_tools_status'), table_name='tools')
    op.drop_index(op.f('ix_tools_category'), table_name='tools')
    op.drop_index(op.f('ix_tools_slug'), table_name='tools')
    op.drop_index(op.f('ix_tools_name'), table_name='tools')
    
    # Drop tools table
    op.drop_table('tools')
    
    # Drop enums
    op.execute('DROP TYPE tool_category')
    op.execute('DROP TYPE tool_status')
