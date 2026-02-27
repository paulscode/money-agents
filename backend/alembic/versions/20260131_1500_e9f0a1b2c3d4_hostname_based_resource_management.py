"""Hostname-based resource management for distributed agents.

This migration implements:
1. Hostname as primary identifier for remote agents
2. Agent-scoped resource naming (agent_hostname + local_name)
3. Tool agent availability and per-agent resource requirements

Revision ID: e9f0a1b2c3d4
Revises: d8f9e0a1b2c3
Create Date: 2026-01-31 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd8f9e0a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # 1. REMOTE AGENTS: Make hostname the primary identifier
    # =========================================================================
    
    # First, ensure all existing agents have a hostname (use name as fallback)
    op.execute("""
        UPDATE remote_agents 
        SET hostname = name 
        WHERE hostname IS NULL OR hostname = ''
    """)
    
    # Make hostname NOT NULL
    op.alter_column('remote_agents', 'hostname',
        existing_type=sa.VARCHAR(255),
        nullable=False
    )
    
    # Add unique constraint on hostname
    op.create_unique_constraint('uq_remote_agents_hostname', 'remote_agents', ['hostname'])
    
    # Rename 'name' to 'display_name' (keep for backward compatibility, but make nullable)
    op.alter_column('remote_agents', 'name',
        new_column_name='display_name',
        existing_type=sa.VARCHAR(100),
        nullable=True
    )
    
    # Drop the old unique index on name (now display_name)
    # The original migration created an index, not a constraint
    op.drop_index('ix_remote_agents_name', 'remote_agents')
    
    # =========================================================================
    # 2. RESOURCES: Add agent-scoped naming
    # =========================================================================
    
    # Add agent_hostname column (denormalized for easier lookups)
    op.add_column('resources', 
        sa.Column('agent_hostname', sa.String(255), nullable=True)
    )
    
    # Add local_name column (name within the agent scope)
    op.add_column('resources',
        sa.Column('local_name', sa.String(100), nullable=True)
    )
    
    # Backfill agent_hostname from remote_agent_id
    op.execute("""
        UPDATE resources r
        SET agent_hostname = ra.hostname
        FROM remote_agents ra
        WHERE r.remote_agent_id = ra.id
    """)
    
    # Backfill local_name by extracting from existing name
    # e.g., "linux-mint-main-gpu-0" -> "gpu-0" (strip agent name prefix)
    op.execute("""
        UPDATE resources
        SET local_name = 
            CASE 
                WHEN agent_hostname IS NOT NULL AND name LIKE agent_hostname || '-%' 
                THEN substring(name from length(agent_hostname) + 2)
                WHEN agent_hostname IS NOT NULL AND position('-' in name) > 0
                THEN substring(name from position('-' in reverse(name)) * -1 + length(name) + 1)
                ELSE name
            END
        WHERE agent_hostname IS NOT NULL
    """)
    
    # Create index on agent_hostname
    op.create_index('idx_resources_agent_hostname', 'resources', ['agent_hostname'])
    
    # Create unique constraint on (agent_hostname, local_name) for remote resources
    # Note: This allows multiple resources with same local_name if agent_hostname is NULL (local resources)
    op.execute("""
        CREATE UNIQUE INDEX uq_resource_agent_local_name 
        ON resources (agent_hostname, local_name) 
        WHERE agent_hostname IS NOT NULL
    """)
    
    # =========================================================================
    # 3. TOOLS: Add agent availability and per-agent resource requirements
    # =========================================================================
    
    # Which agents can run this tool
    # null = local only (not distributed)
    # [] = explicitly disabled everywhere  
    # ["pc1", "pc2"] = available on these agents
    # ["*"] = available on all connected agents
    op.add_column('tools',
        sa.Column('available_on_agents', postgresql.JSONB, nullable=True)
    )
    
    # Per-agent resource requirements
    # Keys are agent hostnames, values are lists of local resource names
    # {"pc1": ["gpu-0"], "pc2": ["gpu-0", "storage"]}
    op.add_column('tools',
        sa.Column('agent_resource_map', postgresql.JSONB, nullable=True)
    )
    
    # Add index for tools that are distributed (have agent availability)
    op.create_index('idx_tools_distributed', 'tools', ['available_on_agents'],
        postgresql_using='gin',
        postgresql_where=sa.text("available_on_agents IS NOT NULL")
    )


def downgrade() -> None:
    # =========================================================================
    # Reverse TOOLS changes
    # =========================================================================
    op.drop_index('idx_tools_distributed', 'tools')
    op.drop_column('tools', 'agent_resource_map')
    op.drop_column('tools', 'available_on_agents')
    
    # =========================================================================
    # Reverse RESOURCES changes
    # =========================================================================
    op.execute("DROP INDEX IF EXISTS uq_resource_agent_local_name")
    op.drop_index('idx_resources_agent_hostname', 'resources')
    op.drop_column('resources', 'local_name')
    op.drop_column('resources', 'agent_hostname')
    
    # =========================================================================
    # Reverse REMOTE AGENTS changes
    # =========================================================================
    # Recreate index on name (now display_name)
    op.create_index('ix_remote_agents_name', 'remote_agents', ['display_name'])
    
    # Rename display_name back to name
    op.alter_column('remote_agents', 'display_name',
        new_column_name='name',
        existing_type=sa.VARCHAR(100),
        nullable=False
    )
    
    # Drop hostname unique constraint
    op.drop_constraint('uq_remote_agents_hostname', 'remote_agents', type_='unique')
    
    # Make hostname nullable again
    op.alter_column('remote_agents', 'hostname',
        existing_type=sa.VARCHAR(255),
        nullable=True
    )
