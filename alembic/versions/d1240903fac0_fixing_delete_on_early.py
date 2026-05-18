"""fixing delete on early

Revision ID: d1240903fac0
Revises: 03c02637dec3
Create Date: 2026-02-26 17:22:30.384787

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1240903fac0'
down_revision: Union[str, Sequence[str], None] = '03c02637dec3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "proposal_early_vote_proposal_id_fkey",
        "proposal_early_vote",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "proposal_early_vote_proposal_id_fkey",
        source_table="proposal_early_vote",
        referent_table="agendaproposal",
        local_cols=["proposal_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "proposal_early_vote_proposal_id_fkey",
        "proposal_early_vote",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "proposal_early_vote_proposal_id_fkey",
        source_table="proposal_early_vote",
        referent_table="agendaproposal",
        local_cols=["proposal_id"],
        remote_cols=["id"],
    )