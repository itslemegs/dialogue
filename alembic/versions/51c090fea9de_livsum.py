"""livsum

Revision ID: 51c090fea9de
Revises: 4ef9153c47dd
Create Date: 2026-04-15 08:53:50.125563

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '51c090fea9de'
down_revision: Union[str, Sequence[str], None] = '4ef9153c47dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_constraint("live_summary_room_id_fkey", "live_summary", type_="foreignkey")
    op.create_foreign_key(
        "live_summary_room_id_fkey",
        "live_summary",
        "proposalroom",
        ["room_id"],
        ["id"],
        ondelete="CASCADE",
    )

def downgrade():
    op.drop_constraint("live_summary_room_id_fkey", "live_summary", type_="foreignkey")
    op.create_foreign_key(
        "live_summary_room_id_fkey",
        "live_summary",
        "proposalroom",
        ["room_id"],
        ["id"],
    )