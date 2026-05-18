"""fixe add passcode

Revision ID: 563488c6c428
Revises: 9cff8a2a3c85
Create Date: 2026-04-15 04:44:45.440843

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '563488c6c428'
down_revision: Union[str, Sequence[str], None] = '9cff8a2a3c85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # -----------------------------
    # event: access control columns
    # -----------------------------
    op.execute("""
    ALTER TABLE event
      ADD COLUMN IF NOT EXISTS access_mode VARCHAR(16) NOT NULL DEFAULT 'open';
    """)

    op.execute("""
    ALTER TABLE event
      ADD COLUMN IF NOT EXISTS passcode_hash VARCHAR(255);
    """)

    op.execute("""
    ALTER TABLE event
      ADD COLUMN IF NOT EXISTS created_by_id INTEGER;
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_event_access_mode ON event (access_mode);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_event_created_by_id ON event (created_by_id);
    """)

    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_event_created_by_id_user'
      ) THEN
        ALTER TABLE event
          ADD CONSTRAINT fk_event_created_by_id_user
          FOREIGN KEY (created_by_id)
          REFERENCES "user"(id)
          ON DELETE SET NULL;
      END IF;
    END $$;
    """)

    # ----------------------------------
    # event_access_grant: passcode access
    # ----------------------------------
    op.execute("""
    CREATE TABLE IF NOT EXISTS event_access_grant (
      id SERIAL PRIMARY KEY,
      event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
      granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      CONSTRAINT uq_event_access_grant UNIQUE (event_id, user_id)
    );
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_event_access_grant_event_id
      ON event_access_grant (event_id);
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_event_access_grant_user_id
      ON event_access_grant (user_id);
    """)

    # ----------------------------------
    # question.event_id (missing column)
    # ----------------------------------
    op.execute("""
    ALTER TABLE question
      ADD COLUMN IF NOT EXISTS event_id INTEGER;
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS ix_question_event_id
      ON question (event_id);
    """)

    op.execute("""
    DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_question_event_id_event'
      ) THEN
        ALTER TABLE question
          ADD CONSTRAINT fk_question_event_id_event
          FOREIGN KEY (event_id)
          REFERENCES event(id)
          ON DELETE SET NULL;
      END IF;
    END $$;
    """)

    # Backfill legacy general-floor questions from GeneralFloorLink -> AgendaProposal
    op.execute("""
    DO $$
    BEGIN
      IF to_regclass('general_floor_link') IS NOT NULL THEN
        UPDATE question q
        SET event_id = ap.event_id
        FROM general_floor_link gfl
        JOIN agendaproposal ap
          ON ap.id = gfl.proposal_id
        WHERE gfl.question_id = q.id
          AND q.event_id IS NULL;
      END IF;
    END $$;
    """)


def downgrade():
    op.execute("""
    ALTER TABLE question
      DROP CONSTRAINT IF EXISTS fk_question_event_id_event;
    """)
    op.execute("""
    DROP INDEX IF EXISTS ix_question_event_id;
    """)
    op.execute("""
    ALTER TABLE question
      DROP COLUMN IF EXISTS event_id;
    """)

    op.execute("""
    DROP TABLE IF EXISTS event_access_grant;
    """)

    op.execute("""
    ALTER TABLE event
      DROP CONSTRAINT IF EXISTS fk_event_created_by_id_user;
    """)
    op.execute("""
    DROP INDEX IF EXISTS ix_event_created_by_id;
    """)
    op.execute("""
    DROP INDEX IF EXISTS ix_event_access_mode;
    """)
    op.execute("""
    ALTER TABLE event
      DROP COLUMN IF EXISTS created_by_id;
    """)
    op.execute("""
    ALTER TABLE event
      DROP COLUMN IF EXISTS passcode_hash;
    """)
    op.execute("""
    ALTER TABLE event
      DROP COLUMN IF EXISTS access_mode;
    """)