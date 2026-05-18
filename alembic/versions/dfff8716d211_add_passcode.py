"""add passcode

Revision ID: dfff8716d211
Revises: 9bdcb0939aba
Create Date: 2026-04-15 04:17:47.883272

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'dfff8716d211'
down_revision: Union[str, Sequence[str], None] = '9bdcb0939aba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "event",
        sa.Column("access_mode", sa.String(length=16), nullable=False, server_default="open"),
    )
    op.add_column(
        "event",
        sa.Column("passcode_hash", sa.String(length=255), nullable=True),
    )

    # remove server default after backfill
    op.alter_column("event", "access_mode", server_default=None)

def downgrade() -> None:
    op.drop_column("event", "passcode_hash")
    op.drop_column("event", "access_mode")