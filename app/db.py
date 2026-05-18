import os
from contextlib import contextmanager
from typing import Iterator

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel, Session as SyncSession, create_engine as create_engine_sync, select

# --- load environment ---------------------------------------------------------
load_dotenv()

# Default to local Postgres; override via env in prod
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/consensus")
ASYNC_DATABASE_URL = os.getenv("ASYNC_DATABASE_URL", "postgresql+asyncpg://app:app@localhost:5432/consensus")

# --- engines ------------------------------------------------------------------
# Async engine for FastAPI request handlers
engine: AsyncEngine = create_async_engine(
    ASYNC_DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Sync engine for Alembic and quick utilities
engine_sync = create_engine_sync(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    future=True,
)

# --- session makers -----------------------------------------------------------
# Async sessions (use in FastAPI routes with dependency)
async_session_maker = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Sync session helper (use in alembic scripts / one-off utilities)
@contextmanager
def get_session() -> Iterator[SyncSession]:
    session = SyncSession(engine_sync, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()

def get_db() -> Iterator[SyncSession]:
    with SyncSession(engine_sync, expire_on_commit=False) as session:
        yield session

# --- models needed for seeding / role helpers --------------------------------
from app.models import User, Role, Session as AuthSession

BASE_ROLES = ["member", "invited speaker", "chairman", "president", "admin", "banned"]

def ensure_role(db: SyncSession, name: str) -> Role:
    name = (name or "").strip().lower()
    role = db.exec(select(Role).where(Role.name == name)).first()
    if not role:
        role = Role(name=name)
        db.add(role); db.commit(); db.refresh(role)
    return role

def grant_role(db: SyncSession, user: User, role_name: str) -> None:
    role = ensure_role(db, role_name)
    if not any(r.id == role.id for r in user.roles or []):
        user.roles.append(role)
        db.add(user); db.commit(); db.refresh(user)

def revoke_role(db: SyncSession, user: User, role_name: str) -> None:
    role_name = (role_name or "").strip().lower()
    if not getattr(user, "roles", None):
        return
    user.roles = [r for r in user.roles if (r.name or "").lower() != role_name]
    db.add(user); db.commit(); db.refresh(user)

def seed_roles() -> None:
    with get_session() as db:
        existing = {r.name for r in db.exec(select(Role)).all()}
        for name in BASE_ROLES:
            if name not in existing:
                db.add(Role(name=name))
        db.commit()

def backfill_member_role() -> None:
    """Ensure every user has the 'member' role."""
    with get_session() as db:
        users = db.exec(select(User)).all()
        for u in users:
            if not any((r.name or "").lower() == "member" for r in u.roles or []):
                grant_role(db, u, "member")

def invalidate_sessions_for_user(db: SyncSession, user_id: int) -> None:
    sessions = db.exec(select(AuthSession).where(AuthSession.user_id == user_id)).all()
    for s in sessions:
        db.delete(s)
    db.commit()

def init_db() -> None:
    """
    Alembic owns the schema. We only seed/backfill here.
    If you *really* want a one-off bootstrap without Alembic, set RUN_CREATE_ALL=1.
    """
    if os.getenv("RUN_CREATE_ALL") == "1":
        # IMPORTANT: import all models so metadata includes every table
        from app.models import (
            User, Role, UserRole, Question, Intervention, FloorState,
            SpeakerRequest, Notification, RorInvite, Event, EventStage,
            AgendaProposal, GeneralFloorLink, ProposalRoom, ProposalMessage,
            ProposalDraft, DraftCosign,
        )
        SQLModel.metadata.create_all(engine_sync)

    # Safe to run always
    seed_roles()
    backfill_member_role()

# --- optional: tiny connectivity smoke test ----------------------------------
def ping_sync() -> str:
    with engine_sync.connect() as conn:
        return conn.execute(text("select version()")).scalar() or ""