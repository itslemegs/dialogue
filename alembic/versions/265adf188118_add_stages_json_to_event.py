"""add stages_json to event

Revision ID: 265adf188118
Revises: d2a5201adf5c
Create Date: 2025-09-17 16:06:20.253449

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '265adf188118'
down_revision: Union[str, Sequence[str], None] = 'd2a5201adf5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "event",
        sa.Column("stages_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute("UPDATE event SET stages_json = '[]'::jsonb WHERE stages_json IS NULL")


def downgrade() -> None:
    op.drop_column("event", "stages_json")
