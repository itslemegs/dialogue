# scripts/seed_events.py
from datetime import datetime, timedelta, timezone
from sqlmodel import Session
from app.db import engine
from app.models import Event, EventStage

now = datetime.now(timezone.utc)
evt = Event(
    title="General Debate – 12th Session",
    starts_at=now + timedelta(minutes=30),
    ends_at=now + timedelta(hours=2, minutes=30),
)

with Session(engine) as s:
    s.add(evt)
    s.commit()
    s.refresh(evt)

    s.add_all([
        EventStage(event_id=evt.id, name="Opening",        starts_at=evt.starts_at,                              ends_at=evt.starts_at + timedelta(minutes=15)),
        EventStage(event_id=evt.id, name="General Debate", starts_at=evt.starts_at + timedelta(minutes=15),      ends_at=evt.starts_at + timedelta(hours=1, minutes=45)),
        EventStage(event_id=evt.id, name="Voting",         starts_at=evt.starts_at + timedelta(hours=1, minutes=45), ends_at=evt.ends_at),
    ])
    s.commit()