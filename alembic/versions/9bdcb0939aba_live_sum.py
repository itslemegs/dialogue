"""live sum

Revision ID: 9bdcb0939aba
Revises: f683a056d5df
Create Date: 2026-02-27 11:08:30.549014

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9bdcb0939aba'
down_revision: Union[str, Sequence[str], None] = 'f683a056d5df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "live_summary",
        sa.Column("scope_key", sa.String(length=200), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),

        sa.Column("question_id", sa.Integer(), sa.ForeignKey("question.id"), nullable=True),
        sa.Column("room_id", sa.Integer(), sa.ForeignKey("proposalroom.id"), nullable=True),

        sa.Column("event_id", sa.Integer(), sa.ForeignKey("event.id"), nullable=True),
        sa.Column("proposal_id", sa.Integer(), sa.ForeignKey("agendaproposal.id"), nullable=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("proposaldraft.id"), nullable=True),
        sa.Column("amendment_id", sa.Integer(), sa.ForeignKey("amendment.id"), nullable=True),

        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_item_id", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dirty", sa.Boolean(), nullable=False, server_default=sa.text("false")),

        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index("ix_live_summary_kind", "live_summary", ["kind"], unique=False)
    op.create_index("ix_live_summary_question_id", "live_summary", ["question_id"], unique=False)
    op.create_index("ix_live_summary_room_id", "live_summary", ["room_id"], unique=False)
    op.create_index("ix_live_summary_event_id", "live_summary", ["event_id"], unique=False)
    op.create_index("ix_live_summary_proposal_id", "live_summary", ["proposal_id"], unique=False)
    op.create_index("ix_live_summary_draft_id", "live_summary", ["draft_id"], unique=False)
    op.create_index("ix_live_summary_amendment_id", "live_summary", ["amendment_id"], unique=False)
    op.create_index("ix_live_summary_dirty", "live_summary", ["dirty"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_live_summary_dirty", table_name="live_summary")
    op.drop_index("ix_live_summary_amendment_id", table_name="live_summary")
    op.drop_index("ix_live_summary_draft_id", table_name="live_summary")
    op.drop_index("ix_live_summary_proposal_id", table_name="live_summary")
    op.drop_index("ix_live_summary_event_id", table_name="live_summary")
    op.drop_index("ix_live_summary_room_id", table_name="live_summary")
    op.drop_index("ix_live_summary_question_id", table_name="live_summary")
    op.drop_index("ix_live_summary_kind", table_name="live_summary")
    op.drop_table("live_summary")