"""remove stages_json from models

Revision ID: b426683e86f0
Revises: 50b00e80fc58
Create Date: 2025-09-17 17:02:22.237577

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b426683e86f0'
down_revision: Union[str, Sequence[str], None] = '50b00e80fc58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, col: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == col for c in insp.get_columns(table))

def upgrade() -> None:
    if _has_column("event", "stages_json"):
        op.drop_column("event", "stages_json")


def downgrade() -> None:
    if not _has_column("event", "stages_json"):
        op.add_column("event", sa.Column("stages_json", postgresql.JSONB, nullable=True))