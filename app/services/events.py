# app/services/events.py
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlmodel import select
from app.models import Event, EventStage

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()

def _human(dt: Optional[datetime], tz: timezone = timezone.utc) -> str:
    if not dt: return ""
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%a, %b %-d • %H:%M")

def get_live_or_next_event(session, now: Optional[datetime]=None, display_tz: timezone=timezone.utc) -> Optional[Dict[str, Any]]:
    now = now or datetime.now(timezone.utc)

    live = session.exec(
        select(Event)
        .where(Event.starts_at <= now)
        .where((Event.ends_at.is_(None)) | (Event.ends_at > now))
        .order_by(Event.starts_at.asc())
        .limit(1)
    ).first()

    target = live
    state = "live" if live else "upcoming"

    if not target:
        target = session.exec(
            select(Event)
            .where(Event.starts_at > now)
            .order_by(Event.starts_at.asc())
            .limit(1)
        ).first()
        if not target:
            return None

    srows: List[EventStage] = session.exec(
        select(EventStage)
        .where(EventStage.event_id == target.id)
        .order_by(EventStage.starts_at.asc())
    ).all()

    stages = [{"name": s.name, "start": _iso(s.starts_at), "end": _iso(s.ends_at)} for s in srows]

    return {
        "title": target.title,
        "starts_at_iso": _iso(target.starts_at),
        "ends_at_iso": _iso(target.ends_at),
        "starts_at_human": _human(target.starts_at, display_tz),
        "stages": stages,
        "state": state,
    }