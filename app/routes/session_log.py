# app/routes/session_log.py

from __future__ import annotations

from app.i18n import request_locale

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlmodel import select

from app.db import get_session
from app.models import (
    ExperimentSessionLog,
    User,
    Event,
    EventArchiveState,
    RecordSourceArchiveState,
)
from app.services.session_log import add_session_log
from app.security import effective_flags, unsign_cookie


router = APIRouter()


def _require_user(request: Request):
    """
    Replace this if your current_user() lives somewhere else.
    This assumes your existing app has current_user(request) in app.main.
    """
    try:
        from app.main import current_user

        user = current_user(request)
    except Exception:
        user = None

    if not user:
        raise HTTPException(status_code=401)

    return user

def _require_event_user(request: Request, event_id: int, db):
    """Require a logged-in user who may access this event."""
    user = _require_user(request)

    # Import lazily to avoid a module-level circular import with app.main.
    from app.main import _require_event_access

    _require_event_access(
        db=db,
        user=user,
        event_id=event_id,
    )
    return user


def _require_log_viewer(request: Request, event_id: int, db):
    """Restrict experiment logs to privileged event operators."""
    user = _require_event_user(request, event_id, db)
    flags = effective_flags(user)

    if not any(
        (
            flags.get("IS_ADMIN"),
            flags.get("IS_PRESIDENT"),
            flags.get("IS_CHAIR"),
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="Experiment logs are restricted to admins, presidents, and chairmen",
        )

    return user




def _require_record_log_viewer(
    request: Request,
    source_key: str,
    event_id: int,
    record_db,
):
    """
    Record Session Logs are President-only.

    Event/log data is read from the selected Record source.
    Any source-B manual closure override is checked only in
    surviving Instance A.
    """
    user = _require_user(request)
    flags = effective_flags(user)

    if not flags.get("IS_PRESIDENT"):
        raise HTTPException(
            status_code=403,
            detail=(
                "Record Session Logs are restricted "
                "to the President"
            ),
        )

    event = record_db.get(
        Event,
        event_id,
    )

    if event is None:
        raise HTTPException(status_code=404)

    event_end = event.ends_at

    if (
        event_end is not None
        and event_end.tzinfo is None
    ):
        event_end = event_end.replace(
            tzinfo=timezone.utc
        )

    timer_closed = bool(
        event_end
        and event_end <= datetime.now(timezone.utc)
    )

    manual_closed = False

    if source_key == "a":
        manual_closed = (
            record_db.exec(
                select(EventArchiveState.id)
                .where(
                    EventArchiveState.event_id
                    == event_id
                )
            ).first()
            is not None
        )

    elif source_key == "b":
        # B itself remains read-only. Its manual archive state
        # lives only in the surviving A database.
        with get_session() as live_db:
            manual_closed = (
                live_db.exec(
                    select(
                        RecordSourceArchiveState.id
                    )
                    .where(
                        RecordSourceArchiveState.source_key
                        == "b",
                        RecordSourceArchiveState.event_id
                        == event_id,
                    )
                ).first()
                is not None
            )

    else:
        raise HTTPException(status_code=404)

    # A Record Session Log must never expose an event that has
    # not yet entered the Records lifecycle.
    if not (manual_closed or timer_closed):
        raise HTTPException(status_code=404)

    return user



def _user_id_from_session_cookie(request: Request) -> int | None:
    """
    Get logged-in user id from the signed session cookie.
    Never crash logging if the cookie is missing/bad.
    """
    try:
        return unsign_cookie(request.cookies.get("session"))
    except Exception:
        return None


def _log_to_dict(log: ExperimentSessionLog) -> dict[str, Any]:
    try:
        details = json.loads(log.details_json) if log.details_json else None
    except Exception:
        details = log.details_json

    return {
        "id": log.id,
        "created_at": log.created_at.isoformat() if isinstance(log.created_at, datetime) else str(log.created_at),
        "event_id": log.event_id,
        "user_id": log.user_id,
        "session_key": log.session_key,
        "source": log.source,
        "action": log.action,
        "page": log.page,
        "route": log.route,
        "method": log.method,
        "status_code": log.status_code,
        "duration_ms": log.duration_ms,
        "phase": log.phase,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "ip_hash": log.ip_hash,
        "user_agent": log.user_agent,
        "request_id": log.request_id,
        "details": details,
    }


@router.post("/api/session-log/client")
async def client_session_log(request: Request):
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    event_id = payload.get("event_id")
    try:
        event_id = int(event_id) if event_id not in (None, "") else None
    except Exception:
        event_id = None

    payload_user_id = payload.get("user_id")
    try:
        payload_user_id = int(payload_user_id) if payload_user_id not in (None, "") else None
    except Exception:
        payload_user_id = None

    cookie_user_id = _user_id_from_session_cookie(request)
    effective_user_id = cookie_user_id or payload_user_id

    details = dict(payload.get("details") or {})

    if payload.get("user_handle"):
        details["user_handle"] = payload.get("user_handle")

    with get_session() as db:
        # Never trust a client-supplied user_id for event-scoped telemetry.
        # The authenticated user must actually be allowed into the event.
        if event_id is not None:
            event_user = _require_event_user(request, event_id, db)
            effective_user_id = int(event_user.id)

        add_session_log(
            db,
            request=request,
            user_id=effective_user_id,
            event_id=event_id,
            source="client",
            action=payload.get("action") or "CLIENT_EVENT",
            page=payload.get("page"),
            phase=payload.get("phase"),
            target_type=payload.get("target_type"),
            target_id=payload.get("target_id"),
            details=details,
        )
        db.commit()

    return {"ok": True}


@router.get("/events/{event_id}/session-log/export")
def export_session_log(event_id: int, request: Request, format: str = "csv"):
    with get_session() as db:
        _require_log_viewer(request, event_id, db)
        logs = db.exec(
            select(ExperimentSessionLog)
            .where(ExperimentSessionLog.event_id == event_id)
            .order_by(ExperimentSessionLog.created_at, ExperimentSessionLog.id)
        ).all()

        user_ids = sorted({log.user_id for log in logs if log.user_id})
        users = db.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
        user_map = {u.id: u for u in users}

    rows = []

    for log in logs:
        row = _log_to_dict(log)

        user_label = ""
        user_handle = ""

        if log.user_id:
            u = user_map.get(log.user_id)
            if u:
                user_handle = u.handle or ""
                user_label = f"@{u.handle} ({log.user_id})"
            else:
                user_label = str(log.user_id)

        row["user_handle"] = user_handle
        row["user_label"] = user_label

        rows.append(row)

    if format.lower() == "json":
        return JSONResponse(rows)

    output = io.StringIO()

    fieldnames = [
        "id",
        "created_at",
        "event_id",
        "user_id",
        "user_handle",
        "user_label",
        "session_key",
        "source",
        "action",
        "page",
        "route",
        "method",
        "status_code",
        "duration_ms",
        "phase",
        "target_type",
        "target_id",
        "ip_hash",
        "user_agent",
        "request_id",
        "details",
    ]

    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in rows:
        row = dict(row)
        row["details"] = json.dumps(row.get("details"), ensure_ascii=False, default=str)
        writer.writerow(row)

    filename = f"event_{event_id}_session_log.csv"

    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


@router.get("/events/{event_id}/session-log", response_class=HTMLResponse)
def view_session_log(event_id: int, request: Request):
    with get_session() as db:
        _require_log_viewer(request, event_id, db)
        logs = db.exec(
            select(ExperimentSessionLog)
            .where(ExperimentSessionLog.event_id == event_id)
            .order_by(ExperimentSessionLog.created_at.desc(), ExperimentSessionLog.id.desc())
            .limit(300)
        ).all()

        user_ids = sorted({log.user_id for log in logs if log.user_id})
        users = db.exec(select(User).where(User.id.in_(user_ids))).all() if user_ids else []
        user_map = {u.id: u for u in users}

    return _render_log_view(logs, user_map, event_id, request)



@router.get(
    "/records/{source_key}/events/"
    "{event_id}/session-log/export"
)
def export_record_session_log(
    source_key: str,
    event_id: int,
    request: Request,
    format: str = "csv",
):
    """
    Source-aware, read-only Session Log export for Records.
    """
    from app.record_db import get_record_session

    if source_key not in ("a", "b"):
        raise HTTPException(status_code=404)

    try:
        with get_record_session(source_key) as db:
            _require_record_log_viewer(
                request,
                source_key,
                event_id,
                db,
            )

            logs = db.exec(
                select(ExperimentSessionLog)
                .where(
                    ExperimentSessionLog.event_id
                    == event_id
                )
                .order_by(
                    ExperimentSessionLog.created_at,
                    ExperimentSessionLog.id,
                )
            ).all()

            user_ids = sorted({
                log.user_id
                for log in logs
                if log.user_id
            })

            users = (
                db.exec(
                    select(User)
                    .where(
                        User.id.in_(user_ids)
                    )
                ).all()
                if user_ids
                else []
            )

            user_map = {
                u.id: u
                for u in users
            }

            # Materialize while the Record DB session is open.
            rows = []

            for log in logs:
                row = _log_to_dict(log)

                user_label = ""
                user_handle = ""

                if log.user_id:
                    record_user = user_map.get(
                        log.user_id
                    )

                    if record_user:
                        user_handle = (
                            record_user.handle or ""
                        )
                        user_label = (
                            f"@{record_user.handle} "
                            f"({log.user_id})"
                        )
                    else:
                        user_label = str(
                            log.user_id
                        )

                row["user_handle"] = user_handle
                row["user_label"] = user_label

                rows.append(row)

    except KeyError:
        raise HTTPException(status_code=404)

    if format.lower() == "json":
        return JSONResponse(rows)

    output = io.StringIO()

    fieldnames = [
        "id",
        "created_at",
        "event_id",
        "user_id",
        "user_handle",
        "user_label",
        "session_key",
        "source",
        "action",
        "page",
        "route",
        "method",
        "status_code",
        "duration_ms",
        "phase",
        "target_type",
        "target_id",
        "ip_hash",
        "user_agent",
        "request_id",
        "details",
    ]

    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
    )

    writer.writeheader()

    for row in rows:
        row = dict(row)

        row["details"] = json.dumps(
            row.get("details"),
            ensure_ascii=False,
            default=str,
        )

        writer.writerow(row)

    filename = (
        f"record_{source_key}_event_"
        f"{event_id}_session_log.csv"
    )

    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"'
        },
    )


@router.get(
    "/records/{source_key}/events/"
    "{event_id}/session-log",
    response_class=HTMLResponse,
)
def view_record_session_log(
    source_key: str,
    event_id: int,
    request: Request,
):
    """
    Source-aware, read-only Session Log viewer for Records.
    """
    from app.record_db import get_record_session

    if source_key not in ("a", "b"):
        raise HTTPException(status_code=404)

    try:
        with get_record_session(source_key) as db:
            _require_record_log_viewer(
                request,
                source_key,
                event_id,
                db,
            )

            logs = db.exec(
                select(ExperimentSessionLog)
                .where(
                    ExperimentSessionLog.event_id
                    == event_id
                )
                .order_by(
                    ExperimentSessionLog.created_at.desc(),
                    ExperimentSessionLog.id.desc(),
                )
                .limit(300)
            ).all()

            user_ids = sorted({
                log.user_id
                for log in logs
                if log.user_id
            })

            users = (
                db.exec(
                    select(User)
                    .where(
                        User.id.in_(user_ids)
                    )
                ).all()
                if user_ids
                else []
            )

            user_map = {
                u.id: u
                for u in users
            }

            # Render/materialize before the read-only archive
            # session rolls back and expires ORM state.
            return _render_log_view(
                logs,
                user_map,
                event_id,
                request,
                back_href=(
                    f"/records/{source_key}/"
                    f"events/{event_id}"
                ),
                export_href_base=(
                    f"/records/{source_key}/"
                    f"events/{event_id}/"
                    "session-log/export"
                ),
                record_source_key=source_key,
            )

    except KeyError:
        raise HTTPException(status_code=404)



def _render_log_view(
    logs,
    user_map,
    event_id,
    request,
    *,
    back_href=None,
    export_href_base=None,
    record_source_key=None,
):
    # Reuse the application's final Jinja environment, including t() and |jst.
    from app.main import templates
    from app.services.session_log_view import build_log_view

    records = []
    for log in logs:
        record = _log_to_dict(log)
        record["details_json"] = log.details_json  # Preserve even malformed/truncated legacy JSON.
        records.append(record)
    view = build_log_view(records, user_map, request_locale(request))
    return templates.env.get_template("session_log.html").render(
        request=request,
        event_id=event_id,
        view=view,
        back_href=(
            back_href
            or f"/events/{event_id}/menu"
        ),
        export_href_base=(
            export_href_base
            or f"/events/{event_id}/session-log/export"
        ),
        record_source_key=record_source_key,
    )
