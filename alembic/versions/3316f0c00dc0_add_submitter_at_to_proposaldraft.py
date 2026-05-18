"""add submitter_at to proposaldraft

Revision ID: 3316f0c00dc0
Revises: 0101eab07bc4
Create Date: 2025-09-29 01:19:47.302355

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3316f0c00dc0'
down_revision: Union[str, Sequence[str], None] = '0101eab07bc4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proposaldraft",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proposaldraft", "submitted_at")
