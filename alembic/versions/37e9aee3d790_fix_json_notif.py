"""fix_json_notif

Revision ID: 37e9aee3d790
Revises: 5dd7f3a8125a
Create Date: 2025-09-26 18:24:37.772136

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision: str = '37e9aee3d790'
down_revision: Union[str, Sequence[str], None] = '5dd7f3a8125a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Ensure jsonb type & default remain correct (no-op if already done)
    conn.execute(text("""
        ALTER TABLE notification
        ALTER COLUMN payload_json TYPE jsonb
        USING (
          CASE
            WHEN payload_json IS NULL THEN '{}'::jsonb
            WHEN pg_typeof(payload_json)::text IN ('json','jsonb') THEN payload_json::jsonb
            WHEN pg_typeof(payload_json)::text = 'text' THEN to_jsonb(payload_json::text)
            ELSE to_jsonb(payload_json)
          END
        );
    """))
    conn.execute(text("""
        ALTER TABLE notification
        ALTER COLUMN payload_json SET DEFAULT '{}'::jsonb;
        UPDATE notification SET payload_json = '{}'::jsonb WHERE payload_json IS NULL;
    """))

    # For rows where payload_json is a JSONB string that *looks like* JSON,
    # parse it into an object/array; else set to {}.
    conn.execute(text("""
        -- Parse JSON-looking strings (leading { or [)
        UPDATE notification
        SET payload_json = ((payload_json #>> '{}')::jsonb)
        WHERE jsonb_typeof(payload_json) = 'string'
          AND (payload_json #>> '{}') ~ '^[\\s]*[\\{\\[]';

        -- Non-JSON strings → {}
        UPDATE notification
        SET payload_json = '{}'::jsonb
        WHERE jsonb_typeof(payload_json) = 'string';
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("ALTER TABLE notification ALTER COLUMN payload_json DROP DEFAULT"))
    # (keep as jsonb; no lossy downgrade)