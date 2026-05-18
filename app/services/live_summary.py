# app/services/live_summary.py
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal, List, Dict

import httpx
import sqlalchemy as sa
from sqlmodel import Session, select

from app.models import User
from app.models import LiveSummary
from app.models import Intervention, ProposalMessage, ProposalIntervention

def now_utc():
    return datetime.now(timezone.utc)

def ensure_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

Kind = Literal["GENERAL", "PROOM", "PFLOOR"]

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral:7b-instruct")
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "240"))
SUMMARY_MAX_ITEMS = int(os.getenv("LIVE_SUMMARY_MAX_ITEMS", "20"))
SUMMARY_MAX_CHARS = int(os.getenv("LIVE_SUMMARY_MAX_CHARS", "6000"))

SYSTEM = """You are a neutral meeting rapporteur.
Update the EXISTING SUMMARY using ONLY the NEW MESSAGES provided for THIS ROOM.
Do not invent facts. If unclear, say it's unclear.

Output format (markdown):
## Summary
- bullets (5-10)

## Decisions
- (or "None yet")

## Action items
- (or "None yet")

## Open questions
- (or "None yet")
"""

@dataclass(frozen=True)
class Scope:
    kind: Kind
    question_id: Optional[int] = None
    room_id: Optional[int] = None
    event_id: Optional[int] = None
    proposal_id: Optional[int] = None
    draft_id: Optional[int] = None
    amendment_id: Optional[int] = None

def make_scope_key(s: Scope) -> str:
    if s.kind == "GENERAL":
        assert s.question_id is not None
        return f"GENERAL:question={s.question_id}"
    if s.kind == "PROOM":
        assert s.room_id is not None
        return f"PROOM:room={s.room_id}"
    # PFLOOR
    assert s.event_id is not None and s.proposal_id is not None
    if s.draft_id is not None:
        return f"PFLOOR:event={s.event_id}:proposal={s.proposal_id}:draft={s.draft_id}"
    assert s.amendment_id is not None
    return f"PFLOOR:event={s.event_id}:proposal={s.proposal_id}:amend={s.amendment_id}"

async def ollama_generate(prompt: str) -> str:
    timeout = httpx.Timeout(
        timeout=OLLAMA_TIMEOUT_S,
        connect=10.0,
        read=OLLAMA_TIMEOUT_S,
        write=30.0,
        pool=30.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
            },
        )
        r.raise_for_status()

        data = r.json()
        text = (data.get("response") or "").strip()
        if not text:
            raise RuntimeError("Ollama returned an empty response")
        return text

def _ensure_row(db: Session, scope: Scope) -> LiveSummary:
    key = make_scope_key(scope)
    row = db.get(LiveSummary, key)
    if row:
        return row
    row = LiveSummary(
        scope_key=key,
        kind=scope.kind,
        question_id=scope.question_id,
        room_id=scope.room_id,
        event_id=scope.event_id,
        proposal_id=scope.proposal_id,
        draft_id=scope.draft_id,
        amendment_id=scope.amendment_id,
        dirty=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row

def mark_dirty(db: Session, scope: Scope) -> None:
    row = _ensure_row(db, scope)
    row.dirty = True
    db.add(row)
    db.commit()

def _fetch_new_items(db: Session, scope: Scope, last_id: int, limit: int = 40) -> List[Dict]:
    if scope.kind == "GENERAL":
        q = (
            select(Intervention, User.handle)
            .join(User, User.id == Intervention.by_user)
            .where((Intervention.question_id == scope.question_id) & (Intervention.id > last_id))
            .order_by(Intervention.id.asc())
            .limit(limit)
        )
        rows = db.exec(q).all()
        return [{"id": i.id, "author": h, "text": i.body} for (i, h) in rows]

    if scope.kind == "PROOM":
        q = (
            select(ProposalMessage, User.handle)
            .join(User, User.id == ProposalMessage.user_id)
            .where((ProposalMessage.room_id == scope.room_id) & (ProposalMessage.id > last_id))
            .order_by(ProposalMessage.id.asc())
            .limit(limit)
        )
        rows = db.exec(q).all()
        return [{"id": m.id, "author": h, "text": m.body} for (m, h) in rows]

    # PFLOOR
    q = (
        select(ProposalIntervention, User.handle)
        .join(User, User.id == ProposalIntervention.by_user)
        .where(
            (ProposalIntervention.event_id == scope.event_id)
            & (ProposalIntervention.proposal_id == scope.proposal_id)
            & (ProposalIntervention.id > last_id)
        )
    )
    if scope.draft_id is not None:
        q = q.where((ProposalIntervention.draft_id == scope.draft_id) & (ProposalIntervention.amendment_id.is_(None)))
    else:
        q = q.where((ProposalIntervention.amendment_id == scope.amendment_id) & (ProposalIntervention.draft_id.is_(None)))

    q = q.order_by(ProposalIntervention.id.asc()).limit(limit)
    rows = db.exec(q).all()
    return [{"id": pi.id, "author": h, "text": pi.body} for (pi, h) in rows]

def _build_prompt(existing_summary: str, new_items: List[Dict]) -> str:
    clipped_items = new_items[-SUMMARY_MAX_ITEMS:]

    lines = []
    total_chars = 0
    for x in clipped_items:
        line = f"- ({x['id']}) {x['author']}: {x['text']}"
        if total_chars + len(line) > SUMMARY_MAX_CHARS:
            break
        lines.append(line)
        total_chars += len(line)

    transcript = "\n".join(lines) if lines else "(no new usable content)"

    return f"""{SYSTEM}

EXISTING SUMMARY:
{existing_summary if existing_summary else "(empty)"}

NEW MESSAGES (ONLY FROM THIS ROOM):
{transcript}

TASK: Return the UPDATED summary in the required format.
"""

async def refresh_summary_if_needed(
    db: Session,
    scope: Scope,
    *,
    min_interval_sec: int = 2,
    max_new: int = 40,
    force: bool = False,
) -> LiveSummary:
    row = _ensure_row(db, scope)

    # --- FIX timezone math ---
    now = now_utc()
    row_updated = ensure_aware_utc(row.updated_at)

    if not force:
        if not row.dirty:
            return row
        if row_updated and (now - row_updated) < timedelta(seconds=min_interval_sec):
            return row

    new_items = _fetch_new_items(db, scope, row.last_item_id, limit=max_new)
    if not new_items:
        row.dirty = False
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    prompt = _build_prompt(row.summary, new_items)

    try:
        updated = await ollama_generate(prompt)
    except (httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPError, RuntimeError) as e:
        logger.warning("live summary refresh failed for %s: %s", row.scope_key, e)

        # Keep it dirty so the app can retry later instead of crashing now.
        row.dirty = True
        row.updated_at = now
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    row.summary = updated
    row.last_item_id = new_items[-1]["id"]
    row.dirty = False
    row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row