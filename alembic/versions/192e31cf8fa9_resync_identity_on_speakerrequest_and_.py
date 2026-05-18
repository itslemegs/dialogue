"""resync identity on speakerrequest (and friends)

Revision ID: 192e31cf8fa9
Revises: 8728bb495184
Create Date: 2025-09-17 19:00:15.278186

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '192e31cf8fa9'
down_revision: Union[str, Sequence[str], None] = '8728bb495184'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "speakerrequest",
    # add others if needed:
    "floorstate", "event", "eventstage", "agendaproposal", "question", "proposalroom"
]

def _restart_identity(table: str, col: str = "id"):
    conn = op.get_bind()

    # 1) compute next value as max(id)+1 (or 1 if empty)
    next_id = conn.execute(
        sa.text(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table}")
    ).scalar_one()

    # 2) detect if it's an IDENTITY column
    is_identity = conn.execute(sa.text("""
        SELECT c.is_identity = 'YES'
        FROM information_schema.columns c
        WHERE c.table_name = :t AND c.column_name = :c
    """), {"t": table, "c": col}).scalar()

    if is_identity:
        # Must inline the literal, parameters are NOT allowed here
        conn.exec_driver_sql(f"ALTER TABLE {table} ALTER COLUMN {col} RESTART WITH {int(next_id)}")
    else:
        # Older SERIAL/sequence style – bump the underlying sequence
        seq = conn.execute(sa.text("""
            SELECT pg_get_serial_sequence(:tbl, :col)
        """), {"tbl": table, "col": col}).scalar()
        if not seq:
            # Nothing to do (no identity and no sequence)
            return
        # setval(seq, value, is_called)
        # Using is_called=false makes the next nextval() return EXACTLY next_id
        conn.execute(sa.text("SELECT setval(:seq, :val, false)"), {"seq": seq, "val": int(next_id)})


def upgrade() -> None:
    for t in TABLES:
        _restart_identity(t)


def downgrade() -> None:
    """Downgrade schema."""
    pass
