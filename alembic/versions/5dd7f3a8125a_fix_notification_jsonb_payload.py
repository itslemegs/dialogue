"""fix_notification_jsonb_payload

Revision ID: 5dd7f3a8125a
Revises: 411269d44e69
Create Date: 2025-09-26 18:03:20.852178

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision: str = '5dd7f3a8125a'
down_revision: Union[str, Sequence[str], None] = '411269d44e69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Convert / normalize to JSONB (and coerce NULL to {}).
    conn.execute(text("""
        ALTER TABLE notification
        ALTER COLUMN payload_json TYPE jsonb
        USING (
          CASE
            WHEN payload_json IS NULL THEN '{}'::jsonb

            -- Already json/jsonb → just cast to jsonb
            WHEN pg_typeof(payload_json)::text IN ('json', 'jsonb')
              THEN payload_json::jsonb

            -- TEXT: if it looks like JSON (starts with { or [), try to parse;
            -- otherwise store as a JSON string value.
            WHEN pg_typeof(payload_json)::text = 'text'
              THEN CASE
                     WHEN payload_json::text ~ '^[\\s]*[\\{\\[]'
                       THEN (payload_json::text)::jsonb
                     ELSE to_jsonb(payload_json::text)
                   END

            -- Fallback: wrap whatever it is as jsonb
            ELSE to_jsonb(payload_json)
          END
        );
    """))

    # Default to {}
    conn.execute(text("""
        ALTER TABLE notification
        ALTER COLUMN payload_json SET DEFAULT '{}'::jsonb
    """))

    # Backfill any remaining NULLs
    conn.execute(text("""
        UPDATE notification
        SET payload_json = '{}'::jsonb
        WHERE payload_json IS NULL
    """))

    # If you want it non-nullable later, uncomment:
    # conn.execute(text("ALTER TABLE notification ALTER COLUMN payload_json SET NOT NULL"))


def downgrade() -> None:
    conn = op.get_bind()
    # If you set NOT NULL above, drop it here first:
    # conn.execute(text("ALTER TABLE notification ALTER COLUMN payload_json DROP NOT NULL"))
    conn.execute(text("""
        ALTER TABLE notification
        ALTER COLUMN payload_json DROP DEFAULT
    """))
    # (Optional) revert type if your previous schema used TEXT
    # conn.execute(text("""
    #     ALTER TABLE notification
    #     ALTER COLUMN payload_json TYPE text
    #     USING payload_json::text
    # """))