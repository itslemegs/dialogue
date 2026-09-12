"""reset bad live summaries

Revision ID: 7e95386f37b0
Revises: d425dddce4b6
Create Date: 2026-06-12 03:22:08.493984

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e95386f37b0'
down_revision: Union[str, Sequence[str], None] = 'd425dddce4b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


live_summary = sa.table(
    "live_summary",
    sa.column("scope_key", sa.String),
    sa.column("summary", sa.Text),
    sa.column("last_item_id", sa.Integer),
    sa.column("dirty", sa.Boolean),
)


def upgrade() -> None:
    op.execute(
        live_summary.update().values(
            summary="",
            last_item_id=0,
            dirty=True,
        )
    )

def downgrade() -> None:
    """Downgrade schema."""
    pass
