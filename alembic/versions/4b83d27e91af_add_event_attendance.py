"""Add event attendance tracking.

Revision ID: 4b83d27e91af
Revises: 9d7f3b1a6c20
"""
from alembic import op
import sqlalchemy as sa


revision = "4b83d27e91af"
down_revision = "9d7f3b1a6c20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_attendance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "user_id",
            name="uq_event_attendance_event_user",
        ),
    )

    op.create_index(
        "ix_event_attendance_event_id",
        "event_attendance",
        ["event_id"],
    )

    op.create_index(
        "ix_event_attendance_user_id",
        "event_attendance",
        ["user_id"],
    )


def downgrade():
    op.drop_index(
        "ix_event_attendance_user_id",
        table_name="event_attendance",
    )
    op.drop_index(
        "ix_event_attendance_event_id",
        table_name="event_attendance",
    )
    op.drop_table("event_attendance")
