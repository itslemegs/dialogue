# app/services/live_summary.py
from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal, List, Dict

import httpx
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
    return dt.astimezone(timezone.utc)


Kind = Literal["GENERAL", "PROOM", "PFLOOR"]

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "stablelm2:1.6b")
OLLAMA_TIMEOUT_S = int(os.getenv("OLLAMA_TIMEOUT_S", "240"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE", "10m")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))

SUMMARY_MAX_ITEMS = int(os.getenv("LIVE_SUMMARY_MAX_ITEMS", "20"))
SUMMARY_MAX_CHARS = int(os.getenv("LIVE_SUMMARY_MAX_CHARS", "5000"))
SUMMARY_MAX_EXISTING_CHARS = int(os.getenv("LIVE_SUMMARY_MAX_EXISTING_CHARS", "2500"))
SUMMARY_MAX_ITEM_CHARS = int(os.getenv("LIVE_SUMMARY_MAX_ITEM_CHARS", "800"))


SYSTEM = """You are a neutral meeting note-taker.

Your job is to summarize the actual participant messages.

Rules:
- Use ONLY the messages inside <new_messages>.
- Do NOT invent participants.
- Do NOT write placeholder names like participant1, participant2, or Participant123.
- Do NOT copy the instructions.
- Do NOT write phrases like "5 neutral bullet points".
- Do NOT add sections other than the required four sections.
- If there is not enough content, write a short factual summary.
- Keep it concise.

Required output format exactly:

## Summary
- ...

## Decisions
- None yet

## Action items
- None yet

## Open questions
- None yet
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

    assert s.event_id is not None and s.proposal_id is not None

    if s.draft_id is not None:
        return f"PFLOOR:event={s.event_id}:proposal={s.proposal_id}:draft={s.draft_id}"

    assert s.amendment_id is not None
    return f"PFLOOR:event={s.event_id}:proposal={s.proposal_id}:amend={s.amendment_id}"


def _compact_text(text: str | None) -> str:
    text = (text or "").strip()
    return " ".join(text.split())


def _clip_text(text: str | None, max_chars: int) -> str:
    text = _compact_text(text)

    if len(text) <= max_chars:
        return text

    if max_chars <= 1:
        return "…"

    return text[: max_chars - 1].rstrip() + "…"


def _clean_summary_output(text: str) -> str:
    text = (text or "").strip()

    # Remove code fences.
    if text.startswith("```markdown"):
        text = text.removeprefix("```markdown").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()

    if text.endswith("```"):
        text = text[:-3].strip()

    # Cut anything before the real summary.
    idx = text.find("## Summary")
    if idx >= 0:
        text = text[idx:].strip()

    bad_phrases = [
        "participant1",
        "participant2",
        "participant123",
        "5 neutral bullet points",
        "updated summary with new participant messages",
        "inserted into the existing room summary",
        "the task is to return",
        "required output format",
    ]

    lowered = text.lower()

    # If StableLM produced template garbage, discard it.
    if any(p in lowered for p in bad_phrases):
        return """## Summary
- New messages were posted in the room, but the automatic summary could not reliably summarize them.

## Decisions
- None yet

## Action items
- None yet

## Open questions
- None yet"""

    required_sections = [
        "## Summary",
        "## Decisions",
        "## Action items",
        "## Open questions",
    ]

    if "## Summary" not in text:
        text = f"""## Summary
- {_clip_text(text, 1000) if text else "No usable summary was generated."}

## Decisions
- None yet

## Action items
- None yet

## Open questions
- None yet"""

    for heading in required_sections:
        if heading not in text:
            text += f"\n\n{heading}\n- None yet"

    # Remove accidental extra content after a repeated second summary.
    second_summary = text.find("\n## Summary", len("## Summary"))
    if second_summary != -1:
        text = text[:second_summary].strip()

    return text.strip()


def _looks_like_bad_stablelm_summary(text: str, new_items: List[Dict]) -> bool:
    text = (text or "").strip()
    lowered = text.lower()

    bad_phrases = [
        "participant1",
        "participant2",
        "participant123",
        "participants' actual messages",
        "participant's actual messages",
        "5 points",
        "4 points",
        "3 points",
        "2 points",
        "1 point",
        "unresolved queries",
        "additional, open-ended inquiries",
        "5 neutral bullet points",
        "inserted into the existing room summary",
        "the task is to return",
        "required output format",
    ]

    if any(p in lowered for p in bad_phrases):
        return True

    # Bad if it repeats required sections.
    if lowered.count("## action items") > 1:
        return True

    if lowered.count("## open questions") > 1:
        return True

    # Bad if it does not mention any meaningful word from the actual messages.
    message_words = []
    for item in new_items:
        for word in _compact_text(item.get("text")).lower().split():
            word = word.strip(".,!?;:()[]{}\"'")
            if len(word) >= 4:
                message_words.append(word)

    if message_words:
        matched = any(word in lowered for word in message_words[:30])
        if not matched:
            return True

    return False


def _fallback_summary_from_messages(new_items: List[Dict]) -> str:
    summary_lines: List[str] = []
    open_questions: List[str] = []

    for item in new_items[:8]:
        author = item.get("author") or "unknown"
        text = _clip_text(item.get("text"), 220)

        if not text:
            continue

        summary_lines.append(f"- @{author} said: “{text}”")

        if "?" in text:
            open_questions.append(f"- @{author} asked: “{text}”")

    if not summary_lines:
        summary_lines = ["- New messages were posted, but no usable message text was available."]

    if not open_questions:
        open_questions = ["- None yet"]

    return f"""## Summary
{chr(10).join(summary_lines)}

## Decisions
- None yet

## Action items
- None yet

## Open questions
{chr(10).join(open_questions)}"""

async def ollama_generate(prompt: str) -> str:
    timeout = httpx.Timeout(
        timeout=OLLAMA_TIMEOUT_S,
        connect=10.0,
        read=OLLAMA_TIMEOUT_S,
        write=30.0,
        pool=30.0,
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": OLLAMA_KEEP_ALIVE,
        "options": {
            "temperature": 0.0,
            "top_p": 0.8,
            "num_ctx": OLLAMA_NUM_CTX,
            "num_predict": 450,
            "repeat_penalty": 1.2,
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(OLLAMA_URL, json=payload)

        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Ollama HTTP error: {e.response.status_code} {e.response.text[:500]}"
            ) from e

        data = r.json()
        text = (data.get("response") or "").strip()

        if not text:
            raise RuntimeError(f"Ollama returned an empty response: {data}")

        return _clean_summary_output(text)


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


def _fetch_new_items(
    db: Session,
    scope: Scope,
    last_id: int | None,
    limit: int = 40,
) -> List[Dict]:
    last_id = int(last_id or 0)

    if scope.kind == "GENERAL":
        q = (
            select(Intervention, User.handle)
            .join(User, User.id == Intervention.by_user)
            .where(
                (Intervention.question_id == scope.question_id)
                & (Intervention.id > last_id)
            )
            .order_by(Intervention.id.asc())
            .limit(limit)
        )

        rows = db.exec(q).all()

        return [
            {
                "id": i.id,
                "author": h or "unknown",
                "text": _clip_text(i.body, SUMMARY_MAX_ITEM_CHARS),
            }
            for i, h in rows
        ]

    if scope.kind == "PROOM":
        q = (
            select(ProposalMessage, User.handle)
            .join(User, User.id == ProposalMessage.user_id)
            .where(
                (ProposalMessage.room_id == scope.room_id)
                & (ProposalMessage.id > last_id)
            )
            .order_by(ProposalMessage.id.asc())
            .limit(limit)
        )

        rows = db.exec(q).all()

        return [
            {
                "id": m.id,
                "author": h or "unknown",
                "text": _clip_text(m.body, SUMMARY_MAX_ITEM_CHARS),
            }
            for m, h in rows
        ]

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
        q = q.where(
            (ProposalIntervention.draft_id == scope.draft_id)
            & (ProposalIntervention.amendment_id.is_(None))
        )
    else:
        q = q.where(
            (ProposalIntervention.amendment_id == scope.amendment_id)
            & (ProposalIntervention.draft_id.is_(None))
        )

    q = q.order_by(ProposalIntervention.id.asc()).limit(limit)

    rows = db.exec(q).all()

    return [
        {
            "id": pi.id,
            "author": h or "unknown",
            "text": _clip_text(pi.body, SUMMARY_MAX_ITEM_CHARS),
        }
        for pi, h in rows
    ]


def _build_prompt(existing_summary: str | None, new_items: List[Dict]) -> tuple[str, int, bool]:
    included: List[Dict] = []
    lines: List[str] = []
    total_chars = 0

    for x in new_items[:SUMMARY_MAX_ITEMS]:
        text = _clip_text(x.get("text"), SUMMARY_MAX_ITEM_CHARS)

        if not text:
            continue

        author = x.get("author") or "unknown"
        line = f"[{x['id']}] {author}: {text}"

        if lines and total_chars + len(line) + 1 > SUMMARY_MAX_CHARS:
            break

        lines.append(line)
        included.append(x)
        total_chars += len(line) + 1

    if not included:
        raise RuntimeError("No usable new items could be included in summary prompt")

    existing = _clip_text(existing_summary or "None yet.", SUMMARY_MAX_EXISTING_CHARS)
    transcript = "\n".join(lines)
    has_more_pending_items = len(included) < len(new_items)

    prompt = f"""You are updating a live meeting summary.

Existing summary:
<existing_summary>
{existing}
</existing_summary>

New messages:
<new_messages>
{transcript}
</new_messages>

Write the updated live summary.

Important:
- The actual messages are only inside <new_messages>.
- Do not invent names.
- Do not mention participant1, participant2, or Participant123.
- Do not explain what you are doing.
- Do not copy this prompt.
- Return only the four markdown sections below.

## Summary
- Mention what participants actually said.
- If the messages are casual or unclear, say that briefly.

## Decisions
- None yet, unless a clear decision was made.

## Action items
- None yet, unless someone clearly needs to do something.

## Open questions
- Include unanswered questions only.

Now return the final markdown:
"""

    return prompt, included[-1]["id"], has_more_pending_items


async def refresh_summary_if_needed(
    db: Session,
    scope: Scope,
    *,
    min_interval_sec: int = 2,
    max_new: int = 40,
    force: bool = False,
) -> LiveSummary:
    row = _ensure_row(db, scope)

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
        row.updated_at = now
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    try:
        prompt, last_included_id, has_more_pending_items = _build_prompt(
            row.summary,
            new_items,
        )
    except RuntimeError as e:
        logger.warning("live summary prompt build failed for %s: %s", row.scope_key, e)

        # Avoid getting stuck forever on bad/empty rows.
        row.last_item_id = new_items[-1]["id"]
        row.dirty = len(new_items) >= max_new
        row.updated_at = now
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    try:
        updated = await ollama_generate(prompt)

        if _looks_like_bad_stablelm_summary(updated, new_items):
            logger.warning(
                "StableLM produced unusable live summary for %s; using fallback summary",
                row.scope_key,
            )
            updated = _fallback_summary_from_messages(new_items)

    except (httpx.ReadTimeout, httpx.ConnectError, httpx.HTTPError, RuntimeError) as e:
        logger.warning("live summary refresh failed for %s: %s", row.scope_key, e)

        # Keep dirty so the app retries later instead of crashing.
        row.dirty = True
        row.updated_at = now
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    row.summary = updated

    # Only advance to the final message actually included in the prompt.
    row.last_item_id = last_included_id

    # Keep dirty if some fetched messages were not summarized yet,
    # or if we hit the fetch limit and there may be even more messages.
    row.dirty = has_more_pending_items or len(new_items) >= max_new

    row.updated_at = now

    db.add(row)
    db.commit()
    db.refresh(row)

    return row