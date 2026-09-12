"""reset live summary rows

Revision ID: d425dddce4b6
Revises: 18ec2e296a9b
Create Date: 2026-06-10 04:18:04.216403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd425dddce4b6'
down_revision: Union[str, Sequence[str], None] = '18ec2e296a9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    live_summary = sa.sql.table(
        "live_summary",
        sa.sql.column("summary", sa.Text),
        sa.sql.column("last_item_id", sa.Integer),
        sa.sql.column("dirty", sa.Boolean),
    )

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
