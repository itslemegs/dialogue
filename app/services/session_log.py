# app/services/session_log.py

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request
from sqlmodel import Session

from app.models import ExperimentSessionLog
from app.security import unsign_cookie


SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "csrf",
    "authorization",
    "cookie",
}

MAX_DETAILS_CHARS = 10_000


def now_utc():
    return datetime.now(timezone.utc)


def get_or_make_session_key(request: Request) -> str:
    existing = getattr(request.state, "exp_session_key", None)
    if existing:
        return existing

    cookie_value = request.cookies.get("exp_session_key")
    if cookie_value:
        request.state.exp_session_key = cookie_value
        return cookie_value

    new_value = str(uuid.uuid4())
    request.state.exp_session_key = new_value
    return new_value


def get_request_id(request: Request) -> str:
    existing = getattr(request.state, "request_id", None)
    if existing:
        return existing

    new_value = str(uuid.uuid4())
    request.state.request_id = new_value
    return new_value


def user_id_from_user(user: Any | None) -> Optional[int]:
    if user is None:
        return None

    value = getattr(user, "id", None)
    if value is None:
        return None

    try:
        return int(value)
    except Exception:
        return None


def user_id_from_request(request: Request) -> Optional[int]:
    """
    Best-effort user id detection for experiment logs.

    Your app stores login identity in the signed "session" cookie,
    so generic client/server logs need to read that cookie too.
    """
    state_user = getattr(request.state, "user", None)
    if state_user is not None:
        return user_id_from_user(state_user)

    state_user_id = getattr(request.state, "user_id", None)
    if state_user_id is not None:
        try:
            return int(state_user_id)
        except Exception:
            pass

    try:
        session_user_id = request.session.get("user_id")
        if session_user_id is not None:
            return int(session_user_id)
    except Exception:
        pass

    try:
        cookie_user_id = unsign_cookie(request.cookies.get("session"))
        if cookie_user_id is not None:
            return int(cookie_user_id)
    except Exception:
        pass

    return None


def hash_ip(request: Request) -> Optional[str]:
    raw_ip = None

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        raw_ip = forwarded.split(",")[0].strip()
    elif request.client:
        raw_ip = request.client.host

    if not raw_ip:
        return None

    salt = os.getenv("SESSION_LOG_SALT", "dev-session-log-salt")
    return hashlib.sha256(f"{salt}:{raw_ip}".encode("utf-8")).hexdigest()[:24]


def scrub_value(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {}
        for k, v in value.items():
            key = str(k)
            lower_key = key.lower()

            if any(sensitive in lower_key for sensitive in SENSITIVE_KEYS):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = scrub_value(v)

        return cleaned

    if isinstance(value, list):
        return [scrub_value(v) for v in value[:200]]

    if isinstance(value, tuple):
        return [scrub_value(v) for v in value[:200]]

    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    return str(value)


def details_to_json(details: dict[str, Any] | None) -> Optional[str]:
    if not details:
        return None

    cleaned = scrub_value(details)

    try:
        text = json.dumps(cleaned, ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps({"unserializable_details": str(cleaned)}, ensure_ascii=False)

    if len(text) > MAX_DETAILS_CHARS:
        text = text[:MAX_DETAILS_CHARS] + "...[TRUNCATED]"

    return text


def page_log_context(path: str | None) -> dict[str, Any]:
    """Categorical context from known participant paths; no DB or query values.

    This describes the page/route, not proof that an action succeeded. Explicit
    semantic targets and details remain authoritative over these defaults.
    """
    path = (path or "").split("?", 1)[0].rstrip("/")
    if path == "/dashboard":
        return {"phase": "dashboard", "page_kind": "dashboard"}
    event = re.match(r"^/events/(\d+)(?:/|$)", path)
    context = {"event_id": int(event[1])} if event else {}
    tail = path[event.end(1):] if event else path
    pages = {
        "/menu": ("event_menu", "event_menu"),
        "/propose-agenda": ("agenda_setting", "propose_agenda"),
        "/review-agenda": ("agenda_review", "review_agenda"),
        "/view-agenda": ("agenda_setting", "view_agenda"),
        "/general-floor": ("general_floor", "general_floor_index"),
        "/proposal-discussion": ("proposal_discussion", "proposal_discussion_index"),
        "/proposal-floor": ("proposal_floor", "proposal_floor_index"),
        "/view-draft": ("proposal_discussion", "view_draft_index"),
        "/decision": ("decision", "decision"),
    }
    if event and tail in pages:
        phase, page_kind = pages[tail]
        return dict(context, phase=phase, page_kind=page_kind,
                    target_type="event", target_id=context["event_id"])
    match = re.match(r"^/(general-floor|review-agenda|proposal-discussion)/(\d+)(?:/|$)", tail)
    if event and match:
        section, proposal_id = match[1], int(match[2])
        phase, page_kind = {
            "general-floor": ("general_floor", "general_floor_item"),
            "review-agenda": ("agenda_review", "review_agenda"),
            "proposal-discussion": ("proposal_discussion", "rooms_index"),
        }[section]
        context.update(phase=phase, page_kind=page_kind, proposal_id=proposal_id,
                       target_type="agenda_proposal", target_id=proposal_id)
        room = re.match(r"^/proposal-discussion/\d+/rooms/(\d+)(?:/|$)", tail)
        if room:
            context.update(room_id=int(room[1]), page_kind="proposal_room",
                           target_type="proposal_room", target_id=int(room[1]))
            draft = re.match(r"^/proposal-discussion/\d+/rooms/\d+/drafts/(\d+)(?:/|$)", tail)
            if draft:
                context.update(draft_id=int(draft[1]), page_kind="draft_detail",
                               target_type="proposal_draft", target_id=int(draft[1]))
                amendment = re.search(r"/amendments/(\d+)(?:/|$)", tail)
                if amendment:
                    context.update(amendment_id=int(amendment[1]), page_kind="amendment_detail",
                                   target_type="amendment", target_id=int(amendment[1]))
        return context
    match = re.match(r"^/proposal-floor/(draft|amendment)/(\d+)(?:/|$)", tail)
    if event and match:
        kind, item_id = match[1], int(match[2])
        context.update(phase="proposal_floor", page_kind="proposal_floor_item",
                       target_type=kind, target_id=item_id)
        context[kind + "_id"] = item_id
        return context
    match = re.match(r"^/drafts/(\d+)(?:/|$)", tail)
    if match:
        context.update(phase="proposal_discussion", page_kind="draft_detail",
                       draft_id=int(match[1]), target_type="proposal_draft", target_id=int(match[1]))
    return context


def add_session_log(
    db: Session,
    *,
    request: Request | None = None,
    user: Any | None = None,
    event_id: int | None = None,
    source: str = "semantic",
    user_id: int | None = None,
    action: str,
    page: str | None = None,
    route: str | None = None,
    method: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    phase: str | None = None,
    target_type: str | None = None,
    target_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> ExperimentSessionLog:
    session_key = None
    request_id = None
    ip = None
    ua = None

    inferred_user_id = None

    if user_id is not None:
        try:
            inferred_user_id = int(user_id)
        except Exception:
            inferred_user_id = None

    if inferred_user_id is None:
        inferred_user_id = user_id_from_user(user)

    if request is not None:
        session_key = get_or_make_session_key(request)
        request_id = get_request_id(request)
        ip = hash_ip(request)
        ua = request.headers.get("user-agent")

        if inferred_user_id is None:
            inferred_user_id = user_id_from_request(request)

        if page is None:
            page = str(request.url.path)

        if method is None:
            method = request.method

    context = page_log_context(page)
    # Resolved document context is supplied by routes already loading the object.
    # Never derive client identity/access from client-reported paths or details.
    if request is not None and source != "client":
        context.update(getattr(request.state, "session_log_context", None) or {})
        if route is None:
            route = getattr(request.scope.get("route"), "path", None)
        if event_id is None:
            event_id = context.get("event_id")
    supplied = details or {}
    phase = phase or supplied.get("phase") or context.get("phase")
    inferred_target = target_type is None and target_id is None
    if inferred_target:
        target_type = context.get("target_type")
        target_id = context.get("target_id")
    context_details = {key: value for key, value in context.items()
                       if key not in {"phase", "event_id", "target_type", "target_id"}}
    details = {**context_details, **supplied}
    if inferred_target and target_type is not None:
        details["target_inferred_from"] = "page" if source == "client" else "route"
    # Created objects may not yet occur in the request path (rooms/interventions).
    id_key = {"agenda_proposal": "proposal_id", "proposal_room": "room_id",
              "proposal_draft": "draft_id", "draft": "draft_id",
              "amendment": "amendment_id"}.get(target_type)
    if id_key and target_id is not None and str(target_id).isdigit():
        details.setdefault(id_key, int(target_id))

    log = ExperimentSessionLog(
        event_id=event_id,
        user_id=inferred_user_id,
        session_key=session_key,
        source=source,
        action=action,
        page=page,
        route=route,
        method=method,
        status_code=status_code,
        duration_ms=duration_ms,
        phase=phase,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        ip_hash=ip,
        user_agent=ua,
        request_id=request_id,
        details_json=details_to_json(details),
    )

    db.add(log)
    return log


def log_event(
    db: Session,
    *,
    request: Request | None = None,
    user: Any | None = None,
    user_id: int | None = None,
    event_id: int | None = None,
    source: str = "semantic",
    action: str,
    page: str | None = None,
    route: str | None = None,
    method: str | None = None,
    status_code: int | None = None,
    duration_ms: int | None = None,
    phase: str | None = None,
    target_type: str | None = None,
    target_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> ExperimentSessionLog:
    log = add_session_log(
        db,
        request=request,
        user=user,
        user_id=user_id,
        event_id=event_id,
        source=source,
        action=action,
        page=page,
        route=route,
        method=method,
        status_code=status_code,
        duration_ms=duration_ms,
        phase=phase,
        target_type=target_type,
        target_id=target_id,
        details=details,
    )
    db.commit()
    return log