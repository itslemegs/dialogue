"""Add event Chairman of Record assignment history.

Revision ID: 9d7f3b1a6c20
Revises: c2a94b6d810f
"""
from alembic import op
import sqlalchemy as sa


revision = "9d7f3b1a6c20"
down_revision = "c2a94b6d810f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_chair_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("chairman_user_id", sa.Integer(), nullable=False),
        sa.Column("assigned_by_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),

        sa.ForeignKeyConstraint(
            ["event_id"],
            ["event.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chairman_user_id"],
            ["user.id"],
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_id"],
            ["user.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_event_chair_assignment_event_id",
        "event_chair_assignment",
        ["event_id"],
    )

    op.create_index(
        "ix_event_chair_assignment_chairman_user_id",
        "event_chair_assignment",
        ["chairman_user_id"],
    )

    op.create_index(
        "ix_event_chair_assignment_assigned_by_id",
        "event_chair_assignment",
        ["assigned_by_id"],
    )

    # PostgreSQL guarantees that an event can have only one active
    # Chairman of Record at a time.
    op.create_index(
        "uq_event_chair_assignment_active",
        "event_chair_assignment",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade():
    op.drop_index(
        "uq_event_chair_assignment_active",
        table_name="event_chair_assignment",
    )
    op.drop_index(
        "ix_event_chair_assignment_assigned_by_id",
        table_name="event_chair_assignment",
    )
    op.drop_index(
        "ix_event_chair_assignment_chairman_user_id",
        table_name="event_chair_assignment",
    )
    op.drop_index(
        "ix_event_chair_assignment_event_id",
        table_name="event_chair_assignment",
    )
    op.drop_table("event_chair_assignment")
