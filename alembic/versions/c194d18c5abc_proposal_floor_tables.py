"""proposal floor tables

Revision ID: c194d18c5abc
Revises: 57e90774854d
Create Date: 2025-10-22 10:47:11.248164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c194d18c5abc'
down_revision: Union[str, Sequence[str], None] = '57e90774854d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Speaker queue table (must exist before floor_state FK)
    op.create_table(
        "proposal_speaker_request",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amendment_id", sa.Integer, sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="GENERAL"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="QUEUED"),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column("target_intervention_id", sa.Integer, sa.ForeignKey("intervention.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_psr_event", "proposal_speaker_request", ["event_id"])
    op.create_index("ix_psr_proposal", "proposal_speaker_request", ["proposal_id"])
    op.create_index("ix_psr_draft", "proposal_speaker_request", ["draft_id"])
    op.create_index("ix_psr_amend", "proposal_speaker_request", ["amendment_id"])
    op.create_index("ix_psr_user", "proposal_speaker_request", ["user_id"])

    # 2) Floor state (now we can reference proposal_speaker_request)
    op.create_table(
        "proposal_floor_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amendment_id", sa.Integer, sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_open", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("speaking_time_sec", sa.Integer, nullable=False, server_default="120"),
        sa.Column("current_speaker_request_id", sa.Integer, sa.ForeignKey("proposal_speaker_request.id"), nullable=True),
        sa.Column("early_is_open", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("formal_is_open", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id","proposal_id","draft_id","amendment_id", name="uq_pfloor_scope"),
    )
    op.create_index("ix_pfs_event", "proposal_floor_state", ["event_id"])
    op.create_index("ix_pfs_proposal", "proposal_floor_state", ["proposal_id"])
    op.create_index("ix_pfs_draft", "proposal_floor_state", ["draft_id"])
    op.create_index("ix_pfs_amend", "proposal_floor_state", ["amendment_id"])

    # 3) Early ballot
    op.create_table(
        "proposal_early_ballot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amendment_id", sa.Integer, sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("choice", sa.String(length=8), nullable=False),  # "YES" | "NO"
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id","proposal_id","draft_id","amendment_id","user_id", name="uq_early_ballot_scope_user"),
    )
    op.create_index("ix_peb_event", "proposal_early_ballot", ["event_id"])
    op.create_index("ix_peb_proposal", "proposal_early_ballot", ["proposal_id"])
    op.create_index("ix_peb_draft", "proposal_early_ballot", ["draft_id"])
    op.create_index("ix_peb_amend", "proposal_early_ballot", ["amendment_id"])
    op.create_index("ix_peb_user", "proposal_early_ballot", ["user_id"])

    # 4) Formal ballot
    op.create_table(
        "proposal_formal_ballot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("event.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False),
        sa.Column("draft_id", sa.Integer, sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"), nullable=True),
        sa.Column("amendment_id", sa.Integer, sa.ForeignKey("amendment.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("choice", sa.String(length=8), nullable=False),  # "YES" | "NO" | "ABSTAIN"
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id","proposal_id","draft_id","amendment_id","user_id", name="uq_formal_ballot_scope_user"),
    )
    op.create_index("ix_pfb_event", "proposal_formal_ballot", ["event_id"])
    op.create_index("ix_pfb_proposal", "proposal_formal_ballot", ["proposal_id"])
    op.create_index("ix_pfb_draft", "proposal_formal_ballot", ["draft_id"])
    op.create_index("ix_pfb_amend", "proposal_formal_ballot", ["amendment_id"])
    op.create_index("ix_pfb_user", "proposal_formal_ballot", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_pfb_user", table_name="proposal_formal_ballot")
    op.drop_index("ix_pfb_amend", table_name="proposal_formal_ballot")
    op.drop_index("ix_pfb_draft", table_name="proposal_formal_ballot")
    op.drop_index("ix_pfb_proposal", table_name="proposal_formal_ballot")
    op.drop_index("ix_pfb_event", table_name="proposal_formal_ballot")
    op.drop_table("proposal_formal_ballot")

    op.drop_index("ix_peb_user", table_name="proposal_early_ballot")
    op.drop_index("ix_peb_amend", table_name="proposal_early_ballot")
    op.drop_index("ix_peb_draft", table_name="proposal_early_ballot")
    op.drop_index("ix_peb_proposal", table_name="proposal_early_ballot")
    op.drop_index("ix_peb_event", table_name="proposal_early_ballot")
    op.drop_table("proposal_early_ballot")

    op.drop_index("ix_pfs_amend", table_name="proposal_floor_state")
    op.drop_index("ix_pfs_draft", table_name="proposal_floor_state")
    op.drop_index("ix_pfs_proposal", table_name="proposal_floor_state")
    op.drop_index("ix_pfs_event", table_name="proposal_floor_state")
    op.drop_table("proposal_floor_state")

    op.drop_index("ix_psr_user", table_name="proposal_speaker_request")
    op.drop_index("ix_psr_amend", table_name="proposal_speaker_request")
    op.drop_index("ix_psr_draft", table_name="proposal_speaker_request")
    op.drop_index("ix_psr_proposal", table_name="proposal_speaker_request")
    op.drop_index("ix_psr_event", table_name="proposal_speaker_request")
    op.drop_table("proposal_speaker_request")