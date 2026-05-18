"""abstain

Revision ID: 72222adb11ed
Revises: cc55eec76df6
Create Date: 2025-12-08 02:10:46.749773

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '72222adb11ed'
down_revision: Union[str, Sequence[str], None] = 'cc55eec76df6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add abstain column to proposal_early_vote."""
    op.add_column(
        "proposal_early_vote",
        sa.Column("abstain", sa.Integer(), nullable=False, server_default="0"),
    )
    # drop the default so future inserts must set it explicitly (or your code does)
    op.alter_column("proposal_early_vote", "abstain", server_default=None)


def downgrade() -> None:
    """Downgrade schema: remove abstain column."""
    op.drop_column("proposal_early_vote", "abstain")