"""fix add passcode

Revision ID: 9cff8a2a3c85
Revises: dfff8716d211
Create Date: 2026-04-15 04:26:04.936989

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9cff8a2a3c85'
down_revision: Union[str, Sequence[str], None] = 'dfff8716d211'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(insp, table_name: str) -> bool:
    return table_name in insp.get_table_names()


def _column_exists(insp, table_name: str, column_name: str) -> bool:
    return column_name in {c["name"] for c in insp.get_columns(table_name)}


def _index_exists(insp, table_name: str, index_name: str) -> bool:
    return index_name in {i["name"] for i in insp.get_indexes(table_name)}


def _fk_exists(insp, table_name: str, fk_name: str) -> bool:
    return fk_name in {fk["name"] for fk in insp.get_foreign_keys(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- event table ---
    if not _column_exists(insp, "event", "access_mode"):
        op.add_column(
            "event",
            sa.Column("access_mode", sa.String(length=16), nullable=False, server_default="open"),
        )
        op.alter_column("event", "access_mode", server_default=None)

    if not _column_exists(insp, "event", "passcode_hash"):
        op.add_column(
            "event",
            sa.Column("passcode_hash", sa.String(length=255), nullable=True),
        )

    if not _column_exists(insp, "event", "created_by_id"):
        op.add_column(
            "event",
            sa.Column("created_by_id", sa.Integer(), nullable=True),
        )

    # refresh inspector after structural changes
    insp = sa.inspect(bind)

    if not _index_exists(insp, "event", "ix_event_access_mode"):
        op.create_index("ix_event_access_mode", "event", ["access_mode"], unique=False)

    if not _index_exists(insp, "event", "ix_event_created_by_id"):
        op.create_index("ix_event_created_by_id", "event", ["created_by_id"], unique=False)

    if _column_exists(insp, "event", "created_by_id") and not _fk_exists(insp, "event", "fk_event_created_by_id_user"):
        op.create_foreign_key(
            "fk_event_created_by_id_user",
            source_table="event",
            referent_table="user",
            local_cols=["created_by_id"],
            remote_cols=["id"],
        )

    # --- event_access_grant table ---
    if not _table_exists(insp, "event_access_grant"):
        op.create_table(
            "event_access_grant",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("event_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column(
                "granted_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
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
            sa.UniqueConstraint("event_id", "user_id", name="uq_event_access_grant"),
        )

    # refresh inspector again
    insp = sa.inspect(bind)

    if _table_exists(insp, "event_access_grant"):
        if not _index_exists(insp, "event_access_grant", "ix_event_access_grant_event_id"):
            op.create_index(
                "ix_event_access_grant_event_id",
                "event_access_grant",
                ["event_id"],
                unique=False,
            )
        if not _index_exists(insp, "event_access_grant", "ix_event_access_grant_user_id"):
            op.create_index(
                "ix_event_access_grant_user_id",
                "event_access_grant",
                ["user_id"],
                unique=False,
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if _table_exists(insp, "event_access_grant"):
        if _index_exists(insp, "event_access_grant", "ix_event_access_grant_user_id"):
            op.drop_index("ix_event_access_grant_user_id", table_name="event_access_grant")
        if _index_exists(insp, "event_access_grant", "ix_event_access_grant_event_id"):
            op.drop_index("ix_event_access_grant_event_id", table_name="event_access_grant")
        op.drop_table("event_access_grant")

    insp = sa.inspect(bind)

    if _fk_exists(insp, "event", "fk_event_created_by_id_user"):
        op.drop_constraint("fk_event_created_by_id_user", "event", type_="foreignkey")

    if _index_exists(insp, "event", "ix_event_created_by_id"):
        op.drop_index("ix_event_created_by_id", table_name="event")

    if _index_exists(insp, "event", "ix_event_access_mode"):
        op.drop_index("ix_event_access_mode", table_name="event")

    if _column_exists(insp, "event", "created_by_id"):
        op.drop_column("event", "created_by_id")

    if _column_exists(insp, "event", "passcode_hash"):
        op.drop_column("event", "passcode_hash")

    if _column_exists(insp, "event", "access_mode"):
        op.drop_column("event", "access_mode")