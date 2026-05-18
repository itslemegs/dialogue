from fastapi import FastAPI, Request, Depends, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import select, Session
from sqlalchemy.exc import IntegrityError
from app.db import (
    init_db,
    get_session,
    invalidate_sessions_for_user,
    grant_role,
    revoke_role,
    seed_roles,
    engine,
)
from app.models import (
    User,
    Role,
    Session as AuthSession,
    Question,
    ProposalStatus,
    FloorState,
    SpeakerRequest,
    Draft,
    Objection,
    Intervention,
    Proposal,
    Notification,
    RorInvite,
    Event,
    EventStage,
    AgendaProposal,
    GeneralFloorLink,
    ProposalRoom,
    ProposalMessage,
    ProposalDraft,
    EventSequence,
    ProposalDraftStatus,
    Amendment,
    ProposalFloorState,
    ProposalSpeakerRequest,
    ProposalEarlyVote,
    ProposalFormalVote,
    ProposalEarlyBallot,
    ProposalFormalBallot,
    ProposalIntervention,
    AmendmentVote,
    AmendmentVoteState,
    EventAccessGrant,
    EventAccessMode,
)
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any
import json
from sqlalchemy.orm import selectinload

import os
import logging
import httpx
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.security import (
    hash_password,
    verify_password,
    login_user,      # <-- use this to set cookie
    unsign_cookie,   # <-- returns int user_id or None
    effective_flags, # <-- for flags injection
    has_role,
)

from fastapi import FastAPI

from sqlalchemy import delete

from datetime import datetime, timedelta, timezone

def _now_utc():
    return datetime.now(timezone.utc)

# VISIBLE_AFTER = timedelta(days=3)
VISIBLE_AFTER = timedelta(hours=0)

@asynccontextmanager
async def lifespan(app: FastAPI):
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "30m")

    try:
        httpx.post(
            f"{base}/api/chat",
            json={
                "model": model,
                "messages": [],
                "stream": False,
                "keep_alive": keep_alive,
            },
            timeout=120.0,
        ).raise_for_status()
        log.info("Ollama warmup ok (%s)", model)
    except Exception as e:
        log.warning("Ollama warmup failed: %s", e)

    yield

from app.services.local_translate import warm_model

for lang in ("ar", "zh", "fr", "ru", "es"):
    warm_model(lang)

app = FastAPI(title="Consensus MVP",lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

init_db()

# wherever you initialize templates
from starlette.templating import Jinja2Templates
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["getattr"] = getattr

# ----- helpers: MUST be above routes -----
EMPTY_FLAGS = {"IS_ADMIN": False, "IS_PRESIDENT": False, "IS_CHAIR": False, "IS_INVITED": False, "IS_MEMBER": False}

from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from fastapi import HTTPException
from sqlmodel import select
from sqlalchemy.orm import selectinload

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

def _is_event_member(user) -> bool:
    if not user:
        return False
    flags = effective_flags(user)
    return any([
        flags.get("IS_MEMBER"),
        flags.get("IS_CHAIR"),
        flags.get("IS_PRESIDENT"),
        flags.get("IS_ADMIN"),
    ])

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

def _is_event_member(user) -> bool:
    if not user:
        return False
    flags = effective_flags(user)
    return any([
        flags.get("IS_MEMBER"),
        flags.get("IS_CHAIR"),
        flags.get("IS_PRESIDENT"),
        flags.get("IS_ADMIN"),
    ])


def _get_event_or_404(*, db: Session, event_id: int) -> Event:
    event = db.exec(
        select(Event)
        .where(Event.id == event_id)
        .options(selectinload(Event.stages))
    ).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _user_has_event_access(*, db: Session, user, event: Event) -> bool:
    if not _is_event_member(user):
        return False

    flags = effective_flags(user)
    if flags.get("IS_ADMIN") or flags.get("IS_PRESIDENT") or flags.get("IS_CHAIR"):
        return True

    if event.access_mode == EventAccessMode.open:
        return True

    grant = db.exec(
        select(EventAccessGrant).where(
            EventAccessGrant.event_id == event.id,
            EventAccessGrant.user_id == user.id,
        )
    ).first()
    return grant is not None


def _require_event_access(*, db: Session, user, event_id: int) -> Event:
    event = _get_event_or_404(db=db, event_id=event_id)
    if not _user_has_event_access(db=db, user=user, event=event):
        raise HTTPException(status_code=403, detail="Event access denied")
    return event

@app.get("/events/{event_id}/unlock", response_class=HTMLResponse)
def event_unlock_get(request: Request, event_id: int):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    with get_session() as s:
        event = _get_event_or_404(db=s, event_id=event_id)

        if not _is_event_member(user):
            raise HTTPException(status_code=403, detail="Members only")

        if event.access_mode == EventAccessMode.open or _user_has_event_access(db=s, user=user, event=event):
            return RedirectResponse(f"/events/{event.id}/menu", status_code=303)

        return templates.TemplateResponse(
            "events/unlock_event.html",
            {
                "request": request,
                "user": user,
                "flags": effective_flags(user),
                "event": event,
                "err": request.query_params.get("err"),
            },
        )


@app.post("/events/{event_id}/unlock")
def event_unlock_post(request: Request, event_id: int, passcode: str = Form(...)):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    with get_session() as s:
        event = _get_event_or_404(db=s, event_id=event_id)

        if not _is_event_member(user):
            raise HTTPException(status_code=403, detail="Members only")

        if event.access_mode == EventAccessMode.open:
            return RedirectResponse(f"/events/{event.id}/menu", status_code=303)

        if not event.passcode_hash or not verify_password(passcode, event.passcode_hash):
            return RedirectResponse(
                f"/events/{event.id}/unlock?err=Invalid+passcode",
                status_code=303,
            )

        existing = s.exec(
            select(EventAccessGrant).where(
                EventAccessGrant.event_id == event.id,
                EventAccessGrant.user_id == user.id,
            )
        ).first()

        if not existing:
            s.add(EventAccessGrant(event_id=event.id, user_id=user.id))
            s.commit()

    return RedirectResponse(f"/events/{event_id}/menu", status_code=303)

def current_user(request: Request, allow_banned: bool = False):
    if hasattr(request.state, "user_cached") and allow_banned:
        # Only reuse when allow_banned=True or the cached user is not banned
        return request.state.user_cached

    uid = unsign_cookie(request.cookies.get("session"))
    if uid is None:
        user = None
    else:
        with get_session() as db:
            user = db.get(User, uid)
            if user and (not allow_banned) and has_role(user, "banned"):
                raise HTTPException(status_code=403, detail="Your account is banned")

    # cache for the rest of the request
    request.state.user_cached = user
    return user
    
def role_flags(user):
    return {
        "IS_ADMIN": bool(user and has_role(user, "admin")),
        "IS_PRESIDENT": bool(user and has_role(user, "president")),
        "IS_CHAIR": bool(user and has_role(user, "chairman")),
        "IS_BANNED": bool(user and has_role(user, "banned")),
    }

# app/main.py
PUBLIC_PATHS = {"/login", "/logout", "/health"}
def _is_public(path: str) -> bool:
    return (
        path in PUBLIC_PATHS
        or path.startswith("/static/")
        or path.startswith("/favicon")
    )

@app.middleware("http")
async def banned_wall(request: Request, call_next):
    try:
        uid = unsign_cookie(request.cookies.get("session"))
    except Exception:
        uid = None

    if uid and not _is_public(request.url.path):
        # If a logged-in user is banned, block non-public endpoints pre-emptively
        with get_session() as db:
            u = db.get(User, uid)
            if u and has_role(u, "banned"):
                return HTMLResponse(
                    "<h3>Your account is banned.</h3>",
                    status_code=403
                )

    return await call_next(request)

# render: keep it tolerant of anonymous users
def render(template_name: str, request: Request, **context):
    # If you've added request.state.user_cached in current_user, this is cheap.
    user = current_user(request)
    flags = effective_flags(user) if user is not None else EMPTY_FLAGS
    base_ctx = {"request": request, "user": user, "flags": flags}
    base_ctx.update(context)
    return templates.TemplateResponse(template_name, base_ctx)

# --- routes ---
# Home
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render("home.html", request)

# --- Register ---
@app.get("/register", response_class=HTMLResponse)
def get_register(request: Request):
    return render("register.html", request)

@app.post("/register")
def post_register(handle: str = Form(...), email: str = Form(...), password: str = Form(...)):
    with get_session() as db:
        if db.exec(select(User).where((User.email == email) | (User.handle == handle))).first():
            raise HTTPException(status_code=400, detail="Handle or email already in use")
        user = User(handle=handle, email=email, password_hash=hash_password(password), verified=True)
        db.add(user); db.commit(); db.refresh(user)

        # Grant baseline role immediately (in addition to any backfill/seed you run)
        from app.db import grant_role  # local import to avoid circulars on startup
        grant_role(db, user, "member")

    resp = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return login_user(resp, user.id)

from fastapi import Request, Form, status, HTTPException
from starlette.responses import RedirectResponse
from urllib.parse import urlparse, urlencode

from app.routers.ai_lookup import router as ai_lookup_router
app.include_router(ai_lookup_router)

SESSION_COOKIE_NAME = "session"  # or "__Host-session" in prod (secure + path=/ + no domain)

def safe_next_url(next_url: str | None) -> str | None:
    """Allow only same-origin relative paths to avoid open redirects."""
    if not next_url:
        return None
    p = urlparse(next_url)
    if p.scheme or p.netloc:
        return None
    return next_url if next_url.startswith("/") else None

# --- Login ---
@app.get("/login", response_class=HTMLResponse)
def get_login(request: Request, next: str | None = None):
    # Pass `next` into the template so the form can include it as a hidden field
    return render("login.html", request, next=next)

import re

@app.post("/login")
def post_login(
    request: Request,
    identifier: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
):
    with get_session() as db:
        # Get a mapped User instance (not a Row)
        is_email = re.match(r"[^@]+@[^@]+\.[^@]+", identifier)
        if is_email:
            result = db.exec(select(User).where(User.email == identifier))
        else:
            result = db.exec(select(User).where(User.handle == identifier))
        try:
            user = result.scalar_one_or_none()   # SA 1.4+/2.0 way to get the entity
        except AttributeError:
            # Older SQLModel/Result interface: .first() returns Row or tuple
            row = result.first()
            if row is None:
                user = None
            else:
                # row could be (User,) or a Row with the model at index 0
                user = row[0] if isinstance(row, (tuple, list)) else row

        # Uniform error to avoid user enumeration
        if not user:
            raise HTTPException(status_code=400, detail="Invalid credentials")

        # Handle potential column name differences across migrations
        pwd_hash = (
            getattr(user, "password_hash", None)
            or getattr(user, "hashed_password", None)
            or getattr(user, "password_digest", None)
        )
        if not pwd_hash:
            # Developer-facing message; still keep the login response generic
            # so users don't learn anything about the account.
            raise HTTPException(status_code=400, detail="Invalid credentials")

        if not verify_password(password, pwd_hash):
            raise HTTPException(status_code=400, detail="Invalid credentials")

        # Optional: prevent sessions for banned users right here
        if has_role(user, "banned"):
            raise HTTPException(status_code=403, detail="Your account is banned")

    dest = safe_next_url(next) or "/dashboard"
    resp = RedirectResponse(url=dest, status_code=status.HTTP_303_SEE_OTHER)
    return login_user(resp, user.id)

@app.api_route("/logout", methods=["GET", "POST"])
def logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("session", path="/")
    return resp

from app.routes.live_summary_fragment import router as live_summary_fragment_router

app.include_router(live_summary_fragment_router)


def _event_access_cookie_name(event_id: int) -> str:
    return f"event_access_{event_id}"


def _event_requires_passcode(event: Event) -> bool:
    # assumes your revised Event model has passcode_hash
    return bool(getattr(event, "passcode_hash", None))


def _has_event_access(request: Request, event: Event) -> bool:
    if not _event_requires_passcode(event):
        return True
    return request.cookies.get(_event_access_cookie_name(event.id)) == "1"


def _get_event_proposal_or_404(db: Session, event_id: int, pid: int, *, accepted_only: bool = False) -> AgendaProposal:
    prop = db.get(AgendaProposal, pid)
    if not prop or prop.event_id != event_id:
        raise HTTPException(404, "Agenda item not found for this event")
    if accepted_only and prop.status != ProposalStatus.accepted:
        raise HTTPException(404, "Agenda item not found or not accepted")
    return prop


def _get_event_room_or_404(db: Session, event_id: int, pid: int, rid: int) -> ProposalRoom:
    room = db.get(ProposalRoom, rid)
    if not room or room.event_id != event_id or room.proposal_id != pid:
        raise HTTPException(404, "Room not found for this event")
    return room


def _get_event_draft_or_404(db: Session, event_id: int, draft_id: int) -> tuple[ProposalDraft, AgendaProposal]:
    draft = db.get(ProposalDraft, draft_id)
    if not draft or draft.event_id != event_id:
        raise HTTPException(404, "Draft not found for this event")

    prop = db.get(AgendaProposal, draft.proposal_id)
    if not prop or prop.event_id != event_id:
        raise HTTPException(404, "Agenda item not found for this event")

    return draft, prop


def _get_event_amendment_or_404(db: Session, event_id: int, amendment_id: int) -> tuple[Amendment, ProposalDraft, AgendaProposal]:
    am = db.get(Amendment, amendment_id)
    if not am:
        raise HTTPException(404, "Amendment not found")

    draft = db.get(ProposalDraft, am.draft_id)
    if not draft or draft.event_id != event_id:
        raise HTTPException(404, "Amendment not found for this event")

    prop = db.get(AgendaProposal, draft.proposal_id)
    if not prop or prop.event_id != event_id:
        raise HTTPException(404, "Agenda item not found for this event")

    return am, draft, prop

# --- Dashboard ---
# Dashboard route – single current_user call + proper redirect + next param
from starlette.responses import RedirectResponse
from urllib.parse import urlencode
from fastapi import Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from urllib.parse import urlencode
from sqlmodel import select
from zoneinfo import ZoneInfo
from app.services.events import get_live_or_next_event

from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
import json


def _dashboard_event_cards(db: Session, request: Request, display_tz: ZoneInfo):
    now_utc = datetime.now(timezone.utc)

    rows = db.exec(
        select(Event)
        .options(selectinload(Event.stages))
        .where(
            or_(
                Event.ends_at.is_(None),
                Event.ends_at >= now_utc,
            )
        )
        .order_by(Event.starts_at.asc())
    ).all()

    cards = []
    for e in rows:
        starts_at = e.starts_at if e.starts_at.tzinfo else e.starts_at.replace(tzinfo=timezone.utc)
        ends_at = (
            e.ends_at if (e.ends_at and e.ends_at.tzinfo)
            else (e.ends_at.replace(tzinfo=timezone.utc) if e.ends_at else None)
        )

        if starts_at <= now_utc and (ends_at is None or ends_at > now_utc):
            state = "live"
        else:
            state = "upcoming"

        ordered_stages = sorted((e.stages or []), key=lambda s: s.starts_at)

        def infer_kind(name: str) -> str:
            n = (name or "").lower()
            if "vot" in n:
                return "voting"
            if "debate" in n:
                return "debate"
            if "open" in n:
                return "opening"
            return "stage"

        stage_cards = [
            {
                "name": st.name,
                "kind": infer_kind(st.name),
                "start": to_iso_z(st.starts_at),
                "end": to_iso_z(st.ends_at),
            }
            for st in ordered_stages
        ]

        locked = bool(getattr(e, "passcode_hash", None)) and request.cookies.get(f"event_access_{e.id}") != "1"

        cards.append(
            {
                "id": e.id,
                "title": e.title,
                "starts_at_iso": to_iso_z(starts_at),
                "ends_at_iso": to_iso_z(ends_at) if ends_at else None,
                "starts_at_human": starts_at.astimezone(display_tz).strftime("%Y-%m-%d %H:%M %Z"),
                "stages": json.dumps(stage_cards),
                "stages_json": stage_cards,
                "state": state,
                "locked": locked,
                "menu_href": f"/events/{e.id}/menu" if not locked else f"/events/{e.id}/unlock",
            }
        )

    # live first, then upcoming, each by start time
    cards.sort(key=lambda x: (0 if x["state"] == "live" else 1, x["starts_at_iso"]))
    return cards


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    with get_session() as db:
        qs = db.exec(select(Question)).all()

        JST = ZoneInfo("Asia/Tokyo")
        dashboard_events = _dashboard_event_cards(db, request, JST)

    return render(
        "dashboard.html",
        request,
        questions=qs,
        dashboard_events=dashboard_events,
        next_event=(dashboard_events[0] if dashboard_events else None),  # temporary backward compatibility
    )

from collections import defaultdict
from datetime import datetime
from datetime import timezone

def ordered_speakers(db, qid: int):
    return db.exec(
        select(SpeakerRequest)
        .where(SpeakerRequest.question_id == qid)
        .order_by(SpeakerRequest.status.desc(), KIND_PRIORITY.desc(), SpeakerRequest.position.asc())
    ).all()

from sqlalchemy import func

from uuid import uuid4

import json

def get_or_create_floor(db, qid: int) -> FloorState:
    fs = db.exec(select(FloorState).where(FloorState.question_id == qid)).first()
    if fs:
        return fs
    fs = FloorState(question_id=qid, is_open=True, speaking_time_sec=120)
    db.add(fs)
    db.commit()
    db.refresh(fs)
    return fs

from typing import Optional, Tuple

from sqlalchemy import case

KIND_PRIORITY = case(
    (SpeakerRequest.kind == "CHAIR", 3),
    (SpeakerRequest.kind == "ROR_ALL", 2),
    (SpeakerRequest.kind == "ROR", 1),
    else_=0
)

def user_has_floor(db, qid: int, user_id: int) -> bool:
    fs = db.exec(select(FloorState).where(FloorState.question_id == qid)).first()
    if not fs or not fs.current_speaker_request_id:
        return False
    cur = db.get(SpeakerRequest, fs.current_speaker_request_id)
    return bool(cur and cur.user_id == user_id and cur.status == "SPEAKING")

from fastapi.responses import JSONResponse

def _parse_payload(value):
    """
    Accepts jsonb/py objects, JSON strings, or junk.
    Returns a dict (or {}).
    """
    # Already a mapping?
    if isinstance(value, dict):
        return value
    # JSON string → try to parse once
    if isinstance(value, str):
        s = value.strip()
        # If the string looks like JSON, try parsing
        if s.startswith("{") or s.startswith("["):
            try:
                j = json.loads(s)
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}
        # Otherwise treat as junk
        return {}
    # SQLAlchemy might hand you a raw JSONB object already
    try:
        # Some drivers give memoryview/bytes
        if isinstance(value, (bytes, bytearray, memoryview)):
            try:
                j = json.loads(bytes(value).decode("utf-8", "ignore"))
                return j if isinstance(j, dict) else {}
            except Exception:
                return {}
    except Exception:
        pass
    return {}

from app.services.help_ctx import build_help_ctx

@app.middleware("http")
async def inject_help_ctx(request: Request, call_next):
    user = current_user(request)
    stage = getattr(request.state, "stage", None)
    request.state.help_ctx = build_help_ctx(request, flags=role_flags(user), stage=stage)
    return await call_next(request)

from sqlalchemy import and_

@app.get("/notifications/pull")
def notifications_pull(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    with get_session() as db:
        notes = db.exec(
            select(Notification)
            .where(
                and_(
                    Notification.user_id == user.id,
                    Notification.is_read.is_(False),
                )
            )
            .order_by(Notification.created_at.desc())
        ).all()

        # Parse payloads once, collect invite IDs we need to validate
        parsed = []
        invite_ids = []
        for n in notes:
            payload = _parse_payload(getattr(n, "payload_json", None)) or {}
            iid = payload.get("invite_id") if n.type in ("INVITE_ROR", "INVITE_ROR_PFLOOR") else None
            if iid:
                invite_ids.append(iid)
            parsed.append((n, payload, iid))

        pending_invites = set()
        if invite_ids:
            pending_invites = set(
                db.exec(
                    select(RorInvite.id).where(
                        and_(
                            RorInvite.id.in_(invite_ids),
                            RorInvite.status == "PENDING",
                        )
                    )
                ).all()
            )

        out = []
        for n, payload, iid in parsed:
            if n.type in ("INVITE_ROR", "INVITE_ROR_PFLOOR"):
                if not iid or iid not in pending_invites:
                    continue

            out.append({
                "id": n.id,
                "question_id": n.question_id,
                "type": n.type,
                "message": n.message,
                "ts": n.created_at.isoformat(),
                "payload": payload,
            })

        return JSONResponse(out)

@app.post("/notifications/ack")
def notifications_ack(request: Request, ids: str = Form(...)):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    if not id_list:
        return JSONResponse({"ok": True, "updated": 0})
    with get_session() as db:
        to_mark = db.exec(
            select(Notification)
            .where((Notification.user_id == user.id) & (Notification.id.in_(id_list)))
        ).all()
        for n in to_mark:
            n.is_read = True
            db.add(n)
        db.commit()
    return JSONResponse({"ok": True, "updated": len(id_list)})

@app.get("/invites/{iid}", response_class=HTMLResponse)
def invite_view(iid: int, request: Request):
    user = current_user(request)
    if not user: return RedirectResponse(url="/login")
    with get_session() as db:
        inv = db.get(RorInvite, iid)
        if not inv or inv.to_user_id != user.id:
            raise HTTPException(404)
        q = db.get(Question, inv.question_id)
    # very simple inline template
    html = f"""
    <html><body style="font-family:system-ui">
      <h3>Right of Reply Invitation</h3>
      <p>Question #{inv.question_id}: {q.text if q else ''}</p>
      <p>Target intervention: #{inv.target_intervention_id}</p>
      <form method="post" action="/invites/{iid}/accept" style="display:inline">
        <button>Accept</button>
      </form>
      <form method="post" action="/invites/{iid}/decline" style="display:inline;margin-left:8px">
        <button>Decline</button>
      </form>
    </body></html>
    """
    return HTMLResponse(html)

from fastapi import Query

from sqlalchemy import func, select

def _enqueue_from_invite(db, invite):
    # derive kind safely
    kind = getattr(invite, "kind", None) or ("ROR" if invite.target_intervention_id else "ROR_ALL")

    # --- get current max position as a scalar int (or None) ---
    row = db.exec(
        select(func.max(SpeakerRequest.position)).where(
            (SpeakerRequest.question_id == invite.question_id) &
            (SpeakerRequest.status == "QUEUED")
        )
    ).first()

    maxpos = None
    if row is not None:
        # row can be: Row/tuple or plain int depending on SQLAlchemy/driver
        if isinstance(row, (tuple, list)):
            val = row[0]
        else:
            val = row
        maxpos = int(val) if (val is not None) else None

    nextpos = 1 if maxpos is None else (maxpos + 1)

    req = SpeakerRequest(
        question_id=invite.question_id,
        user_id=invite.to_user_id,
        kind=kind,  # "ROR" or "ROR_ALL"
        status="QUEUED",
        position=nextpos,
        target_intervention_id=invite.target_intervention_id if kind == "ROR" else None,
    )
    db.add(req)

from sqlalchemy import func, select

def _pfloor_enqueue_from_invite(
    db,
    invite: RorInvite,
    *,
    event_id: int,
    proposal_id: int,
    draft_id: int | None,
    amendment_id: int | None,
):
    """
    Enqueue a proposal-floor speaker request from a RoR invite.
    """

    # "ROR" / "ROR_ALL" / etc.
    kind = getattr(invite, "kind", None) or ("ROR" if invite.target_intervention_id else "ROR_ALL")

    # Find current max position in THIS proposal-floor scope
    row = db.exec(
        select(func.max(ProposalSpeakerRequest.position)).where(
            (ProposalSpeakerRequest.event_id == event_id) &
            (ProposalSpeakerRequest.proposal_id == proposal_id) &
            (ProposalSpeakerRequest.draft_id == draft_id) &
            (ProposalSpeakerRequest.amendment_id == amendment_id) &
            (ProposalSpeakerRequest.status == "QUEUED")
        )
    ).first()

    maxpos = None
    if row is not None:
        # depending on SQLAlchemy / driver, row can be int OR (int,)
        if isinstance(row, (tuple, list)):
            val = row[0]
        else:
            val = row
        maxpos = int(val) if (val is not None) else None

    nextpos = 1 if maxpos is None else (maxpos + 1)

    req = ProposalSpeakerRequest(
        event_id=event_id,
        proposal_id=proposal_id,
        draft_id=draft_id,
        amendment_id=amendment_id,
        user_id=invite.to_user_id,
        kind=kind,                         # "ROR" | "ROR_ALL"
        status="QUEUED",
        position=nextpos,
        # NOTE: this FK may currently point to `intervention.id`; if you
        # later change it to `proposal_intervention.id`, update this too.
        target_intervention_id=(
            invite.target_intervention_id if kind == "ROR" else None
        ),
    )
    db.add(req)

def _ack_invite_notifications(db, invite_id: int, user_id: int):
    to_ack = db.exec(
        select(Notification).where(
            (Notification.user_id == user_id) &
            (Notification.payload_json.contains({"invite_id": invite_id}))
        )
    ).all()
    for n in to_ack:
        n.is_read = True
        db.add(n)
    db.commit()

def _notify_chair_invite_result(db, invite: RorInvite, result: str):
    """
    result: 'ACCEPTED' | 'DECLINED'
    Sends a simple notification to the chair (invite.from_user_id).
    """
    # pull some context
    q = db.get(Question, invite.question_id)
    # message like: "@alice declined your RoR invite on “Question text…”"
    to_user = db.get(User, invite.to_user_id)
    handle = f"@{to_user.handle}" if to_user and to_user.handle else f"user {invite.to_user_id}"
    msg = f"{handle} {result.lower()} your Right of Reply invite on “{(q.text if q else 'this question')[:120]}”."
    payload = {"invite_id": invite.id, "result": result}

    note = Notification(
        user_id=invite.from_user_id,        # chair gets this
        question_id=invite.question_id,
        type="INVITE_ROR_RESULT",
        message=msg,
        is_read=False,
        payload_json=payload,
    )
    db.add(note); db.commit()

@app.post("/invites/{iid}/accept")
def invite_accept(iid: int, request: Request, ajax: int = 0):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        inv = db.get(RorInvite, iid) or (_ for _ in ()).throw(HTTPException(404))
        if inv.to_user_id != user.id:
            raise HTTPException(403)

        # --- detect whether this is general-floor or proposal-floor ---
        note = db.exec(
            select(Notification).where(
                (Notification.user_id == user.id) &
                (Notification.payload_json.contains({"invite_id": iid}))
            )
        ).first()

        payload = _parse_payload(getattr(note, "payload_json", None)) if note else {}
        is_pfloor = bool(note and note.type == "INVITE_ROR_PFLOOR")
        pfloor_scope = payload.get("pfloor") if is_pfloor else None

        if inv.status == "PENDING":
            inv.status = "ACCEPTED"
            db.add(inv)

            if is_pfloor:
                # --- Proposal-floor enqueue ---
                kind = (pfloor_scope or {}).get("kind")
                draft_id = (pfloor_scope or {}).get("draft_id")
                amendment_id = (pfloor_scope or {}).get("amendment_id")

                if draft_id:
                    d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))
                    event_id = d.event_id
                    proposal_id = d.proposal_id
                elif amendment_id:
                    a = db.get(Amendment, amendment_id) or (_ for _ in ()).throw(HTTPException(404))
                    d = db.get(ProposalDraft, a.draft_id)
                    event_id = d.event_id
                    proposal_id = d.proposal_id
                else:
                    raise HTTPException(400, "Bad proposal-floor invite payload (no draft/amendment)")

                _pfloor_enqueue_from_invite(
                    db,
                    invite=inv,
                    event_id=event_id,
                    proposal_id=proposal_id,
                    draft_id=draft_id,
                    amendment_id=amendment_id,
                )
            else:
                # general floor: existing behavior
                _enqueue_from_invite(db, inv)

            db.commit()
            _notify_chair_invite_result(db, inv, "ACCEPTED")

        # mark both GF + PFLOOR notifications as read
        _ack_invite_notifications(db, inv.id, user.id)

    if ajax:
        return JSONResponse({"ok": True})

    # redirect: proposal-floor if we have context, else back to the general question page
    if is_pfloor and pfloor_scope:
        if pfloor_scope.get("kind") == "draft":
            return RedirectResponse(
                url=f"/events/proposal-floor/draft/{pfloor_scope.get('draft_id')}",
                status_code=303,
            )
        else:
            return RedirectResponse(
                url=f"/events/proposal-floor/amendment/{pfloor_scope.get('amendment_id')}",
                status_code=303,
            )
    else:
        return RedirectResponse(url=f"/questions/{inv.question_id}", status_code=303)

@app.post("/invites/{iid}/decline")
def invite_decline(iid: int, request: Request, ajax: int = 0):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        inv = db.get(RorInvite, iid) or (_ for _ in ()).throw(HTTPException(404))
        if inv.to_user_id != user.id:
            raise HTTPException(403)

        pf_ctx = _get_pfloor_context_for_invite(db, inv.id, user.id)

        if inv.status == "PENDING":
            inv.status = "DECLINED"
            db.add(inv)
            db.commit()
            _notify_chair_invite_result(db, inv, "DECLINED")

        _ack_invite_notifications(db, inv.id, user.id)

    if ajax:
        return JSONResponse({"ok": True})

    if pf_ctx:
        if pf_ctx["kind"] == "draft" and pf_ctx["draft_id"]:
            return RedirectResponse(
                url=f"/events/proposal-floor/draft/{pf_ctx['draft_id']}",
                status_code=303,
            )
        if pf_ctx["kind"] == "amendment" and pf_ctx["amendment_id"]:
            return RedirectResponse(
                url=f"/events/proposal-floor/amendment/{pf_ctx['amendment_id']}",
                status_code=303,
            )

    return RedirectResponse(url=f"/questions/{inv.question_id}", status_code=303)

# app/main.py (additions)
from fastapi import Form
from sqlmodel import select

def _require_admin(user):
    from app.security import effective_flags
    flags = effective_flags(user)
    if not flags["IS_ADMIN"]:
        from fastapi import HTTPException, status
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")

# app/main.py (replace the 3 admin routes with this version)
from fastapi import Form
from sqlmodel import select
from zoneinfo import ZoneInfo
from types import SimpleNamespace

JST = ZoneInfo("Asia/Tokyo")

def _flags(user):
    f = effective_flags(user)
    return f["IS_ADMIN"], f["IS_PRESIDENT"], f["IS_CHAIR"]

@app.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, tab: str = "users"):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    is_admin, is_president, is_chair = _flags(user)
    if not (is_admin or is_president or is_chair):
        raise HTTPException(status_code=403, detail="Admins or presidents or chairmen only")

    # Always pass tab, and have a server-side default.
    active = tab or "users"

    ctx = dict(
        tab=active,
        actor_is_admin=is_admin,
        actor_is_president=is_president,
        actor_is_chair=is_chair,
        actor_id=user.id,
    )

    with get_session() as db:
        if active == "users":
            # LOAD USERS DATA
            seed_roles()
            users = db.exec(select(User)).all()
            roles = db.exec(select(Role)).all()
            role_map = {u.id: sorted({r.name for r in u.roles}) for u in users}

            if is_admin:
                all_roles = [r.name for r in roles]
            elif is_president:
                all_roles = ["chairman", "invited speaker"]
            elif is_chair:
                all_roles = ["invited speaker"]
            else:
                all_roles = []

            ctx.update(users=users, role_map=role_map, all_roles=all_roles)

        elif active == "events":
            now_jst = datetime.now(JST).replace(second=0, microsecond=0)

            if "preset" not in ctx:
                ctx["preset"] = SimpleNamespace(
                    title="",
                    start_local=now_jst + timedelta(minutes=30),
                    open_min=10,
                    debate_min=80,
                    vote_min=20,
                    access_mode="open",
                )

            evs = db.exec(select(Event).order_by(Event.starts_at.desc())).all()
            rows = []
            for ev in evs:
                start_local = ev.starts_at.astimezone(JST)
                end_local = (ev.ends_at or ev.starts_at).astimezone(JST)
                dur = int((ev.ends_at - ev.starts_at).total_seconds() // 60) if ev.ends_at else None

                rows.append(dict(
                    id=ev.id,
                    title=ev.title,
                    start_local_str=start_local.strftime("%Y-%m-%d %H:%M"),
                    end_local_str=end_local.strftime("%Y-%m-%d %H:%M") if ev.ends_at else "—",
                    duration_min=(f"{dur} min" if dur is not None else "—"),
                    access_mode=(ev.access_mode.value if hasattr(ev.access_mode, "value") else str(ev.access_mode)),
                ))

            ctx["events"] = rows

    return render("admin_home.html", request, **ctx)

@app.post("/admin/users/{uid}/roles/grant")
def admin_grant_role(uid: int, request: Request, role: str = Form(...)):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    is_admin, is_president, is_chair = _flags(user)
    role = (role or "").strip().lower()
    if not role:
        raise HTTPException(400, "role required")

    # Baseline: don't explicitly grant 'member' (it’s implied/backfilled)
    if role == "member":
        return RedirectResponse(url="/admin?tab=users", status_code=303)

    # No one can change their own elevated roles
    if uid == user.id and role in {"admin", "president", "chairman", "invited speaker"}:
        raise HTTPException(403, detail="You cannot modify your own roles")

    with get_session() as db:
        target = db.get(User, uid) or (_ for _ in ()).throw(HTTPException(404))

        # Admin: full control
        if is_admin:
            grant_role(db, target, role)
            return RedirectResponse(url="/admin?tab=users", status_code=303)

        # President (not admin): can grant only chairman OR invited speaker; cannot touch admins
        if is_president:
            if has_role(target, "admin"):
                raise HTTPException(403, detail="Cannot modify roles of an admin")
            if role not in {"chairman", "invited speaker"}:
                raise HTTPException(403, detail="Presidents may only grant 'chairman' or 'invited speaker'")
            grant_role(db, target, role)
            return RedirectResponse(url="/admin?tab=users", status_code=303)

        # Chair (not admin/president): can grant only invited speaker
        if is_chair:
            if role != "invited speaker":
                raise HTTPException(403, detail="Chairmen may only grant 'invited speaker'")
            grant_role(db, target, "invited speaker")
            return RedirectResponse(url="/admin?tab=users", status_code=303)

    raise HTTPException(403, detail="Insufficient role")

@app.post("/admin/users/{uid}/roles/revoke")
def admin_revoke_role(uid: int, request: Request, role: str = Form(...)):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    is_admin, is_president, is_chair = _flags(user)
    role = (role or "").strip().lower()
    if not role:
        raise HTTPException(400, "role required")

    # Never revoke baseline member
    if role == "member":
        raise HTTPException(403, detail="Cannot revoke baseline 'member' role")

    # No one can change their own elevated roles
    if uid == user.id and role in {"admin", "president", "chairman", "invited speaker"}:
        raise HTTPException(403, detail="You cannot modify your own roles")

    with get_session() as db:
        target = db.get(User, uid) or (_ for _ in ()).throw(HTTPException(404))

        # Admin: full control. (Optional coupling rule example preserved)
        if is_admin:
            if role == "chairman" and has_role(target, "president"):
                revoke_role(db, target, "president")
            revoke_role(db, target, role)
            return RedirectResponse(url="/admin?tab=users", status_code=303)

        # President (not admin): can revoke only chairman OR invited speaker; cannot touch admins
        if is_president:
            if has_role(target, "admin"):
                raise HTTPException(403, detail="Cannot modify roles of an admin")
            if role not in {"chairman", "invited speaker"}:
                raise HTTPException(403, detail="Presidents may only revoke 'chairman' or 'invited speaker'")
            revoke_role(db, target, role)
            return RedirectResponse(url="/admin?tab=users", status_code=303)

        # Chair (not admin/president): can revoke only invited speaker
        if is_chair:
            if role != "invited speaker":
                raise HTTPException(403, detail="Chairmen may only revoke 'invited speaker'")
            revoke_role(db, target, "invited speaker")
            return RedirectResponse(url="/admin?tab=users", status_code=303)

    raise HTTPException(403, detail="Insufficient role")

# --- privilege helpers ---
def is_privileged(user) -> bool:
    if not user:
        return False
    f = effective_flags(user)
    return bool(f.get("IS_CHAIR") or f.get("IS_PRESIDENT"))

def force_take_floor(db, qid: int, user_id: int, kind: str = "CHAIR", target: int | None = None):
    """
    Pre-empt whoever is speaking and set `user_id` as SPEAKING.
    Creates a SpeakerRequest with status='SPEAKING', sets FloorState pointer.
    kind: 'CHAIR' (default) or 'ROR'/'ROR_ALL'/'GENERAL' if you want to reflect intent.
    """
    fs = get_or_create_floor(db, qid)

    # mark previous as DONE (if any)
    if fs.current_speaker_request_id:
        prev = db.get(SpeakerRequest, fs.current_speaker_request_id)
        if prev and prev.status == "SPEAKING":
            prev.status = "DONE"
            db.add(prev)

    # create a SPEAKING request for this privileged user
    req = SpeakerRequest(
        question_id=qid,
        user_id=user_id,
        kind=kind,
        status="SPEAKING",
        position=0,  # top; irrelevant for SPEAKING
        target_intervention_id=target if kind == "ROR" else None,
    )
    db.add(req); db.commit(); db.refresh(req)

    fs.current_speaker_request_id = req.id
    fs.updated_at = datetime.utcnow()
    db.add(fs); db.commit()

from fastapi import Form

def _can_ban(actor_is_admin, actor_is_president, actor_is_chair, target_roles, actor_id, target_id):
    target_is_admin = "admin" in target_roles
    target_is_pres  = "president" in target_roles
    is_self = (actor_id == target_id)

    # No one can ban themselves
    if is_self:
        return False

    # Admin: can ban/unban anyone except other admins (optional: allow? here we block)
    if actor_is_admin:
        return not target_is_admin

    # President: cannot touch admins or presidents
    if actor_is_president:
        return (not target_is_admin) and (not target_is_pres)

    # Chair: cannot touch admins or presidents
    if actor_is_chair:
        return (not target_is_admin) and (not target_is_pres)

    return False

@app.post("/admin/users/{uid}/ban")
def admin_ban_user(uid: int, request: Request):
    actor = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    is_admin, is_president, is_chair = _flags(actor)

    with get_session() as db:
        target = db.get(User, uid) or (_ for _ in ()).throw(HTTPException(404))
        target_roles = [r.name for r in target.roles or []]

        if not _can_ban(is_admin, is_president, is_chair, target_roles, actor.id, target.id):
            raise HTTPException(403, detail="You are not allowed to ban this user")

        # Add 'banned' role if missing
        grant_role(db, target, "banned")
        invalidate_sessions_for_user(db, target.id)

    return RedirectResponse(url="/admin?tab=users", status_code=303)

@app.post("/admin/users/{uid}/unban")
def admin_unban_user(uid: int, request: Request):
    actor = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    is_admin, is_president, is_chair = _flags(actor)

    with get_session() as db:
        target = db.get(User, uid) or (_ for _ in ()).throw(HTTPException(404))
        target_roles = [r.name for r in target.roles or []]

        # Same permissions as ban (who can unban)
        if not _can_ban(is_admin, is_president, is_chair, target_roles, actor.id, target.id):
            raise HTTPException(403, detail="You are not allowed to unban this user")

        revoke_role(db, target, "banned")

    return RedirectResponse(url="/admin?tab=users", status_code=303)

# app/main.py
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.services.events import get_live_or_next_event
from zoneinfo import ZoneInfo
import json

from fastapi import APIRouter

router = APIRouter()

@router.get("/api/events/next")
def api_next_event(request: Request):
    JST = ZoneInfo("Asia/Tokyo")

    with get_session() as db:
        events = _dashboard_event_cards(db, request, JST)

    payload_events = [
        {
            "id": ev["id"],
            "title": ev["title"],
            "start": ev["starts_at_iso"],
            "end": ev["ends_at_iso"],
            "stages": ev["stages_json"],
            "state": ev["state"],
            "locked": ev["locked"],
            "menu_href": ev["menu_href"],
        }
        for ev in events
    ]

    if not payload_events:
        return {
            "next": None,
            "events": [],
        }

    first = payload_events[0]

    return {
        "next": first,   # backward compatibility
        "title": first["title"],   # backward compatibility
        "start": first["start"],   # backward compatibility
        "end": first["end"],       # backward compatibility
        "stages": first["stages"], # backward compatibility
        "state": first["state"],   # backward compatibility
        "events": payload_events,
    }
    
# app/main.py
from datetime import datetime, timedelta, timezone
from fastapi import status
from fastapi.responses import RedirectResponse
from sqlmodel import Session

@app.get("/dev/seed_event", include_in_schema=False)
def dev_seed_event():
    now = datetime.now(timezone.utc)
    starts = now + timedelta(minutes=2)
    ends   = starts + timedelta(minutes=40)

    with get_session() as s:  # type: Session
        evt = Event(title="General Debate – Test Session", starts_at=starts, ends_at=ends)
        s.add(evt)
        s.commit()
        s.refresh(evt)

        s.add_all([
            EventStage(
                event_id=evt.id, name="Opening",
                starts_at=starts,
                ends_at=starts + timedelta(minutes=10)
            ),
            EventStage(
                event_id=evt.id, name="General Debate",
                starts_at=starts + timedelta(minutes=10),
                ends_at=starts + timedelta(minutes=35)
            ),
            EventStage(
                event_id=evt.id, name="Voting",
                starts_at=starts + timedelta(minutes=35),
                ends_at=ends
            ),
        ])
        s.commit()

    # Jump back to your dashboard to see the tile in action
    return RedirectResponse("/dashboard", status_code=status.HTTP_303_SEE_OTHER)

# app/main.py
from sqlmodel import select

# JST = ZoneInfo("Asia/Tokyo")

@app.get("/api/events", include_in_schema=False)
def list_events():
    with get_session() as s:
        rows = s.exec(select(Event).order_by(Event.starts_at.desc())).all()
        return [
            {
                "id": e.id,
                "title": e.title,
                "starts_at": e.starts_at.isoformat() if e.starts_at.tzinfo else e.starts_at.replace(tzinfo=timezone.utc).isoformat(),
                "ends_at": e.ends_at.isoformat() if e.ends_at and e.ends_at.tzinfo else (e.ends_at.replace(tzinfo=timezone.utc).isoformat() if e.ends_at else None),
            }
            for e in rows
        ]
    
from app.security import role_required

from app.utils.time import to_iso_z

def stages_pack(open_start, open_end, debate_start, debate_end, vote_start, vote_end):
    return [
        {"name": "Opening", "kind": "opening", "start": to_iso_z(open_start),   "end": to_iso_z(open_end)},
        {"name": "General Debate", "kind": "debate", "start": to_iso_z(debate_start), "end": to_iso_z(debate_end)},
        {"name": "Voting", "kind": "voting", "start": to_iso_z(vote_start),     "end": to_iso_z(vote_end)},
    ]

@app.post("/admin/events")
def admin_create_event(
    request: Request,
    user=Depends(role_required("president", "chairman")),
    title: str = Form(...),
    start_local: str = Form(...),        # "YYYY-MM-DDTHH:MM" in JST
    open_min: int = Form(...),
    debate_min: int = Form(...),
    vote_min: int = Form(...),
    access_mode: str = Form("open"),
    passcode: str = Form(""),
):
    error = None
    dt_local = None

    try:
        dt_local = datetime.strptime(start_local, "%Y-%m-%dT%H:%M").replace(tzinfo=JST)
    except Exception:
        error = "Invalid start datetime"

    try:
        open_min = int(open_min)
        debate_min = int(debate_min)
        vote_min = int(vote_min)
        durations = [("Opening", open_min), ("General Debate", debate_min), ("Voting", vote_min)]
        for name, v in durations:
            if v < 1:
                raise ValueError(f"{name} must be at least 1 minute.")
    except ValueError as ve:
        error = str(ve)

    access_mode = (access_mode or "open").strip().lower()
    if access_mode not in {"open", "passcode"}:
        error = "Invalid access mode"

    passcode = (passcode or "").strip()
    if access_mode == "passcode" and not passcode:
        error = "Passcode is required for private events"

    if error:
        preset = SimpleNamespace(
            title=title,
            start_local=(
                dt_local.astimezone(JST).strftime("%Y-%m-%dT%H:%M")
                if isinstance(dt_local, datetime)
                else start_local
            ),
            open_min=open_min,
            debate_min=debate_min,
            vote_min=vote_min,
            access_mode=access_mode,
        )
        is_admin, is_president, is_chair = _flags(user)
        ctx = {
            "actor_is_admin": is_admin,
            "actor_is_president": is_president,
            "actor_is_chair": is_chair,
            "actor_id": user.id,
            "tab": "events",
            "error": error,
            "preset": preset,
        }
        return render("admin_home.html", request, **ctx)

    start_utc = dt_local.astimezone(timezone.utc)
    open_end = start_utc + timedelta(minutes=open_min)
    debate_end = open_end + timedelta(minutes=debate_min)
    vote_end = debate_end + timedelta(minutes=vote_min)

    mode_enum = EventAccessMode.passcode if access_mode == "passcode" else EventAccessMode.open
    passcode_hash = hash_password(passcode) if mode_enum == EventAccessMode.passcode else None

    with get_session() as s:  # type: Session
        evt = Event(
            title=title.strip(),
            starts_at=start_utc,
            ends_at=vote_end,
            access_mode=mode_enum,
            passcode_hash=passcode_hash,
            created_by_id=user.id,
        )
        s.add(evt)
        s.flush()

        s.add_all([
            EventStage(event_id=evt.id, name="Opening",        starts_at=start_utc, ends_at=open_end),
            EventStage(event_id=evt.id, name="General Debate", starts_at=open_end,  ends_at=debate_end),
            EventStage(event_id=evt.id, name="Voting",         starts_at=debate_end, ends_at=vote_end),
        ])

        s.commit()
        s.refresh(evt)

    return RedirectResponse("/admin?tab=events", status_code=303)

from sqlalchemy import text, bindparam

def hard_delete_event(session, event_id: int):
    session.exec(text("DELETE FROM agenda_proposal WHERE event_id = :id"), {"id": event_id})
    session.exec(text("DELETE FROM event_stage WHERE event_id = :id"), {"id": event_id})
    session.exec(text("DELETE FROM event WHERE id = :id"), {"id": event_id})

# GET -> show a tiny confirm page with a POST form (so clicks on links won't 405)
@app.get("/admin/events/{event_id}/delete")
@app.get("/admin/events/{event_id}/delete/", include_in_schema=False)
def admin_delete_event_confirm(event_id: int, next: Optional[str] = Query("/admin/events")):
    dest = next or "/admin/events"
    return HTMLResponse(f"""
      <!doctype html><meta charset="utf-8">
      <div style="max-width:600px;margin:3rem auto;font-family:system-ui">
        <h1>Delete event?</h1>
        <p>This will also delete its proposals and stages.</p>
        <form method="post" action="/admin/events/{event_id}/delete?next={quote(dest)}">
          <button style="padding:.5rem 1rem;background:#dc2626;color:#fff;border-radius:.5rem;border:0">Delete</button>
          <a href="{dest}" style="margin-left:1rem">Cancel</a>
        </form>
      </div>
    """)

# POST/DELETE -> perform deletion, always redirect (no JSON)
@app.api_route("/admin/events/{event_id}/delete", methods=["POST", "DELETE"])
@app.api_route("/admin/events/{event_id}/delete/", methods=["POST", "DELETE"], include_in_schema=False)
def admin_delete_event(request: Request, event_id: int, next: Optional[str] = Query("/admin/events/?deleted=1")):
    dest = next or "/admin/events/?deleted=1"
    with get_session() as s:
        ev = s.get(Event, event_id)
        if ev:
            s.delete(ev)
            try:
                s.commit()
            except Exception:
                s.rollback()
                raise
    return RedirectResponse(dest, status_code=303)

@app.get("/admin/events")
def admin_events_no_slash():
    # 307 keeps the method; for GET it’s perfect, and browsers will follow.
    return RedirectResponse("/admin?tab=events", status_code=307)

@app.get("/events", response_class=HTMLResponse)
def events_index(request: Request):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    if not _is_event_member(user):
        raise HTTPException(status_code=403, detail="Members only")

    now_utc = datetime.now(timezone.utc)

    with get_session() as s:
        rows = s.exec(
            select(Event)
            .options(selectinload(Event.stages))
            .order_by(Event.starts_at.asc())
        ).all()

        events = []
        for evt in rows:
            has_access = _user_has_event_access(s, user, evt)
            state = "past"
            if evt.starts_at <= now_utc and (evt.ends_at is None or evt.ends_at > now_utc):
                state = "live"
            elif evt.starts_at > now_utc:
                state = "upcoming"

            events.append({
                "id": evt.id,
                "title": evt.title,
                "starts_at": evt.starts_at,
                "ends_at": evt.ends_at,
                "state": state,
                "access_mode": evt.access_mode,
                "locked": not has_access,
                "stages": evt.stages_json,
            })

    return templates.TemplateResponse(
        "events/index.html",
        {
            "request": request,
            "user": user,
            "flags": effective_flags(user),
            "events": events,
        },
    )

@app.get("/events/{event_id}/menu", response_class=HTMLResponse)
def event_menu(request: Request, event_id: int):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    with get_session() as s:
        event = _require_event_access(db=s, user=user, event_id=event_id)

        event_ctx = {
            "id": event.id,
            "title": event.title,
            "starts_at_iso": to_iso_z(event.starts_at),
            "ends_at_iso": to_iso_z(event.ends_at) if event.ends_at else None,
            "stages": [
                {
                    "name": st.name,
                    "kind": (
                        "voting" if "vot" in st.name.lower()
                        else "debate" if "debate" in st.name.lower()
                        else "opening" if "open" in st.name.lower()
                        else "stage"
                    ),
                    "start": to_iso_z(st.starts_at),
                    "end": to_iso_z(st.ends_at),
                }
                for st in sorted(event.stages, key=lambda x: x.starts_at)
            ],
        }

    return templates.TemplateResponse(
        "events_menu.html",
        {
            "request": request,
            "user": user,
            "flags": effective_flags(user),
            "event": event_ctx,
        },
    )

def _normalize_url(u: str | None) -> str | None:
    if not u: return None
    u = u.strip()
    if not u: return None
    parsed = urlparse(u if "://" in u else f"https://{u}")
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed.geturl()

from datetime import datetime, timezone
from sqlmodel import select

def _parse_iso_aware(s: str | None):
    if not s:
        return None
    try:
        s = s.strip()
        # Accept both ...Z and ...+00:00
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _to_aware_utc(dt):
    if not dt:
        return None
    if isinstance(dt, str):
        return _parse_iso_aware(dt)
    if isinstance(dt, datetime):
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return None

def _coerce_dt(obj, *names):
    """Try multiple attribute names; fall back to *.starts_at_iso / *.ends_at_iso strings."""
    for n in names:
        if hasattr(obj, n):
            v = getattr(obj, n)
            av = _to_aware_utc(v)
            if av:
                return av
    # common iso string props
    for n in ("starts_at_iso", "start_iso", "ends_at_iso", "end_iso"):
        if hasattr(obj, n):
            av = _to_aware_utc(getattr(obj, n))
            if av:
                return av
    return None

def _get_current_event(session):
    """Pick live -> next -> last, comparing UTC-aware datetimes only."""
    events = session.exec(select(Event)).all()
    if not events:
        return None

    now = datetime.now(timezone.utc)

    def start_dt(e):
        return _coerce_dt(e, "starts_at", "starts_at_utc", "start_at", "start_time")

    def end_dt(e):
        return _coerce_dt(e, "ends_at", "ends_at_utc", "end_at", "end_time")

    with_starts = [(e, start_dt(e)) for e in events]
    with_starts = [(e, s) for (e, s) in with_starts if s is not None]

    if not with_starts:
        return events[0]

    # 1) live (start <= now <= end or no end)
    live = []
    for e, s in with_starts:
        ed = end_dt(e)
        if s <= now and (ed is None or now <= ed):
            live.append((e, s))
    if live:
        live.sort(key=lambda t: t[1], reverse=True)
        return live[0][0]

    # 2) next upcoming
    upcoming = [(e, s) for (e, s) in with_starts if s > now]
    if upcoming:
        upcoming.sort(key=lambda t: t[1])
        return upcoming[0][0]

    # 3) latest past
    past = [(e, s) for (e, s) in with_starts if s <= now]
    past.sort(key=lambda t: t[1], reverse=True)
    return past[0][0] if past else events[0]

@app.get("/events/{event_id}/propose-agenda", response_class=HTMLResponse)
def propose_agenda_get(request: Request, event_id: int):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    with get_session() as session:
        event = _require_event_access(db=session, user=user, event_id=event_id)

        mine = session.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.proposer_id == user.id,
            )
            .order_by(AgendaProposal.created_at.desc())
        ).all()

        return templates.TemplateResponse(
            "events/propose_agenda.html",
            {
                "request": request,
                "user": user,
                "flags": effective_flags(user),
                "event": event,
                "mine": mine,
                "ok": request.query_params.get("ok"),
                "err": request.query_params.get("err"),
            },
        )

@app.post("/events/{event_id}/propose-agenda")
def propose_agenda_post(
    request: Request,
    event_id: int,
    title: str = Form(...),
    background: str = Form(...),
    source_url: str = Form(""),
    submit_anonymously: bool = Form(False),
):
    user = current_user(request)
    if user is None:
        qs = urlencode({"next": str(request.url)})
        return RedirectResponse(f"/login?{qs}", status_code=303)

    title = (title or "").strip()
    background = (background or "").strip()
    url = _normalize_url(source_url)

    if not title or not background:
        return RedirectResponse(
            f"/events/{event_id}/propose-agenda?err=Missing+required+fields",
            status_code=303,
        )
    if source_url and not url:
        return RedirectResponse(
            f"/events/{event_id}/propose-agenda?err=Invalid+URL+format",
            status_code=303,
        )

    with get_session() as session:
        event = _require_event_access(db=session, user=user, event_id=event_id)

        proposal = AgendaProposal(
            event_id=event.id,
            proposer_id=None if submit_anonymously else user.id,
            title=title,
            background=background,
            source_url=url,
            status=ProposalStatus.pending,
        )
        session.add(proposal)
        session.commit()

    return RedirectResponse(f"/events/{event_id}/propose-agenda?ok=1", status_code=303)

from fastapi import Request, HTTPException, Form, Path
from fastapi.responses import RedirectResponse
from sqlmodel import select
from datetime import datetime

def _require_president(user):
    if not user or not (has_role(user, "president") or has_role(user, "chairman")):
        raise HTTPException(status_code=403, detail="President/Chairman access required")

@app.get("/events/{event_id}/review-agenda", response_class=HTMLResponse)
def review_agenda_get(event_id: int, request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    flags = role_flags(user)
    if not (flags.get("IS_PRESIDENT") or flags.get("IS_CHAIR") or flags.get("IS_ADMIN")):
        raise HTTPException(403)

    with get_session() as s:
        event = _get_event_or_404(db=s, event_id=event_id)

        pending = s.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status == ProposalStatus.pending,
            )
            .options(selectinload(AgendaProposal.proposer))
            .order_by(AgendaProposal.created_at.asc())
        ).all()

        decided = s.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status.in_(
                    [ProposalStatus.accepted, ProposalStatus.rejected]
                ),
            )
            .options(selectinload(AgendaProposal.proposer))
            .order_by(AgendaProposal.decided_at.desc(), AgendaProposal.id.desc())
            .limit(50)
        ).all()

        return templates.TemplateResponse(
            "events/review_agenda.html",
            {
                "request": request,
                "user": user,
                "flags": effective_flags(user),
                "event": event,
                "pending": pending,
                "decided": decided,
                "ok": request.query_params.get("ok"),
                "err": request.query_params.get("err"),
            },
        )
        # event = _require_event_access(db=s, user=user, event_id=event_id)

        # proposals = s.exec(
        #     select(AgendaProposal)
        #     .where(AgendaProposal.event_id == event.id)
        #     .options(selectinload(AgendaProposal.proposer))
        #     .order_by(AgendaProposal.created_at.asc())
        # ).all()

        # return templates.TemplateResponse(
        #     "events/review_agenda.html",
        #     {
        #         "request": request,
        #         "user": user,
        #         "flags": flags,
        #         "event": event,
        #         "proposals": proposals,
        #     },
        # )


@app.post("/events/{event_id}/review-agenda/{proposal_id}/decision")
def review_agenda_decide(
    request: Request,
    event_id: int,
    proposal_id: int,
    action: str = Form(...),
    notes: str = Form(""),
):
    user = current_user(request)
    _require_president(user)

    action = (action or "").strip().lower()
    if action not in ("accept", "reject", "reopen"):
        raise HTTPException(status_code=400, detail="Invalid action")

    with get_session() as s:
        event = _get_event_or_404(db=s, event_id=event_id)

        p = s.get(AgendaProposal, proposal_id)
        if not p or p.event_id != event.id:
            return RedirectResponse(
                f"/events/{event_id}/review-agenda?err=Proposal+not+found",
                status_code=303,
            )

        if action == "reopen":
            p.status = ProposalStatus.pending
            p.decided_by_id = None
            p.decided_at = None
            p.notes = (notes or "").strip() or p.notes
        else:
            p.status = (
                ProposalStatus.accepted
                if action == "accept"
                else ProposalStatus.rejected
            )
            p.decided_by_id = user.id if user else None
            p.decided_at = datetime.utcnow()
            p.notes = (notes or "").strip()

        s.add(p)
        s.commit()

    return RedirectResponse(f"/events/{event_id}/review-agenda?ok=1", status_code=303)

@app.get("/events/{event_id}/view-agenda", response_class=HTMLResponse)
def view_agenda_get(event_id: int, request: Request):
    user = current_user(request)
    with get_session() as s:
        event = _require_event_access(db=s, user=user, event_id=event_id)

        proposals = s.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status == ProposalStatus.accepted,
            )
            .options(selectinload(AgendaProposal.proposer))
            .order_by(AgendaProposal.created_at.asc())
        ).all()

        return templates.TemplateResponse(
            "events/view_agenda.html",
            {
                "request": request,
                "user": user,
                "flags": role_flags(user),
                "event": event,
                "proposals": proposals,
            },
        )

# List page: all accepted agenda items for the current event
@app.get("/events/{event_id}/general-floor", response_class=HTMLResponse)
def general_floor_index(event_id: int, request: Request):
    user = current_user(request)
    with get_session() as s:
        event = _require_event_access(db=s, user=user, event_id=event_id)

        proposals = s.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status == ProposalStatus.accepted,
            )
            .options(selectinload(AgendaProposal.proposer))
            .order_by(AgendaProposal.created_at.asc())
        ).all()

        return templates.TemplateResponse(
            "events/general_floor.html",
            {
                "request": request,
                "user": user,
                "flags": role_flags(user),
                "event": event,
                "proposals": proposals,
            },
        )

def ensure_general_floor_question(db: Session, prop: AgendaProposal) -> Question:
    """
    Ensure there's a Question for this accepted AgendaProposal and return it.
    Uses GeneralFloorLink (proposal_id -> question_id).
    """
    link = db.exec(
        select(GeneralFloorLink).where(GeneralFloorLink.proposal_id == prop.id)
    ).first()

    if link:
        q = db.get(Question, link.question_id)
        if q:
            return q
        # link exists but question missing — recreate it
        db.delete(link)
        db.commit()

    # Create a new Question seeded from the proposal
    q = Question(
        event_id=prop.event_id,
        text=prop.title,
        rapporteur_id=(prop.proposer_id or 0),
        # decision_points_json="[]",   # or seed from somewhere if you have points
    )
    db.add(q)
    db.commit()
    db.refresh(q)

    # Create the bridge row
    gfl = GeneralFloorLink(proposal_id=prop.id, question_id=q.id)
    db.add(gfl)
    db.commit()

    return q

@app.get("/events/{event_id}/general-floor/{pid}", response_class=HTMLResponse)
def show_general_floor_item(event_id: int, pid: int, request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=303)

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)

        # backing Question/thread
        q = ensure_general_floor_question(db, prop)

        # fetch users + roles
        all_users = db.exec(select(User).options(selectinload(User.roles))).all()
        user_map = {u.id: u for u in all_users}
        role_map = {u.id: sorted({r.name for r in (u.roles or [])}) for u in all_users}

        # interventions
        ints = db.exec(
            select(Intervention)
            .where(Intervention.question_id == q.id)
            .order_by(Intervention.created_at.asc())
        ).all()

        # threadify
        by_id = {it.id: {"node": it, "children": []} for it in ints}
        roots = []
        for it in ints:
            if it.relates_to_id and it.relates_to_id in by_id:
                by_id[it.relates_to_id]["children"].append(by_id[it.id])
            else:
                roots.append(by_id[it.id])

        # floor state
        fs = get_or_create_floor(db, q.id)
        cur_req = db.get(SpeakerRequest, fs.current_speaker_request_id) if fs.current_speaker_request_id else None

        can_speak = bool(
            cur_req and cur_req.user_id == user.id and cur_req.status == "SPEAKING"
        )

        flags = effective_flags(user)
        if flags.get("IS_CHAIR") or flags.get("IS_PRESIDENT"):
            can_speak = True

        speakers = ordered_speakers(db, q.id)

        last_child_id = db.exec(
            select(func.max(Intervention.id)).where(
                (Intervention.question_id == q.id)
                & (Intervention.relates_to_id.isnot(None))
            )
        ).first() or 0

        last_any_id = db.exec(
            select(func.max(Intervention.id)).where(
                Intervention.question_id == q.id
            )
        ).first() or 0

        # view-model for the template
        item = {
            "id": q.id,              # keep question id for floor-related actions
            "proposal_id": prop.id,
            "title": prop.title,
            "created_at": q.created_at,
        }

    return templates.TemplateResponse(
        "events/general_floor_item.html",
        {
            "request": request,
            "user": user,
            "flags": flags,
            "event": event,
            "item": item,
            "proposal": prop,
            "q": q,
            "threads": roots,
            "user_map": user_map,
            "role_map": role_map,
            "floor": fs,
            "speakers": speakers,
            "can_speak": can_speak,
            "current_req": cur_req,
            "last_child_id": int(last_child_id),
            "last_any_id": int(last_any_id),
        },
    )

@app.get("/events/{event_id}/general-floor/{pid}/interventions/fragment", response_class=HTMLResponse)
def interventions_fragment(event_id: int, pid: int, request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401)

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)

        q = ensure_general_floor_question(db, prop)

        all_users = db.exec(select(User).options(selectinload(User.roles))).all()
        user_map = {u.id: u for u in all_users}
        role_map = {u.id: sorted({r.name for r in (u.roles or [])}) for u in all_users}

        ints = db.exec(
            select(Intervention)
            .where(Intervention.question_id == q.id)
            .order_by(Intervention.created_at.asc())
        ).all()

        by_id = {it.id: {"node": it, "children": []} for it in ints}
        roots = []
        for it in ints:
            if it.relates_to_id and it.relates_to_id in by_id:
                by_id[it.relates_to_id]["children"].append(by_id[it.id])
            else:
                roots.append(by_id[it.id])

        fs = get_or_create_floor(db, q.id)
        cur_req = db.get(SpeakerRequest, fs.current_speaker_request_id) if fs.current_speaker_request_id else None
        can_speak = bool(cur_req and cur_req.user_id == user.id and cur_req.status == "SPEAKING")

        flags = effective_flags(user)
        if flags.get("IS_CHAIR") or flags.get("IS_PRESIDENT"):
            can_speak = True

    return templates.TemplateResponse(
        "partials/interventions_list.html",
        {
            "request": request,
            "user": user,
            "flags": flags,
            "event": event,
            "threads": roots,
            "user_map": user_map,
            "role_map": role_map,
            "q": q,
            "can_speak": can_speak,
        },
    )

# GET /events/{event_id}/general-floor/{pid}/interventions/head
@app.get("/events/{event_id}/general-floor/{pid}/interventions/head")
def interventions_head(event_id: int, pid: int, request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    with get_session() as db:
        _event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event_id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        last_child_id = db.exec(
            select(func.max(Intervention.id)).where(
                (Intervention.question_id == q.id)
                & (Intervention.relates_to_id.isnot(None))
            )
        ).first() or 0

        last_any_id = db.exec(
            select(func.max(Intervention.id)).where(
                Intervention.question_id == q.id
            )
        ).first() or 0

    return {"last_child_id": int(last_child_id), "last_any_id": int(last_any_id)}

from app.services.live_summary import Scope, refresh_summary_if_needed

@router.get("/api/live_summary")
async def api_live_summary(
    kind: str,
    question_id: int | None = None,
    room_id: int | None = None,
    event_id: int | None = None,
    proposal_id: int | None = None,
    draft_id: int | None = None,
    amendment_id: int | None = None,
):
    kind = kind.upper()
    if kind not in ("GENERAL", "PROOM", "PFLOOR"):
        raise HTTPException(400, "kind must be GENERAL|PROOM|PFLOOR")

    if kind == "GENERAL":
        if question_id is None:
            raise HTTPException(400, "question_id required for GENERAL")
        scope = Scope(kind="GENERAL", question_id=question_id)

    elif kind == "PROOM":
        if room_id is None:
            raise HTTPException(400, "room_id required for PROOM")
        scope = Scope(kind="PROOM", room_id=room_id)

    else:
        if event_id is None or proposal_id is None:
            raise HTTPException(400, "event_id and proposal_id required for PFLOOR")
        if (draft_id is None) == (amendment_id is None):
            raise HTTPException(400, "exactly one of draft_id or amendment_id is required for PFLOOR")
        scope = Scope(
            kind="PFLOOR",
            event_id=event_id,
            proposal_id=proposal_id,
            draft_id=draft_id,
            amendment_id=amendment_id,
        )

    with get_session() as db:
        row = await refresh_summary_if_needed(db, scope)
        return {
            "scope_key": row.scope_key,
            "updated_at": row.updated_at.isoformat(),
            "summary": row.summary,
        }

from app.services.live_summary import Scope, mark_dirty

# POST /events/{event_id}/general-floor/{pid}/interventions
@app.post("/events/{event_id}/general-floor/{pid}/interventions")
def post_intervention_for_agenda(
    pid: int,
    event_id: int,
    request: Request,
    # point_key: str = "",
    body: str = Form(...),
    relates_to_id: str = Form(""),   # accept raw string
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    # normalize: "" -> None, otherwise int(...)
    rel_id = None
    if relates_to_id is not None and str(relates_to_id).strip() != "":
        try:
            rel_id = int(relates_to_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="relates_to_id must be an integer")

    with get_session() as db:
        # # 1) Check agenda item exists and is ACCEPTED
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # 2) Ensure/resolve backing Question for this general floor item
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)
        qid = q.id

        # Optional safety: if replying, ensure target intervention belongs to this question
        if rel_id:
            target = db.get(Intervention, rel_id)
            if not target or target.question_id != qid:
                raise HTTPException(status_code=400, detail="Invalid reply target")

        # 3) Privileged users can auto-take the floor, else enforce floor possession
        if is_privileged(user) and not user_has_floor(db, qid, user.id):
            k = "ROR" if rel_id else "CHAIR"
            force_take_floor(db, qid, user.id, kind=k, target=rel_id)

        if not user_has_floor(db, qid, user.id):
            raise HTTPException(status_code=403, detail="You do not currently have the floor.")

        # 4) Create intervention
        it = _insert_intervention(
            db, qid=qid, user_id=user.id, body=body, relates_to_id=rel_id
        )
        db.add(it)

        # 5) AUTO-FINISH: mark current speaker DONE and clear current
        fs = db.exec(select(FloorState).where(FloorState.question_id == qid)).first()
        if fs and fs.current_speaker_request_id:
            cur = db.get(SpeakerRequest, fs.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur.status = "DONE"
                db.add(cur)
            fs.current_speaker_request_id = None
            fs.updated_at = datetime.utcnow()
            db.add(fs)
        db.commit()
        mark_dirty(db, Scope(kind="GENERAL", question_id=qid))

    # back to the agenda item page
    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# POST /events/{event_id}/general-floor/{pid}/floor/register
@app.post("/events/{event_id}/general-floor/{pid}/floor/register")
def floor_register_for_agenda(
    pid: int,
    event_id: int,
    request: Request,
    kind: str = Form("GENERAL"),     # "GENERAL" | "ROR" | "ROR_ALL"
    relates_to_id: str = Form(""),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    # normalize reply target (used only for ROR)
    target_id = None
    if kind == "ROR" and str(relates_to_id).strip():
        try:
            target_id = int(relates_to_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="relates_to_id must be an integer")

    with get_session() as db:
        # # ensure agenda item exists and is ACCEPTED
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # ensure/resolve backing Question for this agenda item
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)
        qid = q.id

        fs = get_or_create_floor(db, qid)

        # when floor is closed, only ROR / ROR_ALL is allowed
        if not fs.is_open and kind not in ("ROR", "ROR_ALL"):
            raise HTTPException(status_code=400, detail="Speaker list is closed (use Right of Reply).")

        # prevent duplicate active entry (QUEUED or SPEAKING) for this user on this item
        existing = db.exec(
            select(SpeakerRequest).where(
                (SpeakerRequest.question_id == qid)
                & (SpeakerRequest.user_id == user.id)
                & (SpeakerRequest.status.in_(["QUEUED", "SPEAKING"]))
            )
        ).first()
        if existing:
            return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

        # next queue position among QUEUED
        row = db.exec(
            select(func.max(SpeakerRequest.position)).where(
                (SpeakerRequest.question_id == qid)
                & (SpeakerRequest.status == "QUEUED")
            )
        ).first()
        maxpos = (row[0] if isinstance(row, tuple) else row) if row is not None else None
        nextpos = 1 if maxpos is None else int(maxpos) + 1

        req = SpeakerRequest(
            question_id=qid,
            user_id=user.id,
            kind=kind,                        # supports "ROR_ALL"
            status="QUEUED",
            position=nextpos,
            target_intervention_id=target_id, # None for GENERAL / ROR_ALL
        )
        db.add(req)
        db.commit()

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# POST /events/{event_id}/general-floor/{pid}/floor/withdraw
@app.post("/events/{event_id}/general-floor/{pid}/floor/withdraw")
def floor_withdraw_for_agenda(pid: int, request: Request, event_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    with get_session() as db:
        # # ensure agenda item exists and is ACCEPTED
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # resolve backing Question for this agenda item
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        # find this user's queued request on this item
        req = db.exec(
            select(SpeakerRequest).where(
                (SpeakerRequest.question_id == q.id)
                & (SpeakerRequest.user_id == user.id)
                & (SpeakerRequest.status == "QUEUED")
            )
        ).first()

        if req:
            req.status = "WITHDRAWN"
            db.add(req)
            db.commit()

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# POST /events/{event_id}/general-floor/{pid}/floor/toggle
@app.post("/events/{event_id}/general-floor/{pid}/floor/toggle")
def floor_toggle_for_agenda(pid: int, request: Request, event_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    if not (flags["IS_PRESIDENT"] or flags["IS_CHAIR"]):
        raise HTTPException(status_code=403, detail="Only president or chairman can toggle the floor")

    with get_session() as db:
        # # ensure agenda item exists and is ACCEPTED
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # back this agenda item with a Question (create if missing)
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        fs = get_or_create_floor(db, q.id)
        fs.is_open = not fs.is_open
        fs.updated_at = datetime.utcnow()
        db.add(fs)
        db.commit()

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# POST /events/{event_id}/general-floor/{pid}/floor/call_next
@app.post("/events/{event_id}/general-floor/{pid}/floor/call_next")
def floor_call_next_for_agenda(pid: int, request: Request, event_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    if not (flags["IS_PRESIDENT"] or flags["IS_CHAIR"]):
        raise HTTPException(status_code=403, detail="Only president or chairman can call speakers")

    with get_session() as db:
        # # agenda must exist and be accepted
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # back it with (or fetch) the underlying Question
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        # floor state for this question
        fs = get_or_create_floor(db, q.id)

        # priority order: ROR_ALL > ROR > GENERAL, then position ASC
        from sqlalchemy import case
        try:
            _ = KIND_PRIORITY  # reuse if already defined elsewhere
        except NameError:
            KIND_PRIORITY = case(
                (SpeakerRequest.kind == "ROR_ALL", 3),
                (SpeakerRequest.kind == "ROR", 2),
                else_=1,
            )

        next_req = db.exec(
            select(SpeakerRequest)
            .where(
                (SpeakerRequest.question_id == q.id)
                & (SpeakerRequest.status == "QUEUED")
            )
            .order_by(KIND_PRIORITY.desc(), SpeakerRequest.position.asc())
        ).first()

        if not next_req:
            return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

        # finish current if any
        if fs.current_speaker_request_id:
            prev = db.get(SpeakerRequest, fs.current_speaker_request_id)
            if prev and prev.status == "SPEAKING":
                prev.status = "DONE"
                db.add(prev)

        # promote next to SPEAKING
        next_req.status = "SPEAKING"
        db.add(next_req)

        fs.current_speaker_request_id = next_req.id
        fs.updated_at = datetime.utcnow()
        db.add(fs)
        db.commit()

        # notify the selected user
        label = (
            "Right of Reply to all" if next_req.kind == "ROR_ALL"
            else (f"Right of Reply to #{next_req.target_intervention_id}"
                  if next_req.kind == "ROR" else "You have the floor")
        )
        msg = f"You have the floor — {label}: \"{prop.title[:120]}\""
        note = Notification(
            user_id=next_req.user_id,
            question_id=q.id,
            type="FLOOR",
            message=msg,
            is_read=False,
        )
        db.add(note)
        db.commit()

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# POST /events/{event_id}/general-floor/{pid}/floor/finish_current
@app.post("/events/{event_id}/general-floor/{pid}/floor/finish_current")
def floor_finish_current_for_agenda(pid: int, request: Request, event_id: int):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    if not (flags["IS_PRESIDENT"] or flags["IS_CHAIR"]):
        raise HTTPException(status_code=403, detail="Only president or chairman can finish speakers")

    with get_session() as db:
        # # agenda must exist and be accepted
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # get/create the backing Question
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        fs = get_or_create_floor(db, q.id)
        if fs.current_speaker_request_id:
            cur = db.get(SpeakerRequest, fs.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur.status = "DONE"
                db.add(cur)
            fs.current_speaker_request_id = None
            fs.updated_at = datetime.utcnow()
            db.add(fs)
            db.commit()

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

# GET /events/{event_id}/general-floor/{pid}/floor/state
@app.get("/events/{event_id}/general-floor/{pid}/floor/state")
def floor_state_for_agenda(pid: int, request: Request, event_id: int):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401)

    with get_session() as db:
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(status_code=404, detail="Agenda item not found or not accepted")

        # # Ensure / fetch the backing Question for this agenda item
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        fs = db.exec(select(FloorState).where(FloorState.question_id == q.id)).first()
        if not fs:
            fs = get_or_create_floor(db, q.id)

        cur_user_id = None
        cur_target = None
        cur_kind = None
        target_local = None
        if fs.current_speaker_request_id:
            cur_req = db.get(SpeakerRequest, fs.current_speaker_request_id)
            if cur_req and cur_req.status == "SPEAKING":
                cur_user_id = cur_req.user_id
                cur_target = cur_req.target_intervention_id
                cur_kind = cur_req.kind

        speakers = ordered_speakers(db, q.id)

        # id -> handle map
        users = db.exec(select(User)).all()
        handle_map = {u.id: u.handle for u in users}

        if cur_target:
            tgt = db.get(Intervention, cur_target)
            target_local = tgt.local_no if tgt else None

        data = {
            "is_open": fs.is_open,
            "speaking_time_sec": fs.speaking_time_sec,
            "current_req_id": fs.current_speaker_request_id,
            "current_user_id": cur_user_id,
            "current_kind": cur_kind,
            "current_target_intervention_id": cur_target,
            "current_target_local_no": target_local,
            "speakers": [
                {
                    "id": s.id,
                    "user_id": s.user_id,
                    "handle": handle_map.get(s.user_id, str(s.user_id)),
                    "kind": s.kind,
                    "status": s.status,
                    "position": s.position,
                    "created_at": s.created_at.isoformat(),
                } for s in speakers
            ],
        }
    return JSONResponse(data)

# POST /events/{event_id}/general-floor/{pid}/floor/take_now
@app.post("/events/{event_id}/general-floor/{pid}/floor/take_now")
def floor_take_now_for_agenda(
    pid: int,
    event_id: int,
    request: Request,
    message: str = Form(""),
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    # Only chair or president may take the floor / announce
    if not (flags["IS_CHAIR"] or flags["IS_PRESIDENT"]):
        raise HTTPException(403, "Only the chair/president can take the floor")

    with get_session() as db:
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(404, detail="Agenda item not found or not accepted")

        # # Ensure backing Question exists for this agenda item
        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        # 1) Finish any current speaker
        fs = db.exec(select(FloorState).where(FloorState.question_id == q.id)).first()
        if fs and fs.current_speaker_request_id:
            cur = db.get(SpeakerRequest, fs.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur.status = "DONE"
                db.add(cur)
            fs.current_speaker_request_id = None
            fs.updated_at = datetime.utcnow()
            db.add(fs)

        # 2) Broadcast announcement (no post to the thread)
        chair = db.get(User, user.id)
        chair_label = f"@{chair.handle}" if chair and chair.handle else "Chair"
        text = (message or "").strip()
        payload = {"kind": "ANNOUNCE", "question_id": q.id, "proposal_id": pid}

        users = db.exec(select(User)).all()
        for u in users:
            if u.id == user.id:
                continue
            note = Notification(
                user_id=u.id,
                question_id=q.id,
                type="ANNOUNCE",
                message=(f"{chair_label}: {text}" if text else f"{chair_label} made an announcement."),
                is_read=False,
                payload_json=json.dumps(payload),
            )
            db.add(note)

        db.commit()

    # Back to the agenda item page
    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=303)

# POST /events/{event_id}/general-floor/{pid}/floor/invite_ror
@app.post("/events/{event_id}/general-floor/{pid}/floor/invite_ror")
def floor_invite_ror_for_agenda(
    pid: int,
    event_id: int,
    request: Request,
    relates_to_id: str = Form(""),
    to_handle: str = Form(...),
    kind: str = Form("ROR"),   # "ROR" | "ROR_ALL"
):
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    if not (flags["IS_PRESIDENT"] or flags["IS_CHAIR"]):
        raise HTTPException(403, "Only president or chairman can invite RoR")

    with get_session() as db:
        # # Find accepted agenda item and ensure a backing Question exists
        # prop = db.get(AgendaProposal, pid)
        # if not prop or prop.status != ProposalStatus.accepted:
        #     raise HTTPException(404, detail="Agenda item not found or not accepted")

        # q = ensure_general_floor_question(db, prop)
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)
        q = ensure_general_floor_question(db, prop)

        to_user = db.exec(select(User).where(User.handle == to_handle.lstrip("@"))).first()
        if not to_user:
            raise HTTPException(404, "User handle not found")

        # Validate/normalize target for targeted RoR
        target_id = None
        k = (kind or "ROR").upper()
        if k == "ROR":
            if not relates_to_id or not str(relates_to_id).strip().isdigit():
                raise HTTPException(400, "relates_to_id required for targeted RoR")
            target_id = int(relates_to_id)
        elif k == "ROR_ALL":
            target_id = None
        else:
            raise HTTPException(400, "invalid kind")

        inv = RorInvite(
            question_id=q.id,
            target_intervention_id=target_id,  # nullable for ROR_ALL
            from_user_id=user.id,
            to_user_id=to_user.id,
            kind=k,
            status="PENDING",
        )
        db.add(inv); db.commit(); db.refresh(inv)

        note = Notification(
            user_id=to_user.id,
            question_id=q.id,
            type="INVITE_ROR",
            message=f'Chair invited you to Right of Reply{(" on intervention #" + str(target_id)) if target_id else " (to all)"}',
            is_read=False,
            payload_json=json.dumps({
                "invite_id": inv.id,
                "kind": k,  # "ROR" | "ROR_ALL"
                "target_intervention_id": target_id,  # may be None
                "question_id": q.id,
                "proposal_id": pid,
            }),
        )
        db.add(note); db.commit()

    wants_json = request.query_params.get("ajax") == "1" or \
                 "application/json" in request.headers.get("accept", "")
    if wants_json:
        return JSONResponse({"ok": True, "invite_id": inv.id, "status": inv.status})

    return RedirectResponse(url=f"/events/{event_id}/general-floor/{pid}", status_code=302)

from sqlalchemy import func, text

def _next_local_no(db, qid: int) -> int:
    row = db.exec(
        select(func.max(Intervention.local_no)).where(Intervention.question_id == qid)
    ).first()
    mx = (row[0] if isinstance(row, tuple) else row) or 0
    return int(mx) + 1

def _insert_intervention(db, *, qid: int, user_id: int, body: str, relates_to_id: int | None):
    # Tiny retry if two writers race to the same local_no
    for attempt in (1, 2):
        try:
            ln = _next_local_no(db, qid)
            it = Intervention(
                question_id=qid,
                by_user=user_id,
                local_no=ln,
                # point_key="",          # until you drop the column
                body=body,
                relates_to_id=relates_to_id,
            )
            db.add(it)
            db.commit()
            return it
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise

import json
from sqlmodel import select

def _agenda_for_draft(db, draft: ProposalDraft) -> Tuple[Optional[int], str]:
    """
    Resolve the agenda item for the draft:
    Agenda == the Question behind the AgendaProposal (via GeneralFloorLink).
    """
    gfl = db.exec(select(GeneralFloorLink).where(GeneralFloorLink.proposal_id == draft.proposal_id)).first()
    if not gfl:
        return (None, "—")
    q = db.get(Question, gfl.question_id)
    return (gfl.question_id, (q.text if q else f"Question #{gfl.question_id}"))

def _already_cosigned(draft: ProposalDraft, user_id: int) -> bool:
    """
    Supports both legacy list[int] and new list[dict{user_id,is_late,created_at}].
    """
    arr = draft.cosigners_json or []
    for c in arr:
        if isinstance(c, dict):
            if int(c.get("user_id", 0)) == int(user_id):
                return True
        else:
            if int(c) == int(user_id):
                return True
    return False

def _append_late_cosign(draft: ProposalDraft, user_id: int):
    arr = list(draft.cosigners_json or [])
    arr.append({
        "user_id": int(user_id),
        "created_at": datetime.utcnow().isoformat(),
        "is_late": True,
    })
    draft.cosigners_json = arr

# --- Proposal Discussion landing: list accepted agenda items for the current event
@app.get("/events/{event_id}/proposal-discussion", response_class=HTMLResponse)
def proposal_discussion_index(event_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)

        from sqlalchemy import func

        props = db.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status == ProposalStatus.accepted,
            )
            .order_by(AgendaProposal.created_at.asc())
        ).all()

        counts = dict(
            db.exec(
                select(ProposalRoom.proposal_id, func.count(ProposalRoom.id))
                .where(ProposalRoom.event_id == event.id)
                .group_by(ProposalRoom.proposal_id)
            ).all()
        )

    return templates.TemplateResponse(
        "proposal_discussion/index.html",
        {
            "request": request,
            "user": user,
            "flags": effective_flags(user),
            "event": event,
            "proposals": props,
            "room_counts": counts,
        },
    )

@app.get("/events/{event_id}/proposal-discussion/{pid}", response_class=HTMLResponse)
def rooms_index(event_id: int, pid: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)

        rooms = db.exec(
            select(ProposalRoom)
            .where(
                ProposalRoom.event_id == event.id,
                ProposalRoom.proposal_id == pid,
            )
            .options(selectinload(ProposalRoom.sponsor))
            .order_by(ProposalRoom.created_at.desc())
        ).all()

    return templates.TemplateResponse(
        "rooms/index.html",
        {
            "request": request,
            "user": user,
            "flags": effective_flags(user),
            "event": event,
            "proposal": prop,
            "rooms": rooms,
        },
    )


# Create a room (creator becomes sponsor)
@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms")
def rooms_create(
    event_id: int,
    pid: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    title = (title or "").strip()
    if not title:
        raise HTTPException(400, "Title required")

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)

        room = ProposalRoom(
            event_id=event.id,
            proposal_id=pid,
            title=title[:160],
            description=(description or "").strip() or None,
            sponsor_id=user.id,
        )
        db.add(room)
        db.commit()
        db.refresh(room)

        draft = ProposalDraft(
            event_id=event.id,
            proposal_id=pid,
            room_id=room.id,
            sponsor_id=user.id,
        )
        db.add(draft)
        db.commit()

    return RedirectResponse(
        url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{room.id}",
        status_code=303,
    )

# imports near top
from sqlalchemy import delete
from fastapi import HTTPException, Form
from starlette.responses import RedirectResponse

# DELETE a room
@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/delete")
def rooms_delete(event_id: int, pid: int, rid: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event_id, pid, accepted_only=True)
        room = _get_event_room_or_404(db, event_id, prop.id, rid)

        f = effective_flags(user)
        can_delete = (
            room.sponsor_id == user.id
            or f.get("IS_PRESIDENT")
            or f.get("IS_CHAIR")
            or f.get("IS_CHAIRMAN")
        )
        if not can_delete:
            raise HTTPException(403, "Not allowed to delete this room")

        db.exec(delete(ProposalMessage).where(ProposalMessage.room_id == rid))
        db.exec(delete(ProposalDraft).where(ProposalDraft.room_id == rid))
        db.exec(delete(ProposalRoom).where(ProposalRoom.id == rid))
        db.commit()

    return RedirectResponse(
        url=f"/events/{event_id}/proposal-discussion/{pid}",
        status_code=303,
    )

@app.get("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}", response_class=HTMLResponse)
def rooms_show(event_id: int, pid: int, rid: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        prop = _get_event_proposal_or_404(db, event.id, pid, accepted_only=True)

        room = db.exec(
            select(ProposalRoom)
            .where(
                ProposalRoom.id == rid,
                ProposalRoom.event_id == event.id,
                ProposalRoom.proposal_id == pid,
            )
            .options(selectinload(ProposalRoom.sponsor))
        ).one()

        msgs = db.exec(
            select(ProposalMessage)
            .where(ProposalMessage.room_id == rid)
            .order_by(ProposalMessage.created_at)
        ).all()

        by_id = {m.id: {"node": m, "children": []} for m in msgs}
        roots = []
        for m in msgs:
            (by_id[m.parent_id]["children"] if (m.parent_id and m.parent_id in by_id) else roots).append(by_id[m.id])

        draft = db.exec(select(ProposalDraft).where(ProposalDraft.room_id == rid)).first()
        if not draft:
            draft = ProposalDraft(
                event_id=event.id,
                proposal_id=pid,
                room_id=rid,
                sponsor_id=room.sponsor_id,
            )
            db.add(draft)
            db.commit()
            db.refresh(draft)

        users = db.exec(select(User)).all()
        user_map = {u.id: u for u in users}

        try:
            cos_list = (
                draft.cosigners_json
                if isinstance(draft.cosigners_json, list)
                else json.loads(draft.cosigners_json or "[]")
            )
            cos_list = [int(x) for x in cos_list]
        except Exception:
            cos_list = []

    return templates.TemplateResponse(
        "rooms/show.html",
        {
            "request": request,
            "user": user,
            "flags": effective_flags(user),
            "event": event,
            "proposal": prop,
            "room": room,
            "threads": roots,
            "user_map": user_map,
            "draft": draft,
            "cos_list": cos_list,
        },
    )

import sqlalchemy as sa

def next_room_local_no(db, room_id: int) -> int:
    row = db.exec(
        sa.text("SELECT COALESCE(MAX(local_no), 0) AS mx FROM proposalmessage WHERE room_id = :rid")
        .bindparams(sa.bindparam("rid", room_id))
    ).first()
    return int(row.mx) + 1 if row and row.mx is not None else 1

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{room_id}/messages")
def room_post_message(
    event_id: int,
    pid: int,
    room_id: int,
    request: Request,
    body: str = Form(...),
    parent_id: str = Form(""),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    body = (body or "").strip()
    if not body:
        raise HTTPException(400, "Message required")

    parent: Optional[int] = int(parent_id) if parent_id and parent_id.isdigit() else None

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        room = _get_event_room_or_404(db, event_id, pid, room_id)

        if parent is not None:
            p = db.get(ProposalMessage, parent)
            if not p or p.room_id != room_id:
                raise HTTPException(400, "Invalid parent_id")

        msg = ProposalMessage(
            room_id=room_id,
            user_id=user.id,
            local_no=next_room_local_no(db, room_id),
            body=body,
            parent_id=parent,
            created_at=datetime.utcnow(),
        )
        db.add(msg)
        try:
            db.commit()
            mark_dirty(db, Scope(kind="PROOM", room_id=msg.room_id))
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(409, f"DB error: {str(e.orig)}")
        db.refresh(msg)

    return RedirectResponse(
        url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{room_id}#m{msg.local_no}",
        status_code=303,
    )

# Save draft (sponsor only)
@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/save")
def draft_save(
    event_id: int,
    pid: int,
    rid: int,
    request: Request,
    title: str = Form(""),
    recalling: str = Form(""),
    noting: str = Form(""),
    welcoming: str = Form(""),
    expressing_regret: str = Form(""),
    expressing_deep_concern: str = Form(""),
    emphasizing: str = Form(""),
    decides: str = Form(""),
    requests: str = Form(""),
    calls_upon: str = Form(""),
    encourages: str = Form(""),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        room = _get_event_room_or_404(db, event_id, pid, rid)

        draft = db.exec(select(ProposalDraft).where(ProposalDraft.room_id == rid)).first()
        if not draft:
            draft = ProposalDraft(
                event_id=event_id,
                proposal_id=pid,
                room_id=rid,
                sponsor_id=room.sponsor_id,
            )
            db.add(draft)
            db.commit()
            db.refresh(draft)

        if user.id != room.sponsor_id:
            raise HTTPException(403, "Only the room sponsor can edit the draft")
        if draft.is_submitted:
            raise HTTPException(400, "Draft already submitted")

        draft.title = (title or "").strip() or None
        draft.recalling = recalling or None
        draft.noting = noting or None
        draft.welcoming = welcoming or None
        draft.expressing_regret = expressing_regret or None
        draft.expressing_deep_concern = expressing_deep_concern or None
        draft.emphasizing = emphasizing or None
        draft.decides = decides or None
        draft.requests = requests or None
        draft.calls_upon = calls_upon or None
        draft.encourages = encourages or None
        draft.updated_at = datetime.utcnow()

        db.add(draft)
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise HTTPException(409, f"DB error: {str(e.orig)}")

    return RedirectResponse(
        url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}",
        status_code=303,
    )

from app.services.draft_ai import generate_draft_from_paragraphs, DraftFill


from datetime import datetime
from fastapi import Form, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlmodel import select

DRAFT_GENERATION_EXECUTOR = ThreadPoolExecutor(max_workers=1)
DRAFT_GENERATION_BUSY = False

DRAFT_GENERATION_JOBS: dict[str, dict[str, Any]] = {}
DRAFT_GENERATION_LOCK = Lock()

def _try_start_draft_generation() -> bool:
    global DRAFT_GENERATION_BUSY
    with DRAFT_GENERATION_LOCK:
        if DRAFT_GENERATION_BUSY:
            return False
        DRAFT_GENERATION_BUSY = True
        return True


def _finish_draft_generation() -> None:
    global DRAFT_GENERATION_BUSY
    with DRAFT_GENERATION_LOCK:
        DRAFT_GENERATION_BUSY = False


def _set_draft_job(job_id: str, data: dict[str, Any]) -> None:
    with DRAFT_GENERATION_LOCK:
        current = DRAFT_GENERATION_JOBS.get(job_id, {})
        current.update(data)
        DRAFT_GENERATION_JOBS[job_id] = current


def _get_draft_job(job_id: str) -> dict[str, Any] | None:
    with DRAFT_GENERATION_LOCK:
        job = DRAFT_GENERATION_JOBS.get(job_id)
        return dict(job) if job else None

def _apply_fill_to_draft(draft: ProposalDraft, fill: DraftFill) -> None:
    if (fill.title or "").strip():
        draft.title = fill.title.strip()

    draft.recalling = (fill.recalling or "").strip() or None
    draft.noting = (fill.noting or "").strip() or None
    draft.welcoming = (fill.welcoming or "").strip() or None
    draft.expressing_regret = (fill.expressing_regret or "").strip() or None
    draft.expressing_deep_concern = (fill.expressing_deep_concern or "").strip() or None
    draft.emphasizing = (fill.emphasizing or "").strip() or None
    draft.decides = (fill.decides or "").strip() or None
    draft.requests = (fill.requests or "").strip() or None
    draft.calls_upon = (fill.calls_upon or "").strip() or None
    draft.encourages = (fill.encourages or "").strip() or None

    draft.updated_at = datetime.utcnow()


def _run_draft_generation_job(
    *,
    job_id: str,
    plain_text: str,
    agenda_title: str | None,
    room_title: str | None,
) -> None:
    try:
        fill = generate_draft_from_paragraphs(
            plain_text=plain_text,
            agenda_title=agenda_title,
            room_title=room_title,
        )

        payload = fill.model_dump() if hasattr(fill, "model_dump") else fill.dict()

        _set_draft_job(job_id, {
            "status": "done",
            "payload": payload,
            "error": None,
        })

    except Exception as e:
        _set_draft_job(job_id, {
            "status": "error",
            "payload": None,
            "error": str(e),
        })

    finally:
        _finish_draft_generation()

def _run_draft_generation_save_job(
    *,
    job_id: str,
    event_id: int,
    pid: int,
    rid: int,
    sponsor_id: int,
    plain_text: str,
    agenda_title: str | None,
    room_title: str | None,
) -> None:
    try:
        fill = generate_draft_from_paragraphs(
            plain_text=plain_text,
            agenda_title=agenda_title,
            room_title=room_title,
        )

        with get_session() as db:
            room = db.get(ProposalRoom, rid)

            if not room:
                raise RuntimeError("Proposal room not found")

            if room.event_id != event_id or room.proposal_id != pid:
                raise RuntimeError("Proposal room does not match this event/proposal")

            if room.sponsor_id != sponsor_id:
                raise RuntimeError("Only the room sponsor can generate the draft")

            draft = db.exec(
                select(ProposalDraft).where(ProposalDraft.room_id == rid)
            ).first()

            if draft and draft.is_submitted:
                raise RuntimeError("Draft already submitted")

            if not draft:
                draft = ProposalDraft(
                    event_id=room.event_id,
                    proposal_id=room.proposal_id,
                    room_id=rid,
                    sponsor_id=room.sponsor_id,
                )

            _apply_fill_to_draft(draft, fill)

            db.add(draft)
            db.commit()

        payload = fill.model_dump() if hasattr(fill, "model_dump") else fill.dict()

        _set_draft_job(job_id, {
            "status": "done",
            "payload": payload,
            "error": None,
        })

    except Exception as e:
        _set_draft_job(job_id, {
            "status": "error",
            "payload": None,
            "error": str(e),
        })

    finally:
        _finish_draft_generation()

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate")
def draft_generate(
    pid: int,
    rid: int,
    request: Request,
    event_id: int,
    plain_text: str = Form(...),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        room = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))

        if room.proposal_id != pid:
            raise HTTPException(404)

        if room.event_id != event_id:
            raise HTTPException(404)

        if user.id != room.sponsor_id:
            raise HTTPException(403, "Only the room sponsor can generate the draft")

        draft = db.exec(
            select(ProposalDraft).where(ProposalDraft.room_id == rid)
        ).first()

        if draft and draft.is_submitted:
            raise HTTPException(400, "Draft already submitted")

        prop = db.get(AgendaProposal, pid)
        agenda_title = prop.title if prop else None
        room_title = room.title

    job_id = uuid.uuid4().hex

    _set_draft_job(job_id, {
        "status": "running",
        "payload": None,
        "error": None,
    })

    if not _try_start_draft_generation():
        raise HTTPException(
            429,
            "Another draft generation is already running. Please wait for it to finish."
        )

    DRAFT_GENERATION_EXECUTOR.submit(
        _run_draft_generation_save_job,
        job_id=job_id,
        event_id=event_id,
        pid=pid,
        rid=rid,
        sponsor_id=user.id,
        plain_text=plain_text,
        agenda_title=agenda_title,
        room_title=room_title,
    )

    return RedirectResponse(
        url=(
            f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}"
            f"?generation_job={job_id}&generation_status=running"
        ),
        status_code=303,
    )

@app.get("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate/status/{job_id}")
def draft_generate_status(
    event_id: int,
    pid: int,
    rid: int,
    job_id: str,
    request: Request,
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        room = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))

        if room.proposal_id != pid:
            raise HTTPException(404)

        if room.event_id != event_id:
            raise HTTPException(404)

        if user.id != room.sponsor_id:
            raise HTTPException(403, "Only the room sponsor can check draft generation")

    job = _get_draft_job(job_id)

    if not job:
        raise HTTPException(404, "Generation job not found")

    return JSONResponse(job)

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate-json")
def draft_generate_json(
    pid: int,
    rid: int,
    request: Request,
    event_id: int,
    plain_text: str = Form(...),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        room = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))

        if room.proposal_id != pid:
            raise HTTPException(404)

        if room.event_id != event_id:
            raise HTTPException(404)

        if user.id != room.sponsor_id:
            raise HTTPException(403, "Only the room sponsor can generate the draft")

        draft = db.exec(select(ProposalDraft).where(ProposalDraft.room_id == rid)).first()
        if draft and draft.is_submitted:
            raise HTTPException(400, "Draft already submitted")

        prop = db.get(AgendaProposal, pid)
        agenda_title = prop.title if prop else None
        room_title = room.title

    job_id = uuid.uuid4().hex

    _set_draft_job(job_id, {
        "status": "running",
        "payload": None,
        "error": None,
    })

    if not _try_start_draft_generation():
        raise HTTPException(
            429,
            "Another draft generation is already running. Please wait for it to finish."
        )

    DRAFT_GENERATION_EXECUTOR.submit(
        _run_draft_generation_job,
        job_id=job_id,
        plain_text=plain_text,
        agenda_title=agenda_title,
        room_title=room_title,
    )

    return JSONResponse({
        "status": "running",
        "job_id": job_id,
    })

@app.get("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/generate-json/status/{job_id}")
def draft_generate_json_status(
    event_id: int,
    pid: int,
    rid: int,
    job_id: str,
    request: Request,
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        room = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))

        if room.proposal_id != pid:
            raise HTTPException(404)

        if room.event_id != event_id:
            raise HTTPException(404)

        if user.id != room.sponsor_id:
            raise HTTPException(403, "Only the room sponsor can check draft generation")

    job = _get_draft_job(job_id)
    if not job:
        raise HTTPException(404, "Generation job not found")

    return JSONResponse(job)

# Toggle co-sign
@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/cosign")
def draft_cosign(pid: int, rid: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        draft = db.exec(select(ProposalDraft).where(ProposalDraft.room_id == rid)).first() or (_ for _ in ()).throw(HTTPException(404))
        if draft.is_submitted:
            raise HTTPException(400, "Draft already submitted")

        # ensure list
        arr = list(draft.cosigners_json or [])
        uid = int(user.id)

        # toggle presence
        if uid in arr:
            arr = [x for x in arr if x != uid]
        else:
            arr.append(uid)

        draft.cosigners_json = sorted(set(int(x) for x in arr))
        draft.updated_at = datetime.utcnow()
        db.add(draft); db.commit()

    return RedirectResponse(url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}", status_code=303)

def build_doc_symbol(event_year:int, event_id: int, seq: int, rev: int | None = None):
    base = f"A/{event_year}/{event_id}/L.{seq}"
    return f"{base}/Rev.{rev}" if rev else base

def _next_l_seq_tx(db, event_id: int) -> int:
    # 1) Lock the event row (single row lock — cheap + effective)
    db.exec(
        text("SELECT id FROM event WHERE id = :eid FOR UPDATE")
        .bindparams(bindparam("eid", event_id))
    ).first() # acquire the lock

    # 2) Now safe to compute MAX without FOR UPDATE
    row = db.exec(
        text(r"""
            SELECT COALESCE(
                MAX( (regexp_match(l_number, 'L\.([0-9]+)'))[1]::int ),
                0
            ) AS mx
            FROM proposaldraft
            WHERE event_id = :eid
                AND is_submitted = true
        """).bindparams(bindparam("eid", event_id))
    ).first()

    return (row.mx if row and row.mx is not None else 0) + 1

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/draft/submit")
def draft_submit(pid: int, rid: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        room  = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))
        draft = db.exec(select(ProposalDraft).where(ProposalDraft.room_id == rid)).first() or (_ for _ in ()).throw(HTTPException(404))
        if room.proposal_id != pid: raise HTTPException(404)
        if user.id != room.sponsor_id: raise HTTPException(403, "Only the room sponsor can submit")
        if draft.is_submitted:
            return RedirectResponse(url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}", status_code=303)
        if not any([draft.decides, draft.requests, draft.calls_upon, draft.encourages]):
            raise HTTPException(400, "At least one operative section is required to submit")
        
        if not draft.title or not draft.title.strip():
            raise HTTPException(400, "Title is required to submit draft")

        # get event year
        event = db.get(Event, draft.event_id) or (_ for _ in ()).throw(HTTPException(400, "Event missing"))
        event_year = (getattr(event, "starts_at", None) or getattr(event, "created_at", None) or datetime.utcnow()).year

        # allocate next L seq for this event (inside tx)
        seq = _next_l_seq_tx(db, draft.event_id)

        # build full symbol
        draft.l_number     = build_doc_symbol(event_year, draft.event_id, seq)
        draft.is_submitted = True
        draft.submitted_at = datetime.utcnow()
        draft.updated_at   = datetime.utcnow()

        db.add(draft)
        db.commit()
    return RedirectResponse(url=f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}?submitted=1", status_code=303)

def add_revision_suffix(symbol: str, rev: int) -> str:
    if '/Rev.' in symbol:
        return re.sub(r'/Rev\.\d+$', f'/Rev.{rev}', symbol)
    return f"{symbol}/Rev.{rev}"

# --- LIST: all submitted L docs visible 3 days after submission
@app.get("/events/{event_id}/view-draft", response_class=HTMLResponse)
def view_draft_index(event_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    now = _now_utc()

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)

        drafts = db.exec(
            select(ProposalDraft)
            .where(ProposalDraft.event_id == event_id)
            .where(ProposalDraft.is_submitted == True)
            .where(ProposalDraft.submitted_at != None)
            .where(ProposalDraft.submitted_at <= (now - VISIBLE_AFTER))
            .order_by(ProposalDraft.submitted_at.asc())
        ).all()

        # Bulk-load sponsors instead of db.get(User, ...) inside the loop
        sponsor_ids = {d.sponsor_id for d in drafts if d.sponsor_id}

        sponsors_by_id = {}
        if sponsor_ids:
            sponsors = db.exec(
                select(User).where(User.id.in_(sponsor_ids))
            ).all()
            sponsors_by_id = {u.id: u for u in sponsors}

        # Bulk-load agenda proposals instead of calling _agenda_for_draft()
        proposal_ids = {d.proposal_id for d in drafts if d.proposal_id}

        proposals_by_id = {}
        if proposal_ids:
            proposals = db.exec(
                select(AgendaProposal).where(AgendaProposal.id.in_(proposal_ids))
            ).all()
            proposals_by_id = {p.id: p for p in proposals}

        def vm(d: ProposalDraft):
            sponsor = sponsors_by_id.get(d.sponsor_id)
            proposal = proposals_by_id.get(d.proposal_id)

            return {
                "id": d.id,
                "title": d.title or "(Untitled)",
                "l_number": d.l_number or "(unassigned)",
                "submitted_at": d.submitted_at,
                "status": d.status,
                "agenda_id": proposal.id if proposal else d.proposal_id,
                "agenda_label": proposal.title if proposal else "—",
                "sponsor_name": sponsor.handle if sponsor else "—",
            }

        active = [
            vm(d)
            for d in drafts
            if d.status in (
                ProposalDraftStatus.TABLED,
                ProposalDraftStatus.REINTRODUCED,
            )
        ]

        withdrawn = [
            vm(d)
            for d in drafts
            if d.status == ProposalDraftStatus.WITHDRAWN
        ]

    return render(
        "events/view_draft_index.html",
        request,
        event=event,
        event_id=event_id,
        active=active,
        withdrawn=withdrawn,
    )

from app.services.translate_ai import translate_text, translate_lang_meta, SUPPORTED, labels_for

FIELDS = [
    "recalling", "noting", "welcoming",
    "expressing_regret", "expressing_deep_concern",
    "emphasizing",
    "decides", "requests", "calls_upon", "encourages",
]

from fastapi.responses import HTMLResponse, JSONResponse
from sqlmodel import select

from app.services.draft_translation import (
    DONE,
    FAILED,
    as_translate_lang,
    get_cached_or_enqueue_draft_translation,
    normalize_translation_lang,
    translation_row_is_ready,
)

# --- DETAIL: one draft (full text), cosign, withdraw, amendments
@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_detail(draft_id: int, request: Request):
    user = current_user(request)
    lang = normalize_translation_lang(
        request.query_params.get("lang") or "en",
        SUPPORTED,
    )

    with get_session() as db:
        d = db.get(ProposalDraft, draft_id)

        if not d:
            raise HTTPException(404, "Draft not found")

        if not d.submitted_at:
            raise HTTPException(403, "This draft is not visible yet.")

        if _now_utc() < d.submitted_at + VISIBLE_AFTER:
            return HTMLResponse(
                "This draft becomes visible 3 days after submission.",
                status_code=403,
            )

        qid, agenda_label = _agenda_for_draft(db, d)
        sponsor = db.get(User, d.sponsor_id)

        early, late = [], []

        for c in (d.cosigners_json or []):
            if isinstance(c, dict):
                if c.get("is_late", True):
                    late.append(c)
                else:
                    early.append(c)
            else:
                early.append(
                    {
                        "user_id": int(c),
                        "created_at": None,
                        "is_late": False,
                    }
                )

        cosigner_ids = {
            int(c["user_id"])
            for c in early + late
            if c.get("user_id") is not None
        }

        users_by_id = {}

        if cosigner_ids:
            users = db.exec(
                select(User).where(User.id.in_(cosigner_ids))
            ).all()

            users_by_id = {
                u.id: u
                for u in users
            }

        def name_of(uid: int) -> str:
            u = users_by_id.get(uid)
            return u.handle if u else f"User#{uid}"

        early_view = [
            {
                "name": name_of(int(c["user_id"])),
                "created_at": c.get("created_at"),
            }
            for c in early
        ]

        late_view = [
            {
                "name": name_of(int(c["user_id"])),
                "created_at": c.get("created_at"),
            }
            for c in late
        ]

        amends = db.exec(
            select(Amendment)
            .where(Amendment.draft_id == d.id)
            .order_by(Amendment.am_no.asc())
        ).all()

        has_amend = len(amends) > 0

        can_withdraw = bool(
            user
            and user.id == d.sponsor_id
            and d.status in (
                ProposalDraftStatus.TABLED,
                ProposalDraftStatus.REINTRODUCED,
            )
            and not has_amend
        )

        can_cosign = False
        already = False

        if user:
            is_sponsor = user.id == d.sponsor_id
            already = _already_cosigned(d, user.id)
            can_cosign = (
                not is_sponsor
                and not already
                and d.status != ProposalDraftStatus.ADOPTED
            )

        title_en = d.title or ""

        raw_draft_text = {
            f: getattr(d, f)
            for f in FIELDS
        }

        translation_status = DONE.lower()
        translation_error = None
        hide_draft_for_translation = False

        if lang == "en":
            title_show = title_en
            draft_text = raw_draft_text

        else:
            row = get_cached_or_enqueue_draft_translation(
                db=db,
                draft=d,
                lang=as_translate_lang(lang),
                fields=FIELDS,
            )

            if translation_row_is_ready(row, d, FIELDS):
                title_show = row.title_show or title_en

                translated_body = row.draft_text_json or {}

                draft_text = {
                    f: translated_body.get(f)
                    for f in FIELDS
                }

                translation_status = DONE.lower()
                hide_draft_for_translation = False

            else:
                # Do not show English fallback.
                # Hide the draft body until the requested translation is ready.
                title_show = ""
                draft_text = {
                    f: None
                    for f in FIELDS
                }

                translation_status = row.status.lower()
                translation_error = row.error
                hide_draft_for_translation = True

    return render(
        "events/draft_detail.html",
        request,
        draft=d,
        agenda_id=qid,
        agenda_label=agenda_label,
        sponsor_name=(sponsor.handle if sponsor else "—"),
        early_cosigns=early_view,
        late_cosigns=late_view,
        amends=amends,
        can_withdraw=can_withdraw,
        can_cosign=can_cosign,
        already_cosigned=already,
        lang=lang,
        lang_meta=translate_lang_meta(lang),
        title_show=title_show,
        draft_text=draft_text,
        ui_labels=labels_for(lang),
        supported_langs=SUPPORTED,
        translation_status=translation_status,
        translation_error=translation_error,
        hide_draft_for_translation=hide_draft_for_translation,
    )

@app.get("/drafts/{draft_id}/translation-status")
def draft_translation_status(draft_id: int, request: Request, lang: str = "en"):
    user = current_user(request)

    lang = normalize_translation_lang(lang, SUPPORTED)

    if lang == "en":
        return JSONResponse(
            {
                "ready": True,
                "status": "done",
                "lang": "en",
            }
        )

    with get_session() as db:
        d = db.get(ProposalDraft, draft_id)

        if not d:
            raise HTTPException(404, "Draft not found")

        if not d.submitted_at:
            raise HTTPException(403, "This draft is not visible yet.")

        if _now_utc() < d.submitted_at + VISIBLE_AFTER:
            raise HTTPException(
                403,
                "This draft becomes visible 3 days after submission.",
            )

        row = get_cached_or_enqueue_draft_translation(
            db=db,
            draft=d,
            lang=as_translate_lang(lang),
            fields=FIELDS,
        )

        ready = translation_row_is_ready(row, d, FIELDS)

        return JSONResponse(
            {
                "ready": ready,
                "status": row.status.lower(),
                "lang": lang,
                "error": row.error if row.status == FAILED else None,
            }
        )

# --- POST: cosign (late; goes to Addendum <L>/Add)
@app.post("/drafts/{draft_id}/cosign")
def cosign_draft(draft_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))
        if d.status == ProposalDraftStatus.ADOPTED:
            raise HTTPException(403, "Cosigning is closed.")
        if user.id == d.sponsor_id:
            raise HTTPException(403, "Main sponsor cannot late co-sign.")
        if _already_cosigned(d, user.id):
            return RedirectResponse(f"/drafts/{draft_id}", status_code=303)
        _append_late_cosign(d, user.id)
        d.updated_at = _now_utc()
        db.add(d); db.commit()
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)

# --- POST: withdraw (only sponsor; blocked if any amendment exists)
@app.post("/events/{event_id}/drafts/{draft_id}/withdraw")
def withdraw_draft(event_id: int, draft_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)

        d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))

        if d.event_id != event_id:
            raise HTTPException(404)

        if user.id != d.sponsor_id:
            raise HTTPException(403, "Only the main sponsor can withdraw")

        has_amend = db.exec(
            select(Amendment)
            .where(Amendment.draft_id == d.id)
        ).first() is not None

        if has_amend:
            raise HTTPException(400, "Cannot withdraw: an amendment has been proposed.")

        d.status = ProposalDraftStatus.WITHDRAWN
        d.withdrawn_at = _now_utc()

        db.add(d)
        db.commit()

    return RedirectResponse(
        url=f"/events/{event_id}/view-draft",
        status_code=303,
    )

# --- POST: reintroduce (any user; becomes new sponsor)
@app.post("/drafts/{draft_id}/reintroduce")
def reintroduce_draft(draft_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))
        if d.status != ProposalDraftStatus.WITHDRAWN:
            raise HTTPException(400, "Only withdrawn drafts can be reintroduced")
        d.status = ProposalDraftStatus.REINTRODUCED
        d.reintroduced_by_id = user.id
        d.reintroduced_at = _now_utc()
        d.sponsor_id = user.id  # new main sponsor
        db.add(d); db.commit()
    return RedirectResponse(f"/drafts/{draft_id}", status_code=303)

from app.services.amend_ai import generate_amend_ops_from_paragraphs, AmendGen
from app.services.amend_validate import validate_ops
from app.services.amend_format import amend_gen_to_body_markdown

AMEND_GENERATION_JOBS: dict[str, dict[str, Any]] = {}
AMEND_GENERATION_LOCK = Lock()


def _set_amend_job(job_id: str, data: dict[str, Any]) -> None:
    with AMEND_GENERATION_LOCK:
        current = AMEND_GENERATION_JOBS.get(job_id, {})
        current.update(data)
        AMEND_GENERATION_JOBS[job_id] = current


def _get_amend_job(job_id: str) -> dict[str, Any] | None:
    with AMEND_GENERATION_LOCK:
        job = AMEND_GENERATION_JOBS.get(job_id)
        return dict(job) if job else None


def _draft_text_for_amend_ai(draft: ProposalDraft) -> str:
    """
    Gives the amendment model the actual draft content, so it can target
    clauses by section or by 'starting with ...' instead of hallucinating numbers.
    """
    sections = [
        ("preambular", "recalling", "Recalling"),
        ("preambular", "noting", "Noting"),
        ("preambular", "welcoming", "Welcoming"),
        ("preambular", "expressing_regret", "Expressing regret"),
        ("preambular", "expressing_deep_concern", "Expressing deep concern"),
        ("preambular", "emphasizing", "Emphasizing"),
        ("operative", "decides", "Decides"),
        ("operative", "requests", "Requests"),
        ("operative", "calls_upon", "Calls upon"),
        ("operative", "encourages", "Encourages"),
    ]

    lines: list[str] = []

    for clause_type, attr, label in sections:
        value = (getattr(draft, attr, "") or "").strip()
        if not value:
            continue

        for raw_line in value.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lines.append(f"- {clause_type} / {label}: {line}")

    return "\n".join(lines)


def _assert_amend_context(
    *,
    db,
    user: User,
    event_id: int,
    pid: int,
    rid: int,
    draft_id: int,
):
    # Keep this if you already have event-scoped access control.
    _require_event_access(db=db, user=user, event_id=event_id)

    room = db.get(ProposalRoom, rid) or (_ for _ in ()).throw(HTTPException(404))
    draft = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))

    if room.event_id != event_id:
        raise HTTPException(404)

    if room.proposal_id != pid:
        raise HTTPException(404)

    if draft.event_id != event_id:
        raise HTTPException(404)

    if draft.proposal_id != pid:
        raise HTTPException(404)

    if draft.room_id != rid:
        raise HTTPException(404)

    if not draft.is_submitted:
        raise HTTPException(400, "Draft not submitted yet")

    if getattr(draft, "status", None) == ProposalDraftStatus.WITHDRAWN:
        raise HTTPException(400, "Cannot amend a withdrawn draft")

    if getattr(draft, "status", None) == ProposalDraftStatus.ADOPTED:
        raise HTTPException(400, "Cannot amend an adopted draft")

    prop = db.get(AgendaProposal, pid)

    return room, draft, prop


def _run_amend_generation_job(
    *,
    job_id: str,
    plain_text: str,
    draft_symbol: str | None,
    draft_title: str | None,
    agenda_label: str | None,
    draft_text: str | None,
) -> None:
    try:
        gen = generate_amend_ops_from_paragraphs(
            plain_text=plain_text,
            draft_symbol=draft_symbol,
            draft_title=draft_title,
            agenda_label=agenda_label,
            draft_text=draft_text,
        )

        payload = gen.model_dump() if hasattr(gen, "model_dump") else gen.dict()

        body_markdown = amend_gen_to_body_markdown(gen)

        # Make sure the generated amendment can actually be submitted.
        validate_ops(body_markdown)

        payload["body_markdown"] = body_markdown

        _set_amend_job(job_id, {
            "status": "done",
            "payload": payload,
            "error": None,
        })

    except Exception as e:
        _set_amend_job(job_id, {
            "status": "error",
            "payload": None,
            "error": str(e),
        })

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amend/generate-json")
def amend_generate_json(
    event_id: int,
    pid: int,
    rid: int,
    draft_id: int,
    request: Request,
    plain_text: str = Form(...),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    plain_text = (plain_text or "").strip()
    if not plain_text:
        raise HTTPException(400, "Amendment instruction is required")

    with get_session() as db:
        room, draft, prop = _assert_amend_context(
            db=db,
            user=user,
            event_id=event_id,
            pid=pid,
            rid=rid,
            draft_id=draft_id,
        )

        agenda_label = prop.title if prop else None
        draft_symbol = draft.l_number or f"L.{draft.id}"
        draft_title = draft.title or ""
        draft_text = _draft_text_for_amend_ai(draft)

    job_id = uuid.uuid4().hex

    _set_amend_job(job_id, {
        "status": "running",
        "payload": None,
        "error": None,
    })

    # Reuse the same single-worker executor as draft generation.
    # This prevents draft AI and amendment AI from running at the same time.
    DRAFT_GENERATION_EXECUTOR.submit(
        _run_amend_generation_job,
        job_id=job_id,
        plain_text=plain_text,
        draft_symbol=draft_symbol,
        draft_title=draft_title,
        agenda_label=agenda_label,
        draft_text=draft_text,
    )

    return JSONResponse({
        "status": "running",
        "job_id": job_id,
    })

@app.get("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amend/generate-json/status/{job_id}")
def amend_generate_json_status(
    event_id: int,
    pid: int,
    rid: int,
    draft_id: int,
    job_id: str,
    request: Request,
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        _assert_amend_context(
            db=db,
            user=user,
            event_id=event_id,
            pid=pid,
            rid=rid,
            draft_id=draft_id,
        )

    job = _get_amend_job(job_id)
    if not job:
        raise HTTPException(404, "Amendment generation job not found")

    return JSONResponse(job)
    
from app.services.amend_validate import validate_ops

@app.post("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amend")
def submit_amendment(
    event_id: int,
    pid: int,
    rid: int,
    draft_id: int,
    request: Request,
    body_markdown: str = Form(...),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    body_markdown = (body_markdown or "").strip()
    if not body_markdown:
        raise HTTPException(400, "Amendment text is required")

    try:
        validate_ops(body_markdown)
    except ValueError as e:
        raise HTTPException(400, str(e))

    with get_session() as db:
        room, draft, prop = _assert_amend_context(
            db=db,
            user=user,
            event_id=event_id,
            pid=pid,
            rid=rid,
            draft_id=draft_id,
        )

        last = db.exec(
            select(Amendment)
            .where(Amendment.draft_id == draft.id)
            .order_by(Amendment.am_no.desc())
        ).first()

        next_no = (last.am_no + 1) if last else 1
        base = draft.l_number or f"L.{draft.id}"
        label = f"{base}/Amend.{next_no}"

        am = Amendment(
            draft_id=draft.id,
            am_no=next_no,
            label=label,
            submitted_by_id=user.id,
            body_markdown=body_markdown,
        )

        db.add(am)
        db.commit()
        db.refresh(am)

        amend_id = am.id

    return RedirectResponse(
        f"/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amendments/{amend_id}",
        status_code=303,
    )

# from app.services.translate_ai import LANGS, translate_text

# --- GET: view one amendment
@app.get("/events/{event_id}/proposal-discussion/{pid}/rooms/{rid}/drafts/{draft_id}/amendments/{amend_id}", response_class=HTMLResponse)
def view_amendment(
    event_id: int,
    pid: int,
    rid: int,
    draft_id: int,
    amend_id: int,
    request: Request,
    lang: str = "en",
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    lang = (request.query_params.get("lang") or lang or "en").lower()
    if lang not in SUPPORTED:
        lang = "en"

    with get_session() as db:
        room, draft, prop = _assert_amend_context(
            db=db,
            user=user,
            event_id=event_id,
            pid=pid,
            rid=rid,
            draft_id=draft_id,
        )

        amend = db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))

        if amend.draft_id != draft.id:
            raise HTTPException(404)

    body_en = amend.body_markdown or ""
    body_show = body_en if lang == "en" else translate_text(body_en, lang)

    return render(
        "events/amendment_detail.html",
        request,
        event={"id": event_id},
        proposal=prop,
        room=room,
        draft=draft,
        amend=amend,
        lang=lang,
        lang_meta=translate_lang_meta(lang),
        body_show=body_show,
        supported_langs=SUPPORTED,
    )

@app.get("/amendments/{amend_id}", response_class=HTMLResponse)
def view_amendment_legacy(amend_id: int, request: Request, lang: str = "en"):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    lang = (request.query_params.get("lang") or lang or "en").lower()

    with get_session() as db:
        amend = db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))
        draft = db.get(ProposalDraft, amend.draft_id) or (_ for _ in ()).throw(HTTPException(404))

        if not draft.room_id:
            raise HTTPException(404, "This amendment is not linked to a proposal room")

        room = db.get(ProposalRoom, draft.room_id) or (_ for _ in ()).throw(HTTPException(404))

    return RedirectResponse(
        f"/events/{draft.event_id}/proposal-discussion/{draft.proposal_id}/rooms/{room.id}/drafts/{draft.id}/amendments/{amend.id}?lang={lang}",
        status_code=303,
    )

# ===== Proposal Floor helpers (per draft or amendment) =====

from sqlalchemy import case as sa_case

def _pfloor_target_check(draft_id: int | None, amend_id: int | None):
    if (not draft_id and not amend_id) or (draft_id and amend_id):
        raise HTTPException(400, "Specify exactly one of draft_id or amendment_id")

def _pfloor_get_or_create_state(db, *, event_id: int, proposal_id: int, draft_id: int | None, amendment_id: int | None) -> ProposalFloorState:
    _pfloor_target_check(draft_id, amendment_id)
    q = select(ProposalFloorState).where(
        ProposalFloorState.event_id == event_id,
        ProposalFloorState.proposal_id == proposal_id,
        (ProposalFloorState.draft_id == draft_id) if draft_id else (ProposalFloorState.amendment_id == amendment_id),
    )
    st = db.exec(q).first()
    if st:
        return st
    st = ProposalFloorState(
        event_id=event_id, proposal_id=proposal_id,
        draft_id=draft_id, amendment_id=amendment_id,
        is_open=True, speaking_time_sec=120
    )
    db.add(st); db.commit(); db.refresh(st)
    return st

def _pf_user_has_floor(db, *, state, user_id: int) -> bool:
    # mirrors user_has_floor but for proposal-floor tables
    if not state or not state.current_speaker_request_id:
        return False
    req = db.get(ProposalSpeakerRequest, state.current_speaker_request_id)
    return bool(req and req.user_id == user_id and req.status == "SPEAKING")

def _pf_finish_current(db, *, state):
    if state and state.current_speaker_request_id:
        cur = db.get(ProposalSpeakerRequest, state.current_speaker_request_id)
        if cur and cur.status == "SPEAKING":
            cur.status = "DONE"
            db.add(cur)
        state.current_speaker_request_id = None
        state.updated_at = datetime.utcnow()
        db.add(state)
        db.commit()

def _pf_force_take_floor(db, *, state, user_id: int, kind: str = "CHAIR"):
    # end any current; then create a SPEAKING request for this user
    _pf_finish_current(db, state=state)
    req = ProposalSpeakerRequest(
        event_id=state.event_id,
        proposal_id=state.proposal_id,
        draft_id=state.draft_id,
        amendment_id=state.amendment_id,
        user_id=user_id,
        kind=kind,         # "CHAIR" | "GENERAL" | "ROR" | "ROR_ALL"
        status="SPEAKING",
        position=0,
        target_intervention_id=None,
    )
    db.add(req); db.commit(); db.refresh(req)
    state.current_speaker_request_id = req.id
    state.updated_at = datetime.utcnow()
    db.add(state); db.commit()

def _pfloor_queue_order():
    # CHAIR > ROR_ALL > ROR > GENERAL (match your style)
    return sa_case(
        (ProposalSpeakerRequest.kind == "CHAIR", 4),
        (ProposalSpeakerRequest.kind == "ROR_ALL", 3),
        (ProposalSpeakerRequest.kind == "ROR", 2),
        else_=1
    ).desc(), ProposalSpeakerRequest.position.asc()

def _pfloor_next_position(db, st: ProposalFloorState) -> int:
    row = db.exec(
        select(func.max(ProposalSpeakerRequest.position)).where(
            ProposalSpeakerRequest.event_id == st.event_id,
            ProposalSpeakerRequest.proposal_id == st.proposal_id,
            (ProposalSpeakerRequest.draft_id == st.draft_id) if st.draft_id else (ProposalSpeakerRequest.amendment_id == st.amendment_id),
            ProposalSpeakerRequest.status == "QUEUED",
        )
    ).first()
    mx = (row[0] if isinstance(row, tuple) else row) if row is not None else None
    return 1 if mx is None else int(mx) + 1

def _pfloor_load_speakers(db, st: ProposalFloorState) -> list[ProposalSpeakerRequest]:
    return db.exec(
        select(ProposalSpeakerRequest).where(
            ProposalSpeakerRequest.event_id == st.event_id,
            ProposalSpeakerRequest.proposal_id == st.proposal_id,
            (ProposalSpeakerRequest.draft_id == st.draft_id) if st.draft_id else (ProposalSpeakerRequest.amendment_id == st.amendment_id),
        ).order_by(*_pfloor_queue_order())
    ).all()

def _pfloor_resolve_question(db, kind: str, item_id: int) -> tuple[AgendaProposal, Question]:
    """
    Resolve (prop, question) for proposal-floor by kind ('draft'|'amendment') and item_id.
    We always thread interventions on the *agenda's* Question, same as General Floor.
    """
    k = (kind or "").strip().lower()
    if k == "draft":
        d = db.get(ProposalDraft, item_id) or (_ for _ in ()).throw(HTTPException(404))
        prop = db.get(AgendaProposal, d.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
    elif k == "amendment":
        a = db.get(Amendment, item_id) or (_ for _ in ()).throw(HTTPException(404))
        d = db.get(ProposalDraft, a.draft_id) or (_ for _ in ()).throw(HTTPException(404))
        prop = db.get(AgendaProposal, d.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
    else:
        raise HTTPException(400, "Invalid kind")

    if prop.status != ProposalStatus.accepted:
        raise HTTPException(404, "Agenda item not accepted")

    q = ensure_general_floor_question(db, prop)
    return prop, q

# ===== Proposal Floor: listing =====
@app.get("/events/{event_id}/proposal-floor", response_class=HTMLResponse)
def proposal_floor_index(event_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)

        items = db.exec(
            select(AgendaProposal)
            .where(
                AgendaProposal.event_id == event.id,
                AgendaProposal.status == ProposalStatus.accepted,
            )
            .order_by(AgendaProposal.created_at.asc())
        ).all()

        def l_seq(d: ProposalDraft) -> int:
            if d.l_number:
                import re
                m = re.search(r"L\.(\d+)", d.l_number)
                if m:
                    return int(m.group(1))
            return 10_000_000 + d.id

        users = db.exec(select(User)).all()
        user_map = {u.id: u for u in users}
        flags = effective_flags(user) if user else EMPTY_FLAGS

        rows = []
        for it in items:
            drafts = db.exec(
                select(ProposalDraft).where(
                    ProposalDraft.event_id == event.id,
                    ProposalDraft.proposal_id == it.id,
                    ProposalDraft.is_submitted == True,
                    ProposalDraft.status.in_([ProposalDraftStatus.TABLED, ProposalDraftStatus.REINTRODUCED]),
                )
            ).all()
            drafts.sort(key=l_seq)

            for d in drafts:
                ams = db.exec(
                    select(Amendment)
                    .where(Amendment.draft_id == d.id)
                    .order_by(Amendment.am_no.asc())
                ).all()
                rows.append(
                    {
                        "proposal": it,
                        "draft": d,
                        "amendments": ams,
                    }
                )

    return templates.TemplateResponse(
        "events/proposal_floor.html",
        {
            "request": request,
            "user": user,
            "flags": flags,
            "event": event,
            "rows": rows,
            "user_map": user_map,
        },
    )

def _pfi_scope_filter(kind: str, item_id: int):
    """Return a SQLAlchemy boolean expression to filter within the PF scope."""
    if kind == "draft":
        return (ProposalIntervention.draft_id == item_id) & (ProposalIntervention.amendment_id.is_(None))
    else:
        return (ProposalIntervention.amendment_id == item_id) & (ProposalIntervention.draft_id.is_(None))

def _pfi_next_local_no(
    db: Session, *,
    event_id: int,
    proposal_id: int,
    draft_id: int | None,
    amendment_id: int | None
) -> int:
    # Build null-safe scope conditions
    conds = [
        ProposalIntervention.event_id == event_id,
        ProposalIntervention.proposal_id == proposal_id,
        (ProposalIntervention.draft_id == draft_id)
            if draft_id is not None else ProposalIntervention.draft_id.is_(None),
        (ProposalIntervention.amendment_id == amendment_id)
            if amendment_id is not None else ProposalIntervention.amendment_id.is_(None),
    ]

    q = select(sa.func.max(ProposalIntervention.local_no)).where(sa.and_(*conds))
    row = db.exec(q).first()

    # Normalize (tuple vs scalar)
    mx = (row[0] if isinstance(row, tuple) else row) or 0
    return int(mx) + 1

def _pfi_insert(db: Session, *, event_id: int, proposal_id: int, draft_id: int | None, amendment_id: int | None,
                user_id: int, body: str, parent_id: int | None):
    # small retry for local_no races
    for _ in (1, 2):
        try:
            ln = _pfi_next_local_no(db,
                event_id=event_id, proposal_id=proposal_id,
                draft_id=draft_id, amendment_id=amendment_id
            )
            it = ProposalIntervention(
                event_id=event_id, proposal_id=proposal_id,
                draft_id=draft_id, amendment_id=amendment_id,
                by_user=user_id, local_no=ln, body=body, parent_id=parent_id
            )
            db.add(it); db.commit(); db.refresh(it)
            return it
        except IntegrityError:
            db.rollback()
    raise HTTPException(409, "Could not allocate local number; please retry.")

# near your other helpers
def _pfi_scope_where(*, event_id: int, proposal_id: int,
                     draft_id: int | None, amendment_id: int | None):
    import sqlalchemy as sa
    return sa.and_(
        ProposalIntervention.event_id == event_id,
        ProposalIntervention.proposal_id == proposal_id,
        (ProposalIntervention.draft_id == draft_id)
            if draft_id is not None else ProposalIntervention.draft_id.is_(None),
        (ProposalIntervention.amendment_id == amendment_id)
            if amendment_id is not None else ProposalIntervention.amendment_id.is_(None),
    )

def _get_pfloor_context_for_invite(db, invite_id: int, user_id: int):
    """
    Look up a proposal-floor context for a given invite, based on the
    Notification payload that carried it to the user.

    Returns:
        dict | None, e.g. {"kind": "draft", "draft_id": 12, "amendment_id": None}
    """
    notes = db.exec(
        select(Notification).where(
            (Notification.user_id == user_id)
            & (Notification.type == "INVITE_ROR_PFLOOR")
            & (Notification.payload_json.contains({"invite_id": invite_id}))
        )
    ).all()

    for n in notes:
        payload = _parse_payload(getattr(n, "payload_json", None))
        pf = payload.get("pfloor") or {}
        k = pf.get("kind")
        if not k:
            continue
        return {
            "kind": k,
            "draft_id": pf.get("draft_id"),
            "amendment_id": pf.get("amendment_id"),
        }

    return None


def _get_early_vote(db, st: ProposalFloorState) -> Optional[ProposalEarlyVote]:
    return db.exec(
        select(ProposalEarlyVote).where(
            ProposalEarlyVote.event_id == st.event_id,
            ProposalEarlyVote.proposal_id == st.proposal_id,
            (ProposalEarlyVote.draft_id == st.draft_id)
            if st.draft_id
            else (ProposalEarlyVote.amendment_id == st.amendment_id),
        )
    ).first()

def _has_user_early_voted(db, st: ProposalFloorState, user_id: int) -> bool:
    ballot = db.exec(
        select(ProposalEarlyBallot).where(
            ProposalEarlyBallot.event_id == st.event_id,
            ProposalEarlyBallot.proposal_id == st.proposal_id,
            (ProposalEarlyBallot.draft_id == st.draft_id)
            if st.draft_id
            else (ProposalEarlyBallot.amendment_id == st.amendment_id),
            ProposalEarlyBallot.user_id == user_id,
        )
    ).first()
    return ballot is not None

# ===== Proposal Floor: item pages (draft / amendment) =====

def _get_formal_vote(db, st: ProposalFloorState) -> Optional[ProposalFormalVote]:
    return db.exec(
        select(ProposalFormalVote).where(
            ProposalFormalVote.event_id == st.event_id,
            ProposalFormalVote.proposal_id == st.proposal_id,
            (ProposalFormalVote.draft_id == st.draft_id)
            if st.draft_id
            else (ProposalFormalVote.amendment_id == st.amendment_id),
        )
    ).first()


def _has_user_formal_voted(db, st: ProposalFloorState, user_id: int) -> bool:
    fv = _get_formal_vote(db, st)
    if not fv:
        return False
    ballot = db.exec(
        select(ProposalFormalBallot).where(
            ProposalFormalBallot.formal_vote_id == fv.id,
            ProposalFormalBallot.user_id == user_id,
        )
    ).first()
    return ballot is not None

def _get_or_create_amend_vote_state(db, amendment_id: int) -> AmendmentVoteState:
    st = db.exec(
        select(AmendmentVoteState).where(AmendmentVoteState.amendment_id == amendment_id)
    ).first()
    if st:
        return st
    st = AmendmentVoteState(amendment_id=amendment_id, is_open=False, yes=0, no=0, abstain=0)
    db.add(st)
    db.commit()
    db.refresh(st)
    return st

def _has_user_voted_amendment(db, amendment_id: int, user_id: int) -> bool:
    v = db.exec(
        select(AmendmentVote.id).where(
            AmendmentVote.amendment_id == amendment_id,
            AmendmentVote.user_id == user_id,
        )
    ).first()
    return v is not None

def _cast_amendment_vote(db, amendment_id: int, user_id: int, choice: str):
    st = _get_or_create_amend_vote_state(db, amendment_id)

    if not st.is_open:
        raise HTTPException(400, "Voting is closed")
    if _has_user_voted_amendment(db, amendment_id, user_id):
        raise HTTPException(400, "Already voted")

    choice = choice.upper()
    if choice not in ("YES", "NO", "ABSTAIN"):
        raise HTTPException(400, "Invalid choice")

    db.add(AmendmentVote(amendment_id=amendment_id, user_id=user_id, choice=choice))

    if choice == "YES":
        st.yes += 1
    elif choice == "NO":
        st.no += 1
    else:
        st.abstain += 1

    db.add(st)
    db.commit()

@app.post("/events/proposal-floor/amendment/{amend_id}/vote")
def amendment_vote(amend_id: int, request: Request, choice: str = Form(...)):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        a = db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))
        _cast_amendment_vote(db, amendment_id=a.id, user_id=user.id, choice=choice)

    # back to where user was (draft page)
    return RedirectResponse(request.headers.get("referer", f"/events/proposal-floor/amendment/{amend_id}"), status_code=303)

@app.post("/events/proposal-floor/amendment/{amend_id}/vote/open")
def amendment_vote_open(amend_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f.get("IS_CHAIR") or f.get("IS_PRESIDENT")):
        raise HTTPException(403)

    with get_session() as db:
        db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))
        st = _get_or_create_amend_vote_state(db, amend_id)
        st.is_open = True
        db.add(st)
        db.commit()

    return RedirectResponse(request.headers.get("referer", f"/events/proposal-floor/amendment/{amend_id}"), status_code=303)

@app.post("/events/proposal-floor/amendment/{amend_id}/vote/close")
def amendment_vote_close(amend_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f.get("IS_CHAIR") or f.get("IS_PRESIDENT")):
        raise HTTPException(403)

    with get_session() as db:
        db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))
        st = _get_or_create_amend_vote_state(db, amend_id)
        st.is_open = False
        db.add(st)
        db.commit()

    return RedirectResponse(request.headers.get("referer", f"/events/proposal-floor/amendment/{amend_id}"), status_code=303)

@app.get("/events/{event_id}/proposal-floor/draft/{draft_id}", response_class=HTMLResponse)
def proposal_floor_draft(event_id: int, draft_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        d, prop = _get_event_draft_or_404(db, event.id, draft_id)

        if prop.status != ProposalStatus.accepted:
            raise HTTPException(404, "Agenda item not accepted")

        st = _pfloor_get_or_create_state(
            db,
            event_id=event.id,
            proposal_id=d.proposal_id,
            draft_id=d.id,
            amendment_id=None,
        )

        early_vote = _get_early_vote(db, st)
        has_early_voted = _has_user_early_voted(db, st, user.id) if early_vote else False

        formal_vote = _get_formal_vote(db, st)
        has_formal_voted = _has_user_formal_voted(db, st, user.id) if formal_vote else False

        can_speak = _pf_user_has_floor(db, state=st, user_id=user.id)
        f = effective_flags(user)
        if f.get("IS_CHAIR") or f.get("IS_PRESIDENT"):
            can_speak = True

        q = ensure_general_floor_question(db, prop)

        users = db.exec(select(User)).all()
        user_map = {u.id: u for u in users}
        speakers = _pfloor_load_speakers(db, st)

        ams = db.exec(
            select(Amendment)
            .where(Amendment.draft_id == d.id)
            .order_by(Amendment.am_no.asc())
        ).all()

        amendment_cards = []
        for am in ams:
            vst = _get_or_create_amend_vote_state(db, am.id)
            has_voted = _has_user_voted_amendment(db, am.id, user.id)
            amendment_cards.append(
                {
                    "am": am,
                    "vote_state": vst,
                    "HAS_VOTED": has_voted,
                }
            )

    return templates.TemplateResponse(
        "events/proposal_floor_item.html",
        {
            "request": request,
            "user": user,
            "flags": f,
            "event": event,
            "early_vote": early_vote,
            "HAS_EARLY_VOTED": has_early_voted,
            "formal_vote": formal_vote,
            "HAS_FORMAL_VOTED": has_formal_voted,
            "mode": "DRAFT",
            "proposal": prop,
            "draft": d,
            "amendment": None,
            "amendment_cards": amendment_cards,
            "pfloor": st,
            "speakers": speakers,
            "user_map": user_map,
            "can_speak": can_speak,
            "q": q,
        },
    )

@app.get("/events/{event_id}/proposal-floor/amendment/{amendment_id}", response_class=HTMLResponse)
def proposal_floor_amendment(event_id: int, amendment_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _require_event_access(db=db, user=user, event_id=event_id)
        am, d, prop = _get_event_amendment_or_404(db, event.id, amendment_id)

        if prop.status != ProposalStatus.accepted:
            raise HTTPException(404, "Agenda item not accepted")

        st = _pfloor_get_or_create_state(
            db,
            event_id=event.id,
            proposal_id=prop.id,
            draft_id=None,
            amendment_id=am.id,
        )

        early_vote = _get_early_vote(db, st)
        has_early_voted = _has_user_early_voted(db, st, user.id) if early_vote else False

        formal_vote = _get_formal_vote(db, st)
        has_formal_voted = _has_user_formal_voted(db, st, user.id) if formal_vote else False

        can_speak = _pf_user_has_floor(db, state=st, user_id=user.id)
        f = effective_flags(user)
        if f.get("IS_CHAIR") or f.get("IS_PRESIDENT"):
            can_speak = True

        q = ensure_general_floor_question(db, prop)

        users = db.exec(select(User)).all()
        user_map = {u.id: u for u in users}
        speakers = _pfloor_load_speakers(db, st)

    return templates.TemplateResponse(
        "events/proposal_floor_item.html",
        {
            "request": request,
            "user": user,
            "flags": f,
            "event": event,
            "early_vote": early_vote,
            "HAS_EARLY_VOTED": has_early_voted,
            "formal_vote": formal_vote,
            "HAS_FORMAL_VOTED": has_formal_voted,
            "mode": "AMENDMENT",
            "proposal": prop,
            "draft": d,
            "amendment": am,
            "amendment_cards": [],
            "pfloor": st,
            "speakers": speakers,
            "user_map": user_map,
            "can_speak": can_speak,
            "q": q,
        },
    )

# GET: fragment list
@app.get("/events/proposal-floor/{kind}/{item_id}/interventions/fragment",
         response_class=HTMLResponse)
def pfloor_interventions_fragment(kind: str, item_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    kind = (kind or "").lower()
    with get_session() as db:

        # fetch users + roles
        all_users = db.exec(select(User).options(selectinload(User.roles))).all()
        user_map = {u.id: u for u in all_users}
        role_map = {u.id: sorted({r.name for r in (u.roles or [])}) for u in all_users}

        if kind == "draft":
            d = db.get(ProposalDraft, item_id) or (_ for _ in ()).throw(HTTPException(404))
            scope = dict(event_id=d.event_id, proposal_id=d.proposal_id,
                         draft_id=d.id, amendment_id=None)
        elif kind == "amendment":
            a = db.get(Amendment, item_id) or (_ for _ in ()).throw(HTTPException(404))
            d = db.get(ProposalDraft, a.draft_id)
            scope = dict(event_id=d.event_id, proposal_id=d.proposal_id,
                         draft_id=None, amendment_id=a.id)
        else:
            raise HTTPException(400, "kind must be draft|amendment")

        rows = db.exec(
            select(ProposalIntervention)
            .where(_pfi_scope_where(**scope))
            .order_by(ProposalIntervention.created_at.asc())
        ).all()

        # threadify within THIS scope only (parent_id-based)
        by_id = {r.id: {"node": r, "children": []} for r in rows}
        roots = []
        for r in rows:
            pid = r.parent_id
            if pid and pid in by_id:
                by_id[pid]["children"].append(by_id[r.id])
            else:
                roots.append(by_id[r.id])

        # proposal-floor state for this DRAFT scope
        fs = _pfloor_get_or_create_state(
            db,
            event_id=d.event_id,
            proposal_id=d.proposal_id,
            draft_id=d.id,
            amendment_id=None,
        )

        # who can speak (proposal-floor possession; chairs/president override)
        can_speak = _pf_user_has_floor(db, state=fs, user_id=(user.id if user else 0))
        f = effective_flags(user)
        if f.get("IS_CHAIR") or f.get("IS_PRESIDENT"):
            can_speak = True

        flags = effective_flags(user)

    flags = effective_flags(user)

    return templates.TemplateResponse(
        "partials/pfloor_interventions_list.html",
        # {"request": request, "threads": roots, "user_map": user_map}
        {
            "request": request,
            "user": user,
            "flags": flags,
            "threads": roots,
            "user_map": user_map,
            "role_map": role_map,
            "kind": kind,
            "item_id": item_id,
            "can_speak": can_speak,
        },
    )

from sqlalchemy import func, and_

@app.get("/events/proposal-floor/{kind}/{item_id}/interventions/head")
def pfloor_interventions_head(kind: str, item_id: int):
    kind = (kind or "").lower()
    with get_session() as db:
        if kind == "draft":
            d = db.get(ProposalDraft, item_id) or (_ for _ in ()).throw(HTTPException(404))
            scope = dict(event_id=d.event_id, proposal_id=d.proposal_id, draft_id=d.id, amendment_id=None)
        elif kind == "amendment":
            a = db.get(Amendment, item_id) or (_ for _ in ()).throw(HTTPException(404))
            d = db.get(ProposalDraft, a.draft_id)
            scope = dict(event_id=d.event_id, proposal_id=d.proposal_id, draft_id=None, amendment_id=a.id)
        else:
            raise HTTPException(400, "kind must be draft|amendment")

        # max id in this scope
        last_any = db.exec(
            select(func.max(ProposalIntervention.id)).where(_pfi_scope_where(**scope))
        ).one_or_none()
        # max id among replies (parented) in this scope
        last_reply = db.exec(
            select(func.max(ProposalIntervention.id))
            .where(and_(_pfi_scope_where(**scope), ProposalIntervention.parent_id.isnot(None)))
        ).one_or_none()

    def _scalar(t):  # normalize (may be (None,) / (val,))
        if t is None: return 0
        v = t[0] if isinstance(t, tuple) else t
        return int(v or 0)

    return {"last_any_id": _scalar(last_any), "last_child_id": _scalar(last_reply)}

@app.post("/events/proposal-floor/{kind}/{item_id}/interventions")
def proposal_floor_post_intervention(
    request: Request,
    kind: str,
    item_id: int,
    body: str = Form(...),
    relates_to_id: str = Form(""),
):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    rel_id = None
    if relates_to_id and str(relates_to_id).strip():
        try: rel_id = int(relates_to_id)
        except: raise HTTPException(400, "relates_to_id must be an integer")
    kind = (kind or "").lower()
    if kind not in ("draft", "amendment"):
        raise HTTPException(400, "invalid kind")

    with get_session() as db:
        # resolve scope
        if kind == "draft":
            target = db.get(ProposalDraft, item_id) or (_ for _ in ()).throw(HTTPException(404))
            scope = dict(event_id=target.event_id, proposal_id=target.proposal_id, draft_id=target.id, amendment_id=None)
            # prop = db.get(AgendaProposal, target.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
            st = _pfloor_get_or_create_state(db, event_id=target.event_id, proposal_id=target.proposal_id,
                                             draft_id=target.id, amendment_id=None)
            # chair/president can always speak; others must have floor
            if not (effective_flags(user)["IS_CHAIR"] or effective_flags(user)["IS_PRESIDENT"]):
                if not _pf_user_has_floor(db, state=st, user_id=user.id):
                    raise HTTPException(403, "You do not currently have the floor.")

            # finish current speaker after posting (mirror general-floor)
            fs = db.get(ProposalFloorState, st.id)
            if fs and fs.current_speaker_request_id:
                cur = db.get(ProposalSpeakerRequest, fs.current_speaker_request_id)
                if cur and cur.status == "SPEAKING":
                    cur.status = "DONE"
                    db.add(cur)
                fs.current_speaker_request_id = None
                fs.updated_at = datetime.utcnow()
                db.add(fs); db.commit()

        elif kind == "amendment":
            amend = db.get(Amendment, item_id) or (_ for _ in ()).throw(HTTPException(404))
            draft = db.get(ProposalDraft, amend.draft_id)
            scope = dict(event_id=draft.event_id, proposal_id=draft.proposal_id, draft_id=None, amendment_id=amend.id)
            # prop  = db.get(AgendaProposal, draft.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
            st = _pfloor_get_or_create_state(db, event_id=draft.event_id, proposal_id=draft.proposal_id,
                                             draft_id=None, amendment_id=amend.id)

            if not (effective_flags(user)["IS_CHAIR"] or effective_flags(user)["IS_PRESIDENT"]):
                if not _pf_user_has_floor(db, state=st, user_id=user.id):
                    raise HTTPException(403, "You do not currently have the floor.")

            fs = db.get(ProposalFloorState, st.id)
            if fs and fs.current_speaker_request_id:
                cur = db.get(ProposalSpeakerRequest, fs.current_speaker_request_id)
                if cur and cur.status == "SPEAKING":
                    cur.status = "DONE"
                    db.add(cur)
                fs.current_speaker_request_id = None
                fs.updated_at = datetime.utcnow()
                db.add(fs); db.commit()
        else:
            raise HTTPException(400)
        # inside proposal_floor_post_intervention after scope resolution
        it = ProposalIntervention(
            event_id=scope["event_id"],
            proposal_id=scope["proposal_id"],
            draft_id=scope["draft_id"],
            amendment_id=scope["amendment_id"],
            by_user=user.id,            # <— use by_user if that's your model field
            body=body,
            parent_id=rel_id,
            local_no=_pfi_next_local_no(db, **scope)  # if you use local numbering
        )
        db.add(it); db.commit()
        mark_dirty(db, Scope(
            kind="PFLOOR",
            event_id=it.event_id,
            proposal_id=it.proposal_id,
            draft_id=it.draft_id,
            amendment_id=it.amendment_id,
        ))

    return RedirectResponse(url=f"/events/proposal-floor/{kind}/{item_id}", status_code=303)


@app.post("/events/proposal-floor/{kind}/{item_id}/floor/invite_ror")
def pfloor_invite_ror(
    kind: str,
    item_id: int,
    request: Request,
    relates_to_id: str = Form(""),
    to_handle: str = Form(...),
    ror_kind: str = Form("ROR"),   # "ROR" | "ROR_ALL"
):
    """
    Invite Right of Reply on proposal floor
    kind: "draft" | "amendment"
    item_id: draft.id or amendment.id (depending on kind)
    """
    user = current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    flags = effective_flags(user)
    if not (flags["IS_PRESIDENT"] or flags["IS_CHAIR"]):
        raise HTTPException(403, "Only president or chairman can invite RoR")

    kind = (kind or "").lower()
    with get_session() as db:
        # --- Resolve scope: draft/amendment + backing proposal ---
        if kind == "draft":
            d = db.get(ProposalDraft, item_id) or (_ for _ in ()).throw(HTTPException(404))
            prop = db.get(AgendaProposal, d.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
            if prop.status != ProposalStatus.accepted:
                raise HTTPException(404, "Agenda item not accepted")
            scope = dict(
                event_id=d.event_id,
                proposal_id=d.proposal_id,
                draft_id=d.id,
                amendment_id=None,
            )
        elif kind == "amendment":
            a = db.get(Amendment, item_id) or (_ for _ in ()).throw(HTTPException(404))
            d = db.get(ProposalDraft, a.draft_id)
            prop = db.get(AgendaProposal, d.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
            if prop.status != ProposalStatus.accepted:
                raise HTTPException(404, "Agenda item not accepted")
            scope = dict(
                event_id=d.event_id,
                proposal_id=d.proposal_id,
                draft_id=None,
                amendment_id=a.id,
            )
        else:
            raise HTTPException(400, "kind must be draft|amendment")

        # 🔹 Anchor to the *same* Question used for general floor for this agenda item
        q = ensure_general_floor_question(db, prop)

        to_user = db.exec(
            select(User).where(User.handle == to_handle.lstrip("@"))
        ).first()
        if not to_user:
            raise HTTPException(404, "User handle not found")

        # --- Normalize and validate target intervention for targeted RoR ---
        k = (ror_kind or "ROR").upper()
        target_id: int | None = None

        if k == "ROR":
            if not relates_to_id or not str(relates_to_id).strip().isdigit():
                raise HTTPException(400, "relates_to_id required for targeted RoR")
            target_id = int(relates_to_id)

            # Ensure the intervention exists AND is in this proposal-floor scope
            pfi = db.exec(
                select(ProposalIntervention)
                .where(
                    ProposalIntervention.id == target_id,
                    ProposalIntervention.event_id == scope["event_id"],
                    ProposalIntervention.proposal_id == scope["proposal_id"],
                    ProposalIntervention.draft_id == scope["draft_id"],
                    ProposalIntervention.amendment_id == scope["amendment_id"],
                )
            ).first()
            if not pfi:
                raise HTTPException(404, "Target intervention not found in this proposal floor")

        elif k == "ROR_ALL":
            target_id = None
        else:
            raise HTTPException(400, "invalid kind")

        # --- Create invite ---
        inv = RorInvite(
            question_id=q.id,                 # ✅ anchor to the agenda Question
            target_intervention_id=target_id, # proposal-floor intervention ID or None
            from_user_id=user.id,
            to_user_id=to_user.id,
            kind=k,                           # "ROR" or "ROR_ALL"
            status="PENDING",
        )
        db.add(inv)
        db.commit()
        db.refresh(inv)

        # --- Notification ---
        msg_target = f" on proposal-floor intervention #{target_id}" if target_id else " (to all)"
        note = Notification(
            user_id=to_user.id,
            question_id=q.id,                 # ✅ same Question anchor
            type="INVITE_ROR_PFLOOR",         # distinguish from general floor if you want
            message=f'Chair invited you to Right of Reply{msg_target}',
            is_read=False,
            payload_json=json.dumps({
                "invite_id": inv.id,
                "kind": k,
                "target_intervention_id": target_id,
                "question_id": q.id,
                "proposal_id": prop.id,
                "pfloor": {
                    "kind": kind,
                    "draft_id": scope["draft_id"],
                    "amendment_id": scope["amendment_id"],
                },
            }),
        )
        db.add(note)
        db.commit()

    wants_json = (
        request.query_params.get("ajax") == "1"
        or "application/json" in request.headers.get("accept", "")
    )
    if wants_json:
        return JSONResponse({"ok": True, "invite_id": inv.id, "status": inv.status})

    # redirect back to the correct proposal-floor page
    if kind == "draft":
        return RedirectResponse(
            url=f"/events/proposal-floor/draft/{item_id}",
            status_code=302,
        )
    else:
        return RedirectResponse(
            url=f"/events/proposal-floor/amendment/{item_id}",
            status_code=302,
        )

# ----- Common resolver for path targets -----
def _resolve_pfloor_target(db, *, draft_id: int | None, amend_id: int | None):
    _pfloor_target_check(draft_id, amend_id)
    if draft_id:
        d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))
        prop = db.get(AgendaProposal, d.proposal_id)
        return ("DRAFT", d.event_id, d.proposal_id, d.id, None, prop)
    a = db.get(Amendment, amend_id) or (_ for _ in ()).throw(HTTPException(404))
    d = db.get(ProposalDraft, a.draft_id); prop = db.get(AgendaProposal, d.proposal_id)
    return ("AMENDMENT", d.event_id, d.proposal_id, None, a.id, prop)

# register
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/register")
def pfloor_register(kind: str, id: int, request: Request, event_id: int, kind_req: str = Form("GENERAL"), relates_to_id: str = Form("")):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, prop = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        if prop.status != ProposalStatus.accepted:
            raise HTTPException(404, "Agenda item not accepted")
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)

        if not st.is_open and kind_req not in ("ROR","ROR_ALL"):
            raise HTTPException(400, "Speaker list is closed (use Right of Reply).")

        # duplicate active?
        exists = db.exec(
            select(ProposalSpeakerRequest).where(
                ProposalSpeakerRequest.event_id == ev_id,
                ProposalSpeakerRequest.proposal_id == prop_id,
                (ProposalSpeakerRequest.draft_id == draft_id) if draft_id else (ProposalSpeakerRequest.amendment_id == amend_id),
                ProposalSpeakerRequest.user_id == user.id,
                ProposalSpeakerRequest.status.in_(["QUEUED","SPEAKING"]),
            )
        ).first()
        if exists:
            return RedirectResponse(request.url_for("proposal_floor_draft" if mode=="DRAFT" else "proposal_floor_amend", **{("draft_id" if mode=="DRAFT" else "amend_id"): id}), status_code=303)

        target_id = None
        if kind_req.upper() == "ROR" and str(relates_to_id).strip():
            try: target_id = int(relates_to_id)
            except: raise HTTPException(400, "relates_to_id must be an integer")

        req = ProposalSpeakerRequest(
            event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id,
            user_id=user.id, kind=kind_req.upper(), status="QUEUED",
            position=_pfloor_next_position(db, st),
            target_intervention_id=target_id if kind_req.upper()=="ROR" else None,
        )
        db.add(req); db.commit()
    dest = f"/events/{event_id}/proposal-floor/{'draft' if mode=='DRAFT' else 'amendment'}/{id}"
    return RedirectResponse(dest, status_code=303)

# withdraw
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/withdraw")
def pfloor_withdraw(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        req = db.exec(
            select(ProposalSpeakerRequest).where(
                ProposalSpeakerRequest.event_id == ev_id,
                ProposalSpeakerRequest.proposal_id == prop_id,
                (ProposalSpeakerRequest.draft_id == draft_id) if draft_id else (ProposalSpeakerRequest.amendment_id == amend_id),
                ProposalSpeakerRequest.user_id == user.id,
                ProposalSpeakerRequest.status == "QUEUED",
            )
        ).first()
        if req:
            req.status = "WITHDRAWN"; db.add(req); db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

# toggle open/close (chair/president)
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/toggle")
def pfloor_toggle(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_PRESIDENT"] or f["IS_CHAIR"]): raise HTTPException(403)
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)
        st.is_open = not st.is_open; st.updated_at = datetime.utcnow()
        db.add(st); db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

# call next
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/call_next")
def pfloor_call_next(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_PRESIDENT"] or f["IS_CHAIR"]): raise HTTPException(403)
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, prop = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)

        next_req = db.exec(
            select(ProposalSpeakerRequest).where(
                ProposalSpeakerRequest.event_id == ev_id,
                ProposalSpeakerRequest.proposal_id == prop_id,
                (ProposalSpeakerRequest.draft_id == draft_id) if draft_id else (ProposalSpeakerRequest.amendment_id == amend_id),
                ProposalSpeakerRequest.status == "QUEUED",
            ).order_by(*_pfloor_queue_order())
        ).first()
        if not next_req:
            return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

        # finish current
        if st.current_speaker_request_id:
            prev = db.get(ProposalSpeakerRequest, st.current_speaker_request_id)
            if prev and prev.status == "SPEAKING":
                prev.status = "DONE"; db.add(prev)

        next_req.status = "SPEAKING"; db.add(next_req)
        st.current_speaker_request_id = next_req.id
        st.updated_at = datetime.utcnow(); db.add(st); db.commit()

        # notify selected
        label = (
            "Right of Reply to all" if next_req.kind == "ROR_ALL"
            else (f"Right of Reply to #{next_req.target_intervention_id}"
                  if next_req.kind == "ROR" else "You have the floor")
        )
        note = Notification(
            user_id=next_req.user_id, question_id=None,
            type="FLOOR", message=f"You have the floor — {label}: “{prop.title[:120]}”",
            is_read=False,
        )
        db.add(note); db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

# finish current
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/finish_current")
def pfloor_finish_current(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_PRESIDENT"] or f["IS_CHAIR"]): raise HTTPException(403)
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)
        if st.current_speaker_request_id:
            cur = db.get(ProposalSpeakerRequest, st.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur.status = "DONE"; db.add(cur)
            st.current_speaker_request_id = None
            st.updated_at = datetime.utcnow(); db.add(st); db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

# state (polled by UI every 3s, like general-floor)
@app.get("/events/{event_id}/proposal-floor/{kind}/{id}/floor/state")
def pfloor_state(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)

        cur_user_id = None; cur_kind = None
        if st.current_speaker_request_id:
            cur = db.get(ProposalSpeakerRequest, st.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur_user_id = cur.user_id; cur_kind = cur.kind

        speakers = _pfloor_load_speakers(db, st)
        handles = {u.id: u.handle for u in db.exec(select(User)).all()}

        data = {
            "is_open": st.is_open,
            "speaking_time_sec": st.speaking_time_sec,
            "current_req_id": st.current_speaker_request_id,
            "current_user_id": cur_user_id,
            "current_kind": cur_kind,
            "speakers": [{
                "id": s.id, "user_id": s.user_id, "handle": handles.get(s.user_id, str(s.user_id)),
                "kind": s.kind, "status": s.status, "position": s.position,
                "created_at": s.created_at.isoformat(),
            } for s in speakers],
        }
        return JSONResponse(data)

# chair announcement (no thread)
@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/floor/take_now")
def pfloor_take_now(kind: str, id: int, request: Request, event_id: int, message: str = Form("")):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]): raise HTTPException(403)
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, prop = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None,
            amend_id=id if kind=="amendment" else None
        )
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)

        # finish current
        if st.current_speaker_request_id:
            cur = db.get(ProposalSpeakerRequest, st.current_speaker_request_id)
            if cur and cur.status == "SPEAKING":
                cur.status = "DONE"; db.add(cur)
            st.current_speaker_request_id = None; st.updated_at = datetime.utcnow(); db.add(st); db.commit()

        chair = db.get(User, user.id); chair_label = f"@{chair.handle}" if chair and chair.handle else "Chair"
        payload = {"kind": "ANNOUNCE", "proposal_id": prop_id, "mode": mode, "id": id}
        for u in db.exec(select(User)).all():
            if u.id == user.id: continue
            db.add(Notification(
                user_id=u.id, question_id=None, type="ANNOUNCE",
                message=(f"{chair_label}: {message.strip()}" if message.strip() else f"{chair_label} made an announcement."),
                is_read=False, payload_json=json.dumps(payload),
            ))
        db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

def _early_cosigner_ids(d: ProposalDraft) -> list[int]:
    out = []
    for c in (d.cosigners_json or []):
        if isinstance(c, dict):
            # early == missing is_late OR False
            if not c.get("is_late", False):
                out.append(int(c.get("user_id", 0)))
        else:
            # legacy list[int] treated as early
            out.append(int(c))
    # include main sponsor first
    ids = [int(d.sponsor_id)] + [i for i in out if i and int(i) != int(d.sponsor_id)]
    # de-dupe
    seen=set(); res=[]
    for i in ids:
        if i and i not in seen: seen.add(i); res.append(i)
    return res

@app.post("/events/proposal-floor/draft/{draft_id}/invite_sponsors")
def pfloor_invite_sponsors(draft_id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]): raise HTTPException(403)
    with get_session() as db:
        d = db.get(ProposalDraft, draft_id) or (_ for _ in ()).throw(HTTPException(404))
        prop = db.get(AgendaProposal, d.proposal_id) or (_ for _ in ()).throw(HTTPException(404))
        if prop.status != ProposalStatus.accepted: raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=d.event_id, proposal_id=d.proposal_id, draft_id=d.id, amendment_id=None)

        handles = {u.id: u.handle for u in db.exec(select(User)).all()}
        for uid in _early_cosigner_ids(d):
            if uid == user.id: continue
            note = Notification(
                user_id=uid, question_id=None, type="INVITE_INTRO",
                message=f'Chair invites you to introduce: “{(d.title or prop.title)[:120]}”',
                is_read=False, payload_json=json.dumps({"kind": "INTRO", "draft_id": d.id}),
            )
            db.add(note)
            # Place them in queue (QUEUED) so first to accept can be called
            # (We could also wait for explicit accept; to keep parity with general-floor,
            # you can switch this to a true invite/accept if you prefer.)
            db.add(ProposalSpeakerRequest(
                event_id=d.event_id, proposal_id=d.proposal_id, draft_id=d.id, amendment_id=None,
                user_id=uid, kind="GENERAL", status="QUEUED", position=_pfloor_next_position(db, st),
            ))
        db.commit()
    return RedirectResponse(f"/events/proposal-floor/draft/{draft_id}", status_code=303)

def _get_or_create_early_vote(db, st: ProposalFloorState) -> ProposalEarlyVote:
    v = db.exec(
        select(ProposalEarlyVote).where(
            ProposalEarlyVote.event_id == st.event_id,
            ProposalEarlyVote.proposal_id == st.proposal_id,
            (ProposalEarlyVote.draft_id == st.draft_id)
            if st.draft_id
            else (ProposalEarlyVote.amendment_id == st.amendment_id),
        )
    ).first()
    if v:
        return v
    v = ProposalEarlyVote(
        event_id=st.event_id,
        proposal_id=st.proposal_id,
        draft_id=st.draft_id,
        amendment_id=st.amendment_id,
        is_open=False,
        yes=0,
        no=0,
    )
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/early/open")
def pfloor_early_open(event_id: int, kind: str, id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]):
        raise HTTPException(403)

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(
            db,
            draft_id=id if kind == "draft" else None,
            amend_id=id if kind == "amendment" else None,
        )
        if ev_id != event_id:
            raise HTTPException(404)

        st = _pfloor_get_or_create_state(
            db,
            event_id=ev_id,
            proposal_id=prop_id,
            draft_id=draft_id,
            amendment_id=amend_id,
        )

        v = _get_or_create_early_vote(db, st)
        v.is_open = True
        st.early_is_open = True
        db.add(v)
        db.add(st)
        db.commit()

    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)


@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/early/close")
def pfloor_early_close(event_id: int, kind: str, id: int, request: Request):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]):
        raise HTTPException(403)

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(
            db,
            draft_id=id if kind == "draft" else None,
            amend_id=id if kind == "amendment" else None,
        )
        if ev_id != event_id:
            raise HTTPException(404)

        st = _pfloor_get_or_create_state(
            db,
            event_id=ev_id,
            proposal_id=prop_id,
            draft_id=draft_id,
            amendment_id=amend_id,
        )

        v = _get_early_vote(db, st)
        if v is None:
            raise HTTPException(400, "No early-vote session exists for this item")

        v.is_open = False
        st.early_is_open = False
        db.add(v)
        db.add(st)
        db.commit()

    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/early/vote")
def pfloor_early_vote(event_id: int, kind: str, id: int, request: Request, choice: str = Form(...)):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    choice = (choice or "").upper()
    if choice not in ("YES", "NO", "ABSTAIN"):
        raise HTTPException(400, "choice must be YES, NO, or ABSTAIN")

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(
            db,
            draft_id=id if kind == "draft" else None,
            amend_id=id if kind == "amendment" else None,
        )
        if ev_id != event_id:
            raise HTTPException(404)

        st = _pfloor_get_or_create_state(
            db,
            event_id=ev_id,
            proposal_id=prop_id,
            draft_id=draft_id,
            amendment_id=amend_id,
        )

        v = _get_early_vote(db, st)
        if v is None or not v.is_open:
            raise HTTPException(400, "Early voting is not open")

        if _has_user_early_voted(db, st, user.id):
            raise HTTPException(400, "You have already voted in this early vote")

        ballot = ProposalEarlyBallot(
            event_id=st.event_id,
            proposal_id=st.proposal_id,
            draft_id=st.draft_id,
            amendment_id=st.amendment_id,
            user_id=user.id,
            choice=choice,
        )
        db.add(ballot)

        if choice == "YES":
            v.yes += 1
        elif choice == "NO":
            v.no += 1
        else:
            v.abstain += 1

        db.add(v)
        db.commit()

    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)


def _get_or_create_formal_vote(db, st: ProposalFloorState) -> ProposalFormalVote:
    v = db.exec(
        select(ProposalFormalVote).where(
            ProposalFormalVote.event_id == st.event_id,
            ProposalFormalVote.proposal_id == st.proposal_id,
            (ProposalFormalVote.draft_id == st.draft_id) if st.draft_id else (ProposalFormalVote.amendment_id == st.amendment_id),
        )
    ).first()
    if v: return v
    v = ProposalFormalVote(
        event_id=st.event_id, proposal_id=st.proposal_id,
        draft_id=st.draft_id, amendment_id=st.amendment_id,
        is_open=False, yes=0, no=0, abstain=0
    )
    db.add(v); db.commit(); db.refresh(v); return v

@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/formal/open")
def pfloor_formal_open(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user)
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]): raise HTTPException(403)
    
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None, amend_id=id if kind=="amendment" else None)
        if ev_id != event_id:
            raise HTTPException(404)
        
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)
        
        # Check if early voting has already accepted the proposal
        evote = db.exec(
            select(ProposalEarlyVote).where(
                ProposalEarlyVote.event_id == ev_id,
                ProposalEarlyVote.proposal_id == prop_id,
                (ProposalEarlyVote.draft_id == draft_id) if draft_id else (ProposalEarlyVote.amendment_id == amend_id),
            )
        ).first()
        adopted_by_consensus = (evote and not evote.is_open and evote.no == 0 and (evote.yes > 0))

        if adopted_by_consensus:
            raise HTTPException(400, "Early vote has already accepted the proposal, no formal vote needed.")
        
        # If not adopted by consensus, open the formal vote
        v = _get_or_create_formal_vote(db, st)
        v.is_open = True
        v.opened_at = datetime.utcnow()
        db.add(v); db.commit()
        
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/formal/vote")
def pfloor_formal_vote(kind: str, id: int, request: Request, event_id: int, choice: str = Form(...)):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    choice = (choice or "").upper()
    if choice not in ("YES", "NO", "ABSTAIN"):
        raise HTTPException(400, "choice must be YES/NO/ABSTAIN")

    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(
            db,
            draft_id=id if kind == "draft" else None,
            amend_id=id if kind == "amendment" else None,
        )
        if ev_id != event_id:
            raise HTTPException(404)
        
        st = _pfloor_get_or_create_state(
            db,
            event_id=ev_id,
            proposal_id=prop_id,
            draft_id=draft_id,
            amendment_id=amend_id,
        )

        v = _get_or_create_formal_vote(db, st)
        if not v.is_open:
            raise HTTPException(400, "Voting is not open")

        # one ballot per user per formal vote → LOCK
        existing = db.exec(
            select(ProposalFormalBallot).where(
                ProposalFormalBallot.formal_vote_id == v.id,
                ProposalFormalBallot.user_id == user.id,
            )
        ).first()
        if existing:
            raise HTTPException(400, "You have already cast your formal vote")

        # record ballot
        ballot = ProposalFormalBallot(
            event_id=st.event_id,
            proposal_id=st.proposal_id,
            draft_id=st.draft_id,
            amendment_id=st.amendment_id,
            user_id=user.id,
            choice=choice,
            formal_vote_id=v.id,
        )

        # update counters once per new ballot
        if choice == "YES":
            v.yes += 1
        elif choice == "NO":
            v.no += 1
        else:
            v.abstain += 1

        db.add(ballot)
        db.add(v)
        db.commit()

    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/formal/close")
def pfloor_formal_close(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user); 
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]): raise HTTPException(403)
    
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)

        mode, ev_id, prop_id, draft_id, amend_id, _ = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None, amend_id=id if kind=="amendment" else None)
        
        if ev_id != event_id:
            raise HTTPException(404)
        
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)
        
        # Check for early vote adoption before closing the formal vote
        evote = db.exec(
            select(ProposalEarlyVote).where(
                ProposalEarlyVote.event_id == ev_id,
                ProposalEarlyVote.proposal_id == prop_id,
                (ProposalEarlyVote.draft_id == draft_id) if draft_id else (ProposalEarlyVote.amendment_id == amend_id),
            )
        ).first()
        adopted_by_consensus = (evote and not evote.is_open and evote.no == 0 and (evote.yes > 0))

        if adopted_by_consensus:
            raise HTTPException(400, "Early vote has already accepted the proposal, no need to close formal vote.")

        v = _get_or_create_formal_vote(db, st)
        v.is_open = False
        v.closed_at = datetime.utcnow()
        db.add(v); db.commit()
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}", status_code=303)

def _formal_result(v: ProposalFormalVote) -> dict:
    counted_total = v.yes + v.no  # abstentions excluded
    status = "accepted" if v.yes > v.no else ("rejected" if v.no >= v.yes else "rejected")  # ties rejected
    return {"yes": v.yes, "no": v.no, "abstain": v.abstain, "counted_total": counted_total, "status": status}

@app.post("/events/{event_id}/proposal-floor/{kind}/{id}/close_discussion")
def pfloor_close_discussion(kind: str, id: int, request: Request, event_id: int):
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))
    f = effective_flags(user); 
    if not (f["IS_CHAIR"] or f["IS_PRESIDENT"]): raise HTTPException(403)
    with get_session() as db:
        _require_event_access(db=db, user=user, event_id=event_id)
        mode, ev_id, prop_id, draft_id, amend_id, prop = _resolve_pfloor_target(db,
            draft_id=id if kind=="draft" else None, amend_id=id if kind=="amendment" else None)
        if ev_id != event_id:
            raise HTTPException(404)
        st = _pfloor_get_or_create_state(db, event_id=ev_id, proposal_id=prop_id, draft_id=draft_id, amendment_id=amend_id)

        # prefer early vote outcome if present/closed; else formal if closed; else neutral
        evote = db.exec(
            select(ProposalEarlyVote).where(
                ProposalEarlyVote.event_id == ev_id,
                ProposalEarlyVote.proposal_id == prop_id,
                (ProposalEarlyVote.draft_id == draft_id) if draft_id else (ProposalEarlyVote.amendment_id == amend_id),
            )
        ).first()
        adopted_by_consensus = (evote and not evote.is_open and evote.no == 0 and (evote.yes > 0))

        fvote = db.exec(
            select(ProposalFormalVote).where(
                ProposalFormalVote.event_id == ev_id,
                ProposalFormalVote.proposal_id == prop_id,
                (ProposalFormalVote.draft_id == draft_id) if draft_id else (ProposalFormalVote.amendment_id == amend_id),
            )
        ).first()
        formally_adopted = False
        if fvote and not fvote.is_open:
            formally_adopted = (fvote.yes > fvote.no)

        if mode == "AMENDMENT":
            # record nothing on draft here; caller decides the next step
            pass
        else:
            d = db.get(ProposalDraft, draft_id)
            if adopted_by_consensus or formally_adopted:
                # mark as ADOPTED (your later "adoption list" page can pick this up)
                d.status = ProposalDraftStatus.ADOPTED
                db.add(d); db.commit()

        # Optionally: clear floor state
        st.is_open = False; st.current_speaker_request_id = None; st.updated_at = datetime.utcnow()
        db.add(st); db.commit()

    # Send user back with a query param the UI can use to prompt about the adoption list
    return RedirectResponse(f"/events/{event_id}/proposal-floor/{kind}/{id}?closed=1", status_code=303)

def _decision_compute_outcome_draft(
    draft: ProposalDraft,
    fvote: ProposalFormalVote | None,
    evote: ProposalEarlyVote | None,
) -> str:
    """
    Map internal status + vote state into a human-readable outcome
    for TABLED DRAFTS on the Decision page.
    """

    # 1) Directly from status (this is what PFLOOR sets)
    if draft.status == ProposalDraftStatus.ADOPTED:
        return "Adopted"
    if draft.status == ProposalDraftStatus.WITHDRAWN:
        return "Withdrawn"
    if draft.status == ProposalDraftStatus.REINTRODUCED:
        return "Reintroduced"

    # From here, status == TABLED
    # Distinguish Pending vs Not adopted based on vote objects.

    # Pending early vote?
    if evote and getattr(evote, "is_open", False):
        return "Pending"

    # Pending formal vote?
    if fvote and getattr(fvote, "is_open", False):
        return "Pending"

    # Voting existed but is closed and draft never got ADOPTED → failed.
    if evote or fvote:
        return "Not adopted"

    # No voting at all → still pending conceptually.
    return "Pending"


def _decision_compute_outcome_amendment(
    fvote: ProposalFormalVote | None,
    evote: ProposalEarlyVote | None,
) -> str:
    """
    Outcome for AMENDMENTS, using the same logic as pfloor_close_discussion,
    but read-only (we do NOT modify anything here).
    """

    adopted_by_consensus = (
        evote
        and not getattr(evote, "is_open", False)
        and (evote.no == 0)
        and (evote.yes > 0)
    )

    formally_adopted = False
    if fvote and not getattr(fvote, "is_open", False):
        formally_adopted = (fvote.yes > fvote.no)

    if adopted_by_consensus or formally_adopted:
        return "Adopted"

    # if we have votes and both early/formal are closed but not adopted
    if ((evote and not evote.is_open) or (fvote and not fvote.is_open)):
        return "Not adopted"

    return "Pending"

@app.get("/events/decision", response_class=HTMLResponse)
def decision_page(request: Request):
    """
    Read-only page listing all *tabled* drafts (L docs) and their amendments
    for the current event, showing final outcome + tallies.

    Proposal floor already decides ADOPTED for drafts via pfloor_close_discussion.
    For amendments, we mirror the same logic but do not mutate anything.
    """
    user = current_user(request) or (_ for _ in ()).throw(HTTPException(401))

    with get_session() as db:
        event = _get_current_event(db) or (_ for _ in ()).throw(HTTPException(404, "No event"))
        drafts = db.exec(
            select(ProposalDraft)
            .where(
                ProposalDraft.event_id == event.id,
                ProposalDraft.is_submitted == True,  # "tabled" L docs
            )
            .order_by(ProposalDraft.l_number, ProposalDraft.id)
        ).all()

        draft_ids = [d.id for d in drafts] if drafts else []
        draft_by_id = {d.id: d for d in drafts}

        users = db.exec(select(User)).all()
        user_map = {u.id: u for u in users}

        flags = effective_flags(user) if user else EMPTY_FLAGS

        evotes_drafts = []
        fvotes_drafts = []
        if draft_ids:
            evotes_drafts = db.exec(
                select(ProposalEarlyVote).where(
                    ProposalEarlyVote.event_id == event.id,
                    ProposalEarlyVote.draft_id.in_(draft_ids),
                )
            ).all()

            fvotes_drafts = db.exec(
                select(ProposalFormalVote).where(
                    ProposalFormalVote.event_id == event.id,
                    ProposalFormalVote.draft_id.in_(draft_ids),
                )
            ).all()

        evote_by_draft = {v.draft_id: v for v in evotes_drafts}
        fvote_by_draft = {v.draft_id: v for v in fvotes_drafts}

        results_drafts = []
        for d in drafts:
            evote = evote_by_draft.get(d.id)
            fvote = fvote_by_draft.get(d.id)
            outcome = _decision_compute_outcome_draft(draft=d, fvote=fvote, evote=evote)

            results_drafts.append(
                {
                    "draft": d,
                    "outcome": outcome,
                    "early_vote": evote,
                    "formal_vote": fvote,
                }
            )

        # ---------------- AMENDMENTS + SINGLE-PHASE VOTES ----------------
        amendments = []
        if draft_ids:
            amendments = db.exec(
                select(Amendment)
                .where(Amendment.draft_id.in_(draft_ids))
                .order_by(Amendment.draft_id, Amendment.am_no.asc())
            ).all()

        amend_ids = [a.id for a in amendments]

        # Load AmendmentVoteState in one query (no N+1)
        vstate_by_amend = {}
        if amend_ids:
            vstates = db.exec(
                select(AmendmentVoteState).where(AmendmentVoteState.amendment_id.in_(amend_ids))
            ).all()
            vstate_by_amend = {v.amendment_id: v for v in vstates}

        def _decision_compute_outcome_amendment_single(vstate: "AmendmentVoteState | None") -> str:
            """
            Single-phase outcome:
              - Pending: no vote_state OR vote still open
              - Adopted: closed AND no==0 AND yes>0
              - Not adopted: closed AND (no>0 OR yes==0)
            """
            if not vstate:
                return "Pending"
            if vstate.is_open:
                return "Pending"
            yes = vstate.yes or 0
            no = vstate.no or 0
            if no == 0 and yes > 0:
                return "Adopted"
            return "Not adopted"

        results_amendments = []
        for a in amendments:
            parent_draft = draft_by_id.get(a.draft_id)
            vstate = vstate_by_amend.get(a.id)
            outcome = _decision_compute_outcome_amendment_single(vstate)

            results_amendments.append(
                {
                    "amendment": a,
                    "draft": parent_draft,       # <-- template expects item.draft
                    "vote_state": vstate,        # <-- template expects item.vote_state
                    "outcome": outcome,
                }
            )

    return templates.TemplateResponse(
        "events/decision.html",
        {
            "request": request,
            "event": event,
            "results_drafts": results_drafts,
            "results_amendments": results_amendments,
            "user": user,
            "flags": flags,
            "user_map": user_map,
        },
    )