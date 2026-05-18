"""fix_general_floor_link_fk_to_question

Revision ID: 89d87af9d6be
Revises: 739b057e79b4
Create Date: 2025-09-26 16:47:39.305645

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89d87af9d6be'
down_revision: Union[str, Sequence[str], None] = '739b057e79b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the wrong FK if it exists
    conn = op.get_bind()
    # name might differ; try to drop by detecting it
    fk_name = conn.exec_driver_sql("""
        SELECT tc.constraint_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
        WHERE tc.table_name = 'general_floor_link'
          AND tc.constraint_type = 'FOREIGN KEY'
          AND kcu.column_name = 'question_id'
    """).scalar()
    if fk_name:
        op.drop_constraint(fk_name, "general_floor_link", type_="foreignkey")

    # Recreate pointing to question.id
    op.create_foreign_key(
        "fk_gfl_question",
        "general_floor_link",
        "question",
        ["question_id"],
        ["id"],
        ondelete="CASCADE",
    )

def downgrade() -> None:
    op.drop_constraint("fk_gfl_question", "general_floor_link", type_="foreignkey")
    # (optionally recreate the old, wrong FK — usually not worth doing)