import os
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlmodel import Session as SyncSession
from sqlmodel import create_engine as create_engine_sync

from app.db import DATABASE_URL, engine_sync


ARCHIVE_DATABASE_NAME = os.getenv(
    "RECORD_ARCHIVE_DATABASE_NAME",
    "consensus_archive_b",
)

_archive_url = make_url(DATABASE_URL).set(
    database=ARCHIVE_DATABASE_NAME
)

archive_engine_sync = create_engine_sync(
    _archive_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=2,
    max_overflow=2,
    future=True,
)


RECORD_SOURCES = {
    "a": {
        "key": "a",
        "label": "Instance A",
        "database": make_url(DATABASE_URL).database,
        "engine": engine_sync,
    },
    "b": {
        "key": "b",
        "label": "Instance B",
        "database": ARCHIVE_DATABASE_NAME,
        "engine": archive_engine_sync,
    },
}


def record_sources():
    """Return public metadata for configured record sources."""
    return [
        {
            "key": source["key"],
            "label": source["label"],
            "database": source["database"],
        }
        for source in RECORD_SOURCES.values()
    ]


@contextmanager
def get_record_session(source_key: str):
    """
    Open a database session that PostgreSQL itself marks READ ONLY.

    Record pages must never mutate historical event data.
    """
    source = RECORD_SOURCES.get(source_key)

    if source is None:
        raise KeyError(f"Unknown record source: {source_key}")

    session = SyncSession(
        source["engine"],
        expire_on_commit=False,
    )

    try:
        # This is intentionally the first statement in the transaction.
        session.exec(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        # Never commit from an archive/record session.
        session.rollback()
        session.close()
