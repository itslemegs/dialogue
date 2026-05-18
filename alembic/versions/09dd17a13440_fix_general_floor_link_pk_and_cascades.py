"""fix general_floor_link pk and cascades

Revision ID: 09dd17a13440
Revises: 4b6d58097187
Create Date: 2025-09-17 16:47:25.582292

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '09dd17a13440'
down_revision: Union[str, Sequence[str], None] = '4b6d58097187'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("general_floor_link")

    op.create_table(
        "general_floor_link",
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), primary_key=True, nullable=False),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("question.id", ondelete="CASCADE"), nullable=False, unique=True),
    )


def downgrade() -> None:
    op.drop_table("general_floor_link")
