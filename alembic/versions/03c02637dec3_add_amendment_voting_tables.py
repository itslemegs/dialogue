"""add amendment voting tables

Revision ID: 03c02637dec3
Revises: d39eb224ec3b
Create Date: 2026-01-12 02:05:47.905294

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '03c02637dec3'
down_revision: Union[str, Sequence[str], None] = 'd39eb224ec3b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "amendment_vote_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("amendment_id", sa.Integer(), sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("yes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("abstain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_amendment_vote_state_amendment_id", "amendment_vote_state", ["amendment_id"], unique=True)

    op.create_table(
        "amendment_vote",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("amendment_id", sa.Integer(), sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("choice", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("amendment_id", "user_id", name="uq_amendment_vote_once"),
    )
    op.create_index("ix_amendment_vote_amendment_id", "amendment_vote", ["amendment_id"])
    op.create_index("ix_amendment_vote_user_id", "amendment_vote", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_amendment_vote_user_id", table_name="amendment_vote")
    op.drop_index("ix_amendment_vote_amendment_id", table_name="amendment_vote")
    op.drop_table("amendment_vote")

    op.drop_index("ix_amendment_vote_state_amendment_id", table_name="amendment_vote_state")
    op.drop_table("amendment_vote_state")