"""Add campaign_id to tool_executions and agent_runs

Adds a nullable campaign_id FK to tool_executions and agent_runs tables
for campaign-level cost attribution and rollups.

Revision ID: j2k3l4m5n6o7
Revises: i1j2k3l4m5n6
Create Date: 2026-02-14 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "j2k3l4m5n6o7"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add campaign_id to tool_executions
    op.add_column(
        "tool_executions",
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tool_executions_campaign_id",
        "tool_executions",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tool_executions_campaign_id",
        "tool_executions",
        ["campaign_id"],
    )

    # Add campaign_id to agent_runs
    op.add_column(
        "agent_runs",
        sa.Column("campaign_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_runs_campaign_id",
        "agent_runs",
        "campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_agent_runs_campaign_id",
        "agent_runs",
        ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_campaign_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_campaign_id", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "campaign_id")

    op.drop_index("ix_tool_executions_campaign_id", table_name="tool_executions")
    op.drop_constraint("fk_tool_executions_campaign_id", "tool_executions", type_="foreignkey")
    op.drop_column("tool_executions", "campaign_id")
