"""Add expected_duration_minutes to JobQueue and AgentDefinition.

Revision ID: c3f5a9b12d4e
Revises: a84ad5e14f02
Create Date: 2026-01-30 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3f5a9b12d4e'
down_revision: Union[str, None] = 'a84ad5e14f02'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add expected_duration_minutes to job_queue
    # This allows each job to specify how long it expects to run
    # Recovery uses this instead of fixed threshold to determine staleness
    op.add_column(
        'job_queue',
        sa.Column(
            'expected_duration_minutes',
            sa.Integer(),
            nullable=True,
            comment='Expected job duration in minutes. Used for staleness detection with padding.'
        )
    )
    
    # Add expected_run_duration_minutes to agent_definitions
    # This is the default expected runtime for the agent
    op.add_column(
        'agent_definitions',
        sa.Column(
            'expected_run_duration_minutes',
            sa.Integer(),
            nullable=True,
            comment='Expected agent run duration in minutes. Used for staleness detection.'
        )
    )
    
    # Set sensible defaults based on observed data:
    # - Opportunity Scout: max observed 36 min, set to 60 min for safety
    # - Tool Scout: max observed 7.5 min, set to 15 min
    # - Proposal Writer: nearly instant, set to 5 min
    # - Campaign Manager: can be long-running, set to 120 min
    op.execute("""
        UPDATE agent_definitions SET expected_run_duration_minutes = 
            CASE slug
                WHEN 'opportunity_scout' THEN 60
                WHEN 'tool_scout' THEN 15
                WHEN 'proposal_writer' THEN 5
                WHEN 'campaign_manager' THEN 120
                ELSE 30
            END
    """)


def downgrade() -> None:
    op.drop_column('agent_definitions', 'expected_run_duration_minutes')
    op.drop_column('job_queue', 'expected_duration_minutes')
