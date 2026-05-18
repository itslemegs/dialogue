"""add status fields to proposaldraft and create amendment table

Revision ID: 57e90774854d
Revises: 8d2e95a725c5
Create Date: 2025-09-29 11:28:58.822609

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '57e90774854d'
down_revision: Union[str, Sequence[str], None] = '8d2e95a725c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def _has_table(insp, name: str) -> bool:
    return insp.has_table(name)


def _has_column(insp, table: str, column: str) -> bool:
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def _has_index(insp, table: str, index_name: str) -> bool:
    try:
        idx = insp.get_indexes(table)
    except Exception:
        return False
    names = {i.get("name") for i in idx}
    return index_name in names


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- proposaldraft: add columns if missing ---
    if _has_table(insp, "proposaldraft"):
        if not _has_column(insp, "proposaldraft", "status"):
            op.add_column(
                "proposaldraft",
                sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'TABLED'")),
            )
            op.create_index("ix_proposaldraft_status", "proposaldraft", ["status"])
        else:
            # make sure index exists
            if not _has_index(insp, "proposaldraft", "ix_proposaldraft_status"):
                op.create_index("ix_proposaldraft_status", "proposaldraft", ["status"])

        if not _has_column(insp, "proposaldraft", "withdrawn_at"):
            op.add_column("proposaldraft", sa.Column("withdrawn_at", sa.DateTime(timezone=True)))
        if not _has_column(insp, "proposaldraft", "reintroduced_by_id"):
            op.add_column("proposaldraft", sa.Column("reintroduced_by_id", sa.Integer()))
            op.create_foreign_key(
                "fk_proposaldraft_reintroduced_by_user",
                source_table="proposaldraft",
                referent_table="user",
                local_cols=["reintroduced_by_id"],
                remote_cols=["id"],
                ondelete=None,
            )
        if not _has_column(insp, "proposaldraft", "reintroduced_at"):
            op.add_column("proposaldraft", sa.Column("reintroduced_at", sa.DateTime(timezone=True)))

    # --- amendment table: create if missing ---
    if not _has_table(insp, "amendment"):
        op.create_table(
            "amendment",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("draft_id", sa.Integer(), nullable=False),
            sa.Column("am_no", sa.Integer(), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("submitted_by_id", sa.Integer(), nullable=False),
            sa.Column("body_markdown", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["draft_id"], ["proposaldraft.id"], name="fk_amendment_draft", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["submitted_by_id"], ["user.id"], name="fk_amendment_submitted_by", ondelete=None),
            sa.UniqueConstraint("draft_id", "am_no", name="uq_amendment_draft_amno"),
        )
        # create indexes explicitly
        op.create_index("ix_amendment_draft_id", "amendment", ["draft_id"])
        op.create_index("ix_amendment_submitted_by_id", "amendment", ["submitted_by_id"])
    else:
        # table exists; ensure indexes exist
        if not _has_index(insp, "amendment", "ix_amendment_draft_id"):
            op.create_index("ix_amendment_draft_id", "amendment", ["draft_id"])
        if not _has_index(insp, "amendment", "ix_amendment_submitted_by_id"):
            op.create_index("ix_amendment_submitted_by_id", "amendment", ["submitted_by_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # amendment
    if _has_table(insp, "amendment"):
        if _has_index(insp, "amendment", "ix_amendment_submitted_by_id"):
            op.drop_index("ix_amendment_submitted_by_id", table_name="amendment")
        if _has_index(insp, "amendment", "ix_amendment_draft_id"):
            op.drop_index("ix_amendment_draft_id", table_name="amendment")
        op.drop_table("amendment")

    # proposaldraft
    if _has_table(insp, "proposaldraft"):
        if _has_index(insp, "proposaldraft", "ix_proposaldraft_status"):
            op.drop_index("ix_proposaldraft_status", table_name="proposaldraft")
        if _has_column(insp, "proposaldraft", "reintroduced_at"):
            op.drop_column("proposaldraft", "reintroduced_at")
        # drop FK if exists
        try:
            op.drop_constraint("fk_proposaldraft_reintroduced_by_user", "proposaldraft", type_="foreignkey")
        except Exception:
            pass
        if _has_column(insp, "proposaldraft", "reintroduced_by_id"):
            op.drop_column("proposaldraft", "reintroduced_by_id")
        if _has_column(insp, "proposaldraft", "withdrawn_at"):
            op.drop_column("proposaldraft", "withdrawn_at")
        if _has_column(insp, "proposaldraft", "status"):
            op.drop_column("proposaldraft", "status")