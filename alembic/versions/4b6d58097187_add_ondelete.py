"""add ondelete

Revision ID: 4b6d58097187
Revises: 265adf188118
Create Date: 2025-09-17 16:42:11.836816

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4b6d58097187'
down_revision: Union[str, Sequence[str], None] = '265adf188118'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # proposalroom.proposal_id
    op.drop_constraint("proposalroom_proposal_id_fkey", "proposalroom", type_="foreignkey")
    op.create_foreign_key(
        "proposalroom_proposal_id_fkey",
        "proposalroom", "agendaproposal",
        ["proposal_id"], ["id"],
        ondelete="CASCADE",
    )

    # proposalroom.event_id
    op.drop_constraint("proposalroom_event_id_fkey", "proposalroom", type_="foreignkey")
    op.create_foreign_key(
        "proposalroom_event_id_fkey",
        "proposalroom", "event",
        ["event_id"], ["id"],
        ondelete="CASCADE",
    )

    # proposaldraft.proposal_id
    op.drop_constraint("proposaldraft_proposal_id_fkey", "proposaldraft", type_="foreignkey")
    op.create_foreign_key(
        "proposaldraft_proposal_id_fkey",
        "proposaldraft", "agendaproposal",
        ["proposal_id"], ["id"],
        ondelete="CASCADE",
    )

    # general_floor_link.proposal_id
    op.drop_constraint("general_floor_link_proposal_id_fkey", "general_floor_link", type_="foreignkey")
    op.create_foreign_key(
        "general_floor_link_proposal_id_fkey",
        "general_floor_link", "agendaproposal",
        ["proposal_id"], ["id"],
        ondelete="CASCADE",
    )

    # general_floor_link.question_id
    op.drop_constraint("general_floor_link_question_id_fkey", "general_floor_link", type_="foreignkey")
    op.create_foreign_key(
        "general_floor_link_question_id_fkey",
        "general_floor_link", "question",
        ["question_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass
