"""Add manual event archive state.

Revision ID: 6f10c2a8d4e1
Revises: 4b83d27e91af
"""

from alembic import op
import sqlalchemy as sa


revision = "6f10c2a8d4e1"
down_revision = "4b83d27e91af"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_archive_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column(
            "closed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("closed_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_id"],
            ["user.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            name="uq_event_archive_state_event_id",
        ),
    )

    op.create_index(
        "ix_event_archive_state_event_id",
        "event_archive_state",
        ["event_id"],
    )

    op.create_index(
        "ix_event_archive_state_closed_by_id",
        "event_archive_state",
        ["closed_by_id"],
    )


def downgrade():
    op.drop_index(
        "ix_event_archive_state_closed_by_id",
        table_name="event_archive_state",
    )
    op.drop_index(
        "ix_event_archive_state_event_id",
        table_name="event_archive_state",
    )
    op.drop_table("event_archive_state")
