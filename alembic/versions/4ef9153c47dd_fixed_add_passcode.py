"""fixed add passcode

Revision ID: 4ef9153c47dd
Revises: 563488c6c428
Create Date: 2026-04-15 04:54:19.143759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4ef9153c47dd'
down_revision: Union[str, Sequence[str], None] = '563488c6c428'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.execute("""
    DO $$
    BEGIN
      -- Old DB had default SQLModel table name; new code expects event_stage
      IF to_regclass('event_stage') IS NULL AND to_regclass('eventstage') IS NOT NULL THEN
        ALTER TABLE eventstage RENAME TO event_stage;
      ELSIF to_regclass('event_stage') IS NULL AND to_regclass('eventstage') IS NULL THEN
        CREATE TABLE event_stage (
          id SERIAL PRIMARY KEY,
          event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
          name VARCHAR NOT NULL,
          starts_at TIMESTAMPTZ NOT NULL,
          ends_at TIMESTAMPTZ NOT NULL
        );
      END IF;
    END $$;
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_event_stage_event_id
    ON event_stage (event_id);
    """)


def downgrade():
    op.execute("""
    DO $$
    BEGIN
      IF to_regclass('eventstage') IS NULL AND to_regclass('event_stage') IS NOT NULL THEN
        ALTER TABLE event_stage RENAME TO eventstage;
      END IF;
    END $$;
    """)