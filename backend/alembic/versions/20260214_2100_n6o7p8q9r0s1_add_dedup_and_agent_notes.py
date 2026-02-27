"""Add dedup infrastructure and agent_notes to opportunities

- Adds agent_notes column to opportunities (for auto-dismiss reasons, etc.)
- Ensures pg_trgm extension is available
- Adds GIN trigram index on opportunities.title for fast similarity search
- Adds GIN index on opportunities.source_urls for fast JSONB containment queries

Revision ID: n6o7p8q9r0s1
Revises: m5n6o7p8q9r0
Create Date: 2026-02-14
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "n6o7p8q9r0s1"
down_revision = "m5n6o7p8q9r0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add agent_notes column to opportunities
    op.add_column(
        "opportunities",
        sa.Column("agent_notes", sa.Text(), nullable=True),
    )

    # Ensure pg_trgm extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # GIN trigram index on title for similarity() queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_opportunities_title_trgm "
        "ON opportunities USING gin (title gin_trgm_ops)"
    )

    # GIN index on source_urls JSONB for @> containment checks
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_opportunities_source_urls_gin "
        "ON opportunities USING gin (source_urls jsonb_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_opportunities_source_urls_gin")
    op.execute("DROP INDEX IF EXISTS ix_opportunities_title_trgm")
    op.drop_column("opportunities", "agent_notes")
