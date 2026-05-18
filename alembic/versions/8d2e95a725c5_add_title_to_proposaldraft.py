"""add title to proposaldraft

Revision ID: 8d2e95a725c5
Revises: 5d0a13dc31b6
Create Date: 2025-09-29 10:06:33.995058

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8d2e95a725c5'
down_revision: Union[str, Sequence[str], None] = '5d0a13dc31b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "proposaldraft",
        sa.Column("title", sa.String(length=200), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("proposaldraft", "title")
