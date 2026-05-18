"""fix user id sequence

Revision ID: b26882644271
Revises: b2895dc8e5dc
Create Date: 2025-11-17 11:04:25.894440

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b26882644271'
down_revision: Union[str, Sequence[str], None] = 'b2895dc8e5dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    SELECT setval(
        pg_get_serial_sequence('"user"', 'id'),
        COALESCE((SELECT MAX(id) FROM "user"), 1),
        true
    );
    """)


def downgrade() -> None:
    """Downgrade schema."""
    pass
