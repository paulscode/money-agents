"""SA2: Add user security fields (password_changed_at, failed_login_attempts, locked_until)

Revision ID: sa2_user_security
Revises: b2c3d4e5f6g7
Create Date: 2026-02-25 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "sa2_user_security"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "locked_until")
    op.drop_column("users", "failed_login_attempts")
    op.drop_column("users", "password_changed_at")
