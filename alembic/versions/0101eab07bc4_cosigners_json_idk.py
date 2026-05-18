"""cosigners json idk

Revision ID: 0101eab07bc4
Revises: 37e9aee3d790
Create Date: 2025-09-26 20:01:50.047094

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


# revision identifiers, used by Alembic.
revision: str = '0101eab07bc4'
down_revision: Union[str, Sequence[str], None] = '37e9aee3d790'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE proposaldraft
        ALTER COLUMN cosigners_json TYPE jsonb
        USING (
          CASE
            WHEN cosigners_json IS NULL THEN '[]'::jsonb
            WHEN pg_typeof(cosigners_json)::text IN ('json','jsonb') THEN cosigners_json::jsonb
            WHEN pg_typeof(cosigners_json)::text = 'text'
              THEN COALESCE(NULLIF(cosigners_json::text, '')::jsonb, '[]'::jsonb)
            ELSE to_jsonb(cosigners_json)
          END
        );
        ALTER TABLE proposaldraft
          ALTER COLUMN cosigners_json SET DEFAULT '[]'::jsonb;
        UPDATE proposaldraft
           SET cosigners_json = '[]'::jsonb
         WHERE cosigners_json IS NULL;
        -- Optional: enforce not null
        -- ALTER TABLE proposaldraft ALTER COLUMN cosigners_json SET NOT NULL;
    """))


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(text("""
        ALTER TABLE proposaldraft
        ALTER COLUMN cosigners_json DROP DEFAULT
    """))
