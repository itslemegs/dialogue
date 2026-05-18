"""pf not global

Revision ID: b2895dc8e5dc
Revises: c194d18c5abc
Create Date: 2025-10-22 12:33:03.798278

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2895dc8e5dc'
down_revision: Union[str, Sequence[str], None] = 'c194d18c5abc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proposal_intervention",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("amendment_id", sa.Integer, sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("by_user", sa.Integer, sa.ForeignKey("user.id"), nullable=False, index=True),
        sa.Column("local_no", sa.Integer, nullable=True, index=True),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("proposal_intervention.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("event_id","proposal_id","draft_id","amendment_id","local_no", name="uq_pfi_scope_local"),
    )


def downgrade() -> None:
    op.drop_table("proposal_intervention")
