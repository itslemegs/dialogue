"""backfill full symbols

Revision ID: 5d0a13dc31b6
Revises: 3316f0c00dc0
Create Date: 2025-09-29 01:52:10.289727

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d0a13dc31b6'
down_revision: Union[str, Sequence[str], None] = '3316f0c00dc0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        UPDATE proposaldraft d
        SET l_number = 'A/' ||
                       COALESCE(EXTRACT(YEAR FROM e.starts_at)::text,
                                EXTRACT(YEAR FROM d.created_at)::text) ||
                       '/' || d.event_id || '/L.' ||
                       COALESCE( (regexp_match(d.l_number, '([0-9]+)'))[1], '0')
        FROM event e
        WHERE e.id = d.event_id
          AND d.l_number ~ '^L\\.[0-9]+$'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE proposaldraft
        SET l_number = 'L.' || COALESCE((regexp_match(l_number, 'L\\.([0-9]+)'))[1], '0')
        WHERE l_number LIKE 'A/%/L.%'
    """)
