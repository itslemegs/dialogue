"""fixing delete on formal

Revision ID: f683a056d5df
Revises: d1240903fac0
Create Date: 2026-02-26 17:29:24.327553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f683a056d5df'
down_revision: Union[str, Sequence[str], None] = 'd1240903fac0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "proposal_formal_vote_proposal_id_fkey",
        "proposal_formal_vote",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "proposal_formal_vote_proposal_id_fkey",
        source_table="proposal_formal_vote",
        referent_table="agendaproposal",
        local_cols=["proposal_id"],
        remote_cols=["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "proposal_formal_vote_proposal_id_fkey",
        "proposal_formal_vote",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "proposal_formal_vote_proposal_id_fkey",
        source_table="proposal_formal_vote",
        referent_table="agendaproposal",
        local_cols=["proposal_id"],
        remote_cols=["id"],
    )