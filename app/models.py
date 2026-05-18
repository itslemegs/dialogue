from typing import Optional, List
from datetime import datetime
from enum import Enum as PyEnum

import sqlalchemy as sa
from sqlalchemy import Column, Integer, ForeignKey, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field, Relationship


# ----------------------------
# Roles & Users
# ----------------------------
class UserRoleLink(SQLModel, table=True):
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", primary_key=True)
    role_id: Optional[int] = Field(default=None, foreign_key="role.id", primary_key=True)

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    handle: str = Field(index=True, unique=True, max_length=80)
    email: str = Field(index=True, unique=True, max_length=320)
    password_hash: str = Field(max_length=255)
    verified: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    roles: list["Role"] = Relationship(
        back_populates="users",
        link_model=UserRoleLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

    created_events: list["Event"] = Relationship(
        back_populates="created_by",
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "[Event.created_by_id]",
        },
    )

    event_access_grants: list["EventAccessGrant"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "lazy": "selectin",
            "foreign_keys": "[EventAccessGrant.user_id]",
        },
    )

class Role(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, max_length=80)

    users: list[User] = Relationship(
        back_populates="roles",
        link_model=UserRoleLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )

class Session(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True, foreign_key="user.id")
    token: str = Field(index=True, unique=True, max_length=255)
    issued_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


# ----------------------------
# Questions / Interventions / Proposals (legacy thread)
# ----------------------------
class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: Optional[int] = Field(default=None, foreign_key="event.id", index=True)
    text: str
    rapporteur_id: int = Field(foreign_key="user.id", index=True)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class Intervention(SQLModel, table=True):
    __tablename__ = "intervention"
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    question_id: int = Field(index=True, foreign_key="question.id")
    by_user: int = Field(index=True, foreign_key="user.id")
    local_no: Optional[int] = Field(default=None, index=True)
    body: str
    relates_to_id: Optional[int] = Field(default=None, index=True, foreign_key="intervention.id")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class Proposal(SQLModel, table=True):
    # __tablename__ = "proposal"
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(index=True, foreign_key="question.id")
    by_user: int = Field(index=True, foreign_key="user.id")
    point_key: str
    op: str # "add" | "replace" | "delete" | "parameterize"
    text: str
    sponsors_json: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class Draft(SQLModel, table=True):
    # __tablename__ = "draft"
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(index=True, foreign_key="question.id")
    version_int: int = Field(default=1)
    # [{id, point_key, text, status}]
    clauses_json: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    status: str = Field(default="WORKING", max_length=32) # or "ADOPTED"


class Objection(SQLModel, table=True):
    # __tablename__ = "objection"
    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True, foreign_key="draft.id")
    clause_id: str # local id inside clauses_json
    by_user: int = Field(foreign_key="user.id")
    reason_code: str # LEGAL | TECH_FEASIBILITY | RIGHTS_IMPACT | PRECEDENT | AMBIGUITY
    alt_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    maintained: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


# ----------------------------
# Floor / Speaker queue
# ----------------------------
class FloorState(SQLModel, table=True):
    # __tablename__ = "floorstate"
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    question_id: int = Field(index=True, unique=True, foreign_key="question.id")
    is_open: bool = Field(default=True)
    speaking_time_sec: int = Field(default=120)
    current_speaker_request_id: Optional[int] = Field(default=None, index=True, foreign_key="speakerrequest.id")
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class SpeakerRequest(SQLModel, table=True):
    # __tablename__ = "speakerrequest"
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    question_id: int = Field(index=True, foreign_key="question.id")
    user_id: int = Field(index=True, foreign_key="user.id")
    # "GENERAL" | "ROR" | "ROR_ALL" | "CHAIR"
    kind: str = Field(default="GENERAL", max_length=16)
    # "QUEUED" | "SPEAKING" | "DONE" | "WITHDRAWN"
    status: str = Field(default="QUEUED", max_length=16)
    position: int = Field(default=0)
    target_intervention_id: Optional[int] = Field(default=None, index=True, foreign_key="intervention.id")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


# ----------------------------
# Notifications / Invites
# ----------------------------
class Notification(SQLModel, table=True):
    # __tablename__ = "notification"
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(index=True, foreign_key="user.id")
    question_id: Optional[int] = Field(default=None, index=True, foreign_key="question.id")
    type: str = Field(max_length=40)
    message: str
    is_read: bool = Field(default=False)
    payload_json: Optional[dict] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=True, server_default=text("'{}'::jsonb")),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class RorInvite(SQLModel, table=True):
    __tablename__ = "rorinvite"
    id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True),
    )
    question_id: int = Field(foreign_key="question.id", index=True)
    target_intervention_id: Optional[int] = Field(default=None, index=True, foreign_key="intervention.id") # NULL => ROR_ALL
    from_user_id: int = Field(foreign_key="user.id", index=True)
    to_user_id: int = Field(foreign_key="user.id", index=True)
    kind: str = Field(default="ROR", max_length=16)             # "ROR" | "ROR_ALL"
    status: str = Field(default="PENDING", max_length=16)      # PENDING | ACCEPTED | DECLINED
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


# ----------------------------
# Events (+ access control)
# ----------------------------

class EventAccessMode(str, PyEnum):
    open = "open"
    passcode = "passcode"


class Event(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    title: str
    starts_at: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))
    ends_at: Optional[datetime] = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))

    access_mode: EventAccessMode = Field(
        default=EventAccessMode.open,
        sa_column=sa.Column(sa.String(16), nullable=False, server_default="open", index=True),
    )
    passcode_hash: Optional[str] = Field(default=None, max_length=255)
    created_by_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)

    created_by: Optional["User"] = Relationship(
        back_populates="created_events",
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "[Event.created_by_id]",
        },
    )

    stages: List["EventStage"] = Relationship(
        back_populates="event",
        sa_relationship_kwargs={"cascade": "all, delete-orphan", "lazy": "selectin"},
    )

    proposals: list["AgendaProposal"] = Relationship(
        back_populates="event",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    access_grants: list["EventAccessGrant"] = Relationship(
        back_populates="event",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )

    @property
    def stages_json(self) -> list[dict]:
        return [
            {
                "id": s.id,
                "name": s.name,
                "starts": s.starts_at.isoformat(),
                "ends": s.ends_at.isoformat() if s.ends_at else None,
            }
            for s in sorted(self.stages, key=lambda x: x.starts_at)
        ]


class EventAccessGrant(SQLModel, table=True):
    __tablename__ = "event_access_grant"
    __table_args__ = (
        UniqueConstraint("event_id", "user_id", name="uq_event_access_grant"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("event.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    user_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )

    granted_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    event: Optional["Event"] = Relationship(back_populates="access_grants")

    user: Optional["User"] = Relationship(
        back_populates="event_access_grants",
        sa_relationship_kwargs={
            "lazy": "selectin",
            "foreign_keys": "[EventAccessGrant.user_id]",
        },
    )


class EventStage(SQLModel, table=True):
    __tablename__ = "event_stage"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("event.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    name: str
    starts_at: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=sa.Column(sa.DateTime(timezone=True), nullable=False))

    event: "Event" = Relationship(back_populates="stages")


# ----------------------------
# Agenda proposals (enum status) + bridge to general-floor Question
# ----------------------------
class ProposalStatus(str, PyEnum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    submitted = "submitted"

class AgendaProposal(SQLModel, table=True):
    __tablename__ = "agendaproposal"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("event.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    event: Optional["Event"] = Relationship(back_populates="proposals")

    proposer_id: Optional[int] = Field(default=None, foreign_key="user.id") # allow anonymous
    title: str = Field(index=True, max_length=160)
    background: str = Field(sa_column=Column(Text, nullable=False))
    source_url: Optional[str] = Field(default=None, max_length=2048)
    status: ProposalStatus = Field(default=ProposalStatus.pending, index=True)
    decided_by_id: Optional[int] = Field(default=None, foreign_key="user.id")
    decided_at: Optional[datetime] = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))
    notes: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    proposer: Optional["User"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[AgendaProposal.proposer_id]"})
    decided_by: Optional["User"] = Relationship(sa_relationship_kwargs={"foreign_keys": "[AgendaProposal.decided_by_id]"})

class GeneralFloorLink(SQLModel, table=True):
    __tablename__ = "general_floor_link"
    # one proposal -> one question
    proposal_id: int = Field(
        sa_column=Column(Integer, ForeignKey("agendaproposal.id", ondelete="CASCADE"), primary_key=True, nullable=False)
    )
    question_id: int = Field(
        sa_column=Column(Integer, ForeignKey("question.id", ondelete="CASCADE"), unique=True, nullable=False)
    )


# ----------------------------
# Proposal Rooms & Messages & Drafts
# ----------------------------
class ProposalRoom(SQLModel, table=True):
    __tablename__ = "proposalroom"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(
        sa_column=Column(Integer, ForeignKey("event.id", ondelete="CASCADE"), nullable=False)
    )
    proposal_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"),
            nullable=False,
        )
    )

    title: str = Field(index=True, max_length=160)
    description: Optional[str] = None
    sponsor_id: int = Field(foreign_key="user.id", nullable=False, index=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    sponsor: Optional["User"] = Relationship()

class ProposalMessage(SQLModel, table=True):
    __tablename__ = "proposalmessage"
    __table_args__ = (UniqueConstraint("room_id", "local_no", name="uq_proposalmessage_room_local"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    room_id: int = Field(sa_column=Column(Integer, ForeignKey("proposalroom.id", ondelete="CASCADE")))
    user_id: int = Field(sa_column=sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False, index=True))
    local_no: Optional[int] = Field(default=None, index=True)
    body: str
    parent_id: Optional[int] = Field(default=None, foreign_key="proposalmessage.id")
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class ProposalDraftStatus(str, PyEnum):
    TABLED = "TABLED"
    WITHDRAWN = "WITHDRAWN"
    REINTRODUCED = "REINTRODUCED"
    ADOPTED = "ADOPTED" # (future: closes cosign)

class ProposalDraft(SQLModel, table=True):
    __tablename__ = "proposaldraft"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(
        sa_column=Column(Integer, ForeignKey("event.id", ondelete="CASCADE"), nullable=False)
    )
    proposal_id: int = Field(
        sa_column=Column(Integer, ForeignKey("agendaproposal.id", ondelete="CASCADE"), nullable=False)
    )
    room_id: int = Field(sa_column=Column(Integer, ForeignKey("proposalroom.id", ondelete="CASCADE")))

    sponsor_id: int = Field(foreign_key="user.id", nullable=False, index=True)

    title: Optional[str] = Field(default=None, max_length=200)

    # blocks
    recalling: Optional[str] = None
    noting: Optional[str] = None
    welcoming: Optional[str] = None
    expressing_regret: Optional[str] = None
    expressing_deep_concern: Optional[str] = None
    emphasizing: Optional[str] = None
    decides: Optional[str] = None
    requests: Optional[str] = None
    calls_upon: Optional[str] = None
    encourages: Optional[str] = None

    # meta
    cosigners_json: Optional[list] = Field(default=None, sa_column=Column(JSONB, nullable=True))
    is_submitted: bool = Field(default=False)
    l_number: Optional[str] = None

    status: ProposalDraftStatus = Field(default=ProposalDraftStatus.TABLED, index=True)
    withdrawn_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True))
    )
    reintroduced_by_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    reintroduced_at: Optional[datetime] = Field(
        default=None,
        sa_column=sa.Column(sa.DateTime(timezone=True))
    )

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    submitted_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class Amendment(SQLModel, table=True):
    __tablename__ = "amendment"
    id: Optional[int] = Field(default=None, primary_key=True)
    draft_id: int = Field(index=True, foreign_key="proposaldraft.id")
    am_no: int
    label: str
    submitted_by_id: int = Field(foreign_key="user.id", nullable=False, index=True)
    body_markdown: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class AmendmentVoteState(SQLModel, table=True):
    __tablename__ = "amendment_vote_state"
    id: Optional[int] = Field(default=None, primary_key=True)

    amendment_id: int = Field(index=True, unique=True, foreign_key="amendment.id")

    is_open: bool = Field(default=False)
    yes: int = Field(default=0)
    no: int = Field(default=0)
    abstain: int = Field(default=0)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class AmendmentVote(SQLModel, table=True):
    __tablename__ = "amendment_vote"
    __table_args__ = (
        UniqueConstraint("amendment_id", "user_id", name="uq_amendment_vote_once"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    amendment_id: int = Field(index=True, foreign_key="amendment.id")
    user_id: int = Field(index=True, foreign_key="user.id")

    # "YES" | "NO" | "ABSTAIN"
    choice: str = Field(sa_column=Column(sa.String(16), nullable=False))

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class EventSequence(SQLModel, table=True):
    # __tablename__ = "eventsequence"
    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(index=True, unique=True, foreign_key="event.id")
    next_l_no: int = Field(default=1)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

# ----------------------------
# Proposal Floor (per draft/amendment)
# ----------------------------
class ProposalSpeakerKind(str, PyEnum):
    GENERAL = "GENERAL"
    ROR = "ROR"
    ROR_ALL = "ROR_ALL"
    CHAIR = "CHAIR"

class ProposalSpeakerStatus(str, PyEnum):
    QUEUED = "QUEUED"
    SPEAKING = "SPEAKING"
    DONE = "DONE"
    WITHDRAWN = "WITHDRAWN"

class ProposalFloorState(SQLModel, table=True):
    __tablename__ = "proposal_floor_state"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(foreign_key="event.id", index=True)
    proposal_id: int = Field(foreign_key="agendaproposal.id", index=True)

    # Exactly one of these must be set to scope the floor to a draft OR an amendment.
    draft_id: Optional[int] = Field(default=None, foreign_key="proposaldraft.id", index=True)
    amendment_id: Optional[int] = Field(default=None, foreign_key="amendment.id", index=True)

    is_open: bool = Field(default=True)
    speaking_time_sec: int = Field(default=120)

    # Chair “now speaking” pointer into the proposal-floor queue
    current_speaker_request_id: Optional[int] = Field(
        default=None,
        foreign_key="proposal_speaker_request.id",
        index=True
    )

    # Voting windows
    early_is_open: bool = Field(default=False)
    formal_is_open: bool = Field(default=False)

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    __table_args__ = (
        # Each scope (event+proposal+draft/amendment) has at most one state row
        sa.UniqueConstraint(
            "event_id", "proposal_id", "draft_id", "amendment_id",
            name="uq_pfloor_scope"
        ),
    )

class ProposalSpeakerRequest(SQLModel, table=True):
    __tablename__ = "proposal_speaker_request"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(foreign_key="event.id", index=True)
    proposal_id: int = Field(foreign_key="agendaproposal.id", index=True)
    draft_id: Optional[int] = Field(default=None, foreign_key="proposaldraft.id", index=True)
    amendment_id: Optional[int] = Field(default=None, foreign_key="amendment.id", index=True)

    user_id: int = Field(foreign_key="user.id", index=True)

    # "GENERAL" | "ROR" | "ROR_ALL" | "CHAIR"
    kind: str = Field(default="GENERAL", max_length=16)
    # "QUEUED" | "SPEAKING" | "DONE" | "WITHDRAWN"
    status: str = Field(default="QUEUED", max_length=16)
    position: int = Field(default=0)

    # Optional: targeted reply (if you later add per-message threads here)
    target_intervention_id: Optional[int] = Field(default=None, foreign_key="intervention.id", index=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

# --- Proposal Floor Interventions (separate from General Floor) ---------------
class ProposalIntervention(SQLModel, table=True):
    __tablename__ = "proposal_intervention"
    __table_args__ = (
        sa.UniqueConstraint(
            "event_id", "proposal_id", "draft_id", "amendment_id", "local_no",
            name="uq_pfi_scope_local"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # scope (exactly one of draft_id / amendment_id is set for a given record)
    event_id: int = Field(foreign_key="event.id", index=True)
    proposal_id: int = Field(foreign_key="agendaproposal.id", index=True)
    draft_id: Optional[int] = Field(default=None, foreign_key="proposaldraft.id", index=True)
    amendment_id: Optional[int] = Field(default=None, foreign_key="amendment.id", index=True)

    by_user: int = Field(foreign_key="user.id", index=True)
    local_no: Optional[int] = Field(default=None, index=True)
    body: str

    # self-threading (only within same scope)
    parent_id: Optional[int] = Field(default=None, foreign_key="proposal_intervention.id", index=True)

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class ProposalEarlyVote(SQLModel, table=True):
    """
    Informal consensus check: Adopted iff No == 0 at close.
    """
    __tablename__ = "proposal_early_vote"
    __table_args__ = (
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND amendment_id IS NULL) OR (draft_id IS NULL AND amendment_id IS NOT NULL)",
            name="ck_pev_target_exactly_one"
        ),
        sa.UniqueConstraint("draft_id", name="uq_pev_draft", deferrable=True, initially="DEFERRED"),
        sa.UniqueConstraint("amendment_id", name="uq_pev_amendment", deferrable=True, initially="DEFERRED"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(
        # index=True,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("event.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    proposal_id: int = Field(
        # index=True,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    draft_id: Optional[int] = Field(
        default=None,
        # index=True,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    amendment_id: Optional[int] = Field(
        default=None,
        # index=True,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("amendment.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    is_open: bool = Field(default=True)
    yes: int = Field(default=0)
    no: int = Field(default=0)
    abstain: int = Field(default=0)  # <-- new
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class ProposalFormalVote(SQLModel, table=True):
    """
    Formal vote: simple majority of Yes vs No; ties rejected.
    Abstentions tracked but NOT counted toward result.
    """
    __tablename__ = "proposal_formal_vote"
    __table_args__ = (
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND amendment_id IS NULL) OR (draft_id IS NULL AND amendment_id IS NOT NULL)",
            name="ck_pfv_target_exactly_one"
        ),
        sa.UniqueConstraint("draft_id", name="uq_pfv_draft", deferrable=True, initially="DEFERRED"),
        sa.UniqueConstraint("amendment_id", name="uq_pfv_amendment", deferrable=True, initially="DEFERRED"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("event.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    proposal_id: int = Field(
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("agendaproposal.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )

    draft_id: Optional[int] = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("proposaldraft.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    amendment_id: Optional[int] = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("amendment.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    is_open: bool = Field(default=False)
    opened_at: Optional[datetime] = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))
    closed_at: Optional[datetime] = Field(default=None, sa_column=sa.Column(sa.DateTime(timezone=True)))
    yes: int = Field(default=0)
    no: int = Field(default=0)
    abstain: int = Field(default=0)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    ballots: list["ProposalFormalBallot"] = Relationship(back_populates="formal_vote", sa_relationship_kwargs={"cascade": "all, delete-orphan", "passive_deletes": True},)

class ProposalEarlyBallot(SQLModel, table=True):
    __tablename__ = "proposal_early_ballot"
    __table_args__ = (
        sa.CheckConstraint(
            "(draft_id IS NOT NULL AND amendment_id IS NULL) OR "
            "(draft_id IS NULL AND amendment_id IS NOT NULL)",
            name="ck_peb_target_exactly_one"
        ),
        sa.UniqueConstraint(
            "event_id", "proposal_id", "draft_id", "amendment_id", "user_id",
            name="uq_peb_one_per_user"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(foreign_key="event.id", index=True)
    proposal_id: int = Field(foreign_key="agendaproposal.id", index=True)

    draft_id: Optional[int] = Field(default=None, foreign_key="proposaldraft.id", index=True)
    amendment_id: Optional[int] = Field(default=None, foreign_key="amendment.id", index=True)

    user_id: int = Field(foreign_key="user.id", index=True)

    # "YES", "NO", or "ABSTAIN"
    choice: str = Field(sa_column=sa.Column(sa.String(8), nullable=False))

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

class ProposalFormalBallot(SQLModel, table=True):
    __tablename__ = "proposal_formal_ballot"
    id: Optional[int] = Field(default=None, primary_key=True)

    event_id: int = Field(foreign_key="event.id", index=True)
    proposal_id: int = Field(foreign_key="agendaproposal.id", index=True)
    draft_id: Optional[int] = Field(default=None, foreign_key="proposaldraft.id", index=True)
    amendment_id: Optional[int] = Field(default=None, foreign_key="amendment.id", index=True)

    user_id: int = Field(foreign_key="user.id", index=True)
    choice: str = Field(max_length=8)

    formal_vote_id: Optional[int] = Field(default=None, foreign_key="proposal_formal_vote.id")  # Add this field for relationship

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "event_id", "proposal_id", "draft_id", "amendment_id", "user_id",
            name="uq_formal_ballot_scope_user"
        ),
    )

    # Define the relationship
    formal_vote: ProposalFormalVote = Relationship(back_populates="ballots")

class LiveSummary(SQLModel, table=True):
    __tablename__ = "live_summary"

    # One row per scope, e.g.:
    # "GENERAL:question=12"
    # "PROOM:room=55"
    # "PFLOOR:event=1:proposal=2:draft=7"  or  "...:amend=9"
    scope_key: str = Field(primary_key=True, max_length=200)

    kind: str = Field(index=True, max_length=24)

    # optional denormalized scope fields (useful for debugging/admin)
    question_id: Optional[int] = Field(default=None, index=True, foreign_key="question.id")
    room_id: Optional[int] = Field(
        default=None,
        sa_column=sa.Column(
            sa.Integer,
            sa.ForeignKey("proposalroom.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )

    event_id: Optional[int] = Field(default=None, index=True, foreign_key="event.id")
    proposal_id: Optional[int] = Field(default=None, index=True, foreign_key="agendaproposal.id")
    draft_id: Optional[int] = Field(default=None, index=True, foreign_key="proposaldraft.id")
    amendment_id: Optional[int] = Field(default=None, index=True, foreign_key="amendment.id")

    summary: str = Field(default="", sa_column=sa.Column(sa.Text, nullable=False))

    # cursor for incremental updates (we use the source table's PK id)
    last_item_id: int = Field(default=0)

    dirty: bool = Field(default=False, index=True)

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=sa.Column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

class DraftTranslation(SQLModel, table=True):
    __tablename__ = "drafttranslation"

    __table_args__ = (
        UniqueConstraint("draft_id", "lang", name="uq_drafttranslation_draft_lang"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    draft_id: int = Field(foreign_key="proposaldraft.id", index=True)
    lang: str = Field(index=True, max_length=10)

    source_hash: str = Field(index=True, max_length=64)

    # PENDING | RUNNING | DONE | FAILED
    status: str = Field(default="PENDING", index=True, max_length=20)

    title_show: Optional[str] = None

    draft_text_json: dict = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False),
    )

    error: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)