# app/services/draft_translation.py

import hashlib
import json
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, cast

from sqlmodel import select

from app.db import get_session
from app.models import DraftTranslation, ProposalDraft
from app.services.local_translate import Lang, translate_many_en_to


DONE = "DONE"
FAILED = "FAILED"
PENDING = "PENDING"
RUNNING = "RUNNING"


@dataclass(frozen=True)
class TranslationJob:
    draft_id: int
    lang: Lang
    source_hash: str
    fields: tuple[str, ...]


_QUEUE: queue.Queue[TranslationJob] = queue.Queue()
_QUEUED_KEYS: set[tuple[int, str, str]] = set()
_QUEUED_LOCK = threading.Lock()

_WORKER_STARTED = False
_WORKER_LOCK = threading.Lock()


def draft_source_payload(draft: ProposalDraft, fields: Iterable[str]) -> dict:
    return {
        "title": draft.title or "",
        "fields": {
            field: getattr(draft, field) or ""
            for field in fields
        },
    }


def draft_source_hash(draft: ProposalDraft, fields: Iterable[str]) -> str:
    payload = draft_source_payload(draft, fields)

    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_values(draft: ProposalDraft, fields: tuple[str, ...]) -> list[str]:
    values = [draft.title or ""]

    for field in fields:
        values.append(getattr(draft, field) or "")

    return values


def _build_translated_payload(
    source_values: list[str],
    translated_values: list[str],
    fields: tuple[str, ...],
) -> tuple[str, dict]:
    title_source = source_values[0]
    title_translated = translated_values[0] if title_source.strip() else title_source

    draft_text: dict[str, str | None] = {}

    for i, field in enumerate(fields, start=1):
        original = source_values[i]

        if original and original.strip():
            draft_text[field] = translated_values[i]
        else:
            draft_text[field] = None

    return title_translated, draft_text


def _start_worker_once() -> None:
    global _WORKER_STARTED

    if _WORKER_STARTED:
        return

    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return

        thread = threading.Thread(
            target=_worker_loop,
            name="draft-translation-worker",
            daemon=True,
        )

        thread.start()
        _WORKER_STARTED = True


def _enqueue(job: TranslationJob) -> None:
    _start_worker_once()

    key = (job.draft_id, job.lang, job.source_hash)

    with _QUEUED_LOCK:
        if key in _QUEUED_KEYS:
            return

        _QUEUED_KEYS.add(key)

    _QUEUE.put(job)


def _worker_loop() -> None:
    while True:
        job = _QUEUE.get()
        key = (job.draft_id, job.lang, job.source_hash)

        try:
            _run_job(job)
        finally:
            with _QUEUED_LOCK:
                _QUEUED_KEYS.discard(key)

            _QUEUE.task_done()


def _mark_failed(draft_id: int, lang: str, source_hash: str, error: str) -> None:
    with get_session() as db:
        row = db.exec(
            select(DraftTranslation)
            .where(DraftTranslation.draft_id == draft_id)
            .where(DraftTranslation.lang == lang)
        ).first()

        if not row:
            return

        if row.source_hash != source_hash:
            return

        row.status = FAILED
        row.error = error[:2000]
        row.updated_at = datetime.utcnow()

        db.add(row)
        db.commit()


def _run_job(job: TranslationJob) -> None:
    try:
        with get_session() as db:
            draft = db.get(ProposalDraft, job.draft_id)

            if not draft:
                return

            current_hash = draft_source_hash(draft, job.fields)

            if current_hash != job.source_hash:
                return

            source_values = _source_values(draft, job.fields)

            row = db.exec(
                select(DraftTranslation)
                .where(DraftTranslation.draft_id == draft.id)
                .where(DraftTranslation.lang == job.lang)
            ).first()

            if row and row.status == DONE and row.source_hash == current_hash:
                return

            if not row:
                row = DraftTranslation(
                    draft_id=draft.id,
                    lang=job.lang,
                    source_hash=current_hash,
                    status=RUNNING,
                    draft_text_json={},
                )
            else:
                row.source_hash = current_hash
                row.status = RUNNING
                row.error = None
                row.updated_at = datetime.utcnow()

            db.add(row)
            db.commit()

        translated_values = translate_many_en_to(source_values, job.lang)

        title_show, draft_text = _build_translated_payload(
            source_values=source_values,
            translated_values=translated_values,
            fields=job.fields,
        )

        with get_session() as db:
            row = db.exec(
                select(DraftTranslation)
                .where(DraftTranslation.draft_id == job.draft_id)
                .where(DraftTranslation.lang == job.lang)
            ).first()

            if not row:
                return

            if row.source_hash != job.source_hash:
                return

            row.title_show = title_show
            row.draft_text_json = draft_text
            row.status = DONE
            row.error = None
            row.updated_at = datetime.utcnow()

            db.add(row)
            db.commit()

    except Exception as exc:
        _mark_failed(
            draft_id=job.draft_id,
            lang=job.lang,
            source_hash=job.source_hash,
            error=repr(exc),
        )


def get_cached_or_enqueue_draft_translation(
    db,
    draft: ProposalDraft,
    lang: Lang,
    fields: Iterable[str],
) -> DraftTranslation:
    fields_tuple = tuple(fields)
    source_hash = draft_source_hash(draft, fields_tuple)

    row = db.exec(
        select(DraftTranslation)
        .where(DraftTranslation.draft_id == draft.id)
        .where(DraftTranslation.lang == lang)
    ).first()

    if row and row.status == DONE and row.source_hash == source_hash:
        return row

    now = datetime.utcnow()

    if not row:
        row = DraftTranslation(
            draft_id=draft.id,
            lang=lang,
            source_hash=source_hash,
            status=PENDING,
            draft_text_json={},
            created_at=now,
            updated_at=now,
        )
    else:
        row.source_hash = source_hash
        row.status = PENDING
        row.error = None
        row.title_show = None
        row.draft_text_json = {}
        row.updated_at = now

    db.add(row)
    db.commit()
    db.refresh(row)

    _enqueue(
        TranslationJob(
            draft_id=draft.id,
            lang=lang,
            source_hash=source_hash,
            fields=fields_tuple,
        )
    )

    return row


def translation_row_is_ready(
    row: DraftTranslation | None,
    draft: ProposalDraft,
    fields: Iterable[str],
) -> bool:
    if not row:
        return False

    return (
        row.status == DONE
        and row.source_hash == draft_source_hash(draft, fields)
    )


def normalize_translation_lang(lang: str, supported: Iterable[str]) -> str:
    lang = (lang or "en").lower()

    if lang not in supported:
        return "en"

    return lang


def as_translate_lang(lang: str) -> Lang:
    return cast(Lang, lang)