"""add proposal_early_vote table

Revision ID: cc55eec76df6
Revises: b26882644271
Create Date: 2025-12-07 23:41:16.057253

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'cc55eec76df6'
down_revision: Union[str, Sequence[str], None] = 'b26882644271'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add proposal_early_vote and proposal_formal_vote tables."""

    # --- proposal_early_vote ---
    op.create_table(
        "proposal_early_vote",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("amendment_id", sa.Integer(), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("yes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND amendment_id IS NULL) "
            "OR (draft_id IS NULL AND amendment_id IS NOT NULL)",
            name="ck_pev_target_exactly_one",
        ),
        sa.ForeignKeyConstraint(["amendment_id"], ["amendment.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["proposaldraft.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["agendaproposal.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "amendment_id",
            deferrable=True,
            initially="DEFERRED",
            name="uq_pev_amendment",
        ),
        sa.UniqueConstraint(
            "draft_id",
            deferrable=True,
            initially="DEFERRED",
            name="uq_pev_draft",
        ),
    )

    op.create_index(
        op.f("ix_proposal_early_vote_amendment_id"),
        "proposal_early_vote",
        ["amendment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_early_vote_draft_id"),
        "proposal_early_vote",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_early_vote_event_id"),
        "proposal_early_vote",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_early_vote_proposal_id"),
        "proposal_early_vote",
        ["proposal_id"],
        unique=False,
    )

    # --- proposal_formal_vote ---
    op.create_table(
        "proposal_formal_vote",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("amendment_id", sa.Integer(), nullable=True),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("abstain", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND amendment_id IS NULL) "
            "OR (draft_id IS NULL AND amendment_id IS NOT NULL)",
            name="ck_pfv_target_exactly_one",
        ),
        sa.ForeignKeyConstraint(["amendment_id"], ["amendment.id"]),
        sa.ForeignKeyConstraint(["draft_id"], ["proposaldraft.id"]),
        sa.ForeignKeyConstraint(["event_id"], ["event.id"]),
        sa.ForeignKeyConstraint(["proposal_id"], ["agendaproposal.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "amendment_id",
            deferrable=True,
            initially="DEFERRED",
            name="uq_pfv_amendment",
        ),
        sa.UniqueConstraint(
            "draft_id",
            deferrable=True,
            initially="DEFERRED",
            name="uq_pfv_draft",
        ),
    )

    op.create_index(
        op.f("ix_proposal_formal_vote_amendment_id"),
        "proposal_formal_vote",
        ["amendment_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_formal_vote_draft_id"),
        "proposal_formal_vote",
        ["draft_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_formal_vote_event_id"),
        "proposal_formal_vote",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_formal_vote_proposal_id"),
        "proposal_formal_vote",
        ["proposal_id"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema: drop proposal_early_vote and proposal_formal_vote tables."""

    # Drop formal vote indexes + table
    op.drop_index(op.f("ix_proposal_formal_vote_proposal_id"), table_name="proposal_formal_vote")
    op.drop_index(op.f("ix_proposal_formal_vote_event_id"), table_name="proposal_formal_vote")
    op.drop_index(op.f("ix_proposal_formal_vote_draft_id"), table_name="proposal_formal_vote")
    op.drop_index(op.f("ix_proposal_formal_vote_amendment_id"), table_name="proposal_formal_vote")
    op.drop_table("proposal_formal_vote")

    # Drop early vote indexes + table
    op.drop_index(op.f("ix_proposal_early_vote_proposal_id"), table_name="proposal_early_vote")
    op.drop_index(op.f("ix_proposal_early_vote_event_id"), table_name="proposal_early_vote")
    op.drop_index(op.f("ix_proposal_early_vote_draft_id"), table_name="proposal_early_vote")
    op.drop_index(op.f("ix_proposal_early_vote_amendment_id"), table_name="proposal_early_vote")
    op.drop_table("proposal_early_vote")