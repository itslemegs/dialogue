"""add draft translation cache

Revision ID: 18ec2e296a9b
Revises: 51c090fea9de
Create Date: 2026-05-17 23:26:40.394796

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '18ec2e296a9b'
down_revision: Union[str, Sequence[str], None] = '51c090fea9de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "drafttranslation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("lang", sa.String(length=10), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title_show", sa.Text(), nullable=True),
        sa.Column(
            "draft_text_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposaldraft.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "lang",
            name="uq_drafttranslation_draft_lang",
        ),
    )

    op.create_index(
        "ix_drafttranslation_draft_id",
        "drafttranslation",
        ["draft_id"],
        unique=False,
    )

    op.create_index(
        "ix_drafttranslation_lang",
        "drafttranslation",
        ["lang"],
        unique=False,
    )

    op.create_index(
        "ix_drafttranslation_source_hash",
        "drafttranslation",
        ["source_hash"],
        unique=False,
    )

    op.create_index(
        "ix_drafttranslation_status",
        "drafttranslation",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_drafttranslation_status", table_name="drafttranslation")
    op.drop_index("ix_drafttranslation_source_hash", table_name="drafttranslation")
    op.drop_index("ix_drafttranslation_lang", table_name="drafttranslation")
    op.drop_index("ix_drafttranslation_draft_id", table_name="drafttranslation")
    op.drop_table("drafttranslation")