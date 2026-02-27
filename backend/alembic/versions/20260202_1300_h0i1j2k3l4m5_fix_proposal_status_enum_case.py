"""fix proposal_status enum case - sync db with code (lowercase values)

Revision ID: h0i1j2k3l4m5
Revises: g9h0i1j2k3l4
Create Date: 2026-02-02 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h0i1j2k3l4m5'
down_revision: Union[str, None] = 'g9h0i1j2k3l4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Fix proposal_status enum values to use lowercase (matching Python code).
    The database was created with uppercase values, but the Python enum uses lowercase.
    """
    # Rename old uppercase values to lowercase
    # Note: PostgreSQL enum values are case-sensitive
    
    # First, update any existing rows to use lowercase values
    # (There shouldn't be any proposals yet, but just in case)
    op.execute("""
        UPDATE proposals 
        SET status = LOWER(status::text)::proposal_status
        WHERE status::text IN ('PENDING', 'UNDER_REVIEW', 'APPROVED', 'REJECTED', 'DEFERRED', 'CHANGES_REQUESTED')
    """)
    
    # Now rename the enum values
    # PostgreSQL 10+ supports ALTER TYPE ... RENAME VALUE
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'PENDING' TO 'pending'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'UNDER_REVIEW' TO 'under_review'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'APPROVED' TO 'approved'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'REJECTED' TO 'rejected'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'DEFERRED' TO 'deferred'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'CHANGES_REQUESTED' TO 'changes_requested'")


def downgrade() -> None:
    """Revert to uppercase enum values."""
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'pending' TO 'PENDING'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'under_review' TO 'UNDER_REVIEW'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'approved' TO 'APPROVED'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'rejected' TO 'REJECTED'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'deferred' TO 'DEFERRED'")
    op.execute("ALTER TYPE proposal_status RENAME VALUE 'changes_requested' TO 'CHANGES_REQUESTED'")
