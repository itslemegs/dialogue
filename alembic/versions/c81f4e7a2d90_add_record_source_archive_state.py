"""add record source archive state

Revision ID: c81f4e7a2d90
Revises: 6f10c2a8d4e1
Create Date: 2026-09-30

"""

from alembic import op
import sqlalchemy as sa


revision = "c81f4e7a2d90"
down_revision = "6f10c2a8d4e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "record_source_archive_state",
        sa.Column(
            "id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "source_key",
            sa.String(length=16),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "closed_by_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_key",
            "event_id",
            name=(
                "uq_record_source_archive_state_"
                "source_event"
            ),
        ),
    )

    op.create_index(
        "ix_record_source_archive_state_source_key",
        "record_source_archive_state",
        ["source_key"],
        unique=False,
    )

    op.create_index(
        "ix_record_source_archive_state_event_id",
        "record_source_archive_state",
        ["event_id"],
        unique=False,
    )

    op.create_index(
        "ix_record_source_archive_state_closed_by_id",
        "record_source_archive_state",
        ["closed_by_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_record_source_archive_state_closed_by_id",
        table_name="record_source_archive_state",
    )

    op.drop_index(
        "ix_record_source_archive_state_event_id",
        table_name="record_source_archive_state",
    )

    op.drop_index(
        "ix_record_source_archive_state_source_key",
        table_name="record_source_archive_state",
    )

    op.drop_table(
        "record_source_archive_state"
    )
