"""remove decision_points_json from question

Revision ID: a8dad0140810
Revises: 0a9c9a048b97
Create Date: 2025-09-17 18:42:00.816679

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a8dad0140810'
down_revision: Union[str, Sequence[str], None] = '0a9c9a048b97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('question', 'decision_points_json')


def downgrade() -> None:
    op.add_column('question', sa.Column('decision_points_json', postgresql.JSONB(astext_type=sa.TEXT()), nullable=True))