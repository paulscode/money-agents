"""Add disclaimer acknowledgement and system settings

Revision ID: p1q2r3s4t5u6
Revises: n7o8p9q0r1s2
Create Date: 2026-02-16 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'p1q2r3s4t5u6'
down_revision = 'n7o8p9q0r1s2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add disclaimer fields to users table
    op.add_column('users', sa.Column('disclaimer_acknowledged_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('show_disclaimer_on_login', sa.Boolean(), nullable=False, server_default='true'))

    # Create system_settings table
    op.create_table(
        'system_settings',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Insert initial setting: agents start disabled until admin acknowledges disclaimer
    op.execute(
        "INSERT INTO system_settings (key, value) VALUES ('agents_enabled', 'false')"
    )


def downgrade() -> None:
    op.drop_table('system_settings')
    op.drop_column('users', 'show_disclaimer_on_login')
    op.drop_column('users', 'disclaimer_acknowledged_at')
