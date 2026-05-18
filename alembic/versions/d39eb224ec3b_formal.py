"""formal

Revision ID: d39eb224ec3b
Revises: 72222adb11ed
Create Date: 2025-12-08 08:54:08.550620

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd39eb224ec3b'
down_revision: Union[str, Sequence[str], None] = '72222adb11ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add formal_vote_id to proposal_formal_ballot."""
    op.add_column(
        "proposal_formal_ballot",
        sa.Column("formal_vote_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "proposal_formal_ballot_formal_vote_id_fkey",
        "proposal_formal_ballot",
        "proposal_formal_vote",
        ["formal_vote_id"],
        ["id"],
    )


def downgrade() -> None:
    """Downgrade schema: drop formal_vote_id from proposal_formal_ballot."""
    op.drop_constraint(
        "proposal_formal_ballot_formal_vote_id_fkey",
        "proposal_formal_ballot",
        type_="foreignkey",
    )
    op.drop_column("proposal_formal_ballot", "formal_vote_id")